"""Tests del helper display_proyecto.

Garantiza que los mensajes WhatsApp/log identifican el plano por nombre
del proyecto (legible para el operador) en vez de por número RDF-XXXX.
"""
from __future__ import annotations
import json

import pytest

from src.utils.display import display_proyecto, display_solo_proyecto


class TestDisplayProyecto:
    def test_metadata_json_string_con_proyecto(self):
        exp = {
            "numero_expediente": "RDF-2026-002",
            "metadata_json": json.dumps({"nombre_proyecto": "OMAR_2026"}),
        }
        assert display_proyecto(exp) == "OMAR_2026 (RDF-2026-002)"

    def test_metadata_json_string_sin_proyecto_fallback_a_numero(self):
        exp = {
            "numero_expediente": "RDF-2026-001",
            "metadata_json": "{}",
        }
        assert display_proyecto(exp) == "RDF-2026-001"

    def test_metadata_json_invalido_no_explota(self):
        exp = {
            "numero_expediente": "RDF-2026-001",
            "metadata_json": "{invalid json",
        }
        assert display_proyecto(exp) == "RDF-2026-001"

    def test_dict_ya_parseado_con_metadata_json_dict(self):
        """Si pre-parsearon metadata_json como dict, también funciona."""
        exp = {
            "numero_expediente": "RDF-2026-002",
            "metadata_json": {"nombre_proyecto": "OMAR_2026"},
        }
        assert display_proyecto(exp) == "OMAR_2026 (RDF-2026-002)"

    def test_dict_de_metadata_directa(self):
        """Pasando solo metadata sin numero_expediente."""
        meta = {"nombre_proyecto": "OMAR_2026"}
        assert display_proyecto(meta) == "OMAR_2026"

    def test_con_numero_false_oculta_numero(self):
        exp = {
            "numero_expediente": "RDF-2026-002",
            "metadata_json": json.dumps({"nombre_proyecto": "OMAR_2026"}),
        }
        assert display_proyecto(exp, con_numero=False) == "OMAR_2026"

    def test_display_solo_proyecto_atajo(self):
        exp = {
            "numero_expediente": "RDF-2026-002",
            "metadata_json": json.dumps({"nombre_proyecto": "OMAR_2026"}),
        }
        assert display_solo_proyecto(exp) == "OMAR_2026"

    def test_proyecto_con_espacios_y_caracteres_especiales(self):
        exp = {
            "numero_expediente": "RDF-2026-005",
            "metadata_json": json.dumps({"nombre_proyecto": "Rolando Granja"}),
        }
        assert display_proyecto(exp) == "Rolando Granja (RDF-2026-005)"

    def test_nombre_proyecto_vacio_fallback(self):
        exp = {
            "numero_expediente": "RDF-2026-001",
            "metadata_json": json.dumps({"nombre_proyecto": "   "}),
        }
        # Solo whitespace → fallback a número
        assert display_proyecto(exp) == "RDF-2026-001"

    def test_input_none(self):
        assert display_proyecto(None) == ""
        assert display_proyecto({}) == ""

    def test_solo_numero_sin_metadata(self):
        exp = {"numero_expediente": "RDF-2026-001"}
        assert display_proyecto(exp) == "RDF-2026-001"

    def test_alias_numero_en_lugar_de_numero_expediente(self):
        """Algunos lugares usan dict con `numero` en vez de `numero_expediente`."""
        exp = {"numero": "RDF-2026-001", "metadata_json": "{}"}
        assert display_proyecto(exp) == "RDF-2026-001"
