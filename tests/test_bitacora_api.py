"""Tests endpoints /api/bitacora* (Sprint 5 / N-09).

Plan: PLAN_MEJORAS Sprint 5 / N-09.
"""
from __future__ import annotations
import sqlite3
from unittest.mock import patch

import pytest

from src.core.credential_manager import CredentialManager
from src.core.database import Database
from src.utils import daily_log
from src.web.app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CATASTRO_BOT_DEV_MODE", "1")
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("config.settings.DATABASE_PATH", db_path)
    d = Database(path=db_path, credentials=CredentialManager())
    d.initialize_schema()

    # ROOT del módulo dashboard_web → tmp_path para serving correcto
    monkeypatch.setattr("src.utils.dashboard_web.ROOT", tmp_path, raising=False)

    from src.core.control_state import ControlStateManager
    legacy_mgr = ControlStateManager(tmp_path / "control.json")

    with patch("src.web.app.get_control_manager", return_value=legacy_mgr):
        with patch("src.web.app._expected_token", return_value=None):
            app = create_app()
            app.config["TESTING"] = True
            with app.test_client() as c:
                c._db_path = db_path
                c._root = tmp_path
                yield c


class TestBitacoraEndpoint:
    def test_json_default(self, client):
        resp = client.get("/api/bitacora")
        assert resp.status_code == 200
        body = resp.get_json()
        assert "fecha" in body
        assert "resumen" in body
        assert "estados" in body

    def test_fecha_explicita(self, client):
        resp = client.get("/api/bitacora?fecha=2099-01-01")
        assert resp.status_code == 200
        assert resp.get_json()["fecha"] == "2099-01-01"
        assert resp.get_json()["resumen"]["n_eventos_estado"] == 0

    def test_formato_markdown(self, client):
        resp = client.get("/api/bitacora?fecha=2099-01-01&format=markdown")
        assert resp.status_code == 200
        assert resp.mimetype == "text/markdown"
        body = resp.get_data(as_text=True)
        assert "# Bitácora del bot — 2099-01-01" in body

    def test_formato_text(self, client):
        resp = client.get("/api/bitacora?fecha=2099-01-01&format=text")
        assert resp.status_code == 200
        assert resp.mimetype == "text/plain"
        body = resp.get_data(as_text=True)
        assert "📋 Bitácora 2099-01-01" in body


class TestBitacorasListaEndpoint:
    def test_lista_vacia_inicialmente(self, client):
        resp = client.get("/api/bitacoras")
        assert resp.status_code == 200
        assert resp.get_json()["fechas"] == []

    def test_lista_devuelve_archivos_guardados(self, client):
        daily_log.guardar_bitacora(client._root, "2026-05-20", "x")
        daily_log.guardar_bitacora(client._root, "2026-05-22", "y")
        resp = client.get("/api/bitacoras")
        assert resp.get_json()["fechas"] == ["2026-05-20", "2026-05-22"]
