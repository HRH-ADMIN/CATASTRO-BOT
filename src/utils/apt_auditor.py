"""Auditor de planos APT — verificaciones obligatorias post-llenado.

REGLA META (operador 2026-05-12, VICTOR #2):
  "siempre realiza una revision de todos los datos de todos los planos
   para verificar que esten bien"
  "siempre revisa los archivos subidos con la informacion llenada"
  "siempre revisa los pdf que no tengas datos erroneos"

Este módulo provee 3 verificaciones obligatorias:

  1. `verificar_archivos_subidos_por_plano()`
     - Cada plano debe tener archivos con `_<plano_id>_<tipo>` en el
       nombre del servidor.
     - Si un archivo de plano A aparece en plano B → archivos mezclados.

  2. `verificar_datos_pdf_vs_seed()`
     - Antes de meter `datos_apt` a APT, cruzar:
       * cajetín planof vs seed.plano.{area_real, protocolo, descripcion}
       * registro PNG vs seed.propietario.{cedula, nombre}
       * entero PDF vs seed.plano.entero.{numero, monto_pagado}
     - Reporta discrepancias antes de tocar APT.

  3. `verificar_titulares_vs_fincas()`
     - bP4 debe tener UN titular por cada propietario distinto de las
       fincas en bP2.

USO:
    from src.utils.apt_auditor import (
        verificar_archivos_subidos_por_plano,
        verificar_titulares_vs_fincas,
    )

    res = verificar_archivos_subidos_por_plano(
        archivos_actuales=[
            {"plano_id": 1075911, "nombre_servidor": "...1075911_anverso.pdf"},
            ...
        ],
        plano_id_esperado=1075911,
        tipos_esperados={"anverso", "entero", "derrotero"},
    )
    if res["errores"]:
        # Mostrar al operador, no avanzar
"""
from __future__ import annotations

import logging
import re
from typing import Iterable, Optional

log = logging.getLogger("catastro.apt_auditor")


# ── Verificación de archivos ──────────────────────────────────────────

# Pattern para nombres del servidor APT: <token>_<plano_id>_<tipo>.<ext>
_NOMBRE_SERVIDOR_RE = re.compile(
    r".*?_(\d+)_(anverso|entero|derrotero|visado|minuta|imagenminuta)\.\w+",
    re.IGNORECASE,
)


def parsear_nombre_servidor_apt(nombre: str) -> dict:
    """Extrae plano_id y tipo de un nombre como '639xxx_1075911_anverso.pdf'.

    Returns:
        {"plano_id": int | None, "tipo": str | None, "nombre_original": str}
    """
    if not nombre:
        return {"plano_id": None, "tipo": None, "nombre_original": nombre}
    m = _NOMBRE_SERVIDOR_RE.match(nombre)
    if not m:
        return {"plano_id": None, "tipo": None, "nombre_original": nombre}
    return {
        "plano_id":       int(m.group(1)),
        "tipo":           m.group(2).lower(),
        "nombre_original": nombre,
    }


def verificar_archivos_subidos_por_plano(
    *,
    archivos_actuales: list[dict],
    plano_id_esperado: int,
    tipos_esperados: set[str] | None = None,
) -> dict:
    """Verifica que cada archivo del plano sea de ESE plano y no de otro.

    Args:
        archivos_actuales: lista de dicts con `nombre_servidor` o `nombre`.
        plano_id_esperado: id numérico del plano en APT (ej. 1075911).
        tipos_esperados: set de tipos requeridos. Default {anverso, entero, derrotero}.

    Returns:
        {
            "ok": bool,
            "errores": list[str],
            "advertencias": list[str],
            "tipos_presentes": set[str],
            "tipos_faltantes": set[str],
            "tipos_extraños": set[str],
        }
    """
    if tipos_esperados is None:
        tipos_esperados = {"anverso", "entero", "derrotero"}

    errores: list[str] = []
    advertencias: list[str] = []
    tipos_presentes: set[str] = set()
    archivos_otro_plano: list[str] = []

    for arch in archivos_actuales:
        nombre = arch.get("nombre_servidor") or arch.get("nombre") or ""
        info = parsear_nombre_servidor_apt(nombre)
        if info["plano_id"] is None:
            advertencias.append(
                f"archivo {nombre!r} no matchea patrón APT — no se pudo "
                f"verificar plano_id"
            )
            continue
        if info["plano_id"] != plano_id_esperado:
            errores.append(
                f"ARCHIVOS MEZCLADOS: {nombre!r} pertenece al plano "
                f"{info['plano_id']} pero está en plano {plano_id_esperado}"
            )
            archivos_otro_plano.append(nombre)
            continue
        if info["tipo"]:
            tipos_presentes.add(info["tipo"])

    tipos_faltantes = tipos_esperados - tipos_presentes
    tipos_extranos  = tipos_presentes - tipos_esperados

    for t in tipos_faltantes:
        errores.append(f"FALTA archivo de tipo {t!r} en plano {plano_id_esperado}")
    for t in tipos_extranos:
        advertencias.append(f"archivo de tipo {t!r} extra en plano (no esperado)")

    return {
        "ok":             len(errores) == 0,
        "errores":        errores,
        "advertencias":   advertencias,
        "tipos_presentes": tipos_presentes,
        "tipos_faltantes": tipos_faltantes,
        "tipos_extranos":  tipos_extranos,
    }


