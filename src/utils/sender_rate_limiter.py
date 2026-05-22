"""Rate-limit por teléfono remitente para comandos WhatsApp.

Defensa contra:
  - Brute-force de comandos por número no autorizado intentando adivinar
    formatos o explotar bugs en el parser.
  - Spam de un número legítimo comprometido.
  - DoS lógico contra el bot (mil comandos en un segundo saturan logs y BD).

Estrategia: por cada número remitente, un token bucket independiente.
Si el bucket está vacío, el comando se rechaza ANTES de tocar BD o IA.

Diseño:
  - Buckets in-memory (dict[telefono → RateLimiter])
  - Buckets se crean perezosamente al primer mensaje
  - Buckets ociosos se purgan tras `ttl_segundos` sin actividad
  - Thread-safe (lock del dict + lock del propio RateLimiter)

USO:
    from src.utils.sender_rate_limiter import SenderRateLimiter
    limiter = SenderRateLimiter(max_por_minuto=20)

    if not limiter.permitir(sender_phone):
        reply(sender_phone, "⚠️ Demasiados mensajes — espera un momento.")
        return
    # ... procesar comando ...
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from src.utils.rate_limiter import RateLimiter

log = logging.getLogger("catastro.sender_rate_limiter")


class SenderRateLimiter:
    """Rate-limit por sender_phone con cleanup perezoso.

    Args:
        max_por_minuto: cuántos comandos puede mandar un mismo número por
            ventana de 60 segundos (default 20 — más que suficiente para
            uso humano, suficiente para detener spam automatizado).
        burst: tamaño del burst (default 5 — permite que un humano mande
            5 comandos seguidos sin esperar).
        ttl_segundos: si un número no manda mensajes por este tiempo, se
            olvida (libera memoria). Default 1 hora.
        max_buckets: tope absoluto de buckets antes de purgar los más
            viejos. Defensa contra un atacante que rote 1 millón de
            teléfonos para agotar memoria.
    """

    def __init__(
        self,
        *,
        max_por_minuto: int = 20,
        burst: int = 5,
        ttl_segundos: int = 3600,
        max_buckets: int = 10000,
    ):
        if max_por_minuto < 1:
            raise ValueError("max_por_minuto debe ser >= 1")
        if burst < 1:
            raise ValueError("burst debe ser >= 1")
        self._max_por_minuto = max_por_minuto
        self._burst = burst
        self._ttl = ttl_segundos
        self._max_buckets = max_buckets
        # intervalo entre tokens: 60/max → 1 token cada N segundos
        self._intervalo = 60.0 / max_por_minuto
        self._buckets: dict[str, RateLimiter] = {}
        self._ultima_actividad: dict[str, float] = {}
        self._lock = threading.Lock()
        # Métricas
        self._total_aceptados = 0
        self._total_rechazados = 0

    def _purgar_si_necesario(self, now: Optional[float] = None) -> int:
        """Borra buckets sin actividad reciente. Debe llamarse con `_lock`."""
        now = now or time.monotonic()
        cutoff = now - self._ttl
        a_borrar = [
            tel for tel, ts in self._ultima_actividad.items()
            if ts < cutoff
        ]
        for tel in a_borrar:
            self._buckets.pop(tel, None)
            self._ultima_actividad.pop(tel, None)

        # Si seguimos por encima del cap, borrar los más viejos
        if len(self._buckets) > self._max_buckets:
            por_edad = sorted(
                self._ultima_actividad.items(), key=lambda kv: kv[1],
            )
            exceso = len(self._buckets) - self._max_buckets
            for tel, _ in por_edad[:exceso]:
                self._buckets.pop(tel, None)
                self._ultima_actividad.pop(tel, None)
            a_borrar.extend(t for t, _ in por_edad[:exceso])

        return len(a_borrar)

    def permitir(self, sender_phone: str) -> bool:
        """¿Puede `sender_phone` mandar un comando ahora? (no bloquea)

        Returns:
            True si el bucket tenía token y se consumió.
            False si el bucket está vacío — el comando debe rechazarse.
        """
        if not sender_phone:
            return False
        now = time.monotonic()
        with self._lock:
            # Cleanup oportunista (cada llamada — barato porque dict pequeño)
            if len(self._buckets) > self._max_buckets // 2:
                self._purgar_si_necesario(now)
            bucket = self._buckets.get(sender_phone)
            if bucket is None:
                bucket = RateLimiter(
                    capacidad=self._burst,
                    intervalo_segundos=self._intervalo,
                    nombre=f"sender:{sender_phone}",
                )
                self._buckets[sender_phone] = bucket
            self._ultima_actividad[sender_phone] = now

        ok = bucket.try_acquire()
        with self._lock:
            if ok:
                self._total_aceptados += 1
            else:
                self._total_rechazados += 1
                log.warning(
                    "rate-limit excedido para %s (rechazados=%d total)",
                    sender_phone, self._total_rechazados,
                )
        return ok

    def reset_sender(self, sender_phone: str) -> bool:
        """Borra el bucket de un sender específico. True si existía."""
        with self._lock:
            existia = sender_phone in self._buckets
            self._buckets.pop(sender_phone, None)
            self._ultima_actividad.pop(sender_phone, None)
        return existia

    def stats(self) -> dict:
        """Métricas globales."""
        with self._lock:
            return {
                "senders_activos":   len(self._buckets),
                "total_aceptados":   self._total_aceptados,
                "total_rechazados":  self._total_rechazados,
                "max_por_minuto":    self._max_por_minuto,
                "burst":             self._burst,
            }


__all__ = ["SenderRateLimiter"]
