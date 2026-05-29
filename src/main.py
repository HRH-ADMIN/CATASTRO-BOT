"""Entry point de catastro-bot.

Modos de ejecución:
  python -m src.main              # equivalente a `run`
  python -m src.main run          # servicio en foreground (loop hasta SIGTERM)
  python -m src.main once [job]   # ejecutar un tick y salir (debugging/CI)

Diseño runtime:
  - Guards de arranque (SIMULAR_APT) — falla rápido si la config es peligrosa.
  - `BackgroundScheduler` corre los 8 jobs registrados en src/scheduler/tasks.py
    con gating por `control_state` (toggle ON/OFF desde la web).
  - `control_tick` (cada 30s) actualiza el heartbeat para que la web detecte
    si el bot está vivo.
  - Signal handlers SIGTERM/SIGBREAK/SIGINT → shutdown ordenado del scheduler,
    cierre del FileManager, salida limpia con código 0.
  - El thread principal bloquea en `_shutdown_event.wait()`, lo que permite
    que el dashboard Flask (tarea #7) tome el thread principal con
    `waitress.serve()` cuando lo integremos.

Convivencia con `windows_service.py`:
  El servicio Windows usa la misma `register_jobs` y comparte el wiring de
  agentes/workflows. Ambos entry points construyen los mismos componentes —
  por ahora con cierta duplicación que se resuelve en una iteración futura.
"""
from __future__ import annotations

