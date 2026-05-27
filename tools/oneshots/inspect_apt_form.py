"""Lista TODOS los inputs/selects/textareas del formulario APT/Contrato/Nuevo
para construir mejores selectores de automatización.
"""
from __future__ import annotations
import io
import sys
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
                    if "apt.cfia.or.cr" in pg.url:
                        apt_page = pg
                        break
                if apt_page:
                    break
            if not apt_page:
                print("No hay pestaña APT")
                return 1

            print(f"URL: {apt_page.url}\n")

            # JavaScript para extraer info estructurada de form fields
            js = """
            () => {
                const out = [];
                const fields = document.querySelectorAll('input, select, textarea');
                fields.forEach((el) => {
                    const rect = el.getBoundingClientRect();
                    const visible = rect.width > 0 && rect.height > 0;
                    // Buscar etiqueta más cercana
                    let label = '';
                    if (el.id) {
                        const lbl = document.querySelector(`label[for="${el.id}"]`);
                        if (lbl) label = lbl.innerText.trim();
                    }
                    if (!label && el.placeholder) label = '(ph) ' + el.placeholder;
                    if (!label && el.name) label = '(name) ' + el.name;
                    out.push({
                        tag: el.tagName.toLowerCase(),
                        type: el.type || '',
                        id: el.id || '',
                        name: el.name || '',
                        value: (el.value || '').substring(0, 80),
                        required: el.required || el.getAttribute('aria-required') === 'true',
                        visible: visible,
                        label: label.substring(0, 100),
                    });
                });
                return out;
            }
            """
            campos = apt_page.evaluate(js)

            # Filtrar visibles
            visibles = [c for c in campos if c["visible"]]
            print(f"Total campos: {len(campos)} (visibles: {len(visibles)})\n")

            print(f"{'#':<4}{'tag':<10}{'type':<14}{'id':<35}{'req':<5}{'value':<30}{'label'}")
            print("─" * 140)
            for i, c in enumerate(visibles):
                req = "✓" if c["required"] else " "
                print(f"{i:<4}{c['tag']:<10}{c['type']:<14}{c['id']:<35}{req:<5}{c['value']:<30}{c['label']}")

            print()
            # También listar botones visibles
            js_btns = """
            () => {
                const out = [];
                document.querySelectorAll('button, input[type=submit], input[type=button], a.btn').forEach((el) => {
                    const rect = el.getBoundingClientRect();
                    if (rect.width === 0 || rect.height === 0) return;
                    out.push({
                        tag: el.tagName.toLowerCase(),
                        id: el.id || '',
                        text: (el.innerText || el.value || '').trim().substring(0, 50),
                        type: el.type || '',
                    });
                });
                return out;
            }
            """
            btns = apt_page.evaluate(js_btns)
            print(f"Botones visibles ({len(btns)}):")
            for b in btns:
                print(f"  [{b['tag']}#{b['id']}] {b['text']!r}  type={b['type']}")

        finally:
            browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
