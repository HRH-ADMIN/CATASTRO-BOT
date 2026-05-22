"""Guarda el contrato APT ya llenado en pantalla — paso explícito de aprobación.

Política de oficina: el bot NUNCA guarda automáticamente. Después de que
`run_apt_crear_auto.py` llene todas las secciones bC1-bC8, el operador
revisa el formulario en Chrome y, si todo está bien, corre este script
para hacer el click GUARDAR y capturar el número de trámite.

Si APT responde con un modal inesperado (no es "éxito"), se dispara el
circuit breaker — MessageBox centrada, WhatsApp al topógrafo, no se
persiste nada en BD.

USO:
  python tools/guardar_contrato_apt.py RDF-2026-004
"""
from __future__ import annotations
import io, json, os, sys, time
from pathlib import Path

os.environ.setdefault("CATASTRO_BOT_DEV_MODE", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from src.agents.apt_agent import APTAgent, SEL_BTN_GUARDAR
from src.agents.apt_anomaly_handler import handle_anomaly
from src.agents.apt_discrepancia_handler import (
    procesar_discrepancias, resolver_telefono_topografo,
)
from src.core.database import Database
from src.core.credential_manager import CredentialManager


def main() -> int:
    if len(sys.argv) < 2:
        print("USO: python tools/guardar_contrato_apt.py <NUMERO_EXP>")
        return 1
    numero_exp = sys.argv[1].upper().strip()

    creds = CredentialManager()
    db = Database(Path("data/catastro.db"), creds)
    db.initialize_schema()

    exp = db.buscar_por_numero(numero_exp)
    if not exp:
        print(f"[ERROR] {numero_exp} no existe")
        return 1

    agent = APTAgent(db, creds)
    if not agent._cdp_disponible():
        print("[ERROR] CDP no disponible")
        return 1

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(agent._cdp_endpoint)
        page = None
        for c in browser.contexts:
            for pg in c.pages:
                if "apt.cfia.or.cr" in pg.url and "Contrato/Nuevo" in pg.url:
                    page = pg
                    break
            if page:
                break
        if not page:
            print("[ERROR] No hay pestaña Contrato/Nuevo abierta")
            return 1
        page.bring_to_front()
        print(f"[OK] Pestaña: {page.url[:80]}")

        # Confirmación final por consola (doble safety)
        print()
        print("═" * 60)
        print(f"  Vas a guardar el contrato de {numero_exp}.")
        print("  Asegúrate de haber revisado las 8 secciones bC1-bC8 en Chrome.")
        print("═" * 60)
        try:
            ans = input(">>> Presiona ENTER para GUARDAR (Ctrl+C para abortar): ")
        except (KeyboardInterrupt, EOFError):
            print("\n[ABORTADO] No se hizo click en GUARDAR")
            return 0

        # Click GUARDAR
        print("\n[GUARDAR] Click...")
        btn = page.locator(SEL_BTN_GUARDAR)
        if btn.count() == 0:
            print(f"[ERROR] No encontré {SEL_BTN_GUARDAR}")
            return 1
        btn.first.click()
        try:
            page.wait_for_load_state("load", timeout=60000)
        except Exception:
            pass
        time.sleep(3.0)
        print("[OK] Click ejecutado")

        # Verificar respuesta de APT
        anomalia = agent._detectar_anomalia_modal(page)
        if anomalia and not any(
            ok_pat in (anomalia.get("title", "").lower() + " " + anomalia.get("html", "").lower())
            for ok_pat in ("éxito", "exito", "guardado", "exitoso")
        ):
            # Anomalía — disparar circuit breaker
            topografo_phone = resolver_telefono_topografo(db, exp)
            try:
                admins = db.listar_usuarios(rol="admin", activo=True)
                telefonos = [a.get("telefono") for a in admins if a.get("telefono")]
            except Exception:
                telefonos = []
            send_fn = None
            try:
                from src.agents.whatsapp_agent import WhatsAppAgent
                send_fn = WhatsAppAgent(db, creds).enviar_mensaje
            except Exception:
                pass
            handle_anomaly(
                db=db, expediente_id=exp["id"], numero_expediente=numero_exp,
                descripcion=(
                    f"APT rechazó el GUARDAR: '{anomalia.get('title','')}' — "
                    f"{anomalia.get('html','')}"
                ),
                contexto="GUARDAR contrato",
                detalle=str(anomalia),
                whatsapp_send_fn=send_fn,
                admins_phones=telefonos,
                topografo_phone=topografo_phone,
            )
            return 1

        # Cerrar modal de éxito
        page.evaluate("document.querySelector('button.swal2-confirm')?.click();")
        time.sleep(2)

        # Extraer trámite
        tramite = agent._extraer_tramite_de_pagina(page)
        print()
        print("═" * 60)
        if tramite:
            print(f"  ✅ CONTRATO GUARDADO — TRAMITE APT: {tramite}")
            db.actualizar_metadata(
                exp["id"],
                {"apt_tramite": tramite, "apt_estado": "en_llenado_plano"},
                actor="tools.guardar_contrato_apt",
            )
            print(f"  Persistido apt_tramite={tramite} en metadata.")
        else:
            print(f"  ⚠️ Contrato guardado pero no se pudo extraer trámite.")
            print(f"     Léelo en pantalla y registra con: APT TRAMITE {numero_exp} <numero>")
        print("═" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
