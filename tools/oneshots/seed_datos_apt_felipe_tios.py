"""Pre-llena datos_apt para RDF-2026-004 (FELIPE_TIOS).

Plano de SEGREGACIÓN — lote de 582.67 m² del folio real 2-422958-000
(propietario: LUIS EMILIO DE LOS ANGELES PIÑEIRO CASTRO).

NOTAS:
  - Protocolo 23549 NO es el activo (24162) → el bot disparará discrepancia
    automáticamente. Si es continuación de contrato viejo, el operador
    puede actualizar honorarios a 0 + agregar observaciones manualmente.
  - Modifica plano anterior A-1095215-2006 (registrado en bP5)
  - Área 582.67 m² < 2000 → URBANA zona E
  - Naturaleza "TERRENO DE CAFE" → tipo_uso 23 (CULTIVOS VARIOS)
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
        "tipo_cedula": "1",                          # FÍSICA
        "cedula":      "2-0466-0095",
        # Nombre/apellidos: APT autocompleta del RNP. Para validación:
        "nombre":      "LUIS EMILIO DE LOS ANGELES",
        "apellido1":   "PIÑEIRO",
        "apellido2":   "CASTRO",
        "correo":      "topografiahrh@gmail.com",
    },
    "contratante_es_propietario": True,
    "profesional": {
        "correo": "topografiahrh@gmail.com",
    },
    "protocolo": {
        "numero":              "23549",              # ⚠️ NO es el activo (24162)
        "folio":               "178",
        "tipo_proyecto_modal": "27",
    },
    "proyecto": {
        "tipo_plano_apt": "27",                      # Plano Simple
        "provincia":      "2",                        # ALAJUELA
        "canton":         "02",                       # SAN RAMON (zero-padded)
        "distrito":       "09",                       # ALFARO (zero-padded)
        "naturaleza":     "2",                        # Equidad
    },
    "general": {
        "area_predio":            "741.68",           # área según registro
        "area_real":              "582.67",           # del cajetín
        "moneda":                 "1",
        # Honorarios=0: continuación del contrato viejo 1209447. El protocolo
        # 23549 es del contrato anterior — ya se cobraron honorarios ahí.
        "honorarios":             "0",
        "exoneracion_honorarios": True,
        "adelanto":               "0",
        "pagos_parciales":        "0",
        "plazo_entrega":          "al finalizar el contrato",
        "max_planos":             "1",
        "observaciones":          "NO SE COBRAN HONORARIOS CAMBIOS POR ERROR EN CONTRATO NUMERO 1209447",
        "composicion":            "unipersonal",
    },

    # ── Sección PLANO ─────────────────────────────────────────────────────
    "plano": {
        "descripcion":     "FELIPETIOS(4)",
        "area_real":       "582.67",
        "area_registro":   "741.68",
        "tipo_zona":       "1",                       # URBANA (<2000m²)
        "tipo_ubicacion":  "E",                       # zona E urbana
        "tipo_uso":        "23",                      # CULTIVOS VARIOS (café)
        "tamanno":         "",                        # auto-detectado del PDF
        "tipo_coordenada": "3",                       # CRTM05
        "norte":           "1114018.99",              # centroide areal
        "este":            "445571.99",
        "vertices":        "8",
        "del_estado":      False,
        "fincas": [
            {"provincia": "2", "numero": "422958", "derecho": "000", "duplicado": ""},
        ],
        "titulares": [
            # Solo el propietario (que ya está en el contrato). No hay
            # otros titulares de derechos.
        ],
        "planos_modificar": [
            # El cajetín dice "MODIFICA PLANOS CATASTRADOS N° A-1095215-2006"
            # y el registro confirma "PLANO: A-1095215-2006".
            {"provincia": "2", "numero": "1095215", "anno": "2006"},
        ],
        "entero": {
            "numero":         "660822113",
            "fecha":          "2026-05-08",
            "total_cfia":     "1600",
            "total_registro": "10000",
            "monto_pagado":   "11940.00",             # tasado sin descuento
            "cit_ntrip":      "300",
        },
        "archivos": {
            "anverso":   "data/files/ALAJUELA/SAN_RAMON/ALFARO/FELIPE_TIOS/01_Campo/planof.pdf",
            "entero":    "data/files/ALAJUELA/SAN_RAMON/ALFARO/FELIPE_TIOS/01_Campo/entero.pdf",
            "derrotero": "data/files/ALAJUELA/SAN_RAMON/ALFARO/FELIPE_TIOS/01_Campo/Derrotero.zip",
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
        ("RDF-2026-004",),
    ).fetchone()
    if not row:
        print("[ERROR] RDF-2026-004 no existe")
        return 1
    meta = json.loads(row[1] or "{}")
    meta["datos_apt"] = DATOS_APT
    conn.execute(
        "UPDATE expedientes SET metadata_json = ? WHERE id = ?",
        (json.dumps(meta, ensure_ascii=False), row[0]),
    )
    conn.commit()
    conn.close()

    print("\n✅ datos_apt guardado para RDF-2026-004")
    print(json.dumps(DATOS_APT, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
