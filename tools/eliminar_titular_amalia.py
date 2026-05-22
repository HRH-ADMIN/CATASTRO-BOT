"""Elimina el titular AMALIA QUESADA (cédula 2-0440-0388) agregado por error en bP4."""
from __future__ import annotations
import io, os, sys, time
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
        page.bring_to_front()
        page.evaluate("document.querySelector('#bP4')?.click();")
        time.sleep(0.6)

        # Click en el botón de eliminar del titular con cédula 204400388 (AMALIA)
        ok = page.evaluate(
            r"""() => {
                // Buscar todos los onclick que sean SeleccionarTitular y emparejar con su EliminarTitular hermano
                const all = [...document.querySelectorAll('[onclick]')];
                const sel = all.find(el =>
                    /SeleccionarTitular\([^)]*204400388[^)]*\)/.test(el.getAttribute('onclick') || '')
                );
                if (!sel) return {found: false};
                // El botón Eliminar (.close) está en la misma "card" del titular
                const card = sel.closest('div,li,tr') || sel.parentElement;
                if (!card) return {found: false};
                let elim = card.querySelector('[onclick^="EliminarTitular"], button.close[onclick*="EliminarTitular"]');
                // fallback: buscar entre hermanos cercanos
                if (!elim) {
                    const parent = sel.parentElement?.parentElement;
                    elim = parent?.querySelector('[onclick^="EliminarTitular"]');
                }
                if (!elim) return {found: false, reason: 'no eliminar btn next to AMALIA'};
                elim.click();
                return {found: true, onclick: elim.getAttribute('onclick')};
            }"""
        )
        print(f"click eliminar: {ok}")
        if not ok or not ok.get("found"):
            # plan B: invocar directamente la función con los args conocidos
            print("[fallback] llamando EliminarTitular directamente...")
            page.evaluate("EliminarTitular('fcB1J1hLs3bMEA4jo+Ax3g==', 1406284);")

        time.sleep(2.0)
        # APT suele pedir confirmación con swal2
        page.evaluate(
            """() => {
                const b = document.querySelector('button.swal2-confirm');
                if (b && b.offsetParent) b.click();
            }"""
        )
        time.sleep(2.0)
        # segundo modal "exitoso"
        page.evaluate(
            """() => {
                const b = document.querySelector('button.swal2-confirm');
                if (b && b.offsetParent) b.click();
            }"""
        )
        time.sleep(1.0)
        print("[OK] Eliminación enviada. Verifica en pantalla.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
