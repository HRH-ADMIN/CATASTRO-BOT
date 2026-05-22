"""CLI para flujo municipal — `catastro-bot muni <subcomando>`.

Reutiliza MunicipalityAgent existente. NO duplica lógica.

USO:
  catastro-bot muni url SEG-2026-003
      → construye URL Google Form pre-llenada y la imprime

  catastro-bot muni url SEG-2026-003 --abrir
      → abre la URL en el browser

  catastro-bot muni listar
      → expedientes que están en estado listo para muni

Subcomandos pendientes (crear cuando se necesiten):
  enviar-mensaje  → manda URL al topógrafo por WhatsApp
  consultar       → IMAP fetch respuestas de la muni
  paquete         → arma zip con los archivos para subir
"""
from __future__ import annotations

import argparse
import io
import os
import sys
import webbrowser
from pathlib import Path

os.chdir(r"C:\catastro-bot")
os.environ.setdefault("CATASTRO_BOT_DEV_MODE", "1")
sys.path.insert(0, os.getcwd())
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")

from src.agents.municipality_agent import MunicipalityAgent
from src.core.credential_manager import CredentialManager
from src.core.database import Database
from src.utils.muni_paquete import armar_paquete_muni
from src.utils.muni_uploader import (
    subir_archivo_a_picker,
    doble_chequeo_form_muni,
    cerrar_pickers_residuales,
)


def _get_agent() -> MunicipalityAgent:
    creds = CredentialManager()
    db = Database(Path("data/catastro.db"), creds)
    return MunicipalityAgent(db, creds)


def cmd_url(args: argparse.Namespace) -> int:
    """Construye URL del formulario Google Form pre-llenada."""
    agent = _get_agent()
    exp = agent.db.buscar_por_numero(args.expediente)
    if not exp:
        print(f"❌ Expediente {args.expediente!r} no existe")
        return 1
    try:
        url = agent.construir_url_formulario(exp["id"])
    except Exception as e:
        print(f"❌ Error construyendo URL: {e}")
        return 2

    print(f"\n📋 URL pre-llenada para {args.expediente}:\n")
    print(url)
    print()
    print("Para abrirla en el browser:")
    print(f"  catastro-bot muni url {args.expediente} --abrir")
    print()
    print("Lo que el topógrafo debe hacer:")
    print("  1. Abrir el link")
    print("  2. Subir DOCUMENTOS (plano firmado + ENT + minuta)")
    print("  3. Subir Archivo Shape (Derrotero.zip)")
    print("  4. Verificar dropdowns y click Enviar")

    if args.abrir:
        try:
            webbrowser.open(url)
            print("\n✅ Abierto en el browser")
        except Exception as e:
            print(f"\n⚠️  No se pudo abrir browser: {e}")
    return 0


def cmd_paquete(args: argparse.Namespace) -> int:
    """Arma PDF combinado para subir al formulario muni."""
    creds = CredentialManager()
    db = Database(Path("data/catastro.db"), creds)
    res = armar_paquete_muni(
        expediente_id=args.expediente, db=db,
        sobrescribir=args.sobrescribir,
    )
    if not res.get("ok"):
        print(f"❌ {res.get('error')}")
        return 1
    print(f"✅ Paquete armado:")
    print(f"   PDF:        {res['pdf_path']}")
    print(f"   N° citas:   {res['n_citas']}")
    print(f"   Páginas:    {res.get('n_paginas_total', '?')}")
    if res.get("archivos_incluidos"):
        print(f"   Archivos incluidos:")
        for a in res["archivos_incluidos"]:
            print(f"     ✓ {a}")
    if res.get("advertencias"):
        print(f"   Advertencias:")
        for w in res["advertencias"]:
            print(f"     ⚠️  {w}")
    return 0


