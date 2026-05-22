"""Extractor de metadata del PDF del plano (cajetín inferior derecho).

Estrategia híbrida:
  1. Primero intenta `pypdf` (rápido, sin red). Si la mayoría de campos
     quedan vacíos, sube a:
  2. `anthropic` con vision sobre el PDF (cuando el cajetín está como
     gráfico vectorial / image overlay y pypdf no lo extrae).

Datos extraídos del cajetín del plano:
  - PROTOCOLO TOMO XXXXX
  - FOLIO XXX
  - NUMERO DE ENTERO XXXXXXXXX
  - ÁREA (m²)
  - IDENTIFICADOR PREDIAL
  - FOLIO REAL (Nº de finca)
  - DISTRITO / CANTÓN / PROVINCIA
  - FECHA (mes/año)
"""
from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger("catastro.plano_extractor")


@dataclass
class PlanoMetadata:
    """Datos extraídos del cajetín del plano. Todos opcionales."""
    protocolo_tomo:        str = ""
    protocolo_folio:       str = ""
    numero_entero:         str = ""
    area_m2:               str = ""       # área del NUEVO lote (lo que sale)
    area_segun_registro:   str = ""       # área de la finca MADRE
    identificador_predial: str = ""
    folio_real:            str = ""
    provincia:             str = ""
    canton:                str = ""
    distrito:              str = ""
    fecha:                 str = ""
    profesional_carne:     str = ""
    profesional_cedula:    str = ""
    profesional_nombre:    str = ""       # ej "LUIS ALONSO ROJAS HERRERA"
    modifica_plano:        str = ""       # ej "A-2025739-2018"
    vertices_a_via:        str = ""       # ej "1-15-16" (formato lista_guion)
    frente_calle_m:        str = ""       # ej "56.69"
    uso_descrito:          str = ""       # ej "USO MIXTO (AGRICOLA Y RESIDENCIAL)"
    extraido_via:          str = ""   # "pypdf" o "claude_vision"

    def as_dict(self) -> dict:
        return asdict(self)

    def campos_llenos(self) -> int:
        return sum(1 for k, v in asdict(self).items() if k != "extraido_via" and v)


# ── Regex (estrategia 1: pypdf) ────────────────────────────────────────────
#
# Patrones aprendidos en SEG-2026-005 (JAVSAL) + SEG-2026-006 (ROLESQ):
#  - El cajetín del plano viene como gráfico vectorial; pero el cuerpo del
#    plano (listado de coordenadas + notas) sí es texto. Aprovechar ese.
#  - "AREA NNNN.NNm2" (a veces "AREA\n7000.00 m²") aparece SUELTO en el
#    plano, antes del listado de coordenadas. Es el área del NUEVO lote.
#  - "AREA SEGUN REGISTRO NNNN.NNm²" es el área de la finca madre.
#  - "PROTOCOLO TOMO NNN FOLIO NNN" — sale en planos como ROLESQ.
#  - "Distancia Frente a Calle Pública Vértices N°X, N°Y, N°Z = NN.NN m"

