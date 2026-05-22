"""Constructor del `datos_apt` desde extracciones Vision + reglas de oficina.

Toma `CajetinData` + `RegistroData` + `EnteroData` y produce un dict listo
para escribir en `metadata.datos_apt` del expediente.

Aplica todas las reglas de oficina aprendidas:
  - Tipo zona: <2000m² → URBANO "3" / >=2000m² → RURAL "2"
  - Tipo ubicación: urbano default Parcela E ("10")
  - Tipo uso: mapper desde naturaleza
  - Naturaleza: siempre Equidad ("2")
  - Composición: siempre unipersonal
  - Moneda: siempre Colón ("1")
  - Tipo proyecto APT: siempre "27" Plano Simple
  - Tipo coordenada: siempre CRTM05 ("3")
  - Honorarios: calculadora decreto 17481 con +5000 ajuste
  - Provincia/cantón/distrito: nombres → códigos APT (zero-padded)
  - Plano previo del registro o cajetín → bP5 planos_modificar
  - Centroide areal (Stokes) de las coordenadas extraídas
  - Correo profesional: hardcoded "topografiahrh@gmail.com"

Devuelve dict con:
  - `datos_apt`: el seed listo
  - `advertencias`: lista de strings con cosas que el operador debe revisar
  - `confianza_baja`: campos donde Vision dudó o no encontró datos
"""
from __future__ import annotations
import re
from pathlib import Path
from typing import Optional

from src.utils.plano_vision_extractor import (
    CajetinData, RegistroData, EnteroData,
)
from src.utils.ubicacion_cr import resolver_ubicacion
from src.utils.tipo_uso_mapper import mapear_tipo_uso
from src.utils.centroide import centroide_para_apt
from src.utils.honorarios_calculator import (
    calcular_honorarios, PlanoInput, HonorariosInput,
    decidir_tipo_y_zona_por_area,
)


CORREO_PROFESIONAL_DEFAULT = "topografiahrh@gmail.com"


# ── Helpers ─────────────────────────────────────────────────────────────

def _parsear_finca(folio_real: str) -> tuple[str, str]:
    """`'2422958-000'` → (`'422958'`, `'000'`).

    Devuelve (numero_sin_provincia, derecho). Si no se puede parsear,
    devuelve el input crudo y "000".

    NOTA: el folio real en el cajetín suele venir como 7 dígitos donde el
    primero es la provincia (ej. '2422958' = provincia 2, finca 422958).
    Vision a veces devuelve "2-118879-000" (con guiones) — soportado.
    """
    if not folio_real:
        return ("", "000")
    raw = folio_real.strip()
    # Caso 3 segmentos: "2-118879-000" → provincia-numero-derecho
    m3 = re.match(r"^(\d)\s*-\s*(\d+)\s*-\s*(\d+)\s*$", raw)
    if m3:
        numero = m3.group(2).lstrip("0") or "0"
        derecho = m3.group(3).zfill(3)
        return (numero, derecho)
    # Caso 2 segmentos: "422958-000" → numero-derecho
    m2 = re.match(r"^(\d+)\s*-\s*(\d+)\s*$", raw)
    if m2:
        numero, derecho = m2.group(1), m2.group(2)
    else:
        numero, derecho = raw, "000"
    # Si el primer dígito es 1-7 y el largo total es 7, asumimos
    # provincia_digit + finca_6digit. Quitar provincia.
    if len(numero) == 7 and numero[0] in "1234567":
        numero = numero[1:]  # quitar primer dígito
    # Limpiar ceros izquierda (APT acepta sin ceros)
    numero = numero.lstrip("0") or "0"
    return (numero, derecho)


def _limpiar_numero_plano(numero_raw: str) -> tuple[str, str]:
    """Vision a veces devuelve `numero="2-62586"` con el prefijo de provincia.

    Devuelve (numero_limpio, provincia_inferida). Si el prefijo no es un
    dígito provincia válido (1-7), provincia_inferida queda vacía.
    """
    if not numero_raw:
        return ("", "")
    s = str(numero_raw).strip()
    m = re.match(r"^([1-7])\s*-\s*(\d+)\s*$", s)
    if m:
        return (m.group(2).lstrip("0") or "0", m.group(1))
    return (s.lstrip("0") or "0", "")


