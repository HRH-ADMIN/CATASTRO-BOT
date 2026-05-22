"""Limpia acción registrar_entero pendiente para que el bot use el nuevo código."""
import os, sqlite3
os.environ['CATASTRO_BOT_DEV_MODE'] = '1'
from config.settings import DATABASE_PATH
from src.core.credential_manager import CredentialManager
from src.core.database import Database

cm = CredentialManager()
db = Database(credentials=cm)
exp = db.buscar_por_numero('SEG-2026-001')
eid = exp['id']

conn = sqlite3.connect(str(DATABASE_PATH))
n = conn.execute(
    "UPDATE acciones_pendientes SET estado='expirada' "
    "WHERE expediente_id=? AND tipo_accion='registrar_entero' AND estado='pendiente'",
    (eid,)
).rowcount
# También limpiar numero_entero de metadata por si quedó algo incorrecto
import json
meta = json.loads(exp.get('metadata_json') or '{}')
meta.pop('numero_entero', None)
meta.pop('entero_confirmado', None)
conn.execute('UPDATE expedientes SET metadata_json=? WHERE id=?',
             (json.dumps(meta), eid))
conn.commit()
conn.close()
print(f'Acciones expiradas: {n}')
print('Metadata limpiada — bot extraerá entero automáticamente en próximo tick')
