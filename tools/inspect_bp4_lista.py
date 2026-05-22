"""Inspecciona la LISTA de titulares ya guardados en bP4 (tabla y botones)."""
from __future__ import annotations
import io
import json
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
        page = next((pg for pg in browser.contexts[0].pages if "apt.cfia.or.cr" in pg.url), None)
        if not page:
            print("[ERROR] no APT tab")
            return 1

        # Asegurar bP4 expandido
        page.evaluate("document.querySelector('#bP4')?.click();")
        import time; time.sleep(0.8)

        info = page.evaluate(
            r"""() => {
                // Buscar TODA tabla cercana a bP4 con titulares listados
                const panel = document.querySelector('#P4');
                const result = {tables: [], buttons: [], list_html: null};
                if (!panel) return result;
                // tablas dentro O hermanas (a veces el list está fuera del panel)
                const tables = [...panel.querySelectorAll('table')];
                for (const t of tables) {
                    result.tables.push({
                        id: t.id, class: t.className,
                        headers: [...t.querySelectorAll('th')].map(h => h.innerText.trim()),
                        rows: [...t.querySelectorAll('tbody tr')].map(r => ({
                            text: r.innerText.replace(/\s+/g,' ').trim(),
                            buttons: [...r.querySelectorAll('button,a.btn,i')].map(b => ({
                                text: (b.innerText || '').trim(),
                                title: b.title || '',
                                cls: b.className || '',
                                onclick: b.getAttribute('onclick') || '',
                                href: b.getAttribute('href') || ''
                            }))
                        }))
                    });
                }
                // todos los botones del panel
                result.buttons = [...panel.querySelectorAll('button,a')].map(b => ({
                    text: (b.innerText || '').trim().slice(0,40),
                    onclick: b.getAttribute('onclick') || '',
                    cls: b.className || ''
                })).filter(x => x.onclick || /elimin|borrar|delete|quitar/i.test(x.text));
                return result;
            }"""
        )
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0


if __name__ == "__main__":
    sys.exit(main())
