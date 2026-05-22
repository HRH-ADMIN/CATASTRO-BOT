"""Pre-llena datos_apt para RDF-2026-001 con datos extraídos de los archivos."""
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


DATOS_APT_RDF_2026_001 = {
    "propietario": {
        "tipo_cedula": "2",  # JURÍDICA
        "cedula":      "3-101-372740",
        "nombre":      "GRANOS Y FORRAJES DEL PONIENTE SOCIEDAD ANONIMA",
        "correo":      "topografiahrh@gmail.com",
    },
    "contratante_es_propietario": True,
    "profesional": {
        "correo": "topografiahrh@gmail.com",
    },
    "protocolo": {
        "numero":              "24162",
        "folio":               "102",
        "tipo_proyecto_modal": "27",
    },
    "proyecto": {
        "tipo_plano_apt": "27",
        "provincia":      "2",       # ALAJUELA
        "canton":         "02",      # SAN RAMÓN
        "distrito":       "07",      # SAN ISIDRO
        "naturaleza":     "2",       # Equidad
    },
    "general": {
        "area_predio":            "131061",     # suma registros: 83,350 + 47,711
        "area_real":              "90349.87",   # del cajetín
        "moneda":                 "1",
        "honorarios":             "607728",     # 602,728 + 5,000 ajuste
        "exoneracion_honorarios": False,
        "adelanto":               "0",
        "pagos_parciales":        "0",
        "plazo_entrega":          "al finalizar el contrato",
        "max_planos":             "1",
        "observaciones":          "",
        "composicion":            "unipersonal",
    },
}


def main() -> int:
    conn = sqlite3.connect("data/catastro.db")
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT id, metadata_json FROM expedientes WHERE numero_expediente = ?",
        ("RDF-2026-001",),
    ).fetchone()
    if not row:
        print("[ERROR] Expediente RDF-2026-001 no existe en BD.")
        return 1
    meta = json.loads(row["metadata_json"]) if row["metadata_json"] else {}
    meta["datos_apt"] = DATOS_APT_RDF_2026_001
    conn.execute(
        "UPDATE expedientes SET metadata_json = ? WHERE id = ?",
        (json.dumps(meta, ensure_ascii=False), row["id"]),
    )
    conn.commit()
    conn.close()
    print("✅ datos_apt guardado para RDF-2026-001")
    print()
    print(json.dumps(DATOS_APT_RDF_2026_001, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