_RE_PROTOCOLO_TOMO  = re.compile(r"PROTOCOLO\s*\n?\s*TOMO\s+(\d+)", re.IGNORECASE)
_RE_PROTOCOLO_FOLIO = re.compile(r"FOLIO\s+(\d+)\b", re.IGNORECASE)
# N° de entero: en el PDF del BCR hay header "Número de entero Boleta de
# seguridad Monto tasado..." y luego una fila con el número. El regex acepta
# hasta ~80 caracteres de "headers" entre "ENTERO" y los dígitos.
_RE_NUMERO_ENTERO   = re.compile(
    r"N[UÚ]MERO\s+DE\s+ENTERO[A-Za-z\s\n]{0,80}?(\d{9,})",
    re.IGNORECASE,
)
_RE_AREA_REGISTRO   = re.compile(
    # Acepta tanto "33,440 m²" como "15364.42m²" (sin separador de miles).
    r"AREA\s+SEGUN\s+REGISTRO\s*\n?\s*"
    r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*m\s*[²2]?",
    re.IGNORECASE,
)
# Área del nuevo lote: número con punto decimal seguido de "m²" o "m2",
# sin "REGISTRO" antes. El truco: pypdf concatena coordenadas + área, así
# el patrón tiene que aceptar números pegados a coordenadas con formato
# "X.YYY.ZZ NNNN.NN m²" — la última secuencia "dígitos . dos dígitos m²"
# es el área. Usamos boundary específico de área (al menos 4 dígitos enteros
# y exactamente 2 decimales, que es el formato APT estándar).
_RE_AREA_NUEVA      = re.compile(
    r"(?:^|[^\d.])(\d{1,7}\.\d{1,4})\s*m\s*[²2]\b",
    re.MULTILINE,
)
# IDENTIFICADOR PREDIAL: solo dígitos (sin letras pegadas al final como "ES")
_RE_ID_PREDIAL      = re.compile(
    r"IDENTIFICADOR\s+PREDIAL[\s\n:]+(\d{12,16})",
    re.IGNORECASE,
)
_RE_CARNE_IT        = re.compile(r"\b(I\.?T\.?\s*\d{4,6})\b", re.IGNORECASE)
_RE_MODIFICA        = re.compile(
    r"MODIFICA\s+PLANO\s+CATASTRADO\s*N?[°˚:]?\s*([A-Z0-9\-\s/]+?)(?=\s{2,}|\n|PARA|LA\s+MUN|$)",
    re.IGNORECASE,
)
_RE_VERTICES_VIA    = re.compile(
    r"V[eé]rtices?\s+N[°˚]?\s*(\d+)(?:\s*(?:al|,|y|N[°˚]?\s*\d+)\s*\d*)*"
    r"\s*=\s*(\d{1,3}(?:\.\d+)?)\s*m",
    re.IGNORECASE,
)
_RE_VERTICES_LISTA  = re.compile(
    r"V[eé]rtices?\s+([N°˚\d\s,yal\-]+?)\s*=\s*\d+(?:\.\d+)?\s*m",
    re.IGNORECASE,
)
# USO: capturar solo hasta keyword de cierre o letras+puntuación habitual.
# Stop a "Distancia", "MODIFICA", "INFORMACION", "DISTRITO", o cualquier dígito
# de coordenada/identificador (que ya no es texto de "uso").
_RE_USO_DESCRITO    = re.compile(
    r"(USO\s+(?:MIXTO|RESIDENCIAL|AGR[IÍ]COLA|URBANO|SOLAR|"
    r"CONSTRUIDO\s+Y\s+SOLAR)[A-ZÁÉÍÓÚÑ\s,\(\)]*?)"
    r"(?=Distancia|MODIFICA|INFORMACION|DISTRITO|\d|$)",
    re.IGNORECASE,
)
_RE_TOPOG_NOMBRE    = re.compile(
    r"PROFESIONAL\s+RESPONSABLE\s*\n?\s*([A-ZÑÁÉÍÓÚ\s]+?)\s*(?:TOPOGRAFO|TOP[oó]grafo|IT\b|I\.T)",
    re.IGNORECASE,
)


def _parsear_vertices_a_via(texto: str) -> tuple[str, str]:
    """Devuelve (lista_guion, frente_m) extraído del texto del plano.

    Ej "Vértices N°1, N°15 y N°16 = 56.69 m"   → ("1-15-16", "56.69")
       "Vértices N°1 al N°5 = 42.83 m"        → ("1-2-3-4-5", "42.83")
    """
    m = _RE_VERTICES_LISTA.search(texto)
    if not m:
        return ("", "")
    crudo = m.group(1).strip()
    frente = ""
    m2 = _RE_VERTICES_VIA.search(texto)
    if m2:
        frente = m2.group(2)
    # Extraer solo los números — puede haber "1, 15 y 16" o "1 al 5"
    if re.search(r"\bal\b", crudo, re.IGNORECASE):
        # Rango "X al Y" → expandir
        nums = re.findall(r"\d+", crudo)
        if len(nums) >= 2:
            a, b = int(nums[0]), int(nums[-1])
            return ("-".join(str(n) for n in range(a, b + 1)), frente)
    nums = re.findall(r"\d+", crudo)
    return ("-".join(nums), frente) if nums else ("", frente)


