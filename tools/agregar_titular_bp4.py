"""Agrega titulares ADICIONALES en la sección bP4 del plano APT abierto.

Asume que:
  - El Chrome del usuario está corriendo con CDP (ver tools/start-chrome-bot.bat)
  - La pestaña APT del plano del expediente target está abierta
  - El propietario del contrato YA está registrado (bC1) y NO se repite en bP4

USO:
  python tools/agregar_titular_bp4.py RDF-2026-002
"""
from __future__ import annotations
import io
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

os.environ.setdefault("CATASTRO_BOT_DEV_MODE", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright
from src.agents.apt_agent import APTAgent
from src.core.database import Database
from src.core.credential_manager import CredentialManager


def main() -> int:
    if len(sys.argv) < 2:
        print("USO: python tools/agregar_titular_bp4.py <NUMERO_EXPEDIENTE>")
        return 1
    numero = sys.argv[1].upper()

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
    meta = json.loads(row["metadata_json"] or "{}")
    titulares = ((meta.get("datos_apt") or {}).get("plano") or {}).get("titulares") or []
    if not titulares:
        print(f"[INFO] {numero} no tiene titulares adicionales en datos_apt.plano.titulares")
        return 0
    print(f"[INFO] Titulares a registrar: {len(titulares)}")
    for i, t in enumerate(titulares, 1):
        print(f"  {i}. tipo={t.get('tipo_cedula')} ced={t.get('cedula')} titularidad={t.get('titularidad','5')}")

    agent = APTAgent(db, creds)
    if not agent._cdp_disponible():
        print("[ERROR] CDP no disponible — abre Chrome con tools/start-chrome-bot.bat")
        return 1

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        ctx = browser.contexts[0]
        page = None
        for pg in ctx.pages:
            if "apt.cfia.or.cr" in pg.url and "Contrato/Nuevo" in pg.url:
                page = pg
                break
        if not page:
            print("[ERROR] no encontró pestaña APT con Contrato/Nuevo abierto")
            return 1
        print(f"[OK] pestaña APT: {page.url[:90]}")
        page.bring_to_front()

        # Asegurar tab PLANOS
        page.evaluate(
            """() => {
                const links = Array.from(document.querySelectorAll('a'));
                const pl = links.find(a => a.innerText.trim() === 'PLANOS' || (a.href || '').includes('#plano'));
                if (pl) pl.click();
            }"""
        )
        time.sleep(1.5)

        agent.reset_discrepancias_rnp()
        agent._llenar_seccion_plano_titulares(page, titulares)
        print(f"\n[OK] {len(titulares)} titular(es) procesado(s).")

        # ── Procesar discrepancias RNP/TSE detectadas durante el llenado ──
        if agent.discrepancias_rnp:
            from src.agents.apt_discrepancia_handler import (
                procesar_discrepancias, resolver_telefono_topografo,
            )
            # Buscar el topógrafo responsable (es quien debe gestionar la
            # corrección registral en RNP si aplica). Si no aparece en la
            # tabla `usuarios`, cae al admin por defecto.
            exp_full = db.obtener_expediente(row["id"]) if hasattr(db, "obtener_expediente") else None
            topografo_phone = resolver_telefono_topografo(db, exp_full or {})
            # Recoger teléfonos de admins activos
            try:
                admins = db.listar_usuarios(rol="admin", activo=True)  # type: ignore[attr-defined]
                telefonos = [a.get("telefono") for a in admins if a.get("telefono")]
            except Exception:
                telefonos = []
            # Intentar enviar por WhatsApp si el orchestrator está disponible
            send_fn = None
            try:
                from src.agents.whatsapp_agent import WhatsAppAgent
                wa = WhatsAppAgent(db, creds)
                send_fn = wa.enviar_mensaje
            except Exception as exc:
                print(f"[warn] WhatsApp no disponible: {exc}")
            res = procesar_discrepancias(
                db=db,
                expediente_id=row["id"],
                numero_expediente=numero,
                discrepancias=agent.discrepancias_rnp,
                whatsapp_send_fn=send_fn,
                admins_phones=telefonos,
                topografo_phone=topografo_phone,
            )
            print()
            print("═" * 60)
            print(f"⚠️  {res['cantidad']} DISCREPANCIA(S) REGISTRAL DETECTADA(S)")
            tip = "sí" if res["notificado_topografo"] else "no (no encontrado en usuarios)"
            print(f"   Topógrafo notificado: {tip}")
            print(f"   Admins notificados:   {res['notificados_admin']}")
            print(f"   Persistidas en metadata.apt_discrepancias_rnp")
            print("═" * 60)
            print(res["mensaje"])
            print("═" * 60)
        else:
            print("Sin discrepancias registrales detectadas.")

        print("\nVerifica que bP4 quede en verde antes de ENVIAR AL CFIA.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
