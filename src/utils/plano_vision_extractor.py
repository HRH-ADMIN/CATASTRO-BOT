"""Extractor completo via Claude Vision para los 3 documentos de un plano APT.

Extrae de los archivos del expediente:
  1. `plano.pdf` o `planof.pdf`     → cajetín + listado de coordenadas
  2. `informacion de registro.png`  → propietario + finca + naturaleza + plano previo
  3. `entero.pdf`                   → número, fecha, timbres, monto tasado

Y arma un `datos_apt` listo para inyectar en metadata.json del expediente.

Diseño:
  - Cada extractor (`extract_cajetin`, `extract_registro`, `extract_entero`)
    hace UN solo call a Anthropic con un prompt específico.
  - `build_datos_apt()` combina los 3 + aplica reglas de oficina (mapeo
    naturaleza → tipo_uso, zona urbana/rural según área, código de
    ubicación, centroide areal, honorarios decreto 17481).
  - Lo que el modelo dudó queda en `confianza_baja`; lo que el operador
    debe verificar manualmente queda en `advertencias`.

Modelo: claude-sonnet-4-5 (vision, JSON-only).
"""
from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger("catastro.vision_extractor")


# ───────────────────── Estructuras de datos ─────────────────────

@dataclass
class CajetinData:
    """Datos extraídos del cajetín (esquina inferior derecha del plano)."""
    descripcion:        str = ""    # "ROVUELT(1)", "FELIPETIOS(4)", etc.
    protocolo_tomo:     str = ""    # "23549"
    protocolo_folio:    str = ""    # "178"
    numero_entero:      str = ""    # "660822113"
    area_real:          str = ""    # del cajetín
    area_registro:      str = ""
    identificador_predial: str = ""
    folio_real:         str = ""
    provincia_nombre:   str = ""    # "ALAJUELA"
    canton_nombre:      str = ""
    distrito_nombre:    str = ""
    fecha:              str = ""
    profesional_carne:  str = ""
    profesional_nombre: str = ""
    # "MODIFICA PLANOS CATASTRADOS N° A-1095215-2006" → [{"letra":"A","numero":"1095215","anno":"2006"}]
    planos_modificar:   list[dict] = field(default_factory=list)
    # Listado de coordenadas (tuplas este, norte) — para centroide y vertices
    coordenadas:        list[list[float]] = field(default_factory=list)


@dataclass
class RegistroData:
    """Datos extraídos de la información de registro RNP."""
    finca:              str = ""    # "422958"
    derecho:            str = "000"
    duplicado:          str = ""    # "HORIZONTAL", "" si none
    provincia_finca:    str = ""    # "ALAJUELA"
    canton_finca:       str = ""    # "SAN RAMON"
    distrito_finca:     str = ""    # "ALFARO"
    tipo_propietario:   str = ""    # "FISICA" o "JURIDICA"
    cedula_propietario: str = ""    # "2-0466-0095" o "3-101-044683"
    cedula_registro_original: str = ""  # si está incompleta o errónea en registro
    nombre_propietario: str = ""    # "LUIS EMILIO DE LOS ANGELES"
    apellido1_propietario: str = "" # "PIÑEIRO"
    apellido2_propietario: str = "" # "CASTRO"
    naturaleza:         str = ""    # "TERRENO DE CAFE"
    area_registro_m2:   str = ""    # "741.68"
    plano_previo:       str = ""    # "A-1095215-2006"
    es_parte_de:        bool = False  # "ES PARTE DE" en el registro → segregación
    anotaciones:        bool = False
    gravamenes:         bool = False
    valor_fiscal:       str = ""


@dataclass
class EnteroData:
    """Datos del comprobante BCR de pago de tasación."""
    numero:           str = ""    # "660822113"
    fecha:            str = ""    # ISO "2026-05-08"
    monto_tasado:     str = ""    # total sin descuento
    monto_pagado:     str = ""    # total con descuento (debitado)
    timbre_cfia:      str = ""    # 038 monto total
    timbre_registro:  str = ""    # 001 monto total
    timbre_cit_ntrip: str = ""    # 055 monto


# ───────────────────── Prompts ─────────────────────

