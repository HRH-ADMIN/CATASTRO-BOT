"""Lista todos los planos/expedientes registrados en BD con su ubicación,
estado, trámite APT y archivos en carpeta. Soporta filtros.

USO:
  python tools/listar_planos.py                       # lista todos
  python tools/listar_planos.py --provincia ALAJUELA  # filtrar por ubicación
  python tools/listar_planos.py --canton SAN_RAMON
  python tools/listar_planos.py --estado enviado_cfia # filtrar por estado APT
  python tools/listar_planos.py --buscar ROLANDO      # buscar texto en nombre/cliente
  python tools/listar_planos.py --json                # salida JSON para scripts
  python tools/listar_planos.py SEG-2026-001          # detalle de un solo expediente
"""
from __future__ import annotations
import argparse
import io
import json
import os
import sqlite3
import sys
from pathlib import Path

os.environ.setdefault("CATASTRO_BOT_DEV_MODE", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def _archivos_carpeta(path: str | None) -> list[str]:
    """Lista archivos en cada subfase de la carpeta del expediente."""
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for sub in ("01_Campo", "02_Oficina", "03_Catastrado"):
        d = p / sub
        if d.exists():
            for f in sorted(d.iterdir()):
                if f.is_file():
                    size_kb = f.stat().st_size / 1024
                    out.append(f"{sub}/{f.name} ({size_kb:.1f} KB)")
    return out


def _resumen_expediente(row: sqlite3.Row) -> dict:
    meta = json.loads(row["metadata_json"] or "{}")
    plano_da = (meta.get("datos_apt") or {}).get("plano") or {}
    return {
        "numero":          row["numero_expediente"],
        "tipo":            row["tipo_plano"],
        "estado":          row["estado_actual"],
        "topografo":       row["nombre_topografo"],
        "cliente":         row["nombre_cliente"],
        "provincia":       meta.get("provincia"),
        "canton":          meta.get("canton"),
        "distrito":        meta.get("distrito"),
        "nombre_proyecto": meta.get("nombre_proyecto"),
        "cajetin":         plano_da.get("descripcion") or "",  # ROGRANJ(1), DANBUR(1)...
        "path_carpeta":    meta.get("path_carpeta"),
        "apt_tramite":     meta.get("apt_tramite"),
        "apt_estado":      meta.get("apt_estado"),
        "apt_envio_fecha": meta.get("apt_envio_fecha"),
        "archivos":        _archivos_carpeta(meta.get("path_carpeta")),
    }


def _imprimir_tabla(items: list[dict]) -> None:
    print(f"\n  Total: {len(items)} expediente(s)\n")
    print(f"  {'#':<3} {'Número':<16} {'Tipo':<20} {'Cajetín':<14} {'Proyecto':<18} {'Ubicación':<33} {'APT trámite + estado'}")
    print(f"  {'─'*3} {'─'*16} {'─'*20} {'─'*14} {'─'*18} {'─'*33} {'─'*30}")
    for i, e in enumerate(items, 1):
        ubic = "/".join(filter(None, [e["provincia"], e["canton"], e["distrito"]])) or "-"
        nombre  = e["nombre_proyecto"] or "-"
        cajetin = e["cajetin"] or "-"
        if e["apt_tramite"]:
            tramite = f"{e['apt_tramite']}"
            if e["apt_estado"]:
                tramite += f" ({e['apt_estado']})"
        else:
            tramite = "-"
        print(f"  {i:<3} {e['numero']:<16} {e['tipo']:<20} {cajetin[:14]:<14} {nombre[:18]:<18} {ubic[:33]:<33} {tramite}")


def _imprimir_detalle(e: dict) -> None:
    print()
    print("═" * 70)
    print(f"  EXPEDIENTE: {e['numero']}")
    print("═" * 70)
    print(f"  Tipo:               {e['tipo']}")
    print(f"  Estado workflow:    {e['estado']}")
    print(f"  Topógrafo:          {e['topografo']}")
    print(f"  Cliente:            {e['cliente'] or '-'}")
    print()
    print(f"  📍 Ubicación:")
    print(f"     Provincia:       {e['provincia'] or '-'}")
    print(f"     Cantón:          {e['canton'] or '-'}")
    print(f"     Distrito:        {e['distrito'] or '-'}")
    print(f"     Nombre proyecto: {e['nombre_proyecto'] or '-'}")
    print(f"     Cajetín plano:   {e['cajetin'] or '-'}")
    print()
    print(f"  📂 Carpeta:")
    print(f"     {e['path_carpeta'] or 'no definida'}")
    print()
    print(f"  🏛️  APT:")
    print(f"     Trámite:         {e['apt_tramite'] or 'no creado aún'}")
    print(f"     Estado APT:      {e['apt_estado'] or '-'}")
    print(f"     Fecha envío:     {e['apt_envio_fecha'] or '-'}")
    print()
    if e["archivos"]:
        print(f"  📄 Archivos en carpeta ({len(e['archivos'])}):")
        for a in e["archivos"]:
            print(f"     • {a}")
    else:
        print(f"  📄 Sin archivos en carpeta")
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("numero", nargs="?", help="Número de expediente para ver detalle")
    parser.add_argument("--provincia")
    parser.add_argument("--canton")
    parser.add_argument("--distrito")
    parser.add_argument("--estado", help="Filtrar por estado workflow o apt_estado")
    parser.add_argument("--tipo",   help="Filtrar por tipo de plano")
    parser.add_argument("--buscar", help="Buscar texto en nombre_proyecto, cliente, topógrafo")
    parser.add_argument("--json", action="store_true", help="Salida JSON")
    args = parser.parse_args()

    conn = sqlite3.connect("data/catastro.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM expedientes WHERE cancelado = 0 ORDER BY numero_expediente"
    ).fetchall()
    conn.close()

    items = [_resumen_expediente(r) for r in rows]

    # Filtros
    if args.provincia:
        items = [e for e in items if (e["provincia"] or "").upper() == args.provincia.upper()]
    if args.canton:
        items = [e for e in items if (e["canton"] or "").upper() == args.canton.upper()]
    if args.distrito:
        items = [e for e in items if (e["distrito"] or "").upper() == args.distrito.upper()]
    if args.tipo:
        items = [e for e in items if e["tipo"] == args.tipo.lower()]
    if args.estado:
        items = [e for e in items if e["estado"] == args.estado or e["apt_estado"] == args.estado]
    if args.buscar:
        q = args.buscar.upper()
        items = [
            e for e in items
            if q in (e["nombre_proyecto"] or "").upper()
            or q in (e["cliente"] or "").upper()
            or q in (e["topografo"] or "").upper()
            or q in (e["numero"] or "").upper()
            or q in (e["cajetin"] or "").upper()         # buscar también por abrev. del cajetín
            or q in (e["apt_tramite"] or "").upper()     # o por número de trámite
        ]

    if args.numero:
        # Detalle de uno
        match = [e for e in items if e["numero"].upper() == args.numero.upper()]
        if not match:
            print(f"[ERROR] No se encontró expediente {args.numero}")
            return 1
        if args.json:
            print(json.dumps(match[0], ensure_ascii=False, indent=2, default=str))
        else:
            _imprimir_detalle(match[0])
        return 0

    if args.json:
        print(json.dumps(items, ensure_ascii=False, indent=2, default=str))
    else:
        _imprimir_tabla(items)
    return 0


if __name__ == "__main__":
    sys.exit(main())
