"""Tracking de costos de API Anthropic (Sprint 4 / O-08).

Helpers para:
  - Calcular costo USD de una respuesta Anthropic dado el modelo + usage.
  - Registrar una llamada en la tabla api_costs.
  - Wrapper `track_anthropic_call(...)` que ejecuta la llamada + persiste
    métricas + publica evento SSE.
  - Reads agregados para el panel /config/costos.
  - Budget check + alerta a 80%.

Tarifas (USD por 1M tokens) — actualizadas 2026-05-22 desde
https://docs.anthropic.com/en/docs/about-claude/pricing.
Si cambian, actualizar el dict + agregar regla en apt_memoria_operador.

Plan: PLAN_MEJORAS Sprint 4 / O-08.
"""
from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

log = logging.getLogger("catastro.api_costs")


# ── Tarifas (USD por 1M tokens, retail) ────────────────────────────────
# Source: docs.anthropic.com/en/docs/about-claude/pricing  (2026-05-22).
# Si un modelo no está listado, se usa _TARIFA_DESCONOCIDA — el cálculo
# queda en 0 pero la llamada igual se registra para que sea visible.
_PRICING_USD_PER_MTOK: dict[str, dict[str, float]] = {
    # Claude 4.x family (asumido similar a 3.5 hasta nueva versión)
    "claude-sonnet-4-20250514":    {"in": 3.00, "out": 15.00,
                                    "cache_read": 0.30, "cache_write": 3.75},
    "claude-opus-4-20250514":      {"in": 15.00, "out": 75.00,
                                    "cache_read": 1.50, "cache_write": 18.75},
    # Claude 3.5
    "claude-3-5-sonnet-20241022":  {"in": 3.00, "out": 15.00,
                                    "cache_read": 0.30, "cache_write": 3.75},
    "claude-3-5-sonnet-20240620":  {"in": 3.00, "out": 15.00,
                                    "cache_read": 0.30, "cache_write": 3.75},
    "claude-3-5-haiku-20241022":   {"in": 0.80, "out": 4.00,
                                    "cache_read": 0.08, "cache_write": 1.00},
    # Claude 3 (legacy)
    "claude-3-opus-20240229":      {"in": 15.00, "out": 75.00,
                                    "cache_read": 1.50, "cache_write": 18.75},
    "claude-3-sonnet-20240229":    {"in": 3.00, "out": 15.00,
                                    "cache_read": 0.30, "cache_write": 3.75},
    "claude-3-haiku-20240307":     {"in": 0.25, "out": 1.25,
                                    "cache_read": 0.03, "cache_write": 0.30},
}

# Default conservador si el modelo no está mapeado.
_TARIFA_DESCONOCIDA = {"in": 3.00, "out": 15.00,
                        "cache_read": 0.30, "cache_write": 3.75}


