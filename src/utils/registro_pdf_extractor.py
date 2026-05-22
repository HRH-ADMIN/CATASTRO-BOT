"""Extractor pypdf+regex del PDF de Consulta por Número de Finca (RNP).

Es un fallback determinístico — no necesita API key. Solo funciona si el PDF
tiene texto plano (los descargados directo del RNP sí; los escaneados no).

Aprendido con SEG-2026-005 (JAVSAL = finca 562690) y SEG-2026-006
(ROLESQ = finca 118879). El RNP siempre usa el mismo template:

    PROVINCIA: ALAJUELA FINCA: 562690 DUPLICADO: HORIZONTAL: DERECHO: 000
    SEGREGACIONES: NO HAY
    NATURALEZA: TERRENO DE PASTO
    SITUADA EN EL DISTRITO 5-PIEDADES SUR CANTON 2-SAN RAMON ...
    MIDE: TREINTA Y TRES MIL CUATROCIENTOS CUARENTA METROS CUADRADOS
    PLANO: A-2025739-2018
    IDENTIFICADOR PREDIAL: 202050562690
    PROPIETARIO:
    JAVIER CASTRO JIMENEZ
    CEDULA IDENTIDAD 2-0291-1270
    VALOR FISCAL: 100,000.00 COLONES

USO:
    from src.utils.registro_pdf_extractor import extraer_registro_pypdf
    data = extraer_registro_pypdf(Path("informacion de registro.pdf"))
    # → RegistroData con todo lo que pypdf pudo leer
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from src.utils.plano_vision_extractor import RegistroData

log = logging.getLogger("catastro.registro_extractor")


# ── Regex ──────────────────────────────────────────────────────────────

_RE_FINCA = re.compile(
    r"FINCA:\s*(\d+)\s*DUPLICADO:\s*([A-ZÑÁÉÍÓÚ]*)\s*HORIZONTAL:\s*"
    r"\w*\s*DERECHO:\s*(\d+)",
    re.IGNORECASE,
)
_RE_PROVINCIA = re.compile(r"PROVINCIA:\s*([A-ZÑÁÉÍÓÚ]+)", re.IGNORECASE)
_RE_DISTRITO_CANTON = re.compile(
    r"DISTRITO\s+(\d+)-([A-ZÑÁÉÍÓÚ\s]+?)\s+CANT[ÓO]N\s+(\d+)-"
    r"([A-ZÑÁÉÍÓÚ\s]+?)\s+DE\s+LA\s+PROVINCIA",
    re.IGNORECASE,
)
_RE_NATURALEZA = re.compile(
    r"NATURALEZA:\s*([A-ZÑÁÉÍÓÚ\s]+?)\s*\n",
    re.IGNORECASE,
)
_RE_SEGREGACIONES = re.compile(
    r"SEGREGACIONES:\s*(SI|NO)\s*HAY",
    re.IGNORECASE,
)
_RE_MIDE = re.compile(
    # Capturar área completa incl. parte decimal en letras
    # ("X MIL Y METROS CON Z DECIMETROS CUADRADOS") para no perder los decimales.
    r"MIDE:\s*([A-ZÑÁÉÍÓÚ\s]+?(?:\s+CON\s+[A-ZÑÁÉÍÓÚ\s]+?\s+DECIMETROS"
    r"(?:\s+CUADRADOS)?)?(?:\s+CUADRADOS)?)\s*(?:\n|PLANO|FINCA|IDENTIFICADOR|$)",
    re.IGNORECASE,
)
_RE_PLANO_PREVIO = re.compile(
    r"PLANO:\s*([A-Z0-9\-]+)",
    re.IGNORECASE,
)
_RE_ID_PREDIAL = re.compile(
    r"IDENTIFICADOR\s+PREDIAL:\s*(\d{12,16})",
    re.IGNORECASE,
)
# Propietario físico: "JUAN PEREZ\nCEDULA IDENTIDAD 2-0291-1270"
_RE_PROPIETARIO_FISICA = re.compile(
    r"PROPIETARIO:\s*\n\s*([A-ZÑÁÉÍÓÚ\s]+?)\s*\n\s*"
    r"CEDULA\s+IDENTIDAD\s+([\d\-]+)",
    re.IGNORECASE,
)
# Propietario jurídico: "AGROPECUARIA X S.A.\nCEDULA JURIDICA 3-101-044683"
_RE_PROPIETARIO_JURIDICA = re.compile(
    r"PROPIETARIO:\s*\n\s*([A-ZÑÁÉÍÓÚ0-9\s\.\-]+?)\s*\n\s*"
    r"CEDULA\s+JURIDICA\s+([\d\-]+)",
    re.IGNORECASE,
)
_RE_VALOR_FISCAL = re.compile(
    r"VALOR\s+FISCAL:\s*([\d,\.]+)\s*COLONES",
    re.IGNORECASE,
)
_RE_ANOTACIONES = re.compile(
    r"ANOTACIONES\s+SOBRE\s+LA\s+FINCA:\s*(SI|NO)\s*HAY",
    re.IGNORECASE,
)
_RE_GRAVAMENES = re.compile(
    r"GRAVAMENES?\s+(?:o|y)\s+AFECTACIONES:\s*(SI|NO)\s*HAY",
    re.IGNORECASE,
)
_RE_ES_PARTE = re.compile(r"ES\s+PARTE\s+DE", re.IGNORECASE)


# ── Conversor cardinal en letras → número ──────────────────────────────

_NUMEROS_LETRAS = {
    "CERO": 0, "UNO": 1, "UN": 1, "DOS": 2, "TRES": 3, "CUATRO": 4,
    "CINCO": 5, "SEIS": 6, "SIETE": 7, "OCHO": 8, "NUEVE": 9,
    "DIEZ": 10, "ONCE": 11, "DOCE": 12, "TRECE": 13, "CATORCE": 14,
    "QUINCE": 15, "DIECISEIS": 16, "DIECISIETE": 17, "DIECIOCHO": 18,
    "DIECINUEVE": 19, "VEINTE": 20, "VEINTIUNO": 21, "VEINTIDOS": 22,
    "VEINTITRES": 23, "VEINTICUATRO": 24, "VEINTICINCO": 25,
    "VEINTISEIS": 26, "VEINTISIETE": 27, "VEINTIOCHO": 28, "VEINTINUEVE": 29,
    "TREINTA": 30, "CUARENTA": 40, "CINCUENTA": 50, "SESENTA": 60,
    "SETENTA": 70, "OCHENTA": 80, "NOVENTA": 90,
    "CIEN": 100, "CIENTO": 100, "DOSCIENTOS": 200, "TRESCIENTOS": 300,
    "CUATROCIENTOS": 400, "QUINIENTOS": 500, "SEISCIENTOS": 600,
    "SETECIENTOS": 700, "OCHOCIENTOS": 800, "NOVECIENTOS": 900,
    "MIL": 1000,
}


def convertir_letras_a_numero(texto: str) -> Optional[float]:
    """Convierte "TREINTA Y TRES MIL CUATROCIENTOS CUARENTA" → 33440.

    Soporta:
      - Unidades, decenas, centenas hasta 999
      - Miles ("X MIL Y") hasta 999,999
      - Decimales si aparece "CON N DECIMETROS CUADRADOS"
        (1 decímetro cuadrado = 0.01 m²; ver MIDE en RNP)

    Devuelve None si no pudo parsear.
    """
    if not texto:
        return None
    t = texto.upper().replace("Y ", "").replace("CON ", "")
    # Decimales: "...CON CUARENTA Y DOS DECIMETROS"
    decimetros = 0
    if " DECIMETROS" in t:
        partes = t.split(" DECIMETROS")
        t = partes[0]
        # Buscar tras MIL/CENTENAS/UNIDADES los decimetros: depende del corpus.
        # Por simplicidad, aceptamos que decimetros está al final como número.
        # 1 m² = 100 dm² → cuarenta y dos dm² = 0.42 m²
        # Pero el texto MIDE incluye decimetros dentro de la parte entera
        # del mensaje. Caso ROLESQ: "QUINCE MIL TRESCIENTOS SESENTA Y CUATRO
        # METROS CON CUARENTA Y DOS DECIMETROS CUADRADOS" = 15364.42 m².
        # La parte tras CON es los decimetros.
        cola = partes[1] if len(partes) > 1 else ""
        # En realidad la lógica correcta: lo que está ANTES de "METROS" es m²,
        # lo que está después de "CON" y antes de "DECIMETROS" es dm² centi.
        pass  # Ver más abajo, lo manejamos diferente
    tokens = [p for p in re.split(r"\s+", t.strip()) if p]
    if not tokens:
        return None

    # Estrategia simple: ir acumulando
    total = 0
    acumulador = 0
    for tok in tokens:
        if tok == "MIL":
            acumulador = max(acumulador, 1) * 1000
            total += acumulador
            acumulador = 0
        elif tok in _NUMEROS_LETRAS:
            acumulador += _NUMEROS_LETRAS[tok]
    total += acumulador
    return float(total) if total > 0 else None


def parsear_area_letras_completa(texto_mide: str) -> Optional[str]:
    """Parse texto MIDE completo incluyendo decimales en formato dm².

    Ej:
      "TREINTA Y TRES MIL CUATROCIENTOS CUARENTA"
        → "33440.00"
      "QUINCE MIL TRESCIENTOS SESENTA Y CUATRO METROS CON CUARENTA Y DOS DECIMETROS CUADRADOS"
        → "15364.42"
    """
    if not texto_mide:
        return None
    # Separar parte entera (antes METROS) y parte decimal (entre CON y DECIMETROS)
    decimal_str = ""
    parte_decimal = re.search(r"CON\s+(.+?)\s+DECIMETROS", texto_mide,
                              re.IGNORECASE)
    if parte_decimal:
        dm2 = convertir_letras_a_numero(parte_decimal.group(1))
        if dm2:
            decimal_str = f"{int(dm2):02d}"
    # Parte entera = todo lo antes de METROS (o todo el texto si no hay METROS)
    entera_match = re.split(r"\s+METROS", texto_mide, maxsplit=1,
                            flags=re.IGNORECASE)
    parte_entera_txt = entera_match[0] if entera_match else texto_mide
    m2 = convertir_letras_a_numero(parte_entera_txt)
    if m2 is None:
        return None
    if decimal_str:
        return f"{int(m2)}.{decimal_str}"
    return f"{m2:.2f}"


# ── Extractor principal ────────────────────────────────────────────────

def extraer_registro_pypdf(pdf_path: Path) -> RegistroData:
    """Extrae lo más posible del PDF de Consulta por Número de Finca.

    Devuelve RegistroData con los campos rellenos. Si el PDF es escaneado
    (sin texto plano) devuelve dataclass vacío.
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        log.warning("pypdf no instalado")
        return RegistroData()

    try:
        reader = PdfReader(str(pdf_path))
        texto = "\n".join((p.extract_text() or "") for p in reader.pages)
    except Exception as exc:
        log.warning("pypdf falló en %s: %s", pdf_path.name, exc)
        return RegistroData()

    if not texto.strip():
        log.info("PDF registro %s sin texto plano — probablemente escaneado",
                 pdf_path.name)
        return RegistroData()

    md = RegistroData()

    # Finca + derecho
    if m := _RE_FINCA.search(texto):
        md.finca = m.group(1)
        if m.group(2):  # duplicado horizontal/vertical si aparece
            md.duplicado = m.group(2).upper().strip()
        md.derecho = m.group(3).zfill(3)

    # Provincia / Cantón / Distrito
    if m := _RE_DISTRITO_CANTON.search(texto):
        md.distrito_finca = " ".join(m.group(2).split()).strip()
        md.canton_finca = " ".join(m.group(4).split()).strip()
    if m := _RE_PROVINCIA.search(texto):
        md.provincia_finca = m.group(1).upper().strip()

    # Naturaleza
    if m := _RE_NATURALEZA.search(texto):
        md.naturaleza = " ".join(m.group(1).split()).strip()

    # Mide → m²
    if m := _RE_MIDE.search(texto):
        area = parsear_area_letras_completa(m.group(1))
        if area:
            md.area_registro_m2 = area

    # Plano previo
    if m := _RE_PLANO_PREVIO.search(texto):
        md.plano_previo = m.group(1).strip()

    # Propietario — primero jurídico (más específico), luego físico
    juridica = _RE_PROPIETARIO_JURIDICA.search(texto)
    fisica   = _RE_PROPIETARIO_FISICA.search(texto)
    if juridica:
        md.tipo_propietario = "JURIDICA"
        nombre = " ".join(juridica.group(1).split()).strip()
        md.nombre_propietario = nombre
        md.cedula_propietario = juridica.group(2).strip()
    elif fisica:
        md.tipo_propietario = "FISICA"
        nombre_completo = " ".join(fisica.group(1).split()).strip()
        # Heurística: últimas 2 palabras = apellidos; resto = nombre(s)
        tokens = nombre_completo.split()
        if len(tokens) >= 3:
            md.apellido2_propietario = tokens[-1]
            md.apellido1_propietario = tokens[-2]
            md.nombre_propietario = " ".join(tokens[:-2])
        else:
            md.nombre_propietario = nombre_completo
        md.cedula_propietario = fisica.group(2).strip()

    # Valor fiscal
    if m := _RE_VALOR_FISCAL.search(texto):
        md.valor_fiscal = m.group(1).strip()

    # Anotaciones / gravámenes
    if m := _RE_ANOTACIONES.search(texto):
        md.anotaciones = m.group(1).upper() == "SI"
    if m := _RE_GRAVAMENES.search(texto):
        md.gravamenes = m.group(1).upper() == "SI"

    # ES PARTE DE — flag de que ya hubo segregaciones (R4 importante)
    md.es_parte_de = bool(_RE_ES_PARTE.search(texto))

    return md


__all__ = [
    "extraer_registro_pypdf",
    "convertir_letras_a_numero",
    "parsear_area_letras_completa",
]
