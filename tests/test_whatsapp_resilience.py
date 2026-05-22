"""Tests para los fixes P1 del audit del WhatsAppAgent:
  - Retry/backoff con tenacity sobre Green API
  - Rate limiter integrado
  - Idempotencia vía `whatsapp_processed` table
"""
from __future__ import annotations

import contextlib
import secrets
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

pytest.importorskip("win32cred", reason="pywin32 requerido (solo Windows)")

from src.agents.whatsapp_agent import WhatsAppAgent, _is_retryable_http
from src.core.database import Database


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


class FakeCm:
    def __init__(self):
        self._key = secrets.token_bytes(32)
    def get_or_create_db_key(self): return self._key
    def get_db_key(self): return self._key
    def get_operators(self): return set()
    def is_operator(self, phone): return False
    def get_green_api(self): return {"instance_id": "12345", "token": "tok"}


@pytest.fixture
def db(tmp_path):
    cm = FakeCm()
    d = TestDatabase(path=tmp_path / "test.db", credentials=cm)
    d.initialize_schema()
    return d


@pytest.fixture
def agent(db):
    return WhatsAppAgent(
        db, FakeCm(),
        base_url="https://fake-green.local",
        http_timeout=2.0,
    )


# ── Retry classifier ───────────────────────────────────────────────────

def test_retry_classifier_network_errors():
    assert _is_retryable_http(requests.ConnectionError("conn"))
    assert _is_retryable_http(requests.Timeout("timeout"))


def test_retry_classifier_5xx():
    resp = MagicMock(status_code=503)
    err = requests.HTTPError(response=resp)
    assert _is_retryable_http(err)


def test_retry_classifier_429():
    resp = MagicMock(status_code=429)
    err = requests.HTTPError(response=resp)
    assert _is_retryable_http(err)


def test_retry_classifier_4xx_not_retryable():
    resp = MagicMock(status_code=400)
    err = requests.HTTPError(response=resp)
    assert not _is_retryable_http(err)


def test_retry_classifier_other_exceptions_not_retryable():
    assert not _is_retryable_http(ValueError("foo"))
    assert not _is_retryable_http(RuntimeError("bar"))


# ── _post integra retry + rate limiter ─────────────────────────────────

def test_post_retries_on_5xx(agent):
    """Tras dos 503 transitorios, el tercer intento exitoso debe pasar."""
    bad_resp = MagicMock(status_code=503)
    bad_resp.raise_for_status.side_effect = requests.HTTPError(response=bad_resp)
    good_resp = MagicMock(status_code=200)
    good_resp.raise_for_status.return_value = None
    good_resp.json.return_value = {"idMessage": "ok-after-retries"}

    with patch("src.agents.whatsapp_agent.requests.post",
               side_effect=[bad_resp, bad_resp, good_resp]) as mock_post:
        result = agent._post("sendMessage", {"chatId": "x", "message": "y"})
        assert result == {"idMessage": "ok-after-retries"}
        assert mock_post.call_count == 3


def test_post_does_not_retry_4xx(agent):
    """400 (request mal formada) NO debe reintentarse."""
    bad_resp = MagicMock(status_code=400)
    bad_resp.raise_for_status.side_effect = requests.HTTPError(response=bad_resp)
    with patch("src.agents.whatsapp_agent.requests.post",
               side_effect=[bad_resp]) as mock_post:
        with pytest.raises(requests.HTTPError):
            agent._post("sendMessage", {})
        assert mock_post.call_count == 1


def test_post_gives_up_after_3_retries(agent):
    """Si el endpoint sigue caído, tras 3 intentos se rinde."""
    bad_resp = MagicMock(status_code=503)
    bad_resp.raise_for_status.side_effect = requests.HTTPError(response=bad_resp)
    with patch("src.agents.whatsapp_agent.requests.post",
               return_value=bad_resp) as mock_post:
        with pytest.raises(requests.HTTPError):
            agent._post("sendMessage", {})
        assert mock_post.call_count == 3


# ── Idempotencia ──────────────────────────────────────────────────────

