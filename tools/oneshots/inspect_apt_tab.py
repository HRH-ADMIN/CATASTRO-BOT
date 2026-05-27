"""Inspecciona la pestaña APT actualmente abierta en Chrome via CDP.

Imprime URL, título, posibles números de trámite, y mensajes de error en pantalla.
Útil para diagnosticar después de un Guardar.
"""
from __future__ import annotations
import io
import os
import sys
import re
from pathlib import Path

os.environ.setdefault("CATASTRO_BOT_DEV_MODE", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        try:
            apt_page = None
            for c in browser.contexts:
                for pg in c.pages:
                    try:
                        if "apt.cfia.or.cr" in pg.url:
                            apt_page = pg
                            break
                    except Exception:
                        continue
                if apt_page:
                    break

            if not apt_page:
                print("[ERROR] No hay pestaña APT en Chrome")
                return 1

            print(f"URL: {apt_page.url}")
            print(f"TITLE: {apt_page.title()}")
            print()

            # Texto visible
            try:
                txt = apt_page.inner_text("body")
            except Exception as e:
                print(f"[WARN] no se pudo leer body: {e}")
                txt = ""

            # Buscar mensajes de error/validación típicos
            print("─── Posibles mensajes de error/validación ───")
            for line in txt.splitlines():
                line = line.strip()
                if not line:
                    continue
                low = line.lower()
                if any(k in low for k in ["error", "obligatorio", "requerido", "inválid", "invalid", "complet", "falta", "debe"]):
                    print(f"  • {line[:200]}")
            print()

            # Buscar número de trámite
            print("─── Búsqueda de número de trámite ───")
            for patron, etiqueta in [
                (r"[Nn]um[Cc]ontrato=(\d+)", "URL ?numContrato="),
                (r"contrato/(\d+)", "URL /contrato/N"),
                (r"tramite=(\d+)", "URL tramite="),
                (r"id=(\d+)", "URL ?id="),
            ]:
                m = re.search(patron, apt_page.url)
                if m:
                    print(f"  ✅ {etiqueta}: {m.group(1)}")
            for patron, etiqueta in [
                (r"[Tt]r[áa]mite[:\s#]*(\d{4,})", "texto 'Trámite NNN'"),
                (r"[Cc]ontrato[:\s#]*(\d{4,})", "texto 'Contrato NNN'"),
            ]:
                m = re.search(patron, txt)
                if m:
                    print(f"  ✅ {etiqueta}: {m.group(1)}")
            print()

            # Selectores comunes
            print("─── Selectores y valores ───")
            for sel in [
                "#txtNumContrato", "#numContrato", "#ddlTipoPlano", "#TipoActo",
                "#txtNombreProfesional", "#txtCedulaProfesional",
                "input[id*='Contrato']", "input[id*='Tramite']",
            ]:
                try:
                    loc = apt_page.locator(sel)
                    n = loc.count()
                    if n > 0:
                        try:
                            val = loc.first.input_value()
                        except Exception:
                            try:
                                val = loc.first.inner_text()
                            except Exception:
                                val = "<no se pudo leer>"
                        print(f"  {sel}  [n={n}]  = {val[:100]!r}")
                except Exception:
                    pass

        finally:
            browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
