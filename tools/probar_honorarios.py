"""Calculadora interactiva de honorarios — Decreto 17481-MOPT + reglas oficina.

Reglas de oficina aplicadas:
  - Auto-detect urbana/rural por área (<2000 → urbana E, ≥2000 → rural)
  - +5000 colones por plano individual (ajuste fijo)
  - Sin terreno quebrado, sin gastos reembolsables (no se cobran)

USO:
  .venv\\Scripts\\python tools\\probar_honorarios.py
"""
from __future__ import annotations
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from src.utils.honorarios_calculator import (
    HonorariosInput, PlanoInput, calcular_honorarios,
    decidir_tipo_y_zona_por_area, AJUSTE_FIJO_POR_PLANO,
)


def ask(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    print(f">>> {label}{suffix}: ", end="", flush=True)
    try:
        ans = input().strip()
    except EOFError:
        ans = ""
    return ans or default


def ask_float(label: str, default: float = 0.0) -> float:
    while True:
        v = ask(label, str(default))
        try:
            return float(v)
        except ValueError:
            print("  ⚠️ Escriba un número.")


def ask_int(label: str, default: int = 1) -> int:
    while True:
        v = ask(label, str(default))
        try:
            return int(v)
        except ValueError:
            print("  ⚠️ Escriba un entero.")


def ask_bool(label: str, default: bool = False) -> bool:
    d = "s" if default else "n"
    while True:
        ans = ask(f"{label} (s/n)", d).lower()
        if ans in ("s", "si", "y"):
            return True
        if ans in ("n", "no"):
            return False
        print("  Responda s o n.")


def imprimir_resultado(r):
    print()
    print("═" * 70)
    print("  RESULTADO POR PLANO")
    print("═" * 70)
    for pr in r.planos_resultado:
        tipo_zona = pr.tipo_parcela + (f"-{pr.zona}" if pr.zona else "")
        print(f"\n  Plano #{pr.indice}: {pr.area_m2} m² ({tipo_zona})")
        print(f"    Y_base                    ₡{pr.y_base:>15,}")
        if pr.tipo_parcela == "urbana":
            print(f"    Y_zona                    ₡{pr.y_zona:>15,}")
        print(f"    Precio formula            ₡{pr.precio_base:>15,}")
        if pr.aplica_multiplicador:
            print(f"    × 1.50 (agravante)")
        print(f"    + ajuste fijo oficina     ₡{int(AJUSTE_FIJO_POR_PLANO):>15,}")
        print(f"    PRECIO INDIVIDUAL FINAL   ₡{pr.precio_final:>15,}")
        if pr.aplico_minimo_legal:
            print(f"    ⚠️ aplicó mínimo legal {pr.tipo_parcela}")

    n = len(r.planos_resultado)
    print()
    print("═" * 70)
    print(f"  DESCUENTO POR CANTIDAD ({n} plano(s))")
    print("═" * 70)
    for etiq, _, _, sub in r.desglose_planos:
        print(f"  {etiq}")
    print(f"  ───────────────────────────────────────────────")
    print(f"  SUBTOTAL HONORARIOS           ₡{r.subtotal_planos:>15,}")

    if r.subtotal_gastos > 0:
        print(f"\n  Gastos reembolsables         ₡{r.subtotal_gastos:>15,}")

    print()
    print(f"  ┌─────────────────────────────────────────────┐")
    print(f"  │ TOTAL FINAL:        ₡{r.total:>15,}        │")
    print(f"  └─────────────────────────────────────────────┘")
    if n > 1:
        promedio = r.subtotal_planos / n
        print(f"  Promedio por plano:           ₡{promedio:>15,.2f}")

    if r.notas:
        print()
        for nota in r.notas:
            print(f"  📌 {nota}")


def caso_simple():
    """Un único plano (o varios planos del mismo tamaño)."""
    print()
    area = ask_float("Área en m² (esquina inferior izq del plano)")
    if area <= 0:
        return None

    tipo_auto, zona_auto = decidir_tipo_y_zona_por_area(area)
    print(f"   → auto-clasificado: {tipo_auto} {zona_auto}")
    n = ask_int("Cantidad de planos en el contrato (mismo tamaño)", 1)
    return HonorariosInput(area_m2=area, n_planos=n)


def caso_multi():
    """Varios planos de diferentes tamaños."""
    print()
    n = ask_int("¿Cuántos planos diferentes hay?", 2)
    planos = []
    for i in range(1, n + 1):
        area = ask_float(f"  Plano #{i} — área m²")
        if area > 0:
            tipo_auto, zona_auto = decidir_tipo_y_zona_por_area(area)
            print(f"     → auto-clasificado: {tipo_auto} {zona_auto}")
            planos.append(PlanoInput(area_m2=area))
    if not planos:
        return None
    return HonorariosInput(planos=planos)


def main() -> int:
    print("\n" + "═" * 70)
    print("  CALCULADORA DE HONORARIOS — Decreto 17481-MOPT + oficina")
    print("═" * 70)
    print("  Reglas de oficina aplicadas automáticamente:")
    print("  • <2000 m² → urbana zona E    | ≥2000 m² → rural")
    print("  • +5,000 fijo por plano individual")
    print("  • sin terreno quebrado, sin gastos de transporte")
    print("═" * 70)

    while True:
        print()
        print("─── Nuevo cálculo ───")
        print("  1 = todos los planos del MISMO tamaño (caso típico)")
        print("  2 = planos de TAMAÑOS DIFERENTES")
        print("  q = salir")
        modo = ask("Modo", "1").lower()
        if modo == "q":
            print("Hasta luego.")
            break

        if modo == "2":
            inp = caso_multi()
        else:
            inp = caso_simple()

        if inp is None:
            continue

        try:
            r = calcular_honorarios(inp)
            imprimir_resultado(r)
        except Exception as exc:
            print(f"\n[ERROR] {exc}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
