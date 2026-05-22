"""Lectura IMAP de respuestas de la Municipalidad.

Polea el Gmail del topógrafo buscando emails relacionados con trámites
muni y los clasifica en 4 tipos para que el bot actúe:

  1. ACUSE_GOOGLE — confirmación automática del Google Form (no requiere acción)
  2. APROBADO    — visado municipal aprobado → descargar adjunto + avanzar estado
  3. MOROSIDAD   — impuestos pendientes / declaración bienes → notificar cliente
  4. RECHAZADO   — visado rechazado o solicita correcciones → notificar topógrafo

Patrones aprendidos del acuse oficial TILMAN (2026-05-13):
  - Acuses vienen de: forms-receipts-noreply@google.com
  - Email catastral muni: mgamboa@sanramon.go.cr
  - Asunto típico: "Formulario APT-PUBLICO"
  - Tema "morosidad" se infiere por keywords sobre obligaciones municipales

USO:
    from src.utils.muni_imap_reader import (
        clasificar_email, buscar_respuestas_muni,
    )
    # Clasificar un email parseado:
    tipo = clasificar_email(from_addr, subject, body)
    # → "ACUSE_GOOGLE" | "APROBADO" | "MOROSIDAD" | "RECHAZADO" | "DESCONOCIDO"

    # Pollear IMAP y procesar:
    resultados = buscar_respuestas_muni(creds, dias_atras=7)
"""
from __future__ import annotations

import email as email_lib
import imaplib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.header import decode_header
from email.message import Message
from typing import Optional

log = logging.getLogger("catastro.muni_imap")


# ── Constantes ──────────────────────────────────────────────────────────

REMITENTE_ACUSE_GOOGLE = "forms-receipts-noreply@google.com"
EMAIL_CATASTRAL_SR     = "mgamboa@sanramon.go.cr"


# Patrones para clasificar (aprendidos del acuse TILMAN y reglas de oficina)
_RE_ACUSE = re.compile(
    r"gracias por rellenar este formulario|formulario apt-publico",
    re.IGNORECASE,
)
_RE_APROBADO = re.compile(
    r"visado\s+aprobado|plano\s+visado|se\s+aprueba|visto\s+bueno|"
    r"visado\s+municipal\s+(otorgado|emitido)",
    re.IGNORECASE,
)
_RE_RECHAZADO = re.compile(
    r"visado\s+rechazado|se\s+rechaza|no\s+procede|"
    r"falta\s+\w+|debe\s+corregir|debe\s+aportar|"
    r"sin\s+visado|visado\s+denegado",
    re.IGNORECASE,
)
_RE_MOROSIDAD = re.compile(
    r"impuestos?\s+(municipal|pendiente|al\s+d[ií]a|"
    r"morosidad|no\s+vigente|adeuda)|"
    r"declaraci[óo]n\s+de\s+bienes|"
    r"obligacion(es)?\s+municipal|"
    r"propietario\s+no\s+est[áa]\s+al\s+d[ií]a",
    re.IGNORECASE,
)
_RE_MONTO_PENDIENTE = re.compile(r"₡\s*([\d,.']+)|colones?")


# ── Tipos ───────────────────────────────────────────────────────────────

TIPO_ACUSE_GOOGLE = "ACUSE_GOOGLE"
TIPO_APROBADO    = "APROBADO"
TIPO_MOROSIDAD   = "MOROSIDAD"
TIPO_RECHAZADO   = "RECHAZADO"
TIPO_DESCONOCIDO = "DESCONOCIDO"


@dataclass
class EmailMuni:
    """Email clasificado de respuesta muni."""
    tipo:         str           # uno de los TIPO_*
    from_addr:    str
    subject:      str
    body_preview: str           # primeros 500 chars
    fecha:        Optional[str] = None
    tramite_apt:  Optional[str] = None   # número extraído si aplica
    adjuntos:     list[str] = field(default_factory=list)
    monto_pendiente: Optional[float] = None
    raw_message:  Optional[Message] = None


# ── Clasificación ──────────────────────────────────────────────────────

def clasificar_email(
    from_addr: str, subject: str, body: str,
) -> str:
    """Clasifica un email en uno de los 5 tipos.

    Args:
        from_addr: remitente (ej "mgamboa@sanramon.go.cr").
        subject: asunto.
        body: cuerpo plano (texto).

    Returns: TIPO_* string.
    """
    from_norm = (from_addr or "").lower().strip()
    subj_norm = (subject or "").lower()
    body_norm = (body or "").lower()
    combinado = f"{subj_norm} {body_norm}"

    # 1. Acuse de Google Forms — alta prioridad (remitente específico)
    if REMITENTE_ACUSE_GOOGLE in from_norm:
        return TIPO_ACUSE_GOOGLE

    # 2. Email de la muni — clasificar por contenido
    es_muni = (
        EMAIL_CATASTRAL_SR in from_norm
        or "sanramon.go.cr" in from_norm
        or "sanramon" in from_norm
    )
    if not es_muni:
        # Tal vez es de un email no reconocido, pero igual chequeamos patrones
        # por si reenvía desde otra cuenta. Si nada matchea, DESCONOCIDO.
        pass

    # Aprobado tiene prioridad sobre rechazado (en caso de menciones cruzadas)
    if _RE_APROBADO.search(combinado):
        return TIPO_APROBADO
    # Morosidad antes que rechazado (la muni notifica morosidad como primer paso)
    if _RE_MOROSIDAD.search(combinado):
        return TIPO_MOROSIDAD
    if _RE_RECHAZADO.search(combinado):
        return TIPO_RECHAZADO

    if es_muni:
        # Es de la muni pero no matcheó ningún patrón — desconocido pero muni
        return TIPO_DESCONOCIDO

    return TIPO_DESCONOCIDO


