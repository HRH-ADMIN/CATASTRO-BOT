"""Registro de jobs de APScheduler — catastro-bot Semana 9.

Jobs registrados (9 jobs + 1 one-shot al arrancar):
  orchestrator-tick           cada 60s     procesa WhatsApp + avanza workflows
  audit-verify                diario 03h   verifica la cadena de hashes del audit log
  stale-alert                 cada 6h      detecta expedientes bloqueados > 48h y alerta
  weekly-report               lunes 07h CR resumen semanal de expedientes al admin
  db-backup                   diario 02h   copia de seguridad (+ Drive si activo)
  apt-sync-estados            cada 30 min  consulta APT vía CDP
  muni-sync-emails-arranque   one-shot     IMAP poll 30s después del boot
  muni-sync-emails            cron 11:00+14:00 CR L-V  IMAP poll (3 veces al día)
  correcciones-renotif        cada 24 h    recordatorios de planos sin corregir

Nota histórica: muni-sync-emails corría cada 15 min hasta el 2026-05-20.
Se cambió a cron 2 veces/día L-V (regla `muni_polling_3_veces_al_dia_no_cada_15min`
en `apt_memoria_operador`) porque los trámites muni demoran días, no minutos.

Gating runtime (cuando se pasa `control_manager`):
  Los jobs que tocan sistemas externos consultan `control_state` antes de
  correr. Si el módulo está OFF o el master `enabled` está OFF, el job
  loggea "skipped (gated)" y retorna sin ejecutar.

  Mapping job → módulo:
    orchestrator-tick     → "scheduler" (master)
    stale-alert           → "whatsapp"
    weekly-report         → "whatsapp"
    correcciones-renotif  → "whatsapp"
    apt-sync-estados      → "apt"
    muni-sync-emails      → "muni"
    audit-verify, db-backup → SIN gate (seguridad/integridad, corren siempre)
"""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from config.settings import DATA_DIR, LOGS_DIR
from src.utils.display import display_proyecto
from src.utils.logger import get_logger

if TYPE_CHECKING:
    from apscheduler.schedulers.base import BaseScheduler
    from src.agents.orchestrator import Orchestrator
    from src.core.control_state import ControlStateManager

_log = get_logger("scheduler")


def _gated(
    func: Callable,
    *,
    module: str,
    manager: "Optional[ControlStateManager]",
    job_id: str,
) -> Callable:
    """Wrappea `func` para que solo corra si `module` está enabled.

    Lectura dual durante la migración U-03 paso 2.4:
      1. ControlStateMachine (nuevo, SSOT): si el módulo está en RUNNING,
         pasa. En cualquier otro estado (STOPPED/STARTING/STOPPING/ERROR),
         skip. Si el módulo es desconocido para la máquina (legacy
         backwards-compat), cae al check del manager legacy.
      2. ControlStateManager legacy (`control_state.py`): se mantiene
         como espejo para no romper código existente que escribe sólo
         ahí. Eventualmente se deprecará en favor de la state machine.

    Si `manager` es None (modo legacy / windows_service.py): se ignora la
    capa legacy pero la state machine sigue activa.

    Plan: PLAN_MEJORAS Sprint 1 / U-03 paso 2.4.
    """

    def gated(*args, **kwargs):
        # ── Capa 1: ControlStateMachine (nuevo SSOT) ─────────────────
        try:
            from src.core.state_machine import (
                get_state_machine, KNOWN_MODULES as SM_KNOWN,
            )
            sm = get_state_machine()
            # 'scheduler' es el master; si está STOPPED o ERROR, gate todo
            # incluso si el módulo específico está RUNNING (semánticamente
            # equivalente al `enabled=False` del manager legacy).
            if "scheduler" in SM_KNOWN:
                sched_state = sm.read("scheduler")
                if sched_state.state in ("STOPPED", "ERROR"):
                    _log.debug(
                        "%s: skipped — scheduler master en estado %s",
                        job_id, sched_state.state,
                    )
                    return None
            if module in SM_KNOWN:
                mod_state = sm.read(module)
                if mod_state.state != "RUNNING":
                    _log.debug(
                        "%s: skipped — módulo %s en estado %s (no RUNNING)",
                        job_id, module, mod_state.state,
                    )
                    return None
                # Estado RUNNING → permitir pasar (sin consultar legacy).
                return func(*args, **kwargs)
            # Módulo no conocido por la state machine → cae al legacy.
        except Exception:
            _log.exception(
                "%s: error leyendo state machine — cayendo a legacy",
                job_id,
            )

        # ── Capa 2: ControlStateManager legacy (espejo de compat) ───
        if manager is None:
            return func(*args, **kwargs)
        try:
            state = manager.read()
        except Exception:
            _log.exception("%s: error leyendo control_state — fail-closed (skip)", job_id)
            return None
        if not state.is_module_enabled(module):
            _log.debug(
                "%s: skipped — módulo %s deshabilitado (reason=%s)",
                job_id, module, state.reason,
            )
            return None
        return func(*args, **kwargs)

    gated.__name__ = f"gated_{getattr(func, '__name__', job_id)}"
    return gated

