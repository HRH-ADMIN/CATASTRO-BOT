"""catastro-bot apt-revisar <EXP-ID>

Crea una revisión pre-envío manual para un expediente. NO toca el portal
CFIA — solo captura el seed desde metadata + un snapshot vacío (operador
puede después agregar screenshot tomado a mano si quiere, o el flujo
real `apt-flujo` lo hará automáticamente cuando se integre).

Útil para:
  - Probar el panel /expediente/<id>/revisar-envio con datos reales.
  - Forzar una pausa de aprobación humana antes de un apt-enviar futuro.

USO:
  catastro-bot apt-revisar SEG-2026-005
  catastro-bot apt-revisar SEG-2026-005 --pdf C:/path/to/anverso.pdf
  catastro-bot apt-revisar SEG-2026-005 --abrir-browser

Plan: PLAN_MEJORAS Sprint 5 / N-02 sub-paso D.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("CATASTRO_BOT_DEV_MODE", "1")


def _resolver_pdf_anverso(expediente: dict, root: Path) -> str | None:
    """Intenta localizar el PDF anverso bajo data/files/PROV/CANT/DIST/PROY/01_Campo/.
    Devuelve path relativo al root o None si no se encuentra."""
    meta = json.loads(expediente.get("metadata_json") or "{}")
    prov = meta.get("provincia", "")
    cant = meta.get("canton", "")
    dist = meta.get("distrito", "")
    proy = meta.get("nombre_proyecto", "")
    if not all([prov, cant, dist, proy]):
        return None
    base = root / "data" / "files" / prov / cant / dist / proy / "01_Campo"
    if not base.is_dir():
        return None
    # Buscar archivo que matchee amberso/anverso/plano
    for candidate_name in ("amberso.pdf", "anverso.pdf"):
        p = base / candidate_name
        if p.exists():
            try:
                return str(p.resolve().relative_to(root)).replace("\\", "/")
            except ValueError:
                return str(p)
    # Fallback: primer .pdf que encuentre
    for p in base.glob("*.pdf"):
        try:
            return str(p.resolve().relative_to(root)).replace("\\", "/")
        except ValueError:
            return str(p)
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="catastro-bot apt-revisar",
        description="Crear una revisión pre-envío manual.",
    )
    parser.add_argument("expediente", help="numero_expediente (ej. SEG-2026-005)")
    parser.add_argument("--pdf", help="path al PDF anverso (override)")
    parser.add_argument("--abrir-browser", action="store_true",
                        help="abrir el panel de revisión en el navegador")
    args = parser.parse_args(argv)

    from config.settings import DATABASE_PATH
    from src.core.credential_manager import CredentialManager
    from src.core.database import Database
    from src.utils import pre_envio_snapshot as pes

    creds = CredentialManager()
    db = Database(credentials=creds)
    db.initialize_schema()

    # Buscar el expediente por numero
    import sqlite3
    with sqlite3.connect(DATABASE_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM expedientes WHERE numero_expediente = ?",
            (args.expediente,),
        ).fetchone()
    if row is None:
        print(f"[ERROR] Expediente {args.expediente!r} no encontrado.")
        return 1
    expediente = dict(row)

    # Resolver PDF
    pdf_path = args.pdf or _resolver_pdf_anverso(expediente, ROOT)
    if pdf_path:
        print(f"  PDF anverso: {pdf_path}")
    else:
        print("  PDF anverso: (no encontrado — la pantalla mostrará 'sin PDF')")

    # Seed desde metadata
    meta = json.loads(expediente.get("metadata_json") or "{}")
    datos_apt = meta.get("datos_apt", {})
    # Usamos el bloque del primer plano si existe; si no, dict vacío
    seed = {}
    if datos_apt.get("planos"):
        seed = datos_apt["planos"][0]
    elif datos_apt.get("contrato"):
        seed = datos_apt["contrato"]

    if not seed:
        print("  [WARN] El expediente no tiene datos_apt en metadata. "
              "La pantalla solo mostrará la estructura vacía.")

    # snapshot_dom vacío — el operador puede agregar screenshot manualmente
    # editando el archivo PNG en data/revisiones/<rev_id>.png si quiere.
    rev_id = pes.crear_revision(
        DATABASE_PATH, ROOT,
        expediente_id=expediente["id"],
        seed_plano=seed,
        snapshot_dom={},  # sin captura — operador revisa solo el seed
        pdf_anverso_path=pdf_path,
    )

    print()
    print("=" * 70)
    print(f"  Revisión creada: {rev_id}")
    print(f"  Estado: pendiente")
    print(f"  Discrepancias: 0 (snapshot_dom vacío; no se compara con portal)")
    print(f"  URL: http://localhost:9224/expediente/{expediente['id']}/revisar-envio")
    print("=" * 70)

    if args.abrir_browser:
        url = f"http://localhost:9224/expediente/{expediente['id']}/revisar-envio"
        try:
            webbrowser.open(url)
        except Exception as exc:
            print(f"  [WARN] No se pudo abrir el browser: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
