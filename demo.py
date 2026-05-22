from __future__ import annotations
import contextlib, io, json, secrets, sqlite3, sys, tempfile, time, zipfile
from pathlib import Path

# UTF-8 en consola Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

def ok(m):    print(f"[OK]  {m}")
def info(m):  print(f"[..]  {m}")
def warn(m):  print(f"[!!]  {m}")
def err(m):   print(f"[XX]  {m}")
def titulo(m): print(f"\n{'='*60}\n  {m}\n{'='*60}")
def sub(m):   print(f"\n>> {m}")

# ============================================================
# Setup: BD sqlite3 sin cifrar + credenciales falsas
# ============================================================

class _FakeCreds:
    def get_or_create_db_key(self): return secrets.token_bytes(32)
    def get_db_key(self):           return secrets.token_bytes(32)
    def is_operator(self, p):       return True
    def get_operators(self):        return {"50688387310"}
    def get_anthropic_key(self):    raise RuntimeError("no configurada")
    def get_apt(self):              return ("u", "p")

from src.core.database import Database

class DemoDatabase(Database):
    @contextlib.contextmanager
    def connect(self):
        conn = sqlite3.connect(str(self.path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
        finally:
            conn.close()

DEMO_DIR = Path(tempfile.mkdtemp(prefix="catastro_demo_"))
info(f"Directorio temporal: {DEMO_DIR}")

db = DemoDatabase(path=DEMO_DIR / "demo.db", credentials=_FakeCreds())
db.initialize_schema()
ok("Base de datos inicializada")

# ============================================================
titulo("1 - NUEVO CONTRATO (comando WhatsApp simulado)")
# ============================================================

from src.agents.folder_manager import FolderManager
from src.agents.whatsapp_commands import WhatsAppCommandRouter
from src.agents.drive_agent import DriveAgent

MEGA_ROOT = DEMO_DIR / "mega"
MEGA_ROOT.mkdir()
folder_manager = FolderManager(db, mega_root=MEGA_ROOT)

mensajes = []
def _reply(phone, msg):
    mensajes.append((phone, msg))

db.crear_usuario(telefono="50688387310", nombre="Alonso Demo", rol="admin", actor="demo")
drive = DriveAgent(db, _FakeCreds(), files_root=DEMO_DIR / "files")

router = WhatsAppCommandRouter(
    db=db,
    credentials=_FakeCreds(),
    drive_agent=drive,
    reply_fn=_reply,
    folder_manager=folder_manager,
)

CMD = (
    "NUEVO CONTRATO\n"
    "Tipo: segregacion\n"
    "Cliente: Maria Fernandez Quesada\n"
    "Cedula: 1-1234-5678\n"
    "Proyecto: Finca Las Palmas\n"
    "Finca: 6123456\n"
    "Protocolo: APT-2026-789\n"
    "Planos: 2\n"
    "Municipalidad: San Ramon"
)

sub("Enviando: NUEVO CONTRATO")
for l in CMD.splitlines(): print(f"   {l}")
router.handle(sender_phone="50688387310", text=CMD)

if mensajes:
    sub("Respuesta del bot:")
    for l in mensajes[-1][1].splitlines(): print(f"   {l}")

exps = db.listar_expedientes()
if not exps:
    err("No se creo expediente"); sys.exit(1)
exp = exps[0]
EID    = exp["id"]
NUMERO = exp["numero_expediente"]
ok(f"Expediente en BD: {NUMERO}  (id={EID[:8]}...)")

# ============================================================
titulo("2 - ESTRUCTURA DE CARPETAS MEGA")
# ============================================================

mega_path = db.get_mega_path(EID)
if not mega_path:
    raiz = folder_manager.crear_estructura_expediente(EID)
    mega_path = db.get_mega_path(EID)

raiz = Path(mega_path)
ok(f"Ruta Mega: {raiz}")

sub("Arbol de carpetas:")
for p in sorted(raiz.rglob("*")):
    rel = p.relative_to(MEGA_ROOT)
    indent = "  " * (len(rel.parts) - 1)
    tipo = "DIR" if p.is_dir() else "---"
    print(f"   {indent}[{tipo}] {p.name}")

sub("EXPEDIENTE.txt:")
txt = (raiz / "EXPEDIENTE.txt").read_text(encoding="utf-8")
for l in txt.splitlines(): print(f"   {l}")

# ============================================================
titulo("3 - VALIDACION DE ARCHIVOS")
# ============================================================

import fitz
from src.agents.file_manager import (
    validar_pdf_anverso, validar_pdf_entero, validar_zip_shape,
    validar_archivo, formatear_reporte, ValidacionResultado, _clasificar_pdf
)

subir_dir = raiz / "03_APT_R1" / "SUBIR"

# PDF anverso B&N
sub("Creando PDF anverso B&N...")
pdf_bn = subir_dir / "plano_FINAL.pdf"
doc = fitz.open()
page = doc.new_page(width=595, height=842)
page.insert_text((50, 100), f"PLANO CATASTRAL  Exp: {NUMERO}", fontsize=14)
page.insert_text((50, 150), "Finca: 6123456 - San Ramon", fontsize=10)
doc.save(str(pdf_bn), garbage=4, deflate=True); doc.close()
r = validar_pdf_anverso(pdf_bn)
ok(f"PDF B&N: {r.mensaje}  ({r.final_kb} KB)") if r.ok else err(r.mensaje)

# PDF con color
sub("Creando PDF con color (debe convertirse a B&N)...")
pdf_col = subir_dir / "plano_v2.pdf"
doc = fitz.open()
page = doc.new_page(width=595, height=842)
page.draw_rect(fitz.Rect(50,50,250,150), color=(0.8,0.1,0.1), fill=(0.8,0.1,0.1))
page.insert_text((50, 200), "Texto con rojo", fontsize=12)
doc.save(str(pdf_col), garbage=4, deflate=True); doc.close()
r2 = validar_pdf_anverso(pdf_col)
if r2.tenia_color:
    ok(f"Color detectado y convertido: {r2.original_kb}KB -> {r2.final_kb}KB  -> {r2.procesado_path.name}")
else:
    warn("PDF simple: no tiene suficiente diferencia de color para detectar")
    ok(f"PDF procesado OK: {r2.mensaje}")

# PDF entero
sub("Creando PDF de entero fiscal...")
pdf_ent = subir_dir / "entero_fiscal.pdf"
doc = fitz.open()
page = doc.new_page()
page.insert_text((50,100), "CERTIFICADO DE PAGO DE IMPUESTOS TERRITORIALES", fontsize=11)
page.insert_text((50,130), "Numero de entero: 210100123456789", fontsize=10)
page.insert_text((50,150), "Monto: 45,000.00 colones", fontsize=10)
doc.save(str(pdf_ent), garbage=4, deflate=True); doc.close()
r3 = validar_pdf_entero(pdf_ent)
ok(f"Entero: {r3.mensaje}")

# ZIP shape valido
sub("Creando ZIP de shapefile completo...")
zip_ok = subir_dir / "levantamiento.zip"
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
    zf.writestr("mapa.shp", b"ESRI Shapefile\x00" + b"\x00"*500)
    zf.writestr("mapa.dbf", b"dBASE III\x00"      + b"\x00"*300)
    zf.writestr("mapa.shx", b"SHX index\x00"      + b"\x00"*200)
    zf.writestr("mapa.prj", b"GEOGCS['WGS_1984']")
zip_ok.write_bytes(buf.getvalue())
r4 = validar_zip_shape(zip_ok)
ok(f"Shape: {r4.mensaje}  ({r4.original_kb} KB)") if r4.ok else err(r4.mensaje)

# ZIP invalido (sin .shx)
sub("Probando ZIP invalido (sin .shx)...")
zip_bad = subir_dir / "shape_malo.zip"
buf2 = io.BytesIO()
with zipfile.ZipFile(buf2, "w") as zf:
    zf.writestr("a.shp", b"data"); zf.writestr("a.dbf", b"data")
buf2.seek(0); zip_bad.write_bytes(buf2.getvalue())
r5 = validar_zip_shape(zip_bad)
ok(f"ZIP invalido detectado correctamente: {r5.mensaje}") if not r5.ok else err("Debio fallar")

# ============================================================
titulo("4 - REPORTE WHATSAPP")
# ============================================================

todos_pdfs = sorted(subir_dir.glob("*.pdf"))
resultados = []
for f in todos_pdfs:
    if f.stem.endswith("_bot") or "malo" in f.name: continue
    tipo = _clasificar_pdf(f, todos_pdfs)
    resultados.append(validar_pdf_anverso(f) if tipo=="anverso"
                      else validar_pdf_entero(f) if tipo=="entero"
                      else validar_archivo(f, tipo))
for f in sorted(subir_dir.glob("*.zip")):
    if "malo" not in f.name: resultados.append(validar_zip_shape(f))

reporte = formatear_reporte(
    nombre_cliente=exp.get("nombre_cliente") or "Cliente",
    numero_expediente=NUMERO,
    fase="03_APT_R1",
    resultados=resultados,
)
sub("Mensaje que se enviaria al operador por WhatsApp:")
print()
for l in reporte.splitlines(): print(f"   {l}")

# ============================================================
titulo("5 - WATCHDOG EN VIVO")
# ============================================================

from src.agents.file_manager import FileManager

notifs = []
def _notify(phone, msg):
    notifs.append((phone, msg))
    sub(f"[NOTIF] Para {phone}:")
    for l in msg.splitlines()[:8]: print(f"   {l}")

fm_live = FileManager(db, folder_manager, _notify,
    mega_root=MEGA_ROOT, operador_phone="50688387310", delay_segundos=1)

sub("Iniciando watchdog...")
fm_live.start()
ok(f"Watchdog activo: {fm_live.is_running()}")

# Colocar minuta en RESPUESTA\
resp_dir = raiz / "03_APT_R1" / "RESPUESTA"
demo_resp = resp_dir / "minuta_2026.pdf"
info("Colocando minuta en RESPUESTA\\ en 2 segundos...")
time.sleep(2)
doc_r = fitz.open()
page = doc_r.new_page()
page.insert_text((50,100), "MINUTA DE PRESENTACION APT")
page.insert_text((50,130), f"Expediente: {NUMERO}")
page.insert_text((50,150), "Estado: Aprobado sin observaciones")
doc_r.save(str(demo_resp), garbage=4, deflate=True); doc_r.close()
info(f"Archivo colocado: {demo_resp.name}")
time.sleep(3)
fm_live.stop()
ok(f"Watchdog detenido. Notificaciones recibidas: {len(notifs)}")

# ============================================================
titulo("6 - COMANDOS: ESTADO / BUSCAR / RESUMEN")
# ============================================================

for cmd, etiqueta in [
    (f"ESTADO {NUMERO}", "ESTADO"),
    ("BUSCAR Maria",     "BUSCAR"),
    ("RESUMEN",          "RESUMEN"),
]:
    mensajes.clear()
    sub(f"Comando: {cmd}")
    router.handle(sender_phone="50688387310", text=cmd)
    if mensajes:
        for l in mensajes[-1][1].splitlines(): print(f"   {l}")

# ============================================================
titulo("7 - GREEN API: verificar estado de instancia")
# ============================================================

import requests
from src.core.credential_manager import CredentialManager
try:
    cm_real = CredentialManager()
    g = cm_real.get_green_api()
    iid = g["instance_id"]
    tok = g["token"]
    url = f"https://api.green-api.com/waInstance{iid}/getStateInstance/{tok}"
    r_ga = requests.get(url, timeout=10)
    estado_ga = r_ga.json().get("stateInstance", "?")
    if estado_ga == "authorized":
        ok(f"Green API AUTORIZADA  (instancia {iid})")
    else:
        warn(f"Green API estado: {estado_ga}  (instancia {iid})")
        info("Para autorizar: escanear QR en https://console.green-api.com")
except Exception as e:
    warn(f"No se pudo verificar Green API: {e}")

# ============================================================
titulo("RESUMEN FINAL")
# ============================================================

exp_f = db.obtener_expediente(EID)
ok(f"Expediente:  {exp_f['numero_expediente']}")
ok(f"Cliente:     {exp_f['nombre_cliente']}")
ok(f"Tipo:        {exp_f['tipo_plano']}")
ok(f"Estado:      {exp_f['estado_actual']}")
ok(f"Archivos BD: {len(db.archivos_de(EID))}")
ok(f"Carpetas:    {len(list(Path(mega_path).rglob('*')))}")

print(f"\nDemo completa sin errores.")
print(f"Directorio demo: {DEMO_DIR}\n")
