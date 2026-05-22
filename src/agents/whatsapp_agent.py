"""Agente Green API para WhatsApp.

Flujo de confirmación:
  1. `solicitar_confirmacion()` crea una acción pendiente en BD y envía
     un mensaje al cliente pidiendo respuesta SI/NO. Devuelve el `accion_id`.
  2. `procesar_respuestas()` (llamado periódicamente por el orchestrator)
     hace polling de Green API, clasifica la respuesta y resuelve la
     acción correspondiente en BD como `confirmada` o `rechazada`.
  3. `expirar_acciones_vencidas()` mueve a `expirada` cualquier acción
     pendiente cuyo `expira_en` ya pasó.

Matching de respuesta a acción:
  - Si el cliente responde citando (quote/reply) el mensaje del bot,
    se usa el `idMessage` citado para match exacto.
  - Si no, se elige la acción pendiente más reciente del cliente.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import requests
from tenacity import (
    RetryError,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from config.settings import WHATSAPP_CONFIRMATION_TIMEOUT
from src.agents.base_agent import BaseAgent
from src.utils.logger import get_logger
from src.utils.rate_limiter import GREEN_API_LIMITER
from src.utils.validators import normalizar_telefono_cr

_GREEN_BASE = "https://api.green-api.com"


# ── Retry policy para Green API ────────────────────────────────────────
# Reintentar: errores de red, timeouts, y respuestas HTTP transitorias
# (429 = too many requests, 5xx = server error). NO reintentar 4xx no-429
# porque significan request mal formada (bug del bot, no transitorio).

def _is_retryable_http(exc: BaseException) -> bool:
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return True
    if isinstance(exc, requests.HTTPError):
        resp = getattr(exc, "response", None)
        status = getattr(resp, "status_code", None)
        return status == 429 or (status is not None and 500 <= status < 600)
    return False


_GREEN_RETRY = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception(_is_retryable_http),
    reraise=True,
)


def _instance_base_url(instance_id: str) -> str:
    """Deriva la URL base específica de la instancia.

    Green API asigna cada instancia a un servidor numerado.
    El prefijo del servidor coincide con los primeros 4 dígitos del instance_id.
    Ej: instancia 7107606637 → https://7107.api.greenapi.com
    """
    prefix = str(instance_id)[:4]
    return f"https://{prefix}.api.greenapi.com"

_AFFIRM = {"si", "sí", "yes", "y", "ok", "okay", "vale",
           "confirmo", "confirmar", "acepto", "1"}
_NEGATE = {"no", "n", "cancelar", "cancel", "rechazo", "rechazar", "0"}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _phone_digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


class WhatsAppAgent(BaseAgent):
    name = "whatsapp"

    def __init__(self, db, credentials, *, base_url: str = _GREEN_BASE,
                 http_timeout: float = 30.0, command_router=None):
        super().__init__(db, credentials)
        self._base = base_url.rstrip("/")
        self._timeout = http_timeout
        self._cache: Optional[tuple[str, str]] = None
        self._log = get_logger("whatsapp_agent")
        self._command_router = command_router

    def set_command_router(self, command_router) -> None:
        """Permite registrar el router después de construir el agente
        (resuelve la dependencia circular: el router necesita el reply_fn
        del agente para enviar respuestas)."""
        self._command_router = command_router

    # ---------- Green API low-level ----------

    def _api(self) -> tuple[str, str]:
        if self._cache is None:
            data = self.credentials.get_green_api()
            instance_id = str(data["instance_id"])
            token = str(data["token"])
            self._cache = (instance_id, token)
            # Actualizar base URL al servidor específico de la instancia
            # solo si se está usando el fallback genérico
            if self._base == _GREEN_BASE:
                self._base = _instance_base_url(instance_id)
                self._log.info("Green API base URL: %s", self._base)
        return self._cache

    def _url(self, method: str, *suffix: str) -> str:
        instance, token = self._api()
        path = f"/waInstance{instance}/{method}/{token}"
        for s in suffix:
            path += f"/{s}"
        return f"{self._base}{path}"

    @_GREEN_RETRY
    def _post(self, method: str, body: dict) -> dict:
        GREEN_API_LIMITER.acquire()
        r = requests.post(self._url(method), json=body, timeout=self._timeout)
        r.raise_for_status()
        return r.json()

    @_GREEN_RETRY
    def _get(self, method: str) -> Any:
        GREEN_API_LIMITER.acquire()
        r = requests.get(self._url(method), timeout=self._timeout)
        r.raise_for_status()
        if not r.text or r.text.strip() == "null":
            return None
        return r.json()

    @_GREEN_RETRY
    def _delete(self, method: str, *suffix: str) -> None:
        GREEN_API_LIMITER.acquire()
        r = requests.delete(self._url(method, *suffix), timeout=self._timeout)
        r.raise_for_status()

    # ---------- chat ID ----------

    def _chat_id(self, telefono: str) -> str:
        normalized = normalizar_telefono_cr(telefono)
        return f"{normalized.lstrip('+')}@c.us"

    # ---------- enviar ----------

    def enviar_mensaje(self, telefono: str, mensaje: str) -> str:
        """Envía un mensaje de texto. Devuelve el `idMessage` de Green API."""
        body = {"chatId": self._chat_id(telefono), "message": mensaje}
        resp = self._post("sendMessage", body)
        return str(resp.get("idMessage", ""))

    def notificar_estado(self, telefono: str, mensaje: str) -> str:
        """Mensaje informativo (sin acción asociada)."""
        return self.enviar_mensaje(telefono, mensaje)

    def enviar_archivo(
        self,
        telefono: str,
        ruta,
        *,
        caption: str = "",
    ) -> str:
        """Sube un archivo local a WhatsApp via Green API sendFileByUpload.

        IMPORTANTE — seguridad de datos:
          - El archivo original NO se modifica, mueve ni elimina.
          - Solo se lee para el upload. Ante cualquier fallo la excepción
            se propaga al caller; el archivo queda intacto en disco.

        Devuelve el `idMessage` de Green API.
        """
        from pathlib import Path
        ruta = Path(ruta)
        if not ruta.is_file():
            from src.core.exceptions import AgentError
            raise AgentError(f"archivo no encontrado: {ruta}")

        instance, token = self._api()
        url = (
            f"{self._base}/waInstance{instance}"
            f"/sendFileByUpload/{token}"
        )
        chat_id = self._chat_id(telefono)

        with open(ruta, "rb") as fh:
            GREEN_API_LIMITER.acquire()
            r = requests.post(
                url,
                data={"chatId": chat_id, "caption": caption},
                files={"file": (ruta.name, fh)},
                timeout=self._timeout,
            )
        r.raise_for_status()
        self._log.info("archivo enviado a %s — %s", telefono, ruta.name)
        return str(r.json().get("idMessage", ""))

    # ---------- solicitud de confirmación ----------

    def solicitar_confirmacion(
        self,
        *,
        expediente_id: str,
        tipo_accion: str,
        descripcion: str,
        payload: Optional[dict] = None,
        ttl_segundos: int = WHATSAPP_CONFIRMATION_TIMEOUT,
        telefono: Optional[str] = None,
    ) -> str:
        exp = self.db.obtener_expediente(expediente_id)
        if not exp:
            raise ValueError(f"expediente {expediente_id!r} no existe")
        if telefono is None:
            telefono = exp["telefono_cliente"]
        mensaje = self._formatear_solicitud(descripcion)
        message_id = self.enviar_mensaje(telefono, mensaje)
        expira_en = (_now_utc() + timedelta(seconds=ttl_segundos)).isoformat()
        accion_id = self.db.crear_accion_pendiente(
            expediente_id=expediente_id,
            tipo_accion=tipo_accion,
            descripcion=descripcion,
            payload=payload,
            whatsapp_message_id=message_id,
            expira_en=expira_en,
            actor=self.name,
        )
        self._log.info("confirmación solicitada accion=%s exp=%s tipo=%s",
                       accion_id, expediente_id, tipo_accion)
        return accion_id

    def _formatear_solicitud(self, descripcion: str) -> str:
        return (
            "*catastro-bot* — solicitud de confirmación\n\n"
            f"{descripcion}\n\n"
            "Responda *SI* para confirmar o *NO* para cancelar.\n"
            "_Esta solicitud expira en 24 horas._"
        )

    # ---------- polling de respuestas ----------

    def procesar_respuestas(self, max_ciclos: int = 50) -> int:
        """Drena la cola de notificaciones de Green API. Resuelve las
        acciones cuya respuesta del cliente sea concluyente (SI/NO).
        Devuelve la cantidad de acciones resueltas."""
        resueltas = 0
        for _ in range(max_ciclos):
            notif = self._get("receiveNotification")
            if not notif:
                break
            receipt_id = notif.get("receiptId")
            try:
                if self._procesar_notificacion(notif):
                    resueltas += 1
            except Exception:
                self._log.exception("error procesando notificación %s", receipt_id)
            finally:
                if receipt_id is not None:
                    try:
                        self._delete("deleteNotification", str(receipt_id))
                    except Exception:
                        self._log.exception("ack falló para %s", receipt_id)
        return resueltas

    def _procesar_notificacion(self, notif: dict) -> bool:
        body = notif.get("body") or {}
        if body.get("typeWebhook") != "incomingMessageReceived":
            return False

        # ── Idempotencia ───────────────────────────────────────────────
        # Si el bot crashea entre `resolver_accion` y `deleteNotification`,
        # la próxima ejecución re-procesaría el mismo mensaje. Marcamos el
        # idMessage en BD ANTES de procesar — `INSERT OR IGNORE` garantiza
        # que si ya existe, no lo re-procesamos.
        msg_id = str(body.get("idMessage") or notif.get("receiptId") or "")
        if msg_id:
            try:
                inserted = self.db.mark_whatsapp_message_processed(msg_id)
                if not inserted:
                    self._log.debug(
                        "notificación %s ya procesada antes — skip (idempotencia)",
                        msg_id,
                    )
                    return False
            except Exception:
                # Si la BD falla, mejor procesar (fail-open) que perder mensajes.
                # Pero loggear porque indica problema serio.
                self._log.exception(
                    "idempotencia: error registrando %s — procesando igual",
                    msg_id,
                )

        chat_id = body.get("senderData", {}).get("chatId", "")
        if not chat_id.endswith("@c.us"):
            return False
        sender_phone = chat_id[: -len("@c.us")]

        md = body.get("messageData", {}) or {}
        text = self._extraer_texto(md)
        if not text:
            return False

        # 1) Comandos administrativos (operadores autorizados)
        if self._command_router and self._command_router.is_command(text):
            handled = self._command_router.handle(
                sender_phone=sender_phone, text=text
            )
            if handled:
                return True  # consumido como comando (incluso si fue rechazado)

        # 2) Respuesta SI/NO a una acción pendiente del cliente
        quoted_id = self._extraer_quoted_id(md)
        decision = self._clasificar(text)
        if decision is None:
            self._log.info("mensaje libre de %s — sin auto-resolución", sender_phone)
            return False

        accion = self._encontrar_accion(sender_phone=sender_phone,
                                        quoted_id=quoted_id)
        if not accion:
            self._log.warning("respuesta de %s sin acción pendiente match",
                              sender_phone)
            return False

        nuevo_estado = "confirmada" if decision else "rechazada"
        self.db.resolver_accion(
            accion["id"], nuevo_estado,
            whatsapp_response=text, actor=self.name,
        )
        self._log.info("acción %s resuelta como %s (exp=%s)",
                       accion["id"], nuevo_estado, accion["expediente_id"])
        return True

    def _extraer_texto(self, md: dict) -> str:
        t = md.get("typeMessage")
        if t == "textMessage":
            return md.get("textMessageData", {}).get("textMessage", "") or ""
        if t in ("extendedTextMessage", "quotedMessage"):
            return md.get("extendedTextMessageData", {}).get("text", "") or ""
        return ""

    def _extraer_quoted_id(self, md: dict) -> Optional[str]:
        if md.get("typeMessage") != "quotedMessage":
            return None
        q = md.get("quotedMessage") or {}
        return q.get("stanzaId") or q.get("idMessage")

    def _clasificar(self, text: str) -> Optional[bool]:
        t = text.strip().lower()
        # tomar la primera palabra significativa
        primera = re.split(r"[\s,.!?]+", t, maxsplit=1)[0] if t else ""
        if primera in _AFFIRM:
            return True
        if primera in _NEGATE:
            return False
        return None

    def _encontrar_accion(
        self, *, sender_phone: str, quoted_id: Optional[str]
    ) -> Optional[dict]:
        import json as _json
        sender_digits = _phone_digits(sender_phone)
        candidatas = []
        for accion in self.db.acciones_pendientes():
            exp = self.db.obtener_expediente(accion["expediente_id"])
            if not exp:
                continue
            # Comparar contra telefono_cliente
            cliente_digits = _phone_digits(exp["telefono_cliente"])
            if cliente_digits.endswith(sender_digits) or sender_digits.endswith(cliente_digits):
                candidatas.append(accion)
                continue
            # También comparar contra operador_telefono en metadata
            try:
                meta = _json.loads(exp.get("metadata_json") or "{}")
                operador = _phone_digits(meta.get("operador_telefono") or "")
                if operador and (operador.endswith(sender_digits) or sender_digits.endswith(operador)):
                    candidatas.append(accion)
            except Exception:
                pass
        if not candidatas:
            return None
        if quoted_id:
            for a in candidatas:
                if a.get("whatsapp_message_id") == quoted_id:
                    return a
        candidatas.sort(key=lambda a: a["fecha_solicitud"], reverse=True)
        return candidatas[0]

    # ---------- expiración ----------

    def expirar_acciones_vencidas(self) -> int:
        ahora = _now_utc().isoformat()
        expiradas = 0
        for a in self.db.acciones_pendientes():
            if a.get("expira_en") and a["expira_en"] <= ahora:
                try:
                    self.db.resolver_accion(a["id"], "expirada", actor=self.name)
                    expiradas += 1
                    self._log.info("acción %s expirada (exp=%s)",
                                   a["id"], a["expediente_id"])
                except Exception:
                    self._log.exception("error expirando acción %s", a["id"])
        return expiradas

    # ---------- BaseAgent ----------

    def run(self, expediente_id: str) -> None:
        """Compatibilidad con BaseAgent — el polling se ejecuta global, no por expediente."""
        self.procesar_respuestas()
        self.expirar_acciones_vencidas()