_PROMPT_CAJETIN = """Eres un asistente que extrae datos del cajetín y del listado de coordenadas de un plano catastral de Costa Rica.

Devuelve un JSON con EXACTAMENTE estos campos (string vacío "" si no encuentras el dato — NO inventes):

{
  "descripcion":        "abreviatura del cajetín (ej: 'ROVUELT(1)', 'FELIPETIOS(4)')",
  "protocolo_tomo":     "número TOMO del protocolo (ej: '23549')",
  "protocolo_folio":    "número FOLIO del protocolo (ej: '178')",
  "numero_entero":      "NÚMERO DE ENTERO (BCR), 9 dígitos (ej: '660822113')",
  "area_real":          "AREA del cajetín en m² SIN m² y SIN coma de miles (ej: '582.67')",
  "area_registro":      "AREA SEGUN REGISTRO en m² (ej: '741.68'). Vacío si no aparece",
  "identificador_predial": "IDENTIFICADOR PREDIAL (ej: '20209042295800')",
  "folio_real":         "Nº de finca / FOLIO REAL (ej: '2422958-000' o '115747-000')",
  "provincia_nombre":   "provincia en mayúsculas (ej: 'ALAJUELA')",
  "canton_nombre":      "cantón en mayúsculas (ej: 'SAN RAMON')",
  "distrito_nombre":    "distrito en mayúsculas (ej: 'ALFARO')",
  "fecha":              "fecha del cajetín tal como aparece (ej: 'MAYO / 2026')",
  "profesional_carne":  "carné del profesional (ej: 'IT10676')",
  "profesional_nombre": "nombre del profesional (ej: 'LUIS ALONSO ROJAS HERRERA')",
  "planos_modificar":   [
    {"letra": "A", "numero": "1095215", "anno": "2006"}
    // Lista de planos que este modifica. Texto típico: "MODIFICA PLANOS CATASTRADOS N° A-1095215-2006"
    // Si NO hay frase de modificación → lista vacía []
  ],
  "coordenadas": [
    [451646.32, 1115438.57],
    // Lista [ESTE, NORTE] de TODOS los vértices del LISTADO DE COORDENADAS del plano.
    // El listado suele estar en una tabla pequeña con columnas VERTICE | ESTE (m) | NORTE (m).
    // Devuélvelos en el MISMO ORDEN que aparecen, máximo 200 vértices.
    // Si no encuentras el listado tabular, devuelve [].
  ]
}

Devuelve SOLO el JSON, sin explicaciones ni markdown."""


_PROMPT_REGISTRO = """Eres un asistente que extrae datos de la 'Información de registro' (Registro Nacional de Costa Rica) de una finca.

Devuelve un JSON con EXACTAMENTE estos campos (string vacío "" si no aparece — NO inventes):

{
  "finca":              "número de finca SIN provincia, sin ceros a la izquierda (ej: '422958' o '115747')",
  "derecho":            "número de derecho (ej: '000')",
  "duplicado":          "valor de DUPLICADO ('HORIZONTAL', '0', letra, o vacío)",
  "provincia_finca":    "provincia ubicación de la finca (ej: 'ALAJUELA')",
  "canton_finca":       "cantón (ej: 'SAN RAMON')",
  "distrito_finca":     "distrito (ej: 'ALFARO')",
  "tipo_propietario":   "'FISICA' o 'JURIDICA'",
  "cedula_propietario": "cédula del propietario (ej: '2-0466-0095' para físicas, '3-101-044683' para jurídicas).\\nIMPORTANTE: si la cédula aparece TRUNCADA o INCOMPLETA en el registro (por ejemplo '3-101-' sin dígitos finales), pon AQUÍ exactamente lo que ves, sin completar.",
  "cedula_registro_original": "si la cédula está claramente truncada/incompleta en el registro (ej: '3-101-' sin dígitos finales después del segundo guión), copia AQUÍ el valor tal como aparece. Si la cédula está completa, deja string vacío.",
  "nombre_propietario": "PRIMER NOMBRE(S) del propietario, sin apellidos (ej: 'LUIS EMILIO DE LOS ANGELES' o 'OMAR')",
  "apellido1_propietario": "PRIMER APELLIDO (ej: 'PIÑEIRO' — con Ñ si corresponde). Si el registro tiene corrupción tipo 'PI?EIRO', escribe igual el que crees correcto.",
  "apellido2_propietario": "SEGUNDO APELLIDO (ej: 'CASTRO')",
  "naturaleza":         "texto NATURALEZA del registro (ej: 'TERRENO DE CAFE EN PRODUCCION CON UNA CASA')",
  "area_registro_m2":   "MIDE en m² (ej: '741.68'). Si está en letras (ej: 'SETECIENTOS CUARENTA Y UNO...'), convierte a número.",
  "plano_previo":       "PLANO citado en el registro (ej: 'A-1095215-2006'). Vacío si dice 'NO SE INDICA'.",
  "es_parte_de":        true,   // true si el registro dice "ES PARTE DE" (señal de segregación). false si no.
  "anotaciones":        true,   // true si dice "ANOTACIONES SOBRE LA FINCA: SI HAY"
  "gravamenes":         true,   // true si dice "GRAVAMENES o AFECTACIONES: SI HAY"
  "valor_fiscal":       "VALOR FISCAL en colones (ej: '35000.00'). Vacío si no aparece."
}

Devuelve SOLO el JSON, sin explicaciones ni markdown."""


