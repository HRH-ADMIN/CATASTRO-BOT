"""Tests del fallback pypdf+regex cuando Vision falla o falta API key."""
from __future__ import annotations
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


def _make_mock_response(json_str: str):
    block = MagicMock()
    block.text = json_str
    resp = MagicMock()
    resp.content = [block]
    return resp


class TestSinApiKeyVaAPypdf:
    def test_sin_api_key_usa_pypdf(self, tmp_path):
        """Sin api_key, va directo a pypdf — no llama Anthropic."""
        from src.utils.plano_vision_extractor import extract_cajetin_con_fallback
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-1.4\n%dummy")
        # Mock plano_pdf_extractor para devolver algo
        from src.utils.plano_pdf_extractor import PlanoMetadata
        with patch(
            "src.utils.plano_pdf_extractor.extraer_plano_metadata",
            return_value=PlanoMetadata(
                protocolo_tomo="23549",
                numero_entero="660822113",
                area_m2="582.67",
                profesional_carne="IT10676",
                extraido_via="pypdf",
            ),
        ):
            cajetin = extract_cajetin_con_fallback(pdf, api_key=None)
        assert cajetin.protocolo_tomo == "23549"
        assert cajetin.numero_entero == "660822113"
        assert cajetin.area_real == "582.67"  # area_m2 → area_real
        assert cajetin.profesional_carne == "IT10676"


class TestVisionExitosaNoUsaFallback:
    def test_vision_devuelve_completo_no_va_a_pypdf(self, tmp_path):
        import json
        from src.utils.plano_vision_extractor import extract_cajetin_con_fallback
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        vision_resp = json.dumps({
            "descripcion": "FELIPETIOS(4)",
            "protocolo_tomo": "23549",
            "protocolo_folio": "178",
            "numero_entero": "660822113",
            "area_real": "582.67",
            "folio_real": "2422958-000",
        })
        with patch("anthropic.Anthropic") as mock_anth:
            client = MagicMock()
            client.messages.create.return_value = _make_mock_response(vision_resp)
            mock_anth.return_value = client
            with patch(
                "src.utils.plano_pdf_extractor.extraer_plano_metadata"
            ) as mock_pypdf:
                cajetin = extract_cajetin_con_fallback(pdf, api_key="sk-ant-x")
        assert cajetin.descripcion == "FELIPETIOS(4)"
        assert cajetin.protocolo_tomo == "23549"
        # pypdf NO debe haber sido llamado
        mock_pypdf.assert_not_called()


class TestVisionPocoCompleta:
    def test_vision_pocos_campos_complementa_con_pypdf(self, tmp_path):
        """Si Vision devuelve solo 1-2 campos, usar pypdf también."""
        import json
        from src.utils.plano_vision_extractor import extract_cajetin_con_fallback
        from src.utils.plano_pdf_extractor import PlanoMetadata
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        # Vision devuelve solo 1 campo
        vision_resp = json.dumps({"descripcion": "PARCIAL(1)"})
        with patch("anthropic.Anthropic") as mock_anth:
            client = MagicMock()
            client.messages.create.return_value = _make_mock_response(vision_resp)
            mock_anth.return_value = client
            # pypdf complementa
            with patch(
                "src.utils.plano_pdf_extractor.extraer_plano_metadata",
                return_value=PlanoMetadata(
                    protocolo_tomo="999",
                    numero_entero="660000000",
                    area_m2="100",
                ),
            ):
                cajetin = extract_cajetin_con_fallback(pdf, api_key="sk-ant-x")
        # descripcion viene de Vision (no la sobrescribe pypdf)
        assert cajetin.descripcion == "PARCIAL(1)"
        # protocolo_tomo viene de pypdf (Vision no lo tenía)
        assert cajetin.protocolo_tomo == "999"
        assert cajetin.numero_entero == "660000000"


class TestVisionExceptionCaeAPypdf:
    def test_vision_lanza_excepcion_usa_pypdf(self, tmp_path):
        from src.utils.plano_vision_extractor import extract_cajetin_con_fallback
        from src.utils.plano_pdf_extractor import PlanoMetadata
        pdf = tmp_path / "test.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        # Vision falla con excepción
        with patch("anthropic.Anthropic", side_effect=Exception("rate limit")):
            with patch(
                "src.utils.plano_pdf_extractor.extraer_plano_metadata",
                return_value=PlanoMetadata(protocolo_tomo="FALLBACK"),
            ):
                cajetin = extract_cajetin_con_fallback(pdf, api_key="sk-ant-x")
        assert cajetin.protocolo_tomo == "FALLBACK"
