"""Métricas operativas del bot — para dashboard + alertas proactivas.

Lee de la BD (tabla `expedientes` y `metadata_json`) y produce números
agregados. Funciones puras — no envía notificaciones ni muta nada.

USO:
    from src.utils.metricas import resumen_completo
    print(resumen_completo(db))
"""
from __future__ import annotations
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Optional


def _meta(exp: dict) -> dict:
    raw = exp.get("metadata_json") if isinstance(exp, dict) else None
    if isinstance(raw, str):
        try:
            return json.loads(raw or "{}") or {}
        except Exception:
            return {}
    if isinstance(raw, dict):
        return raw
    return {}


def _parse_ts(s: Any) -> Optional[datetime]:
    if not s:
        return None
    if isinstance(s, datetime):
        return s
    try:
        # Soporta '2026-05-11T12:24:02' y '2026-05-11T12:24:02+00:00'
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _todos_expedientes(db) -> list[dict]:
    if not db:
        return []
    try:
        return db.listar_expedientes()
    except Exception:
        return []


# ── Métricas individuales ─────────────────────────────────────────────

def resumen_por_estado(db) -> dict[str, int]:
    """Cuenta expedientes por `estado_actual`."""
    c: Counter = Counter()
    for exp in _todos_expedientes(db):
        if exp.get("cancelado"):
            c["__cancelado"] += 1
            continue
        c[exp.get("estado_actual", "?")] += 1
    return dict(c)


def resumen_por_tipo(db) -> dict[str, int]:
    """Cuenta expedientes por `tipo_plano`."""
    c: Counter = Counter()
    for exp in _todos_expedientes(db):
        if exp.get("cancelado"):
            continue
        c[exp.get("tipo_plano", "?")] += 1
    return dict(c)


def resumen_por_topografo(db) -> dict[str, int]:
    """Cuenta expedientes por `nombre_topografo`."""
    c: Counter = Counter()
    for exp in _todos_expedientes(db):
        if exp.get("cancelado"):
            continue
        c[exp.get("nombre_topografo", "?")] += 1
    return dict(c)


def tiempo_promedio_ciclo(
    db,
    *,
    desde_campo: str = "fecha_creacion",
    hasta_meta_key: str = "apt_envio_fecha",
) -> Optional[float]:
    """Días promedio entre `desde_campo` (expediente) y `metadata[hasta_meta_key]`.

    Default mide días de "creación BD → envío al CFIA".
    Devuelve None si no hay datos suficientes.
    """
    diffs: list[float] = []
    for exp in _todos_expedientes(db):
        if exp.get("cancelado"):
            continue
        a = _parse_ts(exp.get(desde_campo))
        meta = _meta(exp)
        b = _parse_ts(meta.get(hasta_meta_key))
        if a and b and b > a:
            diff = (b - a).total_seconds() / 86400.0
            diffs.append(diff)
    if not diffs:
        return None
    return sum(diffs) / len(diffs)


def ratio_exoneracion_honorarios(db) -> Optional[float]:
    """% de expedientes con `metadata.datos_apt.general.honorarios = "0"`."""
    total = 0
    con_exoneracion = 0
    for exp in _todos_expedientes(db):
        if exp.get("cancelado"):
            continue
        general = (_meta(exp).get("datos_apt") or {}).get("general") or {}
        honorarios_raw = str(general.get("honorarios", "")).strip()
        if not honorarios_raw:
            continue
        total += 1
        try:
            h = int(float(honorarios_raw))
        except ValueError:
            continue
        if h == 0:
            con_exoneracion += 1
    if total == 0:
        return None
    return 100.0 * con_exoneracion / total


def discrepancias_frecuentes(db, top: int = 10) -> list[tuple[str, int]]:
    """Top N tipos de discrepancia, contadas a través de todos los expedientes."""
    c: Counter = Counter()
    for exp in _todos_expedientes(db):
        discs = _meta(exp).get("apt_discrepancias_rnp") or []
        for d in discs:
            tipo = (d.get("tipo") or "desconocido") if isinstance(d, dict) else "desconocido"
            c[tipo] += 1
    return c.most_common(top)


