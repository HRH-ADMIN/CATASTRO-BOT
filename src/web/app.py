"""App Flask del catastro-bot — dashboard + control state + healthcheck.

Diseño:
  - Reutiliza el rendering del dashboard existente (`src/utils/dashboard_web.py`)
    para no duplicar HTML/CSS. Migración es solo del transporte (http.server
    → Flask) y el agregado de endpoints de control.
  - Endpoints de mutación (`POST`) requieren bearer token si está configurado
    en Cred Manager bajo `dashboard-token`. Lectura es libre (bind 127.0.0.1
    da defensa en profundidad).
  - Sirve con `waitress` (no `flask run` ni `gunicorn`) — WSGI estable en
    Windows sin event loop ni sorpresas.

Endpoints:
  GET  /                   → dashboard HTML
  GET  /config             → página de credenciales
  POST /config/set         → guardar credencial (form)
  GET  /api/expedientes    → lista expedientes (JSON)
  GET  /api/bot-status     → estado de servicios (JSON)
  GET  /api/config         → estado de credenciales (JSON, no valores)
  GET  /api/state          → control_state actual (JSON)
  POST /api/state          → mutar control_state — auth requerida
  POST /api/pause          → enabled=false — auth requerida
  POST /api/resume         → enabled=true — auth requerida
  GET  /api/health         → verificar_salud() (JSON)
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from flask import Flask, jsonify, redirect, request

from src.core.control_state import KNOWN_MODULES, get_manager as get_control_manager
from src.core.credential_manager import CredentialManager
from src.utils.webhook_security import verify_bearer_token

log = logging.getLogger("catastro.web")


# ── Token bearer para POSTs ────────────────────────────────────────────

_CRED_DASHBOARD_TOKEN = "dashboard-token"


def _expected_token() -> Optional[str]:
    """Lee el token del SecretStore. Si no existe, devuelve None (sin auth)."""
    try:
        cm = CredentialManager()
        if cm.exists(_CRED_DASHBOARD_TOKEN):
            return cm.get_credential(_CRED_DASHBOARD_TOKEN)[1]
    except Exception:
        log.exception("error leyendo dashboard-token")
    return None


def _require_auth_for_mutations() -> Optional[tuple[dict, int]]:
    """Si hay token configurado, valida el Authorization header.

    Returns:
        None si autorizado (o no se requiere auth).
        (response_dict, status_code) si rechazo.
    """
    expected = _expected_token()
    if not expected:
        return None  # No hay token → endpoints públicos (defensa en 127.0.0.1)
    auth = request.headers.get("Authorization", "")
    if not verify_bearer_token(auth, expected=expected):
        return ({"error": "unauthorized"}, 401)
    return None


# ── Factoría de la app ─────────────────────────────────────────────────

def create_app() -> Flask:
    """Construye la app Flask. Idempotente — llamala una vez por proceso."""
    app = Flask("catastro-bot")
    # Importes diferidos para evitar costo en imports al cargar el módulo
    # cuando se usa solo el factory para tests.
    from src.utils import dashboard_web as legacy

    # ──────────────────────────── HTML pages ────────────────────────────

    @app.route("/", methods=["GET"])
    @app.route("/index.html", methods=["GET"])
    @app.route("/dashboard", methods=["GET"])
    def home():
        return legacy._render_html(refresh_sec=30), 200, {
            "Content-Type": "text/html; charset=utf-8",
            "Cache-Control": "no-store",
        }

    @app.route("/config", methods=["GET"])
    def config_page():
        return legacy._render_config_html(), 200, {
            "Content-Type": "text/html; charset=utf-8",
            "Cache-Control": "no-store",
        }

    @app.route("/config/set", methods=["POST"])
    def config_set():
        # Mismas reglas de auth que /api/state — sólo si hay token configurado
        denied = _require_auth_for_mutations()
        if denied:
            return jsonify(denied[0]), denied[1]
        key  = (request.form.get("key")  or "").strip()
        val1 = (request.form.get("val1") or "").strip()
        val2 = (request.form.get("val2") or "").strip()
        resultado = legacy._set_credencial(key, val1, val2)
        # Redirect al /config con el mensaje (compatibilidad con UI legacy)
        from urllib.parse import quote
        return redirect(f"/config?msg={quote(resultado)}", code=303)

    # ──────────────────────────── JSON APIs ─────────────────────────────

    @app.route("/api/expedientes", methods=["GET"])
    def api_expedientes():
        return jsonify(legacy._leer_expedientes())

    @app.route("/api/bot-status", methods=["GET"])
    def api_bot_status():
        return jsonify(legacy._obtener_estado_bot())

    @app.route("/api/config", methods=["GET"])
    def api_config():
        return jsonify(legacy._obtener_config_estado())

    # ─────────────────── Control state — toggle ON/OFF ──────────────────

    @app.route("/api/state", methods=["GET"])
    def api_state_get():
        mgr = get_control_manager()
        state = mgr.read()
        return jsonify(state.to_dict())

    @app.route("/api/state", methods=["POST"])
    def api_state_post():
        denied = _require_auth_for_mutations()
        if denied:
            return jsonify(denied[0]), denied[1]
        data = request.get_json(silent=True) or {}
        # Validación blanda: solo aceptar keys conocidas
        patch = {}
        for key in ("enabled", "modules", "pause_until", "reason"):
            if key in data:
                patch[key] = data[key]
        if "modules" in patch:
            unknown = set(patch["modules"]) - set(KNOWN_MODULES)
            if unknown:
                return jsonify({
                    "error": "modules desconocidos",
                    "unknown": sorted(unknown),
                    "known": list(KNOWN_MODULES),
                }), 400
        set_by = request.headers.get("X-Actor", "web_dashboard")
        new_state = get_control_manager().write(
            patch, set_by=set_by, reason=patch.get("reason"),
        )
        return jsonify(new_state.to_dict())

    @app.route("/api/pause", methods=["POST"])
    def api_pause():
        denied = _require_auth_for_mutations()
        if denied:
            return jsonify(denied[0]), denied[1]
        reason = (request.args.get("reason")
                  or (request.get_json(silent=True) or {}).get("reason")
                  or "manual pause via /api/pause")
        new_state = get_control_manager().write(
            {"enabled": False}, set_by="web_dashboard", reason=reason,
        )
        return jsonify(new_state.to_dict())

    @app.route("/api/resume", methods=["POST"])
    def api_resume():
        denied = _require_auth_for_mutations()
        if denied:
            return jsonify(denied[0]), denied[1]
        new_state = get_control_manager().write(
            {"enabled": True, "pause_until": None},
            set_by="web_dashboard",
            reason="manual resume via /api/resume",
        )
        return jsonify(new_state.to_dict())

    # ─────────────────────────── Healthcheck ────────────────────────────

    @app.route("/api/health", methods=["GET"])
    def api_health():
        from src.utils.healthcheck import verificar_salud
        try:
            salud = verificar_salud(db=None)
        except Exception as exc:
            return jsonify({"status": "error", "error": str(exc)}), 500
        # Convertir status a HTTP code
        code = 200 if salud["status"] in ("ok", "warn") else 503
        return jsonify(salud), code

    return app


# ── Server lifecycle ───────────────────────────────────────────────────

class WebServer:
    """Wrappa waitress en una API simple para arrancar/detener."""

    def __init__(self, app: Flask, host: str = "127.0.0.1", port: int = 9224,
                 threads: int = 4):
        from waitress.server import create_server
        self._app = app
        self._host = host
        self._port = port
        self._server = create_server(
            app, host=host, port=port, threads=threads,
            ident="catastro-bot",
        )
        self._thread: Optional[threading.Thread] = None

    @property
    def url(self) -> str:
        return f"http://{self._host}:{self._port}"

    def run_blocking(self) -> None:
        """Bloquea el thread actual sirviendo requests.

        Diseñado para correr en el thread principal del proceso del bot.
        `shutdown()` desde otro thread (signal handler) lo desbloquea.
        """
        log.info("dashboard escuchando en %s", self.url)
        self._server.run()

    def run_in_background(self) -> None:
        """Arranca el server en un thread daemon. No bloquea."""
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self.run_blocking,
            name="web-server",
            daemon=True,
        )
        self._thread.start()
        log.info("dashboard (background) en %s", self.url)

    def shutdown(self) -> None:
        try:
            self._server.close()
        except Exception:
            log.exception("error cerrando waitress")


def start_web_server(
    *,
    host: str = "127.0.0.1",
    port: int = 9224,
    threads: int = 4,
) -> WebServer:
    """Construye app + server. No los arranca — el caller decide foreground/bg."""
    return WebServer(create_app(), host=host, port=port, threads=threads)


__all__ = ["create_app", "WebServer", "start_web_server"]