_PROMPT_ENTERO = """Eres un asistente que extrae datos del comprobante de pago de tasación del Banco de Costa Rica (BCR) para inscripción de plano en Catastro Nacional.

Devuelve un JSON con EXACTAMENTE estos campos (string vacío "" si no aparece):

{
  "numero":           "Número de entero (ej: '660822113')",
  "fecha":            "fecha del pago en formato ISO 'YYYY-MM-DD' (ej: '2026-05-08' — si el comprobante dice 'BCR 08/05/2026')",
  "monto_tasado":     "MONTO TASADO (total sin descuento) en colones, sin signo ₡ y sin coma de miles (ej: '11940.00')",
  "monto_pagado":     "MONTO DEBITADO (con descuento aplicado, lo que realmente se pagó) (ej: '11241.60')",
  "timbre_cfia":      "Monto TOTAL del timbre '038 TIMBRE R.R.P.CFIA' SIN descuento (ej: '1600')",
  "timbre_registro":  "Monto TOTAL del timbre '001 TIMBRE REGISTRO NACIONAL' SIN descuento (ej: '10000' o '55000')",
  "timbre_cit_ntrip": "Monto del timbre '055 CIT-NTRIP' (ej: '300')"
}

Devuelve SOLO el JSON, sin explicaciones ni markdown."""


# ───────────────────── Cliente Anthropic ─────────────────────

