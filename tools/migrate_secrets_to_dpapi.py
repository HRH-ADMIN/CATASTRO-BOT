"""Migración Cred Manager (per-user) → DPAPI machine-scope (data/secrets.enc).

Necesaria cuando el bot va a correr como servicio bajo una cuenta distinta
del topógrafo (LocalSystem, NetworkService) y por lo tanto no puede leer
los secretos del Cred Manager del usuario actual.

Pasos:
  1. Enumerar todas las credenciales bajo el prefijo `catastro-bot:` en el
     Cred Manager del usuario actual.
  2. Re-escribirlas en data/secrets.enc cifradas con DPAPI machine-scope.
  3. Verificar round-trip (leer de DPAPI y comparar con Cred Manager).
  4. Reportar resultado. NO BORRA del Cred Manager (rollback fácil).

Idempotencia: re-correr sobrescribe las entradas en secrets.enc con los
valores actuales del Cred Manager. Si una entrada existe en DPAPI pero no
en Cred Manager, se preserva.

Después de la migración, podés:
  - Probar con env var: `set CATASTRO_BOT_SECRET_BACKEND=dpapi` y arrancar
    el bot. Debería leer todo desde el archivo.
  - Borrar del Cred Manager solo cuando estés seguro:
    `python -m src.core.credential_manager` → opción 9 (borrar).

Uso:
    python tools/migrate_secrets_to_dpapi.py            # migra
    python tools/migrate_secrets_to_dpapi.py --dry-run  # reporta qué haría
    python tools/migrate_secrets_to_dpapi.py --force    # sobrescribe sin pedir
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true",
                        help="No pide confirmación")
    parser.add_argument("--target", type=Path, default=None,
                        help="Ruta del archivo secrets.enc (default data/secrets.enc)")
    args = parser.parse_args()

    from config.settings import CREDENTIAL_PREFIX, DATA_DIR
    from src.core.secret_store import (
        DPAPIMachineStore, WindowsCredentialStore,
    )

    src = WindowsCredentialStore(prefix=CREDENTIAL_PREFIX)
    names = src.list_names()
    if not names:
        print("[OK] Cred Manager no tiene credenciales bajo "
              f"'{CREDENTIAL_PREFIX}:' — nada que migrar")
        return 0

    target = args.target or (DATA_DIR / "secrets.enc")
    print(f"Origen: Windows Credential Manager ({CREDENTIAL_PREFIX}:*)")
    print(f"Destino: {target}")
    print(f"Credenciales detectadas: {len(names)}")
    for n in names:
        print(f"  - {n}")

    if args.dry_run:
        print("\n[DRY RUN] no se escribió nada")
        return 0

    if target.exists() and not args.force:
        ans = input(
            f"\n{target} ya existe — sobrescribir entradas detectadas? [y/N] "
        ).strip().lower()
        if ans != "y":
            print("[ABORT] cancelado por el usuario")
            return 1

    dst = DPAPIMachineStore(target)
    migrados = 0
    fallos = []
    for name in names:
        try:
            user, password = src.get(name)
            dst.set(name, user, password)
            # Verificación
            u2, p2 = dst.get(name)
            if (u2, p2) != (user, password):
                fallos.append(f"{name}: round-trip mismatch")
                continue
            migrados += 1
        except Exception as exc:
            fallos.append(f"{name}: {exc}")

    print(f"\n[OK] migrados: {migrados}/{len(names)}")
    if fallos:
        print("[ERROR] fallos:")
        for f in fallos:
            print(f"  - {f}")
        return 1

    print(f"\nArchivo: {target} ({target.stat().st_size} bytes)")
    print("\nSiguientes pasos:")
    print("  1. Probar arrancando el bot con: set CATASTRO_BOT_SECRET_BACKEND=dpapi")
    print("  2. Verificar que el bot funciona normal (login APT, WhatsApp, etc.)")
    print("  3. Solo cuando estés seguro, borrar del Cred Manager con:")
    print("       python -m src.core.credential_manager  → opción 9")
    print("\nIMPORTANTE: secrets.enc está atado a esta máquina.")
    print("            Si migrás a otro equipo, debés re-migrar ahí.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