def cmd_enviar(args: argparse.Namespace) -> int:
    """Flujo completo de subida muni para un expediente.

    Hace TODO menos el click final 'Enviar' (Google bloquea esa acción
    programáticamente — el operador debe darle click manual).

    Pasos:
      1. Verifica que existe el paquete PDF combinado (<n_citas>.pdf en 02_Oficina/)
      2. Construye URL pre-llenada del Google Form
      3. Abre form en Chrome del bot (asume sesión Gmail ya logueada)
      4. Sube DOCUMENTOS (PDF) al campo correcto
      5. Sube Archivo Shape (Derrotero.zip) usando FileChooser
      6. Doble chequeo final (todos los campos + ambos archivos)
      7. Avisa al operador para click manual del Enviar
    """
    import json
    import time
    from pathlib import Path
    from playwright.sync_api import sync_playwright

    agent = _get_agent()
    db = agent.db
    exp = db.buscar_por_numero(args.expediente)
    if not exp:
        print(f"❌ Expediente {args.expediente} no existe")
        return 1
    meta = json.loads(exp.get("metadata_json") or "{}")
    path_carpeta = Path(meta.get("path_carpeta", ""))
    subcarpeta   = meta.get("subcarpeta_archivos", "01_Campo")

    # 1. Verificar paquete PDF combinado existe
    print(f"\n[1/6] Verificando paquete PDF combinado...")
    paquete_dir = path_carpeta / "02_Oficina"
    pdfs_paquete = list(paquete_dir.glob("*.pdf")) if paquete_dir.exists() else []
    if not pdfs_paquete:
        print(f"❌ No hay PDF combinado en {paquete_dir}")
        print(f"   Primero corré: catastro-bot muni paquete {args.expediente}")
        return 2
    # El correcto es el que tiene "<año> - <asiento> - <X>.pdf"
    pdf_documentos = pdfs_paquete[0]
    if len(pdfs_paquete) > 1:
        # Preferir el que tiene el formato de citas
        for p in pdfs_paquete:
            if " - " in p.stem and "C" in p.stem.upper():
                pdf_documentos = p
                break
    print(f"   ✅ PDF DOCUMENTOS: {pdf_documentos.name} ({pdf_documentos.stat().st_size//1024}KB)")

    # Derrotero shape
    shape_zip = path_carpeta / subcarpeta / "Derrotero.zip"
    if not shape_zip.exists():
        # Fallback: cualquier *.zip con derrotero en nombre
        for z in (path_carpeta / subcarpeta).glob("*.zip"):
            if "derrotero" in z.name.lower():
                shape_zip = z; break
    if not shape_zip.exists():
        print(f"❌ No hay Derrotero.zip en {path_carpeta / subcarpeta}")
        return 3
    print(f"   ✅ SHAPE: {shape_zip.name} ({shape_zip.stat().st_size//1024}KB)")

    # 2. Construir URL
    print(f"\n[2/6] Construyendo URL pre-llenada...")
    url = agent.construir_url_formulario(exp["id"])
    print(f"   ✅ URL: {url[:80]}...")

    # 3. Abrir form en Chrome del bot
    print(f"\n[3/6] Abriendo form en Chrome del bot...")
    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp("http://localhost:9222")
        except Exception:
            print("❌ Chrome del bot no corriendo — lanzá: python tools/start_chrome_bot.py")
            return 4
        ctx = browser.contexts[0]
        # Cerrar pestañas viejas del form
        for pg in list(ctx.pages):
            if "docs.google.com/forms" in pg.url:
                try: pg.close()
                except Exception: pass
        time.sleep(2)
        page = ctx.new_page()
        page.goto(url, wait_until="load", timeout=60000)
        page.bring_to_front()
        time.sleep(5)
        # Verificar que cargó con prefills (no en pantalla de login)
        if "accounts.google.com/v3/signin" in page.url or "signin" in page.url.lower():
            print("❌ Form pide login — sesión Gmail no persistida")
            print("   Resolver: abrir https://mail.google.com en Chrome del bot")
            print("   y loguear con la cuenta del topógrafo. Después reintentar.")
            return 5
        print(f"   ✅ Form cargado: {page.title()}")

        # 4. Subir DOCUMENTOS
        print(f"\n[4/6] Subiendo DOCUMENTOS...")
        r1 = subir_archivo_a_picker(
            page, titulo_listitem="DOCUMENTOS",
            ruta_archivo=str(pdf_documentos), es_zip=False,
        )
        print(f"   {'✅' if r1['ok'] else '❌'} {r1.get('verif') or r1.get('error')}")
        if not r1["ok"]:
            return 6

        # Cerrar picker residual antes del siguiente (estrategia cascada)
        cierre = cerrar_pickers_residuales(page, verbose=True)
        if not cierre["ok"]:
            print(f"   ⚠️  {cierre['pickers_finales']} picker(s) "
                  f"persisten (estrategias: {cierre['estrategias_usadas']})")

        # 5. Subir SHAPE
        print(f"\n[5/6] Subiendo Archivo Shape (ZIP)...")
        r2 = subir_archivo_a_picker(
            page, titulo_listitem="archivo shape",
            ruta_archivo=str(shape_zip), es_zip=True,
        )
        print(f"   {'✅' if r2['ok'] else '❌'} {r2.get('verif') or r2.get('error')}")
        if not r2["ok"]:
            return 7

        # 6. Doble chequeo
        print(f"\n[6/6] Doble chequeo...")
        esperados = {
            "tomo":         meta.get("apt_tomo", ""),
            "asiento":      meta.get("apt_asiento", ""),
            "tramite":      meta.get("apt_tramite", ""),
            "area":         meta.get("area_m2", ""),
            "vertices":     meta.get("vertices", ""),
        }
        chk = doble_chequeo_form_muni(page, esperados)
        if chk["ok"]:
            print(f"   ✅ Todos los campos OK")
        else:
            print(f"   ⚠️  Faltantes: {chk['faltantes']}")

    print()
    print("=" * 70)
    print("  ✅ FORMULARIO LISTO PARA ENVIAR")
    print("=" * 70)
    print()
    print("  ACCIÓN MANUAL DEL OPERADOR (Google bloquea click programático):")
    print("    1. Andá a Chrome del bot → pestaña del form")
    print("    2. Scroll abajo → click 'Enviar'")
    print("    3. Confirmá que aparece 'Tu respuesta se ha registrado'")
    print()
    return 0