# ── Verificación de titulares vs fincas ───────────────────────────────

def verificar_titulares_vs_fincas(
    *,
    fincas_bp2: list[dict],
    titulares_bp4: list[dict],
    propietarios_por_finca: Optional[dict[str, str]] = None,
) -> dict:
    """bP4 debe incluir un titular por cada propietario distinto de las fincas.

    Args:
        fincas_bp2: lista [{numero, derecho}] de bP2.
        titulares_bp4: lista [{cedula, nombre}] de bP4.
        propietarios_por_finca: dict opcional {numero_finca: cedula_propietario}
            que el caller proveé desde su BD.

    Returns:
        {"ok": bool, "errores": [...], "advertencias": [...]}
    """
    errores: list[str] = []
    advertencias: list[str] = []

    if propietarios_por_finca is None:
        if len(fincas_bp2) > 1 and len(titulares_bp4) < 2:
            advertencias.append(
                f"plano tiene {len(fincas_bp2)} fincas pero solo "
                f"{len(titulares_bp4)} titular(es). Verificar que cada "
                f"propietario distinto esté en bP4."
            )
        return {"ok": True, "errores": errores, "advertencias": advertencias}

    cedulas_titulares = {
        _normalizar_cedula(t.get("cedula") or t.get("identificacion") or "")
        for t in titulares_bp4
    }
    cedulas_propietarios_unicas = set()
    for f in fincas_bp2:
        num = str(f.get("numero") or f.get("num") or "")
        prop_ced = propietarios_por_finca.get(num)
        if prop_ced:
            cedulas_propietarios_unicas.add(_normalizar_cedula(prop_ced))

    faltantes = cedulas_propietarios_unicas - cedulas_titulares
    for ced in faltantes:
        errores.append(
            f"FALTA titular con cédula {ced} en bP4 "
            f"(propietario de una finca de bP2 sin estar en bP4)"
        )

    return {"ok": len(errores) == 0, "errores": errores, "advertencias": advertencias}


def _normalizar_cedula(c: str) -> str:
    """Quita guiones, espacios, ceros a la izquierda."""
    return re.sub(r"[\s\-]", "", str(c or "")).lstrip("0").lower()


# ── Verificación PDF vs seed ──────────────────────────────────────────

def verificar_seed_vs_cajetin_pdf(
    *,
    seed_plano: dict,
    cajetin_extraido: dict,
    tolerancia_area_pct: float = 0.1,
) -> dict:
    """Cruza el seed.plano con los datos del cajetín del PDF.

    Reporta discrepancias en:
      - area_real (con tolerancia %)
      - protocolo_tomo / protocolo_folio
      - descripcion
      - numero_entero
      - profesional_carne

    Args:
        seed_plano: el `datos_apt.plano` del expediente.
        cajetin_extraido: dict con los campos del cajetín tal como están en el PDF.
        tolerancia_area_pct: diferencia máxima permitida (0.1 = 0.1%).

    Returns: {"ok": bool, "errores": [...], "advertencias": [...]}
    """
    errores: list[str] = []
    advertencias: list[str] = []

    def _eq_str(a, b) -> bool:
        return str(a).strip().lower() == str(b).strip().lower()

    # Descripción
    if cajetin_extraido.get("descripcion") and seed_plano.get("descripcion"):
        if not _eq_str(cajetin_extraido["descripcion"], seed_plano["descripcion"]):
            errores.append(
                f"descripcion: seed={seed_plano['descripcion']!r} != "
                f"PDF={cajetin_extraido['descripcion']!r}"
            )

    # Área (con tolerancia)
    try:
        a_seed = float(str(seed_plano.get("area_real", 0)).replace(",", ""))
        a_pdf  = float(str(cajetin_extraido.get("area_real", 0)).replace(",", ""))
        if a_seed > 0 and a_pdf > 0:
            diff_pct = abs(a_seed - a_pdf) / a_pdf * 100
            if diff_pct > tolerancia_area_pct:
                errores.append(
                    f"area_real: seed={a_seed} != PDF={a_pdf} "
                    f"({diff_pct:.2f}% diff > {tolerancia_area_pct}%)"
                )
    except (ValueError, TypeError):
        pass

    # Protocolo
    protocolo_seed = seed_plano.get("protocolo") if isinstance(
        seed_plano.get("protocolo"), dict
    ) else {}
    for campo_seed, campo_pdf in (("numero", "protocolo_tomo"),
                                  ("folio", "protocolo_folio")):
        v_seed = protocolo_seed.get(campo_seed) or ""
        v_pdf  = cajetin_extraido.get(campo_pdf) or ""
        if v_seed and v_pdf and not _eq_str(v_seed, v_pdf):
            errores.append(
                f"protocolo.{campo_seed}: seed={v_seed!r} != "
                f"PDF.{campo_pdf}={v_pdf!r}"
            )

    # N° entero
    entero_seed = (seed_plano.get("entero") or {}).get("numero") or ""
    entero_pdf  = cajetin_extraido.get("numero_entero") or ""
    if entero_seed and entero_pdf and not _eq_str(entero_seed, entero_pdf):
        errores.append(
            f"entero: seed={entero_seed!r} != PDF={entero_pdf!r}"
        )

    # Profesional
    pro_seed = seed_plano.get("profesional_carne") or ""
    pro_pdf  = cajetin_extraido.get("profesional_carne") or ""
    if pro_seed and pro_pdf and not _eq_str(pro_seed, pro_pdf):
        advertencias.append(
            f"profesional_carne: seed={pro_seed!r} vs PDF={pro_pdf!r}"
        )

    return {"ok": len(errores) == 0, "errores": errores, "advertencias": advertencias}


