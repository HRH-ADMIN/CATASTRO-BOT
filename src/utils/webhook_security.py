"""Defensas criptográficas para endpoints HTTP del bot.

Hoy el bot usa POLLING contra Green API (no expone webhook), pero deja la
puerta lista para tres escenarios:

  1. **HMAC SHA-256 webhook verification** — si en el futuro se monta un
     receptor de webhooks (Green API, Stripe, IMAP push, etc.), validar la
     firma evita que terceros maliciosos POSTeen mensajes falsos al bot.

  2. **Bearer token validation** — para endpoints internos como `/health`
     que opcionalmente quieren auth además de bind-to-localhost.

  3. **Constant-time comparison** — defensa contra timing attacks al
     comparar secrets/tokens.

Las primitivas son puras (no tocan red ni BD) y thread-safe.

USO HMAC:
    from src.utils.webhook_security import verify_hmac_signature
    body = request.get_data()  # bytes
    sig  = request.headers.get("X-Signature", "")
    if not verify_hmac_signature(body, sig, secret=SECRET):
        return 403

USO Bearer:
    from src.utils.webhook_security import verify_bearer_token
    auth = request.headers.get("Authorization", "")
    if not verify_bearer_token(auth, expected=TOKEN):
        return 401
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from typing import Optional

log = logging.getLogger("catastro.webhook_security")


# ── Constantes ──────────────────────────────────────────────────────────

# Algoritmos soportados para HMAC (mapeo a hashlib).
_ALGORITMOS: dict[str, str] = {
    "sha256": "sha256",
    "sha1":   "sha1",
    "sha512": "sha512",
}

# Header convencional para firmas; cada proveedor usa el suyo (Stripe:
# `Stripe-Signature`, GitHub: `X-Hub-Signature-256`, Green API: pendiente).
DEFAULT_SIGNATURE_HEADER = "X-Signature"


# ── HMAC signature ──────────────────────────────────────────────────────

def compute_hmac_signature(
    body: bytes,
    *,
    secret: str,
    algoritmo: str = "sha256",
) -> str:
    """Devuelve `"<algoritmo>=<hex>"` (formato GitHub/Stripe).

    El formato `algoritmo=hex` deja explícito qué hash usó quien firma y
    permite rotar algoritmos sin cambiar el header.

    Raises:
        ValueError: si el algoritmo no está soportado.
        ValueError: si secret está vacío.
    """
    if not secret:
        raise ValueError("secret no puede estar vacío")
    if algoritmo not in _ALGORITMOS:
        raise ValueError(
            f"algoritmo {algoritmo!r} no soportado "
            f"(usa: {sorted(_ALGORITMOS)})"
        )
    if isinstance(body, str):
        body = body.encode("utf-8")
    digest = hmac.new(
        secret.encode("utf-8"),
        body,
        getattr(hashlib, _ALGORITMOS[algoritmo]),
    ).hexdigest()
    return f"{algoritmo}={digest}"


def verify_hmac_signature(
    body: bytes,
    signature: str,
    *,
    secret: str,
    algoritmo_default: str = "sha256",
) -> bool:
    """Verifica firma HMAC sin filtrar info via timing.

    Acepta dos formatos en `signature`:
      - `"sha256=abc123..."` → algoritmo explícito en el header
      - `"abc123..."`         → asume `algoritmo_default`

    Returns:
        True si la firma matchea. False ante:
          - signature vacío o malformado
          - algoritmo desconocido
          - hex inválido
          - hash no matchea

    NUNCA lanza excepciones — falla cerrada (returns False).
    """
    if not signature or not secret:
        return False
    sig = signature.strip()

    # Normalizar formato `algoritmo=hex` vs `hex` solo
    if "=" in sig:
        algoritmo, _, hex_recibido = sig.partition("=")
        algoritmo = algoritmo.lower().strip()
    else:
        algoritmo = algoritmo_default
        hex_recibido = sig

    if algoritmo not in _ALGORITMOS:
        log.warning("HMAC: algoritmo %r no soportado", algoritmo)
        return False

    try:
        esperado = compute_hmac_signature(
            body, secret=secret, algoritmo=algoritmo,
        )
        # `esperado` viene como "algoritmo=hex" — extraer la parte hex
        _, _, hex_esperado = esperado.partition("=")
    except ValueError as exc:
        log.warning("HMAC: error calculando firma esperada — %s", exc)
        return False

    # Comparación constant-time
    try:
        return hmac.compare_digest(hex_recibido.lower(), hex_esperado.lower())
    except Exception as exc:  # nosec — defensivo, nunca debería pasar
        log.warning("HMAC: compare_digest falló — %s", exc)
        return False


# ── Bearer token ────────────────────────────────────────────────────────

def verify_bearer_token(
    authorization_header: str,
    *,
    expected: str,
) -> bool:
    """Valida `Authorization: Bearer <token>` con comparación constant-time.

    Returns:
        True si el header tiene el formato correcto y el token matchea.
        False si: header vacío, formato inválido, token no matchea, o
        `expected` vacío.
    """
    if not expected or not authorization_header:
        return False
    parts = authorization_header.strip().split(None, 1)
    if len(parts) != 2:
        return False
    scheme, token = parts
    if scheme.lower() != "bearer":
        return False
    try:
        return hmac.compare_digest(token, expected)
    except Exception:
        return False


# ── Generación de secrets ───────────────────────────────────────────────

def generate_secret(length_bytes: int = 32) -> str:
    """Genera un secret URL-safe de N bytes (default 256 bits)."""
    if length_bytes < 16:
        raise ValueError("length_bytes debe ser >= 16 (mínimo 128 bits)")
    return secrets.token_urlsafe(length_bytes)


# ── Replay protection (timestamp) ───────────────────────────────────────

def verify_timestamp_freshness(
    timestamp: Optional[str | int | float],
    *,
    max_age_seconds: int = 300,
    now_fn=None,
) -> bool:
    """Verifica que `timestamp` (Unix epoch seg) no sea más viejo que N seg.

    Defensa contra replay attacks: aunque la firma sea válida, si el body
    se grabó hace 1 hora no debe aceptarse.

    Returns:
        False si timestamp es None, no numérico, futuro (>60s adelantado),
        o más viejo que `max_age_seconds`.
    """
    if timestamp is None or timestamp == "":
        return False
    try:
        ts = float(timestamp)
    except (TypeError, ValueError):
        return False

    import time as _time
    ahora = (now_fn or _time.time)()

    # Rechazar timestamps muy en el futuro (clock skew razonable = 60s)
    if ts > ahora + 60:
        return False
    edad = ahora - ts
    # edad puede ser negativa hasta -60s (clock skew); aceptamos.
    return edad <= max_age_seconds


__all__ = [
    "compute_hmac_signature",
    "verify_hmac_signature",
    "verify_bearer_token",
    "generate_secret",
    "verify_timestamp_freshness",
    "DEFAULT_SIGNATURE_HEADER",
]
