"""Tests del módulo runtime_processes (U-02 paso A).

Cubre:
  - Schema (tabla + índices creados).
  - register_process idempotente + supersede de runs anteriores.
  - heartbeat actualiza last_heartbeat_at + cpu/memory.
  - mark_stopped marca dead graceful.
  - run_monitor_pass detecta hanging (heartbeat > 2 min) y dead (PID muerto).
  - Publishers SSE invocados con process_died / process_hanging.

Plan: PLAN_MEJORAS Sprint 1 / U-02 paso A.
"""
from __future__ import annotations
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from src.core.credential_manager import CredentialManager
from src.core.database import Database
from src.utils import runtime_processes as rp


@pytest.fixture(autouse=True)
def _reset_bus():
    from src.utils.event_bus import reset_bus_for_testing
    reset_bus_for_testing()
    yield
    reset_bus_for_testing()


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    monkeypatch.setenv("CATASTRO_BOT_DEV_MODE", "1")
    p = tmp_path / "test.db"
    monkeypatch.setattr("config.settings.DATABASE_PATH", p)
    d = Database(path=p, credentials=CredentialManager())
    d.initialize_schema()
    return p


# ─── Schema ──────────────────────────────────────────────────────────

class TestSchema:
    def test_tabla_creada_e_indices(self, db_path):
        with sqlite3.connect(db_path) as conn:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='runtime_processes'")]
            assert tables == ["runtime_processes"]

            indices = sorted(r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND tbl_name='runtime_processes' AND name NOT LIKE 'sqlite_%'"))
            assert "idx_runtime_processes_name" in indices
            assert "idx_runtime_processes_status" in indices

    def test_check_status_invalido(self, db_path):
        with pytest.raises(sqlite3.IntegrityError):
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    "INSERT INTO runtime_processes "
                    "(process_name, pid, started_at, last_heartbeat_at, status) "
                    "VALUES ('x', 1, '2026-01-01', '2026-01-01', 'INVALID')"
                )
                conn.commit()


# ─── register_process ────────────────────────────────────────────────

class TestRegister:
    def test_inserta_fila_alive(self, db_path):
        rid = rp.register_process(db_path, process_name="scheduler", pid=12345)
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM runtime_processes WHERE id = ?", (rid,),
            ).fetchone()
        assert row["process_name"] == "scheduler"
        assert row["pid"] == 12345
        assert row["status"] == "alive"

    def test_pid_default_es_os_getpid(self, db_path):
        rid = rp.register_process(db_path, process_name="dashboard")
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT pid FROM runtime_processes WHERE id = ?", (rid,),
            ).fetchone()
        assert row["pid"] == os.getpid()

    def test_supersede_runs_anteriores(self, db_path):
        """Si ya hay un row alive del mismo process_name con OTRO pid,
        debe marcarse dead automáticamente (housekeeping)."""
        rp.register_process(db_path, process_name="scheduler", pid=1111)
        rp.register_process(db_path, process_name="scheduler", pid=2222)
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = list(conn.execute(
                "SELECT * FROM runtime_processes WHERE process_name='scheduler' "
                "ORDER BY pid"
            ))
        assert len(rows) == 2
        assert rows[0]["pid"] == 1111 and rows[0]["status"] == "dead"
        assert rows[0]["stop_reason"] == "superseded"
        assert rows[1]["pid"] == 2222 and rows[1]["status"] == "alive"

    def test_idempotente_mismo_pid(self, db_path):
        """Re-registrar con MISMO pid NO crea fila nueva, solo refresca."""
        rid1 = rp.register_process(db_path, process_name="scheduler", pid=999)
        rid2 = rp.register_process(db_path, process_name="scheduler", pid=999)
        assert rid1 == rid2

    def test_log_file_path_se_persiste(self, db_path):
        rid = rp.register_process(
            db_path, process_name="scheduler", pid=999,
            log_file_path="C:/logs/scheduler.log",
        )
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT log_file_path FROM runtime_processes WHERE id = ?",
                (rid,),
            ).fetchone()
        assert row["log_file_path"] == "C:/logs/scheduler.log"


# ─── heartbeat ───────────────────────────────────────────────────────

