"""Tracking de progreso del llenado APT — para resume tras crash o reinicio.

Cada sección bC1-bC8 (contrato) y bP1-bP7 (plano) genera un evento de
progreso que se persiste en `metadata.apt_progreso` del expediente.

Cuando el runner se ejecuta y detecta progreso previo:
  1. Lee `apt_progreso` de la BD
  2. Verifica el estado REAL en APT navegando al URL del contrato/plano
     (los íconos de los acordeones bC*/bP* son la fuente autoritativa)
  3. Combina ambas fuentes (live > saved) y skip las ya verdes

Estados del contrato:
  - `contrato_guardado`: bool — true cuando hay `apt_tramite` en metadata
  - `plano_iniciado`:    bool — true cuando hay NumPlano en URL post-bP1

Secciones del plano:
  - bP1_generales       (boolean)
  - bP2_fincas          (count int — soporta múltiples fincas)
  - bP3_situacion       (boolean — default green tras bP1)
  - bP4_titulares       (count int — adicionales al propietario)
  - bP5_planos_modif    (count int)
  - bP6_enteros         (boolean)
  - bP7_archivos        (list[str] de tipos subidos: anverso, entero, derrotero)
  - enviado_cfia        (boolean — terminal)

USO:
    from src.utils.apt_progreso import (
        marcar_seccion_completa, obtener_progreso,
        verificar_progreso_en_apt, debe_skip_seccion,
    )

    # Antes de cada sección:
    if debe_skip_seccion(progreso, "bP6"):
        print("bP6 ya hecho — skip")
        continue
    # ... llenar sección ...
    marcar_seccion_completa(db, exp_id, "bP6")
"""
from __future__ import annotations
import json
import logging
from typing import Any

log = logging.getLogger("catastro.apt_progreso")


# ── Identificadores de sección ──────────────────────────────────────
SECCIONES_CONTRATO = ["bC1", "bC2", "bC3", "bC4", "bC5", "bC6", "bC7", "bC8"]
SECCIONES_PLANO    = ["bP1", "bP2", "bP3", "bP4", "bP5", "bP6", "bP7"]
TODAS_SECCIONES    = SECCIONES_CONTRATO + SECCIONES_PLANO


def _normalizar(meta: dict) -> dict:
    """Asegura estructura mínima de `apt_progreso`."""
    pr = meta.get("apt_progreso") or {}
    pr.setdefault("contrato_guardado", bool(meta.get("apt_tramite")))
    pr.setdefault("plano_iniciado", False)
    pr.setdefault("secciones", {})  # {"bP1": True, "bP6": True, ...}
    pr.setdefault("archivos_subidos", [])  # ["anverso", "entero", "derrotero"]
    pr.setdefault("enviado_cfia", bool(meta.get("apt_envio_fecha")))
    return pr


def obtener_progreso(db, expediente_id: str) -> dict:
    """Lee `apt_progreso` desde metadata de BD. Devuelve dict normalizado.

    Si no existe progreso previo, devuelve la estructura vacía con flags
    derivados (contrato_guardado=True si hay apt_tramite, etc.).
    """
    if not db or not expediente_id:
        return _normalizar({})
    try:
        exp = db.obtener_expediente(expediente_id)
    except Exception as exc:
        log.warning("error leyendo expediente %s: %s", expediente_id, exc)
        return _normalizar({})
    if not exp:
        return _normalizar({})
    raw = exp.get("metadata_json") if isinstance(exp, dict) else \
          getattr(exp, "metadata_json", None)
    if isinstance(raw, str):
        try:
            meta = json.loads(raw or "{}")
        except json.JSONDecodeError:
            meta = {}
    elif isinstance(raw, dict):
        meta = raw
    else:
        meta = {}
    return _normalizar(meta)


def marcar_seccion_completa(db, expediente_id: str, seccion: str,
                            *, archivo: str = "") -> None:
    """Marca una sección como completada en `metadata.apt_progreso`.

    Args:
        seccion: ej "bP6", "bP2", "bC4". Para archivos (bP7) se ignora la
            sección y se usa `archivo` (ej "anverso").
        archivo: si la sección es bP7, identifica qué tipo de archivo se subió.
    """
    if not db or not expediente_id or not seccion:
        return
    progreso = obtener_progreso(db, expediente_id)
    if seccion == "bP7" and archivo:
        if archivo not in progreso["archivos_subidos"]:
            progreso["archivos_subidos"].append(archivo)
    else:
        progreso["secciones"][seccion] = True
    if seccion in SECCIONES_PLANO and seccion != "bP1":
        progreso["plano_iniciado"] = True
    if seccion == "bP1":
        progreso["plano_iniciado"] = True
    try:
        db.actualizar_metadata(
            expediente_id,
            {"apt_progreso": progreso},
            actor="apt_progreso.marcar",
        )
    except Exception as exc:
        log.warning("error persistiendo progreso (%s): %s", seccion, exc)


