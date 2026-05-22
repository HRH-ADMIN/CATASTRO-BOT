"""Healthcheck + watchdog para el bot.

Dos componentes:

  1. **HTTP healthcheck server** — escucha en localhost:9223 (puerto distinto
     al CDP 9222) y responde `/health` con un JSON del estado del sistema.
     Útil para monitores externos (cron, uptime services, otra máquina).

  2. **Watchdog Chrome** — verifica que el Chrome del bot (CDP 9222) está
     vivo. Si no responde, intenta levantarlo de nuevo y notifica al admin.

USO (HTTP):
    curl http://localhost:9223/health
    → 200 OK + {"status": "ok", "chrome_cdp": "alive", "db": "ok", ...}
    → 503 si alguna pieza crítica está caída

USO (watchdog programático):
    from src.utils.healthcheck import verificar_salud, ensure_chrome_running
    salud = verificar_salud(db)
    if not salud["chrome_cdp"]:
        ensure_chrome_running()
"""
from __future__ import annotations
import json
import logging
import os
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Optional

from src.utils.webhook_security import verify_bearer_token

log = logging.getLogger("catastro.healthcheck")


HEALTH_PORT_DEFAULT = 9223
CDP_URL_DEFAULT     = "http://localhost:9222/json/version"
# Si esta env var está definida, /health exige `Authorization: Bearer <token>`.
HEALTH_TOKEN_ENV    = "CATASTRO_HEALTH_TOKEN"


# ── Checks individuales ────────────────────────────────────────────────

def check_chrome_cdp(url: str = CDP_URL_DEFAULT, timeout: float = 2.0) -> dict:
    """¿Está vivo el Chrome del bot con CDP en :9222?"""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            if r.status == 200:
                data = json.loads(r.read().decode("utf-8"))
                return {
                    "alive": True,
                    "browser": data.get("Browser", "?"),
                }
    except Exception as exc:
        return {"alive": False, "error": str(exc)[:120]}
    return {"alive": False, "error": "respuesta no 200"}


def check_db(db_path: Path = Path("data/catastro.db")) -> dict:
    """¿La BD existe y es legible?"""
    try:
        if not db_path.exists():
            return {"ok": False, "error": "archivo no existe"}
        size_kb = db_path.stat().st_size / 1024
        return {"ok": True, "size_kb": round(size_kb, 1)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:120]}


def check_db_reachable(db) -> dict:
    """¿Podemos ejecutar una consulta básica contra la BD?"""
    if db is None:
        return {"reachable": False, "error": "db is None"}
    try:
        # Consulta trivial — solo confirma que el driver responde
        exps = db.listar_expedientes()
        return {"reachable": True, "expedientes_count": len(exps)}
    except Exception as exc:
        return {"reachable": False, "error": str(exc)[:120]}


def check_anomalias_pendientes(db) -> dict:
    """¿Hay anomalías sin resolver recientes?"""
    if db is None:
        return {"count": 0}
    try:
        from src.utils.metricas import anomalias_recientes
        recientes = anomalias_recientes(db, limit=10)
        return {"count": len(recientes), "ultimas": [
            {"ts": a.get("ts", "")[:19], "contexto": a.get("contexto", "?")}
            for a in recientes[:3]
        ]}
    except Exception as exc:
        return {"count": 0, "error": str(exc)[:120]}


def verificar_salud(db=None) -> dict:
    """Compone el reporte completo del estado del sistema."""
    chrome = check_chrome_cdp()
    db_file = check_db()
    db_query = check_db_reachable(db) if db else {"reachable": None,
                                                   "skipped": "no db"}
    anomalias = check_anomalias_pendientes(db) if db else {"count": 0}

    # Status agregado:
    #   ok    → todo bien
    #   warn  → algo funciona pero hay anomalías
    #   error → componente crítico caído
    componentes_criticos_ok = (
        bool(chrome.get("alive"))
        and bool(db_file.get("ok"))
        and (db_query.get("reachable") is not False)
    )
    if not componentes_criticos_ok:
        status = "error"
    elif anomalias.get("count", 0) > 0:
        status = "warn"
    else:
        status = "ok"
    return {
        "status":     status,
        "timestamp":  datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "chrome_cdp": chrome,
        "db":         db_file,
        "db_query":   db_query,
        "anomalias":  anomalias,
    }


# ── HTTP server ─────────────────────────────────────────────────────────

class _HealthHandler(BaseHTTPRequestHandler):
    """HTTP handler que responde GET /health con el estado JSON.

    Si `_required_token` está seteado (ver `CATASTRO_HEALTH_TOKEN`), exige
    `Authorization: Bearer <token>` y rechaza con 401 si no matchea.
    """
    _db = None  # se inyecta desde el server
    _required_token: Optional[str] = None

    def _autorizado(self) -> bool:
        """¿La request está autorizada? True si no se requiere token."""
        if not self._required_token:
            return True
        auth = self.headers.get("Authorization", "")
        return verify_bearer_token(auth, expected=self._required_token)

    def do_GET(self):
        if self.path not in ("/health", "/healthz", "/"):
            self.send_error(404)
            return
        if not self._autorizado():
            # 401 con WWW-Authenticate para clientes legítimos
            self.send_response(401)
            self.send_header("WWW-Authenticate", "Bearer realm=\"catastro-bot\"")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            body = b'{"error":"unauthorized"}'
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        try:
            salud = verificar_salud(self._db)
        except Exception as exc:
            self.send_error(500, str(exc))
            return
        body = json.dumps(salud, ensure_ascii=False, indent=2, default=str)
        code = 200 if salud["status"] in ("ok", "warn") else 503
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body.encode("utf-8"))))
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, format, *args):
        # Silenciar logs HTTP por defecto (van al stderr y son ruidosos)
        log.debug("HTTP %s", format % args)


