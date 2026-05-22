"""Diagnóstico de qué paso en main.py está colgando."""
import os, sys
os.environ['CATASTRO_BOT_DEV_MODE'] = '1'
from pathlib import Path
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

print("1. Importando CredentialManager...")
from src.core.credential_manager import CredentialManager
cm = CredentialManager()
print("   OK")

print("2. Inicializando Database...")
from src.core.database import Database
db = Database(credentials=cm)
db.initialize_schema()
print("   OK")

print("3. Construyendo agentes...")
from src.agents.whatsapp_agent import WhatsAppAgent
from src.agents.drive_agent import DriveAgent
from src.agents.minuta_agent import MinutaAgent
from src.agents.apt_agent import APTAgent
from src.agents.municipality_agent import MunicipalityAgent
from src.agents.rnp_agent import RnpAgent
agents = {
    "drive":    DriveAgent(db, cm),
    "minuta":   MinutaAgent(db, cm),
    "apt":      APTAgent(db, cm),
    "muni":     MunicipalityAgent(db, cm),
    "whatsapp": WhatsAppAgent(db, cm),
    "rnp":      RnpAgent(db, cm),
}
print("   OK")

print("4. Construyendo Orchestrator...")
from src.agents.orchestrator import Orchestrator
orchestrator = Orchestrator(db, cm)
print("   OK")

print("5. Construyendo FolderManager...")
from src.agents.folder_manager import FolderManager
folder_manager = FolderManager(db)
print("   OK")

print("6. Construyendo FileManager...")
from src.agents.file_manager import FileManager
file_manager = FileManager(
    db,
    folder_manager,
    notify_fn=lambda phone, msg: None,
)
print("   OK")

print("7. Iniciando FileManager (watchdog)...")
file_manager.start()
print("   OK")

print("8. Construyendo WhatsAppCommandRouter...")
from src.agents.whatsapp_commands import WhatsAppCommandRouter
command_router = WhatsAppCommandRouter(
    db=db,
    credentials=cm,
    drive_agent=agents["drive"],
    reply_fn=lambda phone, msg: None,
    apt_agent=agents["apt"],
    folder_manager=folder_manager,
    rnp_agent=agents["rnp"],
)
print("   OK")

print("9. Registrando workflows...")
from src.workflows.segregacion import SegregacionWorkflow
from src.workflows.rectificacion import RectificacionWorkflow
from src.workflows.informacion_posesoria import InformacionPosesoriaWorkflow
from src.workflows.reunion_de_fincas import ReunionDeFincasWorkflow
from src.workflows.fincas_completas import FincasCompletasWorkflow
for wf in [
    SegregacionWorkflow(db, agents),
    RectificacionWorkflow(db, agents),
    InformacionPosesoriaWorkflow(db, agents),
    ReunionDeFincasWorkflow(db, agents),
    FincasCompletasWorkflow(db, agents),
]:
    orchestrator.register_workflow(wf)
print("   OK")

print("10. Creando BlockingScheduler...")
from apscheduler.schedulers.blocking import BlockingScheduler
scheduler = BlockingScheduler(timezone="America/Costa_Rica")
print("   OK")

print("11. Registrando jobs en scheduler...")
from src.scheduler.tasks import register_jobs
orchestrator.whatsapp.set_command_router(command_router)
register_jobs(scheduler, orchestrator)
print("   OK")

print("12. Iniciando scheduler (Ctrl+C para detener)...")
try:
    scheduler.start()
except (KeyboardInterrupt, SystemExit):
    print("   Detenido por usuario")
except Exception as e:
    import traceback
    print(f"   CRASH: {e}")
    traceback.print_exc()

print("\nTodo OK — el bot debería iniciar sin problemas.")
file_manager.stop()
