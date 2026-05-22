"""Tests del state machine + resume — apt_progreso."""
from __future__ import annotations
import json
from unittest.mock import MagicMock

import pytest

from src.utils.apt_progreso import (
    obtener_progreso, marcar_seccion_completa, debe_skip_seccion,
    verificar_progreso_en_apt, sincronizar_con_live, resumen_progreso,
    SECCIONES_CONTRATO, SECCIONES_PLANO,
)


def _mock_db(metadata: dict | None = None, exp_id: str = "abc"):
    db = MagicMock()
    meta_json = json.dumps(metadata or {})
    db.obtener_expediente.return_value = {
        "id": exp_id,
        "numero_expediente": "RDF-2026-TEST",
        "metadata_json": meta_json,
    }
    return db


class TestObtenerProgreso:
    def test_sin_metadata_devuelve_estructura_vacia(self):
        db = _mock_db({})
        p = obtener_progreso(db, "abc")
        assert p["contrato_guardado"] is False
        assert p["plano_iniciado"] is False
        assert p["secciones"] == {}
        assert p["archivos_subidos"] == []
        assert p["enviado_cfia"] is False

    def test_contrato_guardado_derivado_de_apt_tramite(self):
        db = _mock_db({"apt_tramite": "1258126"})
        p = obtener_progreso(db, "abc")
        assert p["contrato_guardado"] is True

    def test_enviado_cfia_derivado_de_envio_fecha(self):
        db = _mock_db({"apt_envio_fecha": "2026-05-11T12:24:02"})
        p = obtener_progreso(db, "abc")
        assert p["enviado_cfia"] is True

    def test_lee_progreso_existente(self):
        db = _mock_db({
            "apt_progreso": {
                "contrato_guardado": True,
                "plano_iniciado": True,
                "secciones": {"bP1": True, "bP6": True},
                "archivos_subidos": ["anverso"],
                "enviado_cfia": False,
            }
        })
        p = obtener_progreso(db, "abc")
        assert p["secciones"]["bP1"] is True
        assert p["secciones"]["bP6"] is True
        assert "anverso" in p["archivos_subidos"]

    def test_db_none(self):
        p = obtener_progreso(None, "abc")
        assert p == {
            "contrato_guardado": False, "plano_iniciado": False,
            "secciones": {}, "archivos_subidos": [], "enviado_cfia": False,
        }


class TestMarcarSeccionCompleta:
    def test_marca_seccion_plano(self):
        db = _mock_db({})
        marcar_seccion_completa(db, "abc", "bP1")
        # Debió llamar actualizar_metadata con secciones[bP1]=True
        args = db.actualizar_metadata.call_args[0]
        assert args[1]["apt_progreso"]["secciones"]["bP1"] is True
        # plano_iniciado se marca true al marcar cualquier bP*
        assert args[1]["apt_progreso"]["plano_iniciado"] is True

    def test_marca_archivo_en_bp7(self):
        db = _mock_db({})
        marcar_seccion_completa(db, "abc", "bP7", archivo="anverso")
        args = db.actualizar_metadata.call_args[0]
        assert "anverso" in args[1]["apt_progreso"]["archivos_subidos"]
        # bP7 con archivo NO marca secciones[bP7] (es una lista separada)
        assert "bP7" not in args[1]["apt_progreso"]["secciones"]

    def test_no_duplica_archivos(self):
        db = _mock_db({
            "apt_progreso": {
                "archivos_subidos": ["anverso"],
                "secciones": {}, "contrato_guardado": False,
                "plano_iniciado": True, "enviado_cfia": False,
            }
        })
        marcar_seccion_completa(db, "abc", "bP7", archivo="anverso")
        args = db.actualizar_metadata.call_args[0]
        # No duplicado
        assert args[1]["apt_progreso"]["archivos_subidos"].count("anverso") == 1


class TestDebeSkipSeccion:
    def test_seccion_marcada_skip(self):
        progreso = {"secciones": {"bP1": True}}
        assert debe_skip_seccion(progreso, "bP1") is True

    def test_seccion_no_marcada_no_skip(self):
        progreso = {"secciones": {}}
        assert debe_skip_seccion(progreso, "bP1") is False

    def test_archivo_ya_subido_skip(self):
        progreso = {"archivos_subidos": ["anverso", "entero"]}
        assert debe_skip_seccion(progreso, "bP7", archivo="anverso") is True
        assert debe_skip_seccion(progreso, "bP7", archivo="derrotero") is False

    def test_progreso_vacio_no_skip(self):
        assert debe_skip_seccion({}, "bP1") is False
        assert debe_skip_seccion(None, "bP1") is False


class TestVerificarProgresoEnApt:
    def test_lee_iconos_de_la_pagina(self):
        page = MagicMock()
        page.evaluate.return_value = {
            "bC1": True, "bC2": True, "bC3": True, "bC4": True,
            "bC5": True, "bC6": True, "bC7": True, "bC8": True,
            "bP1": True, "bP2": False, "bP3": True, "bP4": True,
            "bP5": False, "bP6": False, "bP7": False,
        }
        estados = verificar_progreso_en_apt(page)
        assert estados["bP1"] is True
        assert estados["bP2"] is False

    def test_page_none_vacio(self):
        assert verificar_progreso_en_apt(None) == {}

    def test_evaluate_falla_vacio(self):
        page = MagicMock()
        page.evaluate.side_effect = Exception("APT cerrado")
        assert verificar_progreso_en_apt(page) == {}


class TestSincronizarConLive:
    def test_live_sobrescribe_secciones_no_marcadas(self):
        db = _mock_db({})
        estados = {"bP1": True, "bP2": True, "bP3": False}
        p = sincronizar_con_live(db, "abc", estados)
        assert p["secciones"]["bP1"] is True
        assert p["secciones"]["bP2"] is True
        # bP3 False en live: NO se setea (no marca como completo)
        assert p["secciones"].get("bP3") is not True

    def test_live_no_baja_lo_ya_completo(self):
        """Si saved tenía bP1=True y live ahora dice False (raro), saved wins."""
        db = _mock_db({
            "apt_progreso": {
                "secciones": {"bP1": True},
                "contrato_guardado": False, "plano_iniciado": True,
                "archivos_subidos": [], "enviado_cfia": False,
            }
        })
        # Live dice bP1=False — no debe bajarlo
        estados = {"bP1": False}
        p = sincronizar_con_live(db, "abc", estados)
        # bP1 saved era True, NO se baja
        assert p["secciones"]["bP1"] is True


class TestResumenProgreso:
    def test_resumen_legible(self):
        p = {
            "contrato_guardado": True,
            "plano_iniciado": True,
            "secciones": {"bP1": True, "bP6": True},
            "archivos_subidos": ["anverso"],
            "enviado_cfia": False,
        }
        s = resumen_progreso(p)
        assert "Contrato guardado" in s
        assert "bP1" in s
        assert "anverso" in s

    def test_resumen_enviado_cfia(self):
        p = {"enviado_cfia": True, "contrato_guardado": True,
             "secciones": {}, "archivos_subidos": []}
        assert "Enviado al CFIA" in resumen_progreso(p)