# Carpeta de backups junto a la BD
_BACKUP_DIR = DATA_DIR / "backups"
_BACKUP_KEEP = 14    # días de historial de backups


# ── helpers ────────────────────────────────────────────────────────────────────

def _verify_audit_safe(orchestrator: "Orchestrator") -> None:
    """Verifica el audit chain y registra el resultado."""
    try:
        n = orchestrator.db.verify_audit_chain()
        _log.info("audit chain verificado (%s entradas)", n)
    except Exception:
        _log.exception("verificación del audit chain falló")


def _backup_db(orchestrator: "Orchestrator") -> None:
    """Backup completo del bot — BD + .env + config + reglas + (opcional) archivos.

    Genera un ZIP con todo lo crítico en data/backups/completos/.
    Si DRIVE_BACKUP_ENABLED=1, sube también a Google Drive (cuenta del topógrafo).
    """
    try:
        from src.utils.backup_completo import (
            generar_backup, purgar_backups_viejos,
        )

        # Backup local (siempre — básico, sin archivos pesados)
        path = generar_backup(con_archivos=False)
        _log.info("backup completo → %s (%.2f MB)",
                  path.name, path.stat().st_size / (1024 * 1024))

        # Una vez por semana, también backup CON archivos (más pesado)
        # Ejecutado los lunes
        if datetime.now(timezone.utc).weekday() == 0:
            path_full = generar_backup(con_archivos=True)
            _log.info("backup semanal CON archivos → %s (%.2f MB)",
                      path_full.name, path_full.stat().st_size / (1024 * 1024))

        # Purgar backups viejos (> 30 días)
        purgar_backups_viejos(dias=30)

        # Subir a Drive si está activo (paso 4 — OAuth)
        import os as _os
        if _os.getenv("DRIVE_BACKUP_ENABLED") == "1":
            try:
                from src.agents.drive_agent import DriveAgent
                drive = DriveAgent(orchestrator.db, orchestrator.credentials)
                resultado = drive.subir_backup(path)
                _log.info("backup Drive: %s", resultado)
            except Exception as exc:
                _log.warning("Drive backup falló: %s", exc)

    except Exception:
        _log.exception("backup completo falló")


def _stale_alert(orchestrator: "Orchestrator") -> None:
    """Detecta expedientes activos sin actividad > 48h y alerta al admin.

    El mensaje llega por WhatsApp al primer número con rol 'admin' en BD.
    """
    try:
        stale = orchestrator.db.expedientes_stale(horas=48)
        if not stale:
            _log.debug("stale-alert: sin expedientes bloqueados")
            return

        lineas = [f"⚠️ *{len(stale)} expediente(s) sin actividad >48h*\n"]
        for exp in stale[:10]:   # máximo 10 en el mensaje
            horas_sin_actividad = _horas_desde(exp.get("fecha_actualizacion", ""))
            lineas.append(
                f"  • *{display_proyecto(exp)}* "
                f"({exp['tipo_plano']}) — {exp['estado_actual']} "
                f"— {horas_sin_actividad}h sin cambios"
            )
        if len(stale) > 10:
            lineas.append(f"  … y {len(stale) - 10} más")

        mensaje = "\n".join(lineas)

        # Enviar al primer admin activo
        admins = orchestrator.db.listar_usuarios(rol="admin", activo=True)
        if not admins:
            _log.warning("stale-alert: no hay admins registrados para notificar")
            return
        for admin in admins:
            try:
                orchestrator.whatsapp.enviar_mensaje(admin["telefono"], mensaje)
                _log.info("stale-alert enviado a %s (%d exp)", admin["telefono"], len(stale))
                break
            except Exception:
                _log.exception("no se pudo notificar admin %s", admin["telefono"])

    except Exception:
        _log.exception("stale-alert falló")


