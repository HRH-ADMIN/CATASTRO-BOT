"""Test end-to-end de sincronización U-04.

Verifica que el ciclo completo funciona:
  Database.cambiar_estado() → Database.actualizar_metadata()
       → publish al event_bus
       → cliente SSE conectado recibe el evento

Plan: PLAN_MEJORAS Sprint 1 / U-04 paso 7.
"""
from __future__ import annotations
import json
import sqlite3
import threading
import time
import uuid

import pytest

from src.core.credential_manager import CredentialManager
from src.core.database import Database
from src.utils.event_bus import get_bus, reset_bus_for_testing


@pytest.fixture(autouse=True)
def _reset_bus():
    reset_bus_for_testing()
    yield
    reset_bus_for_testing()


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CATASTRO_BOT_DEV_MODE", "1")
    db_path = tmp_path / "test_catastro.db"
    monkeypatch.setattr("config.settings.DATABASE_PATH", db_path)
    d = Database(path=db_path, credentials=CredentialManager())
    d.initialize_schema()
    return d


def _crear_expediente_minimal(db: Database, *, numero: str | None = None) -> str:
    exp_id = str(uuid.uuid4())
    numero = numero or f"TEST-{exp_id[:8]}"
    with sqlite3.connect(db.path) as conn:
        conn.execute(
            """INSERT INTO expedientes
               (id, numero_expediente, tipo_plano, nombre_topografo,
                telefono_cliente, estado_actual, municipalidad,
                fecha_creacion, fecha_actualizacion, metadata_json,
                completado, cancelado)
               VALUES (?, ?, 'segregacion', 'Test', '50612345678',
                       'recibido', 'San Ramón',
                       '2026-01-01T00:00:00.000Z',
                       '2026-01-01T00:00:00.000Z',
                       '{}', 0, 0)""",
            (exp_id, numero),
        )
        conn.commit()
    return exp_id


def _subscribe_in_thread(bus, received: list, ready: threading.Event,
                         stop: threading.Event) -> threading.Thread:
    """Suscriptor en thread que dropea eventos en `received` hasta `stop`."""
    def consumer():
        gen = bus.subscribe()
        try:
            for ev in gen:
                if not ready.is_set():
                    ready.set()
                received.append(ev)
                if stop.is_set():
                    break
        finally:
            gen.close()

    t = threading.Thread(target=consumer, daemon=True)
    t.start()
    return t


class TestCambiarEstadoPublica:
    def test_cambiar_estado_publica_evento_al_bus(self, db):
        from src.models.estado import Estado

        bus = get_bus()
        received: list = []
        ready = threading.Event()
        stop = threading.Event()
        t = _subscribe_in_thread(bus, received, ready, stop)

        # Forzar registro del subscriber
        bus.publish({"type": "kickstart"})
        ready.wait(timeout=2.0)

        exp_id = _crear_expediente_minimal(db, numero="SYNC-E2E-01")
        db.cambiar_estado(exp_id, Estado.FORMATO_VALIDADO.value, actor="test")

        # Esperar a que el evento llegue
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            tipos = [e.get("type") for e in received]
            if "expediente_updated" in tipos:
                break
            time.sleep(0.02)

        stop.set()
        bus.publish({"type": "stop"})  # destrabar get()
        t.join(timeout=2.0)

        # Debe haber recibido el evento tipado
        eventos_exp = [e for e in received if e.get("type") == "expediente_updated"]
        assert len(eventos_exp) == 1, (
            f"Esperaba 1 expediente_updated, recibí {received!r}"
        )
        ev = eventos_exp[0]
        assert ev["id"] == exp_id
        assert ev["numero_expediente"] == "SYNC-E2E-01"
        assert ev["estado_actual"] == Estado.FORMATO_VALIDADO.value
        assert ev["actor"] == "test"
        assert ev["accion"] == "cambiar_estado"
        assert "ts" in ev
        # Defensa: no debe filtrarse metadata_json crudo
        assert "metadata_json" not in ev


class TestActualizarMetadataPublica:
    def test_actualizar_metadata_publica_evento(self, db):
        bus = get_bus()
        received: list = []
        ready = threading.Event()
        stop = threading.Event()
        t = _subscribe_in_thread(bus, received, ready, stop)

        bus.publish({"type": "kickstart"})
        ready.wait(timeout=2.0)

        exp_id = _crear_expediente_minimal(db, numero="META-E2E-01")
        db.actualizar_metadata(
            exp_id,
            {"apt_tramite": "1234567", "apt_estado": "Calificación RN"},
            actor="apt-sync",
        )

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            tipos = [e.get("type") for e in received]
            if "expediente_updated" in tipos:
                break
            time.sleep(0.02)

        stop.set()
        bus.publish({"type": "stop"})
        t.join(timeout=2.0)

        eventos = [e for e in received if e.get("type") == "expediente_updated"]
        assert len(eventos) == 1
        ev = eventos[0]
        assert ev["numero_expediente"] == "META-E2E-01"
        assert ev["accion"] == "actualizar_metadata"
        assert ev["actor"] == "apt-sync"
        # apt_tramite NO debe filtrarse al payload
        assert "apt_tramite" not in ev


class TestControlStatePublica:
    def test_write_control_state_publica_evento(self, tmp_path):
        from src.core.control_state import ControlStateManager

        bus = get_bus()
        received: list = []
        ready = threading.Event()
        stop = threading.Event()
        t = _subscribe_in_thread(bus, received, ready, stop)

        bus.publish({"type": "kickstart"})
        ready.wait(timeout=2.0)

        mgr = ControlStateManager(tmp_path / "control.json")
        mgr.write({"enabled": False}, set_by="test", reason="e2e")

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            tipos = [e.get("type") for e in received]
            if "control_state_changed" in tipos:
                break
            time.sleep(0.02)

        stop.set()
        bus.publish({"type": "stop"})
        t.join(timeout=2.0)

        eventos = [e for e in received if e.get("type") == "control_state_changed"]
        assert len(eventos) == 1
        ev = eventos[0]
        assert ev["enabled"] is False
        assert ev["set_by"] == "test"
        assert ev["reason"] == "e2e"


class TestFallaDeEventBusNoRompeMutacion:
    """Si el event_bus falla (no debería), las mutaciones del Database
    deben seguir funcionando. Es best-effort en la signaling layer."""

    def test_cambiar_estado_funciona_aunque_bus_falle(self, db, monkeypatch):
        from src.models.estado import Estado

        def _boom(*args, **kwargs):
            raise RuntimeError("event_bus simulated failure")

        monkeypatch.setattr(
            "src.utils.event_bus.publish_expediente_updated", _boom,
        )

        exp_id = _crear_expediente_minimal(db)
        # NO debe lanzar — el publisher está envuelto en try/except.
        db.cambiar_estado(exp_id, Estado.FORMATO_VALIDADO.value, actor="test")

        # Mutación efectiva en BD:
        with sqlite3.connect(db.path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT estado_actual FROM expedientes WHERE id = ?",
                (exp_id,),
            ).fetchone()
        assert row["estado_actual"] == Estado.FORMATO_VALIDADO.value
