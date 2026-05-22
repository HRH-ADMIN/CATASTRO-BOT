"""Lee la lista de fincas guardadas en bP2 y los datos de RNP que trae APT
(propietarios, áreas, etc.) para encontrar la cédula correcta de cada dueño.
"""
from __future__ import annotations
import io, json, os, sys, time
from pathlib import Path
os.environ.setdefault("CATASTRO_BOT_DEV_MODE", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://localhost:9222")
    page = next((pg for pg in browser.contexts[0].pages if "apt.cfia.or.cr" in pg.url), None)
    if not page:
        print("[ERROR] no APT tab")
        sys.exit(1)
    page.bring_to_front()
    # Expandir bP2
    page.evaluate("document.querySelector('#bP2')?.click();")
    time.sleep(1.0)
    info = page.evaluate(
        r"""() => {
            const panel = document.querySelector('#P2');
            if (!panel) return {error: 'no P2'};
            // Volcar todo el texto del panel + onclick relevantes
            const onclicks = [...panel.querySelectorAll('[onclick]')].map(el => ({
                onclick: el.getAttribute('onclick'),
                text: (el.innerText || '').replace(/\s+/g, ' ').trim().slice(0,100)
            })).filter(x => /Finca|Titular|Propietario/i.test(x.onclick || ''));
            return {
                text: panel.innerText.replace(/\s+/g, ' '),
                onclicks: onclicks
            };
        }"""
    )
    print(json.dumps(info, ensure_ascii=False, indent=2))
