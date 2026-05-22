"""Extrae datos del plano + registro + entero via Claude Vision y arma datos_apt.

USO:
  python tools/extraer_datos_apt.py <NUMERO_EXP>
    └→ Lee data/files/<PROV>/<CANT>/<DIST>/<NOMBRE>/01_Campo/ del expediente
       en BD, detecta los 3 archivos clave, extrae con Vision, arma datos_apt
       y muestra el JSON.

  python tools/extraer_datos_apt.py <NUMERO_EXP> --save
    └→ Persiste datos_apt en metadata_json del expediente.

  python tools/extraer_datos_apt.py <NUMERO_EXP> --files <folder>
    └→ Override del folder donde están los archivos.

Auto-detecta archivos:
  plano.pdf | planof.pdf          → anverso/cajetín
  INFORMACION DE REGISTRO.png     → registro RNP (case-insensitive)
  entero.pdf                       → comprobante BCR
  Derrotero.zip                    → shapefiles (para bP7)
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

from src.core.credential_manager import CredentialManager  # noqa: E402
from src.utils.plano_vision_extractor import PlanoVisionExtractor  # noqa: E402
from src.utils.seed_builder import build_datos_apt  # noqa: E402


def _detectar_archivos(folder: Path, subcarpeta: str = "01_Campo") -> dict:
    """Busca archivos típicos del expediente.

    Args:
        folder: raíz del proyecto (path_carpeta del expediente).
        subcarpeta: relativa a `folder`. Default '01_Campo'. Si el
            expediente comparte contrato con un hermano, esto puede ser
            '01_Campo/REU' o '01_Campo/SEG' (de metadata.subcarpeta_archivos).

    Patrones tolerantes (acepta sufijos numerados como "planof 1.pdf"):
      plano*.pdf | planof*.pdf       → anverso/cajetín (planof gana)
      *registro*.png|.jpg            → consulta registral
      entero*.pdf                    → comprobante BCR
      derrotero*.zip                 → shapefiles
    """
    campo = folder / subcarpeta
    if not campo.exists():
        return {}
    out: dict = {"plano": None, "registro": None, "entero": None, "derrotero": None}
    for f in campo.iterdir():
        if not f.is_file():
            continue
        nombre_lower = f.name.lower()
        stem = f.stem.lower()  # sin extensión

        # planof toma precedencia (firmado) sobre plano
        if (stem == "planof" or stem.startswith("planof ")
                or stem.startswith("planof_") or stem.startswith("planof-")):
            out["plano"] = f
        elif out["plano"] is None and (
                stem == "plano" or stem.startswith("plano ")
                or stem.startswith("plano_") or stem.startswith("plano-")):
            out["plano"] = f
        elif ("registro" in nombre_lower
              and nombre_lower.endswith((".png", ".jpg", ".jpeg", ".pdf"))):
            # La oficina a veces descarga la consulta como PDF, otras como PNG.
            # Si ya hay uno, preferir el sin "2" en el nombre (la primera consulta).
            if out["registro"] is None or "2" in out["registro"].stem:
                out["registro"] = f
        elif (stem == "entero" or stem.startswith("entero ")
              or stem.startswith("entero_") or stem.startswith("entero-")):
            out["entero"] = f
        elif (nombre_lower.endswith(".zip")
              and "derrotero" in nombre_lower):
            out["derrotero"] = f
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("numero", help="Número de expediente (ej. RDF-2026-004)")
    parser.add_argument("--save", action="store_true", help="Persistir datos_apt en BD")
    parser.add_argument("--files", help="Override folder con los archivos del plano")
    parser.add_argument("--correo", default="topografiahrh@gmail.com",
                        help="Correo del profesional (default oficina)")
    args = parser.parse_args()

    numero = args.numero.upper().strip()

    # Encontrar expediente en BD
    conn = sqlite3.connect("data/catastro.db")
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT id, metadata_json FROM expedientes WHERE numero_expediente = ?",
        (numero,),
    ).fetchone()
    if not row:
        print(f"[ERROR] expediente {numero} no existe")
        return 1
    meta = json.loads(row["metadata_json"] or "{}")

    # Folder
    if args.files:
        folder = Path(args.files)
    elif meta.get("path_carpeta"):
        folder = Path(meta["path_carpeta"])
    else:
        print(f"[ERROR] expediente {numero} sin path_carpeta en metadata — usar --files")
        return 1
    if not folder.exists():
        print(f"[ERROR] folder no existe: {folder}")
        return 1

    # Si el expediente comparte contrato con un hermano, usar subcarpeta
    # específica (ej. '01_Campo/REU' o '01_Campo/SEG').
    subcarpeta = meta.get("subcarpeta_archivos", "01_Campo")
    archivos = _detectar_archivos(folder, subcarpeta=subcarpeta)
    print(f"\n📂 Folder:     {folder}")
    print(f"   Subcarpeta: {subcarpeta}")
    for k, v in archivos.items():
        print(f"   {k:9} → {v.name if v else '(no encontrado)'}")
    print()

    if not archivos["plano"]:
        print("[ERROR] no se encontró plano.pdf ni planof.pdf en 01_Campo/")
        return 1

    # API key — prioridad: env var > Credential Manager
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        creds = CredentialManager()
        try:
            api_key = creds.get_anthropic_key()
        except Exception:
            pass
    # Fallback: si no hay API key, intentar pypdf+regex.
    # Cobertura buena para planos modernos del CFIA (texto plano en el cuerpo);
    # PDFs escaneados (entero a veces) quedan sin extracción.
    if not api_key:
        print("⚠️  Sin ANTHROPIC API key — usando fallback pypdf+regex.")
        print("    (PDFs escaneados no se podrán leer)")
        print()
        from src.utils.plano_pdf_extractor import extraer_plano_metadata
        from src.utils.registro_pdf_extractor import extraer_registro_pypdf
        from src.utils.plano_vision_extractor import (
            CajetinData, RegistroData, EnteroData,
        )

        md_plano = extraer_plano_metadata(archivos["plano"], anthropic_api_key=None)

        # Si hay Derrotero.zip, usar sus coordenadas (autoritativas) como
        # cajetin.coordenadas — esto destraba el cálculo de centroide y vertices.
        coords_derrotero = []
        if archivos.get("derrotero"):
            try:
                from src.utils.derrotero_validator import leer_derrotero_zip
                d = leer_derrotero_zip(archivos["derrotero"])
                if d.coords and not d.error:
                    coords_derrotero = [list(c) for c in d.coords]
                    print(f"   [pre] Derrotero.zip: {d.n_vertices} vértices, "
                          f"área {d.area_m2:.2f}m²")
            except Exception as exc:
                print(f"   [pre] Derrotero falló: {exc}")

        cajetin = CajetinData(
            protocolo_tomo=md_plano.protocolo_tomo,
            protocolo_folio=md_plano.protocolo_folio,
            numero_entero=md_plano.numero_entero,
            area_real=md_plano.area_m2,
            area_registro=md_plano.area_segun_registro,
            identificador_predial=md_plano.identificador_predial,
            profesional_carne=md_plano.profesional_carne,
            profesional_nombre=md_plano.profesional_nombre,
            coordenadas=coords_derrotero,  # del shapefile (autoritativa)
        )
        print(f"   [1/3] Cajetín (pypdf): área real {cajetin.area_real} m² | "
              f"protocolo {cajetin.protocolo_tomo}/{cajetin.protocolo_folio} | "
              f"modifica {md_plano.modifica_plano!r}")
        if md_plano.vertices_a_via:
            print(f"         vértices a vía: {md_plano.vertices_a_via} "
                  f"(frente {md_plano.frente_calle_m}m)")

        if archivos["registro"]:
            registro = extraer_registro_pypdf(archivos["registro"])
            print(f"   [2/3] Registro (pypdf): finca {registro.finca}/{registro.derecho} | "
                  f"{registro.tipo_propietario} {registro.cedula_propietario} | "
                  f"{registro.nombre_propietario or ''} "
                  f"{registro.apellido1_propietario} {registro.apellido2_propietario}".strip())
        else:
            registro = RegistroData()
            print("   [2/3] (sin registro — se omite)")

        if archivos["entero"]:
            # Para el entero usamos los regex de plano_pdf_extractor (NUMERO_ENTERO)
            md_ent = extraer_plano_metadata(archivos["entero"], anthropic_api_key=None)
            entero = EnteroData(numero=md_ent.numero_entero)
            print(f"   [3/3] Entero (pypdf): {entero.numero or '(no detectado — '
                  f'PDF probablemente escaneado)'}")
        else:
            entero = EnteroData()
            print("   [3/3] (sin entero — se omite)")
    else:
        print("🤖 Extrayendo con Claude Vision...")
        ex = PlanoVisionExtractor(api_key=api_key)

        print("   [1/3] Cajetín del plano...")
        cajetin = ex.extract_cajetin(archivos["plano"])
        print(f"         → {cajetin.descripcion or '(?)'} | "
              f"protocolo {cajetin.protocolo_tomo}/{cajetin.protocolo_folio} | "
              f"{len(cajetin.coordenadas)} vértices")

        if archivos["registro"]:
            print("   [2/3] Información de registro...")
            registro = ex.extract_registro(archivos["registro"])
            # Defensa en profundidad: Vision a veces deja campos vacíos
            # (especialmente nombre para jurídicas). Cruzar con pypdf y
            # rellenar lo faltante — pypdf es determinístico.
            try:
                from src.utils.registro_pdf_extractor import extraer_registro_pypdf
                reg_pp = extraer_registro_pypdf(archivos["registro"])
                for campo in (
                    "finca", "derecho", "tipo_propietario", "cedula_propietario",
                    "nombre_propietario", "apellido1_propietario",
                    "apellido2_propietario", "naturaleza", "area_registro_m2",
                    "plano_previo", "provincia_finca", "canton_finca",
                    "distrito_finca", "valor_fiscal",
                ):
                    if not getattr(registro, campo, "") and getattr(reg_pp, campo, ""):
                        setattr(registro, campo, getattr(reg_pp, campo))
                        print(f"         (pypdf rellena {campo})")
            except Exception as exc:
                print(f"         (pypdf fallback falló: {exc})")
            print(f"         → finca {registro.finca}/{registro.derecho} | "
                  f"{registro.tipo_propietario} {registro.cedula_propietario} | "
                  f"{registro.nombre_propietario} {registro.apellido1_propietario} {registro.apellido2_propietario}")
        else:
            from src.utils.plano_vision_extractor import RegistroData
            registro = RegistroData()
            print("   [2/3] (sin registro.png — se omite)")

        if archivos["entero"]:
            print("   [3/3] Entero BCR...")
            entero = ex.extract_entero(archivos["entero"])
            print(f"         → {entero.numero} | tasado {entero.monto_tasado} | "
                  f"fecha {entero.fecha}")
        else:
            from src.utils.plano_vision_extractor import EnteroData
            entero = EnteroData()
            print("   [3/3] (sin entero.pdf — se omite)")

    # Armar datos_apt
    print("\n🔧 Aplicando reglas de oficina + computando derivados...")
    rutas_relativas = {
        k: str(archivos[k_real]).replace("\\", "/")
        for k, k_real in [("anverso", "plano"), ("entero", "entero"), ("derrotero", "derrotero")]
        if archivos.get(k_real)
    }
    res = build_datos_apt(
        cajetin=cajetin,
        registro=registro,
        entero=entero,
        correo_profesional=args.correo,
        archivos_paths=rutas_relativas,
    )

    print("\n📋 datos_apt resultante:")
    print(json.dumps(res["datos_apt"], ensure_ascii=False, indent=2))

    if res["advertencias"]:
        print("\n⚠️  ADVERTENCIAS:")
        for a in res["advertencias"]:
            print(f"   • {a}")
    if res["confianza_baja"]:
        print("\n❓ CAMPOS CON BAJA CONFIANZA (revisar antes de seedear):")
        for c in res["confianza_baja"]:
            print(f"   • {c}")

    if args.save:
        meta["datos_apt"] = res["datos_apt"]
        conn.execute(
            "UPDATE expedientes SET metadata_json = ? WHERE id = ?",
            (json.dumps(meta, ensure_ascii=False), row["id"]),
        )
        conn.commit()
        print(f"\n✅ datos_apt persistido en BD para {numero}")
    else:
        print(f"\n(usa --save para persistir a BD)")
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
