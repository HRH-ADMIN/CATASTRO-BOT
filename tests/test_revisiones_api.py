"""Tests endpoints /api/revisiones/* + página side-by-side (N-02 B+C).

Plan: PLAN_MEJORAS Sprint 5 / N-02.
"""
from __future__ import annotations
import sqlite3
from unittest.mock import patch

import pytest

from src.core.credential_manager import CredentialManager
from src.core.database import Database
from src.utils import pre_envio_snapshot as pes
from src.web.app import create_app


@pytest.fixture(autouse=True)
def _reset_bus():
    from src.utils.event_bus import reset_bus_for_testing
    reset_bus_for_testing()
    yield
    reset_bus_for_testing()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CATASTRO_BOT_DEV_MODE", "1")
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("config.settings.DATABASE_PATH", db_path)
    d = Database(path=db_path, credentials=CredentialManager())
    d.initialize_schema()

    # Crear expediente dummy
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO expedientes
               (id, numero_expediente, tipo_plano, nombre_topografo,
                telefono_cliente, estado_actual, municipalidad,
                fecha_creacion, fecha_actualizacion, metadata_json,
                completado, cancelado)
               VALUES ('EXP-1', 'TEST-1', 'segregacion', 'Test',
                       '50612345678', 'recibido', 'San Ramón',
                       '2026-01-01T00:00:00.000Z',
                       '2026-01-01T00:00:00.000Z', '{}', 0, 0)"""
        )
        conn.commit()

    # Reemplazar ROOT en los endpoints de serving para apuntar a tmp_path
    # (sino intenta resolver bajo el ROOT real del proyecto)
    monkeypatch.setattr("src.utils.dashboard_web.ROOT", tmp_path,
                        raising=False)

    from src.core.control_state import ControlStateManager
    legacy_mgr = ControlStateManager(tmp_path / "control.json")

    with patch("src.web.app.get_control_manager", return_value=legacy_mgr):
        with patch("src.web.app._expected_token", return_value=None):
            app = create_app()
            app.config["TESTING"] = True
            app.config["CSRF_DISABLED"] = True
            app.config["RATE_LIMIT_DISABLED"] = True
            with app.test_client() as c:
                c._db_path = db_path
                c._root = tmp_path
                yield c


# ─── Pendientes / Detalle ────────────────────────────────────────────

class TestListEndpoint:
    def test_pendientes_vacio(self, client):
        resp = client.get("/api/revisiones/pendientes")
        assert resp.status_code == 200
        assert resp.get_json()["revisiones"] == []

    def test_pendientes_lista_creadas(self, client):
        rev_id = pes.crear_revision(
            client._db_path, client._root,
            expediente_id="EXP-1",
            seed_plano={"a": 1}, snapshot_dom={"a": 1},
        )
        resp = client.get("/api/revisiones/pendientes")
        body = resp.get_json()
        assert len(body["revisiones"]) == 1
        assert body["revisiones"][0]["id"] == rev_id


class TestDetalleEndpoint:
    def test_detalle_ok(self, client):
        rev_id = pes.crear_revision(
            client._db_path, client._root,
            expediente_id="EXP-1",
            seed_plano={"tamanno": 1}, snapshot_dom={"tamanno": 2},
        )
        resp = client.get(f"/api/revisiones/{rev_id}")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["id"] == rev_id
        assert body["n_discrepancias"] == 1
        # Diff deserializado
        assert body["diff"][0]["campo"] == "tamanno"

    def test_detalle_404(self, client):
        resp = client.get("/api/revisiones/no-existe")
        assert resp.status_code == 404


# ─── Aprobar / Rechazar ──────────────────────────────────────────────

class TestAprobar:
    def test_aprobar_ok(self, client):
        rev_id = pes.crear_revision(
            client._db_path, client._root,
            expediente_id="EXP-1",
            seed_plano={}, snapshot_dom={},
        )
        resp = client.post(f"/api/revisiones/{rev_id}/aprobar")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["estado"] == "aprobado"
        assert body["resuelto_por"] == "web_dashboard"

    def test_aprobar_x_actor_header(self, client):
        rev_id = pes.crear_revision(
            client._db_path, client._root,
            expediente_id="EXP-1",
            seed_plano={}, snapshot_dom={},
        )
        resp = client.post(f"/api/revisiones/{rev_id}/aprobar",
                           headers={"X-Actor": "whatsapp:+50688887310"})
        assert resp.get_json()["resuelto_por"] == "whatsapp:+50688887310"

    def test_aprobar_404(self, client):
        resp = client.post("/api/revisiones/no-existe/aprobar")
        assert resp.status_code == 404

    def test_aprobar_rechazada_conflicto_409(self, client):
        rev_id = pes.crear_revision(
            client._db_path, client._root,
            expediente_id="EXP-1",
            seed_plano={}, snapshot_dom={},
        )
        pes.rechazar(client._db_path, rev_id, razon="test")
        resp = client.post(f"/api/revisiones/{rev_id}/aprobar")
        assert resp.status_code == 409


class TestRechazar:
    def test_rechazar_ok(self, client):
        rev_id = pes.crear_revision(
            client._db_path, client._root,
            expediente_id="EXP-1",
            seed_plano={}, snapshot_dom={},
        )
        resp = client.post(f"/api/revisiones/{rev_id}/rechazar",
                           json={"razon": "Falta validar campo X"})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["estado"] == "rechazado"
        assert body["razon_rechazo"] == "Falta validar campo X"

    def test_rechazar_sin_razon_400(self, client):
        rev_id = pes.crear_revision(
            client._db_path, client._root,
            expediente_id="EXP-1",
            seed_plano={}, snapshot_dom={},
        )
        resp = client.post(f"/api/revisiones/{rev_id}/rechazar", json={})
        assert resp.status_code == 400

        resp = client.post(f"/api/revisiones/{rev_id}/rechazar",
                           json={"razon": "   "})
        assert resp.status_code == 400


# ─── Serving de assets (screenshot + PDF) ────────────────────────────

class TestServingScreenshot:
    def test_screenshot_404_si_no_existe(self, client):
        rev_id = pes.crear_revision(
            client._db_path, client._root,
            expediente_id="EXP-1",
            seed_plano={}, snapshot_dom={},
        )
        # No le pasamos page → screenshot_path queda NULL
        resp = client.get(f"/api/revisiones/{rev_id}/screenshot.png")
        assert resp.status_code == 404

    def test_screenshot_sirve_archivo_real(self, client):
        # Crear archivo PNG fake
        ss_dir = client._root / "data" / "revisiones"
        ss_dir.mkdir(parents=True, exist_ok=True)
        ss_path = ss_dir / "test.png"
        # PNG header mínimo
        ss_path.write_bytes(b"\x89PNG\r\n\x1a\nfake")

        # Insertar directo con path relativo
        with sqlite3.connect(client._db_path) as conn:
            conn.execute(
                "INSERT INTO revisiones_pre_envio "
                "(id, expediente_id, estado, screenshot_path) "
                "VALUES ('R1', 'EXP-1', 'pendiente', 'data/revisiones/test.png')"
            )
            conn.commit()

        resp = client.get("/api/revisiones/R1/screenshot.png")
        assert resp.status_code == 200
        assert resp.mimetype == "image/png"
        assert resp.data == b"\x89PNG\r\n\x1a\nfake"


# ─── Página HTML ─────────────────────────────────────────────────────

class TestRevisionPanelPage:
    def test_renderiza_html_con_exp_id_inyectado(self, client):
        resp = client.get("/expediente/EXP-1/revisar-envio")
        assert resp.status_code == 200
        assert resp.mimetype == "text/html"
        body = resp.get_data(as_text=True)
        # Title + estructura
        assert "Revisión visual pre-envío" in body
        # JS recibe el exp_id como variable
        assert '"EXP-1"' in body or "'EXP-1'" in body
        # Referencias a endpoints
        assert "/api/revisiones/pendientes" in body
        assert "aprobar" in body
        assert "rechazar" in body
