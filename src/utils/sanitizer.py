"""Sanitización de datos antes de enviarlos a la IA.

REGLA CRÍTICA: ningún dato personal sale del sistema hacia Claude API.
Esto incluye nombres propios, cédulas costarricenses, números de finca y
teléfonos. La IA recibe únicamente datos técnicos del plano.
"""
from __future__ import annotations

import re

# Cédula física CR: 0-0000-0000 (también acepta sin guiones)
_CEDULA_FISICA = re.compile(r"\b[1-9]-?\d{4}-?\d{4}\b")
# Cédula jurídica CR: 3-000-000000 / 3-101-123456
_CEDULA_JURIDICA = re.compile(r"\b3-?\d{3}-?\d{6}\b")
# Número de finca CR: provincia (1-7) + número (formatos varían)
_FINCA = re.compile(r"\bfinca[^\d]{0,5}\d{1,7}-?\d{0,6}\b", re.IGNORECASE)
# Teléfono CR: 8 dígitos, opcionalmente con +506
_TELEFONO = re.compile(r"\b(?:\+?506[-\s]?)?[2-8]\d{3}[-\s]?\d{4}\b")
# Plano catastral CR: P-NÚMERO-AÑO o similar
_PLANO_CATASTRAL = re.compile(r"\b[A-Z]-\d{3,7}-\d{4}\b")


def sanitize(text: str) -> str:
    """Reemplaza datos personales/identificadores por placeholders."""
    text = _CEDULA_JURIDICA.sub("[CED-JUR]", text)
    text = _CEDULA_FISICA.sub("[CED-FIS]", text)
    text = _FINCA.sub("[FINCA]", text)
    text = _PLANO_CATASTRAL.sub("[PLANO]", text)
    text = _TELEFONO.sub("[TEL]", text)
    return text


def assert_safe_for_ai(text: str) -> None:
    """Lanza ValueError si el texto aún contiene datos sensibles."""
    for pattern, label in (
        (_CEDULA_FISICA, "cédula física"),
        (_CEDULA_JURIDICA, "cédula jurídica"),
        (_FINCA, "número de finca"),
        (_PLANO_CATASTRAL, "número de plano"),
        (_TELEFONO, "teléfono"),
    ):
        if pattern.search(text):
            raise ValueError(f"texto contiene {label} sin sanitizar")
