"""Notificación WhatsApp a cliente — morosidad / aprobación / rechazo muni.

Componer + enviar mensajes claros al cliente cuando la muni responde:

  - MOROSIDAD: "tenés impuestos pendientes, pagá antes que vencimos el plazo"
  - APROBADO:  "visado emitido, el plano va a inscribirse al CFIA"
  - RECHAZADO: "la muni rechazó, el topógrafo está corrigiendo"

Se diseña como funciones puras (componer) + un wrapper que recibe el
WhatsAppAgent ya construido. Eso permite:

  - Tests sin Green API (solo verificar el texto compuesto)
  - Dry-run desde CLI antes de mandar de verdad
  - Reusar desde scheduler/workflow cuando la lógica esté lista

USO:
    from src.utils.muni_notificacion import (
        componer_mensaje_morosidad, notificar_cliente_morosidad,
    )

    msg = componer_mensaje_morosidad(
        nombre_cliente="VICTOR JIMENEZ",
        numero_expediente="SEG-2026-003",
        monto_pendiente=125000.50,
        email_muni="mgamboa@sanramon.go.cr",
    )

    # Para enviarlo:
    notificar_cliente_morosidad(
        whatsapp_agent=wa, telefono="+50688881234",
        nombre_cliente="VICTOR JIMENEZ", numero_expediente="SEG-2026-003",
        monto_pendiente=125000.50,
    )
"""
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger("catastro.muni_notif")


# ── Composición de mensajes ────────────────────────────────────────────

def _formatear_monto(monto: Optional[float]) -> str:
    """Formatea ₡ con separador de miles. Devuelve '?' si no hay monto."""
    if monto is None:
        return "(monto no especificado)"
    return f"₡{monto:,.2f}"


def componer_mensaje_morosidad(
    *,
    nombre_cliente: str,
    numero_expediente: str,
    monto_pendiente: Optional[float] = None,
    email_muni: str = "mgamboa@sanramon.go.cr",
    tramite_apt: Optional[str] = None,
) -> str:
    """Mensaje de aviso de morosidad municipal para el cliente.

    El cliente típicamente NO conoce la jerga ("visado", "impuesto sobre
    bienes inmuebles"). El mensaje es claro, da pasos concretos, y
    enfatiza que el trámite está EN ESPERA hasta que pague.
    """
    nombre = (nombre_cliente or "Buenas").split()[0].title()
    monto_str = _formatear_monto(monto_pendiente)
    extra = ""
    if tramite_apt:
        extra = f"\n_Trámite CFIA: {tramite_apt}_"
    return (
        f"👋 Hola {nombre},\n\n"
        f"La Municipalidad de San Ramón nos avisó que para poder *visar* "
        f"su plano (expediente *{numero_expediente}*) necesitan que esté "
        f"al día con los impuestos municipales.\n\n"
        f"💰 *Monto pendiente:* {monto_str}\n\n"
        f"📌 *Pasos a seguir:*\n"
        f"1. Pagar en la Muni (Plataforma de Servicios o sitio web)\n"
        f"2. Avisarnos cuando esté pago (puede mandar foto del recibo)\n"
        f"3. Reenviamos el visado y el plano sigue a inscripción\n\n"
        f"⏳ El trámite queda EN ESPERA hasta que esté al día.\n"
        f"📧 Email muni: {email_muni}"
        f"{extra}"
    )


def componer_mensaje_aprobado(
    *,
    nombre_cliente: str,
    numero_expediente: str,
    tramite_apt: Optional[str] = None,
) -> str:
    """Mensaje de visado municipal APROBADO."""
    nombre = (nombre_cliente or "Buenas").split()[0].title()
    extra = f"\n_Trámite CFIA: {tramite_apt}_" if tramite_apt else ""
    return (
        f"✅ ¡Buenas noticias, {nombre}!\n\n"
        f"La Municipalidad de San Ramón *aprobó el visado* del plano "
        f"(expediente *{numero_expediente}*).\n\n"
        f"📌 *Siguientes pasos:*\n"
        f"1. El plano se envía al CFIA para inscripción\n"
        f"2. Le avisamos en cuanto esté inscrito (R1/R2)\n"
        f"3. Después se hace la inscripción en Registro Público"
        f"{extra}"
    )


def componer_mensaje_rechazado(
    *,
    nombre_cliente: str,
    numero_expediente: str,
    motivo: str = "",
    tramite_apt: Optional[str] = None,
) -> str:
    """Mensaje de visado RECHAZADO con motivo si está disponible."""
    nombre = (nombre_cliente or "Buenas").split()[0].title()
    motivo_str = f"\n\n*Motivo:* {motivo}" if motivo else ""
    extra = f"\n_Trámite CFIA: {tramite_apt}_" if tramite_apt else ""
    return (
        f"⚠️ Hola {nombre},\n\n"
        f"La Municipalidad nos devolvió el plano (expediente "
        f"*{numero_expediente}*) con observaciones que hay que corregir."
        f"{motivo_str}\n\n"
        f"📌 *Qué pasa ahora:*\n"
        f"- El topógrafo está revisando las correcciones\n"
        f"- Reenviamos el plano corregido a la muni\n"
        f"- Le avisamos cuando esté aprobado"
        f"{extra}"
    )


# ── Envío ──────────────────────────────────────────────────────────────

def notificar_cliente_morosidad(
    *,
    whatsapp_agent,
    telefono: str,
    nombre_cliente: str,
    numero_expediente: str,
    monto_pendiente: Optional[float] = None,
    email_muni: str = "mgamboa@sanramon.go.cr",
    tramite_apt: Optional[str] = None,
) -> dict:
    """Envía notificación de morosidad. Devuelve {ok, id_message, error?}."""
    msg = componer_mensaje_morosidad(
        nombre_cliente=nombre_cliente,
        numero_expediente=numero_expediente,
        monto_pendiente=monto_pendiente,
        email_muni=email_muni,
        tramite_apt=tramite_apt,
    )
    try:
        idm = whatsapp_agent.enviar_mensaje(telefono, msg)
        log.info("morosidad notificada a %s — exp %s — id=%s",
                 telefono, numero_expediente, idm)
        return {"ok": True, "id_message": idm, "mensaje": msg}
    except Exception as exc:
        log.exception("falló notificación morosidad a %s", telefono)
        return {"ok": False, "error": str(exc), "mensaje": msg}


__all__ = [
    "componer_mensaje_morosidad",
    "componer_mensaje_aprobado",
    "componer_mensaje_rechazado",
    "notificar_cliente_morosidad",
]
