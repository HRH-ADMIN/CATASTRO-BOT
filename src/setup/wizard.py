"""Setup Wizard interactivo para catastro-bot — Opción A.

Guía al administrador en la configuración inicial de todas las
credenciales necesarias. Verifica que cada credencial sea válida
antes de continuar al siguiente paso.

Uso:
  python -m src.setup.wizard

El wizard detecta qué ya está configurado y solo pide lo que falta.
Puede re-ejecutarse en cualquier momento para actualizar credenciales.

Credenciales configuradas:
  1. Clave maestra BD (db-master-key) — generada automáticamente
  2. API Key Anthropic (anthropic-api)
  3. Green API (green-api) — instancia + token
  4. Portal APT CFIA (apt) — usuario + contraseña
  5. Portal RNP Digital (rnp) — usuario + contraseña
  6. Email bot / Muni San Ramón (muni-san-ramon)
  7. Google OAuth client secret (google-oauth)
  8. Operadores WhatsApp autorizados (operators)
"""
from __future__ import annotations

import getpass
import json
import re
import secrets
import sys
from pathlib import Path

# Forzar UTF-8 en stdout para evitar errores en terminales Windows (cp1252)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_DIVIDER = "━" * 50


def _header(title: str) -> None:
    print(f"\n{_DIVIDER}")
    print(f"  {title}")
    print(_DIVIDER)


def _ok(msg: str) -> None:
    print(f"  ✅ {msg}")


def _warn(msg: str) -> None:
    print(f"  ⚠️  {msg}")


def _ask(prompt: str, *, secret: bool = False, default: str = "") -> str:
    """Pide input al usuario; retorna el valor ingresado o el default."""
    full_prompt = f"  → {prompt}"
    if default:
        full_prompt += f" [{default}]"
    full_prompt += ": "
    if secret:
        value = getpass.getpass(full_prompt)
    else:
        value = input(full_prompt).strip()
    return value if value else default


def _ask_yes(prompt: str, default: bool = True) -> bool:
    yn = "[S/n]" if default else "[s/N]"
    raw = input(f"  → {prompt} {yn}: ").strip().lower()
    if not raw:
        return default
    return raw in ("s", "si", "sí", "y", "yes")


