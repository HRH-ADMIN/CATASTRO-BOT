"""Helper para consultar la memoria operativa del operador.

El operador puede enseñarle al bot dos tipos de cosas:

  - **reglas** (texto libre): notas que el operador quiere ver en cada
    reporte pre-flight. El bot las incluye pero no actúa sobre ellas.

  - **ignoras** (patrones de match): silencian discrepancias cuyo valor
    contiene el patrón. Útil para falsos positivos repetitivos.

Patrón de match para `ignora`:
  - Si el patrón está en `cedula`, `valor`, `valor_esperado` o `contexto`
    de la discrepancia, se silencia.
  - Si el patrón empieza con `tipo:`, se compara contra `tipo` directamente
    (ej. `tipo:protocolo_diferente_al_activo`).

USO:
    from src.utils.memoria_operador import (
        discrepancia_silenciada, filtrar_discrepancias_silenciadas,
    )

    if discrepancia_silenciada(db, discrepancia):
        # NO notificar
        ...
"""
from __future__ import annotations
import logging
from typing import Any

log = logging.getLogger("catastro.memoria_operador")


def _campos_buscables(disc: dict) -> str:
    """Concatena los campos donde un patrón puede matchear."""
    return " ".join(str(disc.get(k, "")) for k in
                    ("cedula", "valor", "valor_esperado", "contexto",
                     "descripcion", "campo", "tse_nombre", "registro_nombre"))


def discrepancia_silenciada(db, discrepancia: dict) -> bool:
    """True si alguna regla `ignora` activa matchea esta discrepancia.

    Args:
        db: Database (debe soportar `listar_memoria_operador`).
        discrepancia: dict como los que produce el bot.

    Returns: True si debe silenciarse (no notificar).
    """
    if not db or not discrepancia:
        return False
    try:
        ignoras = db.listar_memoria_operador(tipo="ignora", activa=True)
    except Exception as exc:
        log.warning("error leyendo ignoras: %s", exc)
        return False
    if not ignoras:
        return False

    tipo_disc = str(discrepancia.get("tipo", ""))
    buscables = _campos_buscables(discrepancia).lower()

    for ig in ignoras:
        patron = (ig.get("patron") or "").strip()
        if not patron:
            continue
        # Patrón con prefijo "tipo:..." compara directamente contra tipo
        if patron.lower().startswith("tipo:"):
            tipo_pat = patron[5:].strip().lower()
            if tipo_disc.lower() == tipo_pat:
                return True
            continue
        # Otherwise, sub-string match en los campos buscables
        if patron.lower() in buscables:
            return True
    return False


def filtrar_discrepancias_silenciadas(
    db, discrepancias: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Separa discrepancias en (a_notificar, silenciadas).

    Las silenciadas siguen persistiéndose en metadata para auditoría —
    sólo se omiten del mensaje WhatsApp + MessageBox.
    """
    a_notificar: list[dict] = []
    silenciadas: list[dict] = []
    for d in discrepancias or []:
        if discrepancia_silenciada(db, d):
            silenciadas.append(d)
        else:
            a_notificar.append(d)
    return a_notificar, silenciadas


def listar_reglas_legibles(db) -> list[str]:
    """Devuelve las reglas activas como strings legibles (para reportes)."""
    if not db:
        return []
    try:
        reglas = db.listar_memoria_operador(tipo="regla", activa=True)
    except Exception:
        return []
    return [(r.get("descripcion") or r.get("patron") or "").strip()
            for r in reglas]


__all__ = [
    "discrepancia_silenciada",
    "filtrar_discrepancias_silenciadas",
    "listar_reglas_legibles",
]
