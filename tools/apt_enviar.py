"""APT ENVIAR — click #BtnEnviarAgrimensura del trámite abierto en Chrome.

Es el botón "ENVIAR AL CFIA" del editor de plano. NO requiere Firma Digital
(esa viene después, en inscripción). Después de este click el trámite pasa
de "En Edición" a "Calificación RN".

USO:
  python tools/apt_enviar.py SEG-2026-005

Pre-requisitos:
  - Chrome del bot abierto en la pestaña del contrato/plano del expediente
  - Sesión APT activa
  - bC1-bC8 + bP1-bP7 todos completos (íconos verdes)
"""
from __future__ import annotations
import io
import os
import sys
import time
from pathlib import Path

os.chdir(r"C:\catastro-bot")
os.environ.setdefault("CATASTRO_BOT_DEV_MODE", "1")
sys.path.insert(0, os.getcwd())
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")

from src.agents.apt_agent import APTAgent
from src.core.credential_manager import CredentialManager
from src.core.database import Database


def main() -> int:
    if len(sys.argv) < 2:
        print("USO: python tools/apt_enviar.py <NUMERO_EXP>")
        return 1
    numero_exp = sys.argv[1].upper().strip()

    creds = CredentialManager()
    db = Database(Path("data/catastro.db"), creds)
    exp = db.buscar_por_numero(numero_exp)
    if not exp:
        print(f"[ERROR] {numero_exp} no existe")
        return 1

    import json
    meta = json.loads(exp.get("metadata_json") or "{}")
    tramite = meta.get("apt_tramite", "?")

    print("=" * 70)
    print(f"  APT ENVIAR — {numero_exp} (trámite {tramite})")
    print("=" * 70)

    agent = APTAgent(db, creds)
    if not agent._cdp_disponible():
        print("[ERROR] Chrome del bot no corriendo")
        return 2

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(agent._cdp_endpoint)
        page = None
        for c in browser.contexts:
            for pg in c.pages:
                try:
                    if "apt.cfia.or.cr" in pg.url and "Contrato" in pg.url:
                        page = pg
                        break
                except Exception:
                    continue
            if page:
                break
        if not page:
            print("[ERROR] No hay pestaña apt.cfia.or.cr/Contrato/... abierta")
            print("       Navegá al trámite en Chrome del bot primero.")
            return 3
        page.bring_to_front()
        print(f"[OK] Pestaña: {page.url[:120]}")

        # Verificar que estado tab PLANOS sea visible (donde está el botón)
        time.sleep(1.0)
        try:
            page.evaluate(
                """() => {
                    // Click en tab Planos si no está activo
                    const tab = document.querySelector('a[href=\"#tab2\"], a[aria-controls=\"tab2\"]');
                    if (tab && tab.click) tab.click();
                }"""
            )
        except Exception:
            pass
        time.sleep(1.0)

        # Confirmar que existe el botón
        existe = page.evaluate(
            """() => !!document.querySelector('#BtnEnviarAgrimensura')"""
        )
        if not existe:
            print("[ERROR] #BtnEnviarAgrimensura no existe en esta página.")
            print("        ¿Está abierto el plano correcto?")
            return 4

        # Confirmación humana
        print()
        print("Vas a ENVIAR a CFIA el trámite {} ({}).".format(tramite, numero_exp))
        print("Después de esto NO se puede modificar el plano por 7 días.")
        resp = input(">>> Escribí 'ENVIAR' para confirmar: ").strip().upper()
        if resp != "ENVIAR":
            print("[ABORTADO]")
            return 0

        # Hacer el click + confirmar modal
        print("\n[CLICK] #BtnEnviarAgrimensura...")
        ok = agent._enviar_plano_cfia(page)
        time.sleep(2.0)

        if ok:
            print("[OK] Envío exitoso.")
            # Marcar estado en BD
            import sqlite3
            conn = sqlite3.connect("data/catastro.db")
            conn.execute(
                "UPDATE expedientes SET estado_actual=?, "
                "fecha_actualizacion=datetime('now') WHERE numero_expediente=?",
                ("presentado_apt_r1", numero_exp),
            )
            conn.commit()
            print(f"[OK] Estado BD -> presentado_apt_r1")
            return 0
        else:
            print("[WARN] El bot no confirmó éxito del envío. Verificá visualmente.")
            return 5


if __name__ == "__main__":
    sys.exit(main())
