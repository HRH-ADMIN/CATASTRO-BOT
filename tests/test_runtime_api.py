"""Tests del API /api/runtime/* + página /config/runtime (U-02 paso B).

Plan: PLAN_MEJORAS Sprint 1 / U-02 paso B.
"""
from __future__ import annotations
import os
from unittest.mock import patch

import pytest

from src.core.credential_manager import CredentialManager
from src.core.database import Database
from src.utils import runtime_processes as rp
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
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    monkeypatch.setattr("config.settings.DATABASE_PATH", db_path)
    monkeypatch.setattr("config.settings.LOGS_DIR", logs_dir)
    d = Database(path=db_path, credentials=CredentialManager())
    d.initialize_schema()

    from src.core.control_state import ControlStateManager
    legacy_mgr = ControlStateManager(tmp_path / "control.json")

    with patch("src.web.app.get_control_manager", return_value=legacy_mgr):
        with patch("src.web.app._expected_token", return_value=None):
            app = create_app()
            app.config["TESTING"] = True
            app.config["CSRF_DISABLED"] = True
            app.config["RATE_LIMIT_DISABLED"] = True
            with app.test_client() as c:
                # Adjuntar fixtures que los tests necesitan
                c._db_path = db_path
                c._logs_dir = logs_dir
                yield c


# ─── /api/runtime/processes ──────────────────────────────────────────

class TestListProcesses:
    def test_default_lista_alive_y_hanging(self, client):
        rp.register_process(client._db_path, process_name="scheduler", pid=999)
        resp = client.get("/api/runtime/processes")
        assert resp.status_code == 200
        body = resp.get_json()
        assert "processes" in body
        names = [p["process_name"] for p in body["processes"]]
        assert "scheduler" in names

    def test_include_dead_true_devuelve_terminados(self, client):
        rp.register_process(client._db_path, process_name="scheduler", pid=999)
        rp.mark_stopped(client._db_path, process_name="scheduler", pid=999)
        # Sin include_dead → no aparece
        resp = client.get("/api/runtime/processes")
        names = [p["process_name"] for p in resp.get_json()["processes"]]
        assert "scheduler" not in names
        # Con include_dead → sí aparece
        resp = client.get("/api/runtime/processes?include_dead=1")
        assert resp.get_json()["include_dead"] is True
        names = [p["process_name"] for p in resp.get_json()["processes"]]
        assert "scheduler" in names

    def test_lista_vacia_es_valida(self, client):
        resp = client.get("/api/runtime/processes")
        assert resp.status_code == 200
        assert resp.get_json()["processes"] == []


# ─── /api/runtime/logs/<process_name> ────────────────────────────────

class TestLogTail:
    def test_proceso_no_registrado_404(self, client):
        resp = client.get("/api/runtime/logs/inexistente")
        assert resp.status_code == 404

    def test_proceso_sin_log_file_path_404(self, client):
        rp.register_process(
            client._db_path, process_name="dashboard", pid=999,
            log_file_path=None,
        )
        resp = client.get("/api/runtime/logs/dashboard")
        assert resp.status_code == 404
        assert "log_file_path" in resp.get_json()["error"]

    def test_tail_log_existente(self, client):
        log_path = client._logs_dir / "scheduler.log"
        log_path.write_text("line1\nline2\nline3\n", encoding="utf-8")
        rp.register_process(
            client._db_path, process_name="scheduler", pid=999,
            log_file_path=str(log_path),
        )
        resp = client.get("/api/runtime/logs/scheduler?tail=2")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["lines"] == ["line2", "line3"]
        assert body["total_lines"] == 3
        assert body["tail_lines"] == 2

    def test_path_fuera_de_logs_dir_rechazado_403(self, client, tmp_path):
        """Defensa contra path traversal: si por algún motivo el row tiene
        log_file_path fuera de logs/, debe rechazarse."""
        # Crear log fuera de logs_dir
        rogue = tmp_path / "rogue.log"
        rogue.write_text("secret data\n", encoding="utf-8")
        rp.register_process(
            client._db_path, process_name="scheduler", pid=999,
            log_file_path=str(rogue),
        )
        resp = client.get("/api/runtime/logs/scheduler")
        assert resp.status_code == 403

    def test_tail_se_clamp_al_rango(self, client):
        log_path = client._logs_dir / "scheduler.log"
        log_path.write_text("\n".join(f"l{i}" for i in range(50)), encoding="utf-8")
        rp.register_process(
            client._db_path, process_name="scheduler", pid=999,
            log_file_path=str(log_path),
        )
        # tail=99999 → debe clamp a 1000
        resp = client.get("/api/runtime/logs/scheduler?tail=99999")
        assert resp.status_code == 200
        assert resp.get_json()["tail_lines"] == 1000


# ─── Página HTML /config/runtime ─────────────────────────────────────

class TestRuntimePanelPage:
    def test_get_runtime_panel_devuelve_html(self, client):
        resp = client.get("/config/runtime")
        assert resp.status_code == 200
        assert resp.mimetype == "text/html"
        body = resp.get_data(as_text=True)
        assert "Procesos del sistema" in body
        assert "EventSource" in body
        assert "/api/runtime/processes" in body
        assert "/api/runtime/logs/" in body
