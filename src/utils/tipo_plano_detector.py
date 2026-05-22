"""Detección automática del `tipo_plano` BD desde el contenido del plano/registro.

El campo `tipo_plano` en la BD (`expedientes.tipo_plano`) determina el flujo
del bot — segregaciones pasan por Muni, rectificaciones van directo a APT.
Hoy el operador debe declararlo a mano al crear el expediente; este módulo
lo deduce automáticamente desde:

  1. Texto del cajetín del plano (`PARA RECTIFICAR`, `PARA SEGREGAR`, etc.)
  2. Campo "INFORMACION DE REGISTRO" del cajetín (`ES PARTE DE`, `SEGREGACION`)
  3. Anotaciones del registro RNP (`SEGREGACION DE LOTE EN CABEZA DE SU DUEÑO`)
  4. Diferencia entre área registro y área real (señal de rectificación)

Devuelve uno de los tipos válidos en `src.models.plano.TipoPlano`:
  - segregacion
  - reunion_de_fincas
  - rectificacion
  - informacion_posesoria
  - finca_completa

Cuando hay ambigüedad o no se detecta nada → devuelve "" y el operador
debe declararlo manualmente.

USO:
    from src.utils.tipo_plano_detector import detectar_tipo_plano
    tipo, confianza, motivos = detectar_tipo_plano(
        texto_cajetin="INFORMACION DE REGISTRO PARA RECTIFICAR AREA",
        texto_registro="ANOTACIONES SOBRE LA FINCA: NO HAY",
        area_real_m2=63803.34,
        area_registro_m2=55679.0,
    )
    print(f"{tipo} (confianza={confianza}): {motivos}")
"""
from __future__ import annotations
import re
import unicodedata
from typing import Optional


# Tipos válidos en la BD — duplicado para evitar import circular
TIPOS_VALIDOS = {
    "segregacion",
    "reunion_de_fincas",
    "rectificacion",
    "informacion_posesoria",
    "finca_completa",
}


# ── Patrones de detección ──────────────────────────────────────────────
# Cada patrón es (regex_text, tipo_resultante, peso_confianza).
# Peso: 3=fuerte 2=medio 1=débil. Múltiples patrones se acumulan.

_PATRONES_SEGREGACION = [
    (r"\bES\s+PARTE\s+DE\b",                                3),
    (r"\bSEGREGAR?\b",                                       3),
    (r"\bSEGREGACI[OÓ]N\b",                                  3),
    (r"\bSEGREGACI[OÓ]N\s+DE\s+LOTE\b",                      3),
    (r"\bLOTE\s+SEGREGADO\b",                                2),
    (r"\bPARA\s+SEGREGAR(?:\s+LOTE)?\b",                     3),
    (r"\bRESTO\s+SE\s+RESERVA\b",                            2),
]

_PATRONES_REUNION = [
    (r"\bREUNI[OÓ]N\s+DE\s+FINCAS?\b",                       3),
    (r"\bPARA\s+REUNIR\b",                                   3),
    (r"\bUNIFICAR\b",                                        2),
    (r"\bUNIFICACI[OÓ]N\b",                                  2),
]

_PATRONES_RECTIFICACION = [
    (r"\bPARA\s+RECTIFICAR\s+AREA\b",                        3),
    (r"\bPARA\s+RECTIFICAR\b",                               2),
    (r"\bRECTIFICACI[OÓ]N\s+DE\s+AREA\b",                    3),
    (r"\bRECTIFICAR\s+MEDIDA\b",                             3),
    (r"\bRECTIFICA[CR][LI]A?[OÓ]?N?\s+DE\s+L[IÍ]NDEROS?\b",  2),
]

_PATRONES_INFO_POSESORIA = [
    (r"\bINFORMACI[OÓ]N\s+POSESORIA\b",                      3),
    (r"\bPARA\s+TITULAR\b",                                  3),
    (r"\bUSUCAPI[OÓ]N\b",                                    2),
    (r"\bEN\s+POSESI[OÓ]N\s+DE\b",                           2),
]

_PATRONES_FINCA_COMPLETA = [
    (r"\bFINCA\s+COMPLETA\b",                                3),
    (r"\bLEVANTAMIENTO\s+DE\s+FINCA\s+COMPLETA\b",           3),
    (r"\bPRIMER\s+PLANO\b",                                  1),
]

_PATRONES_POR_TIPO = {
    "segregacion":           _PATRONES_SEGREGACION,
    "reunion_de_fincas":     _PATRONES_REUNION,
    "rectificacion":         _PATRONES_RECTIFICACION,
    "informacion_posesoria": _PATRONES_INFO_POSESORIA,
    "finca_completa":        _PATRONES_FINCA_COMPLETA,
}