def _parsear_plano_previo(texto: str) -> Optional[dict]:
    """`'A-1095215-2006'` → {'provincia':'2', 'numero':'1095215', 'anno':'2006'}.

    Mapping letra → código provincia (cédula de provincia):
      S=1 SAN JOSE, A=2 ALAJUELA, C=3 CARTAGO, H=4 HEREDIA,
      G=5 GUANACASTE, P=6 PUNTARENAS, L=7 LIMON
    """
    if not texto:
        return None
    m = re.match(r"^\s*([A-Z])\s*-\s*(\d+)\s*-\s*(\d{4})\s*$", texto.upper())
    if not m:
        return None
    letra, numero, anno = m.group(1), m.group(2), m.group(3)
    PROV = {"S": "1", "A": "2", "C": "3", "H": "4",
            "G": "5", "P": "6", "L": "7"}
    return {
        "provincia": PROV.get(letra, ""),
        "numero":    numero.lstrip("0") or "0",
        "anno":      anno,
    }


def _separar_nombre(nombre_completo: str) -> tuple[str, str, str]:
    """Heurística para separar 'LUIS EMILIO DE LOS ANGELES PIÑEIRO CASTRO'
    en (nombre, apellido1, apellido2).

    NOTA: en CR muchos nombres incluyen 'DE LOS X' como parte del nombre.
    Esta función NO es robusta — el extractor Vision debería devolver
    ya separados. Esto es solo fallback.
    """
    palabras = nombre_completo.split()
    if len(palabras) >= 3:
        return (" ".join(palabras[:-2]), palabras[-2], palabras[-1])
    if len(palabras) == 2:
        return (palabras[0], palabras[1], "")
    return (nombre_completo, "", "")


# ── Builder ────────────────────────────────────────────────────────────

