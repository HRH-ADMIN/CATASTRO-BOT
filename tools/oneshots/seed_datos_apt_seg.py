"""Pre-llena datos_apt para SEG-2026-001 con los datos resueltos durante la
sesión interactiva. Útil para probar APT CREAR sin pasar por el wizard CLI.

USO:
  python tools/seed_datos_apt_seg.py
"""
from __future__ import annotations
import io
import json
import os
import sqlite3
import sys
from pathlib import Path

os.environ.setdefault("CATASTRO_BOT_DEV_MODE", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


DATOS_APT_SEG_2026_001 = {
    "propietario": {
        "tipo_cedula": "1",                       # FÍSICA
        "cedula":      "2-0281-0882",
        "correo":      "topografiahrh@gmail.com",
        # nombre/apellidos: APT autocompleta del RNP al meter la cédula
    },
    "contratante_es_propietario": True,

    "profesional": {
        "correo": "topografiahrh@gmail.com",      # sobreescribe el de Firma Digital
    },

    "protocolo": {
        "numero":              "24162",            # del cajetín plano.pdf
        "folio":               "098",              # del cajetín
        "tipo_proyecto_modal": "27",               # Plano Simple
    },

    "proyecto": {
        "tipo_plano_apt": "27",                    # Plano Simple (regla)
        "provincia":      "2",                     # ALAJUELA
        "canton":         "02",                    # SAN RAMÓN
        "distrito":       "05",                    # PIEDADES SUR
        # descripcion: bot auto-genera "X plano(s) a catastrar" desde max_planos
        "naturaleza":     "2",                     # Equidad (regla)
        # firma_* → bot auto-copia de la ubicación
    },

    "general": {
        "area_predio":            "20966.88",
        "area_real":              "20966.88",
        "moneda":                 "1",            # COLONES (regla)
        "honorarios":             "295352",       # 290,352 + 5,000 ajuste
        "honorarios_letras":      "",             # opcional
        "exoneracion_honorarios": False,
        "adelanto":               "0",            # regla
        "pagos_parciales":        "0",            # regla
        "plazo_entrega":          "al finalizar el contrato",  # regla — texto corto fijo
        "max_planos":             "1",
        "observaciones":          "",             # vacío (1 plano, no vencido)
        "composicion":            "unipersonal",  # regla
        # entero: NO se llena en este paso — corresponde al llenado del plano
    },

    # firmas: bot pone fecha de hoy automáticamente
}


def main() -> int:
    db_path = "data/catastro.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    row = conn.execute(
        "SELECT id, metadata_json FROM expedientes WHERE numero_expediente = ?",
        ("SEG-2026-001",),
    ).fetchone()
    if not row:
        print("[ERROR] Expediente SEG-2026-001 no existe en BD.")
        return 1

    meta = json.loads(row["metadata_json"]) if row["metadata_json"] else {}
    meta["datos_apt"] = DATOS_APT_SEG_2026_001
    conn.execute(
        "UPDATE expedientes SET metadata_json = ? WHERE id = ?",
        (json.dumps(meta, ensure_ascii=False), row["id"]),
    )
    conn.commit()
    conn.close()

    print("✅ datos_apt guardado en metadata para SEG-2026-001")
    print()
    print(json.dumps(DATOS_APT_SEG_2026_001, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