class PlanoVisionExtractor:
    """Extractor unificado. Reutiliza un solo cliente Anthropic para los 3 docs."""

    DEFAULT_MODEL = "claude-sonnet-4-5"

    def __init__(self, api_key: str, model: str = DEFAULT_MODEL):
        import anthropic  # noqa: PLC0415
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._log = log

    # --- Helpers para mandar el doc + parsear JSON ---

    def _call_vision(self, *, prompt: str, doc_bytes: bytes,
                     media_type: str = "application/pdf") -> dict:
        """Manda el documento + prompt al modelo y parsea JSON.

        Returns dict (puede ser vacío si parsing falló).
        """
        b64 = base64.standard_b64encode(doc_bytes).decode("utf-8")
        kind = "document" if media_type == "application/pdf" else "image"
        source = {"type": "base64", "media_type": media_type, "data": b64}
        try:
            resp = self._client.messages.create(
                model=self._model,
                max_tokens=8000,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": kind, "source": source},
                        {"type": "text", "text": prompt},
                    ],
                }],
            )
        except Exception as exc:
            self._log.warning("Anthropic API error: %s", exc)
            return {}
        # Concatenar texto
        raw = ""
        for block in resp.content:
            if hasattr(block, "text"):
                raw += block.text
        raw = raw.strip()
        # Quitar fences ``` json ... ```
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```\s*$", "", raw)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            self._log.warning("JSON parse error: %s\n---raw---\n%s", exc, raw[:500])
            return {}

    # --- Extractores por documento ---

    def extract_cajetin(self, pdf_path: Path) -> CajetinData:
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            self._log.warning("plano PDF no existe: %s", pdf_path)
            return CajetinData()
        data = self._call_vision(
            prompt=_PROMPT_CAJETIN,
            doc_bytes=pdf_path.read_bytes(),
            media_type="application/pdf",
        )
        return _build_dataclass(CajetinData, data)

    def extract_registro(self, img_path: Path) -> RegistroData:
        img_path = Path(img_path)
        if not img_path.exists():
            self._log.warning("registro img no existe: %s", img_path)
            return RegistroData()
        # Detectar media_type por extensión (incluye PDF: la oficina suele
        # descargar la consulta de registro como PDF, no solo PNG)
        ext = img_path.suffix.lower().lstrip(".")
        media = {
            "png":  "image/png",
            "jpg":  "image/jpeg",
            "jpeg": "image/jpeg",
            "pdf":  "application/pdf",
        }.get(ext, "image/png")
        data = self._call_vision(
            prompt=_PROMPT_REGISTRO,
            doc_bytes=img_path.read_bytes(),
            media_type=media,
        )
        return _build_dataclass(RegistroData, data)

    def extract_entero(self, pdf_path: Path) -> EnteroData:
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            self._log.warning("entero PDF no existe: %s", pdf_path)
            return EnteroData()
        data = self._call_vision(
            prompt=_PROMPT_ENTERO,
            doc_bytes=pdf_path.read_bytes(),
            media_type="application/pdf",
        )
        return _build_dataclass(EnteroData, data)


def _build_dataclass(cls, data: dict):
    """Crea instancia de dataclass tolerando campos extra / faltantes."""
    if not data:
        return cls()
    kwargs = {}
    fields_names = {f.name for f in cls.__dataclass_fields__.values()}
    for k, v in data.items():
        if k in fields_names:
            kwargs[k] = v
    try:
        return cls(**kwargs)
    except TypeError as e:
        log.warning("error armando %s: %s", cls.__name__, e)
        return cls()


def extract_cajetin_con_fallback(
    pdf_path: Path | str,
    api_key: Optional[str] = None,
    *,
    model: str = PlanoVisionExtractor.DEFAULT_MODEL,
) -> CajetinData:
    """Extrae datos del cajetín con fallback a pypdf+regex si Vision falla.

    Estrategia:
      1. Si hay `api_key`, intenta Vision (devuelve TODOS los campos
         incluyendo descripción, planos_modificar y coordenadas).
      2. Si Vision falla / devolvió poco, complementa con pypdf+regex
         del módulo `plano_pdf_extractor` (fields parciales — sin
         coordenadas/descripción/planos_modificar).
      3. Si no hay API key, salta directo a pypdf.

    Devuelve CajetinData con `extraido_via` indicando qué método se usó.
    """
    pdf_path = Path(pdf_path)
    cajetin = CajetinData()

    # Estrategia 1: Vision (si hay API key)
    if api_key:
        try:
            ex = PlanoVisionExtractor(api_key=api_key, model=model)
            cajetin = ex.extract_cajetin(pdf_path)
            # Si Vision devolvió la mayoría de los campos, listo
            campos_llenos = sum(
                1 for k in ("descripcion", "protocolo_tomo", "numero_entero",
                            "area_real", "folio_real")
                if getattr(cajetin, k, "")
            )
            if campos_llenos >= 3:
                return cajetin
            log.info("Vision devolvió pocos campos (%d) — complementando con pypdf",
                     campos_llenos)
        except Exception as exc:
            log.warning("Vision falló: %s — usando pypdf", exc)

    # Estrategia 2: fallback pypdf+regex
    try:
        from src.utils.plano_pdf_extractor import extraer_plano_metadata
        md = extraer_plano_metadata(pdf_path, anthropic_api_key=None)
        # Mergear lo que pypdf encontró sin sobrescribir lo de Vision
        mapping = [
            ("protocolo_tomo",        "protocolo_tomo"),
            ("protocolo_folio",       "protocolo_folio"),
            ("numero_entero",         "numero_entero"),
            ("area_m2",               "area_real"),  # nombres distintos
            ("identificador_predial", "identificador_predial"),
            ("folio_real",            "folio_real"),
            ("provincia",             "provincia_nombre"),
            ("canton",                "canton_nombre"),
            ("distrito",              "distrito_nombre"),
            ("fecha",                 "fecha"),
            ("profesional_carne",     "profesional_carne"),
        ]
        for pypdf_attr, vision_attr in mapping:
            pypdf_val = getattr(md, pypdf_attr, "")
            if pypdf_val and not getattr(cajetin, vision_attr, ""):
                setattr(cajetin, vision_attr, pypdf_val)
    except Exception as exc:
        log.warning("pypdf fallback también falló: %s", exc)

    return cajetin


__all__ = [
    "PlanoVisionExtractor",
    "CajetinData", "RegistroData", "EnteroData",
    "extract_cajetin_con_fallback",
]
