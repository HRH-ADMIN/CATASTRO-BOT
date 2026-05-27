"""Tests del rate limiter del dashboard (Sprint 4 / S-07).

Cubre:
  - _SlidingWindowStore: hit, expiración por ventana, rechazo cuando excede.
  - find_limit_for_path: match más específico gana, fallback a "*".
  - Integración Flask:
      * GET libre hasta el límite, después 429
      * Headers X-RateLimit-Limit/Remaining/Reset siempre presentes
      * Retry-After en 429
      * Endpoints exentos (SSE, health) nunca limitan
      * Endpoint destructivo (emergency-stop) tiene límite agresivo
      * RATE_LIMIT_DISABLED flag apaga el middleware
      * Clientes con IP distinta tienen cuotas independientes

Plan: PLAN_MEJORAS Sprint 4 / S-07.
"""
from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from src.core.control_state import ControlStateManager
from src.web import rate_limit as rl
from src.web.app import create_app


@pytest.fixture(autouse=True)
def _reset_store():
    rl.reset_for_testing()
    yield
    rl.reset_for_testing()


# ─── _SlidingWindowStore ─────────────────────────────────────────────


class TestSlidingWindowStore:
    def test_primer_hit_pasa_con_remaining_correcto(self):
        store = rl._SlidingWindowStore()
        ok, remaining, _ = store.hit(("1.1.1.1", "/x"), max_n=3, window_s=60)
        assert ok is True
        assert remaining == 2

    def test_hits_consecutivos_decrementan_remaining(self):
        store = rl._SlidingWindowStore()
        results = [store.hit(("ip", "/x"), max_n=5, window_s=60)
                   for _ in range(5)]
        assert all(r[0] for r in results)
        assert [r[1] for r in results] == [4, 3, 2, 1, 0]

    def test_excedido_devuelve_false_y_no_decrementa_mas(self):
        store = rl._SlidingWindowStore()
        for _ in range(3):
            store.hit(("ip", "/x"), max_n=3, window_s=60)
        ok, remaining, reset_in = store.hit(("ip", "/x"), max_n=3, window_s=60)
        assert ok is False
        assert remaining == 0
        assert reset_in > 0
        # Otro intento sigue rechazando con el mismo reset
        ok2, _, _ = store.hit(("ip", "/x"), max_n=3, window_s=60)
        assert ok2 is False

    def test_ips_distintas_son_independientes(self):
        store = rl._SlidingWindowStore()
        for _ in range(3):
            store.hit(("ip1", "/x"), max_n=3, window_s=60)
        # ip1 ya excedió, ip2 fresca
        ok, remaining, _ = store.hit(("ip2", "/x"), max_n=3, window_s=60)
        assert ok is True
        assert remaining == 2

    def test_paths_distintos_son_independientes(self):
        store = rl._SlidingWindowStore()
        for _ in range(3):
            store.hit(("ip", "/a"), max_n=3, window_s=60)
        ok, _, _ = store.hit(("ip", "/b"), max_n=3, window_s=60)
        assert ok is True

    def test_ventana_expirada_libera_slots(self):
        store = rl._SlidingWindowStore()
        # Llenar con window muy corto
        for _ in range(2):
            store.hit(("ip", "/x"), max_n=2, window_s=1)
        # Esperar a que expire
        time.sleep(1.1)
        ok, _, _ = store.hit(("ip", "/x"), max_n=2, window_s=1)
        assert ok is True


# ─── find_limit_for_path ─────────────────────────────────────────────


class TestFindLimitForPath:
    def test_fallback_a_star_para_path_desconocido(self):
        rules = {"*": (60, 60), "/api/control/stop": (5, 60)}
        assert rl.find_limit_for_path("/random/path", rules) == (60, 60)

    def test_match_exacto(self):
        rules = {"*": (60, 60), "/api/control/stop": (5, 60)}
        assert rl.find_limit_for_path("/api/control/stop", rules) == (5, 60)

    def test_match_prefijo_con_segmento_adicional(self):
        rules = {"*": (60, 60), "/api/control/stop": (5, 60)}
        # "/api/control/stop/apt" debería matchear "/api/control/stop"
        assert rl.find_limit_for_path("/api/control/stop/apt", rules) == (5, 60)

    def test_match_mas_especifico_gana(self):
        rules = {
            "*": (60, 60),
            "/api/control": (30, 60),
            "/api/control/emergency-stop": (5, 60),
        }
        assert rl.find_limit_for_path("/api/control/emergency-stop", rules) == (5, 60)
        assert rl.find_limit_for_path("/api/control/stop", rules) == (30, 60)
        assert rl.find_limit_for_path("/api/state", rules) == (60, 60)


# ─── Integración Flask ───────────────────────────────────────────────


@pytest.fixture
def control_manager(tmp_path):
    return ControlStateManager(tmp_path / "control.json")


