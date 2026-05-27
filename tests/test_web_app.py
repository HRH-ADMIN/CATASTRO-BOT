"""Tests del Flask app del dashboard.

Usan el test client de Flask — NO arrancan waitress real.
Mockean:
  - `get_control_manager` para usar un manager con archivo temporal
  - Las funciones legacy de dashboard_web para no tocar BD real
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from src.core.control_state import ControlStateManager
from src.web.app import create_app


@pytest.fixture
def control_manager(tmp_path):
    """Manager aislado por test — no toca el control.json global."""
    return ControlStateManager(tmp_path / "control.json")


@pytest.fixture
def client(control_manager):
    with patch("src.web.app.get_control_manager", return_value=control_manager):
        app = create_app()
        app.config["TESTING"] = True
        app.config["CSRF_DISABLED"] = True
        # No hay token configurado → POSTs son públicos (defensa solo en bind)
        with patch("src.web.app._expected_token", return_value=None):
            with app.test_client() as c:
                yield c


def test_state_get_returns_default(client):
    resp = client.get("/api/state")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["enabled"] is True
    assert "apt" in body["modules"]


def test_state_post_updates(client):
    resp = client.post("/api/state", json={"enabled": False, "reason": "test"})
    assert resp.status_code == 200
    assert resp.get_json()["enabled"] is False
    # Verificar persistencia via GET
    assert client.get("/api/state").get_json()["enabled"] is False


def test_state_post_rejects_unknown_modules(client):
    resp = client.post("/api/state", json={"modules": {"fakemod": True}})
    assert resp.status_code == 400
    assert "fakemod" in resp.get_json()["unknown"]


def test_state_post_accepts_known_modules(client):
    resp = client.post("/api/state", json={"modules": {"apt": False}})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["modules"]["apt"] is False
    assert body["modules"]["whatsapp"] is True  # no afectado


def test_pause_disables(client):
    resp = client.post("/api/pause?reason=lunch")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["enabled"] is False
    assert body["reason"] == "lunch"


def test_resume_enables(client):
    client.post("/api/pause")
    resp = client.post("/api/resume")
    assert resp.status_code == 200
    assert resp.get_json()["enabled"] is True


def test_auth_blocks_post_when_token_set(control_manager):
    with patch("src.web.app.get_control_manager", return_value=control_manager):
        with patch("src.web.app._expected_token", return_value="secret-xyz"):
            app = create_app()
            app.config["CSRF_DISABLED"] = True  # testeo auth, no CSRF
            with app.test_client() as c:
                # Sin Authorization → 401
                resp = c.post("/api/pause")
                assert resp.status_code == 401
                # Con Authorization correcto → 200
                resp = c.post(
                    "/api/pause",
                    headers={"Authorization": "Bearer secret-xyz"},
                )
                assert resp.status_code == 200
                # Token incorrecto → 401
                resp = c.post(
                    "/api/pause",
                    headers={"Authorization": "Bearer wrong"},
                )
                assert resp.status_code == 401


def test_get_endpoints_dont_require_auth(control_manager):
    with patch("src.web.app.get_control_manager", return_value=control_manager):
        with patch("src.web.app._expected_token", return_value="secret-xyz"):
            # GETs no requieren auth (verifica eso, no CSRF — CSRF tampoco aplica a GETs)
            app = create_app()
            with app.test_client() as c:
                resp = c.get("/api/state")
                assert resp.status_code == 200


def test_health_endpoint_responds(client):
    # /api/health invoca verificar_salud que llama a chrome CDP y BD.
    # En el entorno de test esos pueden fallar → 503 es OK.
    resp = client.get("/api/health")
    assert resp.status_code in (200, 503)
    body = resp.get_json()
    assert "status" in body


def test_create_app_idempotent():
    """create_app() debe poder llamarse múltiples veces sin efectos secundarios."""
    app1 = create_app()
    app2 = create_app()
    assert app1 is not app2  # apps distintas
    assert app1.name == app2.name == "catastro-bot"


# ─── SSE endpoint (U-04 paso 5) ───────────────────────────────────────

def test_sse_endpoint_responde_mimetype_correcto(client):
    """El endpoint /api/events/stream debe responder con text/event-stream
    y al menos un evento 'hello' inicial."""
    # Usamos stream=True para no agotar el generator infinito
    resp = client.get("/api/events/stream", buffered=False)
    assert resp.status_code == 200
    assert resp.mimetype == "text/event-stream"

    # Leer los primeros bytes — deben contener el 'hello' inicial
    chunks = []
    bytes_leidos = 0
    for chunk in resp.response:
        chunks.append(chunk if isinstance(chunk, str) else chunk.decode("utf-8"))
        bytes_leidos += len(chunks[-1])
        if bytes_leidos > 100:  # suficiente para ver el hello
            break
    body = "".join(chunks)
    resp.close()

    # Formato SSE válido: 'retry: ...' y 'data: ...'
    assert "retry:" in body
    assert "data:" in body
    assert "hello" in body, f"primer evento debe ser 'hello', vio: {body[:200]!r}"


def test_sse_headers_no_cache(client):
    """Defensa: SSE no debe ser cacheable por proxies/browsers."""
    resp = client.get("/api/events/stream", buffered=False)
    assert resp.status_code == 200
    cache_ctl = resp.headers.get("Cache-Control", "")
    assert "no-cache" in cache_ctl
    resp.close()
