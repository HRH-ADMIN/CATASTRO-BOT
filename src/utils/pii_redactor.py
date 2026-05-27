"""Filtro de logging que redacta PII (Sprint 4 / S-08).

Reemplaza datos personales sensibles en mensajes de log con tokens
placeholder ANTES de escribir a archivo o consola. Patrones específicos
de Costa Rica.

Por qué redactar:
  - Si un atacante obtiene `logs/catastro-bot.log`, no debería poder
    enumerar las cédulas/teléfonos/emails de los clientes del operador.
  - Cumple con sentido común de minimización de datos (Ley 8968 CR).
  - El audit_log en BD mantiene los datos completos (está cifrado y
    tiene hash chain) — esto NO redacta ahí.

Patrones cubiertos:
  - **Cédula nacional CR** (1-4-4 o 9 dígitos):
      Ej: "1-1234-5678", "112345678" → "CEDULA_REDACTED"
  - **Cédula jurídica** (10 dígitos comenzando con 3):
      Ej: "3-101-123456", "3101123456" → "CED_JUR_REDACTED"
  - **DIMEX** (12 dígitos):
      Ej: "118200012345" → "DIMEX_REDACTED"
  - **Teléfono CR** (8 dígitos comenzando con 2/4/6/7/8, opcional +506):
      Ej: "+506 8888 8888", "88888888", "506-8888-8888" → "TEL_REDACTED"
  - **Email**:
      Ej: "user@dominio.com" → "EMAIL_REDACTED"

NO redactados:
  - Números de expediente (RDF-2026-001, SEG-2026-001) — necesarios para
    debug.
  - IDs de trámite APT (1258460) — necesarios para debug.
  - Folios, identificadores prediales, planos — son públicos.
  - Fechas, timestamps.
  - Identidades de empleados internos (logger names, módulos).

Override:
  - `CATASTRO_LOG_REDACT_PII=0` → desactiva. Útil en development cuando
    se necesita ver datos crudos.

Plan: PLAN_MEJORAS Sprint 4 / S-08.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Iterable

# Orden importa: emails primero (contienen @) para que no se confundan.
# Después cédula jurídica (10 digitos) antes que nacional (9) para que
# "3-101-123456" no se interprete como nacional + sobrante.
_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Email — patrón conservador, no demasiado greedy
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
     "EMAIL_REDACTED"),

    # Teléfono CR con +506 y separadores opcionales
    # Acepta: +506 8888 8888, +506-8888-8888, +50688888888
    (re.compile(r"\+506[\s\-]?\d{4}[\s\-]?\d{4}\b"),
     "TEL_REDACTED"),

    # Cédula jurídica: 3-XXX-XXXXXX (siempre 3 al inicio, 10 dígitos)
    # ANTES de la cédula nacional para no comerse 3-101-123456
    (re.compile(r"\b3[\s\-]?\d{3}[\s\-]?\d{6}\b"),
     "CED_JUR_REDACTED"),

    # DIMEX (12 dígitos, sin separadores típicamente — extranjeros)
    (re.compile(r"\b\d{12}\b"),
     "DIMEX_REDACTED"),

    # Cédula nacional CR: 1-XXXX-XXXX (con separadores) o 9 dígitos seguidos
    # Comienza con 1-9 (provincia), después 4+4.
    # Con separadores: "1-1234-5678", "1 1234 5678"
    (re.compile(r"\b[1-9][\s\-]\d{4}[\s\-]\d{4}\b"),
     "CEDULA_REDACTED"),

    # Sin separadores: 9 dígitos seguidos. CON cuidado: NO matchear
    # años (4 dígitos) ni códigos cortos. \b a ambos lados.
    # IMPORTANTE: 9 dígitos seguidos sin separadores es raro en logs
    # legítimos (timestamps, IDs de trámite APT son 7 dígitos, planos
    # tienen formato). 9 dígitos solos típicamente son cédulas.
    (re.compile(r"\b[1-9]\d{8}\b"),
     "CEDULA_REDACTED"),

    # Teléfono sin +506: 8 dígitos comenzando con 2/4/5/6/7/8 (rangos
    # válidos de telefonía en CR). NO matchear si ya viene precedido
    # por +506 (eso lo agarra el patrón anterior). \b a ambos lados.
    # Después de redactar nacional, este captura el resto.
    (re.compile(r"\b[24-8]\d{3}[\s\-]?\d{4}\b"),
     "TEL_REDACTED"),
]


def _enabled() -> bool:
    """¿El redactor está habilitado? Default sí, override con env var."""
    val = os.environ.get("CATASTRO_LOG_REDACT_PII", "1").strip().lower()
    return val not in ("0", "false", "no", "off")


def redact(text: str) -> str:
    """Aplica todos los patrones a una string. Idempotente."""
    if not text:
        return text
    out = text
    for pattern, replacement in _PATTERNS:
        out = pattern.sub(replacement, out)
    return out


class PIIRedactor(logging.Filter):
    """Filtro logging que reemplaza PII en `record.msg` y args formateados.

    Aplica al texto FINAL del mensaje — eso captura tanto strings
    pre-formateadas como `logger.info("foo %s", cedula)` (donde `cedula`
    está en args).

    Cómo funciona:
      1. Resolvemos el mensaje final via `record.getMessage()`.
      2. Le aplicamos redact().
      3. Reemplazamos `record.msg` con el resultado y vaciamos args.
      4. El handler recibe el record ya limpio.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not _enabled():
            return True
        try:
            original = record.getMessage()
            redacted = redact(original)
            if redacted != original:
                record.msg = redacted
                record.args = ()
        except Exception:
            # Nunca rompemos el logging por un bug del redactor
            pass
        return True


def install_on_root_logger(logger_name: str = "catastro") -> None:
    """Agrega el filtro al logger root del bot (idempotente).

    Como el filtro vive en el logger (no en el handler), TODOS los
    handlers (file rotante + consola) reciben mensajes ya redactados.

    Llamar después de configurar handlers (sino no hay logger).
    """
    root = logging.getLogger(logger_name)
    # Idempotencia: no agregar si ya está
    for f in root.filters:
        if isinstance(f, PIIRedactor):
            return
    root.addFilter(PIIRedactor())
