"""Estado de servicios externos del bot (Sprint 4 / N-03).

Maneja la tabla `external_services_health` con flags persistentes para
cada servicio que el bot consume:
  - green_api  (WhatsApp)
  - anthropic  (Vision, minutas)
  - drive      (Drive OAuth)
  - rnp        (Registro Nacional)
  - cfia_apt   (Portal APT — opcional, CDP no es HTTP REST estricto)

Cada uno tiene status: 'up' | 'down' | 'degraded'.

Publishers SSE: cada cambio de estado emite 'external_service_changed'.
Audit log: cada cambio crea entry inmutable para trazabilidad.

Plan: PLAN_MEJORAS Sprint 4 / N-03.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("catastro.external_services")


KNOWN_SERVICES = (
    "green_api",
    "anthropic",
    "drive",
    "rnp",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _open(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_row(conn: sqlite3.Connection, service: str) -> None:
    if conn.execute(
        "SELECT 1 FROM external_services_health WHERE service_name = ?",
        (service,),
    ).fetchone():
        return
    conn.execute(
        """INSERT INTO external_services_health
           (service_name, status, last_check_at) VALUES (?, 'up', ?)""",
        (service, _now_iso()),
    )


def read_status(db_path: Path, service: str) -> dict:
    """Devuelve el snapshot del servicio. Auto-inicializa como 'up'."""
    with _open(db_path) as conn:
        _ensure_row(conn, service)
        conn.commit()
        row = conn.execute(
            "SELECT * FROM external_services_health WHERE service_name = ?",
            (service,),
        ).fetchone()
    return dict(row)


def read_all(db_path: Path) -> list[dict]:
    with _open(db_path) as conn:
        for s in KNOWN_SERVICES:
            _ensure_row(conn, s)
        conn.commit()
        rows = conn.execute(
            "SELECT * FROM external_services_health ORDER BY service_name"
        ).fetchall()
    return [dict(r) for r in rows]


def mark_down(
    db_path: Path,
    service: str,
    *,
    error_code: str,
    error_message: str = "",
) -> dict:
    """Marca un servicio como 'down'. Incrementa consecutive_failures.
    Si ya estaba down con el MISMO error_code, NO emite evento (evita
    spam) — solo actualiza last_check_at y el contador.
    """
    now = _now_iso()
    emit_event = False
    with _open(db_path) as conn:
        _ensure_row(conn, service)
        prev = conn.execute(
            "SELECT status, last_error_code FROM external_services_health "
            "WHERE service_name = ?", (service,),
        ).fetchone()
        was_up = prev["status"] == "up"
        same_error = (
            prev["status"] == "down"
            and prev["last_error_code"] == error_code
        )
        emit_event = was_up or not same_error

        conn.execute(
            """UPDATE external_services_health
                  SET status = 'down',
                      last_check_at = ?,
                      last_down_at = COALESCE(?, last_down_at),
                      last_error_code = ?,
                      last_error_message = ?,
                      consecutive_failures = consecutive_failures + 1
                WHERE service_name = ?""",
            (now, now if was_up else None, error_code,
             error_message[:500] if error_message else "", service),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM external_services_health WHERE service_name = ?",
            (service,),
        ).fetchone()

    result = dict(row)

    if emit_event:
        log.warning(
            "[external_service] %s → DOWN (%s) %s",
            service, error_code, error_message[:80],
        )
        try:
            from src.utils.event_bus import publish
            publish({
                "type": "external_service_changed",
                "service": service,
                "status": "down",
                "error_code": error_code,
                "error_message": error_message[:500],
            })
        except Exception:
            pass

    return result


def mark_up(db_path: Path, service: str, *, reason: str = "healthcheck OK") -> dict:
    """Marca el servicio como 'up'. Resetea consecutive_failures.
    Si ya estaba up, no emite evento."""
    now = _now_iso()
    emit_event = False
    with _open(db_path) as conn:
        _ensure_row(conn, service)
        prev = conn.execute(
            "SELECT status FROM external_services_health WHERE service_name = ?",
            (service,),
        ).fetchone()
        was_down = prev["status"] in ("down", "degraded")
        emit_event = was_down

        conn.execute(
            """UPDATE external_services_health
                  SET status = 'up',
                      last_check_at = ?,
                      last_up_at = ?,
                      last_error_code = NULL,
                      last_error_message = NULL,
                      consecutive_failures = 0
                WHERE service_name = ?""",
            (now, now, service),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM external_services_health WHERE service_name = ?",
            (service,),
        ).fetchone()

    result = dict(row)

    if emit_event:
        log.info("[external_service] %s → UP (%s)", service, reason)
        try:
            from src.utils.event_bus import publish
            publish({
                "type": "external_service_changed",
                "service": service,
                "status": "up",
                "reason": reason,
            })
        except Exception:
            pass

    return result


def is_down(db_path: Path, service: str) -> bool:
    """Helper rápido para checks en callers."""
    try:
        return read_status(db_path, service).get("status") == "down"
    except Exception:
        return False


__all__ = [
    "KNOWN_SERVICES",
    "read_status",
    "read_all",
    "mark_down",
    "mark_up",
    "is_down",
]
