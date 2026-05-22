"""Procesador de discrepancias de registro detectadas durante el llenado APT.

Maneja dos categorías de discrepancia entre lo que muestra el registro de
bienes inmuebles y la realidad:

  1. **RNP/TSE mismatch** — la cédula del registro existe en TSE pero
     corresponde a otra persona (ej: cédula 2-0440-0388 — registro dice
     GRACE pero TSE devuelve AMALIA).

  2. **Dato incompleto en registro** — el registro muestra un dato
     truncado o claramente erróneo (ej: cédula jurídica "3-101-" sin
     dígitos finales, propietario sin apellidos, etc.).

Política operativa (decisión de oficina):
  - Conservar los datos del registro tal como están (no inventar)
  - Notificar al **topógrafo** (encargado del plano) Y a los admins,
    para que el topógrafo gestione la corrección registral si aplica
  - Persistir todas las discrepancias en metadata para auditoría

Cada discrepancia es un dict con al menos:
  - `tipo`: "rnp_tse_mismatch" | "registro_dato_incompleto"
  - `cedula`: cédula relacionada (puede estar truncada)
  - `contexto`: ej. "propietario", "titular#1", "contratante"
  - `descripcion`: una línea legible para el topógrafo
"""
from __future__ import annotations
import json
from datetime import datetime
from typing import Iterable

from src.utils.logger import get_logger

_log = get_logger("apt.discrepancias")


# ── Categorías de discrepancia ────────────────────────────────────────
TIPO_RNP_TSE_MISMATCH      = "rnp_tse_mismatch"
TIPO_REGISTRO_INCOMPLETO   = "registro_dato_incompleto"
TIPO_PROTOCOLO_NO_ACTIVO   = "protocolo_diferente_al_activo"


def crear_discrepancia_rnp_tse(
    *, contexto: str, cedula: str, tse_nombre: str, registro_nombre: str,
) -> dict:
    """Construye un dict de discrepancia tipo RNP/TSE mismatch."""
    return {
        "tipo":             TIPO_RNP_TSE_MISMATCH,
        "ts":               datetime.now().isoformat(timespec="seconds"),
        "contexto":         contexto,
        "cedula":           cedula,
        "tse_nombre":       tse_nombre,
        "registro_nombre":  registro_nombre,
        "descripcion":      (
            f"Cédula {cedula} aparece en el registro a nombre de "
            f"'{registro_nombre}' pero en el TSE/Padrón Electoral "
            f"está a nombre de '{tse_nombre}'."
        ),
    }


def crear_discrepancia_registro_incompleto(
    *, contexto: str, campo: str, valor: str, descripcion: str = "",
) -> dict:
    """Construye un dict de discrepancia tipo dato incompleto."""
    desc = descripcion or (
        f"El campo '{campo}' del registro está incompleto o malformado: "
        f"valor='{valor}'. Verificar con RNP y corregir registralmente."
    )
    return {
        "tipo":         TIPO_REGISTRO_INCOMPLETO,
        "ts":           datetime.now().isoformat(timespec="seconds"),
        "contexto":     contexto,
        "campo":        campo,
        "valor":        valor,
        "descripcion":  desc,
    }