def cmd_revisar_respuestas(args: argparse.Namespace) -> int:
    """Polea IMAP del topógrafo y reporta respuestas muni clasificadas.

    Útil mientras esperamos respuestas (TILMAN demora ~5-7 días).
    """
    from src.utils.muni_imap_reader import (
        buscar_respuestas_muni, TIPO_ACUSE_GOOGLE, TIPO_APROBADO,
        TIPO_MOROSIDAD, TIPO_RECHAZADO, TIPO_DESCONOCIDO,
    )
    creds = CredentialManager()
    try:
        user, pw = creds.get_muni_san_ramon()
    except Exception as e:
        print(f"❌ Credenciales muni no configuradas: {e}")
        print("   Configurar con: src/setup/wizard.py")
        return 1
    print(f"📬 Conectando IMAP {user[:20]}... (últimos {args.dias} días)")
    try:
        emails = buscar_respuestas_muni(
            user=user, password=pw, dias_atras=args.dias, limit=args.limit,
        )
    except Exception as e:
        print(f"❌ Error IMAP: {e}")
        return 2
    print(f"✅ {len(emails)} email(s) encontrados")
    print()
    # Agrupar por tipo
    por_tipo = {}
    for e in emails:
        por_tipo.setdefault(e.tipo, []).append(e)
    iconos = {
        TIPO_ACUSE_GOOGLE: "📩",
        TIPO_APROBADO:    "✅",
        TIPO_MOROSIDAD:   "💰",
        TIPO_RECHAZADO:   "❌",
        TIPO_DESCONOCIDO: "❓",
    }
    for tipo in [TIPO_APROBADO, TIPO_MOROSIDAD, TIPO_RECHAZADO,
                 TIPO_ACUSE_GOOGLE, TIPO_DESCONOCIDO]:
        emails_tipo = por_tipo.get(tipo, [])
        if not emails_tipo: continue
        print(f"{iconos[tipo]} {tipo} ({len(emails_tipo)}):")
        for em in emails_tipo:
            print(f"  - {em.from_addr[:40]:<40} | {em.subject[:60]}")
            if em.tramite_apt:
                print(f"    Trámite: {em.tramite_apt}")
            if em.adjuntos:
                print(f"    Adjuntos: {', '.join(em.adjuntos)}")
            if em.monto_pendiente:
                print(f"    Monto pendiente: ₡{em.monto_pendiente:,.2f}")
        print()
    if not emails:
        print("(no se encontraron emails muni en el rango)")
    return 0


