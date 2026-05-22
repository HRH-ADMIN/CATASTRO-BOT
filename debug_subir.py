import sys, io, sqlite3, contextlib
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from src.core.credential_manager import CredentialManager
from src.core.database import Database

cm = CredentialManager()

class ProdDB(Database):
    @contextlib.contextmanager
    def connect(self):
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
        finally:
            conn.close()

db = ProdDB(path=Path("data/catastro.db"), credentials=cm)

# Todos los numeros exactos en la BD
print("=== Numeros exactos en BD (repr) ===")
with db.connect() as conn:
    rows = conn.execute("SELECT numero_expediente, nombre_cliente FROM expedientes").fetchall()
    for r in rows:
        print(f"  repr: {r['numero_expediente']!r}  |  cliente: {r['nombre_cliente']}")

print()
# Probar buscar cada uno
print("=== Test buscar_por_numero ===")
for r in rows:
    num = r["numero_expediente"]
    resultado = db.buscar_por_numero(num)
    print(f"  buscar_por_numero({num!r}) -> {'ENCONTRADO' if resultado else 'NO ENCONTRADO'}")

print()
# Simular el comando SUBIR completo
print("=== Simulacion comando SUBIR ===")
from src.agents.drive_agent import DriveAgent
from src.agents.whatsapp_commands import WhatsAppCommandRouter
from src.agents.folder_manager import FolderManager

respuestas = []
def _reply(phone, msg):
    respuestas.append(msg)
    print(f"  Respuesta: {msg}")

router = WhatsAppCommandRouter(
    db=db, credentials=cm,
    drive_agent=DriveAgent(db, cm, files_root=Path("data/files")),
    reply_fn=_reply,
    folder_manager=FolderManager(db),
)

for r in rows:
    num = r["numero_expediente"]
    respuestas.clear()
    print(f"\n  >> SUBIR {num}")
    router.handle(sender_phone="50663089219", text=f"SUBIR {num}")