def start_healthcheck_server(
    db=None, port: int = HEALTH_PORT_DEFAULT,
    token: Optional[str] = None,
) -> Optional[HTTPServer]:
    """Arranca el servidor HTTP en thread daemon. Devuelve el server (o None).

    NO bloquea — el thread es daemon y muere cuando el proceso principal sale.

    Args:
        token: si se provee (o `CATASTRO_HEALTH_TOKEN` está en env), /health
            exige `Authorization: Bearer <token>`. Defensa en profundidad
            extra al bind 127.0.0.1.
    """
    if token is None:
        token = os.environ.get(HEALTH_TOKEN_ENV) or None
    handler_cls = type(
        "_HealthHandlerWithDb", (_HealthHandler,),
        {"_db": db, "_required_token": token},
    )
    try:
        server = HTTPServer(("127.0.0.1", port), handler_cls)
    except OSError as exc:
        log.warning("no se pudo levantar healthcheck en :%d — %s", port, exc)
        return None

    def _run():
        try:
            server.serve_forever()
        except Exception:
            log.exception("healthcheck server crashed")

    thread = threading.Thread(target=_run, name="healthcheck", daemon=True)
    thread.start()
    auth_label = "con bearer token" if token else "sin auth (solo 127.0.0.1)"
    log.info(
        "healthcheck escuchando en http://127.0.0.1:%d/health (%s)",
        port, auth_label,
    )
    return server


# ── Watchdog Chrome ─────────────────────────────────────────────────────

def ensure_chrome_running(*, launcher_script: str = "tools/start_chrome_bot.py",
                          timeout_s: float = 10.0) -> bool:
    """Si Chrome del bot no responde por CDP, intenta levantarlo.

    Returns True si Chrome está vivo (después del intento de relanzar).
    NO ejecuta el launcher si el CDP ya responde.
    """
    if check_chrome_cdp().get("alive"):
        return True

    log.warning("Chrome CDP caído — intentando relanzar via %s", launcher_script)
    try:
        # Lanzar en background (NO esperar — el script hace su lock-cleanup)
        subprocess.Popen(
            [sys.executable, launcher_script],
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) |
                          getattr(subprocess, "DETACHED_PROCESS", 0),
            close_fds=True,
        )
    except Exception as exc:
        log.exception("no se pudo lanzar Chrome: %s", exc)
        return False

    # Esperar hasta `timeout_s` a que CDP responda
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(1.0)
        if check_chrome_cdp().get("alive"):
            log.info("Chrome CDP recuperado")
            return True
    log.warning("Chrome no respondió tras %ds", timeout_s)
    return False


def watchdog_chrome_loop(
    intervalo_segundos: int = 60,
    *,
    launcher_script: str = "tools/start_chrome_bot.py",
    max_relanzos: Optional[int] = None,
    on_relaunch=None,
) -> None:
    """Loop bloqueante que monitorea Chrome CDP y lo relanza si cae.

    Diseñado para correr como proceso/servicio independiente:
        python -m src.utils.healthcheck

    Args:
        intervalo_segundos: cada cuánto chequea (default 60s).
        launcher_script: cómo relanzar Chrome.
        max_relanzos: si se excede, sale (None = infinito).
        on_relaunch: callback opcional cuando se relanza.
    """
    relanzos = 0
    log.info("watchdog_chrome iniciado — chequeando cada %ds", intervalo_segundos)
    while True:
        try:
            estado = check_chrome_cdp()
            if not estado.get("alive"):
                log.warning("Chrome CDP caído — intentando relanzar (intento %d)",
                            relanzos + 1)
                ok = ensure_chrome_running(launcher_script=launcher_script)
                if ok:
                    relanzos += 1
                    if on_relaunch:
                        try: on_relaunch(relanzos)
                        except Exception: log.exception("on_relaunch falló")
                    log.info("Chrome relanzado OK (total relanzos: %d)", relanzos)
                else:
                    log.error("Chrome NO se pudo relanzar")
                if max_relanzos is not None and relanzos >= max_relanzos:
                    log.warning("Alcanzado max_relanzos=%d — saliendo", max_relanzos)
                    return
            time.sleep(intervalo_segundos)
        except KeyboardInterrupt:
            log.info("watchdog detenido por usuario")
            return
        except Exception:
            log.exception("watchdog excepción inesperada — siguiendo")
            time.sleep(intervalo_segundos)


if __name__ == "__main__":
    # Permite correr como `python -m src.utils.healthcheck`
    import argparse, io, logging, sys
    # UTF-8 en stdout para emojis en consola Windows
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                       errors="replace")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=int, default=60,
                        help="Segundos entre chequeos (default 60)")
    parser.add_argument("--max-relanzos", type=int, default=None)
    args = parser.parse_args()
    print(f"🛡️  watchdog_chrome — interval={args.interval}s")
    watchdog_chrome_loop(
        intervalo_segundos=args.interval,
        max_relanzos=args.max_relanzos,
    )


__all__ = [
    "check_chrome_cdp", "check_db", "check_db_reachable",
    "check_anomalias_pendientes", "verificar_salud",
    "start_healthcheck_server", "ensure_chrome_running",
    "watchdog_chrome_loop",
    "HEALTH_PORT_DEFAULT",
]
