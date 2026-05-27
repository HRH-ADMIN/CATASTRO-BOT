"""Tests del replicador del hash root del audit_log (Sprint 2 / N-10).

Cubre:
  - Schema: tabla audit_root_replicas existe + índice.
  - obtener_hash_root: devuelve último, None si BD vacía, acepta Path o Database.
  - replicar:
      * BD vacía → skipped=True, ok=True (no es error)
      * primera vez → email + drive llamados, fila insertada
      * misma hash, sin force → skipped, no envía de nuevo
      * misma hash, con force=True → envía
      * email falla, drive OK → ok=True (al menos uno funcionó)
      * email Y drive fallan → ok=False
      * drive_agent=None → solo email
  - _formato_email / _formato_linea_drive contienen el hash + metadata.
  - Hook en _verify_audit_safe se dispara solo si verify_audit_chain OK.

Plan: PLAN_MEJORAS Sprint 2 / N-10.
"""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from src.core.credential_manager import CredentialManager
from src.core.database import Database
from src.utils import audit_root_replicator as arr


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


@pytest.fixture
def db_sin_hash(db, monkeypatch):
    """Simula caso 'no hay hash root' monkey-pacheando obtener_hash_root.

    El audit_log NO se puede vaciar realmente porque hay un trigger SQL
    que rechaza DELETE (es inmutable por diseño). Para testear el path
    'skipped por BD sin hashes', monkey-pacheamos la función pura.
    """
    monkeypatch.setattr(
        "src.utils.audit_root_replicator.obtener_hash_root",
        lambda *args, **kwargs: None,
    )
    return db


@pytest.fixture
def db_con_eventos(db):
    """BD con 3 eventos en audit_log para tener hash root."""
    db.registrar_evento("test_evento_1", detalles={"n": 1})
    db.registrar_evento("test_evento_2", detalles={"n": 2})
    db.registrar_evento("test_evento_3", detalles={"n": 3})
    return db


@pytest.fixture
def creds_fake():
    creds = MagicMock()
    creds.get_muni_san_ramon.return_value = ("bot@test.com", "fake-pass")
    return creds


# ─── Schema ──────────────────────────────────────────────────────────


class TestSchema:
    def test_tabla_existe(self, db_path):
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='audit_root_replicas'"
            ).fetchone()
        assert row is not None

    def test_columnas_correctas(self, db_path):
        with sqlite3.connect(db_path) as conn:
            cols = [c[1] for c in conn.execute(
                "PRAGMA table_info('audit_root_replicas')"
            ).fetchall()]
        assert set(cols) == {"id", "timestamp", "root_hash", "audit_log_id",
                             "destinos_json", "ok"}

    def test_check_constraint_ok(self, db_path):
        """Insertar ok=2 debe fallar (CHECK ok IN (0,1))."""
        with sqlite3.connect(db_path) as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO audit_root_replicas "
                    "(timestamp, root_hash, audit_log_id, destinos_json, ok) "
                    "VALUES ('2026-01-01', 'h', 1, '{}', 2)"
                )


# ─── obtener_hash_root ──────────────────────────────────────────────


class TestObtenerHashRoot:
    def test_path_inexistente_o_bd_sin_audit_log_devuelve_none(self, tmp_path):
        # Path que no existe → None. (BD existente con audit_log vacío
        # no se puede simular: hay trigger inmutable. Pero el flujo
        # post-schema-init siempre tiene al menos system.init.)
        assert arr.obtener_hash_root(tmp_path / "no.db") is None

    def test_con_eventos_devuelve_ultimo(self, db_con_eventos):
        root = arr.obtener_hash_root(db_con_eventos)
        assert root is not None
        assert "hash_actual" in root
        assert "id" in root
        assert "timestamp" in root
        assert len(root["hash_actual"]) == 64  # SHA-256 hex

    def test_acepta_path_directamente(self, db_path, db_con_eventos):
        """Pasando una Path debería funcionar igual."""
        root_db = arr.obtener_hash_root(db_con_eventos)
        root_path = arr.obtener_hash_root(db_path)
        assert root_path is not None
        assert root_db["hash_actual"] == root_path["hash_actual"]

    def test_path_inexistente_devuelve_none(self, tmp_path):
        assert arr.obtener_hash_root(tmp_path / "no-existe.db") is None


# ─── replicar ────────────────────────────────────────────────────────