def extraer_numero_tramite(texto: str) -> Optional[str]:
    """Extrae N° de trámite APT del texto si aparece (ej '1223951' o 'APT 1223951')."""
    if not texto:
        return None
    # Buscar después de "trámite", "APT", "proyecto", etc.
    m = re.search(
        r"(?:tr[áa]mite|apt|proyecto|expediente)\s*(?:#|n[°˚]?\.?)?\s*(\d{6,})",
        texto, re.IGNORECASE,
    )
    if m:
        return m.group(1)
    # Fallback: cualquier número de 7+ dígitos solo
    m2 = re.search(r"\b(\d{7,})\b", texto)
    return m2.group(1) if m2 else None


def extraer_monto_pendiente(texto: str) -> Optional[float]:
    """Extrae monto en colones si aparece (ej '₡ 50,000' o 'monto a pagar 50000')."""
    if not texto:
        return None
    m = _RE_MONTO_PENDIENTE.search(texto)
    if m and m.group(1):
        try:
            return float(re.sub(r"[,.']", "", m.group(1)))
        except ValueError:
            return None
    return None


# ── IMAP polling ───────────────────────────────────────────────────────

def _decode(s) -> str:
    """Decode email headers (encoded-words)."""
    if not s:
        return ""
    if isinstance(s, bytes):
        try:
            return s.decode("utf-8", errors="replace")
        except Exception:
            return str(s)
    parts = decode_header(s)
    out = ""
    for p, enc in parts:
        if isinstance(p, bytes):
            try:
                out += p.decode(enc or "utf-8", errors="replace")
            except Exception:
                out += p.decode("utf-8", errors="replace")
        else:
            out += p
    return out


def _extraer_body(msg: Message) -> str:
    """Extrae texto plano del email (preferir text/plain)."""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "")
            if ctype == "text/plain" and "attachment" not in disp:
                try:
                    return part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8",
                        errors="replace",
                    )
                except Exception:
                    pass
        # Fallback HTML
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                try:
                    raw = part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8",
                        errors="replace",
                    )
                    return re.sub(r"<[^>]+>", " ", raw)
                except Exception:
                    pass
    else:
        try:
            return msg.get_payload(decode=True).decode(
                msg.get_content_charset() or "utf-8", errors="replace",
            )
        except Exception:
            pass
    return ""


def _extraer_adjuntos(msg: Message) -> list[str]:
    """Lista nombres de archivos adjuntos en el email."""
    if not msg.is_multipart():
        return []
    out = []
    for part in msg.walk():
        disp = str(part.get("Content-Disposition") or "")
        if "attachment" in disp.lower() or "filename" in disp.lower():
            fn = part.get_filename()
            if fn:
                out.append(_decode(fn))
    return out


def buscar_respuestas_muni(
    *,
    user: str,
    password: str,
    imap_host: str = "imap.gmail.com",
    imap_port: int = 993,
    dias_atras: int = 7,
    folder: str = "INBOX",
    limit: int = 50,
) -> list[EmailMuni]:
    """Busca emails relacionados con muni en los últimos N días.

    Devuelve lista clasificada. No descarga adjuntos a disco.

    Args:
        user, password: credenciales IMAP (App Password de Gmail).
        dias_atras: cuántos días atrás escanear.
        limit: máximo de emails a procesar.
    """
    desde = (datetime.now() - timedelta(days=dias_atras)).strftime("%d-%b-%Y")
    out: list[EmailMuni] = []
    try:
        M = imaplib.IMAP4_SSL(imap_host, imap_port)
        M.login(user, password)
        M.select(folder)
        # Buscar emails relevantes:
        #  - de muni san ramón
        #  - O acuses de Google Forms con "APT-PUBLICO" en asunto
        criterios = [
            f'(SINCE "{desde}" FROM "sanramon.go.cr")',
            f'(SINCE "{desde}" FROM "forms-receipts-noreply@google.com")',
        ]
        ids_acumulados: list[bytes] = []
        for crit in criterios:
            typ, data = M.search(None, crit)
            if typ == "OK" and data and data[0]:
                ids_acumulados.extend(data[0].split())
        # Dedup
        ids = list(dict.fromkeys(ids_acumulados))[-limit:]
        for eid in ids:
            typ, data = M.fetch(eid, "(RFC822)")
            if typ != "OK" or not data:
                continue
            raw = data[0][1]
            msg = email_lib.message_from_bytes(raw)
            from_addr = _decode(msg.get("From", ""))
            subject   = _decode(msg.get("Subject", ""))
            body      = _extraer_body(msg)
            fecha     = _decode(msg.get("Date", ""))
            tipo = clasificar_email(from_addr, subject, body)
            out.append(EmailMuni(
                tipo=tipo,
                from_addr=from_addr,
                subject=subject,
                body_preview=body[:500],
                fecha=fecha,
                tramite_apt=extraer_numero_tramite(subject + " " + body),
                adjuntos=_extraer_adjuntos(msg),
                monto_pendiente=(extraer_monto_pendiente(body)
                                 if tipo == TIPO_MOROSIDAD else None),
                raw_message=msg,
            ))
        M.close()
        M.logout()
    except Exception as exc:
        log.exception("buscar_respuestas_muni falló: %s", exc)
        raise
    return out


__all__ = [
    "clasificar_email",
    "extraer_numero_tramite",
    "extraer_monto_pendiente",
    "buscar_respuestas_muni",
    "EmailMuni",
    "TIPO_ACUSE_GOOGLE", "TIPO_APROBADO", "TIPO_MOROSIDAD",
    "TIPO_RECHAZADO", "TIPO_DESCONOCIDO",
    "REMITENTE_ACUSE_GOOGLE", "EMAIL_CATASTRAL_SR",
]
