"""Inspecciona campos hidden y form completo de bP4 para entender por qué
APT guarda el nombre TSE aunque sobrescribamos los campos visibles."""
from __future__ import annotations
import io, json, os, sys
from pathlib import Path
os.environ.setdefault("CATASTRO_BOT_DEV_MODE", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://localhost:9222")
    page = next(pg for pg in browser.contexts[0].pages if "apt.cfia.or.cr" in pg.url)
    info = page.evaluate(
        r"""() => {
            const panel = document.querySelector('#P4');
            if (!panel) return {};
            const allInputs = [...panel.querySelectorAll('input,select,textarea')].map(el => ({
                tag: el.tagName,
                type: el.type,
                id: el.id,
                name: el.name,
                value: el.value,
                visible: el.offsetParent !== null
            }));
            // Buscar el JS de GuardarTitular para entender qué envía
            const scripts = [...document.querySelectorAll('script')].map(s => s.textContent || '')
                .filter(s => /function\s+GuardarTitular\s*\(/.test(s)).slice(0,1);
            return {
                inputs: allInputs,
                guardar_titular_fn: scripts[0] ? scripts[0].slice(scripts[0].indexOf('function GuardarTitular'), scripts[0].indexOf('function GuardarTitular') + 1500) : null
            };
        }"""
    )
    print(json.dumps(info, ensure_ascii=False, indent=2))