def _notif(id_message: str, text: str = "si") -> dict:
    return {
        "receiptId": 123,
        "body": {
            "typeWebhook": "incomingMessageReceived",
            "idMessage": id_message,
            "senderData": {"chatId": "50688880001@c.us"},
            "messageData": {
                "typeMessage": "textMessage",
                "textMessageData": {"textMessage": text},
            },
        },
    }


def test_idempotencia_mensaje_no_se_procesa_dos_veces(agent, db):
    """Si llega el mismo idMessage 2 veces, solo se procesa una."""
    notif = _notif("MSG-DUP-001")
    # Primera vez: debe procesar (intentará buscar acción pendiente, no hay)
    result1 = agent._procesar_notificacion(notif)
    # Segunda vez: debe skipear por idempotencia
    result2 = agent._procesar_notificacion(notif)

    # Verificamos que el id quedó marcado en BD
    assert db.whatsapp_message_already_processed("MSG-DUP-001")
    # El segundo intento NO debe procesar (skip por idempotencia)
    # result2 es False porque el id ya estaba registrado
    assert result2 is False


def test_idempotencia_ids_distintos_se_procesan(agent, db):
    a = agent._procesar_notificacion(_notif("MSG-A"))
    b = agent._procesar_notificacion(_notif("MSG-B"))
    assert db.whatsapp_message_already_processed("MSG-A")
    assert db.whatsapp_message_already_processed("MSG-B")


def test_idempotencia_db_helpers(db):
    assert not db.whatsapp_message_already_processed("MSG-X")
    assert db.mark_whatsapp_message_processed("MSG-X", accion_id="A1") is True
    assert db.whatsapp_message_already_processed("MSG-X")
    # Segunda llamada: ya existe → False
    assert db.mark_whatsapp_message_processed("MSG-X", accion_id="A1") is False


def test_idempotencia_purga_viejos(db):
    """purgar_whatsapp_processed borra filas con fecha_proceso vieja."""
    db.mark_whatsapp_message_processed("VIEJO")
    # Forzar fecha vieja (usar _transaction para que persista en el TestDatabase)
    with db._transaction() as conn:
        conn.execute(
            "UPDATE whatsapp_processed SET fecha_proceso = ? WHERE id_message = ?",
            ("2020-01-01T00:00:00+00:00", "VIEJO"),
        )
    n = db.purgar_whatsapp_processed(dias=30)
    assert n == 1
    assert not db.whatsapp_message_already_processed("VIEJO")


def test_idempotencia_no_id_message(agent, db):
    """Si el body no trae idMessage ni receiptId, no marca nada — fail-open."""
    notif = {
        "body": {
            "typeWebhook": "incomingMessageReceived",
            "senderData": {"chatId": "50688880001@c.us"},
            "messageData": {
                "typeMessage": "textMessage",
                "textMessageData": {"textMessage": "si"},
            },
        },
    }
    # Sin idMessage ni receiptId — la idempotencia no aplica
    agent._procesar_notificacion(notif)
    # No marca nada porque msg_id="" (no se pudo determinar)
    # Pero debe haber intentado procesar (no crashear)


# ── Rate limiter integrado ─────────────────────────────────────────────

def test_post_calls_rate_limiter(agent):
    """Cada llamada a _post debe pasar por GREEN_API_LIMITER.acquire()."""
    with patch("src.agents.whatsapp_agent.GREEN_API_LIMITER") as mock_lim, \
         patch("src.agents.whatsapp_agent.requests.post") as mock_post:
        mock_post.return_value = MagicMock(
            status_code=200,
            raise_for_status=MagicMock(return_value=None),
            json=MagicMock(return_value={}),
        )
        agent._post("sendMessage", {})
        mock_lim.acquire.assert_called()


def test_get_calls_rate_limiter(agent):
    with patch("src.agents.whatsapp_agent.GREEN_API_LIMITER") as mock_lim, \
         patch("src.agents.whatsapp_agent.requests.get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200,
            raise_for_status=MagicMock(return_value=None),
            text='{"a":1}',
            json=MagicMock(return_value={"a": 1}),
        )
        agent._get("receiveNotification")
        mock_lim.acquire.assert_called()