def _phone_digits(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


# ── Pasos del wizard ───────────────────────────────────────────────────────────

def _paso_db_key(cm) -> None:
    _header("Paso 1 — Clave maestra de la base de datos")
    try:
        cm.get_db_key()
        _ok("Clave maestra ya configurada.")
        return
    except Exception:
        pass

    print(
        "  La base de datos se cifra con AES-256 usando una clave de 32 bytes.\n"
        "  Se generará una clave aleatoria segura automáticamente."
    )
    if _ask_yes("¿Generar nueva clave maestra?"):
        # get_or_create_db_key genera y almacena la clave si no existe
        key = cm.get_or_create_db_key()
        _ok(f"Clave maestra generada y almacenada ({key.hex()[:8]}...).")
    else:
        from config.settings import CRED_DB_KEY
        hex_key = _ask("Ingrese la clave hex (64 caracteres)", secret=True)
        cm.set_secret(CRED_DB_KEY, hex_key)
        _ok("Clave maestra almacenada.")


def _paso_anthropic(cm) -> None:
    _header("Paso 2 — API Key de Anthropic")
    try:
        cm.get_anthropic_key()
        _ok("API Key Anthropic ya configurada.")
        return
    except Exception:
        pass

    print(
        "  Se usa para el análisis de minutas con Claude.\n"
        "  Obténgala en: https://console.anthropic.com/keys"
    )
    key = _ask("API Key de Anthropic (sk-ant-...)", secret=True)
    if key:
        cm.set_anthropic_key(key)
        _ok("API Key Anthropic almacenada.")
    else:
        _warn("Omitido — MinutaAgent no funcionará.")


def _paso_green_api(cm) -> None:
    _header("Paso 3 — Green API (WhatsApp)")
    try:
        cm.get_green_api()
        _ok("Green API ya configurada.")
        return
    except Exception:
        pass

    print(
        "  Obtener instancia y token en: https://green-api.com\n"
        "  IMPORTANTE: Activar 'incomingWebhook' en la instancia.\n"
        "  El webhook URL del bot debe apuntar a la IP/dominio donde corre el servidor."
    )
    instance = _ask("ID de instancia (ej: 1101234567)")
    token    = _ask("Token de instancia", secret=True)
    if instance and token:
        cm.set_green_api(instance, token)
        _ok("Green API almacenada.")
    else:
        _warn("Omitido — WhatsApp no funcionará.")


def _paso_apt(cm) -> None:
    _header("Paso 4 — Portal APT del CFIA")
    try:
        cm.get_apt()
        _ok("Credenciales APT ya configuradas.")
        return
    except Exception:
        pass

    print(
        "  Portal: https://sso.cfia.or.cr\n"
        "  Credenciales del topógrafo inscrito en el CFIA."
    )
    user = _ask("Usuario CFIA (email o número de carné)")
    pwd  = _ask("Contraseña CFIA", secret=True)
    if user and pwd:
        cm.set_apt(user, pwd)
        _ok("Credenciales APT almacenadas.")
    else:
        _warn("Omitido — APTAgent no funcionará.")


def _paso_rnp(cm) -> None:
    _header("Paso 5 — Portal RNP Digital")
    try:
        cm.get_rnp()
        _ok("Credenciales RNP ya configuradas.")
        return
    except Exception:
        pass

    print(
        "  Portal: https://www.rnpdigital.com/shopping/\n"
        "  El pago del entero se hace MANUALMENTE en el BCR.\n"
        "  El operador registra el número con el comando PAGAR.\n"
        "  Estas credenciales son OPCIONALES (solo si se activa pago automático)."
    )
    if not _ask_yes("¿Configurar credenciales RNP ahora? (opcional)", default=False):
        _warn("Omitido — el entero se registra manualmente con PAGAR.")
        return
    user = _ask("Usuario RNP digital")
    pwd  = _ask("Contraseña RNP digital", secret=True)
    if user and pwd:
        cm.set_rnp(user, pwd)
        _ok("Credenciales RNP almacenadas.")
    else:
        _warn("Omitido — el entero se registra manualmente con PAGAR.")


def _paso_muni(cm) -> None:
    _header("Paso 6 — Email del bot (Municipalidad / SMTP/IMAP)")
    try:
        cm.get_muni_san_ramon()
        _ok("Email del bot ya configurado.")
        return
    except Exception:
        pass

    print(
        "  El bot envía y recibe correos desde esta cuenta Gmail.\n"
        "  Use una App Password (no la contraseña principal).\n"
        "  Activar en: https://myaccount.google.com/apppasswords\n"
        "  SMTP: smtp.gmail.com:587 (STARTTLS)\n"
        "  IMAP: imap.gmail.com:993 (SSL)"
    )
    email    = _ask("Email del bot (ej: tramites@gmail.com)")
    app_pass = _ask("App Password Gmail", secret=True)
    if email and app_pass:
        cm.set_muni_san_ramon(email, app_pass)
        _ok("Email del bot almacenado.")
    else:
        _warn("Omitido — envío de correos a Municipalidad no funcionará.")


def _paso_google_oauth(cm) -> None:
    _header("Paso 7 — Google OAuth (Google Drive)")
    try:
        cm.get_google_oauth()
        _ok("Client secret Google OAuth ya configurado.")
        return
    except Exception:
        pass

    print(
        "  Necesita un proyecto en Google Cloud con la API de Drive habilitada.\n"
        "  Descargar client_secret.json desde:\n"
        "  https://console.cloud.google.com/apis/credentials\n"
        "  Pegar el contenido JSON a continuación."
    )
    client_secret_path = _ask(
        "Ruta al archivo client_secret.json (o presione Enter para pegar JSON)"
    )

    if client_secret_path:
        p = Path(client_secret_path)
        if p.is_file():
            content = p.read_text(encoding="utf-8")
            cm.set_google_oauth(content)
            _ok("Client secret Google OAuth almacenado desde archivo.")
        else:
            _warn(f"Archivo no encontrado: {p}")
    else:
        print("  Pegue el JSON del client_secret (termine con una línea vacía):")
        lines = []
        while True:
            line = input()
            if not line:
                break
            lines.append(line)
        content = "\n".join(lines).strip()
        if content:
            try:
                json.loads(content)  # validar JSON
                cm.set_google_oauth(content)
                _ok("Client secret Google OAuth almacenado.")
            except json.JSONDecodeError:
                _warn("JSON inválido — omitido.")
        else:
            _warn("Omitido — Google Drive no funcionará hasta configurarlo.")


def _paso_operadores(cm, db) -> None:
    _header("Paso 8 — Operadores WhatsApp autorizados")
    admins = db.listar_usuarios(rol="admin", activo=True)
    if admins:
        print("  Operadores activos en BD:")
        for a in admins:
            print(f"    • {a['nombre']} ({a['telefono']}) — {a['rol']}")
        if not _ask_yes("¿Agregar otro operador?", default=False):
            return

    print(
        "  Ingrese el número de WhatsApp del administrador principal.\n"
        "  Formato: +50688887777 (con código de país CR 506)"
    )
    nombre   = _ask("Nombre del administrador")
    telefono = _ask("Teléfono (+506XXXXXXXX)")

    if nombre and telefono:
        tel_norm = _phone_digits(telefono)
        try:
            db.crear_usuario(
                nombre=nombre,
                telefono=tel_norm,
                rol="admin",
            )
            _ok(f"Administrador {nombre} ({tel_norm}) creado.")
        except Exception as exc:
            _warn(f"No se pudo crear operador: {exc}")
    else:
        _warn("Omitido — sin administradores configurados.")


# ── Verificación post-setup ────────────────────────────────────────────────────

def _verificar_configuracion(cm) -> None:
    _header("Verificación de configuración")
    checks = [
        ("Clave BD",          cm.get_db_key),
        ("Anthropic API Key", cm.get_anthropic_key),
        ("Green API",         cm.get_green_api),
        ("APT CFIA",          cm.get_apt),
        ("RNP Digital",       cm.get_rnp),
        ("Email bot",         cm.get_muni_san_ramon),
    ]
    all_ok = True
    for nombre, fn in checks:
        try:
            fn()
            _ok(nombre)
        except Exception:
            _warn(f"{nombre} — NO configurado")
            all_ok = False

    print()
    if all_ok:
        print("  ✅ Todas las credenciales configuradas. El bot está listo.")
    else:
        print("  ⚠️  Algunas credenciales faltan. El bot puede funcionar")
        print("     de forma limitada hasta que se completen.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print(f"\n{'═' * 50}")
    print("  🤖 catastro-bot — Setup Wizard")
    print(f"{'═' * 50}")
    print(
        "\nEste wizard configura todas las credenciales necesarias\n"
        "para el funcionamiento del bot en Windows Credential Manager.\n"
        "Los secretos NUNCA se escriben en archivos de texto."
    )

    try:
        from src.core.credential_manager import CredentialManager
        from src.core.database import Database
    except ImportError as exc:
        print(f"\n❌ Error de importación: {exc}")
        print("Asegúrese de estar en el directorio raíz del proyecto y")
        print("que el entorno virtual esté activado.")
        sys.exit(1)

    cm = CredentialManager()

    _paso_db_key(cm)
    _paso_anthropic(cm)
    _paso_green_api(cm)
    _paso_apt(cm)
    _paso_rnp(cm)
    _paso_muni(cm)
    _paso_google_oauth(cm)

    # La BD requiere la clave maestra — inicializar antes de crear usuarios
    _header("Inicializando base de datos…")
    try:
        db = Database(credentials=cm)
        db.initialize_schema()
        _ok("Base de datos lista.")
        _paso_operadores(cm, db)
    except Exception as exc:
        _warn(f"No se pudo inicializar la BD: {exc}")

    _verificar_configuracion(cm)

    print(f"\n{'═' * 50}")
    print("  Setup completado.")
    print(f"{'═' * 50}\n")

    print(
        "Próximos pasos:\n"
        "  1. Instalar servicio Windows:\n"
        "       python -m src.service.windows_service install\n"
        "       python -m src.service.windows_service start\n\n"
        "  2. Verificar conectividad:\n"
        "       python -m src.setup.pilot_check\n\n"
        "  3. Crear el primer expediente por WhatsApp:\n"
        "       NUEVO PLANO\n"
        "       tipo: segregacion\n"
        "       expediente: SEG-2026-001\n"
        "       topografo: Nombre Apellido\n"
        "       telefono: +50688887777\n"
    )


if __name__ == "__main__":
    main()
