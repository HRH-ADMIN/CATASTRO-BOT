"""Rate limiter para llamadas a APIs externas (Green API, Anthropic, etc.).

Implementa un token bucket simple — N llamadas permitidas por ventana de
tiempo, con espera (blocking) si se excede.

Diseño:
  - Token bucket: capacidad inicial N, refill 1 token cada `intervalo` segundos
  - Thread-safe (lock interno) — usable desde múltiples threads
  - `acquire()` bloquea hasta que haya un token, opcionalmente con timeout
  - `try_acquire()` no bloquea, devuelve True/False
  - `with limiter:` context manager para acquire + release automático

Casos típicos:
  - Green API: ~1 mensaje/segundo para no triggear rate limit del provider
  - Anthropic API: spike protection (10 calls/min default)
  - Cualquier endpoint HTTP externo

USO:
    # Permite 5 llamadas cada 10 segundos
    limiter = RateLimiter(capacidad=5, intervalo_segundos=10)

    # Bloquea hasta tener token
    limiter.acquire()
    enviar_mensaje(...)

    # O usando context manager
    with limiter:
        enviar_mensaje(...)
"""
from __future__ import annotations
import logging
import threading
import time
from typing import Optional

log = logging.getLogger("catastro.rate_limiter")


class RateLimiter:
    """Token bucket rate limiter thread-safe.

    Args:
        capacidad: cantidad máxima de tokens (burst size).
        intervalo_segundos: tiempo para que se refill 1 token.
            Ej. capacidad=10, intervalo=1.0 → ~10 calls/segundo sostenidos
            con burst hasta 10.
        nombre: identificador para logs.
    """

    def __init__(
        self, *,
        capacidad: int,
        intervalo_segundos: float,
        nombre: str = "default",
    ):
        if capacidad < 1:
            raise ValueError("capacidad debe ser >= 1")
        if intervalo_segundos <= 0:
            raise ValueError("intervalo_segundos debe ser > 0")
        self._capacidad = capacidad
        self._intervalo = intervalo_segundos
        self._nombre = nombre
        self._tokens = float(capacidad)
        self._ultimo_refill = time.monotonic()
        self._lock = threading.Lock()
        # Métricas
        self._total_acquired = 0
        self._total_waited_sec = 0.0
        self._total_rejected = 0

    def _refill(self) -> None:
        """Recalcula tokens disponibles según el tiempo transcurrido.
        Debe llamarse con `_lock` ya adquirido.
        """
        ahora = time.monotonic()
        elapsed = ahora - self._ultimo_refill
        if elapsed <= 0:
            return
        # Tokens a agregar: 1 cada `intervalo` segundos
        nuevos = elapsed / self._intervalo
        self._tokens = min(self._capacidad, self._tokens + nuevos)
        self._ultimo_refill = ahora

    def try_acquire(self, tokens: int = 1) -> bool:
        """No bloqueante. Devuelve True si pudo adquirir `tokens`, False si no."""
        with self._lock:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                self._total_acquired += tokens
                return True
            self._total_rejected += tokens
            return False

    def acquire(
        self, tokens: int = 1, *, timeout: Optional[float] = None,
    ) -> bool:
        """Bloqueante. Espera hasta tener `tokens` disponibles o `timeout`.

        Returns:
            True si adquirió. False si timeout (sólo si `timeout` no es None).
        """
        deadline = time.monotonic() + timeout if timeout is not None else None
        inicio_espera = time.monotonic()
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    self._total_acquired += tokens
                    self._total_waited_sec += time.monotonic() - inicio_espera
                    return True
                # ¿Cuánto falta para tener el próximo token?
                faltan = tokens - self._tokens
                esperar = faltan * self._intervalo
            if deadline is not None:
                restante = deadline - time.monotonic()
                if restante <= 0:
                    self._total_rejected += tokens
                    return False
                esperar = min(esperar, restante)
            time.sleep(max(0.001, esperar))

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass  # no liberamos — los tokens se reponen solos por tiempo

    def stats(self) -> dict:
        """Devuelve métricas acumuladas."""
        with self._lock:
            self._refill()
            return {
                "nombre":           self._nombre,
                "capacidad":        self._capacidad,
                "tokens_disponibles": round(self._tokens, 2),
                "total_acquired":   self._total_acquired,
                "total_rejected":   self._total_rejected,
                "total_waited_sec": round(self._total_waited_sec, 2),
            }

    def reset(self) -> None:
        """Resetea contadores y rellena los tokens."""
        with self._lock:
            self._tokens = float(self._capacidad)
            self._ultimo_refill = time.monotonic()
            self._total_acquired = 0
            self._total_waited_sec = 0.0
            self._total_rejected = 0


# ── Limiters preconfigurados para APIs comunes ─────────────────────────

# Green API: ~1 msg/seg con burst de 5 (conservador — Green API permite más
# pero el sender se reserva margen para múltiples acciones simultáneas).
GREEN_API_LIMITER = RateLimiter(
    capacidad=5, intervalo_segundos=1.0, nombre="green-api",
)

# Anthropic API: 10 calls/min default (conservador para vision extraction).
# Sube si tu plan permite más.
ANTHROPIC_LIMITER = RateLimiter(
    capacidad=10, intervalo_segundos=6.0, nombre="anthropic",
)


__all__ = [
    "RateLimiter",
    "GREEN_API_LIMITER", "ANTHROPIC_LIMITER",
]
