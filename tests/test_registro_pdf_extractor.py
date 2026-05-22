"""Tests para src/utils/registro_pdf_extractor.py.

Casos basados en JAVSAL (finca 562690 — propietario físico) y ROLESQ
(finca 118879 — propietario jurídico). Verifican el contrato del parser
pypdf+regex que es el fallback cuando no hay Anthropic API key.
"""
from __future__ import annotations

import pytest

from src.utils.registro_pdf_extractor import (
    convertir_letras_a_numero,
    parsear_area_letras_completa,
    _RE_FINCA,
    _RE_DISTRITO_CANTON,
    _RE_NATURALEZA,
    _RE_PROPIETARIO_FISICA,
    _RE_PROPIETARIO_JURIDICA,
    _RE_ES_PARTE,
)


# ── convertir_letras_a_numero ──────────────────────────────────────────

class TestConvertirLetrasANumero:
    @pytest.mark.parametrize("texto,esperado", [
        ("UNO", 1),
        ("DIEZ", 10),
        ("CIEN", 100),
        ("DOSCIENTOS CINCUENTA", 250),
        ("MIL", 1000),
        ("DOS MIL", 2000),
        ("MIL CIEN", 1100),
        ("CINCO MIL CUATROCIENTOS CINCUENTA", 5450),
    ])
    def test_casos_basicos(self, texto, esperado):
        assert convertir_letras_a_numero(texto) == float(esperado)

    def test_javsal_33440(self):
        # "TREINTA Y TRES MIL CUATROCIENTOS CUARENTA"
        assert convertir_letras_a_numero(
            "TREINTA Y TRES MIL CUATROCIENTOS CUARENTA"
        ) == 33440.0

    def test_rolesq_15364(self):
        assert convertir_letras_a_numero(
            "QUINCE MIL TRESCIENTOS SESENTA Y CUATRO"
        ) == 15364.0

    def test_vacio_devuelve_none(self):
        assert convertir_letras_a_numero("") is None


# ── parsear_area_letras_completa ───────────────────────────────────────

class TestParsearAreaCompleta:
    def test_sin_decimales(self):
        r = parsear_area_letras_completa(
            "TREINTA Y TRES MIL CUATROCIENTOS CUARENTA"
        )
        assert r == "33440.00"

    def test_con_decimales(self):
        # "QUINCE MIL TRESCIENTOS SESENTA Y CUATRO METROS CON CUARENTA Y DOS DECIMETROS"
        r = parsear_area_letras_completa(
            "QUINCE MIL TRESCIENTOS SESENTA Y CUATRO METROS "
            "CON CUARENTA Y DOS DECIMETROS CUADRADOS"
        )
        assert r == "15364.42"

    def test_decimal_un_digito(self):
        # "CUARENTA Y CINCO METROS CON CINCO DECIMETROS" → 45.05
        r = parsear_area_letras_completa(
            "CUARENTA Y CINCO METROS CON CINCO DECIMETROS CUADRADOS"
        )
        assert r == "45.05"


# ── Regex finca ─────────────────────────────────────────────────────────

class TestFincaRegex:
    def test_javsal(self):
        m = _RE_FINCA.search(
            "FINCA: 562690 DUPLICADO:  HORIZONTAL:  DERECHO: 000"
        )
        assert m
        assert m.group(1) == "562690"
        assert m.group(3) == "000"

    def test_rolesq(self):
        m = _RE_FINCA.search(
            "FINCA: 118879 DUPLICADO:  HORIZONTAL:  DERECHO: 000"
        )
        assert m
        assert m.group(1) == "118879"


# ── Distrito/Cantón ─────────────────────────────────────────────────────

class TestDistritoCanton:
    def test_javsal(self):
        m = _RE_DISTRITO_CANTON.search(
            "SITUADA EN EL DISTRITO 5-PIEDADES SUR CANTON 2-SAN RAMON "
            "DE LA PROVINCIA DE ALAJUELA"
        )
        assert m
        assert "PIEDADES" in m.group(2)
        assert "SAN RAMON" in m.group(4)

    def test_rolesq(self):
        m = _RE_DISTRITO_CANTON.search(
            "SITUADA EN EL DISTRITO 7-SAN ISIDRO CANTON 2-SAN RAMON "
            "DE LA PROVINCIA DE ALAJUELA"
        )
        assert m
        assert "SAN ISIDRO" in m.group(2)


# ── Propietario físico vs jurídico ──────────────────────────────────────

class TestPropietario:
    def test_fisica_javsal(self):
        m = _RE_PROPIETARIO_FISICA.search(
            "PROPIETARIO:\nJAVIER CASTRO JIMENEZ\nCEDULA IDENTIDAD 2-0291-1270"
        )
        assert m
        assert "JAVIER" in m.group(1)
        assert m.group(2) == "2-0291-1270"

    def test_juridica_rolesq(self):
        m = _RE_PROPIETARIO_JURIDICA.search(
            "PROPIETARIO:\n"
            "AGROPECUARIA LAS ESTUFAS SOCIEDAD ANONIMA\n"
            "CEDULA JURIDICA 3-101-044683"
        )
        assert m
        assert "AGROPECUARIA" in m.group(1)
        assert m.group(2) == "3-101-044683"


# ── ES PARTE DE (flag de segregación previa) ───────────────────────────

class TestEsParteDe:
    def test_detecta(self):
        # ROLESQ tiene "ES PARTE DE FOLIO REAL" implícito en algunos templates
        assert _RE_ES_PARTE.search("ESTE LOTE ES PARTE DE LA FINCA 118879") is not None

    def test_no_match_si_no_aparece(self):
        assert _RE_ES_PARTE.search("FINCA INDEPENDIENTE") is None
