"""Tests de búsqueda global (Sprint 5 / N-07).

Cubre:
  - Database.buscar_expedientes:
      * query < 2 chars → []
      * match en numero_expediente (score 100)
      * match en nombre_cliente (score 80)
      * match en metadata_json (score 10) — incluye apt_tramite
      * orden por score DESC
      * limit cap
      * case-insensitive (ASCII)
  - Endpoint /api/search:
      * q vacío → {count: 0, resultados: []}
      * q válido → JSON con resultados slim
      * limit param respetado
      * shape de respuesta
  - Frontend (smoke):
      * dashboard contiene la caja de search y el JS

Plan: PLAN_MEJORAS Sprint 5 / N-07.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from src.core.control_state import ControlStateManager
from src.core.credential_manager import CredentialManager
from src.core.database import Database
from src.models.estado import Estado
from src.web.app import create_app


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
def db_seed(db):
    """3 expedientes con datos distintos para probar la búsqueda."""
    db.crear_expediente(
        numero_expediente="RDF-2026-001",
        tipo_plano="rectificacion",
        nombre_topografo="Luis Alonso Rojas",
        cedula_topografo="2-0612-0734",
        telefono_cliente="+506 8888 8888",
        nombre_cliente="María del Carmen López",
        municipalidad="San Ramón",
        metadata={
            "apt_tramite": "1258460",
            "proyecto": "lote 12 los robles",
            "distrito": "San Pedro",
        },
    )
    db.crear_expediente(
        numero_expediente="SEG-2026-005",
        tipo_plano="segregacion",
        nombre_topografo="Luis Alonso Rojas",
        cedula_topografo="2-0612-0734",
        telefono_cliente="+506 7777 7777",
        nombre_cliente="Juan Pérez García",
        municipalidad="San Ramón",
        metadata={
            "apt_tramite": "9999999",
            "proyecto": "casa Pérez García",
        },
    )
    db.crear_expediente(
        numero_expediente="RFI-2026-002",
        tipo_plano="reunion_de_fincas",
        nombre_topografo="Luis Alonso Rojas",
        cedula_topografo="2-0612-0734",
        telefono_cliente="+506 6666 6666",
        nombre_cliente="Pedro Mora",
        municipalidad="San Ramón",
        metadata={"apt_tramite": "1111111"},
    )
    return db


# ─── Database.buscar_expedientes ──────────────────────────────────────


class TestBuscarExpedientes:
    def test_query_vacia_devuelve_lista_vacia(self, db_seed):
        assert db_seed.buscar_expedientes("") == []
        assert db_seed.buscar_expedientes("  ") == []

    def test_query_1_char_devuelve_lista_vacia(self, db_seed):
        # Evitamos disparar la búsqueda con un solo caracter (muy ruidosa)
        assert db_seed.buscar_expedientes("a") == []
        assert db_seed.buscar_expedientes("1") == []

    def test_match_numero_expediente(self, db_seed):
        res = db_seed.buscar_expedientes("RDF-2026")
        assert len(res) >= 1
        assert any(r["numero_expediente"] == "RDF-2026-001" for r in res)
        # Top result debe ser match en numero
        assert res[0]["_match_field"] == "numero_expediente"
        assert res[0]["_score"] == 100

    def test_match_nombre_cliente(self, db_seed):
        res = db_seed.buscar_expedientes("María")
        assert len(res) >= 1
        assert any(r["nombre_cliente"] and "María" in r["nombre_cliente"]
                   for r in res)
        assert res[0]["_match_field"] == "nombre_cliente"
        assert res[0]["_score"] == 80

    def test_match_telefono(self, db_seed):
        res = db_seed.buscar_expedientes("8888 8888")
        assert len(res) >= 1
        # Match es en telefono_cliente; el método registra "telefono_cliente"
        assert res[0]["_match_field"] == "telefono_cliente"

    def test_match_metadata_apt_tramite(self, db_seed):
        res = db_seed.buscar_expedientes("1258460")
        assert len(res) >= 1
        assert res[0]["numero_expediente"] == "RDF-2026-001"
        assert res[0]["_match_field"] == "metadata"

    def test_match_metadata_proyecto(self, db_seed):
        res = db_seed.buscar_expedientes("robles")
        assert len(res) >= 1
        assert res[0]["numero_expediente"] == "RDF-2026-001"

    def test_orden_por_score_desc(self, db_seed):
        # Buscar "Pérez" matcheará: nombre_cliente exp-2 (80) y metadata
        # apt_tramite del exp-2 también (10 via proyecto).
        # Ambos hits son del mismo expediente — solo aparece una vez.
        res = db_seed.buscar_expedientes("Pérez")
        assert len(res) >= 1
        # Verificar que el primer resultado tiene el score más alto
        scores = [r["_score"] for r in res]
        assert scores == sorted(scores, reverse=True)

    def test_limit_respetado(self, db_seed):
        # "San Ramón" matchea todos por municipalidad
        res = db_seed.buscar_expedientes("San Ramón", limit=2)
        assert len(res) == 2

    def test_case_insensitive(self, db_seed):
        # SQLite LIKE es case-insensitive para ASCII por default
        upper = db_seed.buscar_expedientes("RDF")
        lower = db_seed.buscar_expedientes("rdf")
        assert len(upper) == len(lower)

    def test_sin_match_devuelve_lista_vacia(self, db_seed):
        assert db_seed.buscar_expedientes("texto-que-no-existe-xyzabc") == []


# ─── Endpoint /api/search ─────────────────────────────────────────────


@pytest.fixture
def control_manager(tmp_path):
    return ControlStateManager(tmp_path / "control.json")


@pytest.fixture
def client(control_manager, db_seed, db_path, monkeypatch):
    """Cliente Flask con seed de expedientes y la BD apuntando ahí."""
    monkeypatch.setattr("config.settings.DATABASE_PATH", db_path)
    with patch("src.web.app.get_control_manager", return_value=control_manager):
        app = create_app()
        app.config["TESTING"] = True
        app.config["CSRF_DISABLED"] = True
        app.config["RATE_LIMIT_DISABLED"] = True
        with patch("src.web.app._expected_token", return_value=None):
            with app.test_client() as c:
                yield c


class TestEndpoint:
    def test_q_vacia_devuelve_count_0(self, client):
        resp = client.get("/api/search?q=")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["count"] == 0
        assert body["resultados"] == []

    def test_q_un_char_devuelve_count_0(self, client):
        resp = client.get("/api/search?q=a")
        assert resp.status_code == 200
        assert resp.get_json()["count"] == 0

    def test_busqueda_numero_expediente(self, client):
        resp = client.get("/api/search?q=RDF-2026")
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["count"] >= 1
        # Shape slim
        first = body["resultados"][0]
        for key in ("id", "numero_expediente", "tipo_plano",
                    "estado_actual", "_score", "_match_field"):
            assert key in first
        # metadata_json NO debe estar (es slim)
        assert "metadata_json" not in first

    def test_busqueda_cliente(self, client):
        resp = client.get("/api/search?q=María")
        body = resp.get_json()
        assert body["count"] >= 1
        assert any("María" in (r["nombre_cliente"] or "") for r in body["resultados"])

    def test_busqueda_apt_tramite_via_metadata(self, client):
        resp = client.get("/api/search?q=1258460")
        body = resp.get_json()
        assert body["count"] >= 1
        assert body["resultados"][0]["numero_expediente"] == "RDF-2026-001"

    def test_limit_respetado(self, client):
        resp = client.get("/api/search?q=San Ramón&limit=2")
        body = resp.get_json()
        assert len(body["resultados"]) == 2

    def test_limit_invalid_default_20(self, client):
        resp = client.get("/api/search?q=abc&limit=xxx")
        # No debe romper — usa default
        assert resp.status_code == 200

    def test_limit_cap_a_100(self, client):
        # limit muy grande NO debe romper, capa a 100
        resp = client.get("/api/search?q=abc&limit=1000")
        assert resp.status_code == 200


# ─── Frontend smoke ───────────────────────────────────────────────────


class TestFrontendSmoke:
    def test_dashboard_html_contiene_caja_y_js(self, db_path, monkeypatch):
        monkeypatch.setattr("config.settings.DATABASE_PATH", db_path)
        import src.utils.dashboard_web as dw
        dw.DB_PATH = db_path
        html = dw._render_html()
        assert 'id="search-box"' in html
        assert 'id="search-results"' in html
        assert "/api/search" in html
        assert "Buscar expediente" in html or "buscar" in html.lower()

    def test_filas_tienen_data_numero(self, db_seed, db_path, monkeypatch):
        monkeypatch.setattr("config.settings.DATABASE_PATH", db_path)
        import src.utils.dashboard_web as dw
        dw.DB_PATH = db_path
        html = dw._render_html()
        # Al menos una fila debe tener data-numero
        assert 'data-numero="RDF-2026-001"' in html
        assert 'id="row-RDF-2026-001"' in html
