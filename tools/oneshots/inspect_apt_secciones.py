"""Expande TODAS las secciones del form Nuevo Contrato y lista los campos
de cada una. Da el mapa completo para construir la automatización.
"""
from __future__ import annotations
import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright


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
                print("No hay pestaña APT en /Contrato/. Vaya a Nuevo Contrato primero.")
                return 1

            secciones = [
                ("bC1", "Propietario"),
                ("bC2", "Contratante"),
                ("bC3", "Profesional"),
                ("bC4", "Protocolo"),
                ("bC5", "Proyecto"),
                ("bC6", "Controversias"),
                ("bC8", "Firmas"),
            ]

            # Expandir todas
            for sel, _ in secciones:
                try:
                    btn = apt_page.locator(f"#{sel}")
                    if btn.count() > 0:
                        btn.click()
                        time.sleep(0.3)
                except Exception as e:
                    print(f"[WARN] no se pudo expandir #{sel}: {e}")

            time.sleep(1.0)

            # JS para listar campos VISIBLES con su sección padre
            js = """
            () => {
                const out = [];
                const fields = document.querySelectorAll('input, select, textarea');
                fields.forEach((el) => {
                    const rect = el.getBoundingClientRect();
                    if (rect.width === 0 || rect.height === 0) return;
                    let label = '';
                    if (el.id) {
                        const lbl = document.querySelector(`label[for="${el.id}"]`);
                        if (lbl) label = lbl.innerText.trim();
                    }
                    if (!label && el.placeholder) label = '(ph) ' + el.placeholder;
                    if (!label && el.name) label = '(name) ' + el.name;
                    // Buscar contenedor accordion
                    let panel = '';
                    let parent = el.closest('[id^="cC"]');
                    if (parent) panel = parent.id;
                    let opciones = '';
                    if (el.tagName === 'SELECT') {
                        const opts = Array.from(el.options).slice(0, 30).map(o => `${o.value}=${o.text.trim()}`);
                        opciones = opts.join(' | ').substring(0, 200);
                    }
                    out.push({
                        panel,
                        tag: el.tagName.toLowerCase(),
                        type: el.type || '',
                        id: el.id || '',
                        value: (el.value || '').substring(0, 60),
                        required: el.required,
                        label: label.substring(0, 80),
                        opciones,
                    });
                });
                return out;
            }
            """
            campos = apt_page.evaluate(js)

            # Agrupar por panel
            secciones_map = {f"cC{n}": titulo for n, titulo in [
                (1, "Propietario"), (2, "Contratante"), (3, "Profesional"),
                (4, "Protocolo"), (5, "Proyecto"), (6, "Controversias"),
                (8, "Firmas"),
            ]}
            for panel_id, nombre in secciones_map.items():
                print(f"\n══════ {nombre} ({panel_id}) ══════")
                hay = False
                for c in campos:
                    if c["panel"] == panel_id:
                        hay = True
                        req = "*" if c["required"] else " "
                        print(f"  {req} {c['tag']:8}{c['type']:12} #{c['id']:35} = {c['value']!r:30}  «{c['label']}»")
                        if c["opciones"]:
                            print(f"      OPCIONES: {c['opciones']}")
                if not hay:
                    print("  (sin campos)")

        finally:
            browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
