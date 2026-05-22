"""ROESQUINA — solo subir los 2 archivos al form muni ya abierto.

Reutiliza la pestaña abierta del form (no recarga, no toca pre-llenado).
"""
from __future__ import annotations
import io
import os
import sys
import time
from pathlib import Path

os.chdir(r"C:\catastro-bot")
os.environ.setdefault("CATASTRO_BOT_DEV_MODE", "1")
sys.path.insert(0, os.getcwd())
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")

PDF_DOC = Path(r"A:\mega\planos apt\CANTONES\san isidro\rolando\2026\ROESQUINA\2026-42856.pdf")
ZIP_SHAPE = Path(r"A:\mega\planos apt\CANTONES\san isidro\rolando\2026\ROESQUINA\SHAPE\Derrotero.zip")


def main() -> int:
    print("=" * 70)
    print("  ROESQUINA — subida de archivos al form muni")
    print("=" * 70)
    print()

    # Verificar archivos
    for p in (PDF_DOC, ZIP_SHAPE):
        if not p.exists():
            print(f"❌ NO existe: {p}")
            return 1
        kb = p.stat().st_size / 1024
        print(f"   ✅ {p.name} ({kb:.1f} KB)")
    print()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("❌ Playwright no instalado")
        return 2

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp("http://localhost:9222")
        except Exception as exc:
            print(f"❌ Chrome del bot no responde: {exc}")
            return 3

        # Encontrar la pestaña del form
        page = None
        for ctx in browser.contexts:
            for pg in ctx.pages:
                try:
                    if "docs.google.com/forms" in pg.url and "viewform" in pg.url:
                        page = pg
                        break
                except Exception:
                    continue
            if page:
                break
        if not page:
            print("❌ No hay pestaña del form abierta")
            return 4

        page.bring_to_front()
        print(f"📋 Pestaña: {page.title()}")
        print()

        from src.utils.muni_uploader import (
            subir_archivo_a_picker, cerrar_pickers_residuales,
        )

        # ─── DOCUMENTOS ───────────────────────────────────────────────
        print(f"📄 [1/2] DOCUMENTOS → {PDF_DOC.name}")
        r1 = subir_archivo_a_picker(
            page, titulo_listitem="DOCUMENTOS",
            ruta_archivo=str(PDF_DOC.resolve()), es_zip=False,
        )
        print(f"      {'✅' if r1.get('ok') else '❌'} "
              f"{r1.get('verif') or r1.get('error') or '(sin mensaje)'}")
        time.sleep(2)

        # Cerrar picker residual antes del siguiente
        c = cerrar_pickers_residuales(page, verbose=True)
        if not c["ok"]:
            print(f"      ⚠️  {c['pickers_finales']} picker(s) persisten")
        time.sleep(2)

        # ─── ARCHIVO SHAPE ────────────────────────────────────────────
        print(f"\n📦 [2/2] Archivo Shape → {ZIP_SHAPE.name}")
        r2 = subir_archivo_a_picker(
            page, titulo_listitem="archivo shape",
            ruta_archivo=str(ZIP_SHAPE.resolve()), es_zip=True,
        )
        print(f"      {'✅' if r2.get('ok') else '❌'} "
              f"{r2.get('verif') or r2.get('error') or '(sin mensaje)'}")

        print()
        print("=" * 70)
        print("  ⏸️  PAUSA — NO se envió, revisión manual del operador")
        print("=" * 70)
        print()
        if r1.get('ok') and r2.get('ok'):
            print("  ✅ Ambos archivos subidos. Revisá y dale click Enviar manual.")
        else:
            print("  ⚠️  Hubo fallos. Subí los archivos faltantes a mano:")
            if not r1.get('ok'):
                print(f"     - DOCUMENTOS: {PDF_DOC}")
            if not r2.get('ok'):
                print(f"     - Archivo Shape: {ZIP_SHAPE}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