def calcular_costo_usd(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> float:
    """Calcula el costo USD de una llamada en base a la tarifa del modelo.

    Maneja matching laxo: si `model` no matchea exacto, intenta prefix
    match (ej. 'claude-3-5-sonnet-20240620' → busca el más cercano).
    """
    tariff = _PRICING_USD_PER_MTOK.get(model)
    if tariff is None:
        # Prefix match: tomar la primera entrada cuya key sea prefijo de model
        for known, t in _PRICING_USD_PER_MTOK.items():
            if model.startswith(known.rsplit("-", 1)[0]):
                tariff = t
                break
    if tariff is None:
        log.warning("api_costs: modelo no reconocido %r — usando tarifa default", model)
        tariff = _TARIFA_DESCONOCIDA

    cost = (
        input_tokens          * tariff["in"]          / 1_000_000
        + output_tokens       * tariff["out"]         / 1_000_000
        + cache_read_tokens   * tariff["cache_read"]  / 1_000_000
        + cache_creation_tokens * tariff["cache_write"] / 1_000_000
    )
    return round(cost, 6)


# ── Persistencia ───────────────────────────────────────────────────────

def record_call(
    db_path: Path,
    *,
    provider: str = "anthropic",
    model: str,
    tipo: str,
    expediente_id: Optional[str] = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    duration_ms: Optional[int] = None,
) -> dict:
    """Inserta una fila en api_costs. Calcula cost_usd internamente.

    Returns:
        dict con la fila persistida + costo calculado.
    """
    cost = calcular_costo_usd(
        model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
    )
    ts = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            """INSERT INTO api_costs
               (ts, provider, model, tipo, expediente_id,
                input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
                cost_usd, duration_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ts, provider, model, tipo, expediente_id,
             input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
             cost, duration_ms),
        )
        row_id = cur.lastrowid
        conn.commit()

    result = {
        "id": row_id, "ts": ts, "provider": provider, "model": model,
        "tipo": tipo, "expediente_id": expediente_id,
        "input_tokens": input_tokens, "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_creation_tokens": cache_creation_tokens,
        "cost_usd": cost, "duration_ms": duration_ms,
    }
    log.info(
        "[api_costs] %s/%s tipo=%s exp=%s in=%d out=%d cache_r=%d cache_w=%d "
        "= $%.4f USD (%dms)",
        provider, model.rsplit("-", 1)[0] if "-" in model else model,
        tipo, expediente_id, input_tokens, output_tokens,
        cache_read_tokens, cache_creation_tokens, cost, duration_ms or 0,
    )

    # Publish SSE event (best-effort)
    try:
        from src.utils.event_bus import publish
        publish({
            "type": "api_cost_recorded",
            "provider": provider,
            "model": model,
            "tipo": tipo,
            "cost_usd": cost,
            "expediente_id": expediente_id,
        })
    except Exception:
        pass

    return result


# ── Wrapper para envolver llamadas existentes ──────────────────────────

def track_anthropic_call(
    fn: Callable[[], Any],
    *,
    db_path: Path,
    model: str,
    tipo: str,
    expediente_id: Optional[str] = None,
) -> Any:
    """Ejecuta `fn()` (que llama a Anthropic) y registra el costo.

    Si la respuesta tiene .usage (estilo anthropic SDK), captura los tokens.
    Si la llamada falla, NO registra costo y propaga la excepción.

    Returns:
        Lo que `fn()` devuelva. Sin modificar.
    """
    t0 = time.monotonic()
    resp = fn()
    duration_ms = int((time.monotonic() - t0) * 1000)

    usage = getattr(resp, "usage", None)
    if usage is None:
        log.warning(
            "track_anthropic_call: response sin .usage — no se registra costo "
            "(model=%s tipo=%s)", model, tipo,
        )
        return resp

    try:
        record_call(
            db_path,
            model=model, tipo=tipo, expediente_id=expediente_id,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_creation_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            duration_ms=duration_ms,
        )
    except Exception:
        log.exception("track_anthropic_call: no se pudo registrar costo")

    return resp


# ── Reads para el panel ────────────────────────────────────────────────

def read_monthly_summary(db_path: Path) -> list[dict]:
    """Snapshot de v_api_costs_mensual."""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM v_api_costs_mensual").fetchall()
    return [dict(r) for r in rows]


def read_daily_summary(db_path: Path) -> list[dict]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM v_api_costs_diario").fetchall()
    return [dict(r) for r in rows]


def read_recent_calls(db_path: Path, limit: int = 50) -> list[dict]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, ts, provider, model, tipo, expediente_id, "
            "       input_tokens, output_tokens, cost_usd, duration_ms "
            "FROM api_costs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def read_by_expediente(db_path: Path, limit: int = 50) -> list[dict]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM v_api_costs_por_expediente LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Budget ─────────────────────────────────────────────────────────────

def read_budget(db_path: Path) -> dict:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM api_budget WHERE id = 1").fetchone()
    return dict(row) if row else {}


def set_budget(db_path: Path, *, monthly_usd: float,
               alert_threshold: float = 0.80) -> dict:
    if monthly_usd < 0:
        raise ValueError("monthly_usd debe ser >= 0")
    if not 0 < alert_threshold <= 1.5:
        raise ValueError("alert_threshold debe estar en (0, 1.5]")
    ts = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO api_budget (id, monthly_usd, alert_threshold, updated_at)
               VALUES (1, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   monthly_usd = excluded.monthly_usd,
                   alert_threshold = excluded.alert_threshold,
                   updated_at = excluded.updated_at""",
            (monthly_usd, alert_threshold, ts),
        )
        conn.commit()
    return read_budget(db_path)


def current_month_spend(db_path: Path) -> float:
    mes = datetime.now(timezone.utc).strftime("%Y-%m")
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM api_costs "
            "WHERE substr(ts, 1, 7) = ?",
            (mes,),
        ).fetchone()
    return float(row[0]) if row else 0.0


def check_budget_alert(db_path: Path) -> Optional[dict]:
    """Verifica si superamos el threshold del budget. Devuelve dict con
    info para alertar, o None si no aplica.

    Idempotente por mes: si ya se alertó este mes, devuelve None
    (last_alert_mes evita spam).
    """
    budget = read_budget(db_path)
    if not budget or not budget.get("monthly_usd"):
        return None
    spend = current_month_spend(db_path)
    threshold_usd = float(budget["monthly_usd"]) * float(budget["alert_threshold"])
    mes = datetime.now(timezone.utc).strftime("%Y-%m")

    if spend < threshold_usd:
        return None
    if budget.get("last_alert_mes") == mes:
        return None  # ya alertamos este mes

    # Registrar la alerta como emitida
    ts = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE api_budget SET last_alert_at = ?, last_alert_mes = ? "
            "WHERE id = 1",
            (ts, mes),
        )
        conn.commit()

    return {
        "mes": mes,
        "spend_usd": round(spend, 4),
        "budget_usd": float(budget["monthly_usd"]),
        "threshold_pct": float(budget["alert_threshold"]) * 100,
        "ratio": round(spend / float(budget["monthly_usd"]), 3),
    }


__all__ = [
    "calcular_costo_usd",
    "record_call",
    "track_anthropic_call",
    "read_monthly_summary",
    "read_daily_summary",
    "read_recent_calls",
    "read_by_expediente",
    "read_budget",
    "set_budget",
    "current_month_spend",
    "check_budget_alert",
]
