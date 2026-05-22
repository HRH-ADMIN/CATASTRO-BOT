"""CLI debug — `catastro-bot debug <subcomando>`.

Inspección reutilizable del estado del bot. Migrado desde scripts ad-hoc
`tools/_inspeccionar_*.py` que se hicieron para VICTOR #2.

Subcomandos:
  bp6 <expediente>       Inspecciona campos bP6 ENTEROS del plano APT abierto
  bp7 <expediente>       Inspecciona campos bP7 ARCHIVOS del plano APT abierto
  ddl <select_id>        Lista opciones de un <select> del form APT abierto
  estado <expediente>    Snapshot completo del expediente (BD + APT actual)
  chrome                 Estado del Chrome del bot (pestañas + URL)
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
from pathlib import Path

os.chdir(r"C:\catastro-bot")
os.environ.setdefault("CATASTRO_BOT_DEV_MODE", "1")
sys.path.insert(0, os.getcwd())
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")


def _get_apt_page():
    """Devuelve la pestaña APT abierta del Chrome del bot o sale con error."""
    from playwright.sync_api import sync_playwright
    p = sync_playwright().start()
    try:
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
    except Exception:
        print("[ERROR] Chrome del bot no corriendo")
        print("  Lanzá: python tools/start_chrome_bot.py")
        sys.exit(1)
    for c in browser.contexts:
        for pg in c.pages:
            if "apt.cfia.or.cr" in pg.url:
                return p, pg
    print("[ERROR] No hay pestaña apt.cfia.or.cr abierta")
    sys.exit(2)


def cmd_bp6(args) -> int:
    """Inspecciona los inputs del bP6 ENTEROS."""
    p, page = _get_apt_page()
    try:
        page.bring_to_front()
        page.evaluate("() => { const x = document.querySelector('#P6'); "
                      "if (x) x.classList.add('show'); }")
        time.sleep(0.5)
        info = page.evaluate(r"""() => {
            const panel = document.querySelector('#P6');
            const out = {inputs: [], botones: []};
            if (!panel) return out;
            panel.querySelectorAll('input, select').forEach(el => {
                if (el.type === 'hidden') return;
                out.inputs.push({
                    id: el.id || '', name: el.name || '',
                    type: el.type, value: el.value,
                    visible: !!el.offsetParent,
                });
            });
            panel.querySelectorAll('button').forEach(b => {
                if (!b.offsetParent) return;
                out.botones.push({
                    id: b.id || '',
                    text: (b.innerText || '').slice(0, 50),
                    oc: (b.getAttribute('onclick') || '').slice(0, 80),
                });
            });
            return out;
        }""")
        print("=== Panel #P6 ENTEROS ===")
        print(f"\nInputs visibles ({len(info['inputs'])}):")
        for i in info["inputs"]:
            v = "V" if i["visible"] else "-"
            print(f"  {v} {i['type']:<8} id={i['id']:<28} value={i['value']!r}")
        print(f"\nBotones ({len(info['botones'])}):")
        for b in info["botones"]:
            print(f"  id={b['id']:<22} text={b['text']!r:<35} oc={b['oc']!r}")
    finally:
        p.stop()
    return 0


def cmd_bp7(args) -> int:
    """Inspecciona los inputs file y botones de bP7 ARCHIVOS."""
    p, page = _get_apt_page()
    try:
        page.bring_to_front()
        page.evaluate("() => { const x = document.querySelector('#P7'); "
                      "if (x) x.classList.add('show'); }")
        time.sleep(0.5)
        info = page.evaluate(r"""() => {
            const panel = document.querySelector('#P7');
            const out = {file_inputs: [], drop_zones: [], botones_carga: [],
                         archivos_subidos: []};
            if (!panel) {
                // Si no existe #P7, buscar inputs file globales
                document.querySelectorAll('input[type=file]').forEach(el => {
                    out.file_inputs.push({
                        id: el.id, name: el.name, accept: el.accept,
                        visible: !!el.offsetParent,
                    });
                });
                return out;
            }
            panel.querySelectorAll('input[type=file]').forEach(el => {
                out.file_inputs.push({
                    id: el.id, name: el.name, accept: el.accept,
                    visible: !!el.offsetParent,
                });
            });
            panel.querySelectorAll('button').forEach(b => {
                if (!b.offsetParent) return;
                const t = (b.innerText || '').trim();
                if (/cargar|subir|examinar|añadir|agregar/i.test(t)) {
                    out.botones_carga.push({id: b.id || '', text: t});
                }
            });
            // Tabla / lista de archivos ya subidos
            panel.querySelectorAll('table tbody tr').forEach(r => {
                if (r.offsetParent) {
                    out.archivos_subidos.push(
                        (r.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 120),
                    );
                }
            });
            return out;
        }""")
        print("=== Panel #P7 ARCHIVOS ===")
        print(f"\nFile inputs ({len(info['file_inputs'])}):")
        for f in info["file_inputs"]:
            print(f"  id={f['id']!r:<20} accept={f['accept'][:50]!r}")
        print(f"\nBotones carga ({len(info['botones_carga'])}):")
        for b in info["botones_carga"]:
            print(f"  - {b['text']!r}  (id={b['id']!r})")
        print(f"\nArchivos ya subidos ({len(info['archivos_subidos'])}):")
        for a in info["archivos_subidos"]:
            print(f"  - {a}")
    finally:
        p.stop()
    return 0


def cmd_ddl(args) -> int:
    """Lista las opciones de un <select> del form APT."""
    p, page = _get_apt_page()
    try:
        page.bring_to_front()
        sel = args.select_id
        if not sel.startswith("#"):
            sel = "#" + sel
        ops = page.evaluate(
            f"() => Array.from((document.querySelector({sel!r}) || "
            f"{{options: []}}).options || []).map(o => "
            f"({{value: o.value, text: (o.innerText || o.textContent || '').trim()}}))"
        )
        if not ops:
            print(f"[ERR] {sel} no existe o sin opciones")
            return 1
        print(f"Opciones de {sel}:")
        for op in ops:
            print(f"  value={op['value']:<8} text={op['text']!r}")
    finally:
        p.stop()
    return 0


def cmd_estado(args) -> int:
    """Snapshot del estado del expediente en BD."""
    import sqlite3
    conn = sqlite3.connect("data/catastro.db")
    conn.row_factory = sqlite3.Row
    r = conn.execute(
        "SELECT * FROM expedientes WHERE numero_expediente = ?",
        (args.expediente,),
    ).fetchone()
    if not r:
        print(f"[ERR] expediente {args.expediente} no existe")
        return 1
    print(f"=== {r['numero_expediente']} ===")
    print(f"  Tipo:       {r['tipo_plano']}")
    print(f"  Estado:     {r['estado_actual']}")
    print(f"  Cliente:    {r['nombre_cliente']}")
    print(f"  Topógrafo:  {r['nombre_topografo']}")
    print(f"  Creado:     {r['fecha_creacion']}")
    print(f"  Actualizado:{r['fecha_actualizacion']}")
    meta = json.loads(r["metadata_json"] or "{}")
    print(f"\n  Metadata keys: {sorted(meta.keys())}")
    if "datos_apt" in meta:
        da = meta["datos_apt"]
        g = da.get("general", {})
        plano = da.get("plano", {})
        print(f"\n  datos_apt.general:")
        for k in ("area_predio", "area_real", "honorarios", "max_planos"):
            print(f"    {k:14}: {g.get(k)!r}")
        print(f"\n  datos_apt.plano:")
        for k in ("descripcion", "area_real", "area_registro", "tamanno",
                 "tipo_uso", "tipo_zona", "vertices"):
            print(f"    {k:14}: {plano.get(k)!r}")
    if meta.get("muni_enviado"):
        print(f"\n  Muni:")
        print(f"    enviado:        {meta.get('muni_fecha_envio')}")
        print(f"    resp esperada:  {meta.get('muni_fecha_respuesta_esperada')}")
    conn.close()
    return 0


def cmd_chrome(args) -> int:
    """Estado del Chrome del bot."""
    import urllib.request
    try:
        with urllib.request.urlopen("http://localhost:9222/json/list",
                                     timeout=3) as resp:
            tabs = json.loads(resp.read().decode())
    except Exception as e:
        print(f"[ERR] Chrome CDP caído: {e}")
        return 1
    print(f"Chrome del bot — {len(tabs)} pestañas:")
    for i, t in enumerate(tabs):
        print(f"  [{i}] {t.get('title', '?')[:60]}")
        print(f"      {t.get('url', '?')[:100]}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="catastro-bot debug",
        description="Inspección reutilizable del bot",
    )
    sub = parser.add_subparsers(dest="sub")

    p_bp6 = sub.add_parser("bp6", help="Inspecciona bP6 ENTEROS")
    p_bp6.add_argument("expediente", nargs="?", default="")
    p_bp6.set_defaults(func=cmd_bp6)

    p_bp7 = sub.add_parser("bp7", help="Inspecciona bP7 ARCHIVOS")
    p_bp7.add_argument("expediente", nargs="?", default="")
    p_bp7.set_defaults(func=cmd_bp7)

    p_ddl = sub.add_parser("ddl", help="Lista opciones de un <select>")
    p_ddl.add_argument("select_id", help="ID con o sin #, ej 'ddlTamanno'")
    p_ddl.set_defaults(func=cmd_ddl)

    p_est = sub.add_parser("estado", help="Estado de un expediente en BD")
    p_est.add_argument("expediente")
    p_est.set_defaults(func=cmd_estado)

    p_chr = sub.add_parser("chrome", help="Estado del Chrome del bot")
    p_chr.set_defaults(func=cmd_chrome)

    args = parser.parse_args()
    if not args.sub:
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
