"""Dashboard CLI — vista de salud operativa del bot.

Muestra cantidades por estado, tipo, topógrafo, tiempos de ciclo,
anomalías recientes, discrepancias frecuentes y alertas proactivas.

USO:
  python tools/dashboard.py                  # texto coloreado en consola
  python tools/dashboard.py --json           # JSON estructurado
  python tools/dashboard.py --csv            # CSV (estado, tipo, topógrafo)
  python tools/dashboard.py --alertas-solo   # sólo alertas proactivas
"""
from __future__ import annotations
import argparse
import csv
import io
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("CATASTRO_BOT_DEV_MODE", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from src.core.credential_manager import CredentialManager  # noqa: E402
from src.core.database import Database  # noqa: E402
from src.utils.metricas import resumen_completo, alertas_proactivas  # noqa: E402


def _tabla(titulo: str, items: list[tuple[str, int]]) -> None:
    print(f"\n  {titulo}")
    print(f"  {'─' * (len(titulo) + 4)}")
    if not items:
        print("    (sin datos)")
        return
    longest = max(len(str(k)) for k, _ in items)
    for k, v in items:
        print(f"    {str(k):<{longest}}  {v:>5}")


def _imprimir_texto(r: dict) -> None:
    print()
    print("═" * 70)
    print(f"  📊 CATASTRO-BOT — Dashboard operativo")
    print("═" * 70)
    print(f"\n  Total expedientes activos: {r['total_expedientes']}")

    _tabla("Por estado workflow",
           sorted(r["por_estado"].items(), key=lambda x: -x[1]))
    _tabla("Por tipo de plano",
           sorted(r["por_tipo"].items(), key=lambda x: -x[1]))
    _tabla("Por topógrafo",
           sorted(r["por_topografo"].items(), key=lambda x: -x[1]))
    _tabla("Por estado APT",
           sorted(r["por_estado_apt"].items(), key=lambda x: -x[1]))

    print("\n  Tiempos")
    print("  ───────")
    t = r["tiempo_promedio_creacion_a_envio_dias"]
    if t is not None:
        print(f"    Creación → Envío CFIA (promedio):  {t:.1f} días")
    else:
        print(f"    Creación → Envío CFIA (promedio):  (sin datos)")

    print("\n  Honorarios")
    print("  ──────────")
    ratio = r["ratio_exoneracion_pct"]
    if ratio is not None:
        print(f"    Tasa de exoneración (honorarios=0): {ratio:.1f}%")
    else:
        print(f"    Tasa de exoneración:                (sin datos)")

    print("\n  Top discrepancias detectadas")
    print("  ─────────────────────────────")
    for tipo, n in r["discrepancias_frecuentes"]:
        print(f"    {n:>4}× {tipo}")
    if not r["discrepancias_frecuentes"]:
        print("    (ninguna)")

    print("\n  Últimas anomalías")
    print("  ──────────────────")
    for a in r["anomalias_recientes"]:
        ts = (a.get("ts") or "")[:19]
        ctx = a.get("contexto", "?")
        desc = (a.get("descripcion", "") or "")[:60]
        display = a.get("display") or a.get("numero_expediente", "?")
        print(f"    [{ts}] {display} | {ctx}")
        print(f"             → {desc}")
    if not r["anomalias_recientes"]:
        print("    (ninguna registrada)")

    if r["alertas_proactivas"]:
        print()
        print("  🚨 ALERTAS PROACTIVAS")
        print("  ─────────────────────")
        for alert in r["alertas_proactivas"]:
            print(f"    {alert}")
    print()
    print("═" * 70)


def _exportar_csv(r: dict) -> None:
    w = csv.writer(sys.stdout)
    w.writerow(["seccion", "clave", "valor"])
    for clave, valor in sorted(r["por_estado"].items()):
        w.writerow(["por_estado", clave, valor])
    for clave, valor in sorted(r["por_tipo"].items()):
        w.writerow(["por_tipo", clave, valor])
    for clave, valor in sorted(r["por_topografo"].items()):
        w.writerow(["por_topografo", clave, valor])
    for clave, valor in sorted(r["por_estado_apt"].items()):
        w.writerow(["por_estado_apt", clave, valor])
    for clave, valor in r["discrepancias_frecuentes"]:
        w.writerow(["discrepancia", clave, valor])
    t = r["tiempo_promedio_creacion_a_envio_dias"]
    if t is not None:
        w.writerow(["tiempo", "creacion_a_envio_dias", round(t, 2)])
    ratio = r["ratio_exoneracion_pct"]
    if ratio is not None:
        w.writerow(["ratio", "exoneracion_pct", round(ratio, 2)])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Salida JSON")
    parser.add_argument("--csv",  action="store_true", help="Salida CSV")
    parser.add_argument("--alertas-solo", action="store_true",
                        help="Mostrar solo alertas proactivas")
    args = parser.parse_args()

    creds = CredentialManager()
    db = Database(Path("data/catastro.db"), creds)
    db.initialize_schema()

    if args.alertas_solo:
        alertas = alertas_proactivas(db)
        if not alertas:
            print("✅ Sin alertas proactivas")
        else:
            for a in alertas:
                print(a)
        return 0

    r = resumen_completo(db)

    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    elif args.csv:
        _exportar_csv(r)
    else:
        _imprimir_texto(r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
