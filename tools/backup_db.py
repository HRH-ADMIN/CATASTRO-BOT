"""CLI: backup manual + poda de viejos.

USO:
  python tools/backup_db.py                # hace backup + poda con retención=30
  python tools/backup_db.py --retener 60   # mantiene 60 más recientes
  python tools/backup_db.py --solo-listar  # solo lista (no respalda)
"""
from __future__ import annotations
import argparse
import io
import os
import sys
from pathlib import Path

os.environ.setdefault("CATASTRO_BOT_DEV_MODE", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from src.utils.backup_db import (  # noqa: E402
    backup_diario, listar_backups, RETENER_DEFAULT,
)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--retener", type=int, default=RETENER_DEFAULT,
                   help=f"backups recientes a mantener (default {RETENER_DEFAULT})")
    p.add_argument("--solo-listar", action="store_true",
                   help="Solo muestra los existentes, no crea backup nuevo")
    args = p.parse_args()

    if args.solo_listar:
        existentes = listar_backups()
        if not existentes:
            print("(sin backups)")
            return 0
        print(f"{len(existentes)} backup(s) existentes:")
        for f in existentes:
            size_kb = f.stat().st_size / 1024
            print(f"  {f.name}  ({size_kb:.1f} KB)")
        return 0

    res = backup_diario(retener=args.retener)
    if res["creado"]:
        print(f"✅ Backup creado: {res['creado']}")
    else:
        print("❌ No se pudo crear backup")
        return 1
    if res["borrados"] > 0:
        print(f"🧹 {res['borrados']} backup(s) viejo(s) eliminado(s)")
    print(f"   Mantieniendo {args.retener} backup(s) más recientes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