def cmd_notificar_cliente(args: argparse.Namespace) -> int:
    """Notifica al cliente por WhatsApp sobre morosidad / aprobación muni.

    Por defecto compone mensaje de MOROSIDAD. Si `--monto` no se pasa,
    intenta extraerlo del email más reciente clasificado como MOROSIDAD
    para ese trámite. Con `--dry-run` solo imprime el mensaje sin enviar.

    Tipos disponibles (--tipo):
      morosidad (default), aprobado, rechazado
    """
    import json
    from src.utils.muni_notificacion import (
        componer_mensaje_morosidad,
        componer_mensaje_aprobado,
        componer_mensaje_rechazado,
        notificar_cliente_morosidad,
    )

    creds = CredentialManager()
    db = Database(Path("data/catastro.db"), creds)
    exp = db.buscar_por_numero(args.expediente)
    if not exp:
        print(f"❌ Expediente {args.expediente!r} no existe")
        return 1
    meta = json.loads(exp.get("metadata_json") or "{}")
    nombre_cliente = exp.get("nombre_cliente") or "Cliente"
    telefono       = exp.get("telefono_cliente") or ""
    tramite_apt    = meta.get("apt_tramite")

    if not telefono:
        print(f"❌ No hay telefono_cliente en {args.expediente}")
        return 2

    monto = args.monto
    # Si no se pasa --monto y es morosidad, intentar leer del IMAP
    if args.tipo == "morosidad" and monto is None and not args.no_imap:
        print(f"📬 Buscando monto pendiente en IMAP (últimos {args.dias} días)...")
        try:
            from src.utils.muni_imap_reader import (
                buscar_respuestas_muni, TIPO_MOROSIDAD,
            )
            user, pw = creds.get_muni_san_ramon()
            emails = buscar_respuestas_muni(
                user=user, password=pw, dias_atras=args.dias, limit=30,
            )
            # Preferir email cuyo trámite matchee
            candidatos = [
                e for e in emails
                if e.tipo == TIPO_MOROSIDAD
                and (not tramite_apt or e.tramite_apt == tramite_apt)
                and e.monto_pendiente
            ]
            if candidatos:
                monto = candidatos[-1].monto_pendiente
                print(f"   ✅ Monto detectado: ₡{monto:,.2f}")
            else:
                print(f"   ⚠️  Sin email de morosidad con monto — se enviará sin monto")
        except Exception as e:
            print(f"   ⚠️  IMAP no disponible ({e}) — se enviará sin monto")

    # Componer
    if args.tipo == "morosidad":
        msg = componer_mensaje_morosidad(
            nombre_cliente=nombre_cliente,
            numero_expediente=args.expediente,
            monto_pendiente=monto,
            tramite_apt=tramite_apt,
        )
    elif args.tipo == "aprobado":
        msg = componer_mensaje_aprobado(
            nombre_cliente=nombre_cliente,
            numero_expediente=args.expediente,
            tramite_apt=tramite_apt,
        )
    elif args.tipo == "rechazado":
        msg = componer_mensaje_rechazado(
            nombre_cliente=nombre_cliente,
            numero_expediente=args.expediente,
            motivo=args.motivo or "",
            tramite_apt=tramite_apt,
        )
    else:
        print(f"❌ Tipo desconocido: {args.tipo}")
        return 3

    print()
    print("=" * 70)
    print(f"  📨 Mensaje para {nombre_cliente} ({telefono})")
    print("=" * 70)
    print(msg)
    print("=" * 70)

    if args.dry_run:
        print("\n(--dry-run — NO se envió)")
        return 0

    # Confirmar antes de enviar
    if not args.yes:
        resp = input("\n¿Enviar este mensaje? [s/N] ").strip().lower()
        if resp not in ("s", "si", "sí", "y", "yes"):
            print("(cancelado)")
            return 0

    # Enviar
    from src.agents.whatsapp_agent import WhatsAppAgent
    wa = WhatsAppAgent(db, creds)
    try:
        if args.tipo == "morosidad":
            res = notificar_cliente_morosidad(
                whatsapp_agent=wa, telefono=telefono,
                nombre_cliente=nombre_cliente,
                numero_expediente=args.expediente,
                monto_pendiente=monto, tramite_apt=tramite_apt,
            )
            ok = res.get("ok")
            idm = res.get("id_message", "")
            err = res.get("error", "")
        else:
            idm = wa.enviar_mensaje(telefono, msg)
            ok = bool(idm); err = ""
    except Exception as e:
        print(f"\n❌ Error enviando: {e}")
        return 4

    if ok:
        print(f"\n✅ Enviado (id={idm})")
        return 0
    print(f"\n❌ Falló: {err}")
    return 5


