"""Pre-llena datos_apt para RDF-2026-003 (ROLANDO_Y_ROLANDO_VUELTA).

Plano de RECTIFICACIÓN DE ÁREA — finca 2-115747-000 propiedad de
SOCIEDAD AGROPECUARIA LAS ESTUFAS S.A.

NOTAS DE REGISTRO:
  - El registro RNP mostraba la cédula jurídica como '3-101-' (incompleta).
    El operador la completó con '3-101-044683'. El bot registra la
    discrepancia automáticamente y notifica al topógrafo.
  - Naturaleza: café en producción + 1 casa → tipo_uso APT = 23 (CULTIVOS VARIOS)
  - No hay plano anterior que rectifique (registro: "PLANO: NO SE INDICA")
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


DATOS_APT = {
    # ── Contrato APT ──────────────────────────────────────────────────────
    "propietario": {
        "tipo_cedula": "2",                          # JURIDICA
        "cedula":      "3-101-044683",               # completada por operador
        "cedula_registro_original": "3-101-",        # ⚠️ registro mostraba incompleto
        "nombre":      "SOCIEDAD AGROPECUARIA LAS ESTUFAS SOCIEDAD ANONIMA",
        "correo":      "topografiahrh@gmail.com",
    },
    "contratante_es_propietario": True,
    "profesional": {
        "correo": "topografiahrh@gmail.com",
    },
    "protocolo": {
        "numero":              "19245",
        "folio":               "082",
        "tipo_proyecto_modal": "27",                 # Plano Simple
    },
    "proyecto": {
        "tipo_plano_apt": "27",
        "provincia":      "2",       # ALAJUELA
        "canton":         "02",      # SAN RAMON — APT exige zero-padding para <10
        "distrito":       "07",      # SAN ISIDRO — idem
        "naturaleza":     "2",       # Equidad
    },
    "general": {
        "area_predio":            "55679",           # área según registro
        "area_real":              "63803.34",        # del cajetín
        "moneda":                 "1",
        "honorarios":             "511500",          # calculadora rural + ajuste
        "exoneracion_honorarios": False,
        "adelanto":               "0",
        "pagos_parciales":        "0",
        "plazo_entrega":          "al finalizar el contrato",
        "max_planos":             "1",
        "observaciones":          "",
        "composicion":            "unipersonal",
    },

    # ── Sección PLANO (post-creación contrato) ────────────────────────────
    "plano": {
        "descripcion":     "ROVUELT(1)",
        "area_real":       "63803.34",
        "area_registro":   "55679",
        "tipo_zona":       "2",                       # RURAL (>2000m²)
        "tipo_ubicacion":  "",                        # rural — no aplica
        "tipo_uso":        "23",                      # CULTIVOS VARIOS (café)
        "tamanno":         "",                        # auto-detectado del PDF
        "tipo_coordenada": "3",                       # CRTM05
        "norte":           "1115404.65",              # centroide areal
        "este":            "451790.07",
        "vertices":        "45",
        "del_estado":      False,
        "fincas": [
            {"provincia": "2", "numero": "115747", "derecho": "000", "duplicado": ""},
        ],
        "titulares": [
            # Solo el propietario (sociedad). No hay otros titulares de
            # derechos en la finca según el registro.
        ],
        # No hay plano anterior — registro dice "PLANO: NO SE INDICA".
        "planos_modificar": [],
        "entero": {
            "numero":         "660821664",
            "fecha":          "2026-05-08",
            "total_cfia":     "1600",
            "total_registro": "55000",
            "monto_pagado":   "57294.01",             # tasado sin descuento
            "cit_ntrip":      "300",
        },
        "archivos": {
            "anverso":   "data/files/ALAJUELA/SAN_RAMON/SAN_ISIDRO/ROLANDO_Y_ROLANDO_VUELTA/01_Campo/planof.pdf",
            "entero":    "data/files/ALAJUELA/SAN_RAMON/SAN_ISIDRO/ROLANDO_Y_ROLANDO_VUELTA/01_Campo/entero.pdf",
            "derrotero": "data/files/ALAJUELA/SAN_RAMON/SAN_ISIDRO/ROLANDO_Y_ROLANDO_VUELTA/01_Campo/Derrotero.zip",
        },
    },
}


def main() -> int:
    # Auto-detectar tamaño del PDF
    try:
        from src.utils.plano_tamanno import detectar_tamanno_apt
        tam = detectar_tamanno_apt(DATOS_APT["plano"]["archivos"]["anverso"])
        if tam:
            DATOS_APT["plano"]["tamanno"] = tam
            print(f"[auto] tamaño del plano detectado: {tam}")
    except Exception as exc:
        print(f"[warn] no se pudo detectar tamaño: {exc}")

    conn = sqlite3.connect("data/catastro.db")
    row = conn.execute(
        "SELECT id, metadata_json FROM expedientes WHERE numero_expediente = ?",
        ("RDF-2026-003",),
    ).fetchone()
    if not row:
        print("[ERROR] RDF-2026-003 no existe")
        return 1
    meta = json.loads(row[1] or "{}")
    meta["datos_apt"] = DATOS_APT
    conn.execute(
        "UPDATE expedientes SET metadata_json = ? WHERE id = ?",
        (json.dumps(meta, ensure_ascii=False), row[0]),
    )
    conn.commit()
    conn.close()

    print("\n✅ datos_apt guardado para RDF-2026-003")
    print(json.dumps(DATOS_APT, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
