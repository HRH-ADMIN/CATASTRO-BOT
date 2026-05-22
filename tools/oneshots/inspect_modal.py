"""Inspecciona modal swal2 abierto en este momento."""
from __future__ import annotations
import io, os, sys, time
from pathlib import Path
os.environ.setdefault("CATASTRO_BOT_DEV_MODE", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://localhost:9222")
    page = next((pg for pg in browser.contexts[0].pages if "apt.cfia.or.cr" in pg.url), None)
    page.bring_to_front()
    # Disparar EliminarTitular y observar el modal que aparece
    page.evaluate("EliminarTitular('fcB1J1hLs3bMEA4jo+Ax3g==',1406284);")
    time.sleep(1.5)
    info = page.evaluate(
        """() => {
            const popup = document.querySelector('.swal2-popup');
            if (!popup || popup.offsetParent === null) return {visible: false};
            return {
                visible: true,
                title: document.querySelector('.swal2-title')?.innerText || '',
                html:  document.querySelector('.swal2-html-container')?.innerText || '',
                buttons: [...popup.querySelectorAll('button')].map(b => ({
                    text: b.innerText.trim(),
                    cls:  b.className,
                    id:   b.id || ''
                }))
            };
        }"""
    )
    import json
    print(json.dumps(info, ensure_ascii=False, indent=2))