class TestHeartbeat:
    def test_actualiza_last_heartbeat_at(self, db_path):
        rid = rp.register_process(db_path, process_name="scheduler", pid=999)
        with sqlite3.connect(db_path) as conn:
            antes = conn.execute(
                "SELECT last_heartbeat_at FROM runtime_processes WHERE id = ?",
                (rid,),
            ).fetchone()[0]

        time.sleep(0.05)
        rp.heartbeat(db_path, process_name="scheduler", pid=999)

        with sqlite3.connect(db_path) as conn:
            despues = conn.execute(
                "SELECT last_heartbeat_at FROM runtime_processes WHERE id = ?",
                (rid,),
            ).fetchone()[0]
        assert despues > antes


# ─── mark_stopped ────────────────────────────────────────────────────

class TestMarkStopped:
    def test_marca_dead_graceful(self, db_path):
        rid = rp.register_process(db_path, process_name="scheduler", pid=999)
        rp.mark_stopped(db_path, process_name="scheduler", pid=999, reason="graceful")
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT status, stop_reason, stopped_at FROM runtime_processes "
                "WHERE id = ?", (rid,),
            ).fetchone()
        assert row["status"] == "dead"
        assert row["stop_reason"] == "graceful"
        assert row["stopped_at"] is not None


# ─── list_alive / list_all_recent ────────────────────────────────────

class TestListing:
    def test_list_alive_excluye_dead(self, db_path):
        rp.register_process(db_path, process_name="scheduler", pid=1)
        rp.register_process(db_path, process_name="dashboard", pid=2)
        rp.mark_stopped(db_path, process_name="dashboard", pid=2)

        alive = rp.list_alive(db_path)
        nombres = [r["process_name"] for r in alive]
        assert "scheduler" in nombres
        assert "dashboard" not in nombres

    def test_list_all_recent_incluye_dead(self, db_path):
        rp.register_process(db_path, process_name="scheduler", pid=1)
        rp.register_process(db_path, process_name="dashboard", pid=2)
        rp.mark_stopped(db_path, process_name="dashboard", pid=2)

        all_recent = rp.list_all_recent(db_path)
        nombres = [r["process_name"] for r in all_recent]
        assert "scheduler" in nombres
        assert "dashboard" in nombres


# ─── run_monitor_pass ────────────────────────────────────────────────

class TestMonitor:
    def test_pid_inexistente_marcado_dead(self, db_path):
        # PID muy alto y específico que casi seguro NO existe
        rp.register_process(db_path, process_name="scheduler", pid=999999999)
        counts = rp.run_monitor_pass(db_path)
        assert counts["dead"] >= 1

        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT status, stop_reason FROM runtime_processes "
                "WHERE pid = 999999999"
            ).fetchone()
        assert row["status"] == "dead"
        assert row["stop_reason"] == "dead_pid"

    def test_heartbeat_viejo_marcado_hanging(self, db_path):
        # Insertar manualmente con heartbeat de hace 5 min
        viejo = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                """INSERT INTO runtime_processes
                   (process_name, pid, started_at, last_heartbeat_at, status)
                   VALUES ('test_hanging', ?, ?, ?, 'alive')""",
                (os.getpid(), viejo, viejo),
            )
            conn.commit()

        counts = rp.run_monitor_pass(db_path)
        assert counts["hanging"] >= 1

        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT status FROM runtime_processes WHERE process_name='test_hanging'"
            ).fetchone()
        assert row["status"] == "hanging"

    def test_proceso_actual_sigue_alive(self, db_path):
        """Un proceso con PID válido y heartbeat reciente queda alive."""
        rp.register_process(db_path, process_name="scheduler", pid=os.getpid())
        rp.heartbeat(db_path, process_name="scheduler", pid=os.getpid())
        counts = rp.run_monitor_pass(db_path)
        assert counts["alive"] >= 1

    def test_monitor_publica_evento_process_died(self, db_path):
        from src.utils.event_bus import get_bus
        import threading

        bus = get_bus()
        received = []
        ready = threading.Event()

        def consumer():
            for ev in bus.subscribe():
                if not ready.is_set():
                    ready.set()
                received.append(ev)
                if any(e.get("type") == "process_died" for e in received):
                    break

        t = threading.Thread(target=consumer, daemon=True)
        t.start()
        bus.publish({"type": "kickstart"})
        ready.wait(timeout=2.0)

        rp.register_process(db_path, process_name="zombie", pid=999999999)
        rp.run_monitor_pass(db_path)

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if any(e.get("type") == "process_died" for e in received):
                break
            time.sleep(0.02)
        t.join(timeout=2.0)

        ev_died = [e for e in received if e.get("type") == "process_died"]
        assert len(ev_died) >= 1
        assert ev_died[0]["process_name"] == "zombie"
