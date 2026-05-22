"""Inspecciona la sección bP4 Titulares del plano APT vía CDP."""
from __future__ import annotations
import io
import os
import sys
from pathlib import Path

os.environ.setdefault("CATASTRO_BOT_DEV_MODE", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        ctx = browser.contexts[0]
        page = None
        for pg in ctx.pages:
            if "apt.cfia.or.cr" in pg.url:
                page = pg
                break
        if not page:
            print("[ERROR] no encontró tab APT")
            return 1
        print(f"URL: {page.url}\n")

        # Expandir bP4
        page.evaluate("document.querySelector('#bP4')?.click();")
        import time
        time.sleep(1.0)

        # Obtener todos los inputs/selects/buttons dentro del panel bP4
        info = page.evaluate(
            """() => {
                // Buscar el panel del bP4 (acordeón colapsable)
                const btn = document.querySelector('#bP4');
                if (!btn) return {error: 'no bP4 button'};
                // Encontrar el div colapsable asociado: en bootstrap acordeon es siguiente sibling .panel-collapse o #div asociado
                let panel = null;
                // intentar data-target / aria-controls / href
                const target = btn.getAttribute('data-target') || btn.getAttribute('href') || ('#' + btn.getAttribute('aria-controls') || '');
                if (target) panel = document.querySelector(target);
                if (!panel) {
                    // fallback: buscar el siguiente .panel-collapse hermano del btn padre
                    let p = btn.closest('.panel-heading') || btn.closest('.card-header') || btn.parentElement;
                    if (p) panel = p.parentElement?.querySelector('.panel-collapse, .collapse');
                }
                if (!panel) return {error: 'no panel found', btn_html: btn.outerHTML};

                const inputs = [...panel.querySelectorAll('input,select,textarea')].map(el => ({
                    tag: el.tagName.toLowerCase(),
                    type: el.type || '',
                    id: el.id || '',
                    name: el.name || '',
                    placeholder: el.placeholder || '',
                    visible: el.offsetParent !== null,
                    options: el.tagName === 'SELECT' ? [...el.options].slice(0, 8).map(o => ({v: o.value, t: o.text.slice(0,40)})) : null
                }));
                const buttons = [...panel.querySelectorAll('button,a.btn,input[type=button],input[type=submit]')].map(el => ({
                    tag: el.tagName.toLowerCase(),
                    id: el.id || '',
                    text: (el.innerText || el.value || '').trim().slice(0, 60),
                    onclick: el.getAttribute('onclick') || '',
                    visible: el.offsetParent !== null
                }));
                const tables = [...panel.querySelectorAll('table')].map(t => ({
                    id: t.id || '',
                    headers: [...t.querySelectorAll('th')].map(th => th.innerText.trim()),
                    rows: t.querySelectorAll('tbody tr').length
                }));
                return {
                    panel_id: panel.id || '',
                    inputs: inputs,
                    buttons: buttons,
                    tables: tables
                };
            }"""
        )
        import json
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0


if __name__ == "__main__":
    sys.exit(main())
