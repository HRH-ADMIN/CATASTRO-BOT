"""One-shot: mover los 2 secretos filtrados del disco al Credential Manager.

Lee `clave api.txt` (Anthropic) y `config/google_oauth_credentials.json`
(Google OAuth client secret), los persiste en Windows Credential Manager
del usuario actual, verifica la escritura leyendo de vuelta, y solo si
todo OK borra los archivos originales.

Idempotente: si los archivos ya no existen, no falla.

Uso:
    python tools/lockdown_leaked_secrets.py            # ejecuta
    python tools/lockdown_leaked_secrets.py --dry-run  # solo reporta
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
ANTHROPIC_FILE = ROOT / "clave api.txt"
GOOGLE_FILE    = ROOT / "config" / "google_oauth_credentials.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="No escribe ni borra — solo reporta qué haría")
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT))
    from src.core.credential_manager import CredentialManager

    cm = CredentialManager()
    cambios = []
    pendientes = []

    # ── Anthropic ──────────────────────────────────────────────────────
    if ANTHROPIC_FILE.exists():
        content = ANTHROPIC_FILE.read_text(encoding="utf-8").strip()
        if not content.startswith("sk-ant-"):
            print(f"[ERROR] {ANTHROPIC_FILE} no parece una API key Anthropic")
            return 1
        if args.dry_run:
            pendientes.append(f"SET anthropic-api (len={len(content)}); DELETE {ANTHROPIC_FILE.name}")
        else:
            cm.set_anthropic_key(content)
            # Verificación round-trip
            recovered = cm.get_anthropic_key()
            if recovered != content:
                print(f"[ERROR] verificación falló — Cred Manager devolvió valor distinto")
                return 1
            ANTHROPIC_FILE.unlink()
            cambios.append(f"anthropic-api guardada en Cred Manager + {ANTHROPIC_FILE.name} borrado")
    else:
        print(f"[skip] {ANTHROPIC_FILE.name} no existe")

    # ── Google OAuth ──────────────────────────────────────────────────
    if GOOGLE_FILE.exists():
        content = GOOGLE_FILE.read_text(encoding="utf-8").strip()
        if "client_secret" not in content:
            print(f"[ERROR] {GOOGLE_FILE} no parece un client_secret de Google")
            return 1
        if args.dry_run:
            pendientes.append(f"SET google-oauth (len={len(content)}); DELETE {GOOGLE_FILE.name}")
        else:
            cm.set_google_oauth(content)
            recovered = cm.get_google_oauth()
            if recovered != content:
                print(f"[ERROR] verificación google-oauth falló")
                return 1
            GOOGLE_FILE.unlink()
            cambios.append(f"google-oauth guardada en Cred Manager + {GOOGLE_FILE.name} borrado")
    else:
        print(f"[skip] {GOOGLE_FILE.name} no existe")

    if args.dry_run:
        if pendientes:
            print("[DRY RUN] cambios pendientes:")
            for p in pendientes:
                print(f"  - {p}")
        else:
            print("[DRY RUN] nada que hacer")
        return 0

    if cambios:
        print("[OK] cambios aplicados:")
        for c in cambios:
            print(f"  - {c}")
        print("\n[VERIFICACION] credenciales activas en Cred Manager:")
        for name in sorted(cm.list_names()):
            print(f"  - catastro-bot:{name}")
    else:
        print("[OK] nada que migrar — archivos ya estaban limpios")

    return 0


if __name__ == "__main__":
    sys.exit(main())
