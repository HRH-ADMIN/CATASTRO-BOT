"""Orchestrator: coordina agentes + workflows.

Responsabilidades en cada `tick()`:
  1. Procesar respuestas WhatsApp pendientes (resuelve acciones).
  2. Expirar acciones cuyo TTL venció.
  3. Enviar recordatorios de vencimiento de plano (10 meses post-inscripción).
  4. Para cada expediente activo sin acciones pendientes, invocar el
     workflow correspondiente para que avance al siguiente estado.

Los workflows se registran con `register_workflow()`. El orchestrator no
ejecuta nada relacionado a un tipo_plano que no tenga workflow registrado.
"""
from __future__ import annotations

import json
from typing import Optional

from src.agents.whatsapp_agent import WhatsAppAgent
from src.utils.logger import get_logger
from src.workflows.base_workflow import BaseWorkflow


class Orchestrator:
    name = "orchestrator"

    def __init__(self, db, credentials, *, logger=None):
        self.db = db
        self.credentials = credentials
        self.log = logger or get_logger("orchestrator")
        self.whatsapp = WhatsAppAgent(db, credentials)
        self._workflows: dict[str, BaseWorkflow] = {}

    def register_workflow(self, workflow: BaseWorkflow) -> None:
        if not workflow.tipo_plano:
            raise ValueError("workflow sin tipo_plano definido")
        self._workflows[workflow.tipo_plano] = workflow
        self.log.info("workflow registrado para tipo=%s", workflow.tipo_plano)

    def _enviar_recordatorios(self) -> int:
        """Envía por WhatsApp los recordatorios de vencimiento de planos.

        Busca acciones de tipo `recordatorio_vencimiento_plano` en estado
        `expirada` (TTL cumplido) que aún no fueron notificadas al cliente.
        Usa metadata `recordatorio_vencimiento_enviado` como bandera para
        garantizar idempotencia entre ticks.
        """
        enviados = 0
        acciones = self.db.buscar_acciones(
            tipo_accion="recordatorio_vencimiento_plano",
            estado="expirada",
        )
        for accion in acciones:
            eid = accion["expediente_id"]
            exp = self.db.obtener_expediente(eid)
            if not exp:
                continue
            meta = json.loads(exp.get("metadata_json") or "{}")
            if meta.get("recordatorio_vencimiento_enviado"):
                continue  # ya notificado en un tick anterior
            try:
                self.whatsapp.notificar_estado(
                    exp["telefono_cliente"],
                    accion["descripcion"],
                )
                self.db.actualizar_metadata(
                    eid,
                    {"recordatorio_vencimiento_enviado": True},
                    actor="orchestrator",
                )
                enviados += 1
                self.log.info(
                    "recordatorio vencimiento enviado para exp %s", eid
                )
            except Exception:
                self.log.exception(
                    "error enviando recordatorio para exp %s", eid
                )
        return enviados

    def tick(self) -> dict:
        """Ejecuta una pasada de coordinación. Devuelve estadísticas."""
        stats = {
            "whatsapp_resueltas": 0,
            "expiradas": 0,
            "recordatorios": 0,
            "avanzadas": 0,
            "fallidas": 0,
            "esperando_confirmacion": 0,
            "sin_workflow": 0,
        }

        try:
            stats["whatsapp_resueltas"] = self.whatsapp.procesar_respuestas()
        except Exception:
            self.log.exception("error en procesar_respuestas")
        try:
            stats["expiradas"] = self.whatsapp.expirar_acciones_vencidas()
        except Exception:
            self.log.exception("error en expirar_acciones_vencidas")
        try:
            stats["recordatorios"] = self._enviar_recordatorios()
        except Exception:
            self.log.exception("error en _enviar_recordatorios")

        for exp in self.db.listar_expedientes(completados=False):
            if exp["cancelado"]:
                continue
            if self.db.acciones_pendientes(expediente_id=exp["id"]):
                stats["esperando_confirmacion"] += 1
                continue
            wf = self._workflows.get(exp["tipo_plano"])
            if wf is None:
                stats["sin_workflow"] += 1
                continue
            try:
                wf.avanzar(exp["id"])
                stats["avanzadas"] += 1
            except NotImplementedError:
                # Workflow registrado pero todavía no implementa la siguiente
                # transición — ok mientras se desarrolla.
                pass
            except Exception:
                self.log.exception(
                    "workflow %s falló para expediente %s",
                    exp["tipo_plano"], exp["id"],
                )
                stats["fallidas"] += 1

        self.log.info("tick %s", stats)
        return stats
