"""Tests para src/utils/tipo_uso_mapper.py.

Mapea NATURALEZA del registro RNP al código tipo_uso de APT (#ddlTipoUso).
Casos aprendidos con JAVSAL (TERRENO DE PASTO → 36) y ROLESQ (TERRENO
SEMBRADO DE CAFE → 23).
"""
from __future__ import annotations

import pytest

from src.utils.tipo_uso_mapper import (
    mapear_tipo_uso,
    TIPO_USO_SOLAR,
    TIPO_USO_CONSTRUIDO_Y_SOLAR,
    TIPO_USO_REPASTOS,
    TIPO_USO_CULTIVOS_VARIOS,
    TIPO_USO_BOSQUE,
    TIPO_USO_FRUTALES,
    TIPO_USO_AGRICULTURA,
    TIPO_USO_RESIDENCIAL,
)


class TestMapeoTipoUso:
    @pytest.mark.parametrize("naturaleza,esperado", [
        # Pasto / repasto / potrero — JAVSAL caso real
        ("TERRENO DE PASTO",              TIPO_USO_REPASTOS),
        ("TERRENO DE REPASTOS",           TIPO_USO_REPASTOS),
        ("POTRERO",                       TIPO_USO_REPASTOS),

        # Café / cultivos — ROLESQ caso real
        ("TERRENO SEMBRADO DE CAFE",      TIPO_USO_CULTIVOS_VARIOS),
        ("TERRENO DE CULTIVO",            TIPO_USO_CULTIVOS_VARIOS),
        ("TERRENO AGRICOLA",              TIPO_USO_CULTIVOS_VARIOS),

        # Solar (lote sin construcción)
        ("SOLAR",                         TIPO_USO_SOLAR),
        ("TERRENO SOLAR",                 TIPO_USO_SOLAR),

        # Construido + solar (compuesto)
        ("CONSTRUIDO Y SOLAR",            TIPO_USO_CONSTRUIDO_Y_SOLAR),
        ("TERRENO CONSTRUIDO Y SOLAR",    TIPO_USO_CONSTRUIDO_Y_SOLAR),

        # Frutales
        ("TERRENO DE FRUTALES",           TIPO_USO_FRUTALES),

        # Bosque / charral
        ("BOSQUE",                        TIPO_USO_BOSQUE),
        ("TERRENO DE CHARRAL",            TIPO_USO_BOSQUE),

        # Caña / sembrado → agricultura
        ("TERRENO DE CAÑA",               TIPO_USO_AGRICULTURA),
        ("TERRENO SEMBRADO",              TIPO_USO_AGRICULTURA),

        # Habitación / residencial
        ("TERRENO DE HABITACION",         TIPO_USO_RESIDENCIAL),
        ("RESIDENCIAL",                   TIPO_USO_RESIDENCIAL),
    ])
    def test_naturaleza_mapea(self, naturaleza, esperado):
        assert mapear_tipo_uso(naturaleza) == esperado

    def test_vacio(self):
        assert mapear_tipo_uso("") == ""

    def test_no_reconocido_devuelve_vacio(self):
        # Naturalezas raras que no debemos inventar
        assert mapear_tipo_uso("TERRENO INDUSTRIAL ESPECIAL") == ""

    def test_construido_y_solar_no_se_confunde_con_solar(self):
        # Caso edge: el orden importa — "construido y solar" debe ganar
        assert mapear_tipo_uso("CONSTRUIDO Y SOLAR") == TIPO_USO_CONSTRUIDO_Y_SOLAR
        assert mapear_tipo_uso("SOLAR") == TIPO_USO_SOLAR
