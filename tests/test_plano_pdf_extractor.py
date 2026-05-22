"""Tests para src/utils/plano_pdf_extractor.py.

Casos derivados del trabajo real con SEG-2026-005 (JAVSAL) y SEG-2026-006
(ROLESQ) — planos donde el cajetín es vector pero el cuerpo es texto plano.

Si Vision no está disponible, los regex del extractor son la única forma de
obtener datos del plano. Estos tests congelan ese contrato.
"""
from __future__ import annotations

import re

import pytest

from src.utils.plano_pdf_extractor import (
    _RE_AREA_NUEVA,
    _RE_AREA_REGISTRO,
    _RE_CARNE_IT,
    _RE_ID_PREDIAL,
    _RE_MODIFICA,
    _RE_PROTOCOLO_TOMO,
    _RE_PROTOCOLO_FOLIO,
    _RE_TOPOG_NOMBRE,
    _RE_USO_DESCRITO,
    _parsear_vertices_a_via,
)


# ── Fixtures: texto real extraído de pypdf en JAVSAL y ROLESQ ──────────

JAVSAL_TEXTO = """N O T A SLEVANTAMIENTO POLAR, POLIGONAL ABIERTA, ERRORES ESTIMADOS:ANGULAR 00° 5' LINEAL 0.10mDATOS TOMADOS DE LA ORTOFOTO OFICIALCR-SIRGAS, ÉPOCA 2014.59EPOCA 2014.59, SISTEMA CR-SIRGASPROYECCIÓN CRTM05ESCALA DEL MAPA CATASTRAL  1:5000.EXACTITUD RELATIVA DE +/-0.10m.EXACTITUD ABSOLUTA +/-1m.
²
12 34
567891011121314
1516
LISTADO DE COORDENADASPUNTOESTE (m)NORTE (m)1432455.161113872.292432485.041113888.233432510.231113903.524432513.291113898.295432532.441113855.986432537.771113846.927432538.321113842.188432535.181113828.209432527.631113818.8110432516.041113809.0111432513.911113807.0612432508.371113800.1113432506.171113788.8014432485.881113771.8215432447.991113834.2716432455.781113850.50
7000.00 m²
REFERNCIA  LPLP432805.901114955.70PUNTOESTENORTECOORDENADAS CRTM05USO MIXTO (AGRICOLA Y RESIDENCIAL)Distancia Frente a Calle Pública Vértices N˚1, N˚15 y N˚16 = 56.69 mMODIFICA PLANO CATASTRADO N° A-2025739-2018"""

ROLESQ_TEXTO = """11836.23m2
LISTADO DE COORDENADASPUNTOESTE (m)NORTE (m)1451015.051115768.31...24452.141115780.24
AREA
SITUADO EN : SAN ISIDRODISTRITO 07 SAN ISIDROCANTON 02 SAN RAMONPROVINCIA 02 ALAJUELA
ESCALA 1: 2000ROESQUINAPROFESIONAL RESPONSABLE
LUIS ALONSO ROJAS HERRERATOPOGRAFO ASOCIADO I.T 10676
FECHAMAYO / 2026PROTOCOLOTOMO 24161FOLIO 106
A SAN RAMONESTE 450980.69NORTE 1115577.52PI
N O T A SLEVANTAMIENTO POLAR
MODIFICA PLANO CATASTRADO: 2-62586-2025 Y A-1850367-2015PARA USO MIXTO, AGRICOLA Y URBANOINFORMACION DE REGISTROIDENTIFICADOR PREDIAL: 202070118879ES PARTE DE FOLIO REAL N°2 118879-000AREA SEGUN REGISTRO15364.42m²
Distancia Frente a Calle Pública Vértices N˚1 al N˚5 = 42.83 m"""


# ── Áreas ──────────────────────────────────────────────────────────────

class TestAreaNueva:
    def test_javsal_area_7000(self):
        # El "7000.00 m²" aparece pegado a "...432455.781113850.50\n7000.00 m²"
        m = _RE_AREA_NUEVA.search(JAVSAL_TEXTO)
        assert m, "área nueva no detectada"
        assert m.group(1) == "7000.00"

    def test_rolesq_area_11836(self):
        m = _RE_AREA_NUEVA.search(ROLESQ_TEXTO)
        assert m
        assert m.group(1) == "11836.23"

    def test_no_capturar_coordenada(self):
        # Una coordenada tipo 432455.16 no es área (no termina en m²)
        m = _RE_AREA_NUEVA.search("432455.161113872.29\n7000.00 m²")
        assert m.group(1) == "7000.00"


