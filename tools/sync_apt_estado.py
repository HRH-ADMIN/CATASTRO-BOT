"""Sincroniza el estado APT real (Calificación RN, Defectuoso, Inscrito, etc.)
desde el portal APT y lo guarda en metadata.apt_estado.

Requiere:
  - Chrome del usuario corriendo con CDP (ver tools/start-chrome-bot.bat)
  - Sesión APT activa

USO:
  python tools/sync_apt_estado.py                   # sincroniza TODOS los con apt_tramite
  python tools/sync_apt_estado.py RDF-2026-001      # solo uno
"""
from __future__ import annotations
import io
import json
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

os.environ.setdefault("CATASTRO_BOT_DEV_MODE", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from src.agents.apt_agent import APTAgent
from src.core.database import Database
from src.core.credential_manager import CredentialManager


def main() -> int:
    target_num = sys.argv[1].upper() if len(sys.argv) > 1 else None

    creds = CredentialManager()
    db = Database(Path("data/catastro.db"), creds)
    db.initialize_schema()

    # Lista de expedientes con apt_tramite
    conn = sqlite3.connect("data/catastro.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, numero_expediente, metadata_json FROM expedientes "
        "WHERE cancelado = 0 ORDER BY numero_expediente"
    ).fetchall()
    conn.close()

    pendientes = []
    for r in rows:
        if target_num and r["numero_expediente"].upper() != target_num:
            continue
        meta = json.loads(r["metadata_json"] or "{}")
        if not meta.get("apt_tramite"):
            continue
        pendientes.append({"id": r["id"], "numero": r["numero_expediente"], "tramite": meta["apt_tramite"]})

    if not pendientes:
        print("Sin expedientes para sincronizar (o sin apt_tramite).")
        return 0

    agent = APTAgent(db, creds)
    if not agent._cdp_disponible():
        print("[ERROR] CDP no disponible — abra Chrome con tools/start-chrome-bot.bat")
        return 1

    print(f"\nSincronizando {len(pendientes)} expediente(s)...\n")
    actualizados = 0
    for p in pendientes:
        try:
            estado = agent.consultar_estado(p["id"])
            if estado:
                db.actualizar_metadata(
                    p["id"],
                    {
                        "apt_estado": estado,
                        "apt_estado_sync": datetime.now().isoformat(timespec="seconds"),
                    },
                    actor="tools.sync_apt_estado",
                )
                print(f"  ✓ {p['numero']:18} (trámite {p['tramite']:>10}) → {estado}")
                actualizados += 1
            else:
                print(f"  ⚠ {p['numero']:18} (trámite {p['tramite']:>10}) → no encontrado en APT")
        except Exception as exc:
            print(f"  ✗ {p['numero']:18} → error: {exc}")
        time.sleep(1.5)

    print(f"\n{actualizados}/{len(pendientes)} estados actualizados.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
