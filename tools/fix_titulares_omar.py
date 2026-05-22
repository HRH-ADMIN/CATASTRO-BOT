"""Reagrega OMAR (borrado por error) y normaliza GRACE a mayúsculas en bP4.

Aprovecha el descubrimiento empírico:
  - INSERT (primer guardado): APT enforce el nombre TSE
  - UPDATE (editar uno ya guardado vía SeleccionarTitular): respeta nombre del form

Flow:
  1. Add OMAR como nuevo titular: cédula → TSE responde OMAR → guarda OK
  2. Edit GRACE: click sobre su card → form se llena → sobrescribir mayúsculas → save
"""
from __future__ import annotations
import io, os, sys, time
from pathlib import Path
os.environ.setdefault("CATASTRO_BOT_DEV_MODE", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from playwright.sync_api import sync_playwright


def add_titular_insert(page, *, tipo, cedula, titularidad="5"):
    """Insert simple — confiando en que TSE responda con el nombre correcto."""
    page.evaluate(f"""() => {{
        const sel = document.querySelector('#ddlTipoIdentificacion');
        sel.value = {tipo!r};
        sel.dispatchEvent(new Event('change', {{bubbles:true}}));
    }}""")
    time.sleep(0.5)
    page.evaluate(f"""() => {{
        const el = document.querySelector('#txtIdentificacion');
        el.focus();
        el.value = {cedula!r};
        el.dispatchEvent(new Event('input', {{bubbles:true}}));
        el.dispatchEvent(new Event('change', {{bubbles:true}}));
        el.dispatchEvent(new KeyboardEvent('keyup', {{bubbles:true, key:'End'}}));
        el.dispatchEvent(new Event('blur', {{bubbles:true}}));
    }}""")
    time.sleep(4.0)  # esperar TSE
    page.evaluate(f"""() => {{
        const sel = document.querySelector('#ddlTitularidad');
        sel.value = {titularidad!r};
        sel.dispatchEvent(new Event('change', {{bubbles:true}}));
    }}""")
    time.sleep(0.4)
    page.evaluate("GuardarTitular();")
    time.sleep(2.5)
    page.evaluate("document.querySelector('button.swal2-confirm')?.click();")
    time.sleep(1.5)


def edit_titular(page, *, cedula_buscar, nombre, ap1, ap2):
    """Click sobre un titular ya guardado → cargar en form → sobrescribir → save."""
    cedula_digits = "".join(c for c in cedula_buscar if c.isdigit())
    # Click en SeleccionarTitular del record que matchee la cédula
    ok = page.evaluate(
        rf"""() => {{
            const sels = [...document.querySelectorAll('[onclick^="SeleccionarTitular"]')];
            const target = sels.find(el =>
                new RegExp('SeleccionarTitular\\\([^)]*{cedula_digits}[^)]*\\\)').test(el.getAttribute('onclick')||'')
            );
            if (!target) return false;
            target.click();
            return true;
        }}"""
    )
    if not ok:
        print(f"  [WARN] no encontré titular con cédula {cedula_buscar}")
        return False
    time.sleep(1.0)

    # Form ya lleno — sobrescribir nombres
    for sel, val in [("#txtNombreTitular", nombre),
                     ("#txtApellido1Titular", ap1),
                     ("#txtApellido2Titular", ap2)]:
        page.evaluate(f"""() => {{
            const el = document.querySelector({sel!r});
            el.value = {val!r};
            el.dispatchEvent(new Event('input', {{bubbles:true}}));
            el.dispatchEvent(new Event('change', {{bubbles:true}}));
            el.dispatchEvent(new Event('blur', {{bubbles:true}}));
        }}""")
    # Save
    page.evaluate("GuardarTitular();")
    time.sleep(2.5)
    page.evaluate("document.querySelector('button.swal2-confirm')?.click();")
    time.sleep(1.5)
    return True


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        page = next(pg for pg in browser.contexts[0].pages if "apt.cfia.or.cr" in pg.url)
        page.bring_to_front()
        page.evaluate("document.querySelector('#bP4')?.click();")
        time.sleep(0.6)

        print("[1] Reagregando OMAR ARIAS RAMIREZ (cédula 2-0310-0121)...")
        add_titular_insert(page, tipo="1", cedula="2-0310-0121", titularidad="5")
        print("    OK")

        print("[2] Editando GRACE → normalizando a mayúsculas...")
        ok = edit_titular(page, cedula_buscar="2-0440-0388",
                          nombre="GRACE", ap1="ALVAREZ", ap2="GONZALEZ")
        print("    OK" if ok else "    FALLÓ")

        # Verificar resultado
        print("\n[3] Estado final bP4:")
        finals = page.evaluate(
            r"""() => [...document.querySelectorAll('[onclick^="SeleccionarTitular"]')]
                .map(el => el.getAttribute('onclick'))"""
        )
        for s in finals:
            print(f"    {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
