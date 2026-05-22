"""Validadores de inputs (teléfonos CR, formatos de plano, etc)."""
from __future__ import annotations

import re

_TELEFONO_CR = re.compile(r"^(?:\+?506)?[2-8]\d{7}$")


def validar_telefono_cr(numero: str) -> bool:
    cleaned = numero.replace(" ", "").replace("-", "")
    return bool(_TELEFONO_CR.match(cleaned))


def normalizar_telefono_cr(numero: str) -> str:
    """Devuelve el teléfono normalizado a formato +506XXXXXXXX."""
    cleaned = numero.replace(" ", "").replace("-", "").replace("+", "")
    if cleaned.startswith("506"):
        cleaned = cleaned[3:]
    if not _TELEFONO_CR.match(cleaned):
        raise ValueError(f"teléfono CR inválido: {numero!r}")
    return f"+506{cleaned}"
