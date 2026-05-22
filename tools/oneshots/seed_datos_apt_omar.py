"""Pre-llena datos_apt para RDF-2026-002 (OMAR_2026)."""
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
        "tipo_cedula": "1",                     # FÍSICA
        "cedula":      "2-0310-0121",
        # nombre_esperado: el bot lo compara contra la respuesta del RNP.
        # Si la cédula trae a otra persona, ABORTA antes de guardar.
        "nombre_esperado": "OMAR ARIAS RAMIREZ",
        # nombre/apellidos: APT autocompleta del RNP
        "correo":      "topografiahrh@gmail.com",
    },
    "contratante_es_propietario": True,
    "profesional": {
        "correo": "topografiahrh@gmail.com",
    },
    "protocolo": {
        "numero":              "24162",
        "folio":               "100",
        "tipo_proyecto_modal": "27",
    },
    "proyecto": {
        "tipo_plano_apt": "27",
        "provincia":      "2",       # ALAJUELA
        "canton":         "16",      # RIO CUARTO
        "distrito":       "03",      # SANTA ISABEL
        "naturaleza":     "2",       # Equidad
    },
    "general": {
        "area_predio":            "40924",      # suma registros: 20,000 + 20,924
        "area_real":              "33497.75",   # del cajetín
        "moneda":                 "1",
        "honorarios":             "372000",     # 367,000 + 5,000 ajuste
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
        "descripcion":     "OMAPRI(1)",     # del cajetín
        "area_real":       "33497.75",
        "area_registro":   "40924",
        "tipo_zona":       "2",             # RURAL (≥2000)
        "tipo_ubicacion":  "",              # rural no se toca
        "tipo_uso":        "23",            # CULTIVOS VARIOS (registro dice "cultivos")
        "tamanno":         "",              # auto-detectado del PDF
        "tipo_coordenada": "3",             # CRTM05
        "norte":           "1156837.95",    # centroide areal
        "este":            "477317.63",     # centroide areal
        "vertices":        "17",
        "del_estado":      False,
        "fincas": [
            {"provincia": "2", "numero": "605596", "derecho": "000", "duplicado": ""},
            {"provincia": "2", "numero": "567434", "derecho": "000", "duplicado": ""},
        ],
        # bP4 Titulares — propietarios ADICIONALES (el del contrato no se repite).
        # Solo "derechos", no usufructos. OMAR ya está en el contrato como
        # propietario; aquí va GRACE (segunda dueña de la finca 2-567434).
        #
        # Schema:
        #   tipo_cedula / cedula / titularidad
        #   nombre / apellido1 / apellido2  ← nombre real del REGISTRO
        # El bot consulta TSE con la cédula; si TSE devuelve nombre distinto
        # al del registro, SOBRESCRIBE los campos con el del registro y
        # registra la discrepancia para avisar al operador.
        #
        # Caso real: cédula 2-0440-0388 — registro dice GRACE ALVAREZ GONZALEZ
        # pero TSE responde AMALIA QUESADA RODRIGUEZ. El bot conserva la
        # cédula 2-0440-0388 y registra GRACE como nombre.
        "titulares": [
            {
                "tipo_cedula": "1",
                "cedula":      "2-0440-0388",
                "nombre":      "GRACE",
                "apellido1":   "ALVAREZ",
                "apellido2":   "GONZALEZ",
                "titularidad": "5",
            },
        ],
        "planos_modificar": [
            {"provincia": "2", "numero": "2285248", "anno": "2021"},
            {"provincia": "2", "numero": "2052383", "anno": "2018"},
        ],
        "entero": {
            "numero":         "660822563",
            "fecha":          "2026-05-08",
            "total_cfia":     "1600",
            "total_registro": "50000",
            "monto_pagado":   "52142.48",
            "cit_ntrip":      "300",
        },
        "archivos": {
            "anverso":   "data/files/ALAJUELA/RIO_CUARTO/SANTA_ISABEL/OMAR_2026/01_Campo/planof.pdf",
            "entero":    "data/files/ALAJUELA/RIO_CUARTO/SANTA_ISABEL/OMAR_2026/01_Campo/entero.pdf",
            "derrotero": "data/files/ALAJUELA/RIO_CUARTO/SANTA_ISABEL/OMAR_2026/01_Campo/Derrotero.zip",
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
        ("RDF-2026-002",),
    ).fetchone()
    if not row:
        print("[ERROR] RDF-2026-002 no existe")
        return 1
    meta = json.loads(row[1] or "{}")
    meta["datos_apt"] = DATOS_APT
    conn.execute(
        "UPDATE expedientes SET metadata_json = ? WHERE id = ?",
        (json.dumps(meta, ensure_ascii=False), row[0]),
    )
    conn.commit()
    conn.close()

    print("\n✅ datos_apt guardado para RDF-2026-002")
    print(json.dumps(DATOS_APT, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