def _weekly_report(orchestrator: "Orchestrator") -> None:
    """Reporte semanal de expedientes — enviado al admin los lunes a las 7am."""
    try:
        resumen = orchestrator.db.resumen_diario()
        stale   = orchestrator.db.expedientes_stale(horas=72)

        tipos_str = ", ".join(
            f"{t}: {c}" for t, c in sorted(resumen["por_tipo"].items())
        ) or "ninguno"

        lineas_stale = []
        for exp in stale[:5]:
            h = _horas_desde(exp.get("fecha_actualizacion", ""))
            lineas_stale.append(
                f"  • {display_proyecto(exp)} — {exp['estado_actual']} ({h}h)"
            )
        stale_str = "\n".join(lineas_stale) or "  (todos al día ✅)"

        mensaje = (
            f"📊 *Reporte semanal catastro-bot*\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"📁 Activos: *{resumen['activos']}*\n"
            f"🔴 Bloqueados (halt): *{resumen['bloqueados']}*\n"
            f"✅ Entregados esta semana: *{resumen['completados_semana']}*\n\n"
            f"Por tipo:\n  {tipos_str}\n\n"
            f"⏰ Sin actividad >72h:\n{stale_str}\n\n"
            f"_Generado {datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M')} UTC_"
        )

        admins = orchestrator.db.listar_usuarios(rol="admin", activo=True)
        if not admins:
            _log.warning("weekly-report: no hay admins")
            return
        for admin in admins:
            try:
                orchestrator.whatsapp.enviar_mensaje(admin["telefono"], mensaje)
                _log.info("weekly-report enviado a %s", admin["telefono"])
            except Exception:
                _log.exception("no se pudo enviar weekly-report a %s", admin["telefono"])

    except Exception:
        _log.exception("weekly-report falló")


def _renotificar_correcciones(orchestrator: "Orchestrator") -> None:
    """Re-notifica al topógrafo cada 24h si su plano lleva > 24h en APT_CORRECCIONES
    o APT_TRASLAPES sin ser corregido.

    El objetivo es que el topógrafo no "olvide" un error sin resolver.
    Si la corrección sigue sin resolverse tras 7 días, se notifica también al admin.
    """
    from src.models.estado import Estado  # import diferido para evitar circulares
    import json as _json

    try:
        for estado_valor in (Estado.APT_CORRECCIONES.value, Estado.APT_TRASLAPES.value):
            expedientes = orchestrator.db.listar_expedientes(
                estado=estado_valor, completados=False
            )
            for exp in expedientes:
                meta       = _json.loads(exp.get("metadata_json") or "{}")
                ultima     = meta.get("correcciones_ultima_notif") or \
                             exp.get("fecha_actualizacion") or ""
                horas      = _horas_desde(ultima)
                if 0 <= horas < 24:
                    continue  # notificado hace menos de 24 h

                correcciones = meta.get("correcciones_lista") or \
                               meta.get("minuta_correcciones") or []
                tipo_error   = meta.get("correcciones_tipo") or \
                               meta.get("minuta_tipo_error") or "observaciones"
                numero       = exp["numero_expediente"]
                ident        = display_proyecto(exp)

                if estado_valor == Estado.APT_CORRECCIONES.value:
                    lineas = "\n".join(f"• {c}" for c in correcciones[:10]) or \
                             f"Tipo: {tipo_error}"
                    mensaje = (
                        f"⚠️ *Recordatorio pendiente — {ident}*\n\n"
                        f"Su plano tiene correcciones sin resolver "
                        f"({tipo_error}):\n{lineas}\n\n"
                        f"Cuando el plano esté corregido, el operador confirma con:\n"
                        f"  *APROBAR {numero}*"
                    )
                else:  # APT_TRASLAPES
                    mensaje = (
                        f"⚠️ *Recordatorio traslapes — {ident}*\n\n"
                        f"Catastro reportó traslapes en su plano. "
                        f"Se requiere apelación o corrección.\n\n"
                        f"Coordine con el operador para resolver los traslapes.\n"
                        f"Cuando esté resuelto: *APROBAR {numero}*"
                    )

                try:
                    orchestrator.whatsapp.enviar_mensaje(
                        exp["telefono_cliente"], mensaje
                    )
                    orchestrator.db.actualizar_metadata(
                        exp["id"],
                        {"correcciones_ultima_notif": datetime.now(timezone.utc).isoformat()},
                        actor="scheduler.renotif",
                    )
                    _log.info(
                        "re-notificación correcciones enviada — exp %s (%dh sin resolver)",
                        exp["id"], horas,
                    )
                except Exception:
                    _log.exception("no se pudo re-notificar exp %s", exp["id"])

                # Si lleva > 7 días → alertar también al admin
                if horas > 7 * 24:
                    try:
                        admins = orchestrator.db.listar_usuarios(rol="admin", activo=True)
                        for admin in admins[:1]:
                            orchestrator.whatsapp.enviar_mensaje(
                                admin["telefono"],
                                f"🔴 *Alerta — corrección sin resolver >7 días*\n"
                                f"Plano: *{ident}*\n"
                                f"Estado: {estado_valor}\n"
                                f"Sin actividad: {horas // 24} días",
                            )
                    except Exception:
                        _log.exception("error notificando admin por corrección >7d")

    except Exception:
        _log.exception("renotificar-correcciones falló")


