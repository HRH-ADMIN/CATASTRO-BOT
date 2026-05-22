"""Clase base para workflows por tipo de plano (Ley 6545).

Cada subclase define:
  - `tipo_plano` (uno de TipoPlano)
  - `_post_carta_agua_estado()` — a qué estado pasar tras carta_agua_ok,
    porque ese es el punto donde los 5 flujos divergen.

Patrón general de cada handler `_h_<estado>(exp)`:
  1. Si la transición requiere confirmación WhatsApp:
       - si la última acción está `confirmada` → ejecutar la acción
         irreversible y avanzar el estado.
       - si está `pendiente` → no hacer nada (el orchestrator ya nos
         saltea, pero verificamos por idempotencia).
       - si está `rechazada` → cancelar el expediente.
       - si está `expirada` o ausente → solicitar la confirmación. El
         estado NO avanza; el orchestrator volverá a llamar tras la
         respuesta del cliente.
  2. Si la transición es de sólo-lectura (poll APT, poll muni), llama
     al agente; si el resultado es positivo avanza, si es None se queda.

Estados halt (correcciones, traslapes, formato_invalido, carta_agua_requerida,
visado_rechazado): no avanzan automáticamente. Solo cambian por
intervención externa (manualmente o vía un futuro CLI).
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from src.core.exceptions import AgentError, WorkflowError
from src.models.estado import Estado
from src.models.plano import TipoPlano
from src.utils.logger import get_logger
from src.utils.sanitizer import sanitize


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _simular_apt_activo() -> bool:
    """¿Está activa la simulación de respuestas APT en workflows?

    Antes (BUG): el código usaba `CATASTRO_BOT_DEV_MODE` directamente,
    que también controla otras cosas (BD sin SQLCipher). Eso causaba
    que en producción Windows (donde DEV_MODE=1 es necesario por BD)
    se ejecutaran SIMULACIONES de respuestas APT que avanzaban estados
    falsamente.

    Ahora dos flags separados:
      - CATASTRO_BOT_DEV_MODE=1     → BD sin cipher (necesario Windows)
      - CATASTRO_BOT_SIMULAR_APT=1  → simular respuestas APT (solo tests)

    Default: NO simular. El operador debe poner explícitamente
    CATASTRO_BOT_SIMULAR_APT=1 si quiere testing.
    """
    import os as _os
    # Compatibilidad: si CATASTRO_BOT_SIMULAR_APT no está definida,
    # mirar CATASTRO_BOT_TEST_MODE (alternativa explícita). NUNCA usar
    # DEV_MODE solo para esto.
    return (
        _os.getenv("CATASTRO_BOT_SIMULAR_APT") == "1"
        or _os.getenv("CATASTRO_BOT_TEST_MODE") == "1"
    )


class BaseWorkflow(ABC):
    tipo_plano: str = ""
    _DEFAULT_TTL_HOURS = 24

    def __init__(self, db, agents: dict):
        self.db = db
        self.agents = agents
        self.log = get_logger(f"workflow.{self.tipo_plano or 'base'}")

    # ------------------------------------------------------------------
    # dispatch
    # ------------------------------------------------------------------

    def avanzar(self, expediente_id: str) -> None:
        exp = self.db.obtener_expediente(expediente_id)
        if not exp:
            raise WorkflowError(f"expediente {expediente_id!r} no existe")
        if exp["tipo_plano"] != self.tipo_plano:
            raise WorkflowError(
                f"workflow {self.tipo_plano!r} no maneja "
                f"{exp['tipo_plano']!r} (exp={expediente_id})"
            )
        estado = exp["estado_actual"]
        if estado in Estado.terminales():
            return
        if estado in Estado.halts():
            self.log.debug("expediente %s en halt %s — esperando", exp["id"], estado)
            return
        handler = self._handler_for(estado)
        if handler is None:
            self.log.warning("sin handler para estado %r en %s",
                             estado, self.tipo_plano)
            return
        handler(exp)

    def _handler_for(self, estado: str) -> Optional[Callable]:
        return getattr(self, f"_h_{estado}", None)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _set_estado(
        self, expediente_id: str, nuevo: Estado, *, detalles: Optional[str] = None
    ) -> None:
        self.db.cambiar_estado(
            expediente_id, nuevo.value,
            actor=f"workflow.{self.tipo_plano}",
            detalles=detalles,
        )

    def _whatsapp(self):
        return self.agents["whatsapp"]

    def _drive(self):
        return self.agents["drive"]

    def _apt(self):
        return self.agents["apt"]

    def _muni(self):
        return self.agents["muni"]

    def _minuta(self):
        return self.agents["minuta"]

    def _rnp(self):
        return self.agents.get("rnp")

    def _cancelar(self, exp: dict, motivo: str) -> None:
        self.log.info("cancelando expediente %s — %s", exp["id"], motivo)
        self._set_estado(exp["id"], Estado.CANCELADO, detalles=motivo)
        self._notificar_topografo(exp, f"Trámite cancelado: {motivo}")

    def _operador_telefono(self, exp: dict) -> Optional[str]:
        """Devuelve el teléfono del operador que creó el expediente, o None."""
        import json as _json
        try:
            meta = _json.loads(exp.get("metadata_json") or "{}")
        except Exception:
            meta = {}
        return meta.get("operador_telefono")

    def _confirmacion_lista(
        self,
        exp: dict,
        tipo_accion: str,
        *,
        descripcion: str,
        payload: Optional[dict] = None,
        ttl_h: int = _DEFAULT_TTL_HOURS,
        a_operador: bool = False,
    ) -> bool:
        """Devuelve True si hay confirmación lista para consumir.

        - confirmada reciente → True (consumir y avanzar)
        - pendiente o expirada hace < ttl_h → re-solicitar
        - rechazada → cancelar expediente
        - sin acciones previas → solicitar confirmación

        a_operador=True envía al operador (quien creó el expediente)
        en lugar del cliente.
        """
        confirmada = self.db.accion_confirmada(
            expediente_id=exp["id"],
            tipo_accion=tipo_accion,
            dentro_de_segundos=ttl_h * 3600,
        )
        if confirmada:
            return True

        ultima = self.db.ultima_accion(
            expediente_id=exp["id"], tipo_accion=tipo_accion
        )
        if ultima and ultima["estado"] == "pendiente":
            return False  # esperando respuesta
        if ultima and ultima["estado"] == "rechazada":
            self._cancelar(exp, f"cliente rechazó {tipo_accion}")
            return False

        # Sin acción previa, expirada, o confirmada-pero-vencida → solicitar
        tel = self._operador_telefono(exp) if a_operador else None
        try:
            self._whatsapp().solicitar_confirmacion(
                expediente_id=exp["id"],
                tipo_accion=tipo_accion,
                descripcion=descripcion,
                payload=payload,
                ttl_segundos=ttl_h * 3600,
                telefono=tel,
            )
            destino = "operador" if a_operador else "cliente"
            self.log.info("solicitada confirmación %s para exp %s → %s",
                          tipo_accion, exp["id"], destino)
        except Exception:
            self.log.exception("no se pudo solicitar confirmación %s", tipo_accion)
        return False

    def _archivos_de(self, expediente_id: str, fase: str) -> list[dict]:
        return self.db.archivos_de(expediente_id, fase=fase)

    # ------------------------------------------------------------------
    # handlers compartidos por la mayoría de workflows
    # ------------------------------------------------------------------

    # paso 1: recibir plano del topógrafo
    def _h_recibido(self, exp: dict) -> None:
        archivos_campo = self._archivos_de(exp["id"], fase="campo")
        if not archivos_campo:
            self.log.info("expediente %s sin archivos en 01_Campo — esperando "
                          "subida del topógrafo", exp["id"])
            return
        # Asegurarnos de que las carpetas estén creadas en Drive/local
        self._drive().folder_for(exp["id"], "campo")
        self._set_estado(exp["id"], Estado.PAGO_CLIENTE_PENDIENTE,
                         detalles=f"{len(archivos_campo)} archivos recibidos")

    # paso 2: confirmar pago del cliente al topógrafo
    def _h_pago_cliente_pendiente(self, exp: dict) -> None:
        if self._confirmacion_lista(
            exp, "pago_cliente",
            descripcion=("¿El cliente ya realizó el pago del trámite?\n"
                         "Responda *SI* cuando haya confirmado el pago."),
            ttl_h=72,
            a_operador=True,
        ):
            self._set_estado(exp["id"], Estado.PAGO_CLIENTE_CONFIRMADO)

    # paso 3: validar formato del PDF/DWG
    def _h_pago_cliente_confirmado(self, exp: dict) -> None:
        archivos = self._archivos_de(exp["id"], fase="campo")
        if not archivos:
            self.log.warning("expediente %s sin archivos para validar", exp["id"])
            self._set_estado(exp["id"], Estado.FORMATO_INVALIDO,
                             detalles="no hay archivos en 01_Campo")
            return
        ok = self._validar_formato(archivos)
        if ok:
            self._set_estado(exp["id"], Estado.FORMATO_VALIDADO)
        else:
            self._set_estado(exp["id"], Estado.FORMATO_INVALIDO,
                             detalles="formato PDF/DWG inválido")
            self._notificar_topografo(
                exp,
                "⚠️ Problema de formato detectado en los archivos del plano. "
                "Revise los archivos y use APROBAR para revalidar cuando esté corregido.",
            )

    def _validar_formato(self, archivos: list[dict]) -> bool:
        """Validación profunda: capas DWG requeridas por CFIA + firma PDF.

        Delega a FileValidator (ezdxf para DWG, pypdf para PDF).
        Si las librerías no están instaladas, cae al modo básico
        (existencia + extensión válida).
        """
        from src.agents.file_validator import validar_archivos  # noqa: PLC0415
        ok, errores = validar_archivos(archivos)
        if not ok:
            self.log.warning(
                "validación de formato falló — %s", "; ".join(errores)
            )
        return ok

    # paso 4: leer entero BCR automáticamente del comprobante PDF
    def _h_formato_validado(self, exp: dict) -> None:
        """Extrae el número de entero BCR del archivo entero.pdf ya subido.

        No requiere ninguna acción del operador — el bot lee el PDF y
        avanza automáticamente a ENTEROS_PAGADOS.
        """
        from pathlib import Path as _Path
        from src.agents.file_manager import validar_pdf_entero as _validar_entero

        meta = json.loads(exp.get("metadata_json") or "{}")
        numero = exp["numero_expediente"]

        # Si el número ya está en metadata (procesado antes), avanzar directamente
        if meta.get("numero_entero"):
            self._set_estado(
                exp["id"], Estado.ENTEROS_PAGADOS,
                detalles=f"entero BCR: {meta['numero_entero']}",
            )
            return

        # Buscar el archivo entero.pdf en los archivos registrados
        archivos = self._archivos_de(exp["id"], fase="campo")
        entero_arch = next(
            (a for a in archivos
             if "entero" in (a.get("nombre_original") or "").lower()
             or a.get("tipo_archivo") == "entero"),
            None,
        )

        if not entero_arch:
            # Evitar spam: solo notificar una vez por expediente
            if not meta.get("entero_notif_enviada"):
                self._notificar_topografo(
                    exp,
                    f"⚠️ *{numero}* — No se encontró el comprobante de entero BCR.\n"
                    "Suba el archivo entero.pdf a la carpeta del expediente.",
                )
                self.db.actualizar_metadata(
                    exp["id"], {"entero_notif_enviada": True},
                    actor=f"workflow.{self.tipo_plano}",
                )
            return

        ruta = _Path(entero_arch.get("ruta_local") or "")
        if not ruta.is_file():
            self._notificar_topografo(
                exp,
                f"⚠️ *{numero}* — El archivo entero.pdf no está en disco: {ruta.name}",
            )
            return

        # Extraer número de entero del PDF
        resultado = _validar_entero(ruta)
        import re as _re
        m = _re.search(r"N[°o]\s*entero:\s*(\d+)", resultado.mensaje, _re.IGNORECASE)
        if not m:
            self._notificar_topografo(
                exp,
                f"⚠️ *{numero}* — No se pudo leer el número de entero del PDF.\n"
                f"Diagnóstico: {resultado.mensaje or 'sin información'}",
            )
            return

        numero_entero = m.group(1)
        self.log.info("entero BCR extraído automáticamente: %s (exp=%s)",
                      numero_entero, exp["id"])

        # Guardar en metadata y avanzar
        self.db.actualizar_metadata(
            exp["id"],
            {"numero_entero": numero_entero},
            actor=f"workflow.{self.tipo_plano}",
        )
        self._notificar_topografo(
            exp,
            f"✅ *{numero}* — Entero BCR registrado automáticamente.\n"
            f"N° entero: *{numero_entero}*\n"
            f"Presentando plano al Catastro Nacional...",
        )
        self._set_estado(
            exp["id"], Estado.ENTEROS_PAGADOS,
            detalles=f"entero BCR auto-extraído: {numero_entero}",
        )

    # paso 5: subir a APT (primera ronda o re-subida tras correcciones)
    def _h_enteros_pagados(self, exp: dict) -> None:
        meta = json.loads(exp.get("metadata_json") or "{}")

        # Notificar al operador (solo una vez) que el trámite avanza a APT
        if not meta.get("apt_r1_archivos_subidos") and not meta.get("apt_notif_enviada"):
            self._notificar_topografo(
                exp,
                f"✅ Entero BCR registrado — {exp['numero_expediente']}. "
                "Subiendo archivos al portal APT (Catastro Nacional)...",
            )
            self.db.actualizar_metadata(
                exp["id"], {"apt_notif_enviada": True},
                actor=f"workflow.{self.tipo_plano}",
            )
            meta["apt_notif_enviada"] = True

        # ── DEV MODE: simular APT sin tocar el portal real ───────────────────
        import os as _os  # noqa: PLC0415
        if _simular_apt_activo() and not meta.get("apt_r1_archivos_subidos"):
            self.log.info("DEV_MODE: simulando subida APT R1 para exp=%s", exp["id"])
            self._notificar_topografo(
                exp,
                f"🧪 *[MODO PRUEBA]* {exp['numero_expediente']}\n"
                "Subida APT simulada — archivos listos para el portal.\n"
                "En producción aquí se subirían al Catastro Nacional y\n"
                "el topógrafo firmaría con Firma Digital.",
            )
            self.db.actualizar_metadata(
                exp["id"],
                {"apt_r1_archivos_subidos": True, "apt_tramite": "TEST-DEV-001"},
                actor=f"workflow.{self.tipo_plano}",
            )
            self._set_estado(
                exp["id"], Estado.PRESENTADO_APT_R1,
                detalles="APT R1 simulado (DEV_MODE)",
            )
            return

        # ── sub-paso 0: verificar que existe número de trámite APT ───────────
        if not meta.get("apt_r1_archivos_subidos") and not meta.get("apt_tramite"):
            if not meta.get("apt_tramite_solicitado"):
                from config.settings import APT_CONTRATO_URL  # noqa: PLC0415
                self._notificar_topografo(
                    exp,
                    f"📋 *{exp['numero_expediente']}* — Cree el contrato en el portal APT:\n\n"
                    f"1️⃣ Ingrese a: {APT_CONTRATO_URL}\n"
                    "2️⃣ Complete el formulario con los datos del plano\n"
                    "3️⃣ Guarde (sin enviar — NO use Firma Digital aún)\n"
                    "4️⃣ El número de trámite aparece en la URL o encabezado\n"
                    f"5️⃣ Envíe: *APT TRAMITE {exp['numero_expediente']} <numero>*",
                )
                self.db.actualizar_metadata(
                    exp["id"], {"apt_tramite_solicitado": True},
                    actor=f"workflow.{self.tipo_plano}",
                )
            return

        # ── sub-paso A: subir archivos al portal ──────────────────────────
        if not meta.get("apt_r1_archivos_subidos"):
            correcciones_count = int(meta.get("apt_correcciones_count", 0))
            if correcciones_count > 0:
                desc_subir = (
                    f"Re-subida #{correcciones_count} a APT tras correcciones. "
                    "⚠️ Asegúrese de que el plano *anverso firmado y corregido* "
                    "está en la carpeta 03_APT_R1\\SUBIR. ¿Continuamos?"
                )
            else:
                desc_subir = (
                    "Vamos a subir los archivos del plano al portal APT "
                    "(Catastro Nacional) para la primera ronda. ¿Continuamos?"
                )
            if not self._confirmacion_lista(
                exp, "subir_apt",
                descripcion=desc_subir,
            ):
                return

            archivos = self._archivos_de(exp["id"], fase="apt_ronda1")
            if not archivos:
                # Fallback: buscar en campo
                archivos = self._archivos_de(exp["id"], fase="campo")
            if not archivos:
                raise WorkflowError(
                    f"no hay archivos para subir a APT R1 (exp={exp['id']})"
                )

            # Clasificar por tipo_archivo
            def _ruta(tipo: str) -> Optional[Path]:
                a = next((x for x in archivos if x.get("tipo_archivo") == tipo), None)
                if a and a.get("ruta_local"):
                    p = Path(a["ruta_local"])
                    return p if p.exists() else None
                return None

            anverso   = _ruta("anverso") or Path(archivos[0]["ruta_local"])
            entero    = _ruta("entero")
            derrotero = _ruta("derrotero") or _ruta("shape")

            try:
                resultado = self._apt().presentar_r1(
                    exp["id"], anverso,
                    archivo_entero=entero,
                    archivo_derrotero=derrotero,
                )
            except Exception as exc:
                from src.agents.apt_agent import APTSesionRequeridaError  # noqa: PLC0415
                self.log.error(
                    "error subiendo archivos APT R1 exp=%s: %s", exp["id"], exc
                )
                if isinstance(exc, APTSesionRequeridaError):
                    # Expirar confirmación para que no reintente infinitamente
                    self.db.conn.execute(
                        "UPDATE acciones_pendientes SET estado='expirada' "
                        "WHERE expediente_id=? AND tipo_accion='subir_apt' AND estado='confirmada'",
                        (exp["id"],),
                    )
                    self.db.conn.commit()
                    self._notificar_topografo(
                        exp,
                        f"🔐 *{exp['numero_expediente']}* — El portal APT requiere *Firma Digital BCR*.\n\n"
                        "Envíe el comando *APT SESION* para autenticarse.\n"
                        "Cuando el bot confirme el login, el plano se enviará automáticamente.",
                    )
                else:
                    self._notificar_topografo(
                        exp, f"❌ Error al subir archivos a APT: {exc}"
                    )
                return

            if not resultado["listo_para_fd"]:
                errores = "; ".join(resultado.get("errores", []))
                self._notificar_topografo(
                    exp,
                    f"Algunos archivos no se pudieron subir a APT: {errores}. "
                    "Por favor revise y reenvíe.",
                )
                return

            # Guardar tramite y bandera en metadata
            tramite = resultado.get("tramite", "")
            self.db.actualizar_metadata(
                exp["id"],
                {
                    "apt_r1_archivos_subidos": True,
                    "apt_tramite": tramite,
                    "apt_r1_archivos": resultado.get("archivos_subidos", []),
                },
                actor=f"workflow.{self.tipo_plano}",
            )
            self._notificar_topografo(
                exp,
                f"Archivos subidos al portal APT (trámite #{tramite}). "
                "Para finalizar la presentación ingrese al portal y firme "
                "con su token de Firma Digital. "
                "Cuando termine, responda *SI* a este mensaje.",
            )
            # Re-leer exp para tener metadata actualizada
            exp = self.db.obtener_expediente(exp["id"])

        # ── sub-paso B: esperar confirmación de Firma Digital ─────────────
        meta = json.loads(exp.get("metadata_json") or "{}")
        tramite = meta.get("apt_tramite", "")
        correcciones_count = int(meta.get("apt_correcciones_count", 0))
        if correcciones_count > 0:
            desc_fd = (
                f"¿Confirmás que ya firmaste digitalmente el trámite "
                f"#{tramite} (resubmisión #{correcciones_count}) en el portal APT? "
                "Recuerda que debes firmar el anverso corregido en el portal."
            )
        else:
            desc_fd = (
                f"¿Confirmás que ya firmaste digitalmente el trámite #{tramite} "
                "en el portal APT?"
            )

        import os as _os2  # noqa: PLC0415
        if _simular_apt_activo():
            # DEV MODE: simular Firma Digital sin interacción del operador
            self.log.info("DEV_MODE: simulando firma digital R1 para exp=%s", exp["id"])
            self._set_estado(
                exp["id"], Estado.PRESENTADO_APT_R1,
                detalles=f"Firma Digital simulada (DEV_MODE) — trámite #{tramite}",
            )
            self._notificar_topografo(
                exp,
                f"🧪 *[MODO PRUEBA]* {exp['numero_expediente']}\n"
                f"Firma Digital simulada — trámite #{tramite} presentado.\n"
                "Respuesta del Catastro esperada en ~6 días hábiles.",
            )
            return

        if not self._confirmacion_lista(
            exp, "firma_digital_r1",
            descripcion=desc_fd,
        ):
            return

        self._set_estado(
            exp["id"], Estado.PRESENTADO_APT_R1,
            detalles=f"trámite APT #{tramite} firmado y presentado",
        )
        self._notificar_topografo(
            exp,
            f"✅ Plano presentado al Catastro Nacional (trámite #{tramite}). "
            "Respuesta esperada en ~6 días hábiles.",
        )

    # paso 6: monitorear respuesta APT
    def _h_presentado_apt_r1(self, exp: dict) -> None:
        import os as _os3  # noqa: PLC0415
        if _simular_apt_activo():
            self.log.info("DEV_MODE: simulando respuesta APT R1 para exp=%s", exp["id"])
            self._notificar_topografo(
                exp,
                f"🧪 *[MODO PRUEBA]* {exp['numero_expediente']}\n"
                "Simulando respuesta del Catastro Nacional...\n"
                "En producción aquí el bot esperaría ~6 días hábiles.",
            )
            self._set_estado(exp["id"], Estado.APT_R1_RESPONDIO,
                             detalles="Respuesta APT simulada (DEV_MODE)")
            return
        estado = self._apt().consultar_estado_r1(exp["id"])
        if estado == "respondido":
            self._set_estado(exp["id"], Estado.APT_R1_RESPONDIO)
        # "pendiente" o None → seguir esperando

    # paso 7: descargar minuta + correcciones + imagen
    def _h_apt_r1_respondio(self, exp: dict) -> None:
        import os as _os4  # noqa: PLC0415
        if _simular_apt_activo():
            self.log.info("DEV_MODE: simulando descarga de minuta APT para exp=%s", exp["id"])
            # Guardar metadata de minuta simulada para que el análisis pueda continuar
            self.db.actualizar_metadata(
                exp["id"],
                {"minuta_dev_simulada": True},
                actor=f"workflow.{self.tipo_plano}",
            )
            self._set_estado(exp["id"], Estado.APT_R1_ANALIZADO,
                             detalles="Minuta simulada (DEV_MODE)")
            return
        carpeta = self._drive().folder_for(exp["id"], "apt_ronda1")
        archivos = self._apt().descargar_archivos_r1(exp["id"], carpeta)
        for ruta in archivos:
            tipo = self._inferir_tipo(ruta)
            self._drive().guardar_archivo(
                expediente_id=exp["id"],
                fase="apt_ronda1",
                archivo_origen=Path(ruta),
                tipo_archivo=tipo,
                actor=f"workflow.{self.tipo_plano}",
            )
        self._set_estado(exp["id"], Estado.APT_R1_ANALIZADO,
                         detalles=f"{len(archivos)} archivos descargados")

    def _inferir_tipo(self, ruta: Path) -> str:
        n = ruta.name.lower()
        if "minuta" in n and "imagen" in n:
            return "imagen_minuta"
        if "minuta" in n:
            return "minuta"
        if "correcc" in n:
            return "correcciones"
        return "otro"

    # paso 8: análisis con IA + branch (9a/9b/9c)
    def _h_apt_r1_analizado(self, exp: dict) -> None:
        import os as _os5  # noqa: PLC0415
        meta_actual = json.loads(exp.get("metadata_json") or "{}")
        if _simular_apt_activo() and meta_actual.get("minuta_dev_simulada"):
            self.log.info("DEV_MODE: simulando análisis de minuta (aprobado) para exp=%s", exp["id"])
            self._notificar_topografo(
                exp,
                f"🧪 *[MODO PRUEBA]* {exp['numero_expediente']}\n"
                "Análisis de minuta simulado — *Plano APROBADO* ✅\n"
                "En producción aquí se analizaría la minuta real con IA.",
            )
            self._set_estado(exp["id"], Estado.APROBADO_R1,
                             detalles="Aprobación simulada (DEV_MODE)")
            return

        archivos = self._archivos_de(exp["id"], fase="apt_ronda1")
        minuta = next((a for a in archivos if a["tipo_archivo"] == "minuta"), None)
        if not minuta:
            self.log.warning("no hay minuta para exp %s — manteniendo estado", exp["id"])
            return
        ruta = Path(minuta["ruta_local"])
        try:
            texto = ruta.read_text(encoding="utf-8", errors="replace")
        except Exception:
            self.log.exception("no se pudo leer la minuta")
            return
        # Sanitización aplica también dentro del agente, pero sanitizamos aquí
        # antes de loggear cualquier cosa derivada del texto
        texto_safe = sanitize(texto)
        self.log.info("analizando minuta de exp %s (%s chars sanitizados)",
                      exp["id"], len(texto_safe))
        analisis = self._minuta().analizar(texto_safe)

        # Persistir resultado (sin datos personales — solo banderas técnicas)
        meta = {
            "minuta_aprobado": analisis.aprobado,
            "minuta_traslapes": analisis.traslapes,
            "minuta_tipo_error": analisis.tipo_error,
            "minuta_correcciones": analisis.correcciones_solicitadas,
            "minuta_apelacion": analisis.requiere_apelacion,
            "minuta_notas": analisis.notas_aprobacion,
        }
        self.db.crear_accion_pendiente(
            expediente_id=exp["id"],
            tipo_accion="minuta_analizada",
            descripcion="resultado análisis IA",
            payload=meta,
            actor=f"workflow.{self.tipo_plano}",
        )
        # Marcar la acción de "minuta_analizada" como confirmada inmediatamente
        # (no requiere intervención del cliente)
        # En BD ya quedó como "pendiente"; resolvemos aquí mismo.
        ult = self.db.ultima_accion(
            expediente_id=exp["id"], tipo_accion="minuta_analizada"
        )
        if ult and ult["estado"] == "pendiente":
            self.db.resolver_accion(
                ult["id"], "confirmada",
                whatsapp_response="auto",
                actor=f"workflow.{self.tipo_plano}",
            )

        if analisis.traslapes:
            self._set_estado(exp["id"], Estado.APT_TRASLAPES,
                             detalles="traslapes detectados — apelación")
            self._notificar_topografo(exp,
                "Catastro reportó traslapes en su plano. Se requiere apelación. "
                "Detalles técnicos: " + analisis.descripcion_tecnica[:300])
        elif not analisis.aprobado:
            self._set_estado(exp["id"], Estado.APT_CORRECCIONES,
                             detalles=f"correcciones: {analisis.tipo_error}")
            correcciones = analisis.correcciones_solicitadas
            lineas_corr  = "\n".join(f"- {c}" for c in correcciones)
            tipo_error   = analisis.tipo_error or "observaciones"

            # ── Detectar si son errores menores de texto/datos ────────────────
            # Errores menores: número de finca, área, notas, número de cantón/provincia.
            # Son corregibles editando el DWG/PDF sin rediseñar el plano.
            _ERRORES_TEXTO = {
                "finca", "area", "área", "nota", "canton", "cantón",
                "provincia", "distrito", "numero", "número", "codigo", "código",
                "descripcion", "descripción", "texto", "cédula", "cedula",
                "fecha", "escala", "coordenada",
            }
            descripcion_lower = analisis.descripcion_tecnica.lower()
            es_error_texto = tipo_error.lower() in _ERRORES_TEXTO or any(
                kw in descripcion_lower
                for kw in ("número de finca", "número de canton", "numero de finca",
                           "area incorrecta", "área incorrecta", "nota incorrecta",
                           "numero de provincia", "número de provincia",
                           "error de digitacion", "error de digitación",
                           "dato incorrecto", "datos incorrectos")
            )

            if es_error_texto and correcciones:
                # ── Intentar auto-corrección DWG ─────────────────────────────
                corrs_struct = getattr(analisis, "correcciones_texto", [])
                resultado_dwg = self._intentar_autocorreccion_dwg(
                    exp, corrs_struct
                )

                # Construir mensaje según resultado de la corrección automática
                if resultado_dwg and resultado_dwg.modificado:
                    msg_auto = (
                        f"🔧 *Auto-corrección aplicada*\n"
                        f"{resultado_dwg.resumen}\n\n"
                        f"El archivo corregido está en su carpeta como "
                        f"*{resultado_dwg.archivo_corregido.name}*.\n"
                        f"Revíselo y si está bien, el operador confirma con "
                        f"*APROBAR {exp['numero_expediente']}*"
                    )
                    if resultado_dwg.no_encontradas:
                        faltantes = "\n".join(
                            f"  • {c}: corrija «{v}»→«{n}»"
                            for c, (v, n) in zip(
                                [c.campo for c in corrs_struct],
                                resultado_dwg.no_encontradas,
                            )
                        )
                        msg_auto += (
                            f"\n\n⚠️ Estas correcciones requieren intervención manual:\n"
                            f"{faltantes}"
                        )
                elif resultado_dwg and resultado_dwg.no_encontradas:
                    msg_auto = (
                        f"⚠️ Catastro detectó errores menores de texto ({tipo_error}):\n"
                        f"{lineas_corr}\n\n"
                        f"No pude localizar los valores en el DWG para corregir "
                        f"automáticamente. Por favor corrija el plano.\n\n"
                        f"📌 Cuando esté listo: *APROBAR {exp['numero_expediente']}*\n"
                        f"💡 Para corrección manual asistida: "
                        f"*CORREGIR {exp['numero_expediente']} <campo> <valor_incorrecto> <valor_correcto>*"
                    )
                else:
                    msg_auto = (
                        f"⚠️ Catastro detectó errores menores de texto ({tipo_error}):\n"
                        f"{lineas_corr}\n\n"
                        f"El bot intentará corrección automática en el DWG. "
                        f"Si necesita corrección manual:\n"
                        f"*CORREGIR {exp['numero_expediente']} <campo> <valor_incorrecto> <valor_correcto>*\n\n"
                        f"📌 Cuando esté listo: *APROBAR {exp['numero_expediente']}*"
                    )

                self._notificar_topografo(exp, msg_auto)
                # Marcar en metadata para rastreo
                self.db.actualizar_metadata(
                    exp["id"],
                    {
                        "correcciones_tipo": "texto",
                        "correcciones_lista": correcciones,
                        "auto_correccion_intentada": resultado_dwg is not None,
                        "auto_correccion_aplicada": bool(
                            resultado_dwg and resultado_dwg.modificado
                        ),
                    },
                    actor=f"workflow.{self.tipo_plano}",
                )
            else:
                # Error mayor o traslape — requiere intervención del topógrafo
                self._notificar_topografo(exp,
                    f"Catastro solicita correcciones ({tipo_error}):\n"
                    f"{lineas_corr or analisis.descripcion_tecnica[:300]}\n\n"
                    f"📌 Corrija el plano y el operador confirma con *APROBAR {exp['numero_expediente']}*\n"
                    f"Se le recordará periódicamente hasta que esté resuelto."
                )
        else:
            self._set_estado(exp["id"], Estado.APROBADO_R1)

    def _notificar_topografo(self, exp: dict, mensaje: str) -> None:
        """Envía al operador que creó el expediente (nunca al cliente final)."""
        telefono = self._operador_telefono(exp) or exp["telefono_cliente"]
        try:
            self._whatsapp().notificar_estado(telefono, mensaje)
        except Exception:
            self.log.exception("no se pudo notificar operador")

    def _intentar_autocorreccion_dwg(self, exp: dict, correcciones_texto) -> "ResultadoCorreccion | None":
        """Intenta corregir texto en el DWG usando ezdxf.

        Busca el DWG/DXF del expediente (tipo_archivo "dwg" o "anverso"),
        aplica las correcciones estructuradas que vienen de la minuta analizada
        y guarda el resultado como ``{stem}_corr.dxf``.

        Args:
            exp: Fila del expediente de la BD.
            correcciones_texto: Lista de ``CorreccionTexto`` extraída de
                ``MinutaAnalisis.correcciones_texto``.

        Returns:
            ``ResultadoCorreccion`` si se encontró el DWG y se intentó la
            corrección, o ``None`` si no hay DWG o no hay correcciones válidas.
        """
        from src.utils.dwg_corrector import CorreccionTexto as DwgCorr
        from src.utils.dwg_corrector import aplicar_correcciones

        # Convertir correcciones_texto (Pydantic) a dataclasses de dwg_corrector
        corrs: list[DwgCorr] = []
        for c in correcciones_texto or []:
            v_actual   = getattr(c, "valor_actual", None)
            v_correcto = getattr(c, "valor_correcto", None)
            campo      = getattr(c, "campo", "campo")
            if v_actual and v_correcto:
                corrs.append(DwgCorr(campo=campo, valor_actual=v_actual, valor_correcto=v_correcto))

        if not corrs:
            self.log.debug(
                "_intentar_autocorreccion_dwg: sin correcciones con valores "
                "visibles para exp %s", exp["id"]
            )
            return None

        # Buscar el DWG en los archivos de campo del expediente
        archivos_campo = self._archivos_de(exp["id"], fase="campo")
        archivo_dwg = next(
            (
                a for a in archivos_campo
                if a.get("tipo_archivo") in ("dwg", "anverso")
                   and Path(a.get("ruta_local", "")).suffix.lower() in (".dwg", ".dxf")
            ),
            None,
        )
        if not archivo_dwg:
            self.log.info(
                "_intentar_autocorreccion_dwg: no hay DWG/DXF en exp %s", exp["id"]
            )
            return None

        dwg_path = Path(archivo_dwg["ruta_local"])
        if not dwg_path.is_file():
            self.log.warning(
                "_intentar_autocorreccion_dwg: ruta no existe %s", dwg_path
            )
            return None

        resultado = aplicar_correcciones(dwg_path, corrs)

        if resultado.modificado and resultado.archivo_corregido:
            # Registrar el DXF corregido en BD
            try:
                import hashlib as _hashlib
                _sha = (
                    _hashlib.sha256(resultado.archivo_corregido.read_bytes()).hexdigest()
                    if resultado.archivo_corregido.is_file()
                    else "0" * 64
                )
                self.db.registrar_archivo(
                    expediente_id=exp["id"],
                    nombre_original=resultado.archivo_corregido.name,
                    tipo_archivo="dwg_corregido",
                    ruta_local=str(resultado.archivo_corregido),
                    fase="correcciones_apt",
                    sha256=_sha,
                    actor=f"workflow.{self.tipo_plano}",
                )
            except Exception:
                self.log.exception(
                    "no se pudo registrar DXF corregido en BD para exp %s", exp["id"]
                )

        return resultado

    # paso 10: verificar carta de agua según 3 tramos por área
    def _h_aprobado_r1(self, exp: dict) -> None:
        self._notificar_topografo(
            exp,
            f"🎉 Plano *aprobado* por Catastro Nacional (R1) — {exp['numero_expediente']}. "
            "Avanzando con trámites finales de inscripción.",
        )

        eval_agua = self._evaluar_carta_agua(exp)

        if eval_agua == self.CARTA_AGUA_OBLIGATORIA:
            # < 1000 m² — siempre carta de agua para visado
            self._set_estado(exp["id"], Estado.CARTA_AGUA_REQUERIDA,
                             detalles="área < 1000 m² — carta obligatoria")
            self._notificar_topografo(
                exp,
                f"💧 *Carta de agua del AyA obligatoria* para {exp['numero_expediente']}.\n\n"
                f"Motivo: área < 1000 m² — la Muni San Ramón exige carta para visado.\n"
                "Trámite: gestiónala con el cliente en el AyA y avísanos cuando llegue.",
            )
        elif eval_agua == self.CARTA_AGUA_OPCIONAL:
            # 1000-5000 m² — puede ir con nota del plano O carta
            self._set_estado(exp["id"], Estado.CARTA_AGUA_REQUERIDA,
                             detalles="área 1000-5000 m² — carta opcional (carta o nota)")
            self._notificar_topografo(
                exp,
                f"💧 *Carta de agua opcional* para {exp['numero_expediente']}.\n\n"
                "Área 1000-5000 m². Dos opciones:\n"
                "  (A) Trámite carta de agua AyA, o\n"
                "  (B) Plano lleva nota: \"LA MUNICIPALIDAD OTORGARÁ EL PERMISO "
                "DE CONSTRUCCIÓN EN ESTE PREDIO, HASTA QUE CUENTE CON LA AUTORIZACIÓN "
                "DE LOS OPERADORES...\" y uso = USO MIXTO (AGRICOLA Y RESIDENCIAL).\n"
                "Avísanos qué camino tomás.",
            )
        elif eval_agua == self.CARTA_AGUA_SOLO_NOTA:
            # > 5000 m² — NO carta, pero NOTA es obligatoria en plano
            self._set_estado(exp["id"], Estado.CARTA_AGUA_OK,
                             detalles="área > 5000 m² — sin carta, con NOTA obligatoria")
            self._notificar_topografo(
                exp,
                f"📝 *VERIFICAR NOTA EN PLANO* para {exp['numero_expediente']}.\n\n"
                "Área > 5000 m². NO requiere carta de agua del AyA.\n\n"
                "⚠️ PERO la Muni San Ramón EXIGE que el plano lleve esta nota textual:\n\n"
                f'"{self.NOTA_AGUA_MUNI_SR}"\n\n'
                "Sin esa nota, NO se otorga visado. Verifica que esté en el cajetín "
                "del plano antes de enviar a muni.",
            )
        else:
            # NO_APLICA — override del operador o tipo no segregación
            self._set_estado(exp["id"], Estado.CARTA_AGUA_OK,
                             detalles="carta de agua no aplica (override o tipo)")

    # Tipos de plano que pueden requerir carta de agua según Ley AyA
    _CARTA_AGUA_TIPOS = {"segregacion"}

    # ── Reglas de carta de agua + NOTA OBLIGATORIA (Muni San Ramón) ──────
    # Actualizado 2026-05-15: la NOTA en el plano es OBLIGATORIA cuando NO
    # se entrega carta de agua, en TODOS los tramos sin carta. Sin la nota,
    # NO se otorga visado.
    #
    # Tramos por área del nuevo lote:
    #
    #   1) Área < 1000 m² (1-999 m²)
    #      → CARTA DE AGUA OBLIGATORIA para visado
    #      → No necesita la nota (la carta basta)
    #      → Estado: carta_agua_requerida
    #
    #   2) Área 1000-5000 m² (inclusive)
    #      → CARTA DE AGUA OPCIONAL — operador elige entre:
    #        (A) Entregar carta de agua del AyA, o
    #        (B) Incluir NOTA + uso = USO MIXTO (AGRICOLA Y RESIDENCIAL)
    #      → Estado: carta_agua_requerida (operador decide después)
    #
    #   3) Área > 5000 m²
    #      → CARTA DE AGUA NO REQUERIDA
    #      → PERO la NOTA SIGUE SIENDO OBLIGATORIA en el plano
    #      → Estado: nota_agua_requerida (verificar nota antes de muni)
    #
    # TEXTO EXACTO DE LA NOTA (constante NOTA_AGUA_MUNI_SR):
    #   "LA MUNICIPALIDAD OTORGARÁ EL PERMISO DE CONSTRUCCIÓN EN ESTE
    #    PREDIO, HASTA QUE CUENTE CON LA AUTORIZACIÓN DE LOS OPERADORES
    #    DE ACUERDO A LA NORMATIVA QUE RIJA PARA CADA UNO DE ELLOS CON
    #    RESPECTO A LOS SERVICIOS PÚBLICOS INDISPENSABLES"
    #
    # El operador puede sobreescribir con `metadata.carta_agua_requerida`.

    CARTA_AGUA_LIMITE_OBLIGATORIO = 1000.0   # m² — bajo esto, carta obligatoria
    CARTA_AGUA_LIMITE_OPCIONAL    = 5000.0   # m² — entre 1000 y 5000, carta opcional

    # Texto exacto de la nota del plano (Muni San Ramón). OBLIGATORIA cuando
    # el plano no lleva carta de agua (tramos 2-opcional y 3-sin carta).
    NOTA_AGUA_MUNI_SR = (
        "LA MUNICIPALIDAD OTORGARÁ EL PERMISO DE CONSTRUCCIÓN EN ESTE "
        "PREDIO, HASTA QUE CUENTE CON LA AUTORIZACIÓN DE LOS OPERADORES "
        "DE ACUERDO A LA NORMATIVA QUE RIJA PARA CADA UNO DE ELLOS CON "
        "RESPECTO A LOS SERVICIOS PÚBLICOS INDISPENSABLES"
    )
    # Alias retro-compatible
    NOTA_AGUA_OPCIONAL = NOTA_AGUA_MUNI_SR

    # Umbrales de área (m²) por zona del Plan Regulador de San Ramón
    # (Decreto Ejecutivo 44647-MJP / Plan Regulador 2004, ProDUS-UCR).
    # Si el umbral es None → zona sin servicio AyA (pozo propio viable);
    # carta de agua no aplica.
    # Si zona no está en este dict → usar _CARTA_AGUA_AREA_UMBRAL_M2.
    _CARTA_AGUA_ZONA_UMBRALES: dict[str, float | None] = {
        # Zonas urbanas ciudad San Ramón y periferias (lote_min 130 m²)
        "SR_RESIDENCIAL":            2000.0,
        "SR_MIXTA":                  2000.0,
        "SR_COMERCIAL":              2000.0,
        "SR_INSTITUCIONAL":          2000.0,
        "SR_PERIFERIA_URBANA":       2000.0,
        "SR_COMERCIAL_INDUSTRIAL":   2000.0,
        "SR_INDUSTRIAL":             2000.0,
        "SR_CARRETERA_INTERAMERICANA": 2000.0,
        "SR_CARRETERA_CAMBRONERO":   2000.0,
        # Núcleos consolidados (lote_min 150 m²)
        "SR_NUCLEO_CONSOLIDADO":     2000.0,
        # Núcleos no consolidados (lote_min 200 m²)
        "SR_NUCLEO_NO_CONSOLIDADO":  2000.0,
        # Crecimiento largo plazo (lote_min 600 m²)
        "SR_CRECIMIENTO_LARGO_PLAZO": 2000.0,
        # Amortiguamiento ciudad 600 m (lote_min 2 000 m²)
        "SR_AMORTIGUAMIENTO_CIUDAD": 5000.0,
        # Amortiguamiento núcleos 300 m (lote_min 2 000 m²)
        "SR_AMORTIGUAMIENTO_NUCLEO": 5000.0,
        # Agropecuario (lote_min 7 000 m²) — Plan no fomenta fraccionamientos
        "SR_AGROPECUARIO":           10000.0,
        # Restricción pecuaria y recursos naturales — sin servicio AyA
        "SR_RESTRICCION_PECUARIA":   None,
        "SR_PROTECCION_RECURSOS":    None,
    }

    # Valores de retorno para _evaluar_carta_agua
    CARTA_AGUA_OBLIGATORIA = "obligatoria"   # < 1000 m² — carta obligatoria
    CARTA_AGUA_OPCIONAL    = "opcional"      # 1000-5000 m² — carta OR nota
    CARTA_AGUA_SOLO_NOTA   = "solo_nota"     # > 5000 m² — sin carta pero CON nota OBLIGATORIA
    CARTA_AGUA_NO_APLICA   = "no_aplica"     # tipo no segregación o override

    def _evaluar_carta_agua(self, exp: dict) -> str:
        """Devuelve uno de: 'obligatoria', 'opcional', 'no_aplica'.

        Regla operativa Muni San Ramón (operador 2026-05-15):
          - Área < 1000 m² (1-999) → OBLIGATORIA siempre
          - Área 1000-5000 m²      → OPCIONAL (carta o nota del plano)
          - Área > 5000 m²         → NO APLICA

        El operador puede sobreescribir con metadata:
          - carta_agua_requerida: true  → fuerza obligatoria
          - carta_agua_requerida: false → fuerza no_aplica
          - carta_agua_opcional:  true  → fuerza opcional
        """
        meta = json.loads(exp.get("metadata_json") or "{}")

        # Override explícito del operador tiene prioridad
        if meta.get("carta_agua_opcional"):
            return self.CARTA_AGUA_OPCIONAL
        if "carta_agua_requerida" in meta:
            val = bool(meta["carta_agua_requerida"])
            return self.CARTA_AGUA_OBLIGATORIA if val else self.CARTA_AGUA_NO_APLICA

        # Solo segregación entra en estas reglas
        tipo = exp.get("tipo_plano", "")
        if tipo not in self._CARTA_AGUA_TIPOS:
            return self.CARTA_AGUA_NO_APLICA

        # Determinar área del lote (m²). Buscar en varios sitios.
        area = (
            meta.get("area_m2")
            or meta.get("area_real")
            or (meta.get("datos_apt", {}).get("plano", {}).get("area_real"))
        )
        if area is None:
            # Sin área — por precaución, opcional (operador decide)
            return self.CARTA_AGUA_OPCIONAL
        try:
            area_f = float(str(area).replace(",", ""))
        except (TypeError, ValueError):
            return self.CARTA_AGUA_OPCIONAL

        # Aplicar los 3 tramos
        if area_f < self.CARTA_AGUA_LIMITE_OBLIGATORIO:
            return self.CARTA_AGUA_OBLIGATORIA
        if area_f <= self.CARTA_AGUA_LIMITE_OPCIONAL:
            return self.CARTA_AGUA_OPCIONAL
        # > 5000 m²: no carta pero NOTA obligatoria
        return self.CARTA_AGUA_SOLO_NOTA

    def _requiere_carta_agua(self, exp: dict) -> bool:
        """Mantiene compatibilidad con código existente.

        True solo si la carta es OBLIGATORIA. Si es OPCIONAL devuelve False
        (porque el operador decide; no bloquea el flujo automático).
        """
        return self._evaluar_carta_agua(exp) == self.CARTA_AGUA_OBLIGATORIA

    # paso 10 → divergencia entre tipos
    def _h_carta_agua_ok(self, exp: dict) -> None:
        siguiente = self._post_carta_agua_estado()
        self._set_estado(exp["id"], siguiente,
                         detalles="post-carta-agua")

    @abstractmethod
    def _post_carta_agua_estado(self) -> Estado:
        """A qué estado pasa cada workflow después de carta_agua_ok."""

    # ------------------------------------------------------------------
    # municipal handlers (sólo segr / div alcanzan estos estados)
    # ------------------------------------------------------------------

    # paso 11: preparar paquete + paso 12: enviar formulario
    def _h_listo_paquete_muni(self, exp: dict) -> None:
        import os as _os6
        if _simular_apt_activo():
            self.log.info(
                "DEV_MODE: simulando envío formulario municipal para exp=%s", exp["id"]
            )
            self._notificar_topografo(
                exp,
                f"🧪 [MODO PRUEBA] {exp['numero_expediente']} — Formulario Municipalidad simulado. "
                "En producción: se ensambla PDF, se envía instrucciones para Google Forms y se espera visado.",
            )
            self._set_estado(
                exp["id"], Estado.FORMULARIO_MUNI_ENVIADO,
                detalles="formulario muni simulado (DEV_MODE)",
            )
            return

        meta = json.loads(exp.get("metadata_json") or "{}")

        # ── sub-paso 0: ensamblar PDF para municipalidad ──────────────────────
        # Combina: imagenminuta + minuta + plano corregido + carta_agua + croquis
        # El PDF resultante se nombra {numero_minuta}.pdf y va en la carpeta del plano.
        if not meta.get("muni_pdf_ensamblado"):
            archivos_apt = self._archivos_de(exp["id"], fase="apt_ronda1") + \
                           self._archivos_de(exp["id"], fase="campo")
            archivos_campo = self._archivos_de(exp["id"], fase="campo")

            def _ruta_tipo(tipo: str, fuentes: list) -> Optional[Path]:
                for a in fuentes:
                    if a.get("tipo_archivo") == tipo and a.get("ruta_local"):
                        p = Path(a["ruta_local"])
                        if p.is_file():
                            return p
                return None

            imagenminuta = _ruta_tipo("imagen_minuta", archivos_apt)
            plano_corr   = (
                _ruta_tipo("amberso", archivos_apt)  # plano corregido = amberso.pdf
                or _ruta_tipo("anverso", archivos_apt)
            )
            minuta_pdf   = _ruta_tipo("minuta_apt", archivos_apt)
            carta_agua   = _ruta_tipo("carta_agua", archivos_apt + archivos_campo)
            croquis      = _ruta_tipo("croquis", archivos_apt + archivos_campo)

            if imagenminuta and plano_corr:
                numero_minuta = meta.get("apt_tramite", "") or exp["numero_expediente"]
                try:
                    pdf_muni = self._muni().ensamblar_pdf_muni(
                        imagenminuta=imagenminuta,
                        minuta=minuta_pdf,
                        plano=plano_corr,
                        carta_agua=carta_agua,
                        croquis=croquis,
                        numero_minuta=numero_minuta,
                        dest_dir=plano_corr.parent,
                    )
                    import hashlib as _hl
                    sha = _hl.sha256(pdf_muni.read_bytes()).hexdigest()
                    self.db.registrar_archivo(
                        expediente_id=exp["id"],
                        fase="municipalidad",
                        tipo_archivo="documento_muni",
                        nombre_original=pdf_muni.name,
                        sha256=sha,
                        ruta_local=str(pdf_muni),
                        tamano_bytes=pdf_muni.stat().st_size,
                        actor=f"workflow.{self.tipo_plano}",
                    )
                    self.db.actualizar_metadata(
                        exp["id"],
                        {
                            "muni_pdf_ensamblado": True,
                            "muni_pdf_path": str(pdf_muni),
                            "muni_pdf_nombre": pdf_muni.name,
                        },
                        actor=f"workflow.{self.tipo_plano}",
                    )
                    self.log.info("PDF municipal ensamblado: %s", pdf_muni.name)
                except Exception:
                    self.log.exception(
                        "error ensamblando PDF municipal para exp=%s", exp["id"]
                    )
                    # No bloquear el flujo si falla el ensamblado
                # Re-leer metadata actualizada
                exp = self.db.obtener_expediente(exp["id"])
                meta = json.loads(exp.get("metadata_json") or "{}")
            else:
                self.log.warning(
                    "PDF municipal: faltan imagenminuta o plano para exp=%s — omitiendo",
                    exp["id"],
                )

        # ── sub-paso A: generar URL del formulario y enviarla al topógrafo ──
        if not meta.get("muni_url_enviada"):
            if not self._confirmacion_lista(
                exp, "enviar_formulario_muni",
                descripcion=(
                    "Vamos a enviar el formulario a la Municipalidad de San Ramón "
                    "para iniciar el visado. ¿Continuamos?"
                ),
            ):
                return

            try:
                self._muni().enviar_formulario(exp["id"])
                instrucciones = self._muni().generar_instrucciones_formulario(exp["id"])
            except Exception as exc:
                self.log.error(
                    "error generando formulario muni para exp=%s: %s", exp["id"], exc
                )
                self._notificar_topografo(
                    exp, f"Error al generar formulario municipal: {exc}"
                )
                return

            # Añadir referencia al PDF ensamblado en las instrucciones
            meta_actual = json.loads(
                (self.db.obtener_expediente(exp["id"]) or {}).get("metadata_json") or "{}"
            )
            pdf_nombre = meta_actual.get("muni_pdf_nombre", "")
            if pdf_nombre:
                instrucciones += (
                    f"\n\n📄 *PDF para DOCUMENTOS ya ensamblado:* `{pdf_nombre}`\n"
                    f"Encuéntrelo en la carpeta del plano."
                )
            self._notificar_topografo(exp, instrucciones)
            self.db.actualizar_metadata(
                exp["id"],
                {"muni_url_enviada": True},
                actor=f"workflow.{self.tipo_plano}",
            )
            # Re-leer exp para tener metadata actualizada
            exp = self.db.obtener_expediente(exp["id"])

        # ── sub-paso B: esperar confirmación de envío manual del formulario ──
        if not self._confirmacion_lista(
            exp, "formulario_muni_completado",
            descripcion=(
                "¿Confirmás que ya enviaste el formulario a la Municipalidad de "
                "San Ramón (subiste los archivos y presionaste Enviar)?"
            ),
        ):
            return

        self._set_estado(
            exp["id"], Estado.FORMULARIO_MUNI_ENVIADO,
            detalles="formulario municipalidad enviado por topógrafo",
        )

    # paso 13: monitor correo municipalidad
    def _h_formulario_muni_enviado(self, exp: dict) -> None:
        import os as _os7
        if _simular_apt_activo():
            self.log.info(
                "DEV_MODE: simulando aviso impuestos municipalidad para exp=%s", exp["id"]
            )
            self._set_estado(
                exp["id"], Estado.AVISO_MUNI_RECIBIDO,
                detalles="aviso muni simulado ₡0 (DEV_MODE)",
            )
            return

        aviso = self._muni().consultar_aviso_impuestos(exp["id"])
        if aviso is None:
            return  # esperando aviso
        meta_json = self.db.obtener_expediente(exp["id"]).get("metadata_json") or "{}"
        meta = json.loads(meta_json)
        meta["aviso_muni"] = aviso
        # Registramos el aviso recibido (solo info — no es acción confirmable)
        self.db.crear_accion_pendiente(
            expediente_id=exp["id"],
            tipo_accion="aviso_muni_recibido",
            descripcion=f"impuestos pendientes: ₡{aviso.get('monto', '?')}",
            payload=aviso,
            actor=f"workflow.{self.tipo_plano}",
        )
        ult = self.db.ultima_accion(
            expediente_id=exp["id"], tipo_accion="aviso_muni_recibido"
        )
        if ult and ult["estado"] == "pendiente":
            self.db.resolver_accion(
                ult["id"], "confirmada",
                whatsapp_response="auto",
                actor=f"workflow.{self.tipo_plano}",
            )
        self._set_estado(exp["id"], Estado.AVISO_MUNI_RECIBIDO,
                         detalles=f"impuestos: ₡{aviso.get('monto', '?')}")

    # paso 14a: cliente paga (confirmación WhatsApp)
    def _h_aviso_muni_recibido(self, exp: dict) -> None:
        import os as _os8
        if _simular_apt_activo():
            self.log.info(
                "DEV_MODE: simulando pago impuestos municipalidad para exp=%s", exp["id"]
            )
            self._set_estado(
                exp["id"], Estado.PAGO_MUNI_CLIENTE_CONFIRMADO,
                detalles="pago muni simulado (DEV_MODE)",
            )
            return

        # Recuperar el monto del aviso anterior si existe
        ult_aviso = self.db.ultima_accion(
            expediente_id=exp["id"], tipo_accion="aviso_muni_recibido"
        )
        monto = "(consulte el aviso oficial)"
        if ult_aviso:
            payload = json.loads(ult_aviso.get("payload_json") or "{}")
            if "monto" in payload:
                monto = f"₡{payload['monto']}"
        if self._confirmacion_lista(
            exp, "pago_muni_cliente",
            descripcion=(
                f"La Municipalidad de San Ramón solicita el pago de impuestos "
                f"({monto}) antes de visar su plano. "
                f"Responda SI cuando haya realizado el pago."
            ),
            ttl_h=14 * 24,  # 14 días
        ):
            self._set_estado(exp["id"], Estado.PAGO_MUNI_CLIENTE_CONFIRMADO)

    # paso 14b: enviar correo a muni con comprobante
    def _h_pago_muni_cliente_confirmado(self, exp: dict) -> None:
        import os as _os9
        if _simular_apt_activo():
            self.log.info(
                "DEV_MODE: simulando envío correo comprobante pago muni para exp=%s", exp["id"]
            )
            self._set_estado(
                exp["id"], Estado.CORREO_MUNI_ENVIADO,
                detalles="correo muni simulado (DEV_MODE)",
            )
            return

        if not self._confirmacion_lista(
            exp, "enviar_correo_muni",
            descripcion=("Vamos a enviar el comprobante de su pago a la "
                         "Municipalidad para que continúen con el visado. "
                         "¿Continuamos?"),
        ):
            return
        ref = self._muni().enviar_correo_pago(exp["id"])
        self._set_estado(exp["id"], Estado.CORREO_MUNI_ENVIADO,
                         detalles=f"correo muni #{ref}")

    # paso 15: monitor visado municipal
    def _h_correo_muni_enviado(self, exp: dict) -> None:
        import os as _os10
        if _simular_apt_activo():
            self.log.info(
                "DEV_MODE: simulando visado municipal aprobado para exp=%s", exp["id"]
            )
            self._notificar_topografo(
                exp,
                f"🧪 [MODO PRUEBA] {exp['numero_expediente']} — Visado Municipal simulado como APROBADO ✅",
            )
            self._set_estado(
                exp["id"], Estado.VISADO_APROBADO,
                detalles="visado muni simulado aprobado (DEV_MODE)",
            )
            return

        resultado = self._muni().consultar_visado(exp["id"])
        if resultado == "aprobado":
            self._set_estado(exp["id"], Estado.VISADO_APROBADO)
        elif resultado == "rechazado":
            self._set_estado(exp["id"], Estado.VISADO_RECHAZADO,
                             detalles="muni rechazó visado")
            self._notificar_topografo(
                exp,
                f"❌ Municipalidad rechazó el visado de {exp['numero_expediente']}. "
                "Revise los detalles y coordine corrección.",
            )

    def _h_visado_aprobado(self, exp: dict) -> None:
        self._set_estado(exp["id"], Estado.LISTO_APT_R2,
                         detalles="visado muni recibido")

    # ------------------------------------------------------------------
    # APT R2 + inscripción (segregacion/reunion_de_fincas/fincas_completas)
    # ------------------------------------------------------------------

    def _h_listo_apt_r2(self, exp: dict) -> None:
        import os as _os11
        if _simular_apt_activo():
            self.log.info(
                "DEV_MODE: simulando subida APT R2 + firma digital para exp=%s", exp["id"]
            )
            self.db.actualizar_metadata(
                exp["id"],
                {"apt_r2_archivos_subidos": True},
                actor=f"workflow.{self.tipo_plano}",
            )
            self._notificar_topografo(
                exp,
                f"🧪 [MODO PRUEBA] {exp['numero_expediente']} — APT Ronda 2 simulada. "
                "Archivos subidos y firma digital confirmada.",
            )
            self._set_estado(
                exp["id"], Estado.PRESENTADO_APT_R2,
                detalles="APT R2 simulado (DEV_MODE)",
            )
            return

        meta = json.loads(exp.get("metadata_json") or "{}")

        # ── sub-paso A: subir archivos R2 ─────────────────────────────────
        if not meta.get("apt_r2_archivos_subidos"):
            if not self._confirmacion_lista(
                exp, "subir_apt_r2",
                descripcion=(
                    "Vamos a subir los archivos para la segunda ronda APT "
                    "(inscripción final). ¿Continuamos?"
                ),
            ):
                return

            archivos_campo = (
                self._archivos_de(exp["id"], fase="apt_ronda1")
                or self._archivos_de(exp["id"], fase="campo")
            )
            archivos_muni = self._archivos_de(exp["id"], fase="municipalidad")

            if not archivos_campo:
                raise WorkflowError(f"sin archivos para R2 (exp={exp['id']})")

            # Para R2: preferir amberso.pdf (plano corregido post-municipalidad)
            # Si no hay, usar el anverso original
            anverso_file = next(
                (a for a in archivos_campo if a.get("tipo_archivo") == "amberso"),
                None,
            ) or archivos_campo[0]
            anverso = Path(anverso_file["ruta_local"])

            # Visado municipal
            visado = next(
                (Path(a["ruta_local"]) for a in archivos_muni
                 if a.get("tipo_archivo") in ("visado", "visado_municipal")),
                None,
            )
            # Fallback: buscar visado.pdf en archivos de campo
            if not visado:
                visado = next(
                    (Path(a["ruta_local"]) for a in archivos_campo
                     if a.get("tipo_archivo") == "visado"),
                    None,
                )

            try:
                resultado = self._apt().presentar_r2(
                    exp["id"], anverso,
                    archivo_entero=None,
                    archivo_visado=visado,
                )
            except Exception as exc:
                self.log.error(
                    "error subiendo archivos APT R2 exp=%s: %s", exp["id"], exc
                )
                self._notificar_topografo(
                    exp, f"Error al subir archivos a APT R2: {exc}"
                )
                return

            if not resultado["listo_para_fd"]:
                errores = "; ".join(resultado.get("errores", []))
                self._notificar_topografo(
                    exp,
                    f"Algunos archivos R2 no se pudieron subir a APT: {errores}.",
                )
                return

            tramite = resultado.get("tramite", meta.get("apt_tramite", ""))
            self.db.actualizar_metadata(
                exp["id"],
                {
                    "apt_r2_archivos_subidos": True,
                    "apt_tramite": tramite,
                },
                actor=f"workflow.{self.tipo_plano}",
            )
            self._notificar_topografo(
                exp,
                f"Archivos R2 subidos al portal APT (trámite #{tramite}). "
                "Por favor firme con su token de Firma Digital y responda *SI*.",
            )
            exp = self.db.obtener_expediente(exp["id"])

        # ── sub-paso B: esperar FD R2 ─────────────────────────────────────
        meta = json.loads(exp.get("metadata_json") or "{}")
        tramite = meta.get("apt_tramite", "")

        if not self._confirmacion_lista(
            exp, "firma_digital_r2",
            descripcion=(
                f"¿Confirmás que ya firmaste digitalmente la segunda ronda "
                f"del trámite #{tramite} en el portal APT?"
            ),
        ):
            return

        self._set_estado(
            exp["id"], Estado.PRESENTADO_APT_R2,
            detalles=f"trámite APT R2 #{tramite} firmado",
        )

    def _h_presentado_apt_r2(self, exp: dict) -> None:
        import os as _os12
        if _simular_apt_activo():
            self.log.info(
                "DEV_MODE: simulando respuesta APT R2 (inscrito) para exp=%s", exp["id"]
            )
            self._set_estado(exp["id"], Estado.INSCRITO,
                             detalles="APT R2 respuesta simulada (DEV_MODE)")
            return

        estado = self._apt().consultar_estado_r2(exp["id"])
        if estado == "inscrito":
            self._set_estado(exp["id"], Estado.INSCRITO)

    def _h_inscrito(self, exp: dict) -> None:
        import os as _os13
        if _simular_apt_activo():
            self.log.info(
                "DEV_MODE: simulando descarga plano inscrito para exp=%s", exp["id"]
            )
            self._notificar_topografo(
                exp,
                f"🧪 [MODO PRUEBA] {exp['numero_expediente']} — Plano INSCRITO en Catastro Nacional ✅\n"
                "En producción: se descargan los archivos desde APT y se envían al cliente.",
            )
            self._set_estado(exp["id"], Estado.INSCRITO_DESCARGADO,
                             detalles="descarga plano inscrito simulada (DEV_MODE)")
            return

        carpeta = self._drive().folder_for(exp["id"], "inscrito")
        archivos = self._apt().descargar_inscrito(exp["id"], carpeta)
        for ruta in archivos:
            self._drive().guardar_archivo(
                expediente_id=exp["id"],
                fase="inscrito",
                archivo_origen=Path(ruta),
                tipo_archivo="plano_inscrito",
                actor=f"workflow.{self.tipo_plano}",
            )
        self._set_estado(exp["id"], Estado.INSCRITO_DESCARGADO,
                         detalles=f"{len(archivos)} archivos del plano inscrito")

    def _h_inscrito_descargado(self, exp: dict) -> None:
        import os as _os14
        if _simular_apt_activo():
            self.log.info(
                "DEV_MODE: simulando entrega plano inscrito al cliente para exp=%s", exp["id"]
            )
            self._notificar_topografo(
                exp,
                f"🧪 [MODO PRUEBA] {exp['numero_expediente']} — ENTREGADO al cliente ✅\n"
                "¡Prueba guiada completa! Todos los estados del flujo segregacion fueron simulados exitosamente.",
            )
            self._set_estado(exp["id"], Estado.ENTREGADO,
                             detalles="entrega simulada al cliente (DEV_MODE)")
            self._agendar_recordatorio_vencimiento(exp)
            return

        # paso 19: enviar plano inscrito al cliente por WhatsApp
        archivos = self._archivos_de(exp["id"], fase="inscrito")
        nombres = ", ".join(a["nombre_original"] for a in archivos) or "(sin nombre)"

        telefono_dest = self._operador_telefono(exp) or exp["telefono_cliente"]

        # Mensaje de aviso previo
        self._notificar_topografo(
            exp,
            f"🎉 Plano inscrito en Catastro Nacional — {exp['numero_expediente']}! "
            f"Documentos: {nombres}. Enviando archivos...",
        )

        # Enviar cada archivo — el original NO se toca (solo lectura)
        enviados = 0
        for a in archivos:
            ruta = Path(a.get("ruta_local") or "")
            if not ruta.is_file():
                self.log.warning(
                    "archivo inscrito no encontrado en disco: %s", ruta
                )
                continue
            try:
                self._whatsapp().enviar_archivo(
                    telefono_dest,
                    ruta,
                    caption=a["nombre_original"],
                )
                enviados += 1
            except Exception:
                self.log.exception(
                    "no se pudo enviar archivo inscrito %s a %s",
                    ruta.name, telefono_dest,
                )

        if enviados == 0 and archivos:
            self._notificar_topografo(
                exp,
                "⚠️ No se pudieron enviar los archivos del plano inscrito "
                "por WhatsApp. Por favor envíelos manualmente al cliente.",
            )

        self._set_estado(
            exp["id"], Estado.ENTREGADO,
            detalles=f"entregado al cliente ({enviados} archivos enviados)",
        )
        # paso 20: agendar recordatorio a 10 meses
        self._agendar_recordatorio_vencimiento(exp)

    def _agendar_recordatorio_vencimiento(self, exp: dict) -> None:
        recordatorio = (_now_utc() + timedelta(days=300)).isoformat()
        self.db.crear_accion_pendiente(
            expediente_id=exp["id"],
            tipo_accion="recordatorio_vencimiento_plano",
            descripcion=(
                "Aviso: su plano fue inscrito hace 10 meses. Vence en 2 meses. "
                "Le recomendamos contactar a un notario para usarlo antes."
            ),
            expira_en=recordatorio,
            actor=f"workflow.{self.tipo_plano}",
        )
        self.log.info("recordatorio agendado para exp %s en 10 meses", exp["id"])
