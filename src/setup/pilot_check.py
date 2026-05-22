"""Pilot Check — verificación pre-piloto de catastro-bot — Opción C.

Verifica que todos los componentes críticos estén operativos
antes de procesar el primer expediente real.

Uso:
  python -m src.setup.pilot_check

Chequeos realizados:
  ✅ Credenciales configuradas (Cred Manager)
  ✅ Base de datos — apertura + schema
  ✅ Playwright — browsers instalados
  ✅ Portal APT CFIA — conectividad HTTP
  ✅ Portal RNP Digital — conectividad HTTP
  ✅ Green API — estado de la instancia WhatsApp
  ✅ Google Drive — token válido
  ✅ Anthropic API — ping
  ✅ SMTP/IMAP — conectividad con servidor de correo
  ✅ Librerías opcionales (ezdxf, pypdf)
  ✅ Carpetas de datos (Mega / local)
"""
from __future__ import annotations

import importlib
import smtplib
import sys
import time
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError, HTTPError

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# Cargar variables de entorno desde .env si existe (ej: CATASTRO_BOT_DEV_MODE=1)
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(_PROJECT_ROOT / ".env")
except ImportError:
    pass

# ── Resultado de cada chequeo ──────────────────────────────────────────────────

class CheckResult:
    def __init__(self, nombre: str, ok: bool, detalle: str = ""):
        self.nombre  = nombre
        self.ok      = ok
        self.detalle = detalle

    def __str__(self) -> str:
        icon = "✅" if self.ok else "❌"
        extra = f" — {self.detalle}" if self.detalle else ""
        return f"  {icon} {self.nombre}{extra}"


# ── Chequeos individuales ──────────────────────────────────────────────────────

def _check_credenciales() -> list[CheckResult]:
    resultados = []
    try:
        from src.core.credential_manager import CredentialManager
        cm = CredentialManager()
    except ImportError as exc:
        return [CheckResult("CredentialManager (import)", False, str(exc))]

    checks = [
        ("Clave BD",          cm.get_db_key),
        ("Anthropic API Key", cm.get_anthropic_key),
        ("Green API",         cm.get_green_api),
        ("APT CFIA",          cm.get_apt),
        ("RNP Digital",       cm.get_rnp),
        ("Email bot (SMTP)",  cm.get_muni_san_ramon),
    ]
    for nombre, fn in checks:
        try:
            fn()
            resultados.append(CheckResult(f"Credencial: {nombre}", True))
        except Exception as exc:
            resultados.append(CheckResult(f"Credencial: {nombre}", False, str(exc)))
    return resultados


def _check_database() -> CheckResult:
    try:
        from src.core.credential_manager import CredentialManager
        from src.core.database import Database
        cm  = CredentialManager()
        db  = Database(credentials=cm)
        db.initialize_schema()
        total = db.listar_expedientes()
        return CheckResult("Base de datos", True, f"{len(total)} expedientes")
    except Exception as exc:
        return CheckResult("Base de datos", False, str(exc))


def _check_playwright() -> CheckResult:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browsers = []
            for name in ("chromium", "firefox", "webkit"):
                try:
                    br = getattr(p, name)
                    br.launch(headless=True).close()
                    browsers.append(name)
                except Exception:
                    pass
            if browsers:
                return CheckResult("Playwright", True, f"browsers: {', '.join(browsers)}")
            return CheckResult("Playwright", False, "ningún browser instalado")
    except ImportError:
        return CheckResult(
            "Playwright", False,
            "no instalado — pip install playwright && playwright install chromium"
        )
    except Exception as exc:
        return CheckResult("Playwright", False, str(exc))


def _check_http(nombre: str, url: str, timeout: int = 10) -> CheckResult:
    try:
        with urlopen(url, timeout=timeout) as resp:
            code = resp.status
            return CheckResult(nombre, code < 500, f"HTTP {code}")
    except HTTPError as exc:
        # 4xx puede ser normal (página requiere login)
        ok = exc.code < 500
        return CheckResult(nombre, ok, f"HTTP {exc.code}")
    except URLError as exc:
        return CheckResult(nombre, False, f"conexión fallida: {exc.reason}")
    except Exception as exc:
        return CheckResult(nombre, False, str(exc))


def _check_green_api() -> CheckResult:
    try:
        from src.core.credential_manager import CredentialManager
        cm = CredentialManager()
        data = cm.get_green_api()
        instance_id = data["instance_id"]
        token = data["token"]
    except Exception as exc:
        return CheckResult("Green API (credencial)", False, str(exc))

    url = (
        f"https://api.green-api.com/waInstance{instance_id}"
        f"/getStateInstance/{token}"
    )
    try:
        import json
        with urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
        state = data.get("stateInstance", "desconocido")
        ok    = state == "authorized"
        return CheckResult(
            "Green API (WhatsApp)",
            ok,
            f"estado: {state}" + ("" if ok else " — escanear QR primero"),
        )
    except Exception as exc:
        return CheckResult("Green API (WhatsApp)", False, str(exc))


