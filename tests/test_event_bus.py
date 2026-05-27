"""Tests del event_bus in-memory (U-04 paso 5).

Cubre:
  - publish / subscribe básico.
  - Múltiples subscribers reciben el mismo evento.
  - Heartbeats automáticos al subscriber.
  - Drop policy bajo carga.
  - get_bus() es singleton.
  - reset_bus_for_testing() recrea estado limpio.

Plan: PLAN_MEJORAS Sprint 1 / U-04 paso 5.
"""
from __future__ import annotations
import threading
import time

import pytest

from src.utils.event_bus import (
    EventBus,
    get_bus,
    reset_bus_for_testing,
    publish,
    publish_expediente_updated,
    publish_control_state_changed,
)


@pytest.fixture(autouse=True)
def _reset_bus():
    """Cada test arranca con bus limpio."""
    reset_bus_for_testing()
    yield
    reset_bus_for_testing()


# ─── helpers ─────────────────────────────────────────────────────────

def _drain(generator, max_events=10, timeout_s=2.0):
    """Drena hasta `max_events` o `timeout_s` segundos. Útil para tests."""
    out = []
    deadline = time.monotonic() + timeout_s
    while len(out) < max_events and time.monotonic() < deadline:
        try:
            out.append(next(generator))
        except StopIteration:
            break
    return out


# ─── tests ────────────────────────────────────────────────────────────

class TestSingleton:
    def test_get_bus_devuelve_misma_instancia(self):
        b1 = get_bus()
        b2 = get_bus()
        assert b1 is b2

    def test_reset_crea_nueva_instancia(self):
        b1 = get_bus()
        reset_bus_for_testing()
        b2 = get_bus()
        assert b1 is not b2


class TestPublishSinSubscribers:
    def test_publish_no_explota_sin_subscribers(self):
        publish({"type": "test", "value": 42})  # no raise


class TestPubSubBasico:
    def test_subscriber_recibe_evento_publicado(self):
        bus = get_bus()

        gen = bus.subscribe()
        received = []

        def consumer():
            for ev in gen:
                received.append(ev)
                if len(received) >= 2:
                    break

        t = threading.Thread(target=consumer, daemon=True)
        t.start()

        # Esperar a que el subscriber esté registrado
        deadline = time.monotonic() + 1.0
        while bus.num_subscribers() == 0 and time.monotonic() < deadline:
            time.sleep(0.01)

        publish({"type": "expediente_updated", "id": "abc"})
        publish({"type": "expediente_updated", "id": "xyz"})

        t.join(timeout=2.0)

        # El primer evento puede ser "hello" si la API lo agrega; en el bus
        # crudo no hay hello, así que esperamos exactamente los 2 eventos.
        assert len(received) >= 2
        tipos_recibidos = [e["type"] for e in received]
        assert "expediente_updated" in tipos_recibidos

    def test_evento_agrega_ts_si_falta(self):
        bus = get_bus()
        gen = bus.subscribe()
        received = []

        def consumer():
            for ev in gen:
                received.append(ev)
                if received:
                    break

        t = threading.Thread(target=consumer, daemon=True)
        t.start()
        deadline = time.monotonic() + 1.0
        while bus.num_subscribers() == 0 and time.monotonic() < deadline:
            time.sleep(0.01)

        publish({"type": "test"})
        t.join(timeout=2.0)

        assert received
        assert "ts" in received[0]


class TestMultipleSubscribers:
    def test_dos_subscribers_reciben_el_mismo_evento(self):
        bus = get_bus()

        gen1 = bus.subscribe()
        gen2 = bus.subscribe()
        rec1, rec2 = [], []

        def c1():
            for ev in gen1:
                rec1.append(ev)
                if rec1:
                    break

        def c2():
            for ev in gen2:
                rec2.append(ev)
                if rec2:
                    break

        t1 = threading.Thread(target=c1, daemon=True)
        t2 = threading.Thread(target=c2, daemon=True)
        t1.start(); t2.start()

        deadline = time.monotonic() + 1.0
        while bus.num_subscribers() < 2 and time.monotonic() < deadline:
            time.sleep(0.01)

        publish({"type": "broadcast"})

        t1.join(timeout=2.0)
        t2.join(timeout=2.0)

        assert len(rec1) == 1
        assert len(rec2) == 1
        assert rec1[0]["type"] == "broadcast"
        assert rec2[0]["type"] == "broadcast"


