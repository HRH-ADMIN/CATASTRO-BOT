"""Tracking de procesos vivos del bot (U-02 paso A).

Cada componente que corre como proceso separado (scheduler `src.main`,
dashboard Flask, watchdog Chrome) llama `register_process(...)` al
arrancar y `heartbeat()` periódicamente. El job
`process-monitor` del scheduler lee la tabla cada 30s y marca como
`hanging` los procesos sin heartbeat reciente, o `dead` cuando el PID
del SO ya no existe.

Mientras `module_state` (U-03) captura la INTENCIÓN del operador,
esta capa captura la REALIDAD del SO.

Plan: PLAN_MEJORAS Sprint 1 / U-02 paso A.
Documentado en docs/SCHEMA.md (sección agregada en este paso).
"""
from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("catastro.runtime_processes")


# ── Constantes ─────────────────────────────────────────────────────────

# Si un proceso no hace heartbeat en este tiempo, se marca 'hanging'.
HEARTBEAT_STALE_SECONDS = 120

# Procesos esperados en una corrida normal del bot.
KNOWN_PROCESS_NAMES = (
    "scheduler",   # python -m src.main
    "dashboard",   # Flask + Waitress (también vive dentro de src.main)
    "watchdog",    # python -m src.utils.healthcheck
    "chrome_bot",  # tools/start_chrome_bot.py
)


# ── Helpers ────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _open(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _try_get_resource_usage(pid: int) -> tuple[Optional[float], Optional[float]]:
    """Best-effort: devuelve (cpu_percent, memory_mb) usando psutil si está
    disponible. Si psutil no está instalado o el PID no existe, retorna
    (None, None) — el caller registra los datos como NULL en la tabla."""
    try:
        import psutil  # type: ignore[import-untyped]
    except ImportError:
        return (None, None)
    try:
        p = psutil.Process(pid)
        cpu = p.cpu_percent(interval=None)  # non-blocking; primer call retorna 0
        mem = p.memory_info().rss / (1024 * 1024)
        return (cpu, mem)
    except Exception:
        return (None, None)


def _pid_exists(pid: int) -> bool:
    """¿El PID sigue existiendo en el SO? Usa psutil si está, sino
    cae a una verificación cross-platform mínima."""
    try:
        import psutil  # type: ignore[import-untyped]
        return psutil.pid_exists(pid)
    except ImportError:
        pass
    # Fallback: signal 0 en Unix, OpenProcess en Windows.
    if os.name == "nt":
        try:
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, 0, pid,
            )
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return True  # no podemos verificar — asumir vivo (conservador)
    else:
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            return False
        except Exception:
            return True


# ── API pública ────────────────────────────────────────────────────────

def register_process(
    db_path: Path,
    *,
    process_name: str,
    pid: Optional[int] = None,
    log_file_path: Optional[str] = None,
) -> int:
    """Registra un proceso vivo. Si ya existe un row alive con el mismo
    process_name + pid, lo actualiza (idempotente para re-arranques
    rápidos). Devuelve el id de la fila.

    Antes de insertar, marca como 'dead' cualquier fila previa con el
    mismo process_name que tenga status='alive' pero pid distinto
    (housekeeping de runs anteriores cuyo shutdown no se registró).
    """
    pid = pid if pid is not None else os.getpid()
    now = _now_iso()
    with _open(db_path) as conn:
        # Housekeeping de filas viejas con otro PID.
        conn.execute(
            """UPDATE runtime_processes
                  SET status = 'dead', stopped_at = ?, stop_reason = 'superseded'
                WHERE process_name = ? AND status = 'alive' AND pid <> ?""",
            (now, process_name, pid),
        )
        # Insert o update si ya existe con mismo pid (caso replay).
        existing = conn.execute(
            "SELECT id FROM runtime_processes "
            "WHERE process_name = ? AND pid = ? AND status = 'alive'",
            (process_name, pid),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE runtime_processes "
                "SET last_heartbeat_at = ?, log_file_path = COALESCE(?, log_file_path) "
                "WHERE id = ?",
                (now, log_file_path, existing["id"]),
            )
            row_id = existing["id"]
        else:
            cur = conn.execute(
                """INSERT INTO runtime_processes
                   (process_name, pid, started_at, last_heartbeat_at, status,
                    log_file_path)
                   VALUES (?, ?, ?, ?, 'alive', ?)""",
                (process_name, pid, now, now, log_file_path),
            )
            row_id = cur.lastrowid
        conn.commit()
    log.info("[runtime] registrado: %s pid=%d row=%d", process_name, pid, row_id)
    return row_id