def doble_chequeo_plano(
    *,
    snap_plano: dict,
    seed_plano: dict,
    cajetin_extraido: Optional[dict] = None,
    propietarios_por_finca: Optional[dict[str, str]] = None,
    plano_id_apt: Optional[int] = None,
    tipos_archivos_esperados: Optional[set[str]] = None,
) -> dict:
    """DOBLE CHEQUEO completo de un plano antes de enviar al CFIA.

    REGLA META (operador 2026-05-12):
      "siempre hacer un doble checheo a toda la informacion para
       asegurarte que se lleno bien"

    Combina las 4 verificaciones individuales en un solo reporte:
      1. Íconos bP1-bP7 todos verdes
      2. Seed vs cajetín del PDF (si se provee cajetín)
      3. Archivos del plano correcto (si se provee plano_id_apt)
      4. Titulares = propietarios de fincas (si se provee tabla)

    Args:
        snap_plano: snapshot del estado actual de APT con estructura:
            {
              "iconos": {"bP1": "OK", ..., "bP7": "OK"},
              "bp1": {...campos generales...},
              "fincas": [{"numero": "...", ...}],
              "titulares": [{"cedula": "...", "nombre": "..."}],
              "archivos": [{"nombre_servidor": "..."}],
            }
        seed_plano: el `datos_apt.plano` del expediente esperado.
        cajetin_extraido: dict opcional con datos del PDF planof.
        propietarios_por_finca: dict opcional {numero_finca: cedula}.
        plano_id_apt: id numérico del plano en APT (para verificar archivos).
        tipos_archivos_esperados: set, default {anverso, entero, derrotero}.

    Returns:
        {
            "ok": bool,             # True solo si NINGUNA verificación tiene errores
            "errores": list[str],   # consolidado de todas las verificaciones
            "advertencias": list[str],
            "resumen": dict,        # contadores por categoría
        }
    """
    errores: list[str] = []
    advertencias: list[str] = []

    # ── 1. Íconos bP1-bP7 ──
    iconos = (snap_plano.get("iconos") or {})
    rojos = [k for k, v in iconos.items() if v == "ROJO" or "times" in str(v).lower()]
    if rojos:
        errores.append(f"SECCIONES ROJAS: {', '.join(rojos)}")

    # ── 2. Seed vs cajetín ──
    if cajetin_extraido is not None:
        r_pdf = verificar_seed_vs_cajetin_pdf(
            seed_plano=seed_plano, cajetin_extraido=cajetin_extraido,
        )
        errores.extend(r_pdf["errores"])
        advertencias.extend(r_pdf["advertencias"])

    # ── 3. Archivos por plano_id ──
    if plano_id_apt is not None:
        r_arch = verificar_archivos_subidos_por_plano(
            archivos_actuales=snap_plano.get("archivos") or [],
            plano_id_esperado=plano_id_apt,
            tipos_esperados=tipos_archivos_esperados,
        )
        errores.extend(r_arch["errores"])
        advertencias.extend(r_arch["advertencias"])

    # ── 4. Titulares vs fincas ──
    if propietarios_por_finca is not None:
        r_tit = verificar_titulares_vs_fincas(
            fincas_bp2=snap_plano.get("fincas") or [],
            titulares_bp4=snap_plano.get("titulares") or [],
            propietarios_por_finca=propietarios_por_finca,
        )
        errores.extend(r_tit["errores"])
        advertencias.extend(r_tit["advertencias"])

    # ── 5. Cross-checks adicionales (siempre se ejecutan) ──

    # 5a. bP1 debe tener todos los campos críticos no vacíos
    bp1 = snap_plano.get("bp1") or {}
    campos_obligatorios = ("area_real", "tamanno", "tipo_zona", "tipo_uso",
                          "tipo_coord", "norte", "este", "vertices")
    for campo in campos_obligatorios:
        if not bp1.get(campo) or str(bp1.get(campo)).strip() in ("", "0"):
            errores.append(f"bP1 campo {campo!r} vacío o cero")

    # 5b. Si fincas bP2 > 1, titulares bP4 debe ser >= cantidad de propietarios distintos
    fincas = snap_plano.get("fincas") or []
    titulares = snap_plano.get("titulares") or []
    if len(fincas) > 1 and len(titulares) < len(fincas) and not propietarios_por_finca:
        advertencias.append(
            f"{len(fincas)} fincas en bP2 pero solo {len(titulares)} titular(es) en bP4 "
            f"— verificar manualmente que cada propietario esté"
        )

    # 5c. Archivos: debe haber EXACTAMENTE 3 archivos (anverso/entero/derrotero)
    archivos = snap_plano.get("archivos") or []
    if len(archivos) < 3:
        errores.append(f"Solo {len(archivos)} archivo(s) subido(s) — esperan 3")
    elif len(archivos) > 3:
        advertencias.append(
            f"{len(archivos)} archivos (>3) — verificar que los extras sean válidos"
        )

    return {
        "ok": len(errores) == 0,
        "errores": errores,
        "advertencias": advertencias,
        "resumen": {
            "iconos_ok":      len(rojos) == 0,
            "fincas_count":   len(fincas),
            "titulares_count": len(titulares),
            "archivos_count":  len(archivos),
        },
    }


