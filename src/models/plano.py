"""Tipos de plano según Ley 6545 de Costa Rica.

Tipos reconocidos por el CFIA / Catastro Nacional:
  segregacion          — Plano de Segregación (fraccionamiento)
  rectificacion        — Rectificación de Medida o Linderos
  informacion_posesoria — Plano de Información Posesoria (derechos posesorios)
  reunion_de_fincas    — Reunión de Fincas (unificación de varias en una)
  fincas_completas     — Plano de Fincas Completas (localización)

Reglas de visado municipal (Ley 6545 Art. pertinente):
  Requieren visado municipal: segregacion, reunion_de_fincas
  NO requieren visado: rectificacion, informacion_posesoria, fincas_completas

Carta de agua (requisito municipal, fraccionamientos residenciales):
  Solo aplica a: segregacion
"""
from enum import Enum


class TipoPlano(str, Enum):
    SEGREGACION           = "segregacion"
    RECTIFICACION         = "rectificacion"
    INFORMACION_POSESORIA = "informacion_posesoria"
    REUNION_DE_FINCAS     = "reunion_de_fincas"
    FINCAS_COMPLETAS      = "fincas_completas"

    @classmethod
    def values(cls) -> list[str]:
        return [t.value for t in cls]
