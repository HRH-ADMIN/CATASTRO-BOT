"""Tests endpoints /api/costs/* y página /config/costos (O-08 sub-pasos B+C).

Plan: PLAN_MEJORAS Sprint 4 / O-08.
"""
from __future__ import annotations
from unittest.mock import patch

import pytest

from src.core.credential_manager import CredentialManager
from src.core.database import Database
from src.utils import api_costs as ac
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
                yield c


class TestSummaryEndpoint:
    def test_summary_default_vacio(self, client):
        resp = client.get("/api/costs/summary")
        assert resp.status_code == 200
        body = resp.get_json()
        assert "monthly" in body
        assert "daily" in body
        assert "by_expediente" in body
        assert "budget" in body
        assert body["current_month_spend_usd"] == 0.0
        assert body["budget"]["monthly_usd"] == 50.0

    def test_summary_con_data(self, client):
        ac.record_call(
            client._db_path, model="claude-3-5-haiku-20241022",
            tipo="vision_plano", expediente_id="EXP-1",
            input_tokens=10_000, output_tokens=2_000,
        )
        resp = client.get("/api/costs/summary")
        body = resp.get_json()
        assert body["current_month_spend_usd"] > 0
        assert len(body["monthly"]) >= 1
        assert any(e["expediente_id"] == "EXP-1" for e in body["by_expediente"])


class TestRecentEndpoint:
    def test_recent_vacio(self, client):
        resp = client.get("/api/costs/recent")
        assert resp.status_code == 200
        assert resp.get_json()["calls"] == []

    def test_recent_con_limit(self, client):
        for i in range(5):
            ac.record_call(
                client._db_path, model="claude-3-5-haiku-20241022",
                tipo=f"t{i}", input_tokens=100, output_tokens=50,
            )
        resp = client.get("/api/costs/recent?limit=3")
        assert resp.status_code == 200
        assert len(resp.get_json()["calls"]) == 3

    def test_recent_limit_clamp(self, client):
        # limit fuera de rango debería clamp a 500
        resp = client.get("/api/costs/recent?limit=99999")
        assert resp.status_code == 200


class TestBudgetEndpoint:
    def test_set_budget_actualiza(self, client):
        resp = client.post("/api/costs/budget",
                           json={"monthly_usd": 200, "alert_threshold": 0.75})
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["monthly_usd"] == 200.0
        assert body["alert_threshold"] == 0.75

    def test_set_budget_rechaza_negativo(self, client):
        resp = client.post("/api/costs/budget", json={"monthly_usd": -1})
        assert resp.status_code == 400


class TestCostosPanelPage:
    def test_get_config_costos_renderiza_html(self, client):
        resp = client.get("/config/costos")
        assert resp.status_code == 200
        assert resp.mimetype == "text/html"
        body = resp.get_data(as_text=True)
        assert "Costos" in body
        assert "/api/costs/summary" in body
        assert "/api/costs/recent" in body
        assert "EventSource" in body  # SSE wired
        assert "Presupuesto mensual" in body
        assert "updateBudget" in body  # función JS