def _horas_desde(iso_ts: str) -> int:
    """Devuelve horas desde un timestamp ISO, o -1 si no se puede parsear."""
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        return int(delta.total_seconds() / 3600)
    except Exception:
        return -1


# ── Bitácora diaria (Sprint 5 / N-09) ────────────────────────────────────────

def _bitacora_diaria(orchestrator: "Orchestrator") -> None:
    """Genera la bitácora del día CR, la guarda en docs/bitacoras/ y la
    envía por email al operador. Idempotente: si el archivo .md ya existe
    para el día, no la regenera (a menos que se borre manualmente).

    El job está agendado para 19:00 CR (01:00 UTC del día siguiente) —
    momento típico de cierre de jornada del topógrafo.

    Plan: PLAN_MEJORAS Sprint 5 / N-09.
    """
    from pathlib import Path
    from config.settings import DATABASE_PATH
    from src.utils import daily_log

    # Resolver ROOT del proyecto
    try:
        from src.utils.dashboard_web import ROOT
        root = Path(ROOT)
    except Exception:
        root = Path.cwd()

    fecha = daily_log._fecha_default()

    # Si ya existe, leer y enviar — pero no regenerar el .md (idempotente)
    md_existente = daily_log.leer_bitacora(root, fecha)
    if md_existente:
        _log.info("bitácora-diaria: %s.md ya existe, no se regenera", fecha)
        contenido_md = md_existente
        data = None
    else:
        try:
            data = daily_log.recopilar(DATABASE_PATH, fecha)
            contenido_md = daily_log.formatear_markdown(data)
            path = daily_log.guardar_bitacora(root, fecha, contenido_md)
            _log.info("bitácora-diaria: %s guardada (%d bytes)",
                      path.name, len(contenido_md))
        except Exception:
            _log.exception("bitácora-diaria: error generando")
            return

    # Enviar por email al operador
    try:
        from src.utils.email_digest import enviar_email_smtp
        user, password = orchestrator.credentials.get_muni_san_ramon()
        # Regenerar HTML para email si tenemos data; sino plano del md
        if data is not None:
            html_body = daily_log.formatear_html(data)
        else:
            data2 = daily_log.recopilar(DATABASE_PATH, fecha)
            html_body = daily_log.formatear_html(data2)

        ok = enviar_email_smtp(
            from_addr=user, password=password, to=user,
            subject=f"[catastro-bot] Bitácora diaria — {fecha}",
            body_text=contenido_md,
            body_html=html_body,
        )
        if ok:
            _log.info("bitácora-diaria: enviada por email a %s", user)
        else:
            _log.warning("bitácora-diaria: email no salió")
    except Exception:
        _log.exception("bitácora-diaria: error enviando email")


# ── Alerta de budget Anthropic (Sprint 4 / O-08 sub-paso D) ────────────────────

def _api_budget_alert(orchestrator: "Orchestrator") -> None:
    """Si el consumo del mes superó el threshold del budget, alerta al admin.

    Idempotente por mes — check_budget_alert() trackea last_alert_mes y
    devuelve None si ya alertamos este mes (evita spam).

    Usa enviar_mensaje() del WhatsAppAgent que ya tiene fallback email de N-03:
    si Green API está caído, la alerta sale por email automáticamente.
    """
    from config.settings import DATABASE_PATH
    from src.utils import api_costs as _ac

    alert = _ac.check_budget_alert(DATABASE_PATH)
    if not alert:
        return

    msg = (
        f"⚠️ *Alerta de presupuesto IA*\n"
        f"Consumo del mes ({alert['mes']}): "
        f"*${alert['spend_usd']:.2f} USD* "
        f"({alert['ratio']*100:.1f}% del budget ${alert['budget_usd']:.0f})\n"
        f"Threshold configurado: {alert['threshold_pct']:.0f}%\n\n"
        f"Ver detalle: http://localhost:9224/config/costos"
    )

    try:
        admins = orchestrator.db.listar_usuarios(rol="admin", activo=True)
        if not admins:
            _log.warning("api-budget-alert: no hay admins para notificar")
            return
        for admin in admins[:1]:
            orchestrator.whatsapp.enviar_mensaje(
                admin["telefono"], msg, contexto="budget_alert",
            )
            _log.info(
                "api-budget-alert: enviado a admin ($%.2f / $%.0f = %.0f%%)",
                alert["spend_usd"], alert["budget_usd"], alert["ratio"]*100,
            )
            break
    except Exception:
        _log.exception("api-budget-alert falló")


# ── Auto-recovery de Green API (Sprint 4 / N-03 sub-paso C) ────────────────────

