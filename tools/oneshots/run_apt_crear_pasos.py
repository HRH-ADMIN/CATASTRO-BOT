"""APT CREAR — runner paso a paso, interactivo, con pausas entre secciones.

Conecta al Chrome del usuario via CDP, navega a Nuevo Contrato y va llenando
sección por sección, parando para que el operador inspeccione antes de continuar.

USO:
  python tools/run_apt_crear_pasos.py SEG-2026-001
"""
from __future__ import annotations
import io
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("CATASTRO_BOT_DEV_MODE", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from src.agents.apt_agent import APTAgent
from src.core.database import Database
from src.core.credential_manager import CredentialManager
from config.settings import APT_CONTRATO_URL


def pause(msg: str = "Enter para continuar (q para salir)") -> bool:
    print(f"\n>>> {msg}: ", end="", flush=True)
    try:
        ans = input().strip().lower()
    except EOFError:
        return False
    return ans != "q"


def main() -> int:
    if len(sys.argv) < 2:
        print("USO: python tools/run_apt_crear_pasos.py <NUMERO_EXP>")
        return 1
    numero_exp = sys.argv[1].upper().strip()

    creds = CredentialManager()
    db = Database(Path("data/catastro.db"), creds)
    db.initialize_schema()

    exp = db.buscar_por_numero(numero_exp)
    if not exp:
        print(f"[ERROR] Expediente {numero_exp} no existe.")
        return 1

    meta = json.loads(exp.get("metadata_json") or "{}")
    datos_apt = meta.get("datos_apt") or {}
    if not datos_apt:
        print(f"[ERROR] Expediente {numero_exp} no tiene datos_apt en metadata_json.")
        print(f"        Ejecute primero: python tools/seed_datos_apt_seg.py")
        return 1

    print("═" * 70)
    print(f"  APT CREAR PASO A PASO — {numero_exp}")
    print("═" * 70)
    print()
    print("datos_apt cargados:")
    print(json.dumps(datos_apt, ensure_ascii=False, indent=2))

    if not pause("Listo? Voy a conectar via CDP a su Chrome"):
        return 0

    agent = APTAgent(db, creds)
    if not agent._cdp_disponible():
        print("[ERROR] CDP no disponible — abra Chrome con tools/start-chrome-bot.bat")
        return 1

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(agent._cdp_endpoint)
        try:
            # Buscar pestaña APT
            page = None
            for c in browser.contexts:
                for pg in c.pages:
                    try:
                        if "apt.cfia.or.cr" in pg.url:
                            page = pg
                            break
                    except Exception:
                        continue
                if page:
                    break
            if not page:
                print("[ERROR] No hay pestaña apt.cfia.or.cr abierta.")
                print("        Asegúrese de tener sesión APT activa primero.")
                return 1

            page.bring_to_front()
            print(f"[OK] Pestaña APT: {page.url}")

            # Verificar sesión
            print("\n[PASO 0] Verificando sesión APT...")
            if not agent._confirmar_sesion_apt(page):
                print("[ERROR] Sesión APT no activa.")
                return 1
            print("[OK] Sesión APT activa.")

            # Navegar a Nuevo Contrato
            if not pause("Navegar a APT2/Contrato/Nuevo?"):
                return 0
            page.goto(APT_CONTRATO_URL, wait_until="load", timeout=60000)
            time.sleep(2.0)  # esperar que JS de APT termine de rendear modal/dropdowns
            print(f"[OK] En: {page.url}")

            # Cerrar el modal "Tipo de Proyecto" que APT abre automáticamente
            print("\n[PASO 0b] Cerrar modal 'Tipo de Proyecto' (Plano Simple)...")
            tipo_modal = datos_apt.get("protocolo", {}).get("tipo_proyecto_modal", "27")
            ok = agent._cerrar_modal_tipo_proyecto(page, tipo_proyecto=tipo_modal)
            if ok:
                print(f"[OK] Modal cerrado con tipo_proyecto={tipo_modal}")
            else:
                print("[INFO] Modal no estaba presente (o ya cerrado)")

            secciones = [
                ("PROPIETARIO", lambda: agent._llenar_seccion_propietario(page, datos_apt.get("propietario", {}))),
                ("CONTRATANTE", lambda: agent._llenar_seccion_contratante(page, datos_apt)),
                ("PROFESIONAL", lambda: agent._llenar_seccion_profesional(page, datos_apt.get("profesional", {}))),
                ("PROTOCOLO",   lambda: agent._llenar_seccion_protocolo(page, datos_apt.get("protocolo", {}))),
                ("PROYECTO",    lambda: agent._llenar_seccion_proyecto(page, datos_apt.get("proyecto", {}), max_planos=(datos_apt.get("general") or {}).get("max_planos", "1"))),
                ("CONTROVERSIAS", lambda: agent._llenar_seccion_controversias(page, datos_apt.get("controversias", {}))),
                ("GENERAL",     lambda: agent._llenar_seccion_general(page, datos_apt.get("general", {}))),
                ("FIRMAS",      lambda: agent._llenar_seccion_firmas(page, datos_apt.get("firmas", {}))),
            ]

            for nombre, fn in secciones:
                print(f"\n══════ {nombre} ══════")
                if not pause(f"Llenar sección {nombre}?"):
                    return 0
                try:
                    fn()
                    print(f"[OK] {nombre} llenada.")
                except Exception as exc:
                    print(f"[ERROR] {nombre}: {exc}")
                    if not pause("Continuar de todos modos?"):
                        return 1

            print("\n══════ REVISIÓN FINAL ══════")
            print("Revise el formulario en su Chrome.")
            print("Llene cualquier campo que el bot no supo (ej: cantón, distrito).")
            if not pause("¿Hago clic en GUARDAR?"):
                print("Hasta aquí. La pestaña queda abierta para que guarde manualmente.")
                return 0

            from src.agents.apt_agent import SEL_BTN_GUARDAR
            btn = page.locator(SEL_BTN_GUARDAR)
            if btn.count() > 0:
                btn.first.click()
                print("[OK] Click en GUARDAR — esperando carga...")
                try:
                    page.wait_for_load_state("load", timeout=60000)
                except Exception:
                    pass
                time.sleep(2)
            else:
                print(f"[WARN] No encontré {SEL_BTN_GUARDAR} — guarde manualmente.")
                pause("Presione Enter cuando ya guardó")

            tramite = agent._extraer_tramite_de_pagina(page)
            print()
            print("═" * 60)
            if tramite:
                print(f"  ✅ TRAMITE: {tramite}")
                if pause(f"Guardar apt_tramite={tramite} en metadata?"):
                    db.actualizar_metadata(
                        exp["id"], {"apt_tramite": tramite},
                        actor="tools.run_apt_crear_pasos",
                    )
                    print(f"[OK] Guardado.")
            else:
                print("  ⚠️ No se pudo extraer trámite — léalo en pantalla.")
                print(f"  Use:  APT TRAMITE {numero_exp} <numero>")
            print("═" * 60)
            return 0
        finally:
            with __import__("contextlib").suppress(Exception):
                browser.close()


if __name__ == "__main__":
    sys.exit(main())
