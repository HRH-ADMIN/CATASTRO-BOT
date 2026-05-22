"""Tests para MinutaAgent y las utilidades de sanitización.

Cubre:
  - sanitize(): cédulas, fincas, planos, teléfonos
  - assert_safe_for_ai(): lanza si quedan datos sensibles
  - MinutaAgent.analizar(): texto vacío, sanitización previa al API,
    delegación de la respuesta estructurada al caller.

El cliente Anthropic se inyecta directamente en `agent._client`
(sin llamadas reales a la API).
"""
from __future__ import annotations

import contextlib
import secrets
import sqlite3
from unittest.mock import MagicMock

import pytest

pytest.importorskip("win32cred", reason="pywin32 requerido (solo Windows)")

from src.agents.minuta_agent import MinutaAgent, MinutaAnalisis  # noqa: E402
from src.core.database import Database  # noqa: E402
from src.core.exceptions import AgentError  # noqa: E402
from src.utils.sanitizer import assert_safe_for_ai, sanitize  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Infraestructura
# ─────────────────────────────────────────────────────────────────────────────

class TestDatabase(Database):
    @contextlib.contextmanager
    def connect(self):
        conn = sqlite3.connect(str(self.path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
        finally:
            conn.close()


class FakeCredentialManager:
    def __init__(self):
        self._db_key = secrets.token_bytes(32)

    def get_or_create_db_key(self) -> bytes:
        return self._db_key

    def get_db_key(self) -> bytes:
        return self._db_key

    def get_operators(self) -> set[str]:
        return set()

    def is_operator(self, phone: str) -> bool:
        return False

    def get_anthropic_key(self) -> str:
        return "sk-ant-fake-key-for-testing"


@pytest.fixture
def db(tmp_path):
    cm = FakeCredentialManager()
    database = TestDatabase(path=tmp_path / "test.db", credentials=cm)
    database.initialize_schema()
    return database


@pytest.fixture
def agent(db):
    cm = FakeCredentialManager()
    return MinutaAgent(db, cm)


def _mock_response(analisis: MinutaAnalisis) -> MagicMock:
    """Crea un mock de respuesta Anthropic con parsed_output y usage."""
    resp = MagicMock()
    resp.parsed_output = analisis
    resp.usage.input_tokens = 1500
    resp.usage.output_tokens = 250
    resp.usage.cache_read_input_tokens = 800
    resp.usage.cache_creation_input_tokens = 0
    return resp


def _analisis_aprobado() -> MinutaAnalisis:
    return MinutaAnalisis(
        aprobado=True,
        traslapes=False,
        tipo_error=None,
        descripcion_tecnica="Plano aprobado sin observaciones técnicas relevantes.",
        correcciones_solicitadas=[],
        requiere_apelacion=False,
        notas_aprobacion=["Verificar carta de agua antes de inscripción"],
    )


def _analisis_correcciones() -> MinutaAnalisis:
    return MinutaAnalisis(
        aprobado=False,
        traslapes=False,
        tipo_error="georreferenciacion",
        descripcion_tecnica="Los vértices no coinciden con el sistema CRTM05.",
        correcciones_solicitadas=[
            "Referenciar vértices al sistema geodésico CRTM05",
            "Corregir rumbo del lindero norte",
        ],
        requiere_apelacion=False,
        notas_aprobacion=[],
    )


def _analisis_traslapes() -> MinutaAnalisis:
    return MinutaAnalisis(
        aprobado=False,
        traslapes=True,
        tipo_error="otro",
        descripcion_tecnica="Se detectaron traslapes con [FINCA] inscrita previamente.",
        correcciones_solicitadas=["Resolver traslape con parcela adyacente"],
        requiere_apelacion=True,
        notas_aprobacion=[],
    )


# ─────────────────────────────────────────────────────────────────────────────
# sanitize()
# ─────────────────────────────────────────────────────────────────────────────

class TestSanitize:

    def test_reemplaza_cedula_fisica(self):
        resultado = sanitize("El propietario con cédula 1-0234-5678 solicita.")
        assert "1-0234-5678" not in resultado
        assert "[CED-FIS]" in resultado

    def test_reemplaza_cedula_juridica(self):
        resultado = sanitize("Empresa con cédula jurídica 3-101-123456.")
        assert "3-101-123456" not in resultado
        assert "[CED-JUR]" in resultado

    def test_reemplaza_numero_finca(self):
        resultado = sanitize("La finca 123456 está ubicada en el cantón.")
        assert "123456" not in resultado or "[FINCA]" in resultado

    def test_reemplaza_plano_catastral(self):
        resultado = sanitize("Ver plano A-1234567-2024 para detalles.")
        assert "A-1234567-2024" not in resultado
        assert "[PLANO]" in resultado

    def test_reemplaza_telefono_cr(self):
        resultado = sanitize("Llamar al 8888-7777 para consultas.")
        assert "8888-7777" not in resultado
        assert "[TEL]" in resultado

    def test_texto_sin_datos_sensibles_intacto(self):
        texto = "El plano fue rechazado por error de georreferenciación en el vértice norte."
        assert sanitize(texto) == texto

    def test_cedula_fisica_sin_guiones(self):
        resultado = sanitize("Cédula 102345678 del solicitante.")
        assert "102345678" not in resultado

    def test_multiples_patrones_en_mismo_texto(self):
        texto = "Propietario 1-0234-5678, finca 999 en plano A-123456-2023."
        resultado = sanitize(texto)
        assert "1-0234-5678" not in resultado
        assert "A-123456-2023" not in resultado


# ─────────────────────────────────────────────────────────────────────────────
# assert_safe_for_ai()
# ─────────────────────────────────────────────────────────────────────────────

class TestAssertSafeForAi:

    def test_texto_limpio_no_lanza(self):
        assert_safe_for_ai("Error de georreferenciación en vértice norte.")

    def test_cedula_fisica_lanza(self):
        with pytest.raises(ValueError, match="cédula física"):
            assert_safe_for_ai("El dueño 1-0234-5678 solicita corrección.")

    def test_cedula_juridica_lanza(self):
        with pytest.raises(ValueError, match="cédula jurídica"):
            assert_safe_for_ai("Empresa 3-101-123456 presentó el plano.")

    def test_plano_catastral_lanza(self):
        with pytest.raises(ValueError, match="número de plano"):
            assert_safe_for_ai("Ver plano A-1234567-2024.")

    def test_telefono_lanza(self):
        with pytest.raises(ValueError, match="teléfono"):
            assert_safe_for_ai("Contactar al 8888-7777.")

    def test_texto_con_placeholders_no_lanza(self):
        """Texto ya sanitizado con placeholders debe pasar la verificación."""
        assert_safe_for_ai(
            "El [CED-FIS] solicita corrección del plano [PLANO] "
            "en [FINCA]. Teléfono: [TEL]."
        )


# ─────────────────────────────────────────────────────────────────────────────
# MinutaAgent.analizar()
# ─────────────────────────────────────────────────────────────────────────────

class TestMinutaAgentAnalizar:

    def test_texto_vacio_lanza_agent_error(self, agent):
        with pytest.raises(AgentError, match="vacío"):
            agent.analizar("")

    def test_texto_solo_espacios_lanza_agent_error(self, agent):
        with pytest.raises(AgentError, match="vacío"):
            agent.analizar("   \n\t  ")

    def test_retorna_minuta_analisis(self, agent):
        mock_client = MagicMock()
        agent._client = mock_client
        mock_client.messages.parse.return_value = _mock_response(_analisis_aprobado())

        resultado = agent.analizar("El plano fue revisado y aprobado sin observaciones.")

        assert isinstance(resultado, MinutaAnalisis)

    def test_aprobado_true_cuando_api_dice_aprobado(self, agent):
        mock_client = MagicMock()
        agent._client = mock_client
        mock_client.messages.parse.return_value = _mock_response(_analisis_aprobado())

        resultado = agent.analizar("Plano aprobado por Catastro Nacional.")

        assert resultado.aprobado is True
        assert resultado.traslapes is False
        assert resultado.tipo_error is None
        assert resultado.correcciones_solicitadas == []

    def test_correcciones_cuando_api_rechaza(self, agent):
        mock_client = MagicMock()
        agent._client = mock_client
        mock_client.messages.parse.return_value = _mock_response(_analisis_correcciones())

        resultado = agent.analizar(
            "El plano presenta error de georreferenciación en los vértices."
        )

        assert resultado.aprobado is False
        assert resultado.tipo_error == "georreferenciacion"
        assert len(resultado.correcciones_solicitadas) == 2
        assert resultado.requiere_apelacion is False

    def test_traslapes_cuando_api_detecta_traslape(self, agent):
        mock_client = MagicMock()
        agent._client = mock_client
        mock_client.messages.parse.return_value = _mock_response(_analisis_traslapes())

        resultado = agent.analizar(
            "Se detectaron traslapes con planos inscritos adyacentes."
        )

        assert resultado.traslapes is True
        assert resultado.requiere_apelacion is True

    def test_texto_con_cedula_se_sanitiza_antes_del_api(self, agent):
        """El texto enviado al API nunca debe contener la cédula original."""
        mock_client = MagicMock()
        agent._client = mock_client
        mock_client.messages.parse.return_value = _mock_response(_analisis_aprobado())

        texto_con_cedula = "Propietario 1-0234-5678 presentó plano catastral."
        agent.analizar(texto_con_cedula)

        # Extraer el texto que se pasó al API
        call_kwargs = mock_client.messages.parse.call_args
        mensajes = call_kwargs.kwargs.get("messages") or call_kwargs.args[0] if call_kwargs.args else []
        # El texto en el mensaje de usuario no debe contener la cédula original
        texto_enviado = str(call_kwargs)
        assert "1-0234-5678" not in texto_enviado
        assert "[CED-FIS]" in texto_enviado

    def test_llama_parse_con_model_correcto(self, agent):
        mock_client = MagicMock()
        agent._client = mock_client
        mock_client.messages.parse.return_value = _mock_response(_analisis_aprobado())

        agent.analizar("Texto técnico del plano catastral sin datos personales.")

        call_kwargs = mock_client.messages.parse.call_args.kwargs
        assert call_kwargs.get("model") == "claude-opus-4-7"

    def test_llama_parse_con_output_format_minuta_analisis(self, agent):
        mock_client = MagicMock()
        agent._client = mock_client
        mock_client.messages.parse.return_value = _mock_response(_analisis_aprobado())

        agent.analizar("Texto técnico sin datos personales.")

        call_kwargs = mock_client.messages.parse.call_args.kwargs
        assert call_kwargs.get("output_format") is MinutaAnalisis

    def test_llama_parse_con_thinking_adaptivo(self, agent):
        mock_client = MagicMock()
        agent._client = mock_client
        mock_client.messages.parse.return_value = _mock_response(_analisis_aprobado())

        agent.analizar("Texto técnico sin datos personales.")

        call_kwargs = mock_client.messages.parse.call_args.kwargs
        thinking = call_kwargs.get("thinking")
        assert thinking is not None
        assert thinking.get("type") == "adaptive"

    def test_system_prompt_tiene_cache_control(self, agent):
        mock_client = MagicMock()
        agent._client = mock_client
        mock_client.messages.parse.return_value = _mock_response(_analisis_aprobado())

        agent.analizar("Texto técnico del plano.")

        call_kwargs = mock_client.messages.parse.call_args.kwargs
        system = call_kwargs.get("system", [])
        assert len(system) == 1
        assert system[0].get("cache_control") == {"type": "ephemeral"}

    def test_notas_aprobacion_en_respuesta(self, agent):
        mock_client = MagicMock()
        agent._client = mock_client
        mock_client.messages.parse.return_value = _mock_response(_analisis_aprobado())

        resultado = agent.analizar("Plano aprobado con nota de carta de agua.")

        assert len(resultado.notas_aprobacion) == 1
        assert "carta de agua" in resultado.notas_aprobacion[0].lower()
