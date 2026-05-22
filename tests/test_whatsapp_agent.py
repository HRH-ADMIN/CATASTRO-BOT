"""Tests para WhatsAppAgent.

Cubre:
  - _clasificar(): SI/NO/None
  - _extraer_texto(): textMessage, extendedTextMessage, otros
  - _extraer_quoted_id(): quotedMessage vs otros
  - _encontrar_accion(): por teléfono, por quoted_id, sin match
  - solicitar_confirmacion(): crea acción en BD, envía WhatsApp
  - expirar_acciones_vencidas(): marca expiradas las que cumplieron TTL
  - procesar_respuestas(): drena notificaciones, resuelve SI/NO
  - enviar_archivo(): POST multipart, no toca el archivo original
"""
from __future__ import annotations

import contextlib
import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("win32cred", reason="pywin32 requerido (solo Windows)")

from src.agents.whatsapp_agent import WhatsAppAgent  # noqa: E402
from src.core.database import Database  # noqa: E402
from src.core.exceptions import AgentError  # noqa: E402
from src.models.estado import Estado  # noqa: E402
from src.models.plano import TipoPlano  # noqa: E402


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

    def get_green_api(self) -> dict:
        return {"instance_id": "12345", "token": "fake-token"}


@pytest.fixture
def db(tmp_path):
    cm = FakeCredentialManager()
    database = TestDatabase(path=tmp_path / "test.db", credentials=cm)
    database.initialize_schema()
    return database


@pytest.fixture
def agent(db):
    cm = FakeCredentialManager()
    return WhatsAppAgent(
        db, cm,
        base_url="https://fake-green-api.local",
        http_timeout=5.0,
    )


def _crear_exp(db, telefono: str = "50688880001") -> str:
    return db.crear_expediente(
        numero_expediente="EXP-TEST-0001",
        tipo_plano=TipoPlano.SEGREGACION.value,
        nombre_topografo="Topo Test",
        telefono_cliente=telefono,
        nombre_cliente="Cliente Test",
        cedula_topografo="1-0001-0001",
        metadata={},
        actor="test",
    )


def _crear_accion(db, eid: str, *, tipo: str = "pago_cliente",
                  expira_en: str | None = None,
                  msg_id: str | None = None) -> str:
    return db.crear_accion_pendiente(
        expediente_id=eid,
        tipo_accion=tipo,
        descripcion=f"Confirmar {tipo}",
        whatsapp_message_id=msg_id,
        expira_en=expira_en,
        actor="test",
    )