@pytest.fixture
def client(control_manager):
    """App con rate limit Y CSRF deshabilitado (testeamos rate limit solo)."""
    with patch("src.web.app.get_control_manager", return_value=control_manager):
        app = create_app()
        app.config["CSRF_DISABLED"] = True
        # RATE_LIMIT habilitado a propósito — eso es lo que estamos testeando
        with patch("src.web.app._expected_token", return_value=None):
            with app.test_client() as c:
                yield c


class TestIntegracionFlask:
    def test_get_libre_devuelve_headers_ratelimit(self, client):
        resp = client.get("/api/state")
        assert resp.status_code == 200
        assert "X-RateLimit-Limit" in resp.headers
        assert "X-RateLimit-Remaining" in resp.headers
        assert "X-RateLimit-Reset" in resp.headers
        # Primer hit: remaining = limit - 1
        limit = int(resp.headers["X-RateLimit-Limit"])
        remaining = int(resp.headers["X-RateLimit-Remaining"])
        assert remaining == limit - 1

    def test_excede_default_devuelve_429(self, client):
        # Default es 60/min — disparar 61
        for _ in range(60):
            client.get("/api/state")
        resp = client.get("/api/state")
        assert resp.status_code == 429
        body = resp.get_json()
        assert body["error"] == "rate_limit_exceeded"
        assert "retry_after" in body
        assert "Retry-After" in resp.headers
        assert resp.headers["X-RateLimit-Remaining"] == "0"

    def test_endpoint_destructivo_tiene_limite_agresivo(self, client):
        # /api/control/emergency-stop: 5/min según DEFAULT_LIMITS
        # Hacer 5 hits, el 6º debe rechazar
        for _ in range(5):
            client.post(
                "/api/control/emergency-stop",
                json={"confirmation": "no-match"},  # no importa, queremos hit
            )
        resp = client.post(
            "/api/control/emergency-stop",
            json={"confirmation": "no-match"},
        )
        assert resp.status_code == 429

    def test_endpoints_exentos_no_rate_limit(self, client):
        # /api/health: exento. 100 hits seguidos no deben dar 429.
        for _ in range(100):
            resp = client.get("/api/health")
            assert resp.status_code != 429

    def test_clientes_con_ip_distinta_independientes(self, control_manager):
        """Mock remote_addr en cada request para simular dos IPs distintas."""
        with patch("src.web.app.get_control_manager", return_value=control_manager):
            with patch("src.web.app._expected_token", return_value=None):
                app = create_app()
                app.config["CSRF_DISABLED"] = True
                with app.test_client() as c:
                    # IP1: agotar
                    for _ in range(5):
                        c.post("/api/control/emergency-stop",
                               json={"confirmation": "x"},
                               environ_base={"REMOTE_ADDR": "10.0.0.1"})
                    # IP1: 6to → 429
                    resp = c.post("/api/control/emergency-stop",
                                  json={"confirmation": "x"},
                                  environ_base={"REMOTE_ADDR": "10.0.0.1"})
                    assert resp.status_code == 429
                    # IP2: fresca, debería poder hitear
                    resp = c.post("/api/control/emergency-stop",
                                  json={"confirmation": "x"},
                                  environ_base={"REMOTE_ADDR": "10.0.0.2"})
                    assert resp.status_code != 429

    def test_rate_limit_disabled_flag_apaga_middleware(self, control_manager):
        with patch("src.web.app.get_control_manager", return_value=control_manager):
            with patch("src.web.app._expected_token", return_value=None):
                app = create_app()
                app.config["CSRF_DISABLED"] = True
                app.config["RATE_LIMIT_DISABLED"] = True
                with app.test_client() as c:
                    # 100 hits seguidos a un endpoint con límite 5
                    for _ in range(100):
                        resp = c.post("/api/control/emergency-stop",
                                      json={"confirmation": "x"})
                        assert resp.status_code != 429

    def test_429_incluye_retry_after(self, client):
        for _ in range(5):
            client.post("/api/control/emergency-stop",
                        json={"confirmation": "x"})
        resp = client.post("/api/control/emergency-stop",
                           json={"confirmation": "x"})
        assert resp.status_code == 429
        retry = int(resp.headers["Retry-After"])
        assert 1 <= retry <= 65  # ventana 60s + margen

    def test_remaining_decrementa_con_cada_request(self, client):
        remainings = []
        for _ in range(3):
            resp = client.get("/api/state")
            remainings.append(int(resp.headers["X-RateLimit-Remaining"]))
        # Debe ser monotónicamente decreciente
        assert remainings == sorted(remainings, reverse=True)
        # Cada uno es uno menos que el anterior
        assert remainings[0] - remainings[1] == 1
        assert remainings[1] - remainings[2] == 1


class TestHeadersConsistentes:
    def test_headers_aparecen_aunque_200_normal(self, client):
        resp = client.post("/api/pause")
        assert resp.status_code == 200
        assert "X-RateLimit-Limit" in resp.headers
        assert "X-RateLimit-Remaining" in resp.headers