def cmd_listar(args: argparse.Namespace) -> int:
    """Lista expedientes elegibles para muni."""
    creds = CredentialManager()
    db = Database(Path("data/catastro.db"), creds)
    # Solo segregaciones y reuniones van a muni
    exps = db.listar_expedientes(completados=False)
    elegibles = [
        e for e in exps
        if e["tipo_plano"] in ("segregacion", "reunion_de_fincas")
    ]
    print(f"Expedientes elegibles para Muni: {len(elegibles)}")
    for e in elegibles:
        print(f"  - {e['numero_expediente']:<20} {e['tipo_plano']:<25} {e['estado_actual']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="catastro-bot muni",
        description="Flujo municipal (San Ramón formulario web)",
    )
    sub = parser.add_subparsers(dest="sub")

    p_url = sub.add_parser("url", help="Construir URL Google Form pre-llenada")
    p_url.add_argument("expediente",
                       help="Número de expediente (ej. SEG-2026-003)")
    p_url.add_argument("--abrir", action="store_true",
                       help="Abrir URL en el browser")
    p_url.set_defaults(func=cmd_url)

    p_paq = sub.add_parser("paquete",
                           help="Armar PDF combinado para subir a la muni")
    p_paq.add_argument("expediente",
                       help="Número de expediente (ej. SEG-2026-003)")
    p_paq.add_argument("--sobrescribir", action="store_true",
                       help="Rehacer aunque el PDF ya exista")
    p_paq.set_defaults(func=cmd_paquete)

    p_lis = sub.add_parser("listar", help="Expedientes elegibles para muni")
    p_lis.set_defaults(func=cmd_listar)

    p_env = sub.add_parser("enviar",
                           help="Flujo completo: abre form + sube ambos archivos + doble chequeo (el operador da click final)")
    p_env.add_argument("expediente", help="Número de expediente (ej. SEG-2026-003)")
    p_env.set_defaults(func=cmd_enviar)

    p_rev = sub.add_parser("revisar-respuestas",
                           help="Polear IMAP buscando respuestas muni (clasifica ACUSE/APROBADO/MOROSIDAD/RECHAZADO)")
    p_rev.add_argument("--dias", type=int, default=7,
                       help="Días atrás a escanear (default 7)")
    p_rev.add_argument("--limit", type=int, default=50,
                       help="Max emails a procesar (default 50)")
    p_rev.set_defaults(func=cmd_revisar_respuestas)

    p_not = sub.add_parser("notificar-cliente",
                           help="Avisar al cliente por WhatsApp sobre morosidad/aprobado/rechazado")
    p_not.add_argument("expediente", help="Número de expediente")
    p_not.add_argument("--tipo", default="morosidad",
                       choices=["morosidad", "aprobado", "rechazado"],
                       help="Tipo de notificación (default morosidad)")
    p_not.add_argument("--monto", type=float, default=None,
                       help="Monto pendiente ₡ (si se omite, se intenta extraer del IMAP)")
    p_not.add_argument("--motivo", default="",
                       help="Motivo del rechazo (solo para --tipo rechazado)")
    p_not.add_argument("--dias", type=int, default=14,
                       help="Días atrás a escanear IMAP para extraer monto (default 14)")
    p_not.add_argument("--no-imap", action="store_true",
                       help="No consultar IMAP — usar solo --monto si se pasó")
    p_not.add_argument("--dry-run", action="store_true",
                       help="Solo imprime el mensaje, no envía")
    p_not.add_argument("-y", "--yes", action="store_true",
                       help="Confirmar envío sin preguntar")
    p_not.set_defaults(func=cmd_notificar_cliente)

    args = parser.parse_args()
    if not args.sub:
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
