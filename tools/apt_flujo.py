"""APT FLUJO — corre apt-crear + apt-guardar + apt-plano SIN pausas.

Pausa SOLO antes de "Enviar al CFIA" para que el operador haga revisión
visual integral una sola vez.

USO:
  python tools/apt_flujo.py SEG-2026-007

  python tools/apt_flujo.py SEG-2026-007 --auto-enviar
    # Después del último OK pregunta 'ENVIAR' por stdin y manda
    # (en vez de dejarlo abierto para revisión manual).

Flujo:
  1. apt-crear           (llenar bC1-bC8, sin pausa)
  2. apt-guardar         (click GUARDAR — genera N° trámite)
  3. apt-plano           (llenar bP1-bP7 + subir archivos)
  4. PAUSA OBLIGATORIA   → revisión visual humana
  5. apt-enviar          (click #BtnEnviarAgrimensura, no FD)

La FD va embebida en planof.pdf (subido en bP7), por eso el envío no
pide token. Regla aprendida 2026-05-15.
"""
from __future__ import annotations
import io
import os
import subprocess
import sys
from pathlib import Path

os.chdir(r"C:\catastro-bot")
os.environ.setdefault("CATASTRO_BOT_DEV_MODE", "1")
sys.path.insert(0, os.getcwd())
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")


def correr(args: list[str], *, auto_input: str = None) -> int:
    """Ejecuta un tool del bot. Devuelve exit code."""
    cmd = [sys.executable, "tools/" + args[0] + ".py"] + args[1:]
    print()
    print("─" * 70)
    print(f"  ▶ {' '.join(args)}")
    print("─" * 70)
    if auto_input is not None:
        result = subprocess.run(cmd, input=auto_input + "\n",
                                capture_output=False, text=True)
    else:
        result = subprocess.run(cmd, capture_output=False)
    return result.returncode


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    expediente = sys.argv[1].upper().strip()
    auto_enviar = "--auto-enviar" in sys.argv

    print("═" * 70)
    print(f"  APT FLUJO — {expediente}")
    print("═" * 70)
    print()
    print("Paso 1/4 — apt-crear (sin pausa antes de GUARDAR)...")

    # Setear var de env para que apt-crear no pause antes de GUARDAR
    # (lo manejamos via subprocess que ya envía ENTER en su stdin)
    rc = correr(["run_apt_crear_auto", expediente])
    if rc != 0:
        print(f"\n[ABORT] apt-crear falló (rc={rc})")
        return rc

    print("\nPaso 2/4 — apt-guardar (auto)...")
    rc = correr(["guardar_contrato_apt", expediente], auto_input="")
    if rc != 0:
        print(f"\n[ABORT] apt-guardar falló (rc={rc})")
        return rc

    print("\nPaso 3/4 — apt-plano (sin pausa)...")
    rc = correr(["run_apt_plano_auto", expediente])
    if rc != 0:
        print(f"\n[ABORT] apt-plano falló (rc={rc})")
        return rc

    print()
    print("═" * 70)
    print(f"  ✅ CONTRATO + PLANO LISTOS — {expediente}")
    print("═" * 70)
    print()
    print("  PAUSA OBLIGATORIA — revisión visual integral:")
    print("    1. bC1 propietario (cédula + nombre + correo)")
    print("    2. bC4 protocolo (tomo + folio del cajetín)")
    print("    3. bC7 honorarios (verificar +5000 fijos)")
    print("    4. bP1 generales (área + tamaño + uso)")
    print("    5. bP2 finca correcta")
    print("    6. bP4 titular = propietario actual")
    print("    7. bP5 planos a modificar")
    print("    8. bP6 entero TASADO (no debitado)")
    print("    9. bP7 archivos correctos (planof FIRMADO + entero + derrotero)")
    print()
    print("  ⚠️  Recordá: planof.pdf YA debe estar firmado con tu FD.")
    print("      La firma va embebida en el PDF — APT no la pide en click.")
    print()

    if auto_enviar:
        print("  Modo --auto-enviar: paso 4/4 con confirmación stdin")
        rc = correr(["apt_enviar", expediente])
    else:
        print("  Cuando todo esté OK, corré:")
        print(f"    catastro-bot apt-enviar {expediente}")
        rc = 0
    return rc


if __name__ == "__main__":
    sys.exit(main())
