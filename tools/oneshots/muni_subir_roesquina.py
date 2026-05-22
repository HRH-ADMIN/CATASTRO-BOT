"""ROESQUINA — script para subir el form muni San Ramón.

Hace:
  1. Combina los 3 PDFs (minuta + imagenminuta + planof) en data/temp/
  2. Construye URL pre-llenada con los datos del operador
  3. Abre el form en el Chrome del bot (CDP 9222)
  4. Sube el PDF combinado a "DOCUMENTOS"
  5. Sube Derrotero.zip a "Archivo Shape"
  6. PAUSA antes del click "Enviar" para revisión humana
"""
from __future__ import annotations
import io
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

os.chdir(r"C:\catastro-bot")
os.environ.setdefault("CATASTRO_BOT_DEV_MODE", "1")
sys.path.insert(0, os.getcwd())
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")

# ── Datos del trámite ROESQUINA ────────────────────────────────────────
ORIGEN_DIR = Path(r"A:\mega\planos apt\CANTONES\san isidro\rolando\2026\ROESQUINA")

DATOS = {
    "tomo":         "2026",
    "asiento":      "42856",
    "proyecto_apt": "1259216",
    "fecha_minuta_anio":  "2026",
    "fecha_minuta_mes":   "05",
    "fecha_minuta_dia":   "15",
    "area":         "11836.23",
    "finca":        "2 118879-000",
    "distrito":     "07 San Isidro",
    "proceso":      "Segregación",
    "tipo_acceso":  "Ruta Cantonal",
    "vertices":     "1-2-3-4-5",
    "profesional":  "luis alonso rojas herrera",
    "carne":        "it-10676",
}

# Entry IDs del form (del análisis estructural)
FORM_ID = "1FAIpQLSejhSph15X_RPtT39SjiLUYnCqjg_FB1Gq67A8mjgA-1XVXOQ"
ENTRIES = {
    "tomo":         "entry.413935745",
    "asiento":      "entry.112406539",
    "proyecto_apt": "entry.1184863793",
    "fecha":        "entry.1421348955",
    "area":         "entry.2015524080",
    "finca":        "entry.2090596021",
    "distrito":     "entry.1965655160",
    "proceso":      "entry.335206885",
    "tipo_acceso":  "entry.990422047",
    "vertices":     "entry.44837579",
    "profesional":  "entry.299608996",
    "carne":        "entry.1470141826",
}


def construir_url():
    """Arma URL pre-llenada para el form muni San Ramón."""
    params = {
        "usp":          "pp_url",
        ENTRIES["tomo"]:         DATOS["tomo"],
        ENTRIES["asiento"]:      DATOS["asiento"],
        ENTRIES["proyecto_apt"]: DATOS["proyecto_apt"],
        f'{ENTRIES["fecha"]}_year':  DATOS["fecha_minuta_anio"],
        f'{ENTRIES["fecha"]}_month': DATOS["fecha_minuta_mes"],
        f'{ENTRIES["fecha"]}_day':   DATOS["fecha_minuta_dia"],
        ENTRIES["area"]:         DATOS["area"],
        ENTRIES["finca"]:        DATOS["finca"],
        ENTRIES["distrito"]:     DATOS["distrito"],
        ENTRIES["proceso"]:      DATOS["proceso"],
        ENTRIES["tipo_acceso"]:  DATOS["tipo_acceso"],
        ENTRIES["vertices"]:     DATOS["vertices"],
        ENTRIES["profesional"]:  DATOS["profesional"],
        ENTRIES["carne"]:        DATOS["carne"],
    }
    qs = urlencode(params, safe="")
    return f"https://docs.google.com/forms/d/e/{FORM_ID}/viewform?{qs}"


def combinar_pdfs(out_path: Path) -> Path:
    """Combina minuta + imagenminuta + planof en un solo PDF."""
    from pypdf import PdfWriter, PdfReader

    archivos = [
        ORIGEN_DIR / "1077327_minuta.pdf",
        ORIGEN_DIR / "1077327_imagenminuta.pdf",
        ORIGEN_DIR / "planof.pdf",
    ]
    for f in archivos:
        if not f.exists():
            raise FileNotFoundError(f"No existe: {f}")

    writer = PdfWriter()
    for f in archivos:
        reader = PdfReader(str(f))
        for page in reader.pages:
            writer.add_page(page)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as fh:
        writer.write(fh)
    return out_path