def _notif(sender: str, text: str, quoted_id: str | None = None) -> dict:
    """Construye una notificación Green API mínima para tests."""
    if quoted_id:
        md = {
            "typeMessage": "quotedMessage",
            "extendedTextMessageData": {"text": text},
            "quotedMessage": {"stanzaId": quoted_id},
        }
    else:
        md = {
            "typeMessage": "textMessage",
            "textMessageData": {"textMessage": text},
        }
    return {
        "receiptId": 999,
        "body": {
            "typeWebhook": "incomingMessageReceived",
            "senderData": {"chatId": f"{sender}@c.us"},
            "messageData": md,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# _clasificar
# ─────────────────────────────────────────────────────────────────────────────

class TestClasificar:

    def test_si_devuelve_true(self, agent):
        assert agent._clasificar("SI") is True

    def test_si_con_tilde_devuelve_true(self, agent):
        assert agent._clasificar("Sí") is True

    def test_yes_devuelve_true(self, agent):
        assert agent._clasificar("yes") is True

    def test_ok_devuelve_true(self, agent):
        assert agent._clasificar("ok") is True

    def test_no_devuelve_false(self, agent):
        assert agent._clasificar("NO") is False

    def test_cancelar_devuelve_false(self, agent):
        assert agent._clasificar("cancelar") is False

    def test_mensaje_libre_devuelve_none(self, agent):
        assert agent._clasificar("¿Cuándo llega?") is None

    def test_si_con_texto_extra_devuelve_true(self, agent):
        assert agent._clasificar("SI confirmo el pago") is True

    def test_no_con_texto_extra_devuelve_false(self, agent):
        assert agent._clasificar("No puedo ahora") is False

    def test_vacio_devuelve_none(self, agent):
        assert agent._clasificar("") is None


# ─────────────────────────────────────────────────────────────────────────────
# _extraer_texto
# ─────────────────────────────────────────────────────────────────────────────

class TestExtraerTexto:

    def test_text_message(self, agent):
        md = {"typeMessage": "textMessage",
              "textMessageData": {"textMessage": "Hola"}}
        assert agent._extraer_texto(md) == "Hola"

    def test_extended_text_message(self, agent):
        md = {"typeMessage": "extendedTextMessage",
              "extendedTextMessageData": {"text": "SI confirmo"}}
        assert agent._extraer_texto(md) == "SI confirmo"

    def test_quoted_message(self, agent):
        md = {"typeMessage": "quotedMessage",
              "extendedTextMessageData": {"text": "NO"}}
        assert agent._extraer_texto(md) == "NO"

    def test_tipo_desconocido_devuelve_vacio(self, agent):
        md = {"typeMessage": "imageMessage"}
        assert agent._extraer_texto(md) == ""

    def test_sin_tipo_devuelve_vacio(self, agent):
        assert agent._extraer_texto({}) == ""


# ─────────────────────────────────────────────────────────────────────────────
# _extraer_quoted_id
# ─────────────────────────────────────────────────────────────────────────────

class TestExtraerQuotedId:

    def test_quoted_message_devuelve_stanza_id(self, agent):
        md = {"typeMessage": "quotedMessage",
              "quotedMessage": {"stanzaId": "ABC123"}}
        assert agent._extraer_quoted_id(md) == "ABC123"

    def test_quoted_message_fallback_id_message(self, agent):
        md = {"typeMessage": "quotedMessage",
              "quotedMessage": {"idMessage": "MSG456"}}
        assert agent._extraer_quoted_id(md) == "MSG456"

    def test_text_message_devuelve_none(self, agent):
        md = {"typeMessage": "textMessage"}
        assert agent._extraer_quoted_id(md) is None

    def test_sin_tipo_devuelve_none(self, agent):
        assert agent._extraer_quoted_id({}) is None


# ─────────────────────────────────────────────────────────────────────────────
# solicitar_confirmacion
# ─────────────────────────────────────────────────────────────────────────────

class TestSolicitarConfirmacion:

    def test_crea_accion_en_bd(self, db, agent):
        eid = _crear_exp(db)
        with patch.object(agent, "enviar_mensaje", return_value="MSG001"):
            agent.solicitar_confirmacion(
                expediente_id=eid,
                tipo_accion="pago_cliente",
                descripcion="¿Confirma el pago?",
            )

        acciones = db.acciones_pendientes(expediente_id=eid)
        assert len(acciones) == 1
        assert acciones[0]["tipo_accion"] == "pago_cliente"
        assert acciones[0]["estado"] == "pendiente"

    def test_guarda_whatsapp_message_id(self, db, agent):
        eid = _crear_exp(db)
        with patch.object(agent, "enviar_mensaje", return_value="MSG-XYZ"):
            agent.solicitar_confirmacion(
                expediente_id=eid,
                tipo_accion="pago_cliente",
                descripcion="¿Confirma?",
            )

        accion = db.acciones_pendientes(expediente_id=eid)[0]
        assert accion["whatsapp_message_id"] == "MSG-XYZ"

    def test_envia_mensaje_al_telefono_correcto(self, db, agent):
        eid = _crear_exp(db, telefono="50699990001")
        with patch.object(agent, "enviar_mensaje", return_value="X") as mock_send:
            agent.solicitar_confirmacion(
                expediente_id=eid,
                tipo_accion="pago_cliente",
                descripcion="¿Confirma?",
            )

        mock_send.assert_called_once()
        assert mock_send.call_args[0][0] == "50699990001"

    def test_mensaje_contiene_si_no(self, db, agent):
        eid = _crear_exp(db)
        mensajes_enviados = []
        with patch.object(agent, "enviar_mensaje",
                          side_effect=lambda t, m: mensajes_enviados.append(m) or "X"):
            agent.solicitar_confirmacion(
                expediente_id=eid,
                tipo_accion="pago_cliente",
                descripcion="Descripción de prueba",
            )

        assert mensajes_enviados
        assert "SI" in mensajes_enviados[0]
        assert "NO" in mensajes_enviados[0]

    def test_expediente_inexistente_lanza(self, db, agent):
        with pytest.raises(ValueError):
            agent.solicitar_confirmacion(
                expediente_id="uuid-inexistente",
                tipo_accion="pago_cliente",
                descripcion="¿Confirma?",
            )


# ─────────────────────────────────────────────────────────────────────────────
# expirar_acciones_vencidas
# ─────────────────────────────────────────────────────────────────────────────

class TestExpirarAccionesVencidas:

    def _pasado(self, horas: int = 2) -> str:
        return (datetime.now(timezone.utc) - timedelta(hours=horas)).isoformat()

    def _futuro(self, horas: int = 24) -> str:
        return (datetime.now(timezone.utc) + timedelta(hours=horas)).isoformat()

    def test_accion_vencida_se_marca_expirada(self, db, agent):
        eid = _crear_exp(db)
        _crear_accion(db, eid, expira_en=self._pasado())

        expiradas = agent.expirar_acciones_vencidas()

        assert expiradas == 1
        acciones = db.acciones_pendientes(expediente_id=eid)
        assert len(acciones) == 0  # ya no está pendiente

    def test_accion_vigente_no_se_expira(self, db, agent):
        eid = _crear_exp(db)
        _crear_accion(db, eid, expira_en=self._futuro())

        expiradas = agent.expirar_acciones_vencidas()

        assert expiradas == 0
        assert len(db.acciones_pendientes(expediente_id=eid)) == 1

    def test_accion_sin_expira_en_no_se_expira(self, db, agent):
        eid = _crear_exp(db)
        _crear_accion(db, eid)  # sin expira_en

        expiradas = agent.expirar_acciones_vencidas()

        assert expiradas == 0

    def test_multiples_vencidas(self, db, agent):
        eid = _crear_exp(db)
        _crear_accion(db, eid, tipo="acc1", expira_en=self._pasado())
        _crear_accion(db, eid, tipo="acc2", expira_en=self._pasado())

        expiradas = agent.expirar_acciones_vencidas()

        assert expiradas == 2


# ─────────────────────────────────────────────────────────────────────────────
# procesar_respuestas
# ─────────────────────────────────────────────────────────────────────────────

class TestProcesarRespuestas:

    def test_inbox_vacio_devuelve_cero(self, db, agent):
        with patch.object(agent, "_get", return_value=None):
            result = agent.procesar_respuestas()
        assert result == 0

    def test_si_resuelve_accion_como_confirmada(self, db, agent):
        eid = _crear_exp(db)
        _crear_accion(db, eid, msg_id="MSG001")
        notif = _notif("50688880001", "SI")

        respuestas = iter([notif, None])
        with patch.object(agent, "_get", side_effect=lambda m: next(respuestas)):
            with patch.object(agent, "_delete"):
                agent.procesar_respuestas()

        accion = db.ultima_accion(expediente_id=eid, tipo_accion="pago_cliente")
        assert accion["estado"] == "confirmada"

    def test_no_resuelve_accion_como_rechazada(self, db, agent):
        eid = _crear_exp(db)
        _crear_accion(db, eid)
        notif = _notif("50688880001", "NO")

        respuestas = iter([notif, None])
        with patch.object(agent, "_get", side_effect=lambda m: next(respuestas)):
            with patch.object(agent, "_delete"):
                agent.procesar_respuestas()

        accion = db.ultima_accion(expediente_id=eid, tipo_accion="pago_cliente")
        assert accion["estado"] == "rechazada"

    def test_respuesta_por_quoted_id_match_exacto(self, db, agent):
        eid = _crear_exp(db)
        _crear_accion(db, eid, tipo="acc_vieja", msg_id="MSG_OLD")
        _crear_accion(db, eid, tipo="acc_nueva", msg_id="MSG_NEW")
        # El cliente cita el mensaje viejo
        notif = _notif("50688880001", "SI", quoted_id="MSG_OLD")

        respuestas = iter([notif, None])
        with patch.object(agent, "_get", side_effect=lambda m: next(respuestas)):
            with patch.object(agent, "_delete"):
                agent.procesar_respuestas()

        # Solo la acción vieja debe resolverse
        vieja = db.ultima_accion(expediente_id=eid, tipo_accion="acc_vieja")
        nueva = db.ultima_accion(expediente_id=eid, tipo_accion="acc_nueva")
        assert vieja["estado"] == "confirmada"
        assert nueva["estado"] == "pendiente"

    def test_mensaje_libre_no_resuelve_accion(self, db, agent):
        eid = _crear_exp(db)
        _crear_accion(db, eid)
        notif = _notif("50688880001", "¿Cuándo termina el trámite?")

        respuestas = iter([notif, None])
        with patch.object(agent, "_get", side_effect=lambda m: next(respuestas)):
            with patch.object(agent, "_delete"):
                agent.procesar_respuestas()

        accion = db.ultima_accion(expediente_id=eid, tipo_accion="pago_cliente")
        assert accion["estado"] == "pendiente"

    def test_notificacion_no_mensaje_ignorada(self, db, agent):
        notif_status = {
            "receiptId": 42,
            "body": {"typeWebhook": "outgoingMessageDelivered"},
        }
        respuestas = iter([notif_status, None])
        with patch.object(agent, "_get", side_effect=lambda m: next(respuestas)):
            with patch.object(agent, "_delete"):
                result = agent.procesar_respuestas()
        assert result == 0

    def test_siempre_hace_delete_del_receipt(self, db, agent):
        notif = _notif("50688880001", "SI")
        deletes = []
        respuestas = iter([notif, None])
        with patch.object(agent, "_get", side_effect=lambda m: next(respuestas)):
            with patch.object(agent, "_delete",
                              side_effect=lambda *a: deletes.append(a)):
                agent.procesar_respuestas()

        assert len(deletes) == 1
        assert "999" in str(deletes[0])  # receiptId


# ─────────────────────────────────────────────────────────────────────────────
# enviar_archivo
# ─────────────────────────────────────────────────────────────────────────────

class TestEnviarArchivo:

    def test_archivo_no_existe_lanza_agent_error(self, db, agent, tmp_path):
        with pytest.raises(AgentError, match="no encontrado"):
            agent.enviar_archivo("50688880001", tmp_path / "no_existe.pdf")

    def test_envia_multipart_post(self, db, agent, tmp_path):
        archivo = tmp_path / "plano.pdf"
        archivo.write_bytes(b"PDF content here")

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"idMessage": "FILE_MSG_001"}
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.post", return_value=mock_resp) as mock_post:
            result = agent.enviar_archivo("50688880001", archivo)

        mock_post.assert_called_once()
        assert result == "FILE_MSG_001"

    def test_url_contiene_sendfilebyupload(self, db, agent, tmp_path):
        archivo = tmp_path / "doc.pdf"
        archivo.write_bytes(b"data")

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"idMessage": "X"}
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.post", return_value=mock_resp) as mock_post:
            agent.enviar_archivo("50688880001", archivo)

        url_llamada = mock_post.call_args[0][0]
        assert "sendFileByUpload" in url_llamada

    def test_archivo_original_no_se_modifica(self, db, agent, tmp_path):
        """El archivo debe tener el mismo contenido antes y después del envío."""
        archivo = tmp_path / "inscrito.pdf"
        contenido_original = b"datos sensibles del plano inscrito"
        archivo.write_bytes(contenido_original)

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"idMessage": "X"}
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.post", return_value=mock_resp):
            agent.enviar_archivo("50688880001", archivo)

        # El archivo sigue existiendo con el mismo contenido
        assert archivo.exists()
        assert archivo.read_bytes() == contenido_original

    def test_caption_se_incluye_en_post(self, db, agent, tmp_path):
        archivo = tmp_path / "archivo.pdf"
        archivo.write_bytes(b"x")

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"idMessage": "X"}
        mock_resp.raise_for_status = MagicMock()

        with patch("requests.post", return_value=mock_resp) as mock_post:
            agent.enviar_archivo("50688880001", archivo, caption="Plano inscrito")

        data_enviada = mock_post.call_args.kwargs.get("data", {})
        assert data_enviada.get("caption") == "Plano inscrito"
