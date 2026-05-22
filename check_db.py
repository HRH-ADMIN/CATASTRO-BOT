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

db_path = Path("data/catastro.db")
if not db_path.exists():
    print("BD de produccion NO existe todavia")
    print("El bot live crea la BD en data/catastro.db")
    sys.exit(0)

db = ProdDB(path=db_path, credentials=cm)

exps = db.listar_expedientes()
print(f"Expedientes en BD produccion: {len(exps)}")
for e in exps:
    print(f"  [{e['numero_expediente']}] {e['tipo_plano']} | estado={e['estado_actual']} | cliente={e.get('nombre_cliente')}")

print()
usuarios = db.listar_usuarios()
print(f"Usuarios registrados: {len(usuarios)}")
for u in usuarios:
    print(f"  tel={u['telefono']} | nombre={u['nombre']} | rol={u['rol']}")