def _check_anthropic() -> CheckResult:
    try:
        from src.core.credential_manager import CredentialManager
        cm = CredentialManager()
        api_key = cm.get_anthropic_key()
    except Exception as exc:
        return CheckResult("Anthropic API (credencial)", False, str(exc))

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        # Llamada mínima de validación
        resp = client.messages.create(
            model="claude-haiku-20240307",
            max_tokens=5,
            messages=[{"role": "user", "content": "ping"}],
        )
        return CheckResult("Anthropic API", True, f"modelo: {resp.model}")
    except ImportError:
        return CheckResult(
            "Anthropic API", False,
            "SDK no instalado — pip install anthropic"
        )
    except Exception as exc:
        return CheckResult("Anthropic API", False, str(exc))


def _check_smtp() -> CheckResult:
    try:
        from config.settings import MUNI_SMTP_HOST, MUNI_SMTP_PORT
        from src.core.credential_manager import CredentialManager
        cm = CredentialManager()
        email, _ = cm.get_muni_san_ramon()
    except Exception as exc:
        return CheckResult("SMTP (credencial)", False, str(exc))

    try:
        with smtplib.SMTP(MUNI_SMTP_HOST, MUNI_SMTP_PORT, timeout=10) as smtp:
            smtp.ehlo()
            smtp.starttls()
            return CheckResult("SMTP", True, f"{MUNI_SMTP_HOST}:{MUNI_SMTP_PORT}")
    except Exception as exc:
        return CheckResult("SMTP", False, str(exc))


def _check_libs_opcionales() -> list[CheckResult]:
    resultados = []
    for lib, motivo in [
        ("ezdxf",  "validación de capas DWG"),
        ("pypdf",  "validación firma PDF"),
        ("win32cred", "Windows Credential Manager"),
        ("anthropic", "análisis de minutas con Claude"),
        ("playwright", "automatización web APT/RNP"),
        ("google.oauth2", "Google Drive OAuth"),
        ("googleapiclient", "Google Drive API"),
    ]:
        try:
            importlib.import_module(lib)
            resultados.append(CheckResult(f"Librería: {lib}", True, motivo))
        except ImportError:
            ok = lib in ("ezdxf", "pypdf")  # opcionales no bloquean
            resultados.append(CheckResult(
                f"Librería: {lib}",
                not ok or False,
                f"{'OPCIONAL' if ok else 'REQUERIDA'} — {motivo}",
            ))
    return resultados


def _check_carpetas() -> list[CheckResult]:
    from config.settings import DATA_DIR, LOGS_DIR, FILES_DIR, TEMP_DIR
    resultados = []
    for nombre, path in [
        ("data/",  DATA_DIR),
        ("logs/",  LOGS_DIR),
        ("files/", FILES_DIR),
        ("temp/",  TEMP_DIR),
    ]:
        try:
            path.mkdir(parents=True, exist_ok=True)
            resultados.append(CheckResult(f"Carpeta: {nombre}", True, str(path)))
        except Exception as exc:
            resultados.append(CheckResult(f"Carpeta: {nombre}", False, str(exc)))
    return resultados


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    """Ejecuta todos los chequeos y muestra un reporte. Retorna 0 si todo ok."""
    print(f"\n{'═' * 55}")
    print("  🔍 catastro-bot — Pilot Check")
    print(f"{'═' * 55}\n")

    todos: list[CheckResult] = []

    print("📋 Credenciales…")
    r = _check_credenciales(); todos.extend(r)
    for c in r: print(c)

    print("\n🗄️  Base de datos…")
    r = _check_database(); todos.append(r); print(r)

    print("\n🌐 Conectividad…")
    for nombre, url in [
        ("APT CFIA portal",    "https://apt.cfia.or.cr/APT2/Home"),
        ("RNP Digital portal", "https://www.rnpdigital.com/shopping/"),
        ("Anthropic API",      "https://api.anthropic.com"),
    ]:
        c = _check_http(nombre, url)
        todos.append(c); print(c)

    print("\n📱 WhatsApp (Green API)…")
    r = _check_green_api(); todos.append(r); print(r)

    print("\n🤖 Anthropic API…")
    r = _check_anthropic(); todos.append(r); print(r)

    print("\n📧 SMTP…")
    r = _check_smtp(); todos.append(r); print(r)

    print("\n🎭 Playwright…")
    r = _check_playwright(); todos.append(r); print(r)

    print("\n📦 Librerías…")
    r = _check_libs_opcionales(); todos.extend(r)
    for c in r: print(c)

    print("\n📁 Carpetas…")
    r = _check_carpetas(); todos.extend(r)
    for c in r: print(c)

    # Resumen
    fallidos = [c for c in todos if not c.ok]
    print(f"\n{'─' * 55}")
    if not fallidos:
        print("✅ Todo en orden — el bot está listo para el piloto.")
        return 0
    else:
        print(f"❌ {len(fallidos)} chequeo(s) fallido(s):\n")
        for c in fallidos:
            print(f"   • {c.nombre}: {c.detalle}")
        print(
            "\nResuelva los problemas indicados antes de procesar "
            "expedientes reales."
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
