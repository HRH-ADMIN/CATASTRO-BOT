"""Tests del merge de datos APT (Plan APT-FULL, 2026-05-29).

Cubre:
  - Database.actualizar_apt_data(): primer guardado, sobreescritura libre
    cuando source="scan", protección anti-sobreescritura cuando un campo
    fue marcado como manual, detección de discrepancias.
  - APTAgent.consultar_estado() backward-compat: sigue devolviendo string.
  - APTAgent.consultar_apt_data() nuevo: retorna dict completo o None.
  - Scanner _sync_apt_estados: notifica admin cuando hay discrepancias.

Plan: APT-FULL Fases A+B+C, requisitos del operador 2026-05-29.
"""
from __future__ import annotations

import json
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


@pytest.fixture
def expediente(db):
    """Crea un expediente con apt_tramite."""
    eid = db.crear_expediente(
        numero_expediente="TEST-2026-001",
        tipo_plano="rectificacion",
        nombre_topografo="Luis Alonso",
        telefono_cliente="+506 8888 8888",
        nombre_cliente="Cliente Test",
        metadata={"apt_tramite": "1258460"},
    )
    return eid


# ─── Database.actualizar_apt_data ────────────────────────────────────


class TestPrimerGuardado:
    def test_campos_nuevos_se_guardan_con_source(self, db, expediente):
        scan = {
            "estado": "Calificacion RN",
            "tomo": "2026",
            "asiento": "12345",
            "fecha": "29/05/2026",
            "proceso": "En revision",
            "detalle": "Plano de rectificacion finca 1-12-3456-7890",
        }
        res = db.actualizar_apt_data(expediente, scan, source="scan")
        assert set(res["actualizados"]) == {
            "estado", "tomo", "asiento", "fecha", "proceso", "detalle",
        }
        assert res["discrepancias"] == []

        # Verificar en BD
        exp = db.obtener_expediente(expediente)
        meta = json.loads(exp["metadata_json"] or "{}")
        assert meta["apt_estado"] == "Calificacion RN"
        assert meta["apt_tomo"] == "2026"
        assert meta["apt_asiento"] == "12345"
        assert meta["apt_estado_source"] == "scan"
        assert meta["apt_tomo_source"] == "scan"
        assert meta["apt_estado_set_at"]
        assert meta["apt_estado_verified_at"]
        assert meta["apt_data_sync_at"]

    def test_campo_vacio_no_se_guarda(self, db, expediente):
        scan = {"estado": "", "tomo": "  ", "asiento": None, "detalle": "X"}
        res = db.actualizar_apt_data(expediente, scan, source="scan")
        # Solo `detalle` tiene valor real
        assert res["actualizados"] == ["detalle"]


class TestSobreescrituraScan:
    def test_scan_sobre_scan_se_sobrescribe(self, db, expediente):
        db.actualizar_apt_data(expediente, {"estado": "En Edicion"}, source="scan")
        res2 = db.actualizar_apt_data(
            expediente, {"estado": "Calificacion RN"}, source="scan",
        )
        assert "estado" in res2["actualizados"]
        meta = json.loads(db.obtener_expediente(expediente)["metadata_json"])
        assert meta["apt_estado"] == "Calificacion RN"
        assert meta["apt_estado_source"] == "scan"

    def test_scan_repetido_actualiza_verified_at(self, db, expediente):
        db.actualizar_apt_data(expediente, {"estado": "En Edicion"}, source="scan")
        meta_1 = json.loads(db.obtener_expediente(expediente)["metadata_json"])
        v1 = meta_1["apt_estado_verified_at"]
        import time as _t
        _t.sleep(0.05)  # garantizar timestamp distinto
        db.actualizar_apt_data(expediente, {"estado": "En Edicion"}, source="scan")
        meta_2 = json.loads(db.obtener_expediente(expediente)["metadata_json"])
        v2 = meta_2["apt_estado_verified_at"]
        assert v2 >= v1  # verificado de nuevo