class TestAreaRegistro:
    def test_rolesq_15364(self):
        m = _RE_AREA_REGISTRO.search(ROLESQ_TEXTO)
        assert m
        assert m.group(1).replace(",", "") == "15364.42"

    def test_formato_con_coma(self):
        # "33,440 m²" formato con coma de miles
        m = _RE_AREA_REGISTRO.search("AREA SEGUN REGISTRO 33,440 m²")
        assert m
        assert m.group(1) == "33,440"


# ── Identificador predial ──────────────────────────────────────────────

class TestIdPredial:
    def test_rolesq_no_captura_ES(self):
        # Bug histórico: "202070118879ES PARTE..." → capturaba "202070118879ES"
        m = _RE_ID_PREDIAL.search(ROLESQ_TEXTO)
        assert m
        assert m.group(1) == "202070118879"
        assert "E" not in m.group(1)


# ── Carné topógrafo ────────────────────────────────────────────────────

class TestCarneIT:
    @pytest.mark.parametrize("variante", [
        "I.T 10676",
        "IT 10676",
        "IT10676",
        "i.t. 10676",
    ])
    def test_variantes_carne(self, variante):
        m = _RE_CARNE_IT.search(variante)
        assert m


# ── Protocolo tomo+folio ───────────────────────────────────────────────

class TestProtocoloTomo:
    def test_rolesq_24161(self):
        m = _RE_PROTOCOLO_TOMO.search(ROLESQ_TEXTO)
        assert m
        assert m.group(1) == "24161"

    def test_folio_cercano(self):
        m = _RE_PROTOCOLO_FOLIO.search("TOMO 24161FOLIO 106")
        assert m
        assert m.group(1) == "106"


# ── Modifica plano ─────────────────────────────────────────────────────

class TestModificaPlano:
    def test_javsal_un_plano(self):
        m = _RE_MODIFICA.search(JAVSAL_TEXTO)
        assert m
        assert "A-2025739-2018" in m.group(1)

    def test_rolesq_dos_planos(self):
        m = _RE_MODIFICA.search(ROLESQ_TEXTO)
        assert m
        # Captura ambos: "2-62586-2025 Y A-1850367-2015"
        assert "2-62586-2025" in m.group(1)
        assert "A-1850367-2015" in m.group(1)


# ── Vértices a vía ─────────────────────────────────────────────────────

class TestVerticesAVia:
    def test_javsal_lista_explicita(self):
        # "Vértices N°1, N°15 y N°16 = 56.69 m"
        lista, frente = _parsear_vertices_a_via(JAVSAL_TEXTO)
        assert lista == "1-15-16"
        assert frente == "56.69"

    def test_rolesq_rango(self):
        # "Vértices N°1 al N°5 = 42.83 m" → expansión del rango
        lista, frente = _parsear_vertices_a_via(ROLESQ_TEXTO)
        assert lista == "1-2-3-4-5"
        assert frente == "42.83"

    def test_sin_match(self):
        lista, frente = _parsear_vertices_a_via("nada de vértices aquí")
        assert lista == ""
        assert frente == ""


# ── Uso descrito ───────────────────────────────────────────────────────

class TestUsoDescrito:
    def test_javsal_uso_mixto(self):
        m = _RE_USO_DESCRITO.search(JAVSAL_TEXTO)
        assert m
        # No debe pegar "Distancia Frente..." después
        captura = m.group(1).strip()
        assert "USO MIXTO" in captura
        assert "Distancia" not in captura
        assert "MODIFICA" not in captura

    def test_rolesq_uso_mixto(self):
        m = _RE_USO_DESCRITO.search(ROLESQ_TEXTO)
        assert m
        captura = m.group(1).strip()
        assert "USO MIXTO" in captura
        assert "INFORMACION" not in captura


# ── Topógrafo nombre ───────────────────────────────────────────────────

class TestTopografo:
    def test_rolesq_nombre_completo(self):
        m = _RE_TOPOG_NOMBRE.search(ROLESQ_TEXTO)
        assert m
        nombre = " ".join(m.group(1).split())
        assert nombre == "LUIS ALONSO ROJAS HERRERA"
