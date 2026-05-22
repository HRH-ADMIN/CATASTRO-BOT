"""Calcula el centroide de un polígono dado por sus vértices.

Implementa dos métodos:
  - centroide_promedio: promedio simple de los vértices
  - centroide_areal:    fórmula de Stokes / centroide del área del polígono
                        (más correcta para polígonos irregulares)
"""
from __future__ import annotations
from decimal import Decimal


def centroide_promedio(puntos: list[tuple[float, float]]) -> tuple[float, float]:
    """Centroide simple = promedio de los puntos. (x,y)."""
    if not puntos:
        raise ValueError("lista de puntos vacía")
    n = len(puntos)
    sx = sum(p[0] for p in puntos)
    sy = sum(p[1] for p in puntos)
    return (sx / n, sy / n)


def centroide_areal(puntos: list[tuple[float, float]]) -> tuple[float, float]:
    """Centroide del polígono usando la fórmula del área firmada (más exacta).

    Asume polígono cerrado. El último punto NO debe repetir el primero — la
    función cierra el polígono internamente.
    """
    if len(puntos) < 3:
        raise ValueError("se requieren al menos 3 puntos para un polígono")
    n = len(puntos)
    # Cerrar el polígono virtualmente
    pts = list(puntos) + [puntos[0]]
    a = 0.0   # área firmada × 2
    cx = 0.0
    cy = 0.0
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        cross = x0 * y1 - x1 * y0
        a += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    a *= 0.5
    if a == 0:
        # Polígono degenerado — usar promedio simple
        return centroide_promedio(puntos)
    cx /= 6 * a
    cy /= 6 * a
    return (cx, cy)


def centroide_para_apt(puntos: list[tuple[float, float]]) -> tuple[str, str]:
    """Devuelve (este, norte) del centroide areal listo para llenar APT.

    Aplica:
      - método areal (Stokes) — más exacto que el promedio simple
      - redondeo a 2 decimales (APT rechaza más decimales)
      - formato str sin separador de miles
    """
    e, n = centroide_areal(puntos)
    return (f"{e:.2f}", f"{n:.2f}")


__all__ = ["centroide_promedio", "centroide_areal", "centroide_para_apt"]
