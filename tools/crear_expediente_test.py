"""Crea un expediente nuevo en BD + estructura de carpetas.

Estructura de carpetas nueva (organizada por ubicación):
  data/files/<PROVINCIA>/<CANTON>/<DISTRITO>/<NOMBRE_PROYECTO>/
    ├── 01_Campo/
    ├── 02_Oficina/
    └── 03_Catastrado/

USO:
  python tools/crear_expediente_test.py NUMERO_EXP TIPO_PLANO \\
      --provincia ALAJUELA --canton SAN_RAMON --distrito SAN_ISIDRO \\
      --nombre ROGRANJ

  Si no se pasan los flags, los pregunta interactivamente.
"""
from __future__ import annotations
import argparse
import io
import json
import os
import re
import sqlite3
import sys
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path

os.environ.setdefault("CATASTRO_BOT_DEV_MODE", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


TIPOS_VALIDOS = {
    "segregacion", "rectificacion", "informacion_posesoria",
    "reunion_de_fincas", "fincas_completas",
}


def _normalizar(texto: str) -> str:
    """ALAJUELA / san_ramón → ALAJUELA / SAN_RAMON."""
    if not texto:
        return ""
    t = unicodedata.normalize("NFD", texto)
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = t.upper().strip()
    t = re.sub(r"\s+", "_", t)
    t = re.sub(r"[^A-Z0-9_\-]", "", t)
    return t


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    print(f">>> {prompt}{suffix}: ", end="", flush=True)
    try:
        ans = input().strip()
    except EOFError:
        ans = ""
    return ans or default


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("numero", help="Número de expediente (ej. RDF-2026-001)")
    parser.add_argument("tipo", help=f"Tipo plano: {sorted(TIPOS_VALIDOS)}")
    parser.add_argument("--provincia", default="")
    parser.add_argument("--canton",    default="")
    parser.add_argument("--distrito",  default="")
    parser.add_argument("--nombre",    default="")
    args = parser.parse_args()

    numero = args.numero.upper().strip()
    tipo   = args.tipo.lower().strip()
    if tipo not in TIPOS_VALIDOS:
        print(f"[ERROR] tipo inválido: {tipo!r}. Use uno de {sorted(TIPOS_VALIDOS)}")
        return 1

    # Pedir interactivamente lo que falte
    print(f"\n═══ Crear expediente {numero} ({tipo}) ═══\n")
    provincia = args.provincia or _ask("Provincia (ej. ALAJUELA)")
    canton    = args.canton    or _ask("Cantón (ej. SAN_RAMON)")
    distrito  = args.distrito  or _ask("Distrito (ej. SAN_ISIDRO)")
    nombre    = args.nombre    or _ask(
        "Nombre del proyecto (nombre COMPLETO del topógrafo, no la abreviatura "
        "del cajetín — ej. ROLANDO_GRANJA, no ROGRANJ)"
    )
    if not all([provincia, canton, distrito, nombre]):
        print("[ERROR] Provincia, cantón, distrito y nombre son obligatorios.")
        return 1

    # Normalizar a uppercase ASCII
    provincia_n = _normalizar(provincia)
    canton_n    = _normalizar(canton)
    distrito_n  = _normalizar(distrito)
    nombre_n    = _normalizar(nombre)

    # Construir path
    base = Path("data/files") / provincia_n / canton_n / distrito_n / nombre_n
    for sub in ["01_Campo", "02_Oficina", "03_Catastrado"]:
        (base / sub).mkdir(parents=True, exist_ok=True)
    print(f"\n✅ Carpetas creadas en:\n   {base.absolute()}")
    print(f"   ├── 01_Campo")
    print(f"   ├── 02_Oficina")
    print(f"   └── 03_Catastrado")

    # Crear/actualizar expediente en BD
    conn = sqlite3.connect("data/catastro.db")
    conn.row_factory = sqlite3.Row
    existing = conn.execute(
        "SELECT id, metadata_json FROM expedientes WHERE numero_expediente = ?",
        (numero,),
    ).fetchone()
    now = datetime.now().isoformat(timespec="seconds")

    metadata_extra = {
        "provincia":       provincia_n,
        "canton":          canton_n,
        "distrito":        distrito_n,
        "nombre_proyecto": nombre_n,
        "path_carpeta":    str(base.absolute()).replace("\\", "/"),
    }

    if existing:
        meta = json.loads(existing["metadata_json"] or "{}")
        meta.update(metadata_extra)
        conn.execute(
            "UPDATE expedientes SET metadata_json = ?, fecha_actualizacion = ? WHERE id = ?",
            (json.dumps(meta, ensure_ascii=False), now, existing["id"]),
        )
        print(f"\n⚠️  Expediente {numero} ya existía — metadata actualizada con ubicación.")
    else:
        eid = str(uuid.uuid4())
        meta = dict(metadata_extra)
        conn.execute(
            """INSERT INTO expedientes (
                id, numero_expediente, tipo_plano, nombre_topografo, cedula_topografo,
                telefono_cliente, nombre_cliente, estado_actual, municipalidad,
                fecha_creacion, fecha_actualizacion, metadata_json,
                completado, cancelado
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)""",
            (
                eid, numero, tipo, "ROJAS HERRERA LUIS ALONSO", "0205300432",
                "+50688887310", None, "recibido",
                canton_n.replace("_", " "), now, now,
                json.dumps(meta, ensure_ascii=False),
            ),
        )
        print(f"\n✅ Expediente {numero} creado en BD.")
    conn.commit()
    conn.close()

    print()
    print("═" * 60)
    print("  PRÓXIMOS PASOS")
    print("═" * 60)
    print(f"  1. Suba archivos a:")
    print(f"     {base.absolute()}\\01_Campo\\")
    print(f"     • PLANO.pdf, INFORMACION DE REGISTRO.png, entero.pdf")
    print(f"  2. Llene datos_apt y corra:")
    print(f"     run_apt_crear_pasos.py {numero}")
    print(f"     run_apt_plano_pasos.py {numero}")
    print("═" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
