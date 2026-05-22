import os, json
os.environ['CATASTRO_BOT_DEV_MODE'] = '1'
from src.core.credential_manager import CredentialManager
from src.core.database import Database

cm = CredentialManager()
db = Database(credentials=cm)
exp = db.buscar_por_numero('SEG-2026-001')
eid = exp['id']
print('ID:', eid)

meta = json.loads(exp.get('metadata_json') or '{}')
meta['operador_telefono'] = '50688387310'
meta_str = json.dumps(meta)
print('Guardando metadata:', meta_str)

import sqlite3
# Conexión directa para asegurar commit
from config.settings import DATABASE_PATH
conn = sqlite3.connect(str(DATABASE_PATH))
conn.execute('UPDATE expedientes SET metadata_json=? WHERE id=?', (meta_str, eid))
conn.execute(
    "UPDATE acciones_pendientes SET estado='expirada' WHERE expediente_id=? AND tipo_accion='pago_cliente' AND estado='pendiente'",
    (eid,)
)
conn.commit()
conn.close()

# Verificar que se guardó
exp2 = db.buscar_por_numero('SEG-2026-001')
meta2 = json.loads(exp2.get('metadata_json') or '{}')
print('Verificación — operador_telefono:', meta2.get('operador_telefono', 'NO GUARDADO'))