def _normalizar(s: str) -> str:
    """Quita acentos y pasa a uppercase para matching robusto."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.upper()


def _detectar_por_patrones(texto: str) -> dict[str, tuple[int, list[str]]]:
    """Cuenta matches por tipo. Devuelve {tipo: (suma_pesos, [motivos])}."""
    if not texto:
        return {}
    texto_norm = _normalizar(texto)
    scores: dict[str, tuple[int, list[str]]] = {}
    for tipo, patrones in _PATRONES_POR_TIPO.items():
        suma = 0
        motivos: list[str] = []
        for patron, peso in patrones:
            # Patrón ya viene normalizado (sin acentos, mayúsculas)
            if re.search(patron, texto_norm):
                suma += peso
                motivos.append(f"{patron} (peso {peso})")
        if suma > 0:
            scores[tipo] = (suma, motivos)
    return scores


def detectar_tipo_plano(
    *,
    texto_cajetin: str = "",
    texto_registro: str = "",
    naturaleza_registro: str = "",
    area_real_m2: Optional[float] = None,
    area_registro_m2: Optional[float] = None,
    es_parte_de_flag: bool = False,
) -> tuple[str, str, list[str]]:
    """Deduce el `tipo_plano` BD desde texto del cajetín + registro.

    Args:
        texto_cajetin: texto completo extraído del cajetín del plano PDF.
            Suele incluir "INFORMACION DE REGISTRO PARA RECTIFICAR AREA" o
            "FOLIO REAL ES PARTE DE...".
        texto_registro: texto de la "Información de Registro" del RNP.
            Suele incluir anotaciones tipo "SEGREGACION DE LOTE...".
        naturaleza_registro: el campo "NATURALEZA" del registro.
        area_real_m2: área levantada (cajetín).
        area_registro_m2: área según registro RNP.
        es_parte_de_flag: True si el registro/cajetín dice "ES PARTE DE"
            (señal directa de segregación). Si Vision ya lo extrajo
            estructurado, pasarlo aquí.

    Returns:
        (tipo, confianza, motivos)
          - tipo: uno de TIPOS_VALIDOS, o "" si no se pudo deducir
          - confianza: "alta", "media", "baja", ""
          - motivos: lista legible de por qué se eligió ese tipo
    """
    todos_textos = "\n".join(filter(None, [
        texto_cajetin, texto_registro, naturaleza_registro,
    ]))
    scores = _detectar_por_patrones(todos_textos)
    motivos: list[str] = []

    # 1. Flag directo de "ES PARTE DE" → boost a segregación
    if es_parte_de_flag:
        prev = scores.get("segregacion", (0, []))
        scores["segregacion"] = (prev[0] + 3, prev[1] + ["es_parte_de_flag=True"])

    # 2. Diferencia de área → señal de rectificación
    if area_real_m2 and area_registro_m2 and area_registro_m2 > 0:
        diff_pct = abs(area_real_m2 - area_registro_m2) / area_registro_m2 * 100
        # Si áreas muy distintas Y NO hay señal de segregación, podría ser rectificación
        if diff_pct > 10 and "segregacion" not in scores:
            prev = scores.get("rectificacion", (0, []))
            scores["rectificacion"] = (
                prev[0] + 1,
                prev[1] + [f"area difiere {diff_pct:.1f}% (sin señal de segregación)"],
            )

    if not scores:
        return "", "", ["Sin señales — el operador debe declarar el tipo"]

    # Ordenar por peso descendente
    ranked = sorted(scores.items(), key=lambda kv: -kv[1][0])
    top_tipo, (top_score, top_motivos) = ranked[0]

    # Determinar confianza:
    #   alta:  primero tiene >=3 puntos Y al menos 2 más que el segundo
    #   media: primero tiene >=2 puntos Y al menos 1 más que el segundo
    #   baja:  otros casos
    segundo_score = ranked[1][1][0] if len(ranked) > 1 else 0
    if top_score >= 3 and (top_score - segundo_score) >= 2:
        confianza = "alta"
    elif top_score >= 2 and (top_score - segundo_score) >= 1:
        confianza = "media"
    else:
        confianza = "baja"

    motivos = list(top_motivos)
    if len(ranked) > 1:
        # Mencionar otros candidatos para transparencia
        otros = ", ".join(f"{t}({s[0]})" for t, s in ranked[1:3])
        motivos.append(f"otros candidatos: {otros}")

    return top_tipo, confianza, motivos


__all__ = ["detectar_tipo_plano", "TIPOS_VALIDOS"]
