"""Windows Service para catastro-bot — Opción A.

Instala y gestiona el bot como un servicio de Windows usando pywin32.

Uso desde línea de comandos (requiere privilegios de Administrador):

  # Instalar el servicio
  python -m src.service.windows_service install

  # Iniciar
  python -m src.service.windows_service start

  # Detener
  python -m src.service.windows_service stop

  # Eliminar
  python -m src.service.windows_service remove

  # Estado actual
  python -m src.service.windows_service status

  # Depuración (corre en primer plano como proceso normal)
  python -m src.service.windows_service debug

El servicio se configura como inicio automático (AUTO_START) con
recuperación automática ante fallos (reinicio tras 30 s, máx 3 veces).

Prerequisitos:
  pip install pywin32
  python -c "import win32serviceutil"   # verificar instalación
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

# Guardar directorio raíz para importaciones relativas
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# ── Compatibilidad: permitir importar el módulo sin pywin32 ───────────────────
try:
    import win32service
    import win32serviceutil
    import win32event
    import servicemanager
    _WIN32_AVAILABLE = True
except ImportError:
    _WIN32_AVAILABLE = False


SERVICE_NAME         = "CatastroBotSvc"
SERVICE_DISPLAY_NAME = "catastro-bot (Catastro Nacional CR)"
SERVICE_DESCRIPTION  = (
    "Multi-agent workflow para trámites de planos topográficos "
    "ante el Catastro Nacional y la Municipalidad de San Ramón, "
    "Costa Rica. Gestiona APT, RNP digital, correo, WhatsApp y Drive."
)


if _WIN32_AVAILABLE:

    class CatastroBotService(win32serviceutil.ServiceFramework):
        _svc_name_         = SERVICE_NAME
        _svc_display_name_ = SERVICE_DISPLAY_NAME
        _svc_description_  = SERVICE_DESCRIPTION

        def __init__(self, args):
            win32serviceutil.ServiceFramework.__init__(self, args)
            self._stop_event = win32event.CreateEvent(None, 0, 0, None)
            self._scheduler  = None

        # ── Ciclo de vida ──────────────────────────────────────────────────

        def SvcStop(self):
            """Invocado por SCM cuando se solicita detener el servicio."""
            servicemanager.LogInfoMsg(f"{SERVICE_NAME}: recibida señal STOP")
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            if self._scheduler and self._scheduler.running:
                self._scheduler.shutdown(wait=False)
            win32event.SetEvent(self._stop_event)

        def SvcDoRun(self):
            """Punto de entrada principal del servicio."""
            servicemanager.LogInfoMsg(f"{SERVICE_NAME}: iniciando…")
            self.ReportServiceStatus(win32service.SERVICE_RUNNING)
            try:
                self._run()
            except Exception as exc:
                servicemanager.LogErrorMsg(
                    f"{SERVICE_NAME}: error fatal — {exc}"
                )
                raise

        # ── Lógica del bot ────────────────────────────────────────────────

        def _run(self) -> None:
            """Construye y arranca el bot en el contexto del servicio."""
            # Agregar raíz del proyecto al path si no está
            if str(_PROJECT_ROOT) not in sys.path:
                sys.path.insert(0, str(_PROJECT_ROOT))

            from src.agents.orchestrator import Orchestrator
            from src.core.credential_manager import CredentialManager
            from src.core.database import Database
            from src.agents.drive_agent import DriveAgent
            from src.agents.apt_agent import APTAgent
            from src.agents.rnp_agent import RnpAgent
            from src.agents.whatsapp_agent import WhatsAppAgent
            from src.agents.municipality_agent import MunicipalityAgent
            from src.agents.minuta_agent import MinutaAgent
            from src.agents.folder_manager import FolderManager
            from src.agents.file_manager import FileManager
            from src.agents.whatsapp_commands import WhatsAppCommandRouter
            from src.scheduler.tasks import register_jobs
            from src.workflows.fincas_completas import FincasCompletasWorkflow
            from src.workflows.informacion_posesoria import InformacionPosesoriaWorkflow
            from src.workflows.rectificacion import RectificacionWorkflow
            from src.workflows.reunion_de_fincas import ReunionDeFincasWorkflow
            from src.workflows.segregacion import SegregacionWorkflow
            from apscheduler.schedulers.background import BackgroundScheduler

            cm = CredentialManager()
            db = Database(credentials=cm)
            db.initialize_schema()

            agents = {
                "drive":    DriveAgent(db, cm),
                "minuta":   MinutaAgent(db, cm),
                "apt":      APTAgent(db, cm),
                "muni":     MunicipalityAgent(db, cm),
                "whatsapp": WhatsAppAgent(db, cm),
                "rnp":      RnpAgent(db, cm),
            }

            orchestrator = Orchestrator(db, cm)
            agents["whatsapp"] = orchestrator.whatsapp

            folder_manager = FolderManager(db)
            file_manager = FileManager(
                db, folder_manager,
                notify_fn=lambda phone, msg: orchestrator.whatsapp.enviar_mensaje(
                    phone, msg
                ),
            )
            file_manager.start()

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

            for wf in [
                SegregacionWorkflow(db, agents),
                RectificacionWorkflow(db, agents),
                InformacionPosesoriaWorkflow(db, agents),
                ReunionDeFincasWorkflow(db, agents),
                FincasCompletasWorkflow(db, agents),
            ]:
                orchestrator.register_workflow(wf)

            # BackgroundScheduler para correr sin bloquear el hilo del servicio
            scheduler = BackgroundScheduler(timezone="America/Costa_Rica")
            self._scheduler = scheduler

            register_jobs(scheduler, orchestrator)

            # Watchdog del FileManager
            scheduler.add_job(
                lambda: (
                    file_manager.start()
                    if not file_manager.is_running() else None
                ),
                "interval",
                minutes=5,
                id="watchdog_health",
            )

            scheduler.start()
            servicemanager.LogInfoMsg(
                f"{SERVICE_NAME}: scheduler arrancado — "
                f"{len(scheduler.get_jobs())} jobs activos"
            )

            # Esperar señal STOP del SCM
            while True:
                result = win32event.WaitForSingleObject(
                    self._stop_event, 5000  # check cada 5s
                )
                if result == win32event.WAIT_OBJECT_0:
                    break

            file_manager.stop()
            servicemanager.LogInfoMsg(f"{SERVICE_NAME}: detenido")


# ── Gestión de recuperación automática ────────────────────────────────────────

def _configure_recovery(service_name: str) -> None:
    """Configura recuperación automática: reiniciar tras fallos."""
    import subprocess
    # sc failure CatastroBotSvc reset=86400 actions=restart/30000/restart/60000/restart/120000
    cmd = [
        "sc", "failure", service_name,
        "reset=", "86400",  # reset contador tras 24h sin fallos
        "actions=", "restart/30000/restart/60000/restart/120000",
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        print(f"✅ Recuperación automática configurada para {service_name}")
    except Exception as exc:
        print(f"⚠️  No se pudo configurar recuperación (requiere admin): {exc}")


# ── CLI ────────────────────────────────────────────────────────────────────────

def _cli() -> None:
    if not _WIN32_AVAILABLE:
        print(
            "❌ pywin32 no disponible.\n"
            "Instale con:  pip install pywin32\n"
            "Luego:        python -m pip install --upgrade pywin32\n"
            "Reinicie el intérprete de Python."
        )
        sys.exit(1)

    if len(sys.argv) < 2:
        print(
            f"Uso: python -m src.service.windows_service "
            f"[install|start|stop|remove|status|debug]"
        )
        sys.exit(0)

    cmd = sys.argv[1].lower()

    if cmd == "status":
        try:
            status = win32serviceutil.QueryServiceStatus(SERVICE_NAME)
            states = {
                1: "STOPPED", 2: "START_PENDING", 3: "STOP_PENDING",
                4: "RUNNING", 5: "CONTINUE_PENDING", 6: "PAUSE_PENDING",
                7: "PAUSED",
            }
            print(f"{SERVICE_NAME}: {states.get(status[1], 'DESCONOCIDO')}")
        except Exception as exc:
            print(f"❌ {exc}")
        return

    if cmd == "install":
        win32serviceutil.InstallService(
            CatastroBotService._svc_reg_class_,
            SERVICE_NAME,
            SERVICE_DISPLAY_NAME,
            startType=win32service.SERVICE_AUTO_START,
            description=SERVICE_DESCRIPTION,
        )
        _configure_recovery(SERVICE_NAME)
        print(f"✅ Servicio {SERVICE_NAME!r} instalado.")
        return

    if cmd == "debug":
        # Correr en primer plano (útil para desarrollo)
        print(f"🔧 {SERVICE_NAME} corriendo en modo DEBUG (Ctrl+C para detener)…")
        import importlib
        main_mod = importlib.import_module("src.main")
        main_mod.main()
        return

    # Delegar install/start/stop/remove a win32serviceutil
    win32serviceutil.HandleCommandLine(CatastroBotService)


if __name__ == "__main__":
    _cli()
