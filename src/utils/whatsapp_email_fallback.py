"""Fallback por email cuando Green API está down (Sprint 4 / N-03 sub-paso B).

Reutiliza la función enviar_email_smtp() de email_digest.py + las
credenciales `muni-san-ramon` (cuenta Gmail topografiahrh@gmail.com
con app password) que ya están configuradas en Cred Manager.

Usage:
    from src.utils.whatsapp_email_fallback import send_via_email_fallback
    ok = send_via_email_fallback(
        credentials, telefono="+50688887310",
        mensaje="Su trámite SEG-2026-005 cambió a estado X.",
        contexto="cambio_estado",
    )

El destinatario del email **es siempre el operador** (no el cliente),
porque WhatsApp es el canal que el cliente usa; si está caído el bot
no puede inferir el email del cliente con confianza. El operador
recibe la copia y decide a quién reenviarla manualmente.

Plan: PLAN_MEJORAS Sprint 4 / N-03 sub-paso B.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("catastro.whatsapp_email_fallback")


def send_via_email_fallback(
    credentials,
    *,
    telefono: str,
    mensaje: str,
    contexto: str = "whatsapp_fallback",
    to_override: Optional[str] = None,
) -> bool:
    """Envía el mensaje WhatsApp por email al operador como fallback.

    Args:
        credentials: CredentialManager.
        telefono: destinatario original del WhatsApp (queda en el body).
        mensaje: contenido del mensaje WhatsApp original.
        contexto: tag para el subject del email (ej. 'cambio_estado',
                  'morosidad_muni'). Default 'whatsapp_fallback'.
        to_override: si se pasa, envía a esa dirección en lugar del
                     operador default.

    Returns:
        True si el email salió OK, False si falló (con log).
    """
    try:
        user, password = credentials.get_muni_san_ramon()
    except Exception as exc:
        log.error("no se pudieron leer credenciales muni-san-ramon: %s", exc)
        return False

    # Importar lazy para evitar dependencia circular en módulos de test
    try:
        from src.utils.email_digest import enviar_email_smtp
    except Exception as exc:
        log.error("no se pudo importar enviar_email_smtp: %s", exc)
        return False

    to = to_override or user  # default: el mismo operador

    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    subject = (
        f"[catastro-bot] WhatsApp caído — mensaje pendiente ({contexto})"
    )
    body_text = (
        f"El bot intentó enviar este mensaje por WhatsApp pero Green API\n"
        f"está reportando HTTP 466 (instancia desautorizada o quota excedida).\n"
        f"\n"
        f"Destinatario original: {telefono}\n"
        f"Contexto: {contexto}\n"
        f"Timestamp: {ts}\n"
        f"\n"
        f"───────── Mensaje pendiente ─────────\n"
        f"{mensaje}\n"
        f"─────────────────────────────────────\n"
        f"\n"
        f"Acción sugerida:\n"
        f"  1. Verificar la consola de Green API: https://console.green-api.com\n"
        f"  2. Re-autorizar la instancia si está desconectada (escanear QR).\n"
        f"  3. Si fue quota, esperar al reset mensual o subir el plan.\n"
        f"  4. Una vez restaurado, el bot lo detectará en el próximo\n"
        f"     auto-recovery (cada 30 min) y volverá a usar WhatsApp.\n"
        f"\n"
        f"Estado actual del servicio: http://localhost:9224/config/runtime\n"
    )

    ok = enviar_email_smtp(
        from_addr=user,
        password=password,
        to=to,
        subject=subject,
        body_text=body_text,
    )
    if ok:
        log.info(
            "fallback email enviado (de %s a %s, contexto=%s, "
            "destinatario_original=%s)",
            user, to, contexto, telefono,
        )
    return ok


__all__ = ["send_via_email_fallback"]
