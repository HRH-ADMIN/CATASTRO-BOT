"""Monitorea bP4 y captura los requests AJAX hacia el servidor APT
mientras el operador edita un titular a mano.

Uso:
  1. Correr este script (queda escuchando)
  2. En el navegador: click en el titular AMALIA, modificar nombre,
     click Guardar
  3. Ctrl+C para terminar — el script imprime los payloads capturados
"""
from __future__ import annotations
import io, json, os, sys, time
from pathlib import Path
os.environ.setdefault("CATASTRO_BOT_DEV_MODE", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
from playwright.sync_api import sync_playwright


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        page = next(pg for pg in browser.contexts[0].pages if "apt.cfia.or.cr" in pg.url)
        captured = []

        def on_req(req):
            url = req.url
            if "GuardarTitular" in url or "Titular" in url:
                try:
                    data = req.post_data
                except Exception:
                    data = None
                captured.append({
                    "ts": time.strftime("%H:%M:%S"),
                    "method": req.method,
                    "url": url,
                    "post_data": data,
                })
                print(f"\n[REQUEST {req.method}] {url}")
                if data:
                    # Payload JSON suele venir como datos=...
                    print(f"  payload: {data[:500]}")

        def on_resp(resp):
            url = resp.url
            if "GuardarTitular" in url or "ConsultarTitular" in url:
                try:
                    body = resp.text()
                except Exception:
                    body = "<no body>"
                print(f"\n[RESPONSE {resp.status}] {url}")
                print(f"  body: {body[:500]}")

        page.on("request", on_req)
        page.on("response", on_resp)

        print("═" * 60)
        print("MONITOREANDO bP4. Edita el titular en el navegador.")
        print("Ctrl+C para terminar y ver el resumen.")
        print("═" * 60)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n\n══ RESUMEN ══")
            print(json.dumps(captured, ensure_ascii=False, indent=2))
            # Estado final del titular guardado
            saved = page.evaluate(
                r"""() => [...document.querySelectorAll('[onclick^="SeleccionarTitular"]')].map(el =>
                    el.getAttribute('onclick')
                )"""
            )
            print("\nTitulares guardados ahora:")
            for s in saved:
                print(f"  {s}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
