"""APT R2 (segunda ronda) — sube anverso corregido + visado municipal.

USO:
  python tools/run_apt_r2.py SEG-2026-003

Pre-requisitos en BD/carpeta del expediente:
  - apt_tramite ya seteado (de R1)
  - amberso.pdf (plano corregido post-muni) en 01_Campo/
  - visado.pdf (PDF visado de la muni) en 03_Muni/ o 01_Campo/
  - Chrome del bot abierto + sesión APT logueada
  - Trámite en estado "Defectuoso" o "Calificación RN"

NO firma con Firma Digital — eso queda al operador (token físico).
"""
from __future__ import annotations
import io
import json
import os
import sys
import time
from pathlib import Path

os.chdir(r"C:\catastro-bot")
os.environ.setdefault("CATASTRO_BOT_DEV_MODE", "1")
sys.path.insert(0, os.getcwd())
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")

from src.agents.apt_agent import APTAgent
from src.core.credential_manager import CredentialManager
from src.core.database import Database


def _localizar_archivo(carpetas: list[Path], patrones: list[str]) -> Path | None:
    """Devuelve el primer archivo que matchea cualquiera de los patrones."""
    for carpeta in carpetas:
        if not carpeta.exists():
            continue
        for patron in patrones:
            for p in carpeta.glob(patron):
                if p.is_file():
                    return p
    return None


def main() -> int:
    if len(sys.argv) < 2:
        print("USO: python tools/run_apt_r2.py <NUMERO_EXP> [--visado PATH] "
              "[--anverso PATH] [--entero PATH]")
        return 1
    numero_exp = sys.argv[1].upper().strip()

    # Args opcionales
    extra = sys.argv[2:]
    override_anverso = None
    override_visado  = None
    override_entero  = None
    i = 0
    while i < len(extra):
        if extra[i] == "--anverso" and i + 1 < len(extra):
            override_anverso = Path(extra[i + 1]); i += 2
        elif extra[i] == "--visado" and i + 1 < len(extra):
            override_visado = Path(extra[i + 1]); i += 2
        elif extra[i] == "--entero" and i + 1 < len(extra):
            override_entero = Path(extra[i + 1]); i += 2
        else:
            print(f"[WARN] arg desconocido: {extra[i]}")
            i += 1

    creds = CredentialManager()
    db = Database(Path("data/catastro.db"), creds)
    db.initialize_schema()

    exp = db.buscar_por_numero(numero_exp)
    if not exp:
        print(f"[ERROR] {numero_exp} no existe")
        return 1
    meta = json.loads(exp.get("metadata_json") or "{}")
    tramite = meta.get("apt_tramite")
    if not tramite:
        print(f"[ERROR] expediente sin apt_tramite — primero R1")
        return 2

    print("═" * 70)
    print(f"  APT R2 — {numero_exp} (trámite {tramite})")
    print("═" * 70)

    # Localizar archivos
    path_carpeta = Path(meta.get("path_carpeta", ""))
    if not path_carpeta.exists():
        print(f"[ERROR] carpeta no existe: {path_carpeta}")
        return 3
    subcarpeta_campo = path_carpeta / meta.get("subcarpeta_archivos", "01_Campo")
    subcarpeta_muni  = path_carpeta / "03_Muni"

    anverso = override_anverso or _localizar_archivo(
        [subcarpeta_campo],
        ["amberso*.pdf", "anverso_corregido*.pdf", "anverso*.pdf"],
    )
    visado  = override_visado or _localizar_archivo(
        [subcarpeta_muni, subcarpeta_campo],
        ["visado*.pdf", "*visado*.pdf"],
    )
    entero  = override_entero  # opcional — solo si APT lo pidió

    print()
    print(f"  Anverso (corregido): {anverso}")
    print(f"  Visado municipal:    {visado}")
    print(f"  Entero (opcional):   {entero or '(no se sube en R2)'}")
    print()

    if not anverso or not anverso.exists():
        print("[ERROR] no se encontró anverso corregido (buscaba amberso.pdf)")
        return 4
    if not visado or not visado.exists():
        print("[ERROR] no se encontró visado municipal (PDF)")
        print(f"  Buscado en: {subcarpeta_muni} y {subcarpeta_campo}")
        return 5

    # Confirmación humana
    print("─" * 70)
    resp = input("¿Subir estos archivos a APT R2? [s/N] ").strip().lower()
    if resp not in ("s", "si", "sí", "y", "yes"):
        print("(cancelado)")
        return 0

    agent = APTAgent(db, creds)
    try:
        resultado = agent.presentar_r2(
            expediente_id=exp["id"],
            archivo_anverso=anverso,
            archivo_entero=entero,
            archivo_visado=visado,
        )
    except Exception as exc:
        print(f"\n[ERROR] presentar_r2 falló: {exc}")
        return 6

    print()
    print("─" * 70)
    print(f"  Resultado APT R2 ({numero_exp}):")
    print("─" * 70)
    print(f"  Trámite:         {resultado.get('tramite', tramite)}")
    print(f"  Archivos OK:     {resultado.get('archivos_subidos', [])}")
    if resultado.get("errores"):
        print(f"  Errores:")
        for e in resultado["errores"]:
            print(f"    ❌ {e}")
    print(f"  Listo para FD:   {resultado.get('listo_para_fd', False)}")
    print()

    if resultado.get("listo_para_fd"):
        print("=" * 70)
        print("  ✅ ARCHIVOS R2 SUBIDOS")
        print("=" * 70)
        print()
        print("  ACCIÓN MANUAL DEL OPERADOR:")
        print("    1. Verificar archivos visualmente en el portal APT")
        print("    2. Firmar con Firma Digital (token físico)")
        print("    3. Confirmar en WhatsApp: 'FD R2 SI'")
        print("       (o ejecutar el workflow para que avance a INSCRIBIENDO)")
        return 0
    else:
        print("⚠️  R2 NO listo — revisar errores arriba")
        return 7


if __name__ == "__main__":
    sys.exit(main())
