"""CSRF protection para el dashboard (Sprint 4 / S-04).

Patrón: **double-submit cookie**.

Cómo funciona:
  1. En cada GET de una página HTML, si el browser NO tiene una cookie
     `csrf_token`, el server le setea una con un valor aleatorio.
  2. El frontend lee la cookie (via meta tag inyectado en el HTML — no
     usa `document.cookie` porque la cookie es HttpOnly=False pero
     leerla desde JS funciona).
  3. Cuando hace un POST/PUT/DELETE/PATCH, el frontend envía la cookie
     en el header `X-CSRF-Token`.
  4. El server compara: cookie == header. Si no coinciden → 403.

Por qué este patrón (y NO flask-wtf):
  - **No requiere sesiones**: el dashboard del bot es stateless, no
    queremos arrastrar flask-session ni un SECRET_KEY que rotar.
  - **Funciona con SSE**: las cookies cruzan el EventSource correctamente.
  - **Más simple de auditar**: ~80 líneas vs dependencia externa.
  - **El bind 127.0.0.1 ya bloquea CSRF externo desde otros dominios**;
    este patrón es defense-in-depth contra un browser malicioso local
    (extensión hostil, página atacante abierta en otra pestaña).

Exenciones:
  - GET / HEAD / OPTIONS — naturalmente exentos (no mutan).
  - Requests con `Authorization: Bearer <token>` válido — clientes
    máquina (CLI, scripts) que ya tienen una credencial fuerte.

Plan: PLAN_MEJORAS Sprint 4 / S-04.
"""
from __future__ import annotations

import hmac
import logging
import secrets
from typing import Optional

from flask import Flask, Response, jsonify, request

_log = logging.getLogger("catastro.csrf")

# Nombre de la cookie + header
CSRF_COOKIE_NAME = "catastro_csrf"
CSRF_HEADER_NAME = "X-CSRF-Token"
# Largo del token (256 bits hex = 64 chars)
CSRF_TOKEN_BYTES = 32

# Métodos que NO requieren CSRF (lectura pura)
SAFE_METHODS = frozenset(["GET", "HEAD", "OPTIONS"])


def generate_token() -> str:
    """Token random hex, criptográficamente seguro."""
    return secrets.token_hex(CSRF_TOKEN_BYTES)


def tokens_match(a: Optional[str], b: Optional[str]) -> bool:
    """Comparación constante en tiempo para evitar timing attacks.

    Devuelve False si alguno es None o si no coinciden.
    """
    if not a or not b:
        return False
    if len(a) != len(b):
        return False
    return hmac.compare_digest(a, b)


def get_or_create_token() -> str:
    """Devuelve el token del request actual o genera uno nuevo.

    Si el request viene con cookie, la usa. Si no, genera una nueva
    (que el caller debe setear en la response).
    """
    existing = request.cookies.get(CSRF_COOKIE_NAME)
    if existing and len(existing) >= 32:
        return existing
    return generate_token()


def is_csrf_exempt(*, has_valid_bearer: bool) -> bool:
    """¿El request actual está exento de CSRF?

    Args:
        has_valid_bearer: si el caller ya validó que el Authorization
                          header tiene un Bearer token válido.

    Returns:
        True si el request NO debe verificarse contra CSRF.
    """
    if request.method in SAFE_METHODS:
        return True
    if has_valid_bearer:
        return True
    return False


def verify_csrf_or_403() -> Optional[tuple[dict, int]]:
    """Verifica el token CSRF del request actual.

    Returns:
        None si pasa.
        (response_dict, 403) si falla.
    """
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    header_token = request.headers.get(CSRF_HEADER_NAME)
    if not tokens_match(cookie_token, header_token):
        _log.warning(
            "CSRF rechazado: method=%s path=%s cookie=%s header=%s",
            request.method, request.path,
            "yes" if cookie_token else "no",
            "yes" if header_token else "no",
        )
        return ({"error": "csrf_token_invalid_or_missing"}, 403)
    return None


def attach_token_cookie(response: Response, token: str) -> Response:
    """Setea la cookie csrf_token en la response.

    HttpOnly=False porque JavaScript necesita leerla para mandarla en
    el header. Secure=False porque corremos en localhost sin HTTPS.
    SameSite=Strict para que otro origin no pueda inducir el envío.
    """
    response.set_cookie(
        CSRF_COOKIE_NAME,
        token,
        max_age=60 * 60 * 24 * 7,  # 7 días
        httponly=False,             # JS lo lee
        secure=False,                # localhost sin TLS
        samesite="Strict",
    )
    return response


def render_meta_tag(token: str) -> str:
    """HTML meta tag para inyectar el token en páginas HTML.

    El frontend lo lee con:
        document.querySelector('meta[name="csrf-token"]').content
    """
    # Token es hex, no necesita escape
    return f'<meta name="csrf-token" content="{token}">'


def install_csrf_protection(
    app: Flask,
    *,
    is_bearer_authorized,
) -> None:
    """Registra el middleware CSRF en la app Flask.

    Args:
        app: Flask app.
        is_bearer_authorized: callable() → bool. Devuelve True si el
                              request actual tiene un Bearer token válido.

    Comportamiento:
      - GET/HEAD/OPTIONS: pasa libre, setea cookie si no existe.
      - POST/PUT/DELETE/PATCH:
          a) Si bearer válido → exento, pasa.
          b) Si no → verifica cookie ↔ header, devuelve 403 si no matchean.
    """

    @app.before_request
    def _csrf_check():  # pragma: no cover via integration tests
        # Escape hatch para tests que no estén testeando CSRF mismo.
        if app.config.get("CSRF_DISABLED"):
            return None
        if is_csrf_exempt(has_valid_bearer=bool(is_bearer_authorized())):
            return None
        return _denied_or_none()

    @app.after_request
    def _csrf_set_cookie(resp: Response) -> Response:
        # Si la response es HTML y no hay cookie, setear una.
        if request.method not in SAFE_METHODS:
            return resp
        if request.cookies.get(CSRF_COOKIE_NAME):
            return resp
        # Solo setear para responses HTML (no API JSON)
        ct = resp.headers.get("Content-Type", "")
        if "text/html" in ct:
            return attach_token_cookie(resp, generate_token())
        return resp


def _denied_or_none():
    """Helper para Flask: devuelve tuple si rechazo, None si OK."""
    denied = verify_csrf_or_403()
    if denied:
        return jsonify(denied[0]), denied[1]
    return None