def _intentar_pypdf(pdf_path: Path) -> PlanoMetadata | None:
    """Estrategia 1 — texto plano de pypdf. Retorna None si no es viable."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    try:
        reader = PdfReader(str(pdf_path))
        texto = "\n".join((p.extract_text() or "") for p in reader.pages)
    except Exception as exc:
        log.warning("pypdf error en %s: %s", pdf_path.name, exc)
        return None

    md = PlanoMetadata(extraido_via="pypdf")

    # Protocolo TOMO + FOLIO
    if m := _RE_PROTOCOLO_TOMO.search(texto):
        md.protocolo_tomo = m.group(1).strip()
        # Buscar FOLIO en la cercanía (siguientes 80 chars)
        ventana = texto[m.end():m.end() + 80]
        if mf := _RE_PROTOCOLO_FOLIO.search(ventana):
            md.protocolo_folio = mf.group(1).strip()
    if m := _RE_NUMERO_ENTERO.search(texto):
        md.numero_entero = m.group(1).strip()

    # Áreas — registro madre (finca origen) y nuevo lote (lo que sale)
    if m := _RE_AREA_REGISTRO.search(texto):
        md.area_segun_registro = m.group(1).replace(",", "").strip()
    if m := _RE_AREA_NUEVA.search(texto):
        # Evitar matchear el mismo número de AREA SEGUN REGISTRO
        candidato = m.group(1).replace(",", "").strip()
        if candidato != md.area_segun_registro:
            md.area_m2 = candidato

    if m := _RE_ID_PREDIAL.search(texto):
        md.identificador_predial = m.group(1).strip()
    if m := _RE_CARNE_IT.search(texto):
        # Normalizar "I.T 10676" / "IT10676" / "IT 10676" → "IT-10676"
        raw = m.group(1).upper().replace(".", "").replace(" ", "")
        if raw.startswith("IT") and len(raw) > 2:
            md.profesional_carne = f"IT-{raw[2:]}"
        else:
            md.profesional_carne = raw

    # Topógrafo, modifica plano, vertices a vía — extras aprendidos con
    # JAVSAL/ROLESQ. Útil para minimizar dependencia de Vision.
    if m := _RE_TOPOG_NOMBRE.search(texto):
        md.profesional_nombre = " ".join(m.group(1).split()).strip()
    if m := _RE_MODIFICA.search(texto):
        md.modifica_plano = " ".join(m.group(1).split()).strip()
    lista, frente = _parsear_vertices_a_via(texto)
    if lista:
        md.vertices_a_via = lista
    if frente:
        md.frente_calle_m = frente
    if m := _RE_USO_DESCRITO.search(texto):
        md.uso_descrito = " ".join(m.group(1).split()).strip()
    return md


# ── Estrategia 2: Claude vision sobre el PDF ──────────────────────────────

_VISION_PROMPT = """Eres un asistente que extrae datos del cajetín (esquina inferior derecha) de planos catastrales de Costa Rica.

Devuelve un JSON con EXACTAMENTE estos campos (string vacío si no encuentras el dato — NO inventes):

