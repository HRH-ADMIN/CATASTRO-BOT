"""APT PLANO no-interactivo — corre el llenado de las 7 secciones bP1-bP7.

USO:
  python tools/run_apt_plano_auto.py RDF-2026-003
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

from src.agents.apt_agent import APTAgent, APTAnomalyError
from src.agents.apt_anomaly_handler import handle_anomaly
from src.core.database import Database
from src.core.credential_manager import CredentialManager


def _notificar_y_salir(*, db, exp, numero_exp, creds, descripcion, contexto="", detalle="", page=None):
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
    except Exception:
        pass
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


def main() -> int:
    if len(sys.argv) < 2:
        print("USO: python tools/run_apt_plano_auto.py <NUMERO_EXP>")
        return 1
    numero_exp = sys.argv[1].upper().strip()

    creds = CredentialManager()
    db = Database(Path("data/catastro.db"), creds)
    db.initialize_schema()

    exp = db.buscar_por_numero(numero_exp)
    if not exp:
        print(f"[ERROR] {numero_exp} no existe")
        return 1
    meta = json.loads(exp.get("metadata_json") or "{}")
    datos_apt = meta.get("datos_apt") or {}
    plano = datos_apt.get("plano") or {}
    if not plano:
        print("[ERROR] datos_apt.plano vacío")
        return 1

    print("═" * 70)
    print(f"  APT PLANO (auto) — {numero_exp}")
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
        print("🚨" * 30)
        try:
            from src.utils.desktop_notify import notificar_escritorio
            notificar_escritorio(
                titulo=f"catastro-bot — Pre-flight falló ({numero_exp})",
                mensaje=f"{len(pre.errores)} error(es) en datos_apt",
                urgencia="alta",
            )
        except Exception:
            pass
        return 1
    if pre.advertencias:
        print(f"⚠️  {len(pre.advertencias)} advertencia(s):")
        for w in pre.advertencias:
            print(f"    • {w}")

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

        # Tab PLANOS
        print("\n[PASO 1] Click tab PLANOS...")
        page.evaluate(
            """() => {
                const links = Array.from(document.querySelectorAll('a'));
                const pl = links.find(a => /^\\s*PLANOS\\s*$/.test(a.innerText) || (a.href || '').includes('plano-tab'));
                if (pl) pl.click();
            }"""
        )
        time.sleep(2.0)

        # ── RESUME: verificar progreso saved + live antes de empezar ──
        from src.utils.apt_progreso import (
            obtener_progreso, marcar_seccion_completa, debe_skip_seccion,
            verificar_progreso_en_apt, sincronizar_con_live, resumen_progreso,
        )
        progreso = obtener_progreso(db, exp["id"])
        estados_live = verificar_progreso_en_apt(page)
        if estados_live:
            progreso = sincronizar_con_live(db, exp["id"], estados_live)
        ya_completas = [s for s, v in progreso.get("secciones", {}).items()
                       if v and s.startswith("bP")]
        if ya_completas:
            print(f"\n📋 Progreso detectado — secciones ya en verde: {' '.join(ya_completas)}")
            print("   Se hará SKIP de las completas y solo se llenarán las pendientes.")

        # Cada sección con circuit breaker — cualquier APTAnomalyError detiene
        # el ciclo y dispara notificación al operador.
        # Las tuplas son (id_seccion, nombre_legible, lambda_fn).
        pasos = [
            ("bP1", "bP1 GENERALES", lambda: (
                agent._llenar_seccion_plano_generales(page, plano),
                time.sleep(1),
                page.evaluate(
                    """() => {
                        const b = Array.from(document.querySelectorAll('button'))
                            .find(x => /GuardarDatosGenerales/.test(x.getAttribute('onclick') || ''));
                        if (b) b.click();
                    }"""
                ),
                time.sleep(3),
                agent._aceptar_modal_swal(page),
            )),
            ("bP2", "bP2 FINCAS", lambda: (
                agent._llenar_seccion_plano_fincas(page, plano.get("fincas") or [])
                if plano.get("fincas") else None
            )),
            ("bP4", "bP4 TITULARES", lambda: (
                agent._llenar_seccion_plano_titulares(page, plano.get("titulares") or [])
                if plano.get("titulares") else None
            )),
            ("bP5", "bP5 PLANOS A MODIFICAR", lambda: (
                agent._llenar_seccion_plano_planos_modificar(page, plano.get("planos_modificar") or [])
                if plano.get("planos_modificar") else None
            )),
            ("bP6", "bP6 ENTEROS", lambda: (
                agent._llenar_seccion_plano_enteros(page, plano.get("entero") or {})
                if plano.get("entero") else None
            )),
        ]
        for sec_id, nombre, fn in pasos:
            print(f"\n══════ {nombre} ══════")
            if debe_skip_seccion(progreso, sec_id):
                print(f"[SKIP] {sec_id} ya está completa (resume desde estado saved/live)")
                continue
            try:
                fn()
                print(f"[OK] {nombre}")
                # Persistir progreso después de cada paso exitoso
                marcar_seccion_completa(db, exp["id"], sec_id)
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

        # bP7 Archivos — soporta resume por tipo
        print("\n══════ bP7 ARCHIVOS ══════")
        archivos = plano.get("archivos") or {}
        # Re-leer progreso por si bP7 ya tenía archivos subidos
        progreso = obtener_progreso(db, exp["id"])
        for tipo, ruta in [
            ("anverso",   archivos.get("anverso")),
            ("entero",    archivos.get("entero")),
            ("derrotero", archivos.get("derrotero")),
        ]:
            if not ruta:
                continue
            if debe_skip_seccion(progreso, "bP7", archivo=tipo):
                print(f"  [SKIP] {tipo} ya subido (resume)")
                continue
            tipo_cod = {
                "anverso":   APTAgent.TIPO_ARCHIVO_ANVERSO,
                "entero":    APTAgent.TIPO_ARCHIVO_ENTERO,
                "derrotero": APTAgent.TIPO_ARCHIVO_DERROTERO,
            }[tipo]
            ok = agent._subir_archivo_plano(page, tipo_cod, ruta)
            print(f"  [{tipo:9}] {'OK' if ok else 'FAIL'} {ruta}")
            if ok:
                marcar_seccion_completa(db, exp["id"], "bP7", archivo=tipo)

        # Estado final de las 7 secciones
        print("\n[FINAL] Estados bP1-bP7:")
        estados = page.evaluate(
            """() => {
                const r = {};
                for (const id of ['bP1','bP2','bP3','bP4','bP5','bP6','bP7']) {
                    const el = document.querySelector('#' + id);
                    r[id] = el?.querySelector('i')?.className || 'no-icon';
                }
                return r;
            }"""
        )
        for k, v in estados.items():
            ok = "✅" if "check-circle" in v else ("❌" if "times-circle" in v else "?")
            print(f"  {ok} {k}: {v}")

        # Discrepancias detectadas durante este llenado
        if agent.discrepancias_rnp:
            print(f"\n⚠️ {len(agent.discrepancias_rnp)} discrepancia(s) durante plano:")
            for d in agent.discrepancias_rnp:
                print(f"  • [{d.get('tipo')}] {d.get('descripcion','')[:120]}")

        return 0


if __name__ == "__main__":
    sys.exit(main())
