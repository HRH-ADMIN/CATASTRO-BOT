"""Armado del paquete municipal para subir al formulario Google Form.

REGLA OPERATIVA (aprendida 2026-05-13, TILMAN):
  Para cada segregación que va a Muni San Ramón, hay que combinar en UN
  solo PDF los 4 archivos siguientes (en orden):
    1. minuta (PDF) — minuta de calificación CFIA
    2. imagen minuta (PDF) — esquema visual del plano
    3. plano firmado (PDF) — planof.pdf
    4. carta de agua (PDF) — opcional pero típico

  Nombre del archivo combinado = N° de citas de presentación del CFIA
  que aparece dentro del PDF de la minuta como "2025 - 81701 - C".
  Se guarda con formato `2025 - 81701 - C.pdf`.

USO:
    from src.utils.muni_paquete import armar_paquete_muni
    res = armar_paquete_muni(expediente_id=eid, db=db)
    # res = {"pdf_path": Path("...2025 - 81701 - C.pdf"),
    #        "n_citas": "2025 - 81701 - C", "archivos_incluidos": [...]}
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional


# Regex para citas de presentación CFIA: "2025 - 81701 - C"
_RE_CITAS = re.compile(
    r"(\d{4})\s*[-—]\s*(\d{4,6})\s*[-—]\s*([A-Z])",
)


def extraer_numero_citas(minuta_pdf: Path) -> Optional[str]:
    """Extrae 'YYYY - NNNNN - X' del PDF de la minuta CFIA.

    Returns None si no encuentra el patrón.
    """
    try:
        import fitz
    except ImportError:
        return None
    if not minuta_pdf.exists():
        return None
    try:
        doc = fitz.open(str(minuta_pdf))
        texto = "".join(page.get_text() for page in doc)
        doc.close()
    except Exception:
        return None
    m = _RE_CITAS.search(texto)
    if not m:
        return None
    return f"{m.group(1)} - {m.group(2)} - {m.group(3)}"


def detectar_archivos_paquete(carpeta_seg: Path) -> dict:
    """Detecta los 4 archivos típicos en la subcarpeta de la segregación.

    Reconoce:
      *minuta.pdf      → minuta (excluir si tiene 'imagen' en nombre)
      *imagenminuta.pdf → imagen de la minuta
      planof*.pdf | PLANO*F.pdf | PLANO*.pdf  → plano firmado
      agua*.pdf | carta*agua*.pdf  → carta de agua
    """
    out: dict[str, Optional[Path]] = {
        "minuta": None, "imagen_minuta": None,
        "plano": None, "carta_agua": None,
    }
    if not carpeta_seg.exists():
        return out
    for f in carpeta_seg.iterdir():
        if not f.is_file() or not f.suffix.lower() == ".pdf":
            continue
        nombre = f.name.lower()
        stem = f.stem.lower()
        if "imagenminuta" in nombre or "imagen_minuta" in nombre or "imagen minuta" in nombre:
            out["imagen_minuta"] = f
        elif "minuta" in nombre and "imagen" not in nombre:
            out["minuta"] = f
        elif (nombre.startswith("plano") or "planof" in nombre or
              re.match(r"plano\d*f?\.pdf", nombre)):
            out["plano"] = f
        elif "agua" in nombre or "carta" in nombre:
            out["carta_agua"] = f
    return out


def combinar_pdfs_muni(
    *,
    minuta: Path,
    imagen_minuta: Optional[Path] = None,
    plano: Optional[Path] = None,
    carta_agua: Optional[Path] = None,
    salida: Path,
) -> dict:
    """Combina los PDFs en UN solo archivo, en el orden especificado.

    Args:
        minuta: PDF de la minuta CFIA (obligatorio — de él se saca el N° citas).
        imagen_minuta, plano, carta_agua: opcionales pero típicos.
        salida: ruta de salida del PDF combinado.

    Returns:
        {
            "pdf_path": Path al archivo final,
            "archivos_incluidos": list[str] nombres,
            "n_paginas_total": int,
        }
    """
    from pypdf import PdfReader, PdfWriter

    if not minuta.exists():
        raise FileNotFoundError(f"minuta no existe: {minuta}")

    writer = PdfWriter()
    incluidos: list[str] = []
    n_pag = 0

    orden = [
        ("minuta",        minuta),
        ("imagen_minuta", imagen_minuta),
        ("plano",         plano),
        ("carta_agua",    carta_agua),
    ]
    for etiqueta, pdf in orden:
        if pdf is None:
            continue
        if not pdf.exists():
            continue
        try:
            reader = PdfReader(str(pdf))
            for page in reader.pages:
                writer.add_page(page)
                n_pag += 1
            incluidos.append(f"{etiqueta}: {pdf.name}")
        except Exception as exc:
            raise RuntimeError(f"error leyendo {pdf}: {exc}") from exc

    salida.parent.mkdir(parents=True, exist_ok=True)
    with open(salida, "wb") as f:
        writer.write(f)
    return {
        "pdf_path":         salida,
        "archivos_incluidos": incluidos,
        "n_paginas_total":  n_pag,
    }


def armar_paquete_muni(
    *,
    expediente_id: str,
    db,
    sobrescribir: bool = False,
) -> dict:
    """Arma el paquete completo para UN expediente.

    Args:
        expediente_id: id (UUID) o numero_expediente del expediente.
        db: instancia de Database.
        sobrescribir: si False y el PDF ya existe, no se rehace.

    Returns:
        {
            "ok":               bool,
            "pdf_path":         Path al PDF combinado,
            "n_citas":          "YYYY - NNNNN - X",
            "archivos_incluidos": [...],
            "n_paginas_total":  int,
            "advertencias":     [...],
        }
    """
    import json

    exp = db.buscar_por_numero(expediente_id) if not _es_uuid(expediente_id) else \
          db.obtener_expediente(expediente_id)
    if not exp:
        return {"ok": False, "error": f"expediente {expediente_id!r} no existe"}

    meta = json.loads(exp.get("metadata_json") or "{}")
    path_carpeta = meta.get("path_carpeta")
    subcarpeta   = meta.get("subcarpeta_archivos", "01_Campo")
    if not path_carpeta:
        return {"ok": False, "error": "expediente sin path_carpeta en metadata"}

    carpeta_seg = Path(path_carpeta) / subcarpeta
    archivos = detectar_archivos_paquete(carpeta_seg)
    advertencias: list[str] = []

    if not archivos["minuta"]:
        return {"ok": False, "error": f"no se encontró minuta en {carpeta_seg}"}
    if not archivos["plano"]:
        advertencias.append("no se encontró plano firmado")
    if not archivos["imagen_minuta"]:
        advertencias.append("no se encontró imagen_minuta")
    if not archivos["carta_agua"]:
        advertencias.append("no se encontró carta de agua")

    n_citas = extraer_numero_citas(archivos["minuta"])
    if not n_citas:
        return {"ok": False, "error": f"no se pudo extraer N° citas de {archivos['minuta'].name}"}

    # Ruta de salida: 02_Oficina/<n_citas>.pdf
    salida = Path(path_carpeta) / "02_Oficina" / f"{n_citas}.pdf"
    if salida.exists() and not sobrescribir:
        return {
            "ok":             True,
            "pdf_path":       salida,
            "n_citas":        n_citas,
            "advertencias":   ["PDF ya existía — no se sobreescribió"],
            "archivos_incluidos": [],
            "n_paginas_total": 0,
        }

    res = combinar_pdfs_muni(
        minuta=archivos["minuta"],
        imagen_minuta=archivos["imagen_minuta"],
        plano=archivos["plano"],
        carta_agua=archivos["carta_agua"],
        salida=salida,
    )
    res.update({
        "ok":           True,
        "n_citas":      n_citas,
        "advertencias": advertencias,
    })
    return res


def _es_uuid(s: str) -> bool:
    return len(s) == 36 and s.count("-") == 4


__all__ = [
    "extraer_numero_citas",
    "detectar_archivos_paquete",
    "combinar_pdfs_muni",
    "armar_paquete_muni",
]
