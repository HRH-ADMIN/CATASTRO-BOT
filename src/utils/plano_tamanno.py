"""Detecta el tamaño físico del PDF de un plano y lo mapea al código APT.

Lee el MediaBox de la primera página del PDF (en puntos PDF, 1pt = 1/72 in)
y lo convierte a centímetros. Luego busca el tamaño estándar más cercano
del catálogo APT (#ddlTamanno) — distinguiendo orientación portrait/landscape.

Catálogo APT (verificado mayo 2026):
   1 = 22 X 32 CM        11 = 32 X 22 CM
   2 = 32 X 44 CM        12 = 44 X 32 CM
   3 = 44 X 64 CM        13 = 64 X 44 CM
   4 = 64 X 88 CM        14 = 88 X 64 CM
   5 = 88 X 128 CM       15 = 128 X 88 CM
"""
from __future__ import annotations
from pathlib import Path

# Tamaños estándar APT en cm: (ancho, alto) portrait
TAMANNOS_PORTRAIT = [
    ("1",  22,  32),
    ("2",  32,  44),
    ("3",  44,  64),
    ("4",  64,  88),
    ("5",  88, 128),
]
TAMANNOS_LANDSCAPE = [
    ("11", 32,  22),
    ("12", 44,  32),
    ("13", 64,  44),
    ("14", 88,  64),
    ("15", 128, 88),
]


def _pt_to_cm(pt: float) -> float:
    """1 punto PDF = 1/72 pulgada. 1 pulgada = 2.54 cm."""
    return pt / 72.0 * 2.54


def detectar_tamanno_apt(pdf_path: Path | str) -> str:
    """Devuelve el código APT del tamaño del plano (ej. "1", "2", ...).

    Devuelve "" si no se pudo detectar.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        return ""
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(pdf_path))
        if not reader.pages:
            return ""
        page = reader.pages[0]
        mb = page.mediabox
        ancho_pt = float(mb.width)
        alto_pt  = float(mb.height)
    except Exception:
        return ""

    ancho_cm = _pt_to_cm(ancho_pt)
    alto_cm  = _pt_to_cm(alto_pt)

    es_landscape = ancho_cm > alto_cm
    candidatos = TAMANNOS_LANDSCAPE if es_landscape else TAMANNOS_PORTRAIT
    # Tamaño físico real (sin importar orientación) — usar lado mayor y menor
    mayor = max(ancho_cm, alto_cm)
    menor = min(ancho_cm, alto_cm)

    # Buscar el tamaño estándar más cercano (suma de diferencias absolutas)
    mejor = ""
    mejor_dif = float("inf")
    for codigo, w, h in candidatos:
        c_mayor = max(w, h)
        c_menor = min(w, h)
        dif = abs(c_mayor - mayor) + abs(c_menor - menor)
        if dif < mejor_dif:
            mejor_dif = dif
            mejor = codigo
    # Si la diferencia es enorme (>10cm en suma) — no estamos seguros
    if mejor_dif > 10:
        return ""
    return mejor


__all__ = ["detectar_tamanno_apt"]
