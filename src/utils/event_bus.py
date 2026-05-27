"""Event bus in-memory para el dashboard SSE.

Diseño:
  - Publisher / Subscriber pattern con `queue.SimpleQueue` por subscriber.
  - Single-process (gracias al modelo del bot: scheduler + dashboard
    viven en el mismo proceso Python via src.main + Flask). Si en el
    futuro el dashboard se separa, este módulo se sustituye por
    Redis pub/sub o similar — pero la API pública (`publish`, `subscribe`)
    queda igual.
  - Thread-safe: `SimpleQueue.put/get` son thread-safe en CPython.
  - Defensivo: si un subscriber lento se atasca, su cola crece pero NO
    bloquea a los publishers (put() no bloquea en SimpleQueue).
  - Drop policy: cuando una cola excede MAX_QUEUE_SIZE, se descartan
    eventos viejos (no nuevos) — el cliente está colgado y prefer
    feedback reciente.

API:
  publish(event: dict) -> None
      Envía a TODOS los subscribers activos. Nunca bloquea.

  subscribe() -> Iterator[dict]
      Generator que cede eventos hasta que el cliente cierra la conexión.
      Cuando termina, el subscriber se desregistra automáticamente.

  publish_heartbeat() -> None
      Helper: publica {"type": "heartbeat", "ts": <iso>}.

Eventos definidos (ver docs/SCHEMA.md §6):
  {"type": "expediente_updated", "id": ..., "numero_expediente": ..., ...}
  {"type": "control_state_changed", "module": ..., "enabled": bool, ...}
  {"type": "heartbeat", "ts": ...}

Plan: PLAN_MEJORAS_catastro-bot_3.md Sprint 1 / U-04 paso 5.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Iterator, Optional

log = logging.getLogger("catastro.event_bus")


# Límite de la cola por subscriber. Si un cliente se cuelga, las primeras
# este número de eventos quedan; los siguientes se descartan.
MAX_QUEUE_SIZE = 200

# Heartbeat enviado periódicamente para que el cliente sepa que la conexión
# sigue viva (y para que proxies/firewalls no corten la conexión por
# inactividad).
HEARTBEAT_INTERVAL_SECONDS = 25


class _Subscriber:
    """Una conexión SSE activa con su cola privada."""

    __slots__ = ("_q",)

    def __init__(self) -> None:
        # SimpleQueue es thread-safe y no bloquea en put().
        self._q: queue.SimpleQueue = queue.SimpleQueue()

    def put(self, event: dict) -> None:
        # Drop policy: si la cola excede el límite, drenamos los más viejos.
        # (SimpleQueue no expone maxsize ni .qsize() en todas las plataformas
        # de forma confiable; usamos un try/except defensivo.)
        try:
            qsize = self._q.qsize()
        except (NotImplementedError, AttributeError):
            qsize = 0
        if qsize >= MAX_QUEUE_SIZE:
            # Cliente lento: descartar uno viejo antes de meter el nuevo.
            try:
                self._q.get_nowait()
            except queue.Empty:
                pass
        self._q.put(event)

    def get(self, timeout: Optional[float] = None) -> Optional[dict]:
        try:
            return self._q.get(timeout=timeout) if timeout else self._q.get()
        except queue.Empty:
            return None


class EventBus:
    """Singleton del bus de eventos del proceso."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: list[_Subscriber] = []

    # ---------- publish ----------

    def publish(self, event: dict) -> None:
        """Envía a todos los subscribers. Nunca lanza ni bloquea."""
        # Snapshot bajo lock; entrega afuera del lock.
        with self._lock:
            subs = list(self._subscribers)
        if not subs:
            return
        # Garantizar ts si el caller no lo seteó.
        if "ts" not in event:
            event = {**event, "ts": _now_iso()}
        for s in subs:
            try:
                s.put(event)
            except Exception:
                # Nunca propagar — un subscriber roto no debe afectar al
                # resto ni al caller que está en medio de una mutación.
                log.exception("event_bus: subscriber falló")

    def publish_heartbeat(self) -> None:
        self.publish({"type": "heartbeat"})

    # ---------- subscribe ----------

    def subscribe(self) -> Iterator[dict]:
        """Generator de eventos. Se desuscribe al cerrar (StopIteration o GC).

        El consumer típico es un endpoint Flask SSE:

            @app.route("/api/events/stream")
            def stream():
                def gen():
                    for ev in event_bus.subscribe():
                        yield f"data: {json.dumps(ev)}\\n\\n"
                return Response(gen(), mimetype="text/event-stream")
        """
        sub = _Subscriber()
        with self._lock:
            self._subscribers.append(sub)
        log.debug("event_bus: nuevo subscriber (total=%d)", len(self._subscribers))

        last_heartbeat = time.monotonic()
        try:
            while True:
                # Polleamos con timeout para poder emitir heartbeat aunque
                # no haya eventos reales.
                ev = sub.get(timeout=1.0)
                if ev is not None:
                    yield ev
                now = time.monotonic()
                if now - last_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
                    last_heartbeat = now
                    yield {"type": "heartbeat", "ts": _now_iso()}
        except GeneratorExit:
            # El cliente cerró la conexión; salir limpio.
            pass
        finally:
            with self._lock:
                try:
                    self._subscribers.remove(sub)
                except ValueError:
                    pass
            log.debug("event_bus: subscriber desconectado (total=%d)",
                      len(self._subscribers))

    # ---------- helpers ----------

    def num_subscribers(self) -> int:
        with self._lock:
            return len(self._subscribers)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Singleton global del proceso. Lazy via función para tests que necesiten
# reset entre cases.
_GLOBAL_BUS: Optional[EventBus] = None


def get_bus() -> EventBus:
    global _GLOBAL_BUS
    if _GLOBAL_BUS is None:
        _GLOBAL_BUS = EventBus()
    return _GLOBAL_BUS


def reset_bus_for_testing() -> None:
    """Solo para tests — recrea el singleton. NUNCA llamar en producción."""
    global _GLOBAL_BUS
    _GLOBAL_BUS = EventBus()


# Atajos a nivel de módulo para no tener que llamar get_bus() siempre.

def publish(event: dict) -> None:
    get_bus().publish(event)


def publish_expediente_updated(
    expediente_id: str,
    numero_expediente: str,
    estado_actual: str,
    *,
    actor: str = "system",
    accion: Optional[str] = None,
) -> None:
    """Publisher tipado para mutaciones de expedientes.

    Llamarlo DESPUÉS de que la transacción de BD haya hecho commit, para que
    los subscribers vean el estado coherente si re-consultan la BD.
    """
    publish({
        "type": "expediente_updated",
        "id": expediente_id,
        "numero_expediente": numero_expediente,
        "estado_actual": estado_actual,
        "actor": actor,
        "accion": accion,
    })


def publish_control_state_changed(
    *,
    enabled: bool,
    modules: Optional[dict] = None,
    reason: Optional[str] = None,
    set_by: Optional[str] = None,
) -> None:
    publish({
        "type": "control_state_changed",
        "enabled": enabled,
        "modules": modules or {},
        "reason": reason,
        "set_by": set_by,
    })


__all__ = [
    "EventBus",
    "get_bus",
    "reset_bus_for_testing",
    "publish",
    "publish_expediente_updated",
    "publish_control_state_changed",
    "HEARTBEAT_INTERVAL_SECONDS",
    "MAX_QUEUE_SIZE",
]
