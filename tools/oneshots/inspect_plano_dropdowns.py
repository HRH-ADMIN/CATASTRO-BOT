"""Inspecciona los dropdowns de la sección PLANO del trámite actual."""
from __future__ import annotations
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright


JS = r"""
() => {
    const out = {};
    const ids = ['ddlTipoUso', 'ddlTipoUbicacion', 'ddlTamanno', 'ddlTipoZona', 'ddlTipoCoordenada'];
    for (const sid of ids) {
        const el = document.querySelector('#' + sid);
        if (el) {
            out[sid] = Array.from(el.options).map(o => o.value + ' = ' + o.text.trim());
        } else {
            out[sid] = ['NO ENCONTRADO'];
        }
    }
    return out;
}
"""


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        try:
            page = None
            for c in browser.contexts:
                for pg in c.pages:
                    if "apt.cfia.or.cr" in pg.url:
                        page = pg
                        break
                if page:
                    break

            info = page.evaluate(JS)
            for k, opts in info.items():
                print(f"\n=== {k} ({len(opts)} opciones) ===")
                for o in opts:
                    print(f"   {o}")
        finally:
            browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
