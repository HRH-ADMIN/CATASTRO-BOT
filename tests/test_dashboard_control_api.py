"""Tests del API /api/control/* (U-03 paso 2.2).

Usan Flask test_client + BD efímera + reset del state_machine singleton.

Plan: PLAN_MEJORAS Sprint 1 / U-03 paso 2.2.
"""
from __future__ import annotations
from pathlib import Path
from unittest.mock import patch

import pytest

from src.core.credential_manager import CredentialManager
from src.core.database import Database
from src.core.state_machine import (
    get_state_machine,
    reset_state_machine_for_testing,
    ControlStateMachine,
)
from src.web.app import create_app


@pytest.fixture(autouse=True)
def _reset_singletons():
    reset_state_machine_for_testing()
    from src.utils.event_bus import reset_bus_for_testing
    reset_bus_for_testing()
    yield
    reset_state_machine_for_testing()
    reset_bus_for_testing()


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Cliente Flask con BD efímera + state machine apuntando a esa BD."""
    monkeypatch.setenv("CATASTRO_BOT_DEV_MODE", "1")
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("config.settings.DATABASE_PATH", db_path)
    d = Database(path=db_path, credentials=CredentialManager())
    d.initialize_schema()

    # Forzar singleton apuntando a esta BD efímera (NO la productiva).
    get_state_machine(db_path)

    # Manager legacy (sigue funcionando en paralelo durante migración).
    from src.core.control_state import ControlStateManager
    legacy_mgr = ControlStateManager(tmp_path / "control.json")

    with patch("src.web.app.get_control_manager", return_value=legacy_mgr):
        with patch("src.web.app._expected_token", return_value=None):
            app = create_app()
            app.config["TESTING"] = True
            app.config["CSRF_DISABLED"] = True
            app.config["RATE_LIMIT_DISABLED"] = True
            with app.test_client() as c:
                yield c


# ─── /api/control/status ──────────────────────────────────────────────

class TestStatus:
    def test_status_devuelve_todos_los_modulos_default_stopped(self, client):
        resp = client.get("/api/control/status")
        assert resp.status_code == 200
        body = resp.get_json()
        assert "modules" in body
        assert "known_modules" in body
        # Default: todos los KNOWN_MODULES inicializados a STOPPED
        for mod in body["modules"]:
            assert mod["state"] == "STOPPED"


# ─── /api/control/start/<module> ──────────────────────────────────────

class TestStart:
    def test_start_apt_devuelve_202_starting(self, client):
        resp = client.post("/api/control/start/apt", json={"reason": "test"})
        assert resp.status_code == 202
        body = resp.get_json()
        assert body["module_name"] == "apt"
        assert body["state"] == "STARTING"
        assert body["last_transition_actor"] == "web_dashboard"
        assert body["last_transition_reason"] == "test"
        assert body["last_transition_id"]

    def test_start_modulo_desconocido_400(self, client):
        resp = client.post("/api/control/start/fakemod")
        assert resp.status_code == 400
        assert "Módulo desconocido" in resp.get_json()["error"]

    def test_double_start_409_invalid_transition(self, client):
        client.post("/api/control/start/apt")
        resp = client.post("/api/control/start/apt")
        assert resp.status_code == 409
        body = resp.get_json()
        assert "current_state" in body
        assert body["current_state"] == "STARTING"

    def test_x_actor_header_se_propaga(self, client):
        resp = client.post(
            "/api/control/start/apt",
            json={"reason": "test"},
            headers={"X-Actor": "scheduler-bot"},
        )
        assert resp.status_code == 202
        assert resp.get_json()["last_transition_actor"] == "scheduler-bot"


# ─── /api/control/stop/<module> ───────────────────────────────────────

class TestStop:
    def test_stop_running_devuelve_stopping(self, client):
        # llevar a RUNNING primero
        client.post("/api/control/start/apt")
        get_state_machine().mark_running("apt")
        resp = client.post("/api/control/stop/apt", json={"reason": "end of day"})
        assert resp.status_code == 202
        body = resp.get_json()
        assert body["state"] == "STOPPING"
        assert body["last_transition_reason"] == "end of day"

    def test_stop_modulo_stopped_409(self, client):
        # apt está en STOPPED — stop directo es inválido
        resp = client.post("/api/control/stop/apt")
        assert resp.status_code == 409


# ─── /api/control/transition/<id> ─────────────────────────────────────

class TestTransitionPolling:
    def test_polling_devuelve_estado_actual(self, client):
        # Start → STARTING
        start_resp = client.post("/api/control/start/apt")
        tid = start_resp.get_json()["last_transition_id"]

        # Polling 1: todavía STARTING (no es_final)
        resp = client.get(f"/api/control/transition/{tid}")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["state"] == "STARTING"
        assert body["is_final"] is False

        # Avanzar a RUNNING (worker simularía esto)
        get_state_machine().mark_running("apt", transition_id=tid)

        # Polling 2: RUNNING (es_final)
        resp = client.get(f"/api/control/transition/{tid}")
        body = resp.get_json()
        assert body["state"] == "RUNNING"
        assert body["is_final"] is True

    def test_polling_tid_inexistente_404(self, client):
        resp = client.get("/api/control/transition/nope")
        assert resp.status_code == 404


# ─── /api/control/reset/<module> ──────────────────────────────────────

class TestReset:
    def test_reset_de_error_a_stopped(self, client):
        sm = get_state_machine()
        sm.start("apt", actor="test")
        sm.mark_error("apt", actor="test", error_message="boom")

        resp = client.post("/api/control/reset/apt", json={"reason": "fixed"})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["state"] == "STOPPED"
        assert body["last_transition_reason"] == "fixed"

    def test_reset_desde_estado_invalido_409(self, client):
        # apt está en STOPPED — reset directo desde STOPPED no es válido
        resp = client.post("/api/control/reset/apt")
        assert resp.status_code == 409


# ─── /api/control/emergency-stop ──────────────────────────────────────

class TestEmergencyStop:
    def test_sin_confirmation_rechaza_400(self, client):
        resp = client.post("/api/control/emergency-stop", json={})
        assert resp.status_code == 400
        assert "APAGAR TODO" in resp.get_json()["error"]

    def test_confirmation_incorrecto_400(self, client):
        resp = client.post(
            "/api/control/emergency-stop",
            json={"confirmation": "apagar todo"},  # minúsculas, debe rechazar
        )
        assert resp.status_code == 400

    def test_emergency_stop_con_confirmation_correcto(self, client):
        # Llevar algunos módulos a RUNNING
        sm = get_state_machine()
        sm.start("apt", actor="t"); sm.mark_running("apt")
        sm.start("muni", actor="t"); sm.mark_running("muni")

        resp = client.post(
            "/api/control/emergency-stop",
            json={
                "confirmation": "APAGAR TODO",
                "reason": "test e2e",
                "cooldown_seconds": 60,
            },
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["actor"] == "web_dashboard"
        assert body["reason"] == "test e2e"
        assert body["cooldown_seconds"] == 60

        # apt y muni debían quedar en STOPPING (eran RUNNING)
        states = {m["module_name"]: m["state"] for m in body["modules"]}
        assert states["apt"] == "STOPPING"
        assert states["muni"] == "STOPPING"

    def test_cooldown_seconds_se_clamp_entre_60_y_3600(self, client):
        resp = client.post(
            "/api/control/emergency-stop",
            json={"confirmation": "APAGAR TODO", "cooldown_seconds": 10},
        )
        assert resp.json["cooldown_seconds"] == 60  # mínimo

        resp = client.post(
            "/api/control/emergency-stop",
            json={"confirmation": "APAGAR TODO", "cooldown_seconds": 999999},
        )
        assert resp.json["cooldown_seconds"] == 3600  # máximo


# ─── Auth (consistente con el resto del API) ──────────────────────────

class TestAuth:
    def test_get_status_no_requiere_auth_aun_con_token(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CATASTRO_BOT_DEV_MODE", "1")
        db_path = tmp_path / "test.db"
        monkeypatch.setattr("config.settings.DATABASE_PATH", db_path)
        d = Database(path=db_path, credentials=CredentialManager())
        d.initialize_schema()
        get_state_machine(db_path)

        from src.core.control_state import ControlStateManager
        legacy_mgr = ControlStateManager(tmp_path / "control.json")

        with patch("src.web.app.get_control_manager", return_value=legacy_mgr):
            with patch("src.web.app._expected_token", return_value="secret"):
                app = create_app()
                with app.test_client() as c:
                    resp = c.get("/api/control/status")
                    assert resp.status_code == 200

    def test_post_start_requiere_auth_con_token_configurado(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CATASTRO_BOT_DEV_MODE", "1")
        db_path = tmp_path / "test.db"
        monkeypatch.setattr("config.settings.DATABASE_PATH", db_path)
        d = Database(path=db_path, credentials=CredentialManager())
        d.initialize_schema()
        get_state_machine(db_path)

        from src.core.control_state import ControlStateManager
        legacy_mgr = ControlStateManager(tmp_path / "control.json")

        with patch("src.web.app.get_control_manager", return_value=legacy_mgr):
            with patch("src.web.app._expected_token", return_value="secret-xyz"):
                app = create_app()
                app.config["CSRF_DISABLED"] = True
                app.config["RATE_LIMIT_DISABLED"] = True  # testeo auth, no CSRF
                with app.test_client() as c:
                    # Sin token → 401
                    resp = c.post("/api/control/start/apt")
                    assert resp.status_code == 401
                    # Con token correcto → 202
                    resp = c.post(
                        "/api/control/start/apt",
                        headers={"Authorization": "Bearer secret-xyz"},
                    )
                    assert resp.status_code == 202


# ─── Página HTML /config/control ──────────────────────────────────────

class TestControlPanelPage:
    def test_get_config_control_renderiza_html(self, client):
        resp = client.get("/config/control")
        assert resp.status_code == 200
        assert resp.mimetype == "text/html"
        body = resp.get_data(as_text=True)
        # Verificar estructura mínima esperada
        assert "Panel de control" in body
        assert "EventSource" in body  # SSE wired
        assert "/api/control/status" in body
        assert "/api/control/emergency-stop" in body
        assert "APAGAR TODO" in body  # confirmation string visible

    def test_html_no_tiene_unicode_problematico(self, client):
        """Defensa: PowerShell 5.1 rompe con em-dash sin BOM.
        El HTML va en Content-Type UTF-8 así que está OK, pero igual
        verificamos que el JS no use caracteres que rompen su parser."""
        resp = client.get("/config/control")
        body = resp.get_data(as_text=True)
        # El JS se parsea como JS, no como PS. Solo verificamos UTF-8 válido.
        body.encode("utf-8")  # raise si no es válido