def heartbeat(db_path: Path, *, process_name: str, pid: Optional[int] = None) -> None:
    """Actualiza last_heartbeat_at + cpu_percent + memory_mb. NO crea fila
    si no existe (caller debió llamar register_process antes)."""
    pid = pid if pid is not None else os.getpid()
    cpu, mem = _try_get_resource_usage(pid)
    with _open(db_path) as conn:
        conn.execute(
            """UPDATE runtime_processes
                  SET last_heartbeat_at = ?, cpu_percent = ?, memory_mb = ?
                WHERE process_name = ? AND pid = ? AND status = 'alive'""",
            (_now_iso(), cpu, mem, process_name, pid),
        )
        conn.commit()


def mark_stopped(
    db_path: Path,
    *,
    process_name: str,
    pid: Optional[int] = None,
    reason: str = "graceful",
) -> None:
    """Llamar en el shutdown handler. Marca el row como 'dead' graceful."""
    pid = pid if pid is not None else os.getpid()
    with _open(db_path) as conn:
        conn.execute(
            """UPDATE runtime_processes
                  SET status = 'dead', stopped_at = ?, stop_reason = ?
                WHERE process_name = ? AND pid = ? AND status = 'alive'""",
            (_now_iso(), reason, process_name, pid),
        )
        conn.commit()
    log.info("[runtime] mark_stopped: %s pid=%d (%s)", process_name, pid, reason)


def list_alive(db_path: Path) -> list[dict]:
    """Lista de procesos actualmente alive o hanging (no dead)."""
    with _open(db_path) as conn:
        rows = conn.execute(
            """SELECT * FROM runtime_processes
                WHERE status IN ('alive', 'hanging')
                ORDER BY process_name, started_at DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


def list_all_recent(db_path: Path, *, limit: int = 50) -> list[dict]:
    """Para el panel: incluye dead/hanging recientes para historial."""
    with _open(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM runtime_processes "
            "ORDER BY COALESCE(stopped_at, last_heartbeat_at) DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── Job del scheduler ──────────────────────────────────────────────────

def run_monitor_pass(db_path: Path) -> dict:
    """Recorre runtime_processes con status=alive y detecta:
      - PID que ya no existe en el SO → status='dead', reason='dead_pid'.
      - Heartbeat > HEARTBEAT_STALE_SECONDS → status='hanging'.

    Returns:
        dict con conteos {hanging, dead, alive} para logging/observability.
    """
    deadline_iso = (
        datetime.now(timezone.utc) - timedelta(seconds=HEARTBEAT_STALE_SECONDS)
    ).isoformat()
    now = _now_iso()

    counts = {"alive": 0, "hanging": 0, "dead": 0}
    eventos: list[dict] = []  # para publicar SSE

    with _open(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM runtime_processes WHERE status = 'alive'"
        ).fetchall()

        for r in rows:
            pid = r["pid"]
            name = r["process_name"]

            if not _pid_exists(pid):
                conn.execute(
                    """UPDATE runtime_processes
                          SET status = 'dead', stopped_at = ?,
                              stop_reason = 'dead_pid'
                        WHERE id = ?""",
                    (now, r["id"]),
                )
                counts["dead"] += 1
                eventos.append({
                    "type": "process_died",
                    "process_name": name,
                    "pid": pid,
                    "reason": "dead_pid",
                })
                log.warning(
                    "[runtime] proceso muerto: %s pid=%d (PID no existe)",
                    name, pid,
                )
                continue

            if r["last_heartbeat_at"] < deadline_iso:
                conn.execute(
                    "UPDATE runtime_processes SET status = 'hanging' WHERE id = ?",
                    (r["id"],),
                )
                counts["hanging"] += 1
                eventos.append({
                    "type": "process_hanging",
                    "process_name": name,
                    "pid": pid,
                    "last_heartbeat_at": r["last_heartbeat_at"],
                })
                log.warning(
                    "[runtime] proceso colgado: %s pid=%d (último heartbeat %s)",
                    name, pid, r["last_heartbeat_at"],
                )
                continue

            counts["alive"] += 1
        conn.commit()

    # Publish eventos SSE (best-effort)
    if eventos:
        try:
            from src.utils.event_bus import publish
            for ev in eventos:
                publish(ev)
        except Exception:
            pass

    return counts


__all__ = [
    "register_process",
    "heartbeat",
    "mark_stopped",
    "list_alive",
    "list_all_recent",
    "run_monitor_pass",
    "KNOWN_PROCESS_NAMES",
    "HEARTBEAT_STALE_SECONDS",
]