def _formatear_mensaje(display: str, discrepancias: list[dict]) -> str:
    """Construye un mensaje WhatsApp con todas las discrepancias agrupadas."""
    rnp_tse  = [d for d in discrepancias if d.get("tipo") == TIPO_RNP_TSE_MISMATCH]
    incomp   = [d for d in discrepancias if d.get("tipo") == TIPO_REGISTRO_INCOMPLETO]
    proto    = [d for d in discrepancias if d.get("tipo") == TIPO_PROTOCOLO_NO_ACTIVO]
    otras    = [d for d in discrepancias if d.get("tipo") not in
                (TIPO_RNP_TSE_MISMATCH, TIPO_REGISTRO_INCOMPLETO, TIPO_PROTOCOLO_NO_ACTIVO)]

    lineas: list[str] = [f"⚠️ DISCREPANCIA REGISTRAL — {display}", ""]

    if rnp_tse:
        lineas.append("🔁 Cédulas con nombre distinto en TSE vs registro:")
        for i, d in enumerate(rnp_tse, 1):
            lineas.append(f"  {i}. {d.get('contexto','?')}")
            lineas.append(f"     Cédula:   {d.get('cedula','?')}")
            lineas.append(f"     TSE:      {d.get('tse_nombre','?')}")
            lineas.append(f"     Registro: {d.get('registro_nombre','?')}")
        lineas.append("")

    if incomp:
        lineas.append("📋 Datos incompletos en el registro:")
        for i, d in enumerate(incomp, 1):
            lineas.append(f"  {i}. {d.get('contexto','?')} → {d.get('campo','?')}")
            lineas.append(f"     Valor:    '{d.get('valor','')}'")
            lineas.append(f"     {d.get('descripcion','')}")
        lineas.append("")

    if proto:
        lineas.append("📜 Protocolo diferente al activo del topógrafo:")
        for i, d in enumerate(proto, 1):
            lineas.append(f"  {i}. Protocolo declarado: {d.get('valor','?')}")
            lineas.append(f"     Protocolo activo:    {d.get('valor_esperado','?')}")
            lineas.append(f"     {d.get('descripcion','')}")
        lineas.append("")
        lineas.append("Verifica si este plano es continuación de un contrato")
        lineas.append("anterior. Si lo es: pon honorarios=0 y cita el contrato")
        lineas.append("viejo en observaciones del contrato actual.")
        lineas.append("")

    for d in otras:
        lineas.append(f"• {d.get('descripcion','(sin descripción)')}")
        lineas.append("")

    if rnp_tse or incomp:
        lineas.append("Por favor verifica con RNP. Si requiere corrección")
        lineas.append("registral, gestionarla antes de continuar el trámite.")
    return "\n".join(lineas).rstrip()


