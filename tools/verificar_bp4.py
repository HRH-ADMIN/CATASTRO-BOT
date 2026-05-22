"""Verifica el estado de la sección bP4 después de agregar un titular."""
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
        ctx = browser.contexts[0]
        page = None
        for pg in ctx.pages:
            if "apt.cfia.or.cr" in pg.url:
                page = pg
                break
        if not page:
            print("[ERROR] no encontró tab APT")
            return 1

        info = page.evaluate(
            """() => {
                const out = {};
                // estado del header bP4 (clase 'completado'/'pendiente'/etc.)
                const btn = document.querySelector('#bP4');
                out.btn_class = btn ? btn.className : null;
                out.btn_text  = btn ? btn.innerText.trim() : null;
                // posibles tablas/listas dentro del panel P4
                const panel = document.querySelector('#P4');
                if (panel) {
                    const rows = panel.querySelectorAll('tbody tr');
                    out.rows = [...rows].map(r => r.innerText.replace(/\\s+/g,' ').trim());
                    // valores actuales en formulario
                    out.tipo_id = (panel.querySelector('#ddlTipoIdentificacion') || {}).value;
                    out.cedula  = (panel.querySelector('#txtIdentificacion') || {}).value;
                    out.titularidad = (panel.querySelector('#ddlTitularidad') || {}).value;
                    out.nombre = (panel.querySelector('#txtNombreTitular') || {}).value;
                    out.ap1    = (panel.querySelector('#txtApellido1Titular') || {}).value;
                    out.ap2    = (panel.querySelector('#txtApellido2Titular') || {}).value;
                }
                // chequeo del estado de los headers bP1..bP7 (color/clase)
                out.estados = {};
                for (const id of ['bP1','bP2','bP3','bP4','bP5','bP6','bP7']) {
                    const el = document.querySelector('#' + id);
                    if (el) out.estados[id] = {
                        cls: el.className,
                        icon: el.querySelector('i,span.fa,span.glyphicon')?.className || null
                    };
                }
                return out;
            }"""
        )
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0


if __name__ == "__main__":
    sys.exit(main())
