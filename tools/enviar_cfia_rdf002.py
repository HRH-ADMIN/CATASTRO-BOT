"""Envía RDF-2026-002 al CFIA. IRREVERSIBLE.

Click en BtnEnviarAgrimensura → confirma modal → captura número de trámite
→ persiste en metadata + actualiza estado APT.
"""
from __future__ import annotations
import io, json, os, sqlite3, sys, time
from datetime import datetime
from pathlib import Path
os.environ.setdefault("CATASTRO_BOT_DEV_MODE", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from playwright.sync_api import sync_playwright
from src.agents.apt_agent import APTAgent
from src.core.database import Database
from src.core.credential_manager import CredentialManager


def main() -> int:
    numero = "RDF-2026-002"
    creds = CredentialManager()
    db = Database(Path("data/catastro.db"), creds)

    conn = sqlite3.connect("data/catastro.db")
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT id, metadata_json FROM expedientes WHERE numero_expediente = ?",
        (numero,),
    ).fetchone()
    conn.close()
    if not row:
        print(f"[ERROR] No existe {numero}")
        return 1

    agent = APTAgent(db, creds)
    if not agent._cdp_disponible():
        print("[ERROR] CDP no disponible")
        return 1

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        page = next((pg for pg in browser.contexts[0].pages
                    if "apt.cfia.or.cr" in pg.url and "Contrato/Nuevo" in pg.url), None)
        if not page:
            print("[ERROR] no encontró pestaña APT del plano")
            return 1
        page.bring_to_front()
        print(f"[OK] pestaña APT: {page.url[:80]}")

        print("\n══ ENVIANDO AL CFIA — IRREVERSIBLE ══")
        ok = agent._enviar_plano_cfia(page)
        if ok:
            print("\n✅ PLANO ENVIADO AL CFIA EXITOSAMENTE")
            # Persistir
            meta = json.loads(row["metadata_json"] or "{}")
            meta["apt_estado"] = "enviado_cfia"
            meta["apt_envio_fecha"] = datetime.now().isoformat(timespec="seconds")
            db.actualizar_metadata(
                row["id"],
                {"apt_estado": "enviado_cfia",
                 "apt_envio_fecha": meta["apt_envio_fecha"]},
                actor="tools.enviar_cfia",
            )
            # Capturar número de trámite si APT actualizó la URL
            time.sleep(2.0)
            print(f"\nURL post-envío: {page.url}")
            print(f"\nEstado: enviado_cfia | fecha: {meta['apt_envio_fecha']}")
            print("\nEl plano queda en revisión ~7 días en CFIA.")
            print("Bot sincronizará el estado APT cada 30 min vía scheduler.")
        else:
            print("\n❌ ENVÍO FALLÓ — revisar la pestaña en el navegador")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