def main() -> int:
    print("=" * 70)
    print("  ROESQUINA — Subida muni San Ramón")
    print("=" * 70)

    # 1. Combinar PDFs
    print("\n[1/5] Combinando PDFs (minuta + imagenminuta + planof)...")
    pdf_combinado = Path("data/temp/ROESQUINA_DOCUMENTOS.pdf")
    try:
        combinar_pdfs(pdf_combinado)
        size_kb = pdf_combinado.stat().st_size / 1024
        print(f"      ✅ {pdf_combinado.name} ({size_kb:.1f} KB)")
    except Exception as exc:
        print(f"      ❌ Error: {exc}")
        return 1

    # 2. Verificar Derrotero
    print("\n[2/5] Verificando Derrotero.zip...")
    derrotero = ORIGEN_DIR / "SHAPE" / "Derrotero.zip"
    if not derrotero.exists():
        print(f"      ❌ No existe: {derrotero}")
        return 2
    print(f"      ✅ {derrotero.name} ({derrotero.stat().st_size//1024} KB)")

    # 3. URL pre-llenada
    print("\n[3/5] URL pre-llenada:")
    url = construir_url()
    print(f"      {url[:100]}...")

    # 4. Abrir form en Chrome del bot
    print("\n[4/5] Abriendo form en Chrome del bot (CDP 9222)...")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("      ❌ Playwright no instalado")
        return 3

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp("http://localhost:9222")
        except Exception as exc:
            print(f"      ❌ Chrome del bot no responde: {exc}")
            return 4

        ctx = browser.contexts[0]
        # Cerrar pestañas previas del form
        for pg in list(ctx.pages):
            try:
                if "docs.google.com/forms" in pg.url:
                    pg.close()
            except Exception:
                pass
        time.sleep(1.5)

        page = ctx.new_page()
        page.goto(url, wait_until="load", timeout=60000)
        page.bring_to_front()
        time.sleep(5)

        # Verificar que cargó (no login)
        if "accounts.google.com" in page.url or "signin" in page.url.lower():
            print("      ❌ Form pide login Google — sesión no activa")
            print("         Solución: abrí gmail.com en Chrome del bot y logueá")
            print("         con la cuenta del topógrafo (topografiahrh@gmail.com)")
            return 5
        print(f"      ✅ Form cargado: {page.title()}")

        # 5. Subir archivos
        print("\n[5/5] Subiendo archivos via picker...")
        from src.utils.muni_uploader import (
            subir_archivo_a_picker, cerrar_pickers_residuales,
        )

        # DOCUMENTOS
        print("\n   📄 DOCUMENTOS (PDF combinado)...")
        r1 = subir_archivo_a_picker(
            page, titulo_listitem="DOCUMENTOS",
            ruta_archivo=str(pdf_combinado.resolve()), es_zip=False,
        )
        print(f"      {'✅' if r1['ok'] else '❌'} {r1.get('verif') or r1.get('error')}")

        # Cerrar picker residual
        cerrar_pickers_residuales(page, verbose=True)
        time.sleep(2)

        # SHAPE
        print("\n   📦 Archivo Shape (Derrotero.zip)...")
        r2 = subir_archivo_a_picker(
            page, titulo_listitem="archivo shape",
            ruta_archivo=str(derrotero.resolve()), es_zip=True,
        )
        print(f"      {'✅' if r2['ok'] else '❌'} {r2.get('verif') or r2.get('error')}")

        # Final
        print()
        print("=" * 70)
        print("  ⏸️  PAUSA — REVISIÓN HUMANA")
        print("=" * 70)
        print()
        print("  Andá al Chrome del bot y verificá:")
        print("    1. Correo: el del topógrafo (auto-capturado)")
        print(f"    2. Tomo: {DATOS['tomo']}")
        print(f"    3. Asiento: {DATOS['asiento']}")
        print(f"    4. Proyecto-APT: {DATOS['proyecto_apt']}")
        print(f"    5. Fecha Minuta: {DATOS['fecha_minuta_anio']}-{DATOS['fecha_minuta_mes']}-{DATOS['fecha_minuta_dia']}")
        print(f"    6. Área: {DATOS['area']}")
        print(f"    7. Finca: {DATOS['finca']}")
        print(f"    8. Distrito: {DATOS['distrito']}")
        print(f"    9. Proceso: {DATOS['proceso']}")
        print(f"    10. Tipo Acceso: {DATOS['tipo_acceso']}")
        print(f"    11. Vértices: {DATOS['vertices']}")
        print(f"    12. Profesional: {DATOS['profesional']}")
        print(f"    13. Carné: {DATOS['carne']}")
        print(f"    14. DOCUMENTOS: ROESQUINA_DOCUMENTOS.pdf adjunto ✅")
        print(f"    15. Archivo Shape: Derrotero.zip adjunto ✅")
        print()
        print("  Si todo OK → CLICK MANUAL en 'Enviar' (Google bloquea click programático)")
        print()
        return 0


if __name__ == "__main__":
    sys.exit(main())