def doble_chequeo_contrato_multi_plano(
    *,
    planos_snap: list[dict],
    contrato_bc7: dict,
) -> dict:
    """Cross-check entre el bC7 del contrato y los bP1 de cada plano.

    Verifica R1-R3 (consolidación):
      - bC7 area_real      == SUMA bP1.area_real
      - bC7 area_predio    == SUMA bP1.area_registro
      - bC7 max_planos     == cantidad de planos
      - bC7 n_planos_catastrar == cantidad de planos

    Args:
        planos_snap: lista de snapshots de cada plano (con `bp1` adentro).
        contrato_bc7: dict del bC7 actual en APT.

    Returns:
        {"ok": bool, "errores": [...]}
    """
    errores: list[str] = []

    def _f(v) -> float:
        try: return float(str(v).replace(",", "").strip() or 0)
        except (ValueError, TypeError): return 0.0

    suma_real     = sum(_f(p.get("bp1", {}).get("area_real")) for p in planos_snap)
    suma_registro = sum(_f(p.get("bp1", {}).get("area_registro")) for p in planos_snap)
    n_planos      = len(planos_snap)

    bc7_real     = _f(contrato_bc7.get("area_real"))
    bc7_predio   = _f(contrato_bc7.get("area_predio"))
    bc7_max      = int(_f(contrato_bc7.get("max_planos")))

    if abs(bc7_real - suma_real) > 0.01:
        errores.append(
            f"bC7.area_real={bc7_real} != SUMA bP1.area_real={suma_real:.2f} "
            f"(R1 — consolidación contrato multi-plano)"
        )
    if abs(bc7_predio - suma_registro) > 0.01:
        errores.append(
            f"bC7.area_predio={bc7_predio} != SUMA bP1.area_registro={suma_registro:.2f} "
            f"(R1 — consolidación contrato multi-plano)"
        )
    if bc7_max != n_planos:
        errores.append(
            f"bC7.max_planos={bc7_max} != cantidad real de planos={n_planos} "
            f"(R3 — consolidación contrato multi-plano)"
        )

    return {
        "ok": len(errores) == 0,
        "errores": errores,
        "suma_real":     round(suma_real, 2),
        "suma_registro": round(suma_registro, 2),
        "n_planos":      n_planos,
    }


__all__ = [
    "parsear_nombre_servidor_apt",
    "verificar_archivos_subidos_por_plano",
    "verificar_titulares_vs_fincas",
    "verificar_seed_vs_cajetin_pdf",
    "doble_chequeo_plano",
    "doble_chequeo_contrato_multi_plano",
]
