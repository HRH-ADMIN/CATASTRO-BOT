"""Tests del conversor número→letras (colones)."""
from __future__ import annotations

import pytest

from src.utils.num_to_letras import numero_a_letras_colones


class TestUnidades:
    def test_cero(self):
        assert numero_a_letras_colones(0) == "CERO COLONES"

    def test_uno(self):
        assert numero_a_letras_colones(1) == "UNO COLONES"

    def test_nueve(self):
        assert numero_a_letras_colones(9) == "NUEVE COLONES"


class TestDecenas:
    def test_diez(self):
        assert numero_a_letras_colones(10) == "DIEZ COLONES"

    def test_quince(self):
        assert numero_a_letras_colones(15) == "QUINCE COLONES"

    def test_veinte(self):
        assert numero_a_letras_colones(20) == "VEINTE COLONES"

    def test_veintidos(self):
        assert numero_a_letras_colones(22) == "VEINTIDÓS COLONES"

    def test_treinta_y_cinco(self):
        assert numero_a_letras_colones(35) == "TREINTA Y CINCO COLONES"

    def test_noventa_y_nueve(self):
        assert numero_a_letras_colones(99) == "NOVENTA Y NUEVE COLONES"


class TestCentenas:
    def test_cien(self):
        assert numero_a_letras_colones(100) == "CIEN COLONES"

    def test_ciento_uno(self):
        assert numero_a_letras_colones(101) == "CIENTO UNO COLONES"

    def test_doscientos(self):
        assert numero_a_letras_colones(200) == "DOSCIENTOS COLONES"

    def test_quinientos_treinta_y_cuatro(self):
        assert numero_a_letras_colones(534) == "QUINIENTOS TREINTA Y CUATRO COLONES"

    def test_novecientos_noventa_y_nueve(self):
        assert numero_a_letras_colones(999) == "NOVECIENTOS NOVENTA Y NUEVE COLONES"


class TestMiles:
    def test_mil(self):
        assert numero_a_letras_colones(1000) == "MIL COLONES"

    def test_dos_mil(self):
        assert numero_a_letras_colones(2000) == "DOS MIL COLONES"

    def test_5000_ajuste_oficina(self):
        # Ajuste fijo por plano
        assert numero_a_letras_colones(5000) == "CINCO MIL COLONES"

    def test_295352_seg_2026_001(self):
        # Honorarios de SEG-2026-001
        assert numero_a_letras_colones(295352) == (
            "DOSCIENTOS NOVENTA Y CINCO MIL TRESCIENTOS CINCUENTA Y DOS COLONES"
        )

    def test_999_999(self):
        assert numero_a_letras_colones(999_999) == (
            "NOVECIENTOS NOVENTA Y NUEVE MIL NOVECIENTOS NOVENTA Y NUEVE COLONES"
        )


class TestMillones:
    def test_un_millon(self):
        assert numero_a_letras_colones(1_000_000) == "UN MILLÓN COLONES"

    def test_dos_millones(self):
        assert numero_a_letras_colones(2_000_000) == "DOS MILLONES COLONES"

    def test_decreto_4878680(self):
        # Total del ejemplo del decreto con ajuste +5000
        assert numero_a_letras_colones(4_878_680) == (
            "CUATRO MILLONES OCHOCIENTOS SETENTA Y OCHO MIL SEISCIENTOS OCHENTA COLONES"
        )


class TestEdgeCases:
    def test_string_con_decimal(self):
        # Aceptar string con decimal (redondea al entero más cercano)
        assert numero_a_letras_colones("295352.45") == numero_a_letras_colones(295352)

    def test_string_con_coma_de_miles(self):
        assert numero_a_letras_colones("295,352") == numero_a_letras_colones(295352)

    def test_invalido_string(self):
        assert numero_a_letras_colones("abc") == ""

    def test_negativo(self):
        assert numero_a_letras_colones(-100) == ""

    def test_fuera_de_rango(self):
        # > 999_999_999
        assert numero_a_letras_colones(1_000_000_000) == ""
