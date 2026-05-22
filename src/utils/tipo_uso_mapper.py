"""Mapea la NATURALEZA del registro al código de tipo_uso del APT.

Reglas de oficina (definidas durante el modo aprendizaje):
  - Si la naturaleza dice "construido y solar" → CONSTRUIDO Y SOLAR (31)
  - Si dice "solar" sin construido → SOLAR (3)
  - Si dice "repasto/repastos" → REPASTOS (36)
  - Si dice cultivos/café/casa+bodega → CULTIVOS VARIOS (23)
  - Default no reconocido → "" (operador debe elegir manual)
"""
from __future__ import annotations
import re


# Códigos APT relevantes (catálogo del dropdown #ddlTipoUso)
TIPO_USO_SOLAR              = "3"
TIPO_USO_CONSTRUIDO_Y_SOLAR = "31"
TIPO_USO_REPASTOS           = "36"
TIPO_USO_CULTIVOS_VARIOS    = "23"
TIPO_USO_BOSQUE             = "30"
TIPO_USO_FRUTALES           = "27"
TIPO_USO_CONSTRUIDO         = "2"
TIPO_USO_AGRICULTURA        = "35"   # AGRICULTURA pura (sembrado, sin construcción)
TIPO_USO_PASTO              = "36"   # mismo que repastos según catálogo APT
TIPO_USO_RESIDENCIAL        = "37"   # solo casa habitada


def mapear_tipo_uso(naturaleza: str) -> str:
    """Mapea texto de NATURALEZA del registro al código tipo_uso de APT.

    Devuelve "" si no se reconoce — el operador debe elegirlo manualmente
    o ajustar la regla.
    """
    if not naturaleza:
        return ""
    t = naturaleza.lower()

    # Construido y solar (compuesto, va antes que solar simple)
    if "construido y solar" in t or ("construido" in t and "solar" in t):
        return TIPO_USO_CONSTRUIDO_Y_SOLAR

    # Solar simple (lote sin construcción)
    if re.search(r"\bsolar\b", t) and "construido" not in t:
        return TIPO_USO_SOLAR

    # Repastos / pastos / potreros (terreno de pasto)
    if "repasto" in t or re.search(r"\bpasto\b", t) or "potrero" in t:
        return TIPO_USO_REPASTOS

    # Cultivos varios — café, agrícola, cultivo
    if any(k in t for k in ("cultivo", "café", "cafe", "agrícol", "agricol")):
        return TIPO_USO_CULTIVOS_VARIOS

    # Frutales puros
    if "frutal" in t:
        return TIPO_USO_FRUTALES

    # Bosque / charral
    if "bosque" in t or "charral" in t:
        return TIPO_USO_BOSQUE

    # Caña / sembrado genérico → agricultura
    if "caña" in t or "cana" in t or "sembrado" in t:
        return TIPO_USO_AGRICULTURA

    # Residencial / casa de habitación
    if "habitaci" in t or ("residencial" in t and "construido" not in t):
        return TIPO_USO_RESIDENCIAL

    return ""


__all__ = ["mapear_tipo_uso"]
