"""Rutas y constantes no sensibles. Los secretos viven en Windows Credential Manager."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
FILES_DIR = DATA_DIR / "files"
TEMP_DIR = DATA_DIR / "temp"
LOGS_DIR = ROOT / "logs"

DATABASE_PATH = DATA_DIR / "catastro.db"

# Prefijo para todas las entradas en Windows Credential Manager
CREDENTIAL_PREFIX = "catastro-bot"

# Nombres de credenciales (se almacenan como f"{prefix}:{name}")
CRED_DB_KEY = "db-master-key"
CRED_APT = "apt"
CRED_MUNI_SAN_RAMON = "muni-san-ramon"
CRED_GREEN_API = "green-api"
CRED_GOOGLE_OAUTH = "google-oauth"
CRED_DRIVE_TOKEN  = "drive-token"   # access_token + refresh_token del usuario OAuth
CRED_RNP          = "rnp"           # (username, password) del portal RNP digital
CRED_ANTHROPIC    = "anthropic-api"

# Operadores autorizados para comandos administrativos por WhatsApp
# (NUEVO / ESTADO / APROBAR / RECHAZAR / RESUMEN). Es una lista JSON
# de teléfonos normalizados (sólo dígitos, con código de país 506).
CRED_OPERATORS = "operators"

# Confirmaciones por WhatsApp expiran tras este tiempo (segundos)
WHATSAPP_CONFIRMATION_TIMEOUT = 60 * 60 * 24

# Audit log
AUDIT_HASH_ALGO = "sha256"

# Ley 6545 — Ley de Catastro Nacional de Costa Rica
LEY_CATASTRO = "6545"
MUNICIPALIDAD_DEFAULT = "San Ramón"

# Compatibilidad SQLCipher (4 = AES-256-CBC + HMAC-SHA512, default moderno)
SQLCIPHER_COMPATIBILITY = 4

# APT — Portal CFIA (Colegio Federado de Ingenieros y Arquitectos, CR).
# SSO unificado redirige a apt.cfia.or.cr/APT2 tras el login.
APT_LOGIN_URL    = "https://sso.cfia.or.cr/sso/?IdSystem=1"
APT_HOME_URL     = "https://apt.cfia.or.cr/APT2/Home"
APT_TRAMITES_URL = "https://apt.cfia.or.cr/APT2/Plano/Consulta"
APT_CONTRATO_URL = "https://apt.cfia.or.cr/APT2/Contrato/Nuevo"
APT_SESSION_PATH    = TEMP_DIR / "playwright_state_apt.json"   # legado — ya no se usa
APT_PROFILE_PATH    = TEMP_DIR / "chrome_profile_apt"           # perfil system Chrome (fallback)
# CDP endpoint para conectarse al Chrome del usuario (modo preferido).
# El usuario debe iniciar Chrome con tools/start-chrome-bot.bat antes de usar APT.
# Si Chrome NO está corriendo con --remote-debugging-port=9222, el bot cae al
# modo fallback (launch_persistent_context con perfil propio, sin extensiones).
APT_CDP_ENDPOINT    = "http://localhost:9222"

# Protocolo activo del topógrafo (numero del tomo de protocolo CFIA en uso
# durante el año actual). Si un plano se sube con un protocolo DIFERENTE,
# el bot lo detecta como "continuación de contrato anterior" y notifica al
# operador para que valide. Se setea por env var o se actualiza por año.
# Si está vacío, no se hace la detección.
import os as _os_proto
PROTOCOLO_ACTIVO_TOPOGRAFO = _os_proto.environ.get("PROTOCOLO_ACTIVO_TOPOGRAFO", "24162")

# Shadow mode / rollout — cuánto auto-guarda el bot.
# Valores válidos: "manual" | "auto_if_clean" | "auto"
#   manual         → bot llena pero NO guarda (operador click GUARDAR)
#   auto_if_clean  → guarda solo si NO hay discrepancias detectadas
#   auto           → guarda siempre (max trust)
# Env vars del mismo nombre overridean estos defaults.
BOT_SAVE_MODE_CONTRATO       = _os_proto.environ.get("BOT_SAVE_MODE_CONTRATO",       "manual")
BOT_SAVE_MODE_PLANO_SECCION  = _os_proto.environ.get("BOT_SAVE_MODE_PLANO_SECCION",  "auto_if_clean")
BOT_SAVE_MODE_ENVIAR_CFIA    = _os_proto.environ.get("BOT_SAVE_MODE_ENVIAR_CFIA",    "manual")

# Municipalidad de San Ramón.
# El visado de planos se gestiona por correo electrónico (no hay portal web).
# - MUNI_SAN_RAMON_EMAIL: destino de los envíos al depto. catastral.
# - MUNI_IMAP/SMTP: servidor de correo del bot — cuenta Hotmail/Outlook del topógrafo.
#   Las notificaciones de APT y de la Municipalidad llegan a este buzón.
# La credencial CRED_MUNI_SAN_RAMON guarda (username=email_hotmail, password=app_password).
MUNI_SAN_RAMON_EMAIL = "mgamboa@sanramondigital.net"  # catastral Muni San Ramón
MUNI_SMTP_HOST       = "smtp.gmail.com"              # Gmail
MUNI_SMTP_PORT       = 587                           # STARTTLS
MUNI_IMAP_HOST       = "imap.gmail.com"             # Gmail
MUNI_IMAP_PORT       = 993                           # SSL
MUNI_SAN_RAMON_LOGIN_URL = ""   # sin uso — conservado por compatibilidad
MUNI_SESSION_PATH = TEMP_DIR / "playwright_state_muni.json"  # sin uso

# RNP — Portal de pagos del Registro Nacional de Costa Rica.
# rnpdigital.com/shopping/ es el portal de pago de derechos registrales.
# Selectores verificados contra el portal real (2026-05-05).
RNP_HOME_URL     = "https://www.rnpdigital.com/shopping/"
RNP_BUSQUEDA_URL = "https://www.rnpdigital.com/shopping/publico/busquedaEntero.faces"
RNP_SESSION_PATH = TEMP_DIR / "playwright_state_rnp.json"

# Playwright — headless=False en DEV_MODE para poder ver el navegador durante pruebas.
# En producción (DEV_MODE no seteado) corre headless con 0ms de slow_mo.
import os as _os_pw
PLAYWRIGHT_HEADLESS  = _os_pw.getenv("CATASTRO_BOT_DEV_MODE") != "1"
PLAYWRIGHT_SLOW_MO_MS = 500 if _os_pw.getenv("CATASTRO_BOT_DEV_MODE") == "1" else 0

# ── Mega ──────────────────────────────────────────────────────────────────────
# El cliente de escritorio de Mega mapea la nube a A:\mega\
# Todos los expedientes viven bajo MEGA_ROOT.
# En pruebas locales sin Mega (CATASTRO_BOT_DEV_MODE=1), se usa DATA_DIR/activos.
import os as _os
MEGA_ROOT = (
    DATA_DIR / "activos"
    if _os.getenv("CATASTRO_BOT_DEV_MODE")
    else Path("A:/mega/catastro-bot")
)
ACTIVOS_DIR     = MEGA_ROOT / "ACTIVOS"       # expedientes en curso
COMPLETADOS_DIR = MEGA_ROOT / "COMPLETADOS"   # expedientes inscritos y entregados

# Subcarpetas de cada expediente (igual en Mega y en local data/files/)
# Estructura: ACTIVOS\APELLIDO_NOMBRE\PROYECTO_TIPO\{FASE}\
MEGA_FASES = [
    "01_CAMPO",
    "02_DISENO",
    "03_APT_R1",
    "04_MUNICIPAL",
    "05_APT_R2",
    "06_INSCRITO",
]

# Carpeta de entrada (watchdog la vigila — Semana 3)
MEGA_SUBIR_FOLDER    = "SUBIR"
MEGA_RESPUESTA_FOLDER = "RESPUESTA"
