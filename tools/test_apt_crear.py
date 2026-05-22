"""APT CREAR paso-a-paso, interactivo via consola.

Conecta al Chrome del usuario via CDP, navega a Nuevo Contrato y muestra
qué va a llenar campo por campo. Antes de cada paso pregunta:
  s = sí, hacer este paso
  n = saltar este paso (lo lleno manualmente en el Chrome)
  e = editar el valor antes de llenar
  q = cancelar todo

USO:
  .venv/Scripts/python tools/test_apt_crear.py SEG-2026-001
"""
from __future__ import annotations

import io
import os
import sys
import time
from pathlib import Path

# Modo dev: sqlite plano (la BD del bot ya esta así en este equipo)
os.environ.setdefault("CATASTRO_BOT_DEV_MODE", "1")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from src.core.database import Database
from src.core.credential_manager import CredentialManager
from src.agents.apt_agent import APTAgent, APTSesionRequeridaError
from config.settings import DATABASE_PATH, APT_CONTRATO_URL


_TIPO_MAP = {
    "segregacion":           "Segregación",
    "reunion_de_fincas":     "Reunión de Fincas",
    "rectificacion":         "Rectificación de Área",
    "informacion_posesoria": "Información Posesoria",
    "fincas_completas":      "Plano de Finca Completa",
}


def prompt(msg: str, default: str = "s") -> str:
    """Pregunta interactiva: s/n/e/q."""
    print(f"\n>>> {msg}  [s/n/e/q] ({default}): ", end="", flush=True)
    try:
        ans = input().strip().lower() or default
    except EOFError:
        ans = "q"
    return ans[:1] if ans else default


def show_field(label: str, value: str) -> None:
    print(f"   {label:30} = {value!r}")


