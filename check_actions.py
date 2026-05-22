import os, json
os.environ['CATASTRO_BOT_DEV_MODE'] = '1'
from src.core.credential_manager import CredentialManager
from src.core.database import Database

cm = CredentialManager()
db = Database(credentials=cm)
exp = db.buscar_por_numero('SEG-2026-001')
eid = exp['id']

# Ver metadata
meta = json.loads(exp.get('metadata_json') or '{}')
print('metadata:', json.dumps(meta, indent=2))
print('estado_actual:', exp['estado_actual'])

# Ver acciones pendientes
with db.connect() as conn:
    rows = conn.execute(
        "SELECT id, tipo_accion, estado, whatsapp_message_id, fecha_solicitud FROM acciones_pendientes WHERE expediente_id=? ORDER BY fecha_solicitud DESC LIMIT 5",
        (eid,)
    ).fetchall()

print('\nUltimas 5 acciones:')
for r in rows:
    print(f"  {r['fecha_solicitud']} | {r['tipo_accion']} | {r['estado']} | msg_id={r['whatsapp_message_id']}")
