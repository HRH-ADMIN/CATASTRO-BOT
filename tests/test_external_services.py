"""Tests del módulo external_services (Sprint 4 / N-03).

Cubre el flujo completo:
  - Schema (tabla + CHECK constraint).
  - mark_down/mark_up con dedup (no spam si mismo error_code).
  - Eventos SSE emitidos en cambios reales (no en repeticiones).
  - Helper is_down.

Plan: PLAN_MEJORAS Sprint 4 / N-03.
"""
from __future__ import annotations
import sqlite3
import threading
import time

import pytest

from src.core.credential_manager import CredentialManager
from src.core.database import Database
from src.utils import external_services as es


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
    def test_tabla_creada(self, db_path):
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='external_services_health'"
            ).fetchone()
        assert row is not None

    def test_check_status_invalido(self, db_path):
        with pytest.raises(sqlite3.IntegrityError):
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    "INSERT INTO external_services_health "
                    "(service_name, status) VALUES ('x', 'INVALID')"
                )
                conn.commit()


# ─── read_status / read_all ──────────────────────────────────────────

class TestRead:
    def test_read_status_auto_inicializa_up(self, db_path):
        st = es.read_status(db_path, "green_api")
        assert st["status"] == "up"
        assert st["consecutive_failures"] == 0

    def test_read_all_incluye_known_services(self, db_path):
        rows = es.read_all(db_path)
        names = {r["service_name"] for r in rows}
        for s in es.KNOWN_SERVICES:
            assert s in names

    def test_is_down_default_false(self, db_path):
        assert es.is_down(db_path, "green_api") is False


# ─── mark_down ───────────────────────────────────────────────────────

class TestMarkDown:
    def test_mark_down_setea_estado(self, db_path):
        r = es.mark_down(db_path, "green_api",
                         error_code="466", error_message="auth expired")
        assert r["status"] == "down"
        assert r["last_error_code"] == "466"
        assert r["consecutive_failures"] == 1
        assert es.is_down(db_path, "green_api") is True

    def test_mark_down_incrementa_consecutive_failures(self, db_path):
        es.mark_down(db_path, "green_api", error_code="466")
        es.mark_down(db_path, "green_api", error_code="466")
        es.mark_down(db_path, "green_api", error_code="466")
        st = es.read_status(db_path, "green_api")
        assert st["consecutive_failures"] == 3

    def test_mark_down_distinto_error_emite_evento(self, db_path):
        """Si el error_code CAMBIA aunque siga down, sí publica evento."""
        from src.utils.event_bus import get_bus
        bus = get_bus()
        received = []
        ready = threading.Event()

        def consumer():
            for ev in bus.subscribe():
                if not ready.is_set():
                    ready.set()
                received.append(ev)
                if len([e for e in received if e.get("type") == "external_service_changed"]) >= 2:
                    break

        t = threading.Thread(target=consumer, daemon=True)
        t.start()
        bus.publish({"type": "kickstart"})
        ready.wait(timeout=2.0)

        es.mark_down(db_path, "green_api", error_code="466")
        es.mark_down(db_path, "green_api", error_code="500")  # cambia código

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            evs = [e for e in received if e.get("type") == "external_service_changed"]
            if len(evs) >= 2:
                break
            time.sleep(0.02)
        t.join(timeout=2.0)

        evs = [e for e in received if e.get("type") == "external_service_changed"]
        assert len(evs) == 2
        assert evs[0]["error_code"] == "466"
        assert evs[1]["error_code"] == "500"

    def test_mark_down_mismo_error_NO_spamea_eventos(self, db_path):
        """Si el servicio ya está down con el mismo error_code, no
        debe emitir evento de nuevo (evita spam al dashboard)."""
        from src.utils.event_bus import get_bus
        bus = get_bus()
        received = []
        ready = threading.Event()
        stop = threading.Event()

        def consumer():
            for ev in bus.subscribe():
                if not ready.is_set():
                    ready.set()
                received.append(ev)
                if stop.is_set():
                    break

        t = threading.Thread(target=consumer, daemon=True)
        t.start()
        bus.publish({"type": "kickstart"})
        ready.wait(timeout=2.0)

        es.mark_down(db_path, "green_api", error_code="466")
        time.sleep(0.1)
        es.mark_down(db_path, "green_api", error_code="466")  # repeticion
        es.mark_down(db_path, "green_api", error_code="466")
        time.sleep(0.2)

        stop.set()
        bus.publish({"type": "stop"})
        t.join(timeout=2.0)

        evs = [e for e in received if e.get("type") == "external_service_changed"]
        # Solo 1 evento aunque hubo 3 mark_down con mismo código
        assert len(evs) == 1


# ─── mark_up ─────────────────────────────────────────────────────────

class TestMarkUp:
    def test_mark_up_desde_down_resetea_failures(self, db_path):
        es.mark_down(db_path, "green_api", error_code="466")
        es.mark_down(db_path, "green_api", error_code="466")
        es.mark_up(db_path, "green_api", reason="recovered")
        st = es.read_status(db_path, "green_api")
        assert st["status"] == "up"
        assert st["consecutive_failures"] == 0
        assert st["last_error_code"] is None

    def test_mark_up_desde_up_NO_emite_evento(self, db_path):
        """Si el servicio ya estaba up, mark_up no emite evento."""
        from src.utils.event_bus import get_bus
        bus = get_bus()
        received = []
        ready = threading.Event()
        stop = threading.Event()

        def consumer():
            for ev in bus.subscribe():
                if not ready.is_set():
                    ready.set()
                received.append(ev)
                if stop.is_set():
                    break

        t = threading.Thread(target=consumer, daemon=True)
        t.start()
        bus.publish({"type": "kickstart"})
        ready.wait(timeout=2.0)

        es.mark_up(db_path, "green_api")  # ya estaba up — no debe emitir
        time.sleep(0.2)

        stop.set()
        bus.publish({"type": "stop"})
        t.join(timeout=2.0)

        evs = [e for e in received if e.get("type") == "external_service_changed"]
        assert len(evs) == 0

    def test_mark_up_desde_down_SI_emite_evento(self, db_path):
        from src.utils.event_bus import get_bus
        bus = get_bus()
        received = []
        ready = threading.Event()

        def consumer():
            for ev in bus.subscribe():
                if not ready.is_set():
                    ready.set()
                received.append(ev)
                if any(e.get("status") == "up" for e in received):
                    break

        es.mark_down(db_path, "green_api", error_code="466")

        t = threading.Thread(target=consumer, daemon=True)
        t.start()
        bus.publish({"type": "kickstart"})
        ready.wait(timeout=2.0)

        es.mark_up(db_path, "green_api", reason="test")

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if any(e.get("status") == "up" for e in received):
                break
            time.sleep(0.02)
        t.join(timeout=2.0)

        evs = [e for e in received if e.get("status") == "up"]
        assert len(evs) == 1
        assert evs[0]["reason"] == "test"
