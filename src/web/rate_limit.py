"""Rate limiter in-memory para el dashboard (Sprint 4 / S-07).

Defensa contra DoS local (un script enloquecido haciendo 1000 POSTs/s
contra `/api/control/emergency-stop`) y brute-force de Bearer tokens.

Por qué NO flask-limiter:
  - Es overkill para un dashboard local single-user.
  - Pulls deps pesadas (limits, redis-py opcional).
  - flask-limiter v3 requiere `flask>=2.2` con cosas que no necesitamos.

Diseño:
  - Sliding window counter (no fixed buckets para evitar bursts en bordes).
  - Key = (remote_addr, endpoint_id) — un cliente que pega muchos endpoints
    distintos consume cuotas separadas, lo cual es razonable.
  - Storage in-memory con threading.Lock. Sobrevive solo al proceso —
    si se reinicia el bot, las cuotas se resetean (no es un problema:
    Bearer/CSRF siguen vigentes).
  - Sin cleanup periódico: cada read purga el deque del key tocado.

Defaults (configurables al instalar):
  - Default:                        60 req / 60s
  - Endpoints destructivos override: 5 req / 60s
    (emergency-stop, control/stop, config/set)

Exit code: HTTP 429 + headers RFC 6585:
  Retry-After: <segundos>
  X-RateLimit-Limit: <N>
  X-RateLimit-Remaining: 0
  X-RateLimit-Reset: <epoch>

Plan: PLAN_MEJORAS Sprint 4 / S-07.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from typing import Optional

from flask import Flask, Response, jsonify, request

_log = logging.getLogger("catastro.rate_limit")


# ─── Reglas declarativas por endpoint ─────────────────────────────────

# Patrón path → (max_requests, window_seconds)
# Match es por prefijo: si la ruta del request empieza con la key, aplica.
# Reglas más específicas (más largas) tienen precedencia.
DEFAULT_LIMITS: dict[str, tuple[int, int]] = {
    # Default global — todas las rutas no listadas
    "*": (60, 60),
    # Endpoints destructivos / irreversibles
    "/api/control/emergency-stop": (5, 60),
    "/api/control/stop":           (10, 60),
    "/api/control/reset":          (10, 60),
    "/config/set":                 (10, 60),
    # SSE: el stream usa una sola conexión persistente, no es razonable
    # rate-limit a cada heartbeat. Skip vía exenciones.
}

# Endpoints que NO se rate-limit (streams, healthchecks)
EXEMPT_PATHS: frozenset[str] = frozenset([
    "/api/events/stream",   # SSE: una conexión = un request
    "/api/health",          # healthcheck externo
])


# ─── Almacenamiento sliding window ────────────────────────────────────

class _SlidingWindowStore:
    """Almacena timestamps de requests por key, devuelve count en ventana."""

    def __init__(self):
        self._buckets: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(
        self, key: tuple[str, str], *, max_n: int, window_s: int
    ) -> tuple[bool, int, int]:
        """Registra un hit. Devuelve (allowed, remaining, reset_in_s).

        - allowed: True si NO excedió el límite (este hit cuenta igual,
                   pero si es el N+1 devuelve False).
        - remaining: cuántos requests más puede hacer en esta ventana.
        - reset_in_s: segundos hasta que el bucket se vacíe del más viejo.
        """
        now = time.monotonic()
        cutoff = now - window_s
        with self._lock:
            dq = self._buckets[key]
            # Purgar timestamps fuera de la ventana
            while dq and dq[0] < cutoff:
                dq.popleft()
            if len(dq) >= max_n:
                # Rechazado — NO agregamos el hit (sino el atacante sostiene
                # 429s perpetuamente). El reset es cuando el más viejo cae.
                oldest = dq[0]
                reset_in = max(1, int(window_s - (now - oldest)) + 1)
                return (False, 0, reset_in)
            dq.append(now)
            remaining = max_n - len(dq)
            reset_in = window_s if not dq else max(
                1, int(window_s - (now - dq[0])) + 1
            )
            return (True, remaining, reset_in)

    def reset(self) -> None:
        """Borra todos los buckets — útil para tests."""
        with self._lock:
            self._buckets.clear()


# Singleton para la app Flask. Tests pueden resetear via reset_for_testing().
_store = _SlidingWindowStore()


def reset_for_testing() -> None:
    """Limpia el store. Llamar entre tests para aislamiento."""
    _store.reset()


# ─── Resolución de regla aplicable ────────────────────────────────────

def find_limit_for_path(
    path: str, rules: dict[str, tuple[int, int]] = None,
) -> tuple[int, int]:
    """Devuelve (max_n, window_s) para un path dado.

    Match por prefijo: la regla más específica (más larga) gana.
    Si nada matchea, aplica "*".
    """
    rules = rules if rules is not None else DEFAULT_LIMITS
    # Ordenar por longitud descendente para que "/api/control/emergency-stop"
    # gane sobre "/api/control".
    specifics = sorted(
        ((k, v) for k, v in rules.items() if k != "*"),
        key=lambda kv: len(kv[0]),
        reverse=True,
    )
    for prefix, limit in specifics:
        if path == prefix or path.startswith(prefix + "/"):
            return limit
    return rules.get("*", (60, 60))


# ─── Middleware Flask ────────────────────────────────────────────────

def install_rate_limit(
    app: Flask,
    *,
    rules: Optional[dict[str, tuple[int, int]]] = None,
    exempt_paths: Optional[frozenset[str]] = None,
    get_client_id=None,
) -> None:
    """Registra el middleware de rate limiting.

    Args:
        app: Flask app.
        rules: dict path-prefix → (max_requests, window_seconds). Default DEFAULT_LIMITS.
        exempt_paths: set de paths exentos. Default EXEMPT_PATHS.
        get_client_id: callable() → str. Default: request.remote_addr.
                       Override para tests o si querés agrupar por user.
    """
    rules = rules or DEFAULT_LIMITS
    exempt = exempt_paths if exempt_paths is not None else EXEMPT_PATHS

    def _default_client_id() -> str:
        return request.remote_addr or "unknown"

    get_id = get_client_id or _default_client_id

    @app.before_request
    def _rate_check():  # pragma: no cover via integration tests
        # Escape hatch para tests
        if app.config.get("RATE_LIMIT_DISABLED"):
            return None
        path = request.path
        if path in exempt:
            return None
        max_n, window_s = find_limit_for_path(path, rules)
        key = (get_id(), path)
        allowed, remaining, reset_in = _store.hit(
            key, max_n=max_n, window_s=window_s,
        )
        # Setear headers para que el cliente pueda ajustarse
        request._rate_limit_meta = {
            "limit":     max_n,
            "remaining": remaining,
            "reset_in":  reset_in,
        }
        if not allowed:
            _log.warning(
                "rate-limit 429: client=%s path=%s limit=%d window=%ds reset_in=%ds",
                key[0], path, max_n, window_s, reset_in,
            )
            resp = jsonify({
                "error":       "rate_limit_exceeded",
                "limit":       max_n,
                "window":      window_s,
                "retry_after": reset_in,
            })
            resp.status_code = 429
            resp.headers["Retry-After"] = str(reset_in)
            resp.headers["X-RateLimit-Limit"] = str(max_n)
            resp.headers["X-RateLimit-Remaining"] = "0"
            resp.headers["X-RateLimit-Reset"] = str(int(time.time()) + reset_in)
            return resp
        return None

    @app.after_request
    def _add_headers(resp: Response) -> Response:
        meta = getattr(request, "_rate_limit_meta", None)
        if not meta:
            return resp
        resp.headers["X-RateLimit-Limit"]     = str(meta["limit"])
        resp.headers["X-RateLimit-Remaining"] = str(meta["remaining"])
        resp.headers["X-RateLimit-Reset"]     = str(int(time.time()) + meta["reset_in"])
        return resp