def debe_skip_seccion(progreso: dict, seccion: str,
                      *, archivo: str = "") -> bool:
    """True si la sección ya está marcada como completa.

    Para bP7 archivos: True si `archivo` ya está en `archivos_subidos`.
    """
    if not progreso:
        return False
    if seccion == "bP7" and archivo:
        return archivo in progreso.get("archivos_subidos", [])
    return bool(progreso.get("secciones", {}).get(seccion))


def verificar_progreso_en_apt(page) -> dict:
    """Lee íconos de bC1-bC8 + bP1-bP7 desde la página APT abierta.

    Devuelve dict como `{"bC1": True, ..., "bP3": False, ...}` donde True =
    `fa-check-circle` y False = `fa-times-circle` u otro estado.
    """
    if page is None:
        return {}
    try:
        estados = page.evaluate(
            """() => {
                const r = {};
                const ids = ['bC1','bC2','bC3','bC4','bC5','bC6','bC7','bC8',
                             'bP1','bP2','bP3','bP4','bP5','bP6','bP7'];
                for (const id of ids) {
                    const el = document.querySelector('#' + id);
                    if (!el) { r[id] = null; continue; }
                    const icon = el.querySelector('i,span.fa,span.glyphicon');
                    const cls = icon ? icon.className : '';
                    r[id] = /fa-check-circle/.test(cls);
                }
                return r;
            }"""
        )
        if not isinstance(estados, dict):
            return {}
        return estados
    except Exception as exc:
        log.warning("error leyendo íconos APT: %s", exc)
        return {}


def sincronizar_con_live(db, expediente_id: str, estados_live: dict) -> dict:
    """Fusiona el progreso saved + el observado en la página live.

    Live es autoritativo cuando una sección está en verde — eso significa
    que APT confirmó el guardado y no hay que rellenarla.

    Si live dice rojo (times-circle) pero saved dice True, prevalece saved
    SOLO si fue marcada hace <24h (asumimos que el bot acababa de marcarla
    pero la página aún no refrescó). Esto se simplifica acá: live wins.

    Returns el progreso fusionado y lo persiste a BD.
    """
    progreso = obtener_progreso(db, expediente_id)
    secciones = progreso.setdefault("secciones", {})
    for sec_id, esta_verde in (estados_live or {}).items():
        if esta_verde is True:
            secciones[sec_id] = True
            if sec_id in SECCIONES_PLANO:
                progreso["plano_iniciado"] = True
    # Persistir
    try:
        db.actualizar_metadata(
            expediente_id,
            {"apt_progreso": progreso},
            actor="apt_progreso.sync_live",
        )
    except Exception as exc:
        log.warning("error persistiendo sync_live: %s", exc)
    return progreso


def resumen_progreso(progreso: dict) -> str:
    """Devuelve un resumen legible del progreso para mostrar al operador."""
    if not progreso:
        return "(sin progreso registrado)"
    lineas = []
    if progreso.get("contrato_guardado"):
        lineas.append("✅ Contrato guardado (tiene apt_tramite)")
    else:
        lineas.append("⏳ Contrato no guardado")
    secciones = progreso.get("secciones", {})
    completas = [s for s in SECCIONES_PLANO if secciones.get(s)]
    pendientes = [s for s in SECCIONES_PLANO if not secciones.get(s)]
    lineas.append(f"  Plano completo: {' '.join(completas) or '(ninguna)'}")
    lineas.append(f"  Plano pendiente: {' '.join(pendientes) or '(ninguna)'}")
    if progreso.get("archivos_subidos"):
        lineas.append(f"  Archivos subidos: {' '.join(progreso['archivos_subidos'])}")
    if progreso.get("enviado_cfia"):
        lineas.append("✅ Enviado al CFIA")
    return "\n".join(lineas)


__all__ = [
    "obtener_progreso", "marcar_seccion_completa", "debe_skip_seccion",
    "verificar_progreso_en_apt", "sincronizar_con_live", "resumen_progreso",
    "SECCIONES_CONTRATO", "SECCIONES_PLANO", "TODAS_SECCIONES",
]