def _greenapi_recovery(orchestrator: "Orchestrator") -> None:
    """Intenta restaurar Green API si está marcado como down.

    Cada 30 min consulta el estado de la instancia (getStateInstance). Si
    responde OK, marca el servicio como 'up' (la próxima llamada a
    `enviar_mensaje` volverá a usar WhatsApp en lugar del fallback email).

    Si el servicio NO está down, no hace nada (no genera tráfico extra).
    """
    from config.settings import DATABASE_PATH
    from src.utils.external_services import is_down, mark_up
    if not is_down(DATABASE_PATH, "green_api"):
        return  # nada que recuperar

    try:
        # Llama directamente getStateInstance — bypass de los wrappers
        # que sabe el agente para no spammear el audit. Usamos requests
        # crudos con timeout corto.
        agent = orchestrator.whatsapp
        instance, token = agent._api()
        base = agent._base
        url = f"{base}/waInstance{instance}/getStateInstance/{token}"
        import requests as _r
        r = _r.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            estado = data.get("stateInstance") or data.get("state")
            if estado == "authorized":
                mark_up(DATABASE_PATH, "green_api",
                        reason=f"getStateInstance={estado}")
                _log.info("greenapi-recovery: instancia restaurada (%s)", estado)
            else:
                _log.debug("greenapi-recovery: instancia aún en %s", estado)
        elif r.status_code == 466:
            # Sigue desautorizada — no hacer nada
            _log.debug("greenapi-recovery: sigue HTTP 466")
        else:
            _log.debug("greenapi-recovery: estado HTTP %d", r.status_code)
    except Exception as exc:
        # Best-effort — no spammear logs
        _log.debug("greenapi-recovery: error checkeando — %s", exc)


# ── registro de jobs ───────────────────────────────────────────────────────────

def _sync_muni_emails(orchestrator: "Orchestrator") -> None:
    """Polea Gmail vía IMAP buscando respuestas de la muni.

    Cada 15 min consulta los emails de los últimos 7 días, clasifica como
    ACUSE_GOOGLE / APROBADO / MOROSIDAD / RECHAZADO / DESCONOCIDO, y:
      - Asocia el email al expediente por número de trámite APT (1259213, etc.)
      - Actualiza estado_actual si detecta cambio importante:
          MOROSIDAD  → muni_morosidad
          APROBADO   → muni_aprobado
          RECHAZADO  → muni_rechazado
      - Notifica al operador por WhatsApp del cambio

    Si las credenciales muni no están configuradas, hace skip silencioso.
    """
    import json as _json
    try:
        from src.utils.muni_imap_reader import (  # noqa: PLC0415
            buscar_respuestas_muni, TIPO_APROBADO, TIPO_MOROSIDAD,
            TIPO_RECHAZADO,
        )
    except Exception:
        return

    try:
        user, pw = orchestrator.credentials.get_muni_san_ramon()
    except Exception:
        _log.debug("sync-muni-emails: credenciales muni no configuradas — skip")
        return

    try:
        emails = buscar_respuestas_muni(
            user=user, password=pw, dias_atras=7, limit=30,
        )
    except Exception as exc:
        _log.warning("sync-muni-emails: IMAP falló: %s", exc)
        return

    # Mapeo de tipo email → nuevo estado del expediente
    TIPO_A_ESTADO = {
        TIPO_APROBADO:  "muni_aprobado",
        TIPO_MOROSIDAD: "muni_morosidad",
        TIPO_RECHAZADO: "muni_rechazado",
    }

    actualizados = 0
    for em in emails:
        nuevo_estado = TIPO_A_ESTADO.get(em.tipo)
        if not nuevo_estado:
            continue
        # Asociar al expediente por número de trámite APT
        tramite = em.tramite_apt
        if not tramite:
            continue
        try:
            with orchestrator.db.connect() as conn:
                rows = list(conn.execute(
                    "SELECT id, numero_expediente, estado_actual, metadata_json "
                    "FROM expedientes WHERE cancelado=0 AND completado=0"
                ).fetchall())
            for r in rows:
                meta = _json.loads(r["metadata_json"] or "{}")
                t_local = str(meta.get("apt_tramite", "")).replace(" (compartido)", "").strip()
                if t_local != tramite:
                    continue
                # Si el estado no cambia, skip
                if r["estado_actual"] == nuevo_estado:
                    continue
                # Actualizar estado + persistir info del email en metadata
                meta[f"muni_{em.tipo.lower()}"] = True
                meta["muni_fecha_aviso"] = em.fecha or ""
                meta["muni_asunto_aviso"] = em.subject[:120]
                meta["muni_remitente"] = em.from_addr[:120]
                if em.monto_pendiente:
                    meta["muni_monto_pendiente"] = em.monto_pendiente
                orchestrator.db.actualizar_metadata(
                    r["id"], meta, actor="scheduler.sync_muni_emails",
                )
                with orchestrator.db.connect() as conn:
                    conn.execute(
                        "UPDATE expedientes SET estado_actual=?, "
                        "fecha_actualizacion=datetime('now') WHERE id=?",
                        (nuevo_estado, r["id"]),
                    )
                actualizados += 1
                _log.info(
                    "sync-muni-emails: %s %s → %s (email: %s)",
                    r["numero_expediente"], r["estado_actual"],
                    nuevo_estado, em.subject[:60],
                )
        except Exception as exc:
            _log.warning("sync-muni-emails: error procesando email: %s", exc)
            continue

    if actualizados:
        _log.info("sync-muni-emails: %d expediente(s) actualizados", actualizados)