def procesar_discrepancias(
    *,
    db,
    expediente_id: str,
    numero_expediente: str,
    discrepancias: list[dict],
    whatsapp_send_fn=None,
    admins_phones: Iterable[str] = (),
    topografo_phone: str | None = None,
) -> dict:
    """Persiste discrepancias en metadata + notifica al topógrafo y admins.

    Args:
        db: Database (debe soportar `actualizar_metadata` y opcionalmente
            `obtener_expediente`).
        expediente_id: UUID del expediente.
        numero_expediente: ej. "RDF-2026-003" (para fallback en mensaje).
        discrepancias: lista de dicts (usar `crear_discrepancia_*`).
        whatsapp_send_fn: callable(telefono, mensaje) → Any. Si None, no envía.
        admins_phones: lista de teléfonos admin a notificar.
        topografo_phone: teléfono del topógrafo responsable del plano. Si se
            pasa, recibe el mismo mensaje (prioridad: es el que debe gestionar
            la corrección registral).

    Returns dict con:
      - `cantidad`: número de discrepancias
      - `notificados_admin`: admins que recibieron mensaje
      - `notificado_topografo`: bool
      - `mensaje`: el texto enviado
    """
    if not discrepancias:
        return {
            "cantidad": 0, "notificados_admin": 0,
            "notificado_topografo": False, "mensaje": "",
            "silenciadas": 0,
        }

    # 0. Filtrar discrepancias silenciadas por reglas del operador
    # (memoria_operador). Las silenciadas se persisten igual (auditoría)
    # pero NO se notifican por WhatsApp ni MessageBox.
    silenciadas_count = 0
    try:
        from src.utils.memoria_operador import filtrar_discrepancias_silenciadas
        a_notificar, silenciadas = filtrar_discrepancias_silenciadas(db, discrepancias)
        silenciadas_count = len(silenciadas)
        if silenciadas_count:
            _log.info(
                "%d discrepancia(s) silenciada(s) por reglas del operador",
                silenciadas_count,
            )
    except Exception as exc:
        _log.warning("error filtrando silenciadas: %s", exc)
        a_notificar = list(discrepancias)

    # 1. Persistir en metadata (acumula con discrepancias previas si existen)
    try:
        meta_actual = {}
        if hasattr(db, "obtener_expediente"):
            exp = db.obtener_expediente(expediente_id)
            if exp:
                mj = exp.get("metadata_json") if isinstance(exp, dict) else \
                     getattr(exp, "metadata_json", None)
                if isinstance(mj, str):
                    meta_actual = json.loads(mj or "{}") or {}
                elif isinstance(mj, dict):
                    meta_actual = mj
        previas = meta_actual.get("apt_discrepancias_rnp") or []
        nuevas = list(previas) + list(discrepancias)
        db.actualizar_metadata(
            expediente_id,
            {"apt_discrepancias_rnp": nuevas},
            actor="apt_agent.discrepancias",
        )
    except Exception as exc:
        _log.warning("no se pudo persistir discrepancias en metadata: %s", exc)

    # 2. Resolver el identificador legible (nombre proyecto > número)
    from src.utils.display import display_proyecto
    exp_view = None
    if hasattr(db, "obtener_expediente"):
        try:
            exp_view = db.obtener_expediente(expediente_id)
        except Exception:
            pass
    display = display_proyecto(exp_view) if exp_view else numero_expediente

    # Si TODAS fueron silenciadas, no enviar nada
    if not a_notificar:
        return {
            "cantidad": len(discrepancias),
            "notificados_admin": 0,
            "notificado_topografo": False,
            "mensaje": "",
            "silenciadas": silenciadas_count,
        }

    mensaje = _formatear_mensaje(display, a_notificar)

    # 3. Notificar — primero al topógrafo (es quien debe gestionar la
    #    corrección registral), luego a admins para auditoría.
    notificado_topografo = False
    if topografo_phone and whatsapp_send_fn:
        try:
            whatsapp_send_fn(topografo_phone, mensaje)
            notificado_topografo = True
            _log.info("discrepancia notificada al topógrafo %s", topografo_phone)
        except Exception:
            _log.exception("no se pudo notificar al topógrafo %s", topografo_phone)

    notificados_admin = 0
    if whatsapp_send_fn:
        for telefono in admins_phones:
            if not telefono or telefono == topografo_phone:
                continue  # evitar duplicado si el topógrafo es también admin
            try:
                whatsapp_send_fn(telefono, mensaje)
                notificados_admin += 1
                _log.info("discrepancia notificada al admin %s", telefono)
            except Exception:
                _log.exception("no se pudo notificar al admin %s", telefono)
    else:
        _log.info("whatsapp_send_fn=None — discrepancia solo en metadata/log")

    return {
        "cantidad":             len(discrepancias),
        "notificados_admin":    notificados_admin,
        "notificado_topografo": notificado_topografo,
        "mensaje":              mensaje,
        "silenciadas":          silenciadas_count,
    }


def resolver_telefono_topografo(db, expediente: dict) -> str | None:
    """Resuelve el teléfono del topógrafo responsable del expediente.

    Estrategia:
      1. Buscar usuario activo con rol='topografo' cuyo nombre coincida con
         `expediente.nombre_topografo` (búsqueda case-insensitive).
      2. Si no aparece, buscar por `cedula_topografo`.
      3. Si tampoco, devolver None (caller hará fallback al admin).
    """
    if not expediente:
        return None
    nombre = (expediente.get("nombre_topografo") or "").strip().upper()
    cedula = (expediente.get("cedula_topografo") or "").strip()

    try:
        usuarios = db.listar_usuarios(rol="topografo", activo=True)
    except Exception:
        return None

    for u in usuarios:
        if cedula and (u.get("cedula") or "").strip() == cedula:
            return u.get("telefono")
        if nombre and (u.get("nombre") or "").strip().upper() == nombre:
            return u.get("telefono")
    return None
