"""Manejo central de anomalías APT (circuit breaker).

Cuando el bot detecta una situación inesperada durante el llenado del
formulario APT (modal de error desconocido, validación de servidor falla,
selector no encontrado, etc.) el código DEBE:
  1. Lanzar `APTAnomalyError` con descripción + contexto
  2. NO seguir intentando — detener el ciclo

El runner top-level (tools/run_apt_*.py) captura la excepción y llama a
`handle_anomaly(...)` que:
  1. Dispara notificación emergente en el escritorio
  2. Envía WhatsApp al topógrafo + admins
  3. Persiste la anomalía en metadata.apt_anomalias del expediente
  4. Loggea con stack trace para debug

Filosofía: "fail loud, fail safe" — mejor que el operador intervenga 30s
antes que descubra 3 días después que CFIA rechazó el plano por un dato
inválido que el bot silenciosamente envió.
"""
from __future__ import annotations
import json
from datetime import datetime
from typing import Iterable, Optional

from src.utils.logger import get_logger
from src.utils.desktop_notify import notificar_escritorio

_log = get_logger("apt.anomaly")


def _formatear_mensaje_whatsapp(display: str, descripcion: str, contexto: str) -> str:
    head = f"🚨 catastro-bot — ANOMALÍA APT"
    return (
        f"{head}\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"Plano: *{display}*\n"
        f"Sección: {contexto or '(sin contexto)'}\n\n"
        f"⚠️ {descripcion}\n\n"
        f"El bot DETUVO el ciclo automáticamente.\n"
        f"Revisar el navegador y resolver manualmente."
    )


def handle_anomaly(
    *,
    db,
    expediente_id: Optional[str],
    numero_expediente: str,
    descripcion: str,
    contexto: str = "",
    detalle: str = "",
    whatsapp_send_fn=None,
    admins_phones: Iterable[str] = (),
    topografo_phone: Optional[str] = None,
    page=None,
) -> dict:
    """Maneja una anomalía detectada: notifica al operador (escritorio +
    WhatsApp) y persiste en metadata.

    Devuelve dict con `mensaje`, `notif_escritorio`, `notif_topografo`,
    `notif_admin`, `persistida`.
    """
    # Resolver identificador legible
    from src.utils.display import display_proyecto
    exp_view = None
    if db is not None and expediente_id and hasattr(db, "obtener_expediente"):
        try:
            exp_view = db.obtener_expediente(expediente_id)
        except Exception:
            pass
    display = display_proyecto(exp_view) if exp_view else numero_expediente

    # 1. Snapshot de la página (HTML + screenshot + modal actual) si tenemos page
    snapshot_path = None
    if page is not None:
        try:
            from src.utils.anomaly_snapshot import capturar_snapshot
            snapshot_path = capturar_snapshot(
                page,
                expediente_numero=numero_expediente,
                contexto=contexto,
                descripcion=descripcion,
                detalle=detalle,
            )
            if snapshot_path:
                _log.info("snapshot guardado en %s", snapshot_path)
        except Exception as exc:
            _log.warning("error capturando snapshot: %s", exc)

    # 2. Notificación de escritorio (urgencia alta — usa MessageBox de fallback)
    titulo_short = f"catastro-bot — ANOMALÍA {numero_expediente or ''}".strip()
    msg_desktop = f"{contexto}: {descripcion}" if contexto else descripcion
    if snapshot_path:
        msg_desktop += f"\n\nSnapshot: {snapshot_path}"
    metodo = notificar_escritorio(
        titulo=titulo_short, mensaje=msg_desktop, urgencia="alta",
    )
    _log.warning(
        "anomaly handled — display=%s contexto=%s desc=%s notif=%s",
        display, contexto, descripcion, metodo,
    )

    # 2. Persistir en metadata.apt_anomalias
    persistida = False
    if db is not None and expediente_id:
        try:
            previas = []
            if exp_view:
                mj = exp_view.get("metadata_json") if isinstance(exp_view, dict) else \
                     getattr(exp_view, "metadata_json", None)
                meta_dict = {}
                if isinstance(mj, str):
                    meta_dict = json.loads(mj or "{}") or {}
                elif isinstance(mj, dict):
                    meta_dict = mj
                previas = meta_dict.get("apt_anomalias") or []
            registro = {
                "ts":          datetime.now().isoformat(timespec="seconds"),
                "contexto":    contexto,
                "descripcion": descripcion,
                "detalle":     detalle,
            }
            db.actualizar_metadata(
                expediente_id,
                {"apt_anomalias": list(previas) + [registro]},
                actor="apt_agent.anomaly",
            )
            persistida = True
        except Exception as exc:
            _log.warning("no se pudo persistir anomalía en metadata: %s", exc)

    # 3. WhatsApp al topógrafo + admins
    mensaje_wa = _formatear_mensaje_whatsapp(display, descripcion, contexto)
    notificado_topografo = False
    notificados_admin = 0

    if whatsapp_send_fn and topografo_phone:
        try:
            whatsapp_send_fn(topografo_phone, mensaje_wa)
            notificado_topografo = True
        except Exception:
            _log.exception("no se pudo notificar al topógrafo %s", topografo_phone)

    if whatsapp_send_fn:
        for telefono in admins_phones:
            if not telefono or telefono == topografo_phone:
                continue
            try:
                whatsapp_send_fn(telefono, mensaje_wa)
                notificados_admin += 1
            except Exception:
                _log.exception("no se pudo notificar al admin %s", telefono)

    return {
        "mensaje":              mensaje_wa,
        "notif_escritorio":     metodo or "",
        "notif_topografo":      notificado_topografo,
        "notif_admin":          notificados_admin,
        "persistida":           persistida,
        "snapshot":             str(snapshot_path) if snapshot_path else "",
    }