class TestReplicar:
    def test_bd_sin_hash_skipped(self, db_sin_hash, creds_fake):
        """Cuando obtener_hash_root devuelve None → skipped + ok=True (no es error)."""
        res = arr.replicar(db_sin_hash, creds_fake, drive_agent=None)
        assert res["skipped"] is True
        assert res["ok"] is True
        assert res["root_hash"] is None

    def test_primera_vez_envia_email(self, db_con_eventos, creds_fake):
        with patch("src.utils.email_digest.enviar_email_smtp",
                   return_value=True) as mock_email:
            res = arr.replicar(db_con_eventos, creds_fake, drive_agent=None)
        assert res["ok"] is True
        assert res["skipped"] is False
        assert res["destinos"]["email"] is True
        assert res["destinos"]["drive"] == "drive_agent_no_disponible"
        mock_email.assert_called_once()
        # Verificar subject contiene el hash
        kwargs = mock_email.call_args.kwargs
        assert "Audit root" in kwargs["subject"]
        assert res["root_hash"][:12] in kwargs["subject"]

    def test_primera_vez_registra_en_bd(self, db_con_eventos, creds_fake):
        with patch("src.utils.email_digest.enviar_email_smtp",
                   return_value=True):
            arr.replicar(db_con_eventos, creds_fake, drive_agent=None)
        replicas = arr.listar_replicas(db_con_eventos)
        assert len(replicas) == 1
        assert replicas[0]["ok"] == 1
        assert "email" in replicas[0]["destinos_json"]

    def test_mismo_hash_sin_force_skipped(self, db_con_eventos, creds_fake):
        with patch("src.utils.email_digest.enviar_email_smtp",
                   return_value=True) as mock_email:
            arr.replicar(db_con_eventos, creds_fake, drive_agent=None)
            mock_email.reset_mock()
            res2 = arr.replicar(db_con_eventos, creds_fake, drive_agent=None)
        assert res2["skipped"] is True
        assert res2["ok"] is True
        mock_email.assert_not_called()

    def test_mismo_hash_con_force_envia_de_nuevo(self, db_con_eventos, creds_fake):
        with patch("src.utils.email_digest.enviar_email_smtp",
                   return_value=True) as mock_email:
            arr.replicar(db_con_eventos, creds_fake, drive_agent=None)
            mock_email.reset_mock()
            res2 = arr.replicar(db_con_eventos, creds_fake, drive_agent=None,
                                force=True)
        assert res2["skipped"] is False
        mock_email.assert_called_once()

    def test_nuevo_evento_dispara_replica(self, db_con_eventos, creds_fake):
        with patch("src.utils.email_digest.enviar_email_smtp",
                   return_value=True) as mock_email:
            arr.replicar(db_con_eventos, creds_fake, drive_agent=None)
            mock_email.reset_mock()
            # Nuevo evento → hash root cambia
            db_con_eventos.registrar_evento("evento_nuevo", detalles={"x": 1})
            res2 = arr.replicar(db_con_eventos, creds_fake, drive_agent=None)
        assert res2["skipped"] is False
        mock_email.assert_called_once()

    def test_email_falla_drive_ok(self, db_con_eventos, creds_fake):
        fake_drive = MagicMock()
        fake_drive.subir_audit_root.return_value = {"ok": True, "file_id": "abc"}
        with patch("src.utils.email_digest.enviar_email_smtp",
                   return_value=False):
            res = arr.replicar(db_con_eventos, creds_fake, drive_agent=fake_drive)
        assert res["ok"] is True  # al menos uno funcionó
        assert res["destinos"]["email"] == "smtp_send_failed"
        assert res["destinos"]["drive"] is True

    def test_email_y_drive_fallan_ok_false(self, db_con_eventos, creds_fake):
        fake_drive = MagicMock()
        fake_drive.subir_audit_root.return_value = {"ok": False, "error": "boom"}
        with patch("src.utils.email_digest.enviar_email_smtp",
                   return_value=False):
            res = arr.replicar(db_con_eventos, creds_fake, drive_agent=fake_drive)
        assert res["ok"] is False
        assert res["destinos"]["email"] == "smtp_send_failed"
        assert res["destinos"]["drive"] == "boom"
        # Igual debe quedar la fila registrada (con ok=0)
        replicas = arr.listar_replicas(db_con_eventos)
        assert len(replicas) == 1
        assert replicas[0]["ok"] == 0

    def test_email_excepcion_se_captura(self, db_con_eventos, creds_fake):
        with patch("src.utils.email_digest.enviar_email_smtp",
                   side_effect=ConnectionError("network down")):
            res = arr.replicar(db_con_eventos, creds_fake, drive_agent=None)
        assert res["ok"] is False
        assert "ConnectionError" in res["destinos"]["email"]

    def test_destinatario_custom(self, db_con_eventos, creds_fake):
        with patch("src.utils.email_digest.enviar_email_smtp",
                   return_value=True) as mock_email:
            arr.replicar(db_con_eventos, creds_fake, drive_agent=None,
                         email_destinatario="otro@example.com")
        assert mock_email.call_args.kwargs["to"] == "otro@example.com"


# ─── Formato ─────────────────────────────────────────────────────────


class TestFormato:
    def test_email_body_contiene_hash_e_id(self, db_con_eventos):
        root = arr.obtener_hash_root(db_con_eventos)
        body = arr._formato_email(root)
        assert root["hash_actual"] in body
        assert str(root["id"]) in body
        assert "tampering" in body.lower()

    def test_linea_drive_es_tsv_con_4_columnas(self, db_con_eventos):
        root = arr.obtener_hash_root(db_con_eventos)
        linea = arr._formato_linea_drive(root)
        assert linea.endswith("\n")
        cols = linea.rstrip("\n").split("\t")
        assert len(cols) == 4
        assert cols[3] == root["hash_actual"]


# ─── Hook en _verify_audit_safe ──────────────────────────────────────


class TestHookVerifyAudit:
    def test_solo_replica_si_verify_pasa(self, db_con_eventos, creds_fake):
        from src.scheduler.tasks import _verify_audit_safe
        orch = MagicMock()
        orch.db = db_con_eventos
        orch.credentials = creds_fake
        # Drive no autorizado (lo común en tests)
        with patch("src.utils.email_digest.enviar_email_smtp",
                   return_value=True) as mock_email:
            with patch("src.scheduler.tasks._drive_agent_si_disponible",
                       return_value=None):
                _verify_audit_safe(orch)
        mock_email.assert_called_once()

    def test_no_replica_si_verify_falla(self, db, creds_fake):
        from src.scheduler.tasks import _verify_audit_safe
        orch = MagicMock()
        orch.db = MagicMock()
        orch.db.verify_audit_chain.side_effect = RuntimeError("hash mismatch")
        orch.credentials = creds_fake
        with patch("src.utils.email_digest.enviar_email_smtp") as mock_email:
            _verify_audit_safe(orch)
        # verify falló → NO debe replicar (sería divulgar tampering)
        mock_email.assert_not_called()
