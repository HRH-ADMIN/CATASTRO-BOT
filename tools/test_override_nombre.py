"""Prueba si APT respeta una sobrescritura manual del nombre en bP4
o si el servidor lo reemplaza por el nombre del TSE.

Pasos:
  1. Eliminar el titular actual
  2. Llenar cédula → esperar TSE
  3. Sobrescribir nombre manualmente con 'GRACE ALVAREZ GONZALEZ'
  4. Click GuardarTitular
  5. Leer lo que APT acaba de guardar — si trae AMALIA → server enforce
"""
from __future__ import annotations
import io, os, sys, time
from pathlib import Path
os.environ.setdefault("CATASTRO_BOT_DEV_MODE", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://localhost:9222")
    page = next(pg for pg in browser.contexts[0].pages if "apt.cfia.or.cr" in pg.url)
    page.bring_to_front()

    # Paso 1: eliminar el titular AMALIA recién guardado
    print("[1] Eliminando AMALIA actual...")
    page.evaluate(
        r"""() => {
            const el = [...document.querySelectorAll('[onclick]')].find(x =>
                /SeleccionarTitular\([^)]*204400388[^)]*\)/.test(x.getAttribute('onclick')||'')
            );
            if (el) {
                const card = el.closest('div,li,tr')?.parentElement?.parentElement;
                const elim = card?.querySelector('[onclick^="EliminarTitular"]');
                if (elim) elim.click();
            }
        }"""
    )
    time.sleep(1.5)
    page.evaluate("document.querySelector('button.swal2-confirm')?.click();")
    time.sleep(2.0)
    page.evaluate("document.querySelector('button.swal2-confirm')?.click();")
    time.sleep(1.5)
    print("    OK")

    # Paso 2: expandir bP4
    page.evaluate("document.querySelector('#bP4')?.click();")
    time.sleep(0.6)

    # Paso 3: setear tipo + cédula (dispara TSE)
    print("[2] Setear tipo+cédula y esperar TSE...")
    page.evaluate("""() => {
        const sel = document.querySelector('#ddlTipoIdentificacion');
        sel.value = '1';
        sel.dispatchEvent(new Event('change', {bubbles:true}));
    }""")
    time.sleep(0.5)
    page.evaluate("""() => {
        const el = document.querySelector('#txtIdentificacion');
        el.focus();
        el.value = '2-0440-0388';
        el.dispatchEvent(new Event('input', {bubbles:true}));
        el.dispatchEvent(new Event('change', {bubbles:true}));
        el.dispatchEvent(new KeyboardEvent('keyup', {bubbles:true, key:'End'}));
        el.dispatchEvent(new Event('blur', {bubbles:true}));
    }""")
    time.sleep(4.0)  # esperar TSE bien

    # Leer lo que TSE puso
    pre = page.evaluate("""() => ({
        nombre: document.querySelector('#txtNombreTitular')?.value,
        ap1: document.querySelector('#txtApellido1Titular')?.value,
        ap2: document.querySelector('#txtApellido2Titular')?.value
    })""")
    print(f"    TSE puso: {pre}")

    # Paso 4: setear titularidad
    page.evaluate("""() => {
        const sel = document.querySelector('#ddlTitularidad');
        sel.value = '5';
        sel.dispatchEvent(new Event('change', {bubbles:true}));
    }""")
    time.sleep(0.4)

    # Paso 5: SOBRESCRIBIR con GRACE
    print("[3] Sobrescribiendo con GRACE ALVAREZ GONZALEZ...")
    for sel, val in [
        ("#txtNombreTitular",    "GRACE"),
        ("#txtApellido1Titular", "ALVAREZ"),
        ("#txtApellido2Titular", "GONZALEZ"),
    ]:
        page.evaluate(f"""() => {{
            const el = document.querySelector('{sel}');
            el.value = {val!r};
            el.dispatchEvent(new Event('input', {{bubbles:true}}));
            el.dispatchEvent(new Event('change', {{bubbles:true}}));
        }}""")

    # Verificar que los campos quedaron con GRACE inmediatamente antes del save
    pre_save = page.evaluate("""() => ({
        nombre: document.querySelector('#txtNombreTitular')?.value,
        ap1: document.querySelector('#txtApellido1Titular')?.value,
        ap2: document.querySelector('#txtApellido2Titular')?.value
    })""")
    print(f"    Campos pre-save: {pre_save}")

    # Paso 6: click GuardarTitular
    print("[4] Click GuardarTitular...")
    page.evaluate("GuardarTitular();")
    time.sleep(3.0)
    page.evaluate("document.querySelector('button.swal2-confirm')?.click();")
    time.sleep(2.0)

    # Paso 7: leer lo que APT GUARDÓ realmente
    saved = page.evaluate(
        r"""() => {
            const sel = [...document.querySelectorAll('[onclick]')].find(x =>
                /SeleccionarTitular\([^)]*204400388[^)]*\)/.test(x.getAttribute('onclick')||'')
            );
            return sel ? sel.getAttribute('onclick') : null;
        }"""
    )
    print(f"\n[5] APT guardó: {saved}")
    if saved and "AMALIA" in saved.upper():
        print("\n>>> CONFIRMADO: APT IGNORÓ la sobrescritura — server-side enforce TSE.")
    elif saved and "GRACE" in saved.upper():
        print("\n>>> APT RESPETÓ la sobrescritura — GRACE quedó guardada.")
    else:
        print("\n>>> Resultado inesperado. Inspeccionar manualmente.")
