"""Helpers de presentación: cómo mostrar un expediente al operador.

El operador no recuerda los números RDF-2026-XXX, recuerda los proyectos
("OMAR_2026", "Rolando Granja"). Este módulo centraliza la lógica de qué
identificador mostrar en mensajes de WhatsApp / logs / alertas.

Convención:
  - Identificador primario: `nombre_proyecto` (de metadata)
  - Fallback: `numero_expediente` (siempre existe)
  - Para comandos que requieren el número, opcionalmente se incluye en
    paréntesis: "OMAR_2026 (RDF-2026-002)".
"""
from __future__ import annotations
import json
from typing import Any


def _meta_de(exp: Any) -> dict:
    """Extrae el dict de metadata de un expediente, manejando los formatos
    posibles: dict crudo de DB con `metadata_json`, dict ya parseado, o un
    dict que ya es la metadata.
    """
    if not exp:
        return {}
    if isinstance(exp, dict):
        # ¿Tiene metadata_json (formato DB)?
        mj = exp.get("metadata_json")
        if isinstance(mj, str):
            try:
                return json.loads(mj or "{}") or {}
            except Exception:
                return {}
        if isinstance(mj, dict):
            return mj
        # Ya parseado o ya es la metadata directamente
        if "nombre_proyecto" in exp or "path_carpeta" in exp:
            return exp
    return {}


def display_proyecto(exp: Any, *, con_numero: bool = True) -> str:
    """Devuelve el identificador legible del expediente.

    Args:
        exp: dict del expediente (con `numero_expediente` y `metadata_json`)
             o el dict de metadata directamente.
        con_numero: si True (default), incluye el numero_expediente entre
            paréntesis para que el operador pueda usarlo en comandos. Si
            False, solo muestra el nombre del proyecto.

    Returns:
        - "OMAR_2026 (RDF-2026-002)" cuando hay nombre_proyecto y con_numero=True
        - "OMAR_2026" cuando hay nombre_proyecto y con_numero=False
        - "RDF-2026-002" como fallback si no hay nombre_proyecto
        - "" si no hay nada

    Ejemplos:
        >>> exp = {"numero_expediente": "RDF-2026-002",
        ...        "metadata_json": '{"nombre_proyecto": "OMAR_2026"}'}
        >>> display_proyecto(exp)
        'OMAR_2026 (RDF-2026-002)'
        >>> display_proyecto(exp, con_numero=False)
        'OMAR_2026'
    """
    if not exp:
        return ""
    if isinstance(exp, dict):
        numero = exp.get("numero_expediente") or exp.get("numero") or ""
    else:
        numero = ""
    meta = _meta_de(exp)
    proyecto = (meta.get("nombre_proyecto") or "").strip()
    if proyecto and numero and con_numero:
        return f"{proyecto} ({numero})"
    if proyecto:
        return proyecto
    return numero or ""


def display_solo_proyecto(exp: Any) -> str:
    """Atajo: display_proyecto(exp, con_numero=False)."""
    return display_proyecto(exp, con_numero=False)
