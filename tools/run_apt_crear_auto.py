"""APT CREAR no-interactivo — corre el llenado completo del contrato sin pausas.

El operador ve el progreso en Chrome y puede detener cerrando el navegador.

USO:
  python tools/run_apt_crear_auto.py RDF-2026-003
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

from src.agents.apt_agent import APTAgent, APTAnomalyError, SEL_BTN_GUARDAR
from src.agents.apt_anomaly_handler import handle_anomaly
from src.core.database import Database
from src.core.credential_manager import CredentialManager
from config.settings import APT_CONTRATO_URL


def _notificar_y_salir(*, db, exp, numero_exp, creds,
                       descripcion, contexto="", detalle="", page=None):
    """Circuit breaker: snapshot + notificación escritorio + WhatsApp + sale."""
    from src.agents.apt_discrepancia_handler import resolver_telefono_topografo
    print()
    print("🚨" * 30)
    print(f"  ANOMALÍA DETECTADA — DETENIENDO CICLO")
    print(f"  Contexto:    {contexto}")
    print(f"  Descripción: {descripcion}")
    print("🚨" * 30)

    topografo_phone = None
    telefonos_admin = []
    try:
        exp_full = db.obtener_expediente(exp["id"]) if exp else None
        topografo_phone = resolver_telefono_topografo(db, exp_full or {})
        admins = db.listar_usuarios(rol="admin", activo=True)
        telefonos_admin = [a.get("telefono") for a in admins if a.get("telefono")]
    except Exception:
        pass

    send_fn = None
    try:
        from src.agents.whatsapp_agent import WhatsAppAgent
        send_fn = WhatsAppAgent(db, creds).enviar_mensaje
    except Exception as exc:
        print(f"[warn] WhatsApp no disponible: {exc}")

    res = handle_anomaly(
        db=db,
        expediente_id=exp["id"] if exp else None,
        numero_expediente=numero_exp,
        descripcion=descripcion,
        contexto=contexto,
        detalle=detalle,
        whatsapp_send_fn=send_fn,
        admins_phones=telefonos_admin,
        topografo_phone=topografo_phone,
        page=page,
    )
    print(f"Snapshot:          {res.get('snapshot') or '(no se pudo capturar)'}")
    print(f"Notif. escritorio: {res['notif_escritorio'] or 'fallido'}")
    print(f"Notif. topógrafo:  {'sí' if res['notif_topografo'] else 'no'}")
    print(f"Notif. admins:     {res['notif_admin']}")
    print(f"Persistida en metadata: {'sí' if res['persistida'] else 'no'}")


def main() -> int:
    if len(sys.argv) < 2:
        print("USO: python tools/run_apt_crear_auto.py <NUMERO_EXP> [--save]")
        print("  --save  → al final hace click en GUARDAR. Default: NO guarda")
        print("            (el operador revisa el formulario en Chrome y luego")
        print("            corre `tools/guardar_contrato_apt.py <NUMERO_EXP>`).")
        return 1
    numero_exp = sys.argv[1].upper().strip()
    auto_save = "--save" in sys.argv[2:]

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
        print(f"[ERROR] {numero_exp} no tiene datos_apt en metadata.")
        return 1

    print("═" * 70)
    print(f"  APT CREAR (auto) — {numero_exp}")
    print("═" * 70)

    # ── PRE-FLIGHT: validar datos_apt antes de abrir Chrome ────────────
    print("\n[PRE-FLIGHT] Validando datos_apt antes de abrir navegador...")
    from src.utils.preflight_seed import validar_seed_pre_envio
    pre = validar_seed_pre_envio(datos_apt)
    if pre.errores:
        print()
        print("🚨" * 30)
        print(f"  PRE-FLIGHT FALLÓ — {len(pre.errores)} error(es) bloqueante(s):")
        for e in pre.errores:
            print(f"    ❌ {e}")
        if pre.advertencias:
            print(f"  + {len(pre.advertencias)} advertencia(s):")
            for w in pre.advertencias:
                print(f"    ⚠️  {w}")
        print("🚨" * 30)
        print()
        print("  NO se abrió el navegador. Corregir el seed y reintentar.")
        # Notificación desktop urgencia alta
        try:
            from src.utils.desktop_notify import notificar_escritorio
            notificar_escritorio(
                titulo=f"catastro-bot — Pre-flight falló ({numero_exp})",
                mensaje=f"{len(pre.errores)} error(es) en datos_apt:\n" +
                        "\n".join(f"• {e}" for e in pre.errores[:5]),
                urgencia="alta",
            )
        except Exception:
            pass
        return 1
    if pre.advertencias:
        print(f"⚠️  {len(pre.advertencias)} advertencia(s) (no bloquean, pero revisar):")
        for w in pre.advertencias:
            print(f"    • {w}")
    else:
        print("✅ Pre-flight OK")

    agent = APTAgent(db, creds)
    agent.reset_discrepancias_rnp()
    if not agent._cdp_disponible():
        print("[ERROR] CDP no disponible")
        return 1

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(agent._cdp_endpoint)
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
            print("[ERROR] No hay pestaña apt.cfia.or.cr abierta")
            return 1
        page.bring_to_front()
        print(f"[OK] Pestaña: {page.url[:80]}")

        # Sesión
        print("\n[PASO 0] Verificando sesión APT...")
        if not agent._confirmar_sesion_apt(page):
            print("[ERROR] Sesión no activa")
            return 1
        print("[OK] Sesión activa")

        # Navegar a Nuevo Contrato
        print("\n[PASO 1] Navegando a Nuevo Contrato...")
        page.goto(APT_CONTRATO_URL, wait_until="load", timeout=60000)
        time.sleep(2.5)
        print(f"[OK] En: {page.url[:80]}")

        # Modal Tipo Proyecto
        print("\n[PASO 2] Cerrando modal Tipo de Proyecto...")
        tipo_modal = datos_apt.get("protocolo", {}).get("tipo_proyecto_modal", "27")
        ok = agent._cerrar_modal_tipo_proyecto(page, tipo_proyecto=tipo_modal)
        print(f"[OK] Modal cerrado={ok}")

        # Resolver datos APT del topógrafo (multi-user support)
        from src.utils.topografo_resolver import resolver_topografo
        topo = resolver_topografo(db, exp)
        print(f"\n[topógrafo] {topo['nombre'] or '(default)'} "
              f"| protocolo activo: {topo['protocolo_activo'] or '(dropdown)'} "
              f"| correo: {topo['correo_apt']} "
              f"| fuente: {topo['fuente']}")

        # Llenar las 8 secciones — circuit breaker: cualquier APTAnomalyError
        # interrumpe el ciclo, dispara notificación de escritorio + WhatsApp,
        # y retorna con código de error.
        secciones = [
            ("bC1 PROPIETARIO",   lambda: agent._llenar_seccion_propietario(page, datos_apt.get("propietario", {}))),
            ("bC2 CONTRATANTE",   lambda: agent._llenar_seccion_contratante(page, datos_apt)),
            ("bC3 PROFESIONAL",   lambda: agent._llenar_seccion_profesional(page, datos_apt.get("profesional", {}))),
            ("bC4 PROTOCOLO",     lambda: agent._llenar_seccion_protocolo(
                page, datos_apt.get("protocolo", {}),
                protocolo_activo_topografo=topo["protocolo_activo"])),
            ("bC5 PROYECTO",      lambda: agent._llenar_seccion_proyecto(page, datos_apt.get("proyecto", {}), max_planos=(datos_apt.get("general") or {}).get("max_planos", "1"))),
            ("bC6 CONTROVERSIAS", lambda: agent._llenar_seccion_controversias(page, datos_apt.get("controversias", {}))),
            ("bC7 GENERAL",       lambda: agent._llenar_seccion_general(page, datos_apt.get("general", {}))),
            ("bC8 FIRMAS",        lambda: agent._llenar_seccion_firmas(page, datos_apt.get("firmas", {}))),
        ]
        for nombre, fn in secciones:
            print(f"\n══════ {nombre} ══════")
            try:
                fn()
                print(f"[OK] {nombre}")
            except APTAnomalyError as anom:
                _notificar_y_salir(
                    db=db, exp=exp, numero_exp=numero_exp, creds=creds,
                    descripcion=str(anom.descripcion or anom),
                    contexto=anom.contexto or nombre,
                    detalle=anom.detalle,
                    page=page,
                )
                return 1
            except Exception as exc:
                print(f"[ERROR] {nombre}: {exc}")

        print("\n[PASO 3] Esperando 3s antes de GUARDAR para que JS de APT termine validaciones...")
        time.sleep(3.0)

        # Reporte de discrepancias antes de guardar
        if agent.discrepancias_rnp:
            print(f"\n⚠️  {len(agent.discrepancias_rnp)} discrepancia(s) registral(es) detectada(s):")
            for d in agent.discrepancias_rnp:
                print(f"  • [{d.get('tipo')}] {d.get('contexto')}: {d.get('descripcion','')[:120]}")

        # Click GUARDAR — consultar shadow mode + flag --save.
        # Política de oficina: por defecto modo "manual" (operador confirma).
        # `--save` fuerza guardar incluso si shadow mode dice manual.
        from src.utils.shadow_mode import (
            should_proceed, explicar_decision,
            ACCION_GUARDAR_CONTRATO,
        )
        guardar_decision = should_proceed(
            ACCION_GUARDAR_CONTRATO,
            discrepancias=agent.discrepancias_rnp,
            has_anomaly=False,
        )
        print()
        print("─" * 60)
        print("  " + explicar_decision(
            ACCION_GUARDAR_CONTRATO,
            discrepancias=agent.discrepancias_rnp,
        ))
        print("─" * 60)
        tramite = None
        if not (auto_save or guardar_decision):
            print()
            print("═" * 60)
            print("  ⏸️  CONTRATO LLENO — PAUSA ANTES DE GUARDAR")
            print("═" * 60)
            print(f"  Revisa el formulario en Chrome (pestaña Contrato/Nuevo).")
            print(f"  Verifica cada sección bC1-bC8 antes de guardar.")
            print()
            print(f"  Cuando confirmes que está bien, corre:")
            print(f"    .venv\\Scripts\\python.exe tools/guardar_contrato_apt.py {numero_exp}")
            print("═" * 60)
        else:
            print("\n[PASO 4] Click GUARDAR...")
            btn = page.locator(SEL_BTN_GUARDAR)
            if btn.count() > 0:
                btn.first.click()
                try:
                    page.wait_for_load_state("load", timeout=60000)
                except Exception:
                    pass
                time.sleep(3.0)
                print("[OK] Click ejecutado.")
                # Verificar si APT respondió con error
                anomalia = agent._detectar_anomalia_modal(page)
                if anomalia and ("éxito" not in (anomalia.get("title", "").lower())
                                 and "exito" not in (anomalia.get("title", "").lower())):
                    _notificar_y_salir(
                        db=db, exp=exp, numero_exp=numero_exp, creds=creds,
                        descripcion=f"Modal inesperado post-GUARDAR: '{anomalia.get('title','')}' — {anomalia.get('html','')}",
                        contexto="GUARDAR contrato",
                        detalle=str(anomalia),
                        page=page,
                    )
                    return 1
            else:
                print(f"[WARN] No encontré {SEL_BTN_GUARDAR} — guardar manual")

            # Extraer trámite
            print("\n[PASO 5] Extrayendo número de trámite...")
            tramite = agent._extraer_tramite_de_pagina(page)
            print()
            print("═" * 60)
            if tramite:
                print(f"  ✅ TRAMITE APT: {tramite}")
                db.actualizar_metadata(
                    exp["id"], {"apt_tramite": tramite},
                    actor="tools.run_apt_crear_auto",
                )
                print(f"  Persistido apt_tramite={tramite} en metadata.")
            else:
                print("  ⚠️ No se pudo extraer trámite — léelo en pantalla y usa APT TRAMITE")
            print("═" * 60)

        # Procesar discrepancias (notificar topógrafo + admins)
        if agent.discrepancias_rnp:
            print("\n[PASO 6] Notificando discrepancias al topógrafo y admins...")
            from src.agents.apt_discrepancia_handler import (
                procesar_discrepancias, resolver_telefono_topografo,
            )
            exp_full = db.obtener_expediente(exp["id"])
            topografo_phone = resolver_telefono_topografo(db, exp_full or {})
            try:
                admins = db.listar_usuarios(rol="admin", activo=True)
                telefonos_admin = [a.get("telefono") for a in admins if a.get("telefono")]
            except Exception:
                telefonos_admin = []
            send_fn = None
            try:
                from src.agents.whatsapp_agent import WhatsAppAgent
                wa = WhatsAppAgent(db, creds)
                send_fn = wa.enviar_mensaje
            except Exception as exc:
                print(f"[warn] WhatsApp no disponible: {exc}")
            res = procesar_discrepancias(
                db=db,
                expediente_id=exp["id"],
                numero_expediente=numero_exp,
                discrepancias=agent.discrepancias_rnp,
                whatsapp_send_fn=send_fn,
                admins_phones=telefonos_admin,
                topografo_phone=topografo_phone,
            )
            print(f"  Discrepancias persistidas: {res['cantidad']}")
            print(f"  Topógrafo notificado:      {'sí' if res['notificado_topografo'] else 'no'}")
            print(f"  Admins notificados:        {res['notificados_admin']}")
            print()
            print(res["mensaje"])
        return 0


if __name__ == "__main__":
    sys.exit(main())