def main() -> int:
    if len(sys.argv) < 2:
        print("USO: test_apt_crear.py <NUMERO_EXPEDIENTE>")
        print("Ej:  test_apt_crear.py SEG-2026-001")
        return 1

    numero_exp = sys.argv[1].upper().strip()

    # Cargar expediente
    creds = CredentialManager()
    db = Database(DATABASE_PATH, creds)
    db.initialize_schema()
    exp = db.buscar_por_numero(numero_exp)
    if not exp:
        print(f"[ERROR] Expediente {numero_exp!r} no existe en BD")
        return 1

    print("=" * 60)
    print(f"  APT CREAR — paso a paso — {numero_exp}")
    print("=" * 60)
    print()
    print("Datos del expediente cargados de BD:")
    show_field("numero_expediente", exp.get("numero_expediente", ""))
    show_field("tipo_plano", exp.get("tipo_plano", ""))
    show_field("nombre_cliente", exp.get("nombre_cliente", ""))
    show_field("nombre_topografo", exp.get("nombre_topografo", ""))
    show_field("estado_actual", exp.get("estado_actual", ""))
    print()

    tipo_texto = _TIPO_MAP.get(exp.get("tipo_plano", ""), exp.get("tipo_plano", ""))
    topografo  = exp.get("nombre_topografo", "") or ""
    print("Mapeos / valores derivados:")
    show_field("tipo_plano_para_APT", tipo_texto)
    show_field("topografo (truncado 80)", topografo[:80])
    print()

    if prompt("¿Procedo? (s=continuar, q=salir)") not in ("s", ""):
        print("Cancelado.")
        return 0

    # Conectar CDP
    agent = APTAgent(db, creds)
    if not agent._cdp_disponible():
        print("[ERROR] CDP no disponible — Chrome del usuario no está corriendo.")
        print("        Ejecute tools/start-chrome-bot.bat primero.")
        return 1

    print("\n[OK] CDP detectado.")

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(agent._cdp_endpoint)
        try:
            if not browser.contexts:
                print("[ERROR] Chrome sin contextos.")
                return 1

            # Buscar pestaña APT (en TODOS los contextos)
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

            if page is None:
                print("[INFO] No hay pestaña APT abierta — ejecute APT SESION primero.")
                return 1

            print(f"[OK] Pestaña APT encontrada: {page.url}")
            page.bring_to_front()

            # Verificar sesión
            print("\n[PASO 0] Verificar sesión APT (navega a APT_HOME) ...")
            if not agent._confirmar_sesion_apt(page):
                print("[ERROR] Sesión APT no activa. Re-firme con APT SESION.")
                return 1
            print("[OK] Sesión APT activa.")

            # Paso 1: navegar a Nuevo Contrato
            if prompt("Navegar a APT2/Contrato/Nuevo?") == "s":
                page.goto(APT_CONTRATO_URL, wait_until="networkidle")
                print(f"[OK] En: {page.url}")
            else:
                print("Saltado.")

            # Paso 2: tipo de plano
            ans = prompt(f"Seleccionar tipo de plano = '{tipo_texto}'?")
            if ans == "e":
                tipo_texto = input("   Nuevo valor: ").strip() or tipo_texto
                print(f"   Usaré: {tipo_texto}")
                ans = "s"
            if ans == "s":
                # Click bC5
                if page.locator("#bC5").count() > 0:
                    page.locator("#bC5").click()
                    time.sleep(0.4)
                aplicado = False
                for sel in ["#ddlTipoPlano", "#TipoActo", "select[name*='tipo']", "select[name*='Tipo']"]:
                    if page.locator(sel).count() > 0:
                        try:
                            page.select_option(sel, label=tipo_texto)
                            print(f"[OK] Seleccionado en {sel}: {tipo_texto}")
                            aplicado = True
                            break
                        except Exception as e:
                            print(f"[WARN] {sel}: {e}")
                if not aplicado:
                    print("[WARN] No se encontró el dropdown. Llénelo manualmente en su Chrome.")

            # Paso 3: profesional / topógrafo
            ans = prompt(f"Llenar profesional/topógrafo = '{topografo[:80]}'?")
            if ans == "e":
                topografo = input("   Nuevo valor: ").strip() or topografo
                ans = "s"
            if ans == "s":
                if page.locator("#bC3").count() > 0:
                    page.locator("#bC3").click()
                    time.sleep(0.3)
                aplicado = False
                for sel in ["#txtNombreProfesional", "#txtCedulaProfesional", "input[name*='Profesional']"]:
                    elem = page.locator(sel)
                    if elem.count() > 0:
                        try:
                            cur = elem.first.input_value()
                            if cur:
                                print(f"[INFO] Campo {sel} ya tenía: {cur!r}")
                            else:
                                elem.first.fill(topografo[:80])
                                print(f"[OK] {sel} ← {topografo[:80]!r}")
                            aplicado = True
                        except Exception as e:
                            print(f"[WARN] {sel}: {e}")
                if not aplicado:
                    print("[INFO] Llene la sección Profesional manualmente si hace falta.")

            # PAUSA DE REVISIÓN
            print()
            print("=" * 60)
            print("  AHORA REVISE EN SU CHROME el formulario.")
            print("  Llene cualquier campo adicional que falte.")
            print("=" * 60)
            ans = prompt("Cuando esté listo: ¿Hago clic en GUARDAR? (s/n=usted lo guarda)")
            if ans == "s":
                from src.agents.apt_agent import SEL_BTN_GUARDAR
                if page.locator(SEL_BTN_GUARDAR).count() > 0:
                    page.locator(SEL_BTN_GUARDAR).first.click()
                    print("[OK] Click en Guardar — esperando carga...")
                    page.wait_for_load_state("networkidle")
                    time.sleep(1.5)
                else:
                    print(f"[WARN] No encontré {SEL_BTN_GUARDAR}. Guarde manualmente.")
                    input("   Presione Enter cuando ya guardó manualmente...")
            else:
                input("   Guarde manualmente. Presione Enter cuando termine...")

            # Extraer trámite
            tramite = agent._extraer_tramite_de_pagina(page)
            print()
            print("=" * 60)
            if tramite:
                print(f"  ✅ TRAMITE EXTRAÍDO: {tramite}")
                print("=" * 60)
                if prompt(f"¿Guardar apt_tramite={tramite} en BD para {numero_exp}?") == "s":
                    db.actualizar_metadata(
                        exp["id"], {"apt_tramite": tramite},
                        actor="tools.test_apt_crear",
                    )
                    print(f"[OK] Guardado en metadata.apt_tramite")
            else:
                print(f"  ⚠️  No se pudo extraer el número de trámite automáticamente.")
                print(f"  Léalo en pantalla y use: APT TRAMITE {numero_exp} <numero>")
                print("=" * 60)

            print("\n[FIN] Pestaña APT queda abierta en su Chrome.")
            return 0

        finally:
            with __import__("contextlib").suppress(Exception):
                browser.close()


if __name__ == "__main__":
    sys.exit(main())
