"""Lista TODOS los campos del form (visibles y ocultos), agrupados por seccion
inferida del nombre del campo.
"""
from __future__ import annotations
import io
import sys
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright


SECCIONES_KW = [
    ("Propietario", ["propietari"]),
    ("Contratante", ["contratante"]),
    ("Profesional", ["profesional"]),
    ("Protocolo",   ["protocolo", "tomo", "asiento", "folio"]),
    ("Proyecto",    ["proyecto", "tipoplano", "tipoacto", "tipo_proyecto", "ubicacion", "provincia", "canton", "distrito", "naturaleza"]),
    ("Controversias", ["controversia"]),
    ("Firmas",      ["firma"]),
    ("General/Otro",   []),
]


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        try:
            apt_page = None
            for c in browser.contexts:
                for pg in c.pages:
                    if "apt.cfia.or.cr/APT2/Contrato" in pg.url:
                        apt_page = pg
                        break
                if apt_page:
                    break
            if not apt_page:
                print("No hay pestaña APT en /Contrato/")
                return 1

            print(f"URL: {apt_page.url}\n")

            js = """
            () => {
                const out = [];
                document.querySelectorAll('input, select, textarea').forEach((el) => {
                    let label = '';
                    if (el.id) {
                        const lbl = document.querySelector(`label[for="${el.id}"]`);
                        if (lbl) label = lbl.innerText.trim();
                    }
                    if (!label && el.placeholder) label = '(ph) ' + el.placeholder;
                    if (!label && el.name) label = '(name) ' + el.name;
                    let opciones = '';
                    if (el.tagName === 'SELECT') {
                        const opts = Array.from(el.options).slice(0, 25).map(o => `${o.value}=${o.text.trim()}`);
                        opciones = opts.join(' | ').substring(0, 350);
                    }
                    out.push({
                        tag: el.tagName.toLowerCase(),
                        type: el.type || '',
                        id: el.id || '',
                        name: el.name || '',
                        value: (el.value || '').substring(0, 80),
                        required: el.required,
                        label: label.substring(0, 100),
                        opciones,
                    });
                });
                return out;
            }
            """
            campos = apt_page.evaluate(js)
            print(f"Total campos en form: {len(campos)}\n")

            # Clasificar por sección
            por_seccion = {sec: [] for sec, _ in SECCIONES_KW}
            for c in campos:
                key = (c["id"] + " " + c["name"] + " " + c["label"]).lower()
                ubicado = False
                for sec, kws in SECCIONES_KW:
                    if sec == "General/Otro":
                        continue
                    if any(kw in key for kw in kws):
                        por_seccion[sec].append(c)
                        ubicado = True
                        break
                if not ubicado:
                    por_seccion["General/Otro"].append(c)

            for sec, _ in SECCIONES_KW:
                items = por_seccion[sec]
                if not items:
                    continue
                print(f"══════ {sec} ({len(items)}) ══════")
                for c in items:
                    req = "*" if c["required"] else " "
                    print(f"  {req} {c['tag']:8}{c['type']:12} #{c['id']:35} {c['value']!r:25} «{c['label']}»")
                    if c["opciones"]:
                        print(f"      OPCIONES: {c['opciones']}")
                print()

        finally:
            browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
