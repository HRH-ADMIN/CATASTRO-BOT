"""Consolidación de áreas para contratos APT con MÚLTIPLES planos.

REGLA DE OFICINA (registrada el 2026-05-12 por el operador):

  Cuando un contrato APT tiene varios planos:
    1. `area_real` (área real a catastrar — bC7) =
         SUMA de `area_real` de TODOS los planos del contrato.
    2. `area_predio` (área aproximada del predio — bC7) =
         SUMA de `area_registro` de TODOS los planos del contrato.

  Razón: CFIA mira el contrato como UNA unidad de trabajo, no como
  planos sueltos. Los honorarios y el predio se calculan sobre el
  total intervenido.

EJEMPLO — VICTOR #2:
  Plano 1 (reunión):     area_real=2,751.30 / area_registro=2,500.00
  Plano 2 (segregación): area_real=2,097.00 / area_registro=8,082.81
  ─────────────────────────────────────────────────────────────────
  TOTAL contrato:        area_real=4,848.30 / area_predio=10,582.81

USO:
    from src.utils.area_consolidator import consolidar_areas_contrato

    expedientes_del_contrato = [
        meta_rdf_2026_005,  # principal
        meta_seg_2026_002,  # secundario
    ]
    totales = consolidar_areas_contrato(expedientes_del_contrato)
    # → {"area_real_m2": 4848.30, "area_predio_m2": 10582.81, "n_planos": 2}

Esta función NO modifica el seed — solo calcula. El caller decide si
sobreescribe `general.area_real` y `general.area_predio` del contrato
principal o no.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Iterable

log = logging.getLogger("catastro.area_consolidator")


def _to_float(x: Any) -> float:
    """Convierte '2,751.30' / '2751.30' / 2751.3 → 2751.3 (o 0.0 si falla)."""
    if x is None:
        return 0.0
    if isinstance(x, (int, float)):
        return float(x)
    try:
        return float(str(x).replace(",", "").strip())
    except (ValueError, TypeError):
        return 0.0


def consolidar_areas_contrato(
    expedientes: Iterable[dict],
) -> dict:
    """Suma áreas de varios expedientes hermanos en un contrato compartido.

    REGLA OPERATIVA CORREGIDA (operador 2026-05-12 — VICTOR #2 v2):

      bC7.area_real (área real a catastrar) = SUMA de `plano.area_real`
        de TODOS los planos hermanos. Esto SÍ es por plano porque cada
        plano catastra un polígono distinto (incluso si las fincas se
        comparten, el polígono catastrado es nuevo en cada uno).

      bC7.area_predio (área aproximada del predio) = SUMA de
        `area_registro` de las FINCAS ÚNICAS del contrato (deduplicar
        fincas que aparezcan en varios planos hermanos).

      Aprendido del error: yo sumaba area_registro por plano, lo que
      contaba las fincas compartidas DOS veces. Caso VICTOR #2:
        Plano 1 area_registro = 10,582.81 (suma fincas 642038 + 161099)
        Plano 2 area_registro =  8,082.81 (finca 161099 sola)
        Total INCORRECTO suma por plano = 18,665.62 (cuenta 161099 dos veces)
        Total CORRECTO fincas únicas:
          642038 (2,500) + 161099 (8,082.81) = 10,582.81  ✓

    Args:
        expedientes: lista de dicts de expediente.

    Returns:
        {
            "n_planos":         int,
            "area_real_m2":     float,  # SUMA de plano.area_real (por plano)
            "area_predio_m2":   float,  # SUMA fincas únicas (dedup)
            "fincas_unicas":    [{"numero": "...", "area_registro": x}],
            "detalle":          list por plano,
        }
    """
    total_real = 0.0
    detalle = []
    n = 0

    # Dedup fincas: numero_finca → area_registro
    fincas_por_numero: dict[str, float] = {}

    for exp in expedientes:
        datos_apt = None
        if "datos_apt" in exp:
            datos_apt = exp["datos_apt"]
        elif "metadata" in exp:
            datos_apt = (exp["metadata"] or {}).get("datos_apt")
        elif "metadata_json" in exp:
            try:
                meta = json.loads(exp["metadata_json"] or "{}")
                datos_apt = meta.get("datos_apt")
            except (TypeError, ValueError):
                datos_apt = None

        num_exp = exp.get("numero_expediente", "(sin número)")

        if not datos_apt or not isinstance(datos_apt, dict):
            detalle.append({
                "numero_expediente": num_exp,
                "area_real":     0.0,
                "area_registro": 0.0,
                "fincas":        [],
                "error": "sin datos_apt",
            })
            n += 1
            continue

        plano = datos_apt.get("plano", {}) or {}
        area_real    = _to_float(plano.get("area_real"))
        total_real   += area_real

        # Acumular fincas únicas con su area_registro (dedup)
        fincas_plano = plano.get("fincas") or []
        for f in fincas_plano:
            num_finca = str(f.get("numero") or f.get("num") or "")
            if not num_finca:
                continue
            # Si la finca tiene area_registro propia úsala, sino usa la del plano
            area_finca = _to_float(
                f.get("area_registro_m2") or f.get("area_registro")
            )
            if area_finca == 0:
                # Fallback: si solo hay UNA finca en el plano, asumimos
                # que area_registro del plano es de esa finca
                if len(fincas_plano) == 1:
                    area_finca = _to_float(plano.get("area_registro"))
            # Solo registrar la PRIMERA vez (dedup)
            if num_finca not in fincas_por_numero and area_finca > 0:
                fincas_por_numero[num_finca] = area_finca

        detalle.append({
            "numero_expediente": num_exp,
            "area_real":     area_real,
            "area_registro": _to_float(plano.get("area_registro")),
            "fincas":        [str(f.get("numero") or f.get("num") or "")
                              for f in fincas_plano],
        })
        n += 1

    total_predio = sum(fincas_por_numero.values())
    return {
        "n_planos":       n,
        "area_real_m2":   round(total_real, 2),
        "area_predio_m2": round(total_predio, 2),
        "fincas_unicas":  [{"numero": k, "area_registro": v}
                           for k, v in fincas_por_numero.items()],
        "detalle":        detalle,
    }


def aplicar_consolidacion_a_datos_apt(
    datos_apt: dict,
    totales: dict,
    *,
    recalcular_honorarios: bool = True,
) -> dict:
    """Sobrescribe campos del bC7 para que reflejen TODOS los planos.

    Aplica las 3 reglas operativas para contratos multi-plano:
      R1: area_real    = SUMA de area_real de todos los planos
      R2: area_predio  = SUMA de area_registro de todos los planos
      R3: max_planos   = N hermanos (no 1)
      R3: n_planos_catastrar = N hermanos
      Opcional: recalcular honorarios pasando todos los planos al
                calculador (regla R2 de oficina).

    Mutate-friendly: modifica el dict y también lo devuelve.

    Args:
        datos_apt: el datos_apt del expediente PRINCIPAL del contrato.
        totales: resultado de `consolidar_areas_contrato()`. Su `detalle`
            debe contener `area_real` por plano para poder recalcular
            honorarios.
        recalcular_honorarios: si True (default), recalcula honorarios
            con TODOS los planos. Requiere que el detalle tenga áreas
            por plano (siempre las tiene si vino de consolidar_areas_contrato).
    """
    if "general" not in datos_apt:
        datos_apt["general"] = {}

    # R1 + R2: áreas
    datos_apt["general"]["area_real"]   = f"{totales['area_real_m2']:.2f}"
    datos_apt["general"]["area_predio"] = f"{totales['area_predio_m2']:.2f}"

    # R3: conteo de planos
    n = totales["n_planos"]
    datos_apt["general"]["max_planos"]          = str(n)
    datos_apt["general"]["n_planos_catastrar"]  = str(n)

    # Marca de auditoría
    datos_apt["general"]["_areas_consolidadas_n_planos"] = n

    # Honorarios — recalcular con TODOS los planos
    if recalcular_honorarios and n > 1:
        try:
            from src.utils.honorarios_calculator import (
                calcular_honorarios, HonorariosInput, PlanoInput,
                decidir_tipo_y_zona_por_area,
            )
            planos_input = []
            for d in totales["detalle"]:
                area = float(d.get("area_real", 0))
                if area <= 0:
                    continue
                tipo_p, zona = decidir_tipo_y_zona_por_area(area)
                planos_input.append(PlanoInput(
                    area_m2=area, tipo_parcela=tipo_p, zona=zona,
                ))
            if planos_input:
                res = calcular_honorarios(HonorariosInput(planos=planos_input))
                datos_apt["general"]["honorarios"] = str(int(res.total))
                datos_apt["general"]["_honorarios_desglose"] = {
                    "n_planos": n,
                    "subtotal": int(res.subtotal_planos),
                    "total":    int(res.total),
                    "fuente":   "consolidacion_multi_plano",
                }
        except Exception as exc:
            log.warning("recálculo de honorarios falló: %s — "
                        "se preserva el valor anterior", exc)

    return datos_apt


def encontrar_hermanos_de_contrato(
    db, numero_expediente: str,
) -> list[dict]:
    """Devuelve TODOS los expedientes (incluyendo el dado) que comparten
    contrato APT.

    Args:
        db: instancia de Database.
        numero_expediente: cualquiera del grupo (principal o secundario).

    Returns:
        Lista ordenada por `metadata.orden_plano` ascendente. Si el
        expediente no tiene hermanos, devuelve [exp] solo.
    """
    exp = db.buscar_por_numero(numero_expediente)
    if not exp:
        return []
    meta = json.loads(exp.get("metadata_json") or "{}")
    if not meta.get("contrato_apt_compartido"):
        return [exp]
    hermanos = meta.get("expedientes_hermanos", [])
    todos = [exp]
    for num in hermanos:
        h = db.buscar_por_numero(num)
        if h:
            todos.append(h)
    # Ordenar por orden_plano
    def _orden(e):
        m = json.loads(e.get("metadata_json") or "{}")
        return int(m.get("orden_plano", 99))
    todos.sort(key=_orden)
    return todos


def consolidar_area_registro_plano(
    fincas: list[dict] | None,
) -> float:
    """Suma areas_registro de TODAS las fincas que el plano modifica.

    REGLA OPERATIVA (aprendida 2026-05-12, plano de reunión VICTOR #2):

      Cuando UN plano modifica varias fincas (reunión, segregación con
      fincas múltiples, etc.), el 'área según registro' del plano (bP1)
      = SUMA de areas_registro de TODAS esas fincas.

      Esto es DISTINTO de la consolidación del CONTRATO (que suma entre
      planos hermanos). Acá es DENTRO de un plano individual cuando
      involucra múltiples fincas.

    Ejemplo — VICTOR #2 plano de reunión:
       Finca 642038: 2,500.00 m² (registro)
       Finca 161099: 8,082.81 m² (registro — aporta solo 251.30 m² pero
                                   el plano modifica la finca entera)
       ────────────────────────────────────────────
       Área registro bP1 plano:    10,582.81 m²

    Args:
        fincas: lista de dicts con `area_registro_m2` o `area_registro`.
            Acepta string ('2500.00' / '2,500.00') o número.

    Returns:
        Suma en m² (float). 0.0 si fincas es None/vacío.
    """
    if not fincas:
        return 0.0
    total = 0.0
    for f in fincas:
        # Buscar el campo en cualquiera de los nombres comunes
        area = (
            f.get("area_registro_m2")
            or f.get("area_registro")
            or f.get("area_m2")
            or 0
        )
        total += _to_float(area)
    return round(total, 2)


__all__ = [
    "consolidar_areas_contrato",
    "aplicar_consolidacion_a_datos_apt",
    "encontrar_hermanos_de_contrato",
    "consolidar_area_registro_plano",
]