# Cargar .env antes que cualquier otro import (ej: CATASTRO_BOT_DEV_MODE=1)
from pathlib import Path as _Path
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(_Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

import argparse
import io
import signal
import sys
import threading
from datetime import datetime, timezone
from typing import Optional


def _redirect_stdio_if_pythonw() -> None:
    """Bajo `pythonw.exe` (sin consola), sys.stdout/stderr son `None` y
    cualquier `print()` o traceback no manejado se PIERDE silenciosamente.
    Redirigimos a `logs/scheduler.stdout.log` y `logs/scheduler.stderr.log`
    para que el operador pueda ver pánicos via /config/runtime.

    No interfiere con el logging.RotatingFileHandler — son canales distintos.

    Plan: PLAN_MEJORAS Sprint 1 / U-02 paso C.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return  # estamos bajo python.exe con consola, no hace falta
    try:
        from config.settings import LOGS_DIR
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        # Apertura en modo append, line-buffered, encoding utf-8
        sys.stdout = open(LOGS_DIR / "scheduler.stdout.log",
                          "a", encoding="utf-8", buffering=1)
        sys.stderr = open(LOGS_DIR / "scheduler.stderr.log",
                          "a", encoding="utf-8", buffering=1)
        ts = datetime.now(timezone.utc).isoformat()
        sys.stderr.write(f"\n=== {ts} pythonw startup ===\n")
        sys.stderr.flush()
    except Exception:
        # Si la redirección falla, no rompemos el arranque del bot —
        # solo perderemos prints/tracebacks no manejados.
        pass


_redirect_stdio_if_pythonw()

from apscheduler.schedulers.background import BackgroundScheduler

from src.agents.apt_agent import APTAgent
from src.agents.drive_agent import DriveAgent
from src.agents.file_manager import FileManager
from src.agents.folder_manager import FolderManager
from src.agents.minuta_agent import MinutaAgent
from src.agents.municipality_agent import MunicipalityAgent
from src.agents.orchestrator import Orchestrator
from src.agents.rnp_agent import RnpAgent
from src.agents.whatsapp_agent import WhatsAppAgent
from src.agents.whatsapp_commands import WhatsAppCommandRouter
from src.core.control_state import get_manager as get_control_manager
from src.core.credential_manager import CredentialManager
from src.core.database import Database
from src.core.startup_guards import assert_safe_simular_apt
from src.scheduler.tasks import register_jobs
from src.utils.logger import get_logger
from src.workflows.fincas_completas import FincasCompletasWorkflow
from src.workflows.informacion_posesoria import InformacionPosesoriaWorkflow
from src.workflows.rectificacion import RectificacionWorkflow
from src.workflows.reunion_de_fincas import ReunionDeFincasWorkflow
from src.workflows.segregacion import SegregacionWorkflow


# Intervalo del control_tick (lectura de control.json + heartbeat).
# 30s da granularidad razonable sin sobrecargar el disco.
_CONTROL_TICK_SECONDS = 30


# ── Wiring ─────────────────────────────────────────────────────────────────

def build_agents(db: Database, cm: CredentialManager) -> dict:
    return {
        "drive":    DriveAgent(db, cm),
        "minuta":   MinutaAgent(db, cm),
        "apt":      APTAgent(db, cm),
        "muni":     MunicipalityAgent(db, cm),
        "whatsapp": WhatsAppAgent(db, cm),
        "rnp":      RnpAgent(db, cm),
    }


def build_workflows(db: Database, agents: dict) -> list:
    return [
        SegregacionWorkflow(db, agents),
        RectificacionWorkflow(db, agents),
        InformacionPosesoriaWorkflow(db, agents),
        ReunionDeFincasWorkflow(db, agents),
        FincasCompletasWorkflow(db, agents),
    ]


# ── Runtime ────────────────────────────────────────────────────────────────

# Singleton del orchestrator — seteado por BotRuntime.setup() para que
# endpoints REST (ej. /api/apt-sync-now) puedan disparar jobs reusando
# las sesiones CDP/agents del propio bot vivo.
# Plan: APT-FULL Fase F.
_orchestrator_singleton: "Optional[Orchestrator]" = None


class BotRuntime:
    """Encapsula el ciclo de vida del bot. Permite testear sin sys.exit."""

    def __init__(self, log=None, *, web_port: int = 9224, web_enabled: bool = True):
        self.log = log or get_logger("main")
        self._shutdown_event = threading.Event()
        self._scheduler: Optional[BackgroundScheduler] = None
        self._file_manager: Optional[FileManager] = None
        self._control = get_control_manager()
        self._web_port = web_port
        self._web_enabled = web_enabled
        self._web_server = None

    # ---------- setup ----------

    def setup(self) -> tuple[Orchestrator, FileManager]:
        cm = CredentialManager()
        db = Database(credentials=cm)
        db.initialize_schema()

        agents = build_agents(db, cm)
        orchestrator = Orchestrator(db, cm)
        # Exponer el orchestrator como singleton del módulo para que el
        # endpoint /api/apt-sync-now (Flask) pueda dispararlo manualmente
        # sin abrir una segunda sesión CDP.
        # Plan: APT-FULL Fase F.
        global _orchestrator_singleton
        _orchestrator_singleton = orchestrator
        # WhatsAppAgent vive dentro del orchestrator; reutilizamos esa instancia.
        agents["whatsapp"] = orchestrator.whatsapp

        folder_manager = FolderManager(db)
        file_manager = FileManager(
            db,
            folder_manager,
            notify_fn=lambda phone, msg: orchestrator.whatsapp.enviar_mensaje(
                phone, msg
            ),
        )

        command_router = WhatsAppCommandRouter(
            db=db,
            credentials=cm,
            drive_agent=agents["drive"],
            reply_fn=lambda phone, msg: orchestrator.whatsapp.enviar_mensaje(
                phone, msg
            ),
            apt_agent=agents["apt"],
            folder_manager=folder_manager,
            rnp_agent=agents["rnp"],
        )
        orchestrator.whatsapp.set_command_router(command_router)

        for wf in build_workflows(db, agents):
            orchestrator.register_workflow(wf)

        self._file_manager = file_manager
        return orchestrator, file_manager

    # ---------- run modes ----------

    def run_once(self, job: str = "orchestrator-tick") -> int:
        """Ejecuta un tick puntual de un job específico y sale.

        Útil para debugging/CI: `python -m src.main once orchestrator-tick`.
        No arranca scheduler ni file manager watchdog.
        """
        orchestrator, _file_manager = self.setup()
        if job == "orchestrator-tick":
            stats = orchestrator.tick()
            self.log.info("once tick stats=%s", stats)
            return 0
        self.log.error("once: job %r desconocido", job)
        return 1

    def run_forever(self) -> int:
        """Modo servicio: arranca scheduler, FileManager, espera señal."""
        orchestrator, file_manager = self.setup()
        file_manager.start()

        self._scheduler = BackgroundScheduler(timezone="America/Costa_Rica")
        register_jobs(
            self._scheduler, orchestrator,
            control_manager=self._control,
        )

        # control_tick — escribe heartbeat para que la web sepa que estamos vivos.
        self._scheduler.add_job(
            self._control.write_heartbeat,
            trigger="interval",
            seconds=_CONTROL_TICK_SECONDS,
            id="control-heartbeat",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )

        # Watchdog del FileManager: relanzar si murió
        self._scheduler.add_job(
            lambda: (
                file_manager.start()
                if not file_manager.is_running() else None
            ),
            trigger="interval",
            minutes=5,
            id="watchdog_health",
            replace_existing=True,
        )

        # ── U-02: Tracking de procesos vivos ──────────────────────────
        # Registra este proceso (scheduler) en runtime_processes y
        # programa heartbeat cada 10s + monitor que detecta procesos
        # colgados/muertos cada 30s.
        from config.settings import DATABASE_PATH, LOGS_DIR
        from src.utils import runtime_processes as _rp
        self._rp_log_path = str(LOGS_DIR / "scheduler.log")
        try:
            _rp.register_process(
                DATABASE_PATH,
                process_name="scheduler",
                log_file_path=self._rp_log_path,
            )
        except Exception:
            self.log.exception("runtime_processes: register falló — sigo")

        def _scheduler_heartbeat():
            try:
                _rp.heartbeat(DATABASE_PATH, process_name="scheduler")
            except Exception:
                self.log.exception("runtime_processes: heartbeat falló")

        self._scheduler.add_job(
            _scheduler_heartbeat,
            trigger="interval",
            seconds=10,
            id="runtime-heartbeat-scheduler",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )

        def _process_monitor():
            try:
                counts = _rp.run_monitor_pass(DATABASE_PATH)
                if counts["hanging"] or counts["dead"]:
                    self.log.warning(
                        "process-monitor: alive=%d hanging=%d dead=%d",
                        counts["alive"], counts["hanging"], counts["dead"],
                    )
            except Exception:
                self.log.exception("process-monitor falló")

        self._scheduler.add_job(
            _process_monitor,
            trigger="interval",
            seconds=30,
            id="process-monitor",
            max_instances=1,
            coalesce=True,
            replace_existing=True,
        )

        self._install_signal_handlers()

        self._scheduler.start()
        # Heartbeat inicial — para que la web vea estado actualizado de inmediato
        self._control.write_heartbeat()

        # ── Bootstrap U-03: marcar módulos como RUNNING en la state machine ──
        # Si la state machine está vacía (BD fresca o primera vez post-U-03),
        # auto-iniciar los módulos así el gating no bloquea todo el bot.
        # Para módulos individuales (apt, muni, whatsapp, drive_backup, rnp)
        # el operador después puede pararlos vía /config/control.
        try:
            from src.core.state_machine import get_state_machine, KNOWN_MODULES
            sm = get_state_machine()
            for mod in KNOWN_MODULES:
                st = sm.read(mod)
                if st.state == "STOPPED":
                    # Transición STOPPED → STARTING → RUNNING en bootstrap.
                    sm.start(mod, actor="bootstrap", reason="bot startup",
                             force=True)
                    sm.mark_running(mod, actor="bootstrap")
                elif st.state == "ERROR":
                    # ERROR persistente del run anterior — el operador debe
                    # resetear manualmente. NO auto-recuperamos.
                    self.log.warning(
                        "module %s quedó en ERROR del run anterior: %s",
                        mod, st.error_message,
                    )
            self.log.info("state_machine: bootstrap completado (%d módulos)",
                          len(KNOWN_MODULES))
        except Exception:
            self.log.exception("bootstrap state_machine falló — "
                               "el bot sigue corriendo en modo legacy")

        # Dashboard Flask — corre en thread daemon, no bloquea el main
        if self._web_enabled:
            try:
                from src.web.app import start_web_server
                self._web_server = start_web_server(port=self._web_port)
                self._web_server.run_in_background()
            except Exception:
                self.log.exception(
                    "no se pudo arrancar el dashboard web — "
                    "el bot sigue corriendo sin él"
                )

        n_jobs = len(self._scheduler.get_jobs())
        self.log.info(
            "catastro-bot RUNNING — %d jobs · 5 workflows · "
            "control.json gate activo · dashboard %s",
            n_jobs,
            self._web_server.url if self._web_server else "OFF",
        )

        # Bloquea hasta señal — el web ya corre en background daemon thread.
        try:
            self._shutdown_event.wait()
        except KeyboardInterrupt:
            pass

        self._shutdown()
        return 0

    # ---------- shutdown ----------

    def _install_signal_handlers(self) -> None:
        """SIGTERM/SIGINT + SIGBREAK (Windows) → shutdown ordenado."""
        def _handler(signum, _frame):
            self.log.info("señal %s recibida — iniciando shutdown", signum)
            self._shutdown_event.set()

        for sig_name in ("SIGTERM", "SIGINT", "SIGBREAK"):
            sig = getattr(signal, sig_name, None)
            if sig is None:
                continue
            try:
                signal.signal(sig, _handler)
            except (ValueError, OSError):
                # En threads no-main signal puede no estar disponible
                pass

    def _shutdown(self) -> None:
        self.log.info("shutdown iniciado")
        if self._web_server is not None:
            try:
                self._web_server.shutdown()
            except Exception:
                self.log.exception("error apagando web_server")
        if self._scheduler is not None and self._scheduler.running:
            try:
                self._scheduler.shutdown(wait=True)
            except Exception:
                self.log.exception("error apagando scheduler")
        if self._file_manager is not None:
            try:
                self._file_manager.stop()
            except Exception:
                self.log.exception("error apagando file_manager")
        # U-02: marcar el proceso scheduler como dead graceful
        try:
            from config.settings import DATABASE_PATH
            from src.utils import runtime_processes as _rp
            _rp.mark_stopped(DATABASE_PATH, process_name="scheduler",
                             reason="graceful")
        except Exception:
            self.log.exception("runtime_processes: mark_stopped falló")
        self.log.info("catastro-bot DETENIDO")


# ── CLI ────────────────────────────────────────────────────────────────────

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="catastro-bot")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("run", help="Servicio en foreground (default)")
    once_p = sub.add_parser("once", help="Ejecutar un tick puntual y salir")
    once_p.add_argument(
        "job", nargs="?", default="orchestrator-tick",
        help="Job a ejecutar (default: orchestrator-tick)",
    )
    args = parser.parse_args(argv)
    cmd = args.cmd or "run"

    # Guard de arranque — falla rápido si la config es peligrosa
    assert_safe_simular_apt()

    runtime = BotRuntime()
    if cmd == "run":
        return runtime.run_forever()
    if cmd == "once":
        return runtime.run_once(args.job)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