def _sync_apt_estados(orchestrator: "Orchestrator") -> None:
    """Sincroniza el estado APT real para todos los expedientes con trámite activo.

    Llama `APTAgent.consultar_estado()` para cada expediente que tenga
    `apt_tramite` y un estado APT distinto de "Público e Inscrito" / "Inscrito".

    Si CDP no está disponible (Chrome del usuario no corriendo con debug port),
    el job hace skip silencioso. Esto es intencional para no fallar en horas
    fuera de oficina.

    Si detecta cambio importante:
      - "Calificación RN" → "Público y Defectuoso" → notifica topógrafo (R2)
      - cualquier → "Público e Inscrito" → notifica topógrafo (cierre)
    """
    import json as _json
    try:
        from src.agents.apt_agent import APTAgent  # noqa: PLC0415
    except Exception as exc:
        _log.warning("apt-sync-estados: no se pudo importar APTAgent: %s", exc)
        try:
            orchestrator.db.registrar_evento(
                "apt_sync_failed",
                detalles={"error": "import_apt_agent",
                          "type": type(exc).__name__,
                          "msg": str(exc)[:500]},
                actor="scheduler.apt_sync",
            )
        except Exception:
            pass
        return

    try:
        agent = APTAgent(orchestrator.db, orchestrator.credentials)
    except Exception as exc:
        _log.warning("apt-sync-estados: no se pudo crear APTAgent: %s", exc)
        try:
            orchestrator.db.registrar_evento(
                "apt_sync_failed",
                detalles={"error": "create_apt_agent",
                          "type": type(exc).__name__,
                          "msg": str(exc)[:500]},
                actor="scheduler.apt_sync",
            )
        except Exception:
            pass
        return

    if not agent._cdp_disponible():
        # Skip silencioso de cara al usuario — fuera de oficina es esperado
        # que Chrome no esté corriendo. Pero dejamos rastro en audit_log
        # para que el widget del dashboard pueda mostrar "última vez OK
        # hace Xh" vs "CDP caído desde hace Yh".
        _log.debug("apt-sync-estados: CDP no disponible — skip")
        try:
            orchestrator.db.registrar_evento(
                "apt_sync_skipped_cdp",
                detalles={"motivo": "CDP no responde en localhost:9222"},
                actor="scheduler.apt_sync",
            )
        except Exception:
            pass
        return

    # Estados terminales — no es necesario re-consultar
    estados_terminales = {"Público e Inscrito", "Publico e Inscrito", "Inscrito"}

    actualizados = 0
    cambios = 0
    errores_por_exp: list[dict] = []  # acumula errores por-expediente (no fatales)
    try:
        with orchestrator.db.connect() as conn:
            rows = list(conn.execute(
                "SELECT id, numero_expediente, metadata_json FROM expedientes "
                "WHERE cancelado = 0 AND completado = 0"
            ).fetchall())
    except Exception as exc:
        _log.warning("apt-sync-estados: error leyendo BD: %s", exc)
        try:
            orchestrator.db.registrar_evento(
                "apt_sync_failed",
                detalles={"error": "leer_expedientes",
                          "type": type(exc).__name__,
                          "msg": str(exc)[:500]},
                actor="scheduler.apt_sync",
            )
        except Exception:
            pass
        _notificar_sync_caido(orchestrator.db, "Error leyendo BD: " + str(exc)[:120])
        return

    for r in rows:
        try:
            meta = _json.loads(r["metadata_json"] or "{}")
        except Exception:
            continue
        tramite = meta.get("apt_tramite")
        if not tramite:
            continue
        estado_anterior = meta.get("apt_estado")
        if estado_anterior in estados_terminales:
            continue

        try:
            estado_nuevo = agent.consultar_estado(r["id"])
        except Exception as exc:
            _log.warning("apt-sync %s: error: %s", r["numero_expediente"], exc)
            errores_por_exp.append({
                "expediente": r["numero_expediente"],
                "type": type(exc).__name__,
                "msg": str(exc)[:200],
            })
            continue

        if not estado_nuevo:
            continue

        # Actualizar metadata
        from datetime import datetime as _dt
        try:
            orchestrator.db.actualizar_metadata(
                r["id"],
                {
                    "apt_estado": estado_nuevo,
                    "apt_estado_sync": _dt.now().isoformat(timespec="seconds"),
                },
                actor="scheduler.apt_sync",
            )
            actualizados += 1
        except Exception as exc:
            _log.warning("apt-sync %s: no se pudo guardar: %s", r["numero_expediente"], exc)
            continue

        # Detectar cambios importantes
        if estado_nuevo != estado_anterior:
            cambios += 1
            _log.info(
                "apt-sync %s: %s → %s (trámite %s)",
                r["numero_expediente"], estado_anterior, estado_nuevo, tramite,
            )
            try:
                if "Defectuoso" in (estado_nuevo or ""):
                    orchestrator.whatsapp.notificar_estado(
                        r["id"],
                        f"📋 Plano *{r['numero_expediente']}* (trámite {tramite}) "
                        f"pasó a *{estado_nuevo}*.\n"
                        "El CFIA devolvió correcciones — revise minuta + imagenminuta "
                        "para proceder con el flujo R2.",
                    )
                elif "Inscrito" in (estado_nuevo or ""):
                    orchestrator.whatsapp.notificar_estado(
                        r["id"],
                        f"🎉 Plano *{r['numero_expediente']}* (trámite {tramite}) "
                        f"está *INSCRITO* — trámite cerrado.",
                    )
            except Exception as exc:
                _log.warning("apt-sync notif %s: %s", r["numero_expediente"], exc)

    _log.info(
        "apt-sync-estados: %d sincronizados, %d con cambios", actualizados, cambios
    )

    # Audit final — success (con o sin errores por-expediente, mientras el
    # job haya podido correr hasta el final).
    try:
        if errores_por_exp:
            orchestrator.db.registrar_evento(
                "apt_sync_partial",
                detalles={
                    "actualizados": actualizados,
                    "cambios": cambios,
                    "errores": errores_por_exp[:20],  # cap
                    "total_errores": len(errores_por_exp),
                },
                actor="scheduler.apt_sync",
            )
        else:
            orchestrator.db.registrar_evento(
                "apt_sync_success",
                detalles={
                    "actualizados": actualizados,
                    "cambios": cambios,
                    "total_revisados": len(rows),
                },
                actor="scheduler.apt_sync",
            )
    except Exception:
        pass