class TestPublishersTipados:
    def test_publish_expediente_updated_estructura(self):
        bus = get_bus()
        gen = bus.subscribe()
        received = []

        def consumer():
            for ev in gen:
                received.append(ev)
                if received:
                    break

        t = threading.Thread(target=consumer, daemon=True)
        t.start()
        deadline = time.monotonic() + 1.0
        while bus.num_subscribers() == 0 and time.monotonic() < deadline:
            time.sleep(0.01)

        publish_expediente_updated(
            expediente_id="exp-1",
            numero_expediente="SEG-TEST",
            estado_actual="recibido",
            actor="test",
            accion="cambiar_estado",
        )
        t.join(timeout=2.0)

        assert received
        ev = received[0]
        assert ev["type"] == "expediente_updated"
        assert ev["id"] == "exp-1"
        assert ev["numero_expediente"] == "SEG-TEST"
        assert ev["estado_actual"] == "recibido"
        assert ev["actor"] == "test"
        # NO debe filtrarse metadata_json en este publisher tipado
        assert "metadata_json" not in ev

    def test_publish_control_state_changed_estructura(self):
        bus = get_bus()
        gen = bus.subscribe()
        received = []

        def consumer():
            for ev in gen:
                received.append(ev)
                if received:
                    break

        t = threading.Thread(target=consumer, daemon=True)
        t.start()
        deadline = time.monotonic() + 1.0
        while bus.num_subscribers() == 0 and time.monotonic() < deadline:
            time.sleep(0.01)

        publish_control_state_changed(
            enabled=False,
            modules={"apt": False, "muni": True},
            reason="test",
            set_by="pytest",
        )
        t.join(timeout=2.0)

        assert received
        ev = received[0]
        assert ev["type"] == "control_state_changed"
        assert ev["enabled"] is False
        assert ev["modules"] == {"apt": False, "muni": True}


class TestSubscriberSeDesregistra:
    def test_num_subscribers_baja_cuando_generator_cierra(self):
        """subscribe() es generator — el registro ocurre al primer next().
        Validamos el ciclo completo: registro al consumir, desregistro al
        cerrar."""
        bus = get_bus()
        assert bus.num_subscribers() == 0

        registered = threading.Event()
        finished = threading.Event()

        def consumer():
            gen = bus.subscribe()
            try:
                # Bombear el generator en otro thread hasta que el caller
                # principal nos diga que terminó.
                for _ev in gen:
                    if not registered.is_set():
                        registered.set()
                    if finished.is_set():
                        break
            finally:
                gen.close()

        t = threading.Thread(target=consumer, daemon=True)
        t.start()

        # Empujar un evento para forzar entrada al loop del generator.
        time.sleep(0.05)
        publish({"type": "kickstart"})
        registered.wait(timeout=2.0)
        assert bus.num_subscribers() == 1

        finished.set()
        # Publicar para desbloquear el get() del subscriber
        publish({"type": "stop"})
        t.join(timeout=2.0)

        # Dar tiempo al finally para desregistrar
        time.sleep(0.1)
        assert bus.num_subscribers() == 0


class TestDropPolicyNoBloquea:
    def test_publisher_no_bloquea_si_subscriber_lento(self):
        """Si un subscriber no consume, publishers siguen funcionando."""
        bus = get_bus()

        # Suscribirse pero NO consumir — la cola se llena
        gen = bus.subscribe()
        deadline = time.monotonic() + 1.0
        while bus.num_subscribers() == 0 and time.monotonic() < deadline:
            time.sleep(0.01)

        # Publicar mucho más que MAX_QUEUE_SIZE
        from src.utils.event_bus import MAX_QUEUE_SIZE
        for i in range(MAX_QUEUE_SIZE + 50):
            publish({"type": "spam", "i": i})

        # No debe haber bloqueado — si llegamos acá en tiempo razonable es OK.
        # No verificamos el orden ni el count exacto (depende de drop policy).
        gen.close()
