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

import json as _json

from flask import Flask, Response, jsonify, redirect, request

from src.core.control_state import KNOWN_MODULES, get_manager as get_control_manager
from src.core.credential_manager import CredentialManager
from src.core.state_machine import (
    CooldownActive,
    InvalidTransition,
    UnknownModule,
    KNOWN_MODULES as SM_KNOWN_MODULES,
    get_state_machine,
)
from src.utils.event_bus import get_bus as _get_event_bus
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

    @app.route("/config/control", methods=["GET"])
    def control_panel_page():
        """Panel de control de módulos (U-03 paso 2.3).

        Standalone page que consume /api/control/* + SSE para mostrar
        el estado live de cada módulo y permitir transiciones con
        confirmación + audit.
        """
        from src.utils.dashboard_control_html import render_control_panel_html
        return render_control_panel_html(), 200, {
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

    # ──────── Revisiones pre-envío (Sprint 5 / N-02) ───────────────────
    # Cola de revisión visual antes del click irreversible "Enviar al CFIA".

    @app.route("/api/revisiones/pendientes", methods=["GET"])
    def api_revisiones_pendientes():
        from config.settings import DATABASE_PATH
        from src.utils import pre_envio_snapshot as _pes
        return jsonify({"revisiones": _pes.listar_pendientes(DATABASE_PATH)})

    @app.route("/api/revisiones/<rev_id>", methods=["GET"])
    def api_revision_detalle(rev_id: str):
        from config.settings import DATABASE_PATH
        from src.utils import pre_envio_snapshot as _pes
        rev = _pes.read_revision(DATABASE_PATH, rev_id)
        if rev is None:
            return jsonify({"error": "no encontrado"}), 404
        return jsonify(rev)

    @app.route("/api/revisiones/<rev_id>/screenshot.png", methods=["GET"])
    def api_revision_screenshot(rev_id: str):
        """Sirve el screenshot del portal CFIA capturado al crear la
        revisión. Defensa contra path traversal: solo sirve archivos
        bajo data/revisiones/ del proyecto.
        """
        from pathlib import Path
        from flask import send_file
        from config.settings import DATABASE_PATH
        from src.utils import pre_envio_snapshot as _pes
        rev = _pes.read_revision(DATABASE_PATH, rev_id)
        if rev is None or not rev.get("screenshot_path"):
            return jsonify({"error": "sin screenshot"}), 404
        # Resolver path bajo ROOT del proyecto
        try:
            from src.utils.dashboard_web import ROOT  # noqa: F401
            root = ROOT
        except (ImportError, AttributeError):
            root = Path.cwd()
        p = (root / rev["screenshot_path"]).resolve()
        revisiones_dir = (root / "data" / "revisiones").resolve()
        try:
            p.relative_to(revisiones_dir)
        except (ValueError, OSError):
            return jsonify({"error": "path fuera de data/revisiones"}), 403
        if not p.exists():
            return jsonify({"error": "archivo no existe"}), 404
        return send_file(str(p), mimetype="image/png")

    @app.route("/api/revisiones/<rev_id>/pdf", methods=["GET"])
    def api_revision_pdf(rev_id: str):
        """Sirve el PDF anverso. Path traversal defense: solo bajo data/."""
        from pathlib import Path
        from flask import send_file
        from config.settings import DATABASE_PATH
        from src.utils import pre_envio_snapshot as _pes
        rev = _pes.read_revision(DATABASE_PATH, rev_id)
        if rev is None or not rev.get("pdf_anverso_path"):
            return jsonify({"error": "sin PDF"}), 404
        try:
            from src.utils.dashboard_web import ROOT  # noqa: F401
            root = ROOT
        except (ImportError, AttributeError):
            root = Path.cwd()
        p = (root / rev["pdf_anverso_path"]).resolve()
        data_dir = (root / "data").resolve()
        try:
            p.relative_to(data_dir)
        except (ValueError, OSError):
            return jsonify({"error": "path fuera de data/"}), 403
        if not p.exists():
            return jsonify({"error": "PDF no existe"}), 404
        return send_file(str(p), mimetype="application/pdf")

    @app.route("/api/revisiones/<rev_id>/aprobar", methods=["POST"])
    def api_revision_aprobar(rev_id: str):
        denied = _require_auth_for_mutations()
        if denied:
            return jsonify(denied[0]), denied[1]
        from config.settings import DATABASE_PATH
        from src.utils import pre_envio_snapshot as _pes
        actor = request.headers.get("X-Actor", "web_dashboard")
        try:
            result = _pes.aprobar(DATABASE_PATH, rev_id, resuelto_por=actor)
        except KeyError as exc:
            return jsonify({"error": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 409
        return jsonify(result)

    @app.route("/api/revisiones/<rev_id>/rechazar", methods=["POST"])
    def api_revision_rechazar(rev_id: str):
        denied = _require_auth_for_mutations()
        if denied:
            return jsonify(denied[0]), denied[1]
        from config.settings import DATABASE_PATH
        from src.utils import pre_envio_snapshot as _pes
        data = request.get_json(silent=True) or {}
        razon = (data.get("razon") or "").strip()
        if not razon:
            return jsonify({"error": "razon es obligatoria"}), 400
        actor = request.headers.get("X-Actor", "web_dashboard")
        try:
            result = _pes.rechazar(DATABASE_PATH, rev_id,
                                    razon=razon, resuelto_por=actor)
        except KeyError as exc:
            return jsonify({"error": str(exc)}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 409
        return jsonify(result)

    @app.route("/expediente/<exp_id>/revisar-envio", methods=["GET"])
    def revisar_envio_page(exp_id: str):
        """Página side-by-side de revisión visual.

        Si no hay revisión pendiente para el expediente, redirige al
        dashboard principal con flash.
        """
        from src.utils.dashboard_revision_html import render_revision_panel_html
        return render_revision_panel_html(exp_id), 200, {
            "Content-Type": "text/html; charset=utf-8",
            "Cache-Control": "no-store",
        }

    # ──────────── API costs Anthropic (Sprint 4 / O-08) ────────────────

    @app.route("/api/costs/summary", methods=["GET"])
    def api_costs_summary():
        from config.settings import DATABASE_PATH
        from src.utils import api_costs as _ac
        return jsonify({
            "monthly":       _ac.read_monthly_summary(DATABASE_PATH),
            "daily":         _ac.read_daily_summary(DATABASE_PATH),
            "by_expediente": _ac.read_by_expediente(DATABASE_PATH, limit=20),
            "budget":        _ac.read_budget(DATABASE_PATH),
            "current_month_spend_usd":
                round(_ac.current_month_spend(DATABASE_PATH), 4),
        })

    @app.route("/api/costs/recent", methods=["GET"])
    def api_costs_recent():
        from config.settings import DATABASE_PATH
        from src.utils import api_costs as _ac
        try:
            limit = max(1, min(int(request.args.get("limit", 50)), 500))
        except (TypeError, ValueError):
            limit = 50
        return jsonify({"calls": _ac.read_recent_calls(DATABASE_PATH, limit=limit)})

    @app.route("/api/costs/budget", methods=["POST"])
    def api_costs_set_budget():
        denied = _require_auth_for_mutations()
        if denied:
            return jsonify(denied[0]), denied[1]
        from config.settings import DATABASE_PATH
        from src.utils import api_costs as _ac
        data = request.get_json(silent=True) or {}
        try:
            monthly_usd = float(data.get("monthly_usd", 50.0))
            alert_threshold = float(data.get("alert_threshold", 0.80))
            result = _ac.set_budget(
                DATABASE_PATH,
                monthly_usd=monthly_usd,
                alert_threshold=alert_threshold,
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(result)

    @app.route("/config/costos", methods=["GET"])
    def costos_panel_page():
        from src.utils.dashboard_costos_html import render_costos_panel_html
        return render_costos_panel_html(), 200, {
            "Content-Type": "text/html; charset=utf-8",
            "Cache-Control": "no-store",
        }

    # ──────────── External services health (Sprint 4 / N-03) ───────────

    @app.route("/api/external-services", methods=["GET"])
    def api_external_services():
        """Snapshot del estado de servicios externos (Green API, Anthropic,
        Drive, RNP). El dashboard lo usa para mostrar el banner amarillo
        de Green API caído."""
        from config.settings import DATABASE_PATH
        from src.utils import external_services as _es
        return jsonify({"services": _es.read_all(DATABASE_PATH)})

    @app.route("/api/external-services/<service>/mark-up", methods=["POST"])
    def api_external_service_mark_up(service: str):
        """Force-restaura un servicio (botón manual en el dashboard cuando
        el operador re-autoriza Green API y quiere acelerar el recovery).
        """
        denied = _require_auth_for_mutations()
        if denied:
            return jsonify(denied[0]), denied[1]
        from config.settings import DATABASE_PATH
        from src.utils import external_services as _es
        if service not in _es.KNOWN_SERVICES:
            return jsonify({"error": "servicio desconocido",
                            "known": list(_es.KNOWN_SERVICES)}), 400
        data = request.get_json(silent=True) or {}
        reason = (data.get("reason") or "manual mark-up via dashboard")[:200]
        result = _es.mark_up(DATABASE_PATH, service, reason=reason)
        return jsonify(result)

    # ─────────────────── Runtime processes (U-02 paso B) ────────────────
    # Capa SO: qué procesos están vivos.

    @app.route("/api/runtime/processes", methods=["GET"])
    def api_runtime_processes():
        """Lista procesos alive + hanging + opcionalmente recent dead."""
        from config.settings import DATABASE_PATH
        from src.utils import runtime_processes as _rp
        include_dead = request.args.get("include_dead", "").lower() in ("1", "true", "yes")
        if include_dead:
            data = _rp.list_all_recent(DATABASE_PATH, limit=50)
        else:
            data = _rp.list_alive(DATABASE_PATH)
        return jsonify({"processes": data, "include_dead": include_dead})

    @app.route("/api/runtime/logs/<process_name>", methods=["GET"])
    def api_runtime_logs(process_name: str):
        """Tail del log de un proceso. Solo lee el log_file_path declarado
        por el propio proceso al register_process — no acepta paths
        arbitrarios del cliente (defensa contra path traversal).

        Query params:
          tail=N (default 200) — últimas N líneas.
        """
        from config.settings import DATABASE_PATH
        from src.utils import runtime_processes as _rp
        # Whitelist: solo procesos que registraron log_file_path
        rows = _rp.list_all_recent(DATABASE_PATH, limit=20)
        matching = [r for r in rows if r["process_name"] == process_name]
        if not matching:
            return jsonify({"error": f"proceso {process_name!r} no registrado"}), 404
        log_path = matching[0].get("log_file_path")
        if not log_path:
            return jsonify({"error": "proceso sin log_file_path declarado"}), 404

        from pathlib import Path
        p = Path(log_path)
        # Defensa: el log_file_path declarado debe vivir bajo logs/ del proyecto
        # (validación blanda — sirve como sanity check ante registros corruptos).
        from config.settings import LOGS_DIR
        try:
            p.resolve().relative_to(LOGS_DIR.resolve())
        except (ValueError, OSError):
            return jsonify({
                "error": "log_file_path fuera de logs/ — rechazado por seguridad",
                "path": str(p),
            }), 403

        if not p.exists():
            return jsonify({"error": "archivo no existe (todavía)", "path": str(p)}), 404

        try:
            n = max(1, min(int(request.args.get("tail", 200)), 1000))
        except (TypeError, ValueError):
            n = 200

        try:
            # Lectura simple — para logs grandes (>50MB) podría optimizarse
            # leyendo desde el final con seek, pero por ahora es suficiente.
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception as exc:
            return jsonify({"error": f"no se pudo leer: {exc}"}), 500

        return jsonify({
            "process_name": process_name,
            "log_file_path": str(p),
            "tail_lines": n,
            "total_lines": len(lines),
            "lines": lines[-n:],
        })

    @app.route("/config/runtime", methods=["GET"])
    def runtime_panel_page():
        """Panel HTML que lista procesos + permite ver logs en vivo."""
        from src.utils.dashboard_runtime_html import render_runtime_panel_html
        return render_runtime_panel_html(), 200, {
            "Content-Type": "text/html; charset=utf-8",
            "Cache-Control": "no-store",
        }

    # ───────────────── Control State Machine (U-03 paso 2.2) ───────────
    # Reemplaza el modelo binario de /api/state con una máquina de estados
    # explícita que permite mostrar 'Apagando…' en lugar de saltos binarios.
    # SSOT: tabla module_state. control_state.py (legacy) sigue funcionando
    # en paralelo durante la migración (paso 2.4 lo deprecará).

    @app.route("/api/control/status", methods=["GET"])
    def api_control_status():
        """Snapshot de todos los módulos."""
        sm = get_state_machine()
        return jsonify({
            "modules": [s.to_dict() for s in sm.read_all()],
            "known_modules": list(SM_KNOWN_MODULES),
        })

    @app.route("/api/control/transition/<transition_id>", methods=["GET"])
    def api_control_transition(transition_id: str):
        """Polling de una transición en curso. El frontend lo usa para
        saber cuándo la transición llegó a un estado final."""
        sm = get_state_machine()
        st = sm.get_transition(transition_id)
        if st is None:
            return jsonify({"error": "transition_id no encontrado"}), 404
        body = st.to_dict()
        body["is_final"] = st.is_final()
        return jsonify(body)

    @app.route("/api/control/start/<module>", methods=["POST"])
    def api_control_start(module: str):
        """Inicia transición STOPPED → STARTING. Devuelve transition_id.

        El frontend después hace polling a /api/control/transition/<id>
        hasta que state == RUNNING o ERROR.

        Body JSON opcional: {"reason": "...", "force": false}.
        """
        denied = _require_auth_for_mutations()
        if denied:
            return jsonify(denied[0]), denied[1]
        sm = get_state_machine()
        data = request.get_json(silent=True) or {}
        reason = (data.get("reason") or "manual start via dashboard")[:200]
        force = bool(data.get("force"))
        actor = request.headers.get("X-Actor", "web_dashboard")
        try:
            st = sm.start(module, actor=actor, reason=reason, force=force)
        except UnknownModule as exc:
            return jsonify({"error": str(exc), "known": list(SM_KNOWN_MODULES)}), 400
        except InvalidTransition as exc:
            return jsonify({"error": str(exc), "current_state": sm.read(module).state}), 409
        except CooldownActive as exc:
            return jsonify({
                "error": str(exc),
                "cooldown_until": sm.read(module).cooldown_until,
                "hint": "Re-intentar tras el cooldown o usar force=true",
            }), 423  # 423 Locked
        return jsonify(st.to_dict()), 202  # 202 Accepted — transición en curso

    @app.route("/api/control/stop/<module>", methods=["POST"])
    def api_control_stop(module: str):
        """Inicia transición RUNNING → STOPPING. Devuelve transition_id."""
        denied = _require_auth_for_mutations()
        if denied:
            return jsonify(denied[0]), denied[1]
        sm = get_state_machine()
        data = request.get_json(silent=True) or {}
        reason = (data.get("reason") or "manual stop via dashboard")[:200]
        actor = request.headers.get("X-Actor", "web_dashboard")
        try:
            st = sm.stop(module, actor=actor, reason=reason)
        except UnknownModule as exc:
            return jsonify({"error": str(exc), "known": list(SM_KNOWN_MODULES)}), 400
        except InvalidTransition as exc:
            return jsonify({"error": str(exc), "current_state": sm.read(module).state}), 409
        return jsonify(st.to_dict()), 202

    @app.route("/api/control/reset/<module>", methods=["POST"])
    def api_control_reset(module: str):
        """ERROR → STOPPED (reset manual tras error)."""
        denied = _require_auth_for_mutations()
        if denied:
            return jsonify(denied[0]), denied[1]
        sm = get_state_machine()
        data = request.get_json(silent=True) or {}
        reason = (data.get("reason") or "manual reset from ERROR")[:200]
        actor = request.headers.get("X-Actor", "web_dashboard")
        try:
            st = sm.reset_error(module, actor=actor, reason=reason)
        except UnknownModule as exc:
            return jsonify({"error": str(exc)}), 400
        except InvalidTransition as exc:
            return jsonify({"error": str(exc), "current_state": sm.read(module).state}), 409
        return jsonify(st.to_dict())

    @app.route("/api/control/emergency-stop", methods=["POST"])
    def api_control_emergency_stop():
        """Kill switch global. EXIGE confirmación textual.

        Body: {"confirmation": "APAGAR TODO", "reason": "...", "cooldown_seconds": 300}.
        Si el confirmation no matchea exactamente, rechaza con 400.
        """
        denied = _require_auth_for_mutations()
        if denied:
            return jsonify(denied[0]), denied[1]
        data = request.get_json(silent=True) or {}
        if data.get("confirmation") != "APAGAR TODO":
            return jsonify({
                "error": "Para emergency-stop se requiere "
                         "{'confirmation': 'APAGAR TODO'}",
            }), 400
        cooldown = int(data.get("cooldown_seconds", 300))
        # Sanity: máximo 1 hora de cooldown vía API (más es sospechoso)
        cooldown = max(60, min(cooldown, 3600))
        reason = (data.get("reason") or "manual emergency stop")[:200]
        actor = request.headers.get("X-Actor", "web_dashboard")

        sm = get_state_machine()
        results = sm.emergency_stop(
            actor=actor, reason=reason, cooldown_seconds=cooldown,
        )
        return jsonify({
            "modules": [r.to_dict() for r in results],
            "cooldown_seconds": cooldown,
            "actor": actor,
            "reason": reason,
        })

    # ─────────────────────── Server-Sent Events ─────────────────────────
    # Stream de eventos para push en tiempo real al dashboard (U-04 paso 5).
    # Sin auth — política consistente con el resto del dashboard
    # (bind localhost-only). GET-only, así que CSRF no aplica.

    @app.route("/api/events/stream", methods=["GET"])
    def api_events_stream():
        bus = _get_event_bus()

        def _generate():
            # Hint a clientes que reintenten después de 3s si se cae.
            yield "retry: 3000\n\n"
            # Evento inicial para confirmar conexión activa.
            yield f"data: {_json.dumps({'type': 'hello', 'subscribers': bus.num_subscribers() + 1})}\n\n"
            try:
                for ev in bus.subscribe():
                    yield f"data: {_json.dumps(ev, ensure_ascii=False, default=str)}\n\n"
            except GeneratorExit:
                # Cliente cerró conexión — salir limpio sin loggear como error.
                return

        return Response(
            _generate(),
            mimetype="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",  # útil si hay nginx adelante
                "Connection": "keep-alive",
            },
        )

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
