"""Tests del middleware CSRF (Sprint 4 / S-04).

Cubre:
  - Módulo puro src/web/csrf.py:
      * generate_token: hex 64 chars
      * tokens_match: compara constante en tiempo, rechaza None/distintos
      * is_csrf_exempt: GET/HEAD/OPTIONS y Bearer válido
      * render_meta_tag: HTML válido con el token
  - Snippet JS:
      * CSRF_FETCH_WRAPPER_JS contiene el wrapper.
  - Integración con Flask app:
      * GET / setea cookie csrf_token
      * GET no requiere token
      * POST sin cookie + sin header → 403
      * POST con cookie==header → procesa normalmente (200/202)
      * POST con cookie!=header → 403
      * Bearer válido exime de CSRF
      * El meta-render incluye el token

Plan: PLAN_MEJORAS Sprint 4 / S-04.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from flask import Flask, jsonify

from src.core.control_state import ControlStateManager
from src.web import csrf
from src.web.app import create_app
from src.web.csrf_js import CSRF_FETCH_WRAPPER_JS


# ─── Módulo puro ─────────────────────────────────────────────────────


class TestModuloPuro:
    def test_generate_token_es_hex_64(self):
        t = csrf.generate_token()
        assert len(t) == 64
        # Debe ser hex válido
        int(t, 16)

    def test_generate_token_es_unico(self):
        t1 = csrf.generate_token()
        t2 = csrf.generate_token()
        assert t1 != t2

    def test_tokens_match_iguales(self):
        t = csrf.generate_token()
        assert csrf.tokens_match(t, t) is True

    def test_tokens_match_distintos(self):
        assert csrf.tokens_match("a" * 64, "b" * 64) is False

    def test_tokens_match_none(self):
        assert csrf.tokens_match(None, "abc") is False
        assert csrf.tokens_match("abc", None) is False
        assert csrf.tokens_match(None, None) is False
        assert csrf.tokens_match("", "abc") is False

    def test_tokens_match_distinta_longitud(self):
        assert csrf.tokens_match("abc", "abcdef") is False

    def test_render_meta_tag_contiene_token(self):
        token = "deadbeef" * 8
        html = csrf.render_meta_tag(token)
        assert 'name="csrf-token"' in html
        assert f'content="{token}"' in html


# ─── Snippet JS ──────────────────────────────────────────────────────


class TestSnippetJS:
    def test_wrapper_define_patch_de_fetch(self):
        assert "window.fetch" in CSRF_FETCH_WRAPPER_JS
        assert "X-CSRF-Token" in CSRF_FETCH_WRAPPER_JS
        assert "catastro_csrf" in CSRF_FETCH_WRAPPER_JS

    def test_wrapper_lista_metodos_inseguros(self):
        # POST/PUT/DELETE/PATCH son los que parchean
        for m in ("POST", "PUT", "DELETE", "PATCH"):
            assert m in CSRF_FETCH_WRAPPER_JS

    def test_wrapper_se_inyecta_via_fstring_sin_explotar(self):
        """El snippet se inyecta como `{_csrf_js}` en f-strings — NO usa .format().

        f-strings sustituyen el valor de la variable sin re-parsear `{}` dentro
        del valor — así que el JS `init = init || {};` no rompe nada cuando
        está dentro del valor de `_csrf_js`. Probamos un render real.
        """
        _csrf_js = CSRF_FETCH_WRAPPER_JS  # noqa: F841
        rendered = f"<script>{_csrf_js}</script>"
        assert "init = init || {}" in rendered
        assert "X-CSRF-Token" in rendered

    def test_renderiza_en_todos_los_paneles(self):
        """Cada página HTML del dashboard debe inyectar el snippet."""
        from src.utils.dashboard_control_html import render_control_panel_html
        from src.utils.dashboard_runtime_html import render_runtime_panel_html
        from src.utils.dashboard_costos_html import render_costos_panel_html
        from src.utils.dashboard_revision_html import render_revision_panel_html

        for name, fn in [
            ("control",  render_control_panel_html),
            ("runtime",  render_runtime_panel_html),
            ("costos",   render_costos_panel_html),
            ("revision", lambda: render_revision_panel_html("EXP-001")),
        ]:
            html = fn()
            assert "X-CSRF-Token" in html, f"panel {name} no tiene CSRF wrapper"
            assert "catastro_csrf" in html, f"panel {name} no lee la cookie"


# ─── Integración con Flask ───────────────────────────────────────────


@pytest.fixture
def control_manager(tmp_path):
    return ControlStateManager(tmp_path / "control.json")


@pytest.fixture
def app_csrf_on(control_manager):
    """App con CSRF habilitado (default productivo)."""
    with patch("src.web.app.get_control_manager", return_value=control_manager):
        app = create_app()
        # CSRF_DISABLED NO se setea — queremos el comportamiento real
        with patch("src.web.app._expected_token", return_value=None):
            yield app


@pytest.fixture
def client_csrf_on(app_csrf_on):
    with app_csrf_on.test_client() as c:
        yield c


class TestIntegracionFlask:
    def test_get_dashboard_setea_cookie_csrf(self, client_csrf_on):
        resp = client_csrf_on.get("/")
        assert resp.status_code == 200
        # Cookie debe estar seteada en la response
        cookies = resp.headers.getlist("Set-Cookie")
        assert any("catastro_csrf=" in c for c in cookies)

    def test_get_no_requiere_token(self, client_csrf_on):
        # Cualquier GET pasa sin CSRF
        for path in ["/api/state", "/api/health", "/api/expedientes"]:
            resp = client_csrf_on.get(path)
            assert resp.status_code in (200, 503), f"path={path} status={resp.status_code}"

    def test_post_sin_cookie_ni_header_rechaza_403(self, client_csrf_on):
        resp = client_csrf_on.post("/api/pause")
        assert resp.status_code == 403
        body = resp.get_json()
        assert "csrf" in body["error"].lower()

    def test_post_con_cookie_y_header_iguales_procesa(self, client_csrf_on):
        # Setear cookie + header manualmente
        token = "a" * 64
        client_csrf_on.set_cookie("catastro_csrf", token)
        resp = client_csrf_on.post(
            "/api/pause",
            headers={"X-CSRF-Token": token},
        )
        assert resp.status_code == 200

    def test_post_con_cookie_y_header_distintos_rechaza_403(
        self, client_csrf_on
    ):
        client_csrf_on.set_cookie("catastro_csrf", "a" * 64)
        resp = client_csrf_on.post(
            "/api/pause",
            headers={"X-CSRF-Token": "b" * 64},
        )
        assert resp.status_code == 403

    def test_post_solo_con_cookie_sin_header_rechaza(self, client_csrf_on):
        client_csrf_on.set_cookie("catastro_csrf", "a" * 64)
        resp = client_csrf_on.post("/api/pause")
        assert resp.status_code == 403

    def test_post_solo_con_header_sin_cookie_rechaza(self, client_csrf_on):
        resp = client_csrf_on.post(
            "/api/pause",
            headers={"X-CSRF-Token": "a" * 64},
        )
        assert resp.status_code == 403

    def test_bearer_valido_exime_de_csrf(self, control_manager):
        """Cliente máquina con Bearer válido → no requiere CSRF."""
        with patch("src.web.app.get_control_manager", return_value=control_manager):
            with patch("src.web.app._expected_token", return_value="secret-bearer"):
                app = create_app()
                with app.test_client() as c:
                    resp = c.post(
                        "/api/pause",
                        headers={"Authorization": "Bearer secret-bearer"},
                    )
                    # Pasa la auth Y se exime de CSRF
                    assert resp.status_code == 200

    def test_csrf_disabled_flag_apaga_middleware(self, control_manager):
        """app.config['CSRF_DISABLED']=True deja pasar POSTs sin token."""
        with patch("src.web.app.get_control_manager", return_value=control_manager):
            with patch("src.web.app._expected_token", return_value=None):
                app = create_app()
                app.config["CSRF_DISABLED"] = True
                with app.test_client() as c:
                    resp = c.post("/api/pause")
                    assert resp.status_code == 200


# ─── Flujo completo: GET captura cookie, POST la usa ─────────────────


class TestFlujoCompleto:
    def test_navegacion_normal_GET_luego_POST(self, app_csrf_on):
        """Simula el flujo browser: GET dashboard → cookie → POST usa cookie+header."""
        with app_csrf_on.test_client() as c:
            # 1. GET la página HTML — el server setea cookie
            resp = c.get("/")
            assert resp.status_code == 200
            # Extraer cookie del test client
            cookie = c.get_cookie("catastro_csrf")
            assert cookie is not None
            token = cookie.value
            assert len(token) >= 32

            # 2. POST con header X-CSRF-Token = cookie (lo que haría el JS)
            resp = c.post(
                "/api/pause",
                headers={"X-CSRF-Token": token},
            )
            assert resp.status_code == 200, resp.get_json()


# ─── Sólo se setea cookie en HTML, no en JSON ────────────────────────


class TestCookieSoloEnHTML:
    def test_get_api_json_no_setea_cookie(self, client_csrf_on):
        resp = client_csrf_on.get("/api/state")
        cookies = resp.headers.getlist("Set-Cookie")
        # /api/state devuelve application/json — no debería settear cookie
        assert not any("catastro_csrf=" in c for c in cookies), \
            f"GET /api/state seteó cookie inesperadamente: {cookies}"

    def test_get_html_si_setea_cookie(self, client_csrf_on):
        resp = client_csrf_on.get("/")
        cookies = resp.headers.getlist("Set-Cookie")
        assert any("catastro_csrf=" in c for c in cookies)