def anomalias_recientes(db, limit: int = 10) -> list[dict]:
    """Últimas anomalías reportadas en cualquier expediente.

    Cada anomalía tiene `ts`, `contexto`, `descripcion`, y se le adjunta el
    `numero_expediente` y `display` legible.
    """
    from src.utils.display import display_proyecto
    eventos = []
    for exp in _todos_expedientes(db):
        for a in (_meta(exp).get("apt_anomalias") or []):
            if not isinstance(a, dict):
                continue
            a2 = dict(a)
            a2["numero_expediente"] = exp.get("numero_expediente", "?")
            a2["display"] = display_proyecto(exp)
            eventos.append(a2)
    # Ordenar descendente por ts
    eventos.sort(key=lambda e: e.get("ts", ""), reverse=True)
    return eventos[:limit]


def alertas_proactivas(db, *, umbral_anomalias_mismo_contexto: int = 3) -> list[str]:
    """Detecta patrones que sugieren un cambio en APT.

    Si ≥ N expedientes recientes fallaron con la MISMA descripción de
    anomalía, es probable que APT cambió un selector / flujo.

    Devuelve lista de alertas legibles.
    """
    contextos: Counter = Counter()
    descripciones: Counter = Counter()
    for exp in _todos_expedientes(db):
        for a in (_meta(exp).get("apt_anomalias") or []):
            if isinstance(a, dict):
                if a.get("contexto"):
                    contextos[a["contexto"]] += 1
                if a.get("descripcion"):
                    descripciones[a["descripcion"][:80]] += 1

    alertas: list[str] = []
    for ctx, n in contextos.most_common(5):
        if n >= umbral_anomalias_mismo_contexto:
            alertas.append(
                f"⚠️ {n} anomalías en '{ctx}' — posible cambio en APT, revisar el helper de esa sección."
            )
    for desc, n in descripciones.most_common(5):
        if n >= umbral_anomalias_mismo_contexto:
            alertas.append(
                f"⚠️ {n} veces la misma descripción: '{desc[:60]}...'"
            )
    return alertas


def por_estado_apt(db) -> dict[str, int]:
    """Cuenta expedientes por `metadata.apt_estado`."""
    c: Counter = Counter()
    for exp in _todos_expedientes(db):
        if exp.get("cancelado"):
            continue
        meta = _meta(exp)
        estado = meta.get("apt_estado")
        if estado:
            c[estado] += 1
        elif meta.get("apt_tramite"):
            c["(con trámite, sin estado)"] += 1
        else:
            c["(sin contrato APT)"] += 1
    return dict(c)


# ── Resumen completo ─────────────────────────────────────────────────

def resumen_completo(db) -> dict:
    """Devuelve un dict con todas las métricas — para dashboard o JSON export."""
    return {
        "total_expedientes": sum(resumen_por_estado(db).values()),
        "por_estado":        resumen_por_estado(db),
        "por_tipo":          resumen_por_tipo(db),
        "por_topografo":     resumen_por_topografo(db),
        "por_estado_apt":    por_estado_apt(db),
        "tiempo_promedio_creacion_a_envio_dias": tiempo_promedio_ciclo(db),
        "ratio_exoneracion_pct": ratio_exoneracion_honorarios(db),
        "discrepancias_frecuentes": discrepancias_frecuentes(db),
        "anomalias_recientes":      anomalias_recientes(db, limit=5),
        "alertas_proactivas":       alertas_proactivas(db),
    }


__all__ = [
    "resumen_por_estado", "resumen_por_tipo", "resumen_por_topografo",
    "tiempo_promedio_ciclo", "ratio_exoneracion_honorarios",
    "discrepancias_frecuentes", "anomalias_recientes",
    "alertas_proactivas", "por_estado_apt", "resumen_completo",
]