class TestProteccionManual:
    def _set_manual(self, db, expediente, campo, valor):
        """Helper: marca un campo como manual."""
        db.actualizar_apt_data(
            expediente, {campo: valor}, source="manual", actor="manual-test",
        )

    def test_scan_no_sobrescribe_manual_coincidente(self, db, expediente):
        # Operador cargó manualmente
        self._set_manual(db, expediente, "tomo", "2026")
        # Scan trae el mismo valor
        res = db.actualizar_apt_data(
            expediente, {"tomo": "2026"}, source="scan",
        )
        assert res["verificados"] == ["tomo"]
        assert res["actualizados"] == []
        meta = json.loads(db.obtener_expediente(expediente)["metadata_json"])
        assert meta["apt_tomo_source"] == "manual"  # se mantiene manual
        assert meta["apt_tomo"] == "2026"

    def test_scan_no_sobrescribe_manual_discrepante(self, db, expediente):
        # Operador cargó "2026"
        self._set_manual(db, expediente, "tomo", "2026")
        # Scan trae "2027" — discrepancia
        res = db.actualizar_apt_data(
            expediente, {"tomo": "2027"}, source="scan",
        )
        assert res["actualizados"] == []
        assert res["verificados"] == []
        assert len(res["discrepancias"]) == 1
        assert res["discrepancias"][0]["campo"] == "tomo"
        assert res["discrepancias"][0]["manual"] == "2026"
        assert res["discrepancias"][0]["scan"] == "2027"

        # En BD el valor manual NO se sobrescribe
        meta = json.loads(db.obtener_expediente(expediente)["metadata_json"])
        assert meta["apt_tomo"] == "2026"
        assert meta["apt_tomo_source"] == "manual"
        # Pero queda timestamp de la discrepancia
        assert meta.get("apt_tomo_discrepancia_detectada_at")

    def test_manual_puede_sobreescribir_scan(self, db, expediente):
        # Primero scan
        db.actualizar_apt_data(
            expediente, {"tomo": "2026"}, source="scan",
        )
        # Operador corrige manual
        db.actualizar_apt_data(
            expediente, {"tomo": "2027"}, source="manual",
        )
        meta = json.loads(db.obtener_expediente(expediente)["metadata_json"])
        assert meta["apt_tomo"] == "2027"
        assert meta["apt_tomo_source"] == "manual"

    def test_mezcla_un_manual_dos_scan(self, db, expediente):
        """Un expediente con tomo manual, estado y asiento del scan."""
        # Manual: solo tomo
        self._set_manual(db, expediente, "tomo", "MANUAL_TOMO")
        # Scan: estado, tomo (coincide), asiento (nuevo)
        res = db.actualizar_apt_data(
            expediente,
            {"estado": "En Edicion", "tomo": "OTRO_TOMO", "asiento": "999"},
            source="scan",
        )
        assert "estado" in res["actualizados"]
        assert "asiento" in res["actualizados"]
        assert "tomo" not in res["actualizados"]
        assert len(res["discrepancias"]) == 1

        meta = json.loads(db.obtener_expediente(expediente)["metadata_json"])
        assert meta["apt_tomo"] == "MANUAL_TOMO"
        assert meta["apt_tomo_source"] == "manual"
        assert meta["apt_asiento"] == "999"
        assert meta["apt_asiento_source"] == "scan"


class TestValidaciones:
    def test_source_invalido_lanza(self, db, expediente):
        with pytest.raises(ValueError):
            db.actualizar_apt_data(expediente, {"estado": "X"}, source="invalido")

    def test_expediente_inexistente_lanza(self, db):
        from src.core.exceptions import DatabaseError
        with pytest.raises(DatabaseError):
            db.actualizar_apt_data("exp-fake-9999", {"estado": "X"})


# ─── APTAgent.consultar_estado backward compat ───────────────────────


class TestAPTAgentCompat:
    def test_consultar_estado_devuelve_string(self, db, expediente):
        """consultar_estado() wrapper de consultar_apt_data debe seguir
        retornando el string del estado para callers viejos."""
        from src.agents.apt_agent import APTAgent
        # Mock consultar_apt_data
        with patch.object(APTAgent, "consultar_apt_data") as mock:
            mock.return_value = {
                "estado": "Calificacion RN",
                "tomo": "2026",
                "asiento": "999",
                "fecha": "29/05/2026",
                "proceso": "X",
                "detalle": "D",
            }
            agent = APTAgent(db, CredentialManager())
            result = agent.consultar_estado(expediente)
            assert result == "Calificacion RN"

    def test_consultar_estado_devuelve_none_si_data_es_none(self, db, expediente):
        from src.agents.apt_agent import APTAgent
        with patch.object(APTAgent, "consultar_apt_data", return_value=None):
            agent = APTAgent(db, CredentialManager())
            assert agent.consultar_estado(expediente) is None