{
  "protocolo_tomo":        "número del TOMO del protocolo (ej: 24162)",
  "protocolo_folio":       "número del FOLIO del protocolo (ej: 098)",
  "numero_entero":         "NÚMERO DE ENTERO (BCR), suele ser 9 dígitos (ej: 660149974)",
  "area_m2":               "área en m² (solo número, sin coma de miles, ej: 20966.88)",
  "identificador_predial": "IDENTIFICADOR PREDIAL (ej: 20205P00205000)",
  "folio_real":            "Nº de finca / Folio Real (ej: 2166320-000)",
  "provincia":             "nombre de provincia en mayúsculas (ej: ALAJUELA)",
  "canton":                "nombre del cantón en mayúsculas (ej: SAN RAMON)",
  "distrito":              "nombre del distrito en mayúsculas (ej: PIEDADES SUR)",
  "fecha":                 "fecha del cajetín tal como aparece (ej: ABRIL / 2026)",
  "profesional_carne":     "carné del profesional (ej: IT10676)",
  "profesional_cedula":    "cédula del profesional (ej: 02-0530-0432)"
}

Devuelve SOLO el JSON, sin explicaciones ni markdown."""


def _intentar_claude_vision(pdf_path: Path, anthropic_api_key: str) -> PlanoMetadata | None:
    """Estrategia 2 — Claude vision sobre el PDF completo."""
    try:
        import anthropic
    except ImportError:
        log.warning("anthropic no instalado — no se puede usar Claude vision")
        return None

    try:
        pdf_bytes = pdf_path.read_bytes()
        pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")
    except Exception as exc:
        log.warning("error leyendo PDF para vision: %s", exc)
        return None

    try:
        client = anthropic.Anthropic(api_key=anthropic_api_key)
        response = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=2000,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": pdf_b64,
                            },
                        },
                        {"type": "text", "text": _VISION_PROMPT},
                    ],
                }
            ],
        )
        # Extraer JSON del response
        raw_text = ""
        for block in response.content:
            if hasattr(block, "text"):
                raw_text += block.text

        # Limpiar fences ```json ... ```
        raw_text = raw_text.strip()
        if raw_text.startswith("```"):
            raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
            raw_text = re.sub(r"\s*```\s*$", "", raw_text)

        data = json.loads(raw_text)
        md = PlanoMetadata(extraido_via="claude_vision")
        for k in asdict(md):
            if k in data and isinstance(data[k], str):
                setattr(md, k, data[k].strip())
        return md
    except Exception as exc:
        log.warning("Claude vision error: %s", exc)
        return None


# ── Punto de entrada ───────────────────────────────────────────────────────

def extraer_plano_metadata(
    pdf_path: Path | str,
    anthropic_api_key: str | None = None,
    *,
    min_campos_pypdf: int = 4,
) -> PlanoMetadata:
    """Extrae metadata del PDF del plano.

    Intenta pypdf primero. Si los campos llenos < min_campos_pypdf y se
    proveyó api_key, escala a Claude vision.

    Args:
        pdf_path: ruta al plano PDF.
        anthropic_api_key: si se da y pypdf falla, usa Claude.
        min_campos_pypdf: umbral de campos para no escalar a vision (default 4).

    Returns:
        PlanoMetadata con `extraido_via` indicando la estrategia usada.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        log.warning("plano PDF no existe: %s", pdf_path)
        return PlanoMetadata()

    md = _intentar_pypdf(pdf_path) or PlanoMetadata()
    if md.campos_llenos() >= min_campos_pypdf:
        log.info("plano metadata via pypdf (%d campos): %s", md.campos_llenos(), pdf_path.name)
        return md

    if not anthropic_api_key:
        log.info(
            "plano metadata via pypdf incompleta (%d campos) y no hay API key — devolviendo parcial",
            md.campos_llenos(),
        )
        return md

    md_v = _intentar_claude_vision(pdf_path, anthropic_api_key)
    if md_v and md_v.campos_llenos() > md.campos_llenos():
        log.info("plano metadata via Claude vision (%d campos): %s", md_v.campos_llenos(), pdf_path.name)
        return md_v
    return md


__all__ = ["PlanoMetadata", "extraer_plano_metadata"]
