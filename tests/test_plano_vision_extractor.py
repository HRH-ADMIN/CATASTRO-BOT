"""Tests del PlanoVisionExtractor — mocks de Anthropic API.

Verifica que el flujo de extracción funciona end-to-end con respuestas
simuladas (no hace calls reales a la API).
"""
from __future__ import annotations
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("anthropic")

from src.utils.plano_vision_extractor import (
    PlanoVisionExtractor, CajetinData, RegistroData, EnteroData,
    _build_dataclass,
)


def _make_mock_response(json_str: str):
    """Mock que parece anthropic.Messages.create() response."""
    block = MagicMock()
    block.text = json_str
    resp = MagicMock()
    resp.content = [block]
    return resp


def _make_extractor_with_response(json_str: str) -> PlanoVisionExtractor:
    with patch("anthropic.Anthropic") as mock_anthropic:
        client = MagicMock()
        client.messages.create.return_value = _make_mock_response(json_str)
        mock_anthropic.return_value = client
        return PlanoVisionExtractor(api_key="fake")


class TestBuildDataclass:
    def test_campos_validos(self):
        c = _build_dataclass(CajetinData, {
            "descripcion": "ROVUELT(1)",
            "protocolo_tomo": "23549",
        })
        assert c.descripcion == "ROVUELT(1)"
        assert c.protocolo_tomo == "23549"

    def test_ignora_campos_extra(self):
        # Vision podría devolver campos inventados — deben ignorarse
        c = _build_dataclass(CajetinData, {
            "descripcion": "X",
            "campo_inventado": "ignorado",
        })
        assert c.descripcion == "X"

    def test_data_vacia(self):
        c = _build_dataclass(CajetinData, {})
        assert c.descripcion == ""
        c = _build_dataclass(CajetinData, None)
        assert c.descripcion == ""


class TestExtractCajetin:
    def test_respuesta_completa(self, tmp_path):
        # Crear un PDF dummy
        pdf = tmp_path / "plano.pdf"
        pdf.write_bytes(b"%PDF-1.4\n%dummy")

        json_resp = json.dumps({
            "descripcion": "FELIPETIOS(4)",
            "protocolo_tomo": "23549",
            "protocolo_folio": "178",
            "numero_entero": "660822113",
            "area_real": "582.67",
            "area_registro": "741.68",
            "folio_real": "2422958-000",
            "provincia_nombre": "ALAJUELA",
            "canton_nombre": "SAN RAMON",
            "distrito_nombre": "ALFARO",
            "profesional_carne": "IT10676",
            "planos_modificar": [{"letra": "A", "numero": "1095215", "anno": "2006"}],
            "coordenadas": [[445579.27, 1114039.40], [445588.57, 1114036.72]],
        })
        ex = _make_extractor_with_response(json_resp)
        c = ex.extract_cajetin(pdf)
        assert c.descripcion == "FELIPETIOS(4)"
        assert c.protocolo_tomo == "23549"
        assert c.numero_entero == "660822113"
        assert len(c.coordenadas) == 2
        assert c.planos_modificar[0]["numero"] == "1095215"

    def test_response_con_fences_markdown(self, tmp_path):
        """A veces el modelo devuelve ```json ... ```; debe limpiar."""
        pdf = tmp_path / "plano.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        json_resp = '```json\n{"descripcion": "X"}\n```'
        ex = _make_extractor_with_response(json_resp)
        c = ex.extract_cajetin(pdf)
        assert c.descripcion == "X"

    def test_archivo_inexistente(self):
        ex = _make_extractor_with_response("{}")
        c = ex.extract_cajetin(Path("/no/existe.pdf"))
        # Debe devolver dataclass vacía sin crashear
        assert c.descripcion == ""

    def test_json_invalido_devuelve_vacio(self, tmp_path):
        pdf = tmp_path / "plano.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        ex = _make_extractor_with_response("not json at all")
        c = ex.extract_cajetin(pdf)
        assert c.descripcion == ""


class TestExtractRegistro:
    def test_propietario_fisica(self, tmp_path):
        img = tmp_path / "registro.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n")
        json_resp = json.dumps({
            "finca": "422958",
            "derecho": "000",
            "tipo_propietario": "FISICA",
            "cedula_propietario": "2-0466-0095",
            "nombre_propietario": "LUIS EMILIO DE LOS ANGELES",
            "apellido1_propietario": "PIÑEIRO",
            "apellido2_propietario": "CASTRO",
            "naturaleza": "TERRENO DE CAFE",
            "plano_previo": "A-1095215-2006",
            "es_parte_de": True,
            "anotaciones": True,
            "gravamenes": True,
        })
        ex = _make_extractor_with_response(json_resp)
        r = ex.extract_registro(img)
        assert r.finca == "422958"
        assert r.tipo_propietario == "FISICA"
        assert r.cedula_propietario == "2-0466-0095"
        assert r.apellido1_propietario == "PIÑEIRO"  # ñ
        assert r.es_parte_de is True

    def test_juridica_con_cedula_truncada(self, tmp_path):
        """Caso ROVUELT — registro mostraba 3-101- (truncado)."""
        img = tmp_path / "registro.png"
        img.write_bytes(b"\x89PNG\r\n")
        json_resp = json.dumps({
            "tipo_propietario": "JURIDICA",
            "cedula_propietario": "3-101-",  # truncado
            "cedula_registro_original": "3-101-",
            "nombre_propietario": "SOCIEDAD AGROPECUARIA LAS ESTUFAS",
        })
        ex = _make_extractor_with_response(json_resp)
        r = ex.extract_registro(img)
        assert r.cedula_registro_original == "3-101-"

    def test_media_type_por_extension(self, tmp_path):
        """Verifica que se usa el media_type correcto según .png/.jpg."""
        for ext, expected in [("png", "image/png"), ("jpg", "image/jpeg"), ("jpeg", "image/jpeg")]:
            img = tmp_path / f"reg.{ext}"
            img.write_bytes(b"data")
            with patch("anthropic.Anthropic") as mock_anth:
                client = MagicMock()
                client.messages.create.return_value = _make_mock_response("{}")
                mock_anth.return_value = client
                ex = PlanoVisionExtractor(api_key="x")
                ex.extract_registro(img)
                # Verificar que el media_type pasado al API matchea
                call = client.messages.create.call_args
                content = call.kwargs["messages"][0]["content"]
                source = content[0]["source"]
                assert source["media_type"] == expected


class TestExtractEntero:
    def test_entero_completo(self, tmp_path):
        pdf = tmp_path / "entero.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        json_resp = json.dumps({
            "numero": "660822113",
            "fecha": "2026-05-08",
            "monto_tasado": "11940.00",
            "monto_pagado": "11241.60",
            "timbre_cfia": "1600",
            "timbre_registro": "10000",
            "timbre_cit_ntrip": "300",
        })
        ex = _make_extractor_with_response(json_resp)
        e = ex.extract_entero(pdf)
        assert e.numero == "660822113"
        assert e.fecha == "2026-05-08"
        assert e.monto_tasado == "11940.00"
        assert e.timbre_cfia == "1600"