# ─── Render dashboard expone apt nested dict ─────────────────────────


class TestDashboardRender:
    def test_leer_expedientes_incluye_apt_dict(self, db, expediente, db_path, monkeypatch):
        # Agregar datos APT al expediente
        db.actualizar_apt_data(
            expediente,
            {"estado": "En Edicion", "tomo": "2026", "asiento": "1"},
            source="scan",
        )
        # _leer_expedientes apunta a DB_PATH global
        monkeypatch.setattr("src.utils.dashboard_web.DB_PATH", db_path)
        from src.utils.dashboard_web import _leer_expedientes
        exps = _leer_expedientes()
        assert len(exps) == 1
        e = exps[0]
        assert "apt" in e
        assert e["apt"]["estado"] == "En Edicion"
        assert e["apt"]["tomo"] == "2026"
        assert e["apt"]["estado_source"] == "scan"

    def test_render_html_muestra_tomo_asiento(self, db, expediente, db_path, monkeypatch):
        db.actualizar_apt_data(
            expediente,
            {"estado": "En Edicion", "tomo": "2026", "asiento": "ABCD"},
            source="scan",
        )
        monkeypatch.setattr("src.utils.dashboard_web.DB_PATH", db_path)
        from src.utils.dashboard_web import _render_html
        html = _render_html()
        assert "T:2026" in html
        assert "A:ABCD" in html
        # badge scan presente
        assert 'class="apt-badge scan"' in html

    def test_render_muestra_badge_manual(self, db, expediente, db_path, monkeypatch):
        db.actualizar_apt_data(expediente, {"estado": "X"}, source="manual")
        monkeypatch.setattr("src.utils.dashboard_web.DB_PATH", db_path)
        from src.utils.dashboard_web import _render_html
        html = _render_html()
        assert 'class="apt-badge manual"' in html

    def test_render_muestra_badge_discrepancia(self, db, expediente, db_path, monkeypatch):
        db.actualizar_apt_data(expediente, {"estado": "A"}, source="manual")
        db.actualizar_apt_data(expediente, {"estado": "B"}, source="scan")
        monkeypatch.setattr("src.utils.dashboard_web.DB_PATH", db_path)
        from src.utils.dashboard_web import _render_html
        html = _render_html()
        assert 'class="apt-badge discrep"' in html


# ─── _sync_apt_estados notifica discrepancias ────────────────────────


class TestSyncJobNotificaDiscrepancias:
    def test_discrepancia_dispara_notificacion(self, db, expediente, monkeypatch):
        monkeypatch.setattr("config.settings.DATABASE_PATH", db.path)
        # Pre-cargar manual divergente
        db.actualizar_apt_data(expediente, {"tomo": "MANUAL"}, source="manual")

        # Mock orchestrator
        orch = MagicMock()
        orch.db = db
        orch.credentials = CredentialManager()
        orch.whatsapp = MagicMock()

        # APTAgent mock que devuelve dict con tomo distinto
        fake_agent = MagicMock()
        fake_agent._cdp_disponible.return_value = True
        fake_agent.consultar_apt_data.return_value = {
            "estado": "En Edicion",
            "tomo": "SCAN_TOMO",
            "asiento": "1",
            "fecha": "X",
            "proceso": "Y",
            "detalle": "Z",
        }

        # Agregar admin en BD para que notificar funcione
        db.crear_usuario(
            telefono="+50688888888",
            nombre="Admin Test",
            rol="admin",
        )

        with patch("src.agents.apt_agent.APTAgent", return_value=fake_agent):
            from src.scheduler.tasks import _sync_apt_estados
            _sync_apt_estados(orch)

        # Se debió notificar al admin
        orch.whatsapp.enviar_mensaje.assert_called()
        msg = orch.whatsapp.enviar_mensaje.call_args.args[1]
        assert "discrepancia" in msg.lower()
        assert "MANUAL" in msg
        assert "SCAN_TOMO" in msg
