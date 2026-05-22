"""Tests del topografo_resolver — multi-user support."""
from __future__ import annotations
import json
from unittest.mock import MagicMock

from src.utils.topografo_resolver import resolver_topografo, CORREO_APT_DEFAULT


def _make_db(topografos: list[dict]):
    db = MagicMock()
    db.listar_usuarios.return_value = topografos
    return db


class TestResolverTopografo:
    def test_match_por_cedula(self):
        topografos = [
            {"nombre": "OTRO USUARIO", "cedula": "9-9999-9999",
             "telefono": "50699999999", "protocolo_activo": "00000",
             "correo_apt": "otro@y.cr", "carne_cfia": "IT00001"},
            {"nombre": "ROJAS HERRERA LUIS ALONSO", "cedula": "0205300432",
             "telefono": "50688387310", "protocolo_activo": "24162",
             "correo_apt": "topografiahrh@gmail.com", "carne_cfia": "IT10676"},
        ]
        db = _make_db(topografos)
        exp = {
            "nombre_topografo": "ROJAS HERRERA LUIS ALONSO",
            "cedula_topografo": "0205300432",
        }
        r = resolver_topografo(db, exp)
        assert r["fuente"] == "match_cedula"
        assert r["protocolo_activo"] == "24162"
        assert r["correo_apt"] == "topografiahrh@gmail.com"
        assert r["carne_cfia"] == "IT10676"

    def test_match_por_nombre_case_insensitive(self):
        topografos = [
            {"nombre": "rojas herrera luis alonso", "cedula": "",
             "telefono": "...", "protocolo_activo": "24162",
             "correo_apt": "x@y.cr"},
        ]
        db = _make_db(topografos)
        exp = {
            "nombre_topografo": "ROJAS HERRERA LUIS ALONSO",
            "cedula_topografo": "",
        }
        r = resolver_topografo(db, exp)
        assert r["fuente"] == "match_nombre"

    def test_match_nombre_con_acentos(self):
        topografos = [
            {"nombre": "JOSÉ MARÍA PÉREZ", "cedula": "",
             "telefono": "x", "protocolo_activo": "111",
             "correo_apt": "j@y.cr"},
        ]
        db = _make_db(topografos)
        exp = {"nombre_topografo": "JOSE MARIA PEREZ", "cedula_topografo": ""}
        r = resolver_topografo(db, exp)
        assert r["fuente"] == "match_nombre"

    def test_match_por_telefono_operador(self):
        topografos = [
            {"nombre": "OTRO", "cedula": "1-1111-1111", "telefono": "50611112222",
             "protocolo_activo": "X", "correo_apt": "a@b.cr"},
        ]
        db = _make_db(topografos)
        exp = {
            "nombre_topografo": "DESCONOCIDO",
            "cedula_topografo": "",
            "metadata_json": json.dumps({"operador_telefono": "+50611112222"}),
        }
        r = resolver_topografo(db, exp)
        assert r["fuente"] == "match_telefono"

    def test_sin_match_devuelve_default(self):
        db = _make_db([])
        exp = {"nombre_topografo": "INEXISTENTE", "cedula_topografo": ""}
        r = resolver_topografo(db, exp)
        assert r["fuente"] == "default"
        assert r["correo_apt"] == CORREO_APT_DEFAULT

    def test_expediente_vacio(self):
        db = _make_db([])
        r = resolver_topografo(db, None)
        assert r["fuente"] == "default"
        r = resolver_topografo(db, {})
        assert r["fuente"] == "default"

    def test_db_falla_usa_default(self):
        db = MagicMock()
        db.listar_usuarios.side_effect = Exception("DB down")
        exp = {"nombre_topografo": "X", "cedula_topografo": "Y"}
        r = resolver_topografo(db, exp)
        assert r["fuente"] == "default"

    def test_cedula_prevalece_sobre_nombre(self):
        """Si hay match por cédula Y por nombre (otro usuario), cédula gana."""
        topografos = [
            {"nombre": "ROJAS HERRERA LUIS ALONSO", "cedula": "X-XXXX-XXXX",
             "telefono": "1", "protocolo_activo": "AAA", "correo_apt": "wrong@y.cr"},
            {"nombre": "OTRO NOMBRE", "cedula": "0205300432",
             "telefono": "2", "protocolo_activo": "BBB", "correo_apt": "right@y.cr"},
        ]
        db = _make_db(topografos)
        exp = {
            "nombre_topografo": "ROJAS HERRERA LUIS ALONSO",
            "cedula_topografo": "0205300432",
        }
        r = resolver_topografo(db, exp)
        # Cédula match al segundo, NO al primero
        assert r["protocolo_activo"] == "BBB"
        assert r["correo_apt"] == "right@y.cr"

    def test_topografo_sin_protocolo_usa_settings_default(self, monkeypatch):
        """Si el usuario existe pero no tiene protocolo_activo seteado,
        cae al setting global."""
        monkeypatch.setattr("config.settings.PROTOCOLO_ACTIVO_TOPOGRAFO", "24162")
        topografos = [
            {"nombre": "ROJAS HERRERA LUIS ALONSO", "cedula": "0205300432",
             "telefono": "x", "protocolo_activo": None, "correo_apt": "x@y.cr"},
        ]
        db = _make_db(topografos)
        exp = {"nombre_topografo": "ROJAS HERRERA LUIS ALONSO",
               "cedula_topografo": "0205300432"}
        r = resolver_topografo(db, exp)
        assert r["protocolo_activo"] == "24162"
