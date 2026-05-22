"""Bootstrap script para catastro-bot.

Instala dependencias, navegadores de Playwright, crea directorios con
ACLs restrictivas en Windows, genera la llave maestra de SQLCipher en
Windows Credential Manager e inicializa el esquema de la base de datos.

Uso:
    python setup.py
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REQUIRED_PYTHON = (3, 11)


def check_environment() -> None:
    if sys.version_info[:2] < REQUIRED_PYTHON:
        sys.exit(
            f"Python {REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]}+ requerido "
            f"(detectado {sys.version_info.major}.{sys.version_info.minor})"
        )
    if platform.system() != "Windows":
        print(
            "AVISO: este proyecto está pensado para Windows. "
            "Funciones como Windows Credential Manager y los ACLs no funcionarán."
        )


def install_dependencies() -> None:
    print("[1/5] Instalando dependencias de Python...")
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "--upgrade", "pip"]
    )
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")]
    )


def install_playwright_browsers() -> None:
    print("[2/5] Instalando navegadores de Playwright (chromium)...")
    subprocess.check_call(
        [sys.executable, "-m", "playwright", "install", "chromium"]
    )


SENSITIVE_DIRS = ("data", "data/files", "data/temp", "logs")


def create_directories() -> None:
    print("[3/5] Creando directorios...")
    for rel in SENSITIVE_DIRS:
        (ROOT / rel).mkdir(parents=True, exist_ok=True)


def lock_acls() -> None:
    """Restringe acceso a `data/` y `logs/` solo al usuario actual."""
    if platform.system() != "Windows":
        return
    print("[4/5] Aplicando ACLs restrictivas (icacls)...")
    user = os.environ.get("USERNAME") or ""
    if not user:
        print("  (no se detectó USERNAME; saltando ACLs)")
        return
    for rel in ("data", "logs"):
        target = ROOT / rel
        try:
            subprocess.run(
                ["icacls", str(target), "/inheritance:r"],
                check=False, capture_output=True,
            )
            subprocess.run(
                ["icacls", str(target), "/grant:r", f"{user}:(OI)(CI)F"],
                check=False, capture_output=True,
            )
        except FileNotFoundError:
            print("  icacls no disponible; saltando")
            return


def initialize_database() -> None:
    print("[5/5] Inicializando llave maestra y esquema cifrado...")
    sys.path.insert(0, str(ROOT))
    from src.core.credential_manager import CredentialManager  # noqa: E402
    from src.core.database import Database  # noqa: E402

    cm = CredentialManager()
    cm.get_or_create_db_key()
    db = Database(credentials=cm)
    db.initialize_schema()
    print("  Esquema cifrado listo en data/catastro.db")


def main() -> None:
    check_environment()
    install_dependencies()
    install_playwright_browsers()
    create_directories()
    lock_acls()
    initialize_database()
    print()
    print("=" * 60)
    print(" catastro-bot instalado correctamente.")
    print("=" * 60)
    print()
    print("Pasos siguientes:")
    print("  1) Configurar credenciales:")
    print("       python -m src.core.credential_manager")
    print("  2) Arrancar el sistema:")
    print("       python -m src.main")


if __name__ == "__main__":
    main()
