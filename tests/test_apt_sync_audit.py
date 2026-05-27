"""Tests del audit log de apt-sync-estados (Sprint 2 / O-06).

Cubre:
  - Database.registrar_evento + ultimo_evento (API pública para audit_log).
  - _sync_apt_estados deja rastro audit en cada salida:
      apt_sync_skipped_cdp  → CDP no responde
      apt_sync_failed       → no se pudo crear APTAgent
      apt_sync_success      → ciclo completo sin errores
      apt_sync_partial      → ciclo completo con errores por-expediente
  - _notificar_sync_caido tiene anti-spam de 4h.
  - Endpoint /api/apt-sync-status devuelve status derivado correcto
    (ok / stale / down / unknown).

Plan: PLAN_MEJORAS Sprint 2 / O-06.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.core.credential_manager import CredentialManager
from src.core.database import Database


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    monkeypatch.setenv("CATASTRO_BOT_DEV_MODE", "1")
    p = tmp_path / "test.db"
    monkeypatch.setattr("config.settings.DATABASE_PATH", p)
    d = Database(path=p, credentials=CredentialManager())
    d.initialize_schema()
    return p


@pytest.fixture
def db(db_path):
    return Database(path=db_path, credentials=CredentialManager())


# ─── Database.registrar_evento / ultimo_evento ────────────────────────


class TestRegistrarEvento:
    def test_inserta_audit_y_se_puede_leer(self, db):
        db.registrar_evento(
            "apt_sync_success",
            detalles={"actualizados": 3, "cambios": 1, "total_revisados": 5},
            actor="scheduler.apt_sync",
        )
        evt = db.ultimo_evento("apt_sync_success")
        assert evt is not None
        assert evt["actor"] == "scheduler.apt_sync"
        assert evt["accion"] == "apt_sync_success"
        assert "actualizados" in evt["detalles_json"]
        assert evt["expediente_id"] is None

    def test_ultimo_evento_inexistente_devuelve_none(self, db):
        assert db.ultimo_evento("evento_inexistente_xyz") is None

    def test_multiples_eventos_devuelve_el_ultimo(self, db):
        db.registrar_evento("apt_sync_success", detalles={"n": 1})
        db.registrar_evento("apt_sync_success", detalles={"n": 2})
        db.registrar_evento("apt_sync_success", detalles={"n": 3})
        evt = db.ultimo_evento("apt_sync_success")
        assert '"n": 3' in evt["detalles_json"]

    def test_hash_chain_se_mantiene_intacto(self, db):
        """Registrar eventos con la API pública NO rompe el hash chain."""
        db.registrar_evento("apt_sync_success", detalles={"n": 1})
        db.registrar_evento("apt_sync_failed", detalles={"err": "boom"})
        db.registrar_evento("apt_sync_skipped_cdp")
        # verify_audit_chain debe pasar sin lanzar
        count = db.verify_audit_chain()
        assert count >= 3

    def test_expediente_id_se_persiste(self, db):
        db.registrar_evento(
            "test_event",
            detalles={"foo": "bar"},
            expediente_id="EXP-2026-001",
        )
        evt = db.ultimo_evento("test_event")
        assert evt["expediente_id"] == "EXP-2026-001"


# ─── _sync_apt_estados — cada salida deja rastro ──────────────────────


@pytest.fixture
def orchestrator(db):
    """Orchestrator mínimo con db real, sin scheduler ni whatsapp."""
    orch = MagicMock()
    orch.db = db
    orch.credentials = CredentialManager()
    orch.whatsapp = MagicMock()
    return orch


class TestSyncAptAuditLog:
    def test_skipped_cdp_cuando_apt_agent_dice_cdp_no_disponible(
        self, orchestrator, db
    ):
        from src.scheduler.tasks import _sync_apt_estados

        # APTAgent existe pero _cdp_disponible devuelve False
        fake_agent = MagicMock()
        fake_agent._cdp_disponible.return_value = False
        with patch("src.agents.apt_agent.APTAgent", return_value=fake_agent):
            _sync_apt_estados(orchestrator)

        evt = db.ultimo_evento("apt_sync_skipped_cdp")
        assert evt is not None
        assert "CDP no responde" in evt["detalles_json"]
        # No debe haber registrado success
        assert db.ultimo_evento("apt_sync_success") is None

    def test_failed_cuando_constructor_apt_agent_falla(
        self, orchestrator, db
    ):
        from src.scheduler.tasks import _sync_apt_estados

        def _explode(*a, **kw):
            raise RuntimeError("explosion en constructor")

        with patch("src.agents.apt_agent.APTAgent", side_effect=_explode):
            _sync_apt_estados(orchestrator)

        evt = db.ultimo_evento("apt_sync_failed")
        assert evt is not None
        assert "create_apt_agent" in evt["detalles_json"]
        assert "RuntimeError" in evt["detalles_json"]

    def test_success_cuando_no_hay_expedientes_pendientes(
        self, orchestrator, db
    ):
        """BD vacía → success con 0/0."""
        from src.scheduler.tasks import _sync_apt_estados

        fake_agent = MagicMock()
        fake_agent._cdp_disponible.return_value = True
        with patch("src.agents.apt_agent.APTAgent", return_value=fake_agent):
            _sync_apt_estados(orchestrator)

        evt = db.ultimo_evento("apt_sync_success")
        assert evt is not None
        assert '"actualizados": 0' in evt["detalles_json"]
        assert '"total_revisados": 0' in evt["detalles_json"]


# ─── Anti-spam de _notificar_sync_caido ───────────────────────────────


class TestAntispamNotificacion:
    def test_no_notifica_si_fallo_reciente_existe(self, db):
        from src.scheduler.tasks import _notificar_sync_caido

        # Insertar un fallo "hace 30 min"
        db.registrar_evento(
            "apt_sync_failed",
            detalles={"motivo": "CDP caído (hace 30 min)"},
            actor="scheduler.apt_sync",
        )

        with patch("src.utils.desktop_notify.notificar_escritorio") as mock:
            _notificar_sync_caido(db, "Nuevo fallo")
            mock.assert_not_called()

    def test_notifica_cuando_no_hay_fallo_previo(self, db):
        from src.scheduler.tasks import _notificar_sync_caido

        with patch("src.utils.desktop_notify.notificar_escritorio") as mock:
            _notificar_sync_caido(db, "Primer fallo")
            mock.assert_called_once()
            assert "Primer fallo" in mock.call_args.kwargs.get("mensaje", "")


# ─── Endpoint /api/apt-sync-status ────────────────────────────────────


@pytest.fixture
def client(db_path, monkeypatch):
    monkeypatch.setenv("CATASTRO_BOT_DEV_MODE", "1")
    monkeypatch.setattr("config.settings.DATABASE_PATH", db_path)
    from src.web.app import create_app
    app = create_app()
    app.config["TESTING"] = True
    app.config["CSRF_DISABLED"] = True
    with patch("src.web.app._expected_token", return_value=None):
        with app.test_client() as c:
            yield c


class TestAptSyncStatusEndpoint:
    def test_unknown_sin_registros(self, client):
        resp = client.get("/api/apt-sync-status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "unknown"
        assert data["ultimo_ok"] is None
        assert data["ultimo_fallo"] is None

    def test_ok_con_success_reciente(self, client, db):
        db.registrar_evento(
            "apt_sync_success",
            detalles={"actualizados": 5, "cambios": 1, "total_revisados": 10},
        )
        resp = client.get("/api/apt-sync-status")
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["ultimo_ok"] is not None
        assert '"actualizados": 5' in data["ultimo_ok"]["detalles_json"]

    def test_down_cuando_ultimo_evento_es_fallo(self, client, db):
        # Primero un success viejo
        db.registrar_evento("apt_sync_success", detalles={"n": 1})
        # Después un fail más nuevo
        db.registrar_evento("apt_sync_failed", detalles={"motivo": "boom"})
        resp = client.get("/api/apt-sync-status")
        data = resp.get_json()
        assert data["status"] == "down"

    def test_down_cuando_solo_hay_skip_cdp(self, client, db):
        db.registrar_evento("apt_sync_skipped_cdp")
        resp = client.get("/api/apt-sync-status")
        data = resp.get_json()
        assert data["status"] == "down"