def build_datos_apt(
    *,
    cajetin: CajetinData,
    registro: RegistroData,
    entero: EnteroData,
    correo_profesional: str = CORREO_PROFESIONAL_DEFAULT,
    archivos_paths: Optional[dict] = None,
) -> dict:
    """Combina extracciones + reglas de oficina + cálculos derivados.

    Args:
        cajetin/registro/entero: dataclasses extraídas via Vision.
        correo_profesional: correo a usar en bC3 + bC1 propietario.
        archivos_paths: dict con `anverso`, `entero`, `derrotero` (paths
            absolutos). Si None, se omite la sección de archivos.

    Returns dict con:
        - `datos_apt`: el seed
        - `advertencias`: list[str]
        - `confianza_baja`: list[str] (nombres de campos)
    """
    advertencias: list[str] = []
    confianza_baja: list[str] = []

    # ── Ubicación ──────────────────────────────────────────────────
    ubic = resolver_ubicacion(
        registro.provincia_finca or cajetin.provincia_nombre,
        registro.canton_finca    or cajetin.canton_nombre,
        registro.distrito_finca  or cajetin.distrito_nombre,
    )
    if ubic["incompleto"]:
        faltantes = []
        if not ubic["provincia"]: faltantes.append("provincia")
        if not ubic["canton"]:    faltantes.append("cantón")
        if not ubic["distrito"]:  faltantes.append("distrito")
        advertencias.append(
            f"Ubicación no mapeada en src/utils/ubicacion_cr.py — "
            f"faltan códigos para: {', '.join(faltantes)} "
            f"(declarado: {ubic['provincia_nombre']}/{ubic['canton_nombre']}/{ubic['distrito_nombre']})"
        )
        confianza_baja.extend(f"proyecto.{c}" for c in faltantes)

    # ── Finca ─────────────────────────────────────────────────────
    numero_finca, derecho = _parsear_finca(cajetin.folio_real or registro.finca)
    if registro.finca and not numero_finca:
        # Caso: cajetín no trae finca, usar el del registro
        numero_finca = registro.finca.lstrip("0") or "0"

    # ── Propietario ───────────────────────────────────────────────
    propietario = {
        "tipo_cedula": "2" if registro.tipo_propietario == "JURIDICA" else "1",
        "cedula":      registro.cedula_propietario,
        "nombre":      registro.nombre_propietario,
        "apellido1":   registro.apellido1_propietario,
        "apellido2":   registro.apellido2_propietario,
        "correo":      correo_profesional,
    }
    # Si el registro tiene cédula truncada/incompleta → activar flag
    if registro.cedula_registro_original:
        propietario["cedula_registro_original"] = registro.cedula_registro_original
        advertencias.append(
            f"Registro mostraba cédula truncada/errónea: '{registro.cedula_registro_original}'. "
            f"Operador completó con: '{registro.cedula_propietario}'. Verificar con RNP."
        )
    # Validación mínima
    if not propietario["cedula"]:
        advertencias.append("No se pudo extraer cédula del propietario.")
        confianza_baja.append("propietario.cedula")

    # ── Áreas ─────────────────────────────────────────────────────
    area_real_str = (cajetin.area_real or "").replace(",", "").strip()
    area_reg_str  = (registro.area_registro_m2 or cajetin.area_registro or "")
    area_reg_str  = area_reg_str.replace(",", "").strip()
    try:
        area_real_num = float(area_real_str) if area_real_str else 0.0
    except ValueError:
        area_real_num = 0.0
        advertencias.append(f"Área real no parseable: '{area_real_str}'")

    # ── Tipo zona / ubicación ─────────────────────────────────────
    tipo_parcela, zona_letra = decidir_tipo_y_zona_por_area(area_real_num) \
        if area_real_num else ("urbana", "E")
    tipo_zona_codigo = "3" if tipo_parcela == "urbana" else "2"
    # Letra → código APT (sólo aplica para urbano)
    LETRA_A_UBIC = {"A": "5", "B": "6", "C": "7", "CH": "8", "D": "9", "E": "10"}
    tipo_ubicacion = LETRA_A_UBIC.get(zona_letra, "") if tipo_parcela == "urbana" else ""

    # ── Tipo uso ──────────────────────────────────────────────────
    tipo_uso = mapear_tipo_uso(registro.naturaleza)
    if not tipo_uso:
        advertencias.append(
            f"Tipo de uso APT no se pudo inferir desde naturaleza: "
            f"'{registro.naturaleza[:80]}'. Operador debe seleccionar."
        )
        confianza_baja.append("plano.tipo_uso")

    # ── Centroide ─────────────────────────────────────────────────
    norte_str, este_str = "", ""
    if cajetin.coordenadas and len(cajetin.coordenadas) >= 3:
        coords = [(p[0], p[1]) for p in cajetin.coordenadas if len(p) >= 2]
        try:
            este_str, norte_str = centroide_para_apt(coords)
        except Exception as exc:
            advertencias.append(f"No se pudo computar centroide: {exc}")
            confianza_baja.append("plano.centroide")
    elif not cajetin.coordenadas:
        advertencias.append(
            "Vision no extrajo el listado de coordenadas. "
            "Operador debe rellenar norte/este del centroide manualmente."
        )
        confianza_baja.extend(["plano.norte", "plano.este"])
    vertices_count = len(cajetin.coordenadas)

    # ── Honorarios ────────────────────────────────────────────────
    honorarios_str = ""
    if area_real_num:
        try:
            tp, zn = decidir_tipo_y_zona_por_area(area_real_num)
            plano_in = PlanoInput(area_m2=area_real_num, tipo_parcela=tp, zona=zn)
            res = calcular_honorarios(HonorariosInput(planos=[plano_in]))
            honorarios_str = str(int(res.total))
        except Exception as exc:
            advertencias.append(f"No se pudo calcular honorarios: {exc}")

    # ── Planos a modificar ───────────────────────────────────────
    planos_modificar = []
    # 1) Lo que Vision sacó del cajetín (estructurado)
    for p in (cajetin.planos_modificar or []):
        if isinstance(p, dict) and p.get("numero"):
            letra = (p.get("letra") or "").upper()
            PROV = {"S":"1","A":"2","C":"3","H":"4","G":"5","P":"6","L":"7"}
            # Caso 1: Vision dio letra ("A") y numero limpio → mapeamos
            # Caso 2: Vision dio numero con prefijo "2-NNN" → extraemos
            numero_limpio, prov_inferida = _limpiar_numero_plano(p["numero"])
            provincia = PROV.get(letra, "") or prov_inferida
            planos_modificar.append({
                "provincia": provincia,
                "numero":    numero_limpio,
                "anno":      str(p.get("anno", "")),
            })
    # 2) Fallback al plano_previo del registro
    if not planos_modificar and registro.plano_previo:
        p = _parsear_plano_previo(registro.plano_previo)
        if p:
            planos_modificar.append(p)

    # ── Entero ────────────────────────────────────────────────────
    entero_dict = {
        "numero":         entero.numero,
        "fecha":          entero.fecha,
        "total_cfia":     entero.timbre_cfia,
        "total_registro": entero.timbre_registro,
        "monto_pagado":   entero.monto_tasado,  # APT espera total tasado
        "cit_ntrip":      entero.timbre_cit_ntrip,
    }
    if not entero.numero:
        advertencias.append("Entero: número no extraído.")
        confianza_baja.append("plano.entero.numero")

    # ── Detección segregación ────────────────────────────────────
    if registro.es_parte_de:
        advertencias.append(
            "Registro dice 'ES PARTE DE' — esto es SEGREGACIÓN. "
            "Confirmar tipo_plano del BD."
        )

    # ── Armar datos_apt completo ─────────────────────────────────
    datos_apt = {
        "propietario": propietario,
        "contratante_es_propietario": True,
        "profesional": {"correo": correo_profesional},
        "protocolo": {
            "numero":              cajetin.protocolo_tomo,
            "folio":               cajetin.protocolo_folio,
            "tipo_proyecto_modal": "27",
        },
        "proyecto": {
            "tipo_plano_apt": "27",
            "provincia":      ubic["provincia"],
            "canton":         ubic["canton"],
            "distrito":       ubic["distrito"],
            "naturaleza":     "2",
        },
        "general": {
            "area_predio":            area_reg_str,
            "area_real":              area_real_str,
            "moneda":                 "1",
            "honorarios":             honorarios_str,
            "exoneracion_honorarios": False,
            "adelanto":               "0",
            "pagos_parciales":        "0",
            "plazo_entrega":          "al finalizar el contrato",
            "max_planos":             "1",
            "observaciones":          "",
            "composicion":            "unipersonal",
        },
        "plano": {
            "descripcion":     cajetin.descripcion,
            "area_real":       area_real_str,
            "area_registro":   area_reg_str,
            "tipo_zona":       tipo_zona_codigo,
            "tipo_ubicacion":  tipo_ubicacion,
            "tipo_uso":        tipo_uso,
            "tamanno":         "",
            "tipo_coordenada": "3",
            "norte":           norte_str,
            "este":            este_str,
            "vertices":        str(vertices_count) if vertices_count else "",
            "del_estado":      False,
            "fincas": [
                {
                    "provincia": ubic["provincia"],
                    "numero":    numero_finca,
                    "derecho":   derecho,
                    "duplicado": "",  # ignorar HORIZONTAL del registro
                },
            ],
            # R5: bP4 titulares = propietarios actuales de bP2 (1:1).
            # Inicializamos con el propietario actual del registro. El operador
            # puede ajustar si hay co-propietarios (revisar registro completo).
            "titulares": (
                [{
                    "tipo_identificacion": (
                        "2" if registro.tipo_propietario == "JURIDICA" else "1"
                    ),
                    "identificacion":      registro.cedula_propietario,
                    "nombre":              registro.nombre_propietario,
                    "apellido1":           registro.apellido1_propietario,
                    "apellido2":           registro.apellido2_propietario,
                    "titularidad":         "5",  # PROPIETARIO (BOT_PLAYBOOK)
                    "porcentaje":          "100",
                }]
                if registro.cedula_propietario else []
            ),
            "planos_modificar": planos_modificar,
            "entero": entero_dict,
            "archivos": archivos_paths or {},
        },
    }

    # ── Validar Derrotero ZIP (si está disponible) ────────────────
    derrotero_path = (archivos_paths or {}).get("derrotero")
    if derrotero_path:
        from src.utils.derrotero_validator import validar_derrotero
        # Convert area string to float for validation
        try:
            area_decl_num = float(area_real_str) if area_real_str else None
        except (ValueError, TypeError):
            area_decl_num = None
        coords_caj = [(p[0], p[1]) for p in (cajetin.coordenadas or [])
                      if len(p) >= 2]
        val = validar_derrotero(
            zip_path=derrotero_path,
            coords_cajetin=coords_caj or None,
            area_declarada=area_decl_num,
        )
        # Promover errores y advertencias del validador
        for e in val.errores:
            advertencias.append(f"Derrotero: {e}")
        for w in val.advertencias:
            advertencias.append(f"Derrotero: {w}")

    return {
        "datos_apt":     datos_apt,
        "advertencias":  advertencias,
        "confianza_baja": confianza_baja,
    }


__all__ = ["build_datos_apt", "CORREO_PROFESIONAL_DEFAULT"]
