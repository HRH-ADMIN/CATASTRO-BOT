"""CLI para consultar las reglas operativas aprendidas (apt_memoria_operador).

USO:
  catastro-bot reglas listar              → todas las activas
  catastro-bot reglas buscar <palabra>    → busca por keyword
  catastro-bot reglas categoria <cat>     → reglas de una categoría
  catastro-bot reglas stats               → estadísticas resumen
  catastro-bot reglas detalle <patron>    → descripción completa de una regla

Categorías: bp1, bp2, bp4, bp6, bp7, contrato, doble_chequeo, navegacion, meta
"""
from __future__ import annotations

import argparse
import io
import os
import sys
from pathlib import Path

os.chdir(r"C:\catastro-bot")
sys.path.insert(0, os.getcwd())
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")

from src.utils.reglas_consulta import (
    listar_reglas_activas, buscar_regla, consultar_por_categoria,
    categorias_disponibles, estadisticas,
)


def _print_regla(r: dict, *, full: bool = False) -> None:
    print(f"  - {r['patron']}")
    desc = r.get("descripcion", "")
    if full:
        for linea in desc.split("\n"):
            print(f"    {linea}")
    else:
        # primera línea o primeros 100 chars
        primera = desc.split("\n")[0] if desc else ""
        print(f"    {primera[:120]}{'...' if len(primera) > 120 else ''}")


def cmd_listar(args) -> int:
    rs = listar_reglas_activas(incluir_inactivas=args.todas)
    print(f"Reglas activas: {len(rs)}")
    for r in rs:
        marca = "✅" if r["activa"] else "❌"
        print(f"  {marca} {r['patron']}")
    return 0


def cmd_buscar(args) -> int:
    rs = buscar_regla(args.palabra)
    print(f"Coincidencias para '{args.palabra}': {len(rs)}")
    for r in rs:
        _print_regla(r, full=False)
    return 0


def cmd_categoria(args) -> int:
    rs = consultar_por_categoria(args.categoria)
    if not rs and args.categoria not in categorias_disponibles():
        print(f"Categoría inválida. Disponibles: {', '.join(categorias_disponibles())}")
        return 1
    print(f"Reglas en categoría '{args.categoria}': {len(rs)}")
    for r in rs:
        _print_regla(r, full=False)
    return 0


def cmd_detalle(args) -> int:
    rs = buscar_regla(args.patron)
    coinc = [r for r in rs if args.patron.lower() in r["patron"].lower()]
    if not coinc:
        print(f"No se encontró regla con patrón '{args.patron}'")
        return 1
    for r in coinc:
        print(f"━━━ {r['patron']} ━━━")
        for linea in r["descripcion"].split("\n"):
            print(f"  {linea}")
        print()
    return 0


def cmd_stats(args) -> int:
    s = estadisticas()
    print(f"Reglas totales: {s['total']}")
    print(f"Reglas activas: {s['activas']}")
    print()
    print("Por categoría:")
    for cat, n in s["por_categoria"].items():
        print(f"  {cat:15} : {n}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="catastro-bot reglas",
        description="Consultar reglas operativas aprendidas.",
    )
    sub = parser.add_subparsers(dest="sub")

    p_lis = sub.add_parser("listar", help="Listar reglas activas")
    p_lis.add_argument("--todas", action="store_true",
                       help="incluir inactivas también")
    p_lis.set_defaults(func=cmd_listar)

    p_bus = sub.add_parser("buscar", help="Buscar por palabra clave")
    p_bus.add_argument("palabra")
    p_bus.set_defaults(func=cmd_buscar)

    p_cat = sub.add_parser("categoria", help="Filtrar por categoría")
    p_cat.add_argument("categoria",
                       choices=categorias_disponibles())
    p_cat.set_defaults(func=cmd_categoria)

    p_det = sub.add_parser("detalle", help="Ver descripción completa de una regla")
    p_det.add_argument("patron")
    p_det.set_defaults(func=cmd_detalle)

    p_st = sub.add_parser("stats", help="Estadísticas resumen")
    p_st.set_defaults(func=cmd_stats)

    args = parser.parse_args()
    if not args.sub:
        parser.print_help()
        print()
        cmd_stats(args)
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
