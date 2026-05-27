"""Tests del fallback HTTP 466 → email (Sprint 4 / N-03).

Mockea Green API requests + smtplib para verificar:
  - HTTP 466 desde Green API marca el servicio como down + audit log.
  - enviar_mensaje() detecta el flag down y va al fallback de email.
  - Fallback email usa credenciales muni-san-ramon.
  - Si Green API responde normal, NO toca el flag.

Plan: PLAN_MEJORAS Sprint 4 / N-03.
"""
from __future__ import annotations
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.core.credential_manager import CredentialManager
from src.core.database import Database
from src.agents.whatsapp_agent import WhatsAppAgent
from src.utils import external_services as es


@pytest.fixture(autouse=True)
def _reset_bus():
    from src.utils.event_bus import reset_bus_for_testing
    reset_bus_for_testing()
    yield
    reset_bus_for_testing()


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CATASTRO_BOT_DEV_MODE", "1")
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("config.settings.DATABASE_PATH", db_path)
    d = Database(path=db_path, credentials=CredentialManager())
    d.initialize_schema()
    return d


@pytest.fixture
def agent(db):
    """WhatsAppAgent con credenciales y rate limiter mockeados."""
    creds = MagicMock()
    creds.get_green_api.return_value = {
        "instance_id": "7107606637",
        "token": "fake-token-123",
    }
    creds.get_muni_san_ramon.return_value = (
        "topografiahrh@gmail.com",
        "fake-app-password",
    )
    with patch("src.agents.whatsapp_agent.GREEN_API_LIMITER",
               MagicMock(acquire=lambda: None)):
        a = WhatsAppAgent(db, creds)
        yield a


# ─── Detección de HTTP 466 ────────────────────────────────────────────

class TestDeteccion466:
    def test_post_466_marca_servicio_down(self, agent, db, monkeypatch):
        """Cuando _post recibe 466, marca external_services_health
        green_api como 'down' antes de raise."""
        # Mock de requests.post para devolver HTTP 466
        fake_resp = MagicMock()
        fake_resp.status_code = 466
        fake_resp.text = "Instance not authorized"

        with patch("src.agents.whatsapp_agent.requests.post",
                   return_value=fake_resp):
            from src.core.exceptions import AgentError
            with pytest.raises(AgentError) as exc_info:
                agent._post("sendMessage", {"chatId": "x", "message": "y"})
            assert "466" in str(exc_info.value)

        # Verificar persistencia
        st = es.read_status(db.path, "green_api")
        assert st["status"] == "down"
        assert st["last_error_code"] == "466"

    def test_post_200_NO_marca_down(self, agent, db):
        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {"idMessage": "xyz-123"}
        fake_resp.raise_for_status = lambda: None

        with patch("src.agents.whatsapp_agent.requests.post",
                   return_value=fake_resp):
            result = agent._post("sendMessage", {"chatId": "x", "message": "y"})
        assert result["idMessage"] == "xyz-123"
        assert es.read_status(db.path, "green_api")["status"] == "up"


# ─── Fallback por email ──────────────────────────────────────────────

class TestFallbackEmail:
    def test_enviar_mensaje_con_servicio_down_usa_email(self, agent, db):
        """Si green_api está marcado como down, enviar_mensaje delega
        al fallback de email."""
        es.mark_down(db.path, "green_api", error_code="466",
                     error_message="prev fail")

        with patch(
            "src.utils.whatsapp_email_fallback.send_via_email_fallback",
            return_value=True,
        ) as mock_send:
            result = agent.enviar_mensaje("+50688887310", "Hola",
                                          contexto="test")

        # Result debe ser ID sintético tipo 'email:xxx'
        assert result.startswith("email:")
        mock_send.assert_called_once()
        # Verificar argumentos
        call_kwargs = mock_send.call_args.kwargs
        assert call_kwargs["telefono"] == "+50688887310"
        assert call_kwargs["mensaje"] == "Hola"
        assert call_kwargs["contexto"] == "test"

    def test_email_fallido_devuelve_email_failed(self, agent, db):
        es.mark_down(db.path, "green_api", error_code="466")
        with patch(
            "src.utils.whatsapp_email_fallback.send_via_email_fallback",
            return_value=False,
        ):
            result = agent.enviar_mensaje("+50688887310", "Hola")
        assert result.startswith("email-failed:")

    def test_466_durante_envio_dispara_fallback(self, agent, db):
        """Si Green API devuelve 466 EN MEDIO del envío, el agente debe:
        1. Detectar y marcar down (via _post).
        2. Reintentar via fallback email.
        """
        fake_resp = MagicMock()
        fake_resp.status_code = 466
        fake_resp.text = "Quota exceeded"

        with patch("src.agents.whatsapp_agent.requests.post",
                   return_value=fake_resp):
            with patch(
                "src.utils.whatsapp_email_fallback.send_via_email_fallback",
                return_value=True,
            ) as mock_send:
                result = agent.enviar_mensaje("+50688887310", "Hola",
                                              contexto="quota_test")

        assert result.startswith("email:")
        mock_send.assert_called_once()
        # Servicio quedó marcado down
        assert es.is_down(db.path, "green_api")


# ─── Recovery automático ──────────────────────────────────────────────

class TestRecovery:
    def test_mark_up_restaura_uso_de_whatsapp(self, agent, db):
        es.mark_down(db.path, "green_api", error_code="466")
        assert agent._is_greenapi_down() is True

        es.mark_up(db.path, "green_api", reason="manual restore")
        assert agent._is_greenapi_down() is False

    def test_enviar_mensaje_tras_recovery_usa_whatsapp(self, agent, db):
        """Después de recovery, el siguiente envío vuelve a usar Green API."""
        # Estaba down
        es.mark_down(db.path, "green_api", error_code="466")
        # Operador re-autorizó y job recovery lo restauró
        es.mark_up(db.path, "green_api", reason="auto-recovery OK")

        # Ahora envío normal — debe ir a HTTP, no a email
        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {"idMessage": "real-id-789"}
        fake_resp.raise_for_status = lambda: None
        with patch("src.agents.whatsapp_agent.requests.post",
                   return_value=fake_resp):
            with patch(
                "src.utils.whatsapp_email_fallback.send_via_email_fallback"
            ) as mock_send:
                result = agent.enviar_mensaje("+50688887310", "Hola")

        assert result == "real-id-789"
        mock_send.assert_not_called()
