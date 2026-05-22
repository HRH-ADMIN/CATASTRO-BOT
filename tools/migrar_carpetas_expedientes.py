"""Migra carpetas de expedientes del formato viejo al nuevo.

VIEJO:  data/files/<NUMERO_EXP>/01_Campo/...
NUEVO:  data/files/<PROVINCIA>/<CANTON>/<DISTRITO>/<NOMBRE>/01_Campo/...

Lee la BD para encontrar provincia/canton/distrito/nombre_proyecto de cada
expediente. Si faltan datos, los pregunta interactivamente.
Actualiza paths absolutos en metadata_json (path_carpeta y plano.archivos.*).
"""
from __future__ import annotations
import io
import json
import os
import re
import shutil
import sqlite3
import sys
import unicodedata
from pathlib import Path

os.environ.setdefault("CATASTRO_BOT_DEV_MODE", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def _normalizar(t: str) -> str:
    if not t:
        return ""
    t = unicodedata.normalize("NFD", t)
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = t.upper().strip()
    t = re.sub(r"\s+", "_", t)
    return re.sub(r"[^A-Z0-9_\-]", "", t)


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    print(f">>> {prompt}{suffix}: ", end="", flush=True)
    try:
        ans = input().strip()
    except EOFError:
        ans = ""
    return ans or default


def main() -> int:
    conn = sqlite3.connect("data/catastro.db")
    conn.row_factory = sqlite3.Row
    expedientes = conn.execute(
        "SELECT id, numero_expediente, tipo_plano, metadata_json FROM expedientes "
        "ORDER BY numero_expediente"
    ).fetchall()

    base_old = Path("data/files")
    for exp in expedientes:
        numero = exp["numero_expediente"]
        viejo = base_old / numero
        if not viejo.exists():
            continue

        meta = json.loads(exp["metadata_json"] or "{}")
        # Si ya tiene path_carpeta (ya migrado), saltar
        if meta.get("path_carpeta") and Path(meta["path_carpeta"]).exists():
            print(f"  [SKIP] {numero} ya migrado → {meta['path_carpeta']}")
            continue

        print(f"\n═══ Migrando {numero} ═══")
        print(f"  Carpeta vieja: {viejo}")

        # Tomar de metadata o preguntar
        provincia = meta.get("provincia") or _ask(f"  Provincia para {numero}")
        canton    = meta.get("canton")    or _ask(f"  Cantón para {numero}")
        distrito  = meta.get("distrito")  or _ask(f"  Distrito para {numero}")
        # nombre del proyecto (del cajetín del plano)
        nombre_def = (meta.get("datos_apt") or {}).get("plano", {}).get("descripcion", "")
        # remover sufijo (1) del nombre si existe
        if nombre_def:
            nombre_def = re.sub(r"\(\d+\)\s*$", "", nombre_def).strip()
        nombre = meta.get("nombre_proyecto") or _ask(f"  Nombre del proyecto", nombre_def)

        if not all([provincia, canton, distrito, nombre]):
            print(f"  [SKIP] {numero} — faltan datos de ubicación, dejar manual")
            continue

        provincia_n = _normalizar(provincia)
        canton_n    = _normalizar(canton)
        distrito_n  = _normalizar(distrito)
        nombre_n    = _normalizar(nombre)
        nuevo = base_old / provincia_n / canton_n / distrito_n / nombre_n

        if nuevo.exists():
            print(f"  [WARN] destino ya existe: {nuevo} — saltando para no sobreescribir")
            continue

        nuevo.parent.mkdir(parents=True, exist_ok=True)
        print(f"  Movemos a: {nuevo}")
        shutil.move(str(viejo), str(nuevo))

        # Actualizar metadata
        meta["provincia"]       = provincia_n
        meta["canton"]          = canton_n
        meta["distrito"]        = distrito_n
        meta["nombre_proyecto"] = nombre_n
        meta["path_carpeta"]    = str(nuevo.absolute()).replace("\\", "/")

        # Actualizar paths en datos_apt.plano.archivos si existen
        plano = meta.get("datos_apt", {}).get("plano", {})
        archivos = plano.get("archivos") or {}
        archivos_nuevo = {}
        for k, v_path in archivos.items():
            if not v_path:
                archivos_nuevo[k] = v_path
                continue
            old_p = Path(v_path)
            # Reemplazar `data/files/<numero>/...` por la nueva ruta
            try:
                rel = old_p.relative_to(Path("data/files") / numero)
                new_path = nuevo / rel
            except (ValueError, AttributeError):
                # si el path no era relativo a la carpeta vieja, dejar igual
                new_path = old_p
            archivos_nuevo[k] = str(new_path).replace("\\", "/")
        if archivos:
            plano["archivos"] = archivos_nuevo
            meta.setdefault("datos_apt", {})["plano"] = plano

        conn.execute(
            "UPDATE expedientes SET metadata_json = ? WHERE id = ?",
            (json.dumps(meta, ensure_ascii=False), exp["id"]),
        )
        print(f"  ✅ Migrado y metadata actualizada")

    conn.commit()
    conn.close()
    print("\n✅ Migración completa")
    return 0


if __name__ == "__main__":
    sys.exit(main())
