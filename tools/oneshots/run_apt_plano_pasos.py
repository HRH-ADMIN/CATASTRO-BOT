"""APT — sección PLANO paso a paso, interactivo.

Asume que el contrato ya fue creado (existe `apt_tramite` en metadata_json).
Conecta vía CDP, abre el plano del trámite y va llenando las 7 secciones
del plano (Generales, Fincas, Planos a Modificar, Enteros, Archivos).
Termina con ENVIAR AL CFIA si el operador lo confirma.

USO:
  python tools/run_apt_plano_pasos.py <NUMERO_EXP>

Ej:
  python tools/run_apt_plano_pasos.py RDF-2026-001
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


def pause(msg: str = "Enter para continuar (q para salir)") -> bool:
    print(f"\n>>> {msg}: ", end="", flush=True)
    try:
        ans = input().strip().lower()
    except EOFError:
        return False
    return ans != "q"


def main() -> int:
    if len(sys.argv) < 2:
        print("USO: python tools/run_apt_plano_pasos.py <NUMERO_EXP>")
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
        print(f"[ERROR] {numero_exp} no tiene datos_apt en metadata_json.")
        return 1
    plano = datos_apt.get("plano") or {}
    if not plano:
        print(f"[ERROR] {numero_exp} no tiene datos_apt.plano. ")
        print(f"        Llene la sección plano del expediente primero.")
        return 1

    print("═" * 70)
    print(f"  APT PLANO PASO A PASO — {numero_exp}")
    print("═" * 70)
    print()
    print("datos_apt.plano cargados:")
    print(json.dumps(plano, ensure_ascii=False, indent=2))

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
            # Buscar pestaña APT con un trámite/contrato abierto
            page = None
            for c in browser.contexts:
                for pg in c.pages:
                    try:
                        if "apt.cfia.or.cr/APT2/Contrato" in pg.url:
                            page = pg
                            break
                    except Exception:
                        continue
                if page:
                    break
            if not page:
                print("[ERROR] No hay pestaña en /APT2/Contrato/ — abra el contrato primero.")
                return 1

            page.bring_to_front()
            print(f"[OK] Pestaña: {page.url}")

            # Click en el tab PLANOS si no estamos ahí
            page.evaluate(
                """() => {
                    const links = Array.from(document.querySelectorAll('a'));
                    const pl = links.find(a => a.innerText.trim() === 'PLANOS' || (a.href || '').includes('#plano'));
                    if (pl) pl.click();
                }"""
            )
            time.sleep(1.5)
            print("[OK] Tab PLANOS abierto")

            # ── Sección bP1 Generales ──────────────────────────────────────
            print("\n══════ bP1 GENERALES ══════")
            if not pause("Llenar sección Generales del plano?"):
                return 0
            max_planos = (datos_apt.get("general") or {}).get("max_planos", "1")
            agent._llenar_seccion_plano_generales(page, plano)
            print("[OK] Generales llenados.")
            if not pause("Hago clic en Guardar de Generales?"):
                return 0
            page.evaluate(
                """() => {
                    const b = Array.from(document.querySelectorAll('button'))
                        .find(x => /GuardarDatosGenerales/.test(x.getAttribute('onclick') || ''));
                    if (b) b.click();
                }"""
            )
            time.sleep(3)
            agent._aceptar_modal_swal(page)

            # ── bP2 Fincas ─────────────────────────────────────────────────
            print("\n══════ bP2 FINCAS ══════")
            fincas = plano.get("fincas") or []
            print(f"  Hay {len(fincas)} finca(s) a registrar")
            if fincas and pause("Registrar fincas?"):
                agent._llenar_seccion_plano_fincas(page, fincas)
                print(f"[OK] {len(fincas)} fincas registradas.")

            # ── bP4 Titulares (adicionales) ────────────────────────────────
            print("\n══════ bP4 TITULARES ══════")
            titulares = plano.get("titulares") or []
            print(f"  Hay {len(titulares)} titular(es) adicional(es) a registrar")
            print("  (el propietario del contrato NO se repite aquí)")
            if titulares and pause("Registrar titulares adicionales?"):
                agent._llenar_seccion_plano_titulares(page, titulares)
                print(f"[OK] {len(titulares)} titular(es) adicional(es) registrado(s).")

            # ── bP5 Planos a Modificar ─────────────────────────────────────
            print("\n══════ bP5 PLANOS A MODIFICAR ══════")
            planos_mod = plano.get("planos_modificar") or []
            print(f"  Hay {len(planos_mod)} plano(s) que se modifican")
            if planos_mod and pause("Registrar planos a modificar?"):
                agent._llenar_seccion_plano_planos_modificar(page, planos_mod)
                print(f"[OK] {len(planos_mod)} planos a modificar registrados.")

            # ── bP6 Enteros ────────────────────────────────────────────────
            print("\n══════ bP6 ENTEROS ══════")
            entero = plano.get("entero") or {}
            if entero and pause(f"Registrar entero {entero.get('numero')}?"):
                agent._llenar_seccion_plano_enteros(page, entero)
                print("[OK] Entero registrado.")

            # ── bP7 Archivos ───────────────────────────────────────────────
            print("\n══════ bP7 ARCHIVOS ══════")
            archivos = plano.get("archivos") or {}
            if pause("Subir archivos del plano?"):
                if archivos.get("anverso"):
                    if pause(f"  Subir ANVERSO ({archivos['anverso']})?"):
                        ok = agent._subir_archivo_plano(page, APTAgent.TIPO_ARCHIVO_ANVERSO, archivos["anverso"])
                        print(f"  ANVERSO: {'✓' if ok else '✗'}")
                if archivos.get("entero"):
                    if pause(f"  Subir ENTERO ({archivos['entero']})?"):
                        ok = agent._subir_archivo_plano(page, APTAgent.TIPO_ARCHIVO_ENTERO, archivos["entero"])
                        print(f"  ENTERO: {'✓' if ok else '✗'}")
                if archivos.get("derrotero"):
                    if pause(f"  Subir DERROTERO ({archivos['derrotero']})?"):
                        ok = agent._subir_archivo_plano(page, APTAgent.TIPO_ARCHIVO_DERROTERO, archivos["derrotero"])
                        print(f"  DERROTERO: {'✓' if ok else '✗'}")
                # VISADO solo en R2 (después de respuesta CFIA + visado muni)
                if archivos.get("visado"):
                    if pause(f"  Subir VISADO ({archivos['visado']})? [solo R2]"):
                        ok = agent._subir_archivo_plano(page, APTAgent.TIPO_ARCHIVO_VISADO, archivos["visado"])
                        print(f"  VISADO: {'✓' if ok else '✗'}")

            # ── ENVÍO FINAL AL CFIA ────────────────────────────────────────
            print("\n══════ ENVÍO AL CFIA ══════")
            print("  ⚠️ ESTE PASO ES IRREVERSIBLE")
            print("  Tras enviar, el plano queda en revisión 7 días en CFIA.")
            if pause("¿ENVIAR AL CFIA ahora?"):
                ok = agent._enviar_plano_cfia(page)
                if ok:
                    print("\n  ✅ PLANO ENVIADO AL CFIA EXITOSAMENTE")
                    db.actualizar_metadata(
                        exp["id"],
                        {"apt_estado": "enviado_cfia", "apt_envio_fecha": time.strftime("%Y-%m-%d")},
                        actor="tools.run_apt_plano_pasos",
                    )
                    print("  ✅ metadata actualizada (apt_estado=enviado_cfia)")
                else:
                    print("\n  ⚠️ El envío puede haber fallado — revise su Chrome")
            else:
                print("Hasta aquí. Cuando esté listo, dele clic en 'ENVIAR AL CFIA' manualmente.")
            return 0
        finally:
            with __import__("contextlib").suppress(Exception):
                browser.close()


if __name__ == "__main__":
    sys.exit(main())
