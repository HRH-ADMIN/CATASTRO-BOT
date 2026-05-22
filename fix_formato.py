"""Elimina archivos .zip de la BD y resetea estado a pago_cliente_confirmado."""
import os, sqlite3
os.environ['CATASTRO_BOT_DEV_MODE'] = '1'
from config.settings import DATABASE_PATH
from src.core.credential_manager import CredentialManager
from src.core.database import Database

cm = CredentialManager()
db = Database(credentials=cm)
exp = db.buscar_por_numero('SEG-2026-001')
eid = exp['id']
print('Expediente:', eid)
print('Estado actual:', exp['estado_actual'])

conn = sqlite3.connect(str(DATABASE_PATH))

# Ver archivos registrados
rows = conn.execute(
    "SELECT id, nombre_original, tipo_archivo FROM archivos WHERE expediente_id=?", (eid,)
).fetchall()
print('\nArchivos en BD:')
for r in rows:
    print(f'  [{r[0][:8]}] {r[1]} | tipo={r[2]}')

# Eliminar archivos .zip
n = conn.execute(
    "DELETE FROM archivos WHERE expediente_id=? AND nombre_original LIKE '%.zip'", (eid,)
).rowcount
print(f'\nEliminados: {n} archivo(s) .zip')

# Resetear estado a pago_cliente_confirmado para que vuelva a validar
conn.execute(
    "UPDATE expedientes SET estado_actual='pago_cliente_confirmado' WHERE id=?", (eid,)
)
conn.commit()
conn.close()

# Verificar
exp2 = db.buscar_por_numero('SEG-2026-001')
print('Estado nuevo:', exp2['estado_actual'])
print('\nListo — en el proximo tick el bot valida los archivos restantes.')