def _notificar_sync_caido(db, mensaje: str) -> None:
    """Lanza un toast al escritorio cuando apt-sync falla, pero solo si la
    última vez fue OK — evita spam cada 30 min mientras Chrome esté caído.

    Anti-spam: si ya hay un `apt_sync_failed` reciente (< 4h), no notifica.
    """
    try:
        ultimo_fail = db.ultimo_evento("apt_sync_failed")
        if ultimo_fail:
            from datetime import datetime, timedelta, timezone
            try:
                ts = datetime.fromisoformat(ultimo_fail["timestamp"])
                # _now_iso() guarda UTC con tz — comparar tz-aware.
                ahora = datetime.now(timezone.utc) if ts.tzinfo else datetime.now()
                if ahora - ts < timedelta(hours=4):
                    return  # ya notificado recientemente
            except Exception:
                pass
        from src.utils.desktop_notify import notificar_escritorio
        notificar_escritorio(
            titulo="catastro-bot — APT sync falló",
            mensaje=mensaje,
            urgencia="normal",
        )
    except Exception:
        pass


def register_jobs(
    scheduler: "BaseScheduler",
    orchestrator: "Orchestrator",
    *,
    control_manager: "Optional[ControlStateManager]" = None,
) -> None:
    """Registra todos los jobs en el scheduler de APScheduler.

    Args:
      control_manager: si se pasa, los jobs externos (whatsapp, apt, muni)
        consultan el estado ON/OFF antes de correr. Si es None, todos los
        jobs corren siempre (modo legacy, compatible con windows_service.py).
    """

    # ── procesamiento principal (cada 60 s) ──────────────────────────────────
    scheduler.add_job(
        _gated(orchestrator.tick, module="scheduler",
               manager=control_manager, job_id="orchestrator-tick"),
        trigger="interval",
        seconds=60,
        id="orchestrator-tick",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    # ── verificación audit chain (diario 03:00 UTC) — SIN gate ──────────────
    # Corre siempre por integridad — incluso con el bot pausado, conviene
    # detectar tampering en el log de auditoría.
    scheduler.add_job(
        _verify_audit_safe,
        trigger="cron",
        hour=3,
        minute=0,
        args=[orchestrator],
        id="audit-verify",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    # ── backup BD (diario 02:00 UTC) — SIN gate ─────────────────────────────
    # Corre siempre — los backups deben continuar incluso con el bot pausado.
    scheduler.add_job(
        _backup_db,
        trigger="cron",
        hour=2,
        minute=0,
        args=[orchestrator],
        id="db-backup",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    # ── alertas de expedientes stale (cada 6 horas) — gate "whatsapp" ───────
    scheduler.add_job(
        _gated(_stale_alert, module="whatsapp",
               manager=control_manager, job_id="stale-alert"),
        trigger="interval",
        hours=6,
        args=[orchestrator],
        id="stale-alert",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    # ── reporte semanal (lunes 07:00 hora CR = 13:00 UTC) — gate "whatsapp" ─
    scheduler.add_job(
        _gated(_weekly_report, module="whatsapp",
               manager=control_manager, job_id="weekly-report"),
        trigger="cron",
        day_of_week="mon",
        hour=13,
        minute=0,
        args=[orchestrator],
        id="weekly-report",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    # ── re-notificación correcciones/traslapes (cada 24 h) — gate "whatsapp" ─
    scheduler.add_job(
        _gated(_renotificar_correcciones, module="whatsapp",
               manager=control_manager, job_id="correcciones-renotif"),
        trigger="interval",
        hours=24,
        args=[orchestrator],
        id="correcciones-renotif",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    # ── sincronización estado APT (cada 30 min) — gate "apt" ────────────────
    scheduler.add_job(
        _gated(_sync_apt_estados, module="apt",
               manager=control_manager, job_id="apt-sync-estados"),
        trigger="interval",
        minutes=30,
        args=[orchestrator],
        id="apt-sync-estados",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    # ── sincronización emails muni vía IMAP — 3 veces al día — gate "muni" ──
    # Los trámites muni demoran días, no minutos. 3 ejecuciones diarias en
    # horas de oficina CR (11:00 y 14:00) + una al arrancar el bot.
    # CR = UTC-6 → 11:00 CR = 17:00 UTC, 14:00 CR = 20:00 UTC
    # Regla operador 2026-05-20: reducir polling para ahorrar recursos.
    _muni_job_callable = _gated(_sync_muni_emails, module="muni",
                                 manager=control_manager,
                                 job_id="muni-sync-emails")

    # Ejecución one-shot al arrancar (~30s después para que termine init)
    from datetime import timedelta as _td
    scheduler.add_job(
        _muni_job_callable,
        trigger="date",
        run_date=datetime.now(timezone.utc) + _td(seconds=30),
        args=[orchestrator],
        id="muni-sync-emails-arranque",
        max_instances=1,
        replace_existing=True,
    )
    # 11:00 CR (17:00 UTC) y 14:00 CR (20:00 UTC), lunes a viernes
    scheduler.add_job(
        _muni_job_callable,
        trigger="cron",
        hour="17,20",
        minute=0,
        day_of_week="mon-fri",
        args=[orchestrator],
        id="muni-sync-emails",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    # ── Auto-recovery Green API (N-03) — cada 30 min — gate "whatsapp" ──
    # Solo intenta recuperar si el servicio está marcado como down.
    # NO emite tráfico extra cuando está up (corta temprano en _greenapi_recovery).
    scheduler.add_job(
        _gated(_greenapi_recovery, module="whatsapp",
               manager=control_manager, job_id="greenapi-recovery"),
        trigger="interval",
        minutes=30,
        args=[orchestrator],
        id="greenapi-recovery",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    # ── Alerta budget Anthropic (O-08) — cada 6 h — gate "whatsapp" ─────
    # check_budget_alert() es idempotente por mes (last_alert_mes), así
    # que aunque corra cada 6h solo emite 1 alerta por mes/operador.
    scheduler.add_job(
        _gated(_api_budget_alert, module="whatsapp",
               manager=control_manager, job_id="api-budget-alert"),
        trigger="interval",
        hours=6,
        args=[orchestrator],
        id="api-budget-alert",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    # ── Bitácora diaria (N-09) — 19:00 CR (01:00 UTC del día siguiente) ─
    # SIN gate: queremos el reporte aunque el bot esté paused durante el día.
    scheduler.add_job(
        _bitacora_diaria,
        trigger="cron",
        hour=1, minute=0,   # 01:00 UTC = 19:00 CR (UTC-6)
        args=[orchestrator],
        id="bitacora-diaria",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )

    gate_mode = "with gate" if control_manager else "no gate (legacy)"
    _log.info(
        "scheduler: 12 jobs registrados (%s) "
        "(tick, audit-verify, db-backup, stale-alert, weekly-report, "
        "correcciones-renotif, apt-sync-estados, muni-sync-emails-arranque, "
        "muni-sync-emails [11:00+14:00 CR L-V], greenapi-recovery, "
        "api-budget-alert [6h], bitacora-diaria [19:00 CR])",
        gate_mode,
    )
