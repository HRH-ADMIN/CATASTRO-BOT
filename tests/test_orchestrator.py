"""Tests para Orchestrator.

Cubre:
  - tick(): delega a whatsapp.procesar_respuestas y expirar_acciones_vencidas
  - tick(): avanza workflows de expedientes sin acciones pendientes
  - tick(): salta expedientes con acciones pendientes
  - tick(): registra fallidas si el workflow lanza
  - tick(): salta expedientes sin workflow registrado
  - _enviar_recordatorios(): envía WhatsApp para recordatorios expirados
  - _enviar_recordatorios(): idempotente (no reenvía si ya fue enviado)
  - tick(): incluye recordatorios en stats
"""
from __future__ import annotations

import contextlib
import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("win32cred", reason="pywin32 requerido (solo Windows)")

from src.agents.orchestrator import Orchestrator  # noqa: E402
from src.core.database import Database  # noqa: E402
from src.models.estado import Estado  # noqa: E402
from src.models.plano import TipoPlano  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Infraestructura
# ─────────────────────────────────────────────────────────────────────────────

class TestDatabase(Database):
    @contextlib.contextmanager
    def connect(self):
        conn = sqlite3.connect(str(self.path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
        finally:
            conn.close()


class FakeCredentialManager:
    def __init__(self):
        self._db_key = secrets.token_bytes(32)

    def get_or_create_db_key(self) -> bytes:
        return self._db_key

    def get_db_key(self) -> bytes:
        return self._db_key

    def get_operators(self) -> set[str]:
        return set()

    def is_operator(self, phone: str) -> bool:
        return False

    def get_green_api(self) -> dict:
        return {"instance_id": "12345", "token": "fake-token"}


@pytest.fixture
def db(tmp_path):
    cm = FakeCredentialManager()
    database = TestDatabase(path=tmp_path / "test.db", credentials=cm)
    database.initialize_schema()
    return database


@pytest.fixture
def orchestrator(db):
    cm = FakeCredentialManager()
    orc = Orchestrator(db, cm)
    # Reemplazar WhatsAppAgent con mock para evitar llamadas HTTP reales
    orc.whatsapp = MagicMock()
    orc.whatsapp.procesar_respuestas.return_value = 0
    orc.whatsapp.expirar_acciones_vencidas.return_value = 0
    return orc


def _crear_exp(
    db,
    estado: Estado = Estado.RECIBIDO,
    *,
    tipo: str = TipoPlano.SEGREGACION.value,
    meta: dict | None = None,
) -> str:
    eid = db.crear_expediente(
        numero_expediente="EXP-TEST-0001",
        tipo_plano=tipo,
        nombre_topografo="Topo Test",
        telefono_cliente="50688880001",
        nombre_cliente="Cliente Test",
        cedula_topografo="1-0001-0001",
        metadata=meta or {},
        actor="test",
    )
    if estado != Estado.RECIBIDO:
        db.cambiar_estado(eid, estado.value, actor="test", detalles="setup")
    return eid


def _meta(db, eid: str) -> dict:
    return json.loads(db.obtener_expediente(eid).get("metadata_json") or "{}")


# ─────────────────────────────────────────────────────────────────────────────
# tick(): coordinación básica
# ─────────────────────────────────────────────────────────────────────────────

class TestTickCoordinacion:

    def test_llama_procesar_respuestas(self, db, orchestrator):
        orchestrator.tick()
        orchestrator.whatsapp.procesar_respuestas.assert_called_once()

    def test_llama_expirar_acciones_vencidas(self, db, orchestrator):
        orchestrator.tick()
        orchestrator.whatsapp.expirar_acciones_vencidas.assert_called_once()

    def test_devuelve_stats_con_claves_esperadas(self, db, orchestrator):
        stats = orchestrator.tick()
        for clave in (
            "whatsapp_resueltas", "expiradas", "recordatorios",
            "avanzadas", "fallidas", "esperando_confirmacion", "sin_workflow",
        ):
            assert clave in stats, f"falta clave '{clave}' en stats"

    def test_stats_whatsapp_resueltas_refleja_valor(self, db, orchestrator):
        orchestrator.whatsapp.procesar_respuestas.return_value = 3
        stats = orchestrator.tick()
        assert stats["whatsapp_resueltas"] == 3

    def test_tick_sin_expedientes_no_crashea(self, db, orchestrator):
        stats = orchestrator.tick()
        assert stats["avanzadas"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# tick(): dispatch de workflows
# ─────────────────────────────────────────────────────────────────────────────

class TestTickWorkflows:

    def test_avanza_expediente_sin_acciones_pendientes(self, db, orchestrator):
        eid = _crear_exp(db, Estado.RECIBIDO)
        mock_wf = MagicMock()
        mock_wf.tipo_plano = TipoPlano.SEGREGACION.value
        orchestrator.register_workflow(mock_wf)

        stats = orchestrator.tick()

        mock_wf.avanzar.assert_called_once_with(eid)
        assert stats["avanzadas"] == 1

    def test_no_avanza_expediente_con_accion_pendiente(self, db, orchestrator):
        eid = _crear_exp(db, Estado.RECIBIDO)
        db.crear_accion_pendiente(
            expediente_id=eid,
            tipo_accion="pago_cliente",
            descripcion="Confirmar pago",
            actor="test",
        )
        mock_wf = MagicMock()
        mock_wf.tipo_plano = TipoPlano.SEGREGACION.value
        orchestrator.register_workflow(mock_wf)

        stats = orchestrator.tick()

        mock_wf.avanzar.assert_not_called()
        assert stats["esperando_confirmacion"] == 1

    def test_sin_workflow_registrado_incrementa_sin_workflow(self, db, orchestrator):
        _crear_exp(db, Estado.RECIBIDO)
        # No registrar workflow

        stats = orchestrator.tick()

        assert stats["sin_workflow"] == 1
        assert stats["avanzadas"] == 0

    def test_workflow_que_lanza_incrementa_fallidas(self, db, orchestrator):
        eid = _crear_exp(db, Estado.RECIBIDO)
        mock_wf = MagicMock()
        mock_wf.tipo_plano = TipoPlano.SEGREGACION.value
        mock_wf.avanzar.side_effect = RuntimeError("fallo inesperado")
        orchestrator.register_workflow(mock_wf)

        stats = orchestrator.tick()

        assert stats["fallidas"] == 1
        assert stats["avanzadas"] == 0

    def test_not_implemented_error_no_cuenta_como_fallida(self, db, orchestrator):
        eid = _crear_exp(db, Estado.RECIBIDO)
        mock_wf = MagicMock()
        mock_wf.tipo_plano = TipoPlano.SEGREGACION.value
        mock_wf.avanzar.side_effect = NotImplementedError("handler pendiente")
        orchestrator.register_workflow(mock_wf)

        stats = orchestrator.tick()

        assert stats["fallidas"] == 0

    def test_expediente_cancelado_no_se_avanza(self, db, orchestrator):
        eid = _crear_exp(db, Estado.CANCELADO)
        mock_wf = MagicMock()
        mock_wf.tipo_plano = TipoPlano.SEGREGACION.value
        orchestrator.register_workflow(mock_wf)

        orchestrator.tick()

        mock_wf.avanzar.assert_not_called()

    def test_register_workflow_sin_tipo_plano_lanza(self, db, orchestrator):
        mock_wf = MagicMock()
        mock_wf.tipo_plano = ""

        with pytest.raises(ValueError):
            orchestrator.register_workflow(mock_wf)


# ─────────────────────────────────────────────────────────────────────────────
# _enviar_recordatorios()
# ─────────────────────────────────────────────────────────────────────────────

class TestEnviarRecordatorios:

    def _crear_recordatorio_expirado(self, db, eid: str) -> str:
        """Crea y expira una acción de recordatorio de vencimiento."""
        # expira_en en el pasado para que expirar_acciones_vencidas lo procese
        pasado = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        aid = db.crear_accion_pendiente(
            expediente_id=eid,
            tipo_accion="recordatorio_vencimiento_plano",
            descripcion=(
                "Aviso: su plano fue inscrito hace 10 meses. Vence en 2 meses."
            ),
            expira_en=pasado,
            actor="workflow.segregacion",
        )
        # Expirar manualmente
        db.resolver_accion(aid, "expirada", actor="test")
        return aid

    def test_sin_recordatorios_devuelve_cero(self, db, orchestrator):
        _crear_exp(db, Estado.ENTREGADO)
        resultado = orchestrator._enviar_recordatorios()
        assert resultado == 0

    def test_recordatorio_expirado_envia_whatsapp(self, db, orchestrator):
        eid = _crear_exp(db, Estado.ENTREGADO)
        self._crear_recordatorio_expirado(db, eid)

        resultado = orchestrator._enviar_recordatorios()

        assert resultado == 1
        orchestrator.whatsapp.notificar_estado.assert_called_once()
        args = orchestrator.whatsapp.notificar_estado.call_args[0]
        assert args[0] == "50688880001"  # telefono_cliente
        assert "plano" in args[1].lower() or "vence" in args[1].lower()

    def test_recordatorio_marca_metadata_enviado(self, db, orchestrator):
        eid = _crear_exp(db, Estado.ENTREGADO)
        self._crear_recordatorio_expirado(db, eid)

        orchestrator._enviar_recordatorios()

        meta = _meta(db, eid)
        assert meta.get("recordatorio_vencimiento_enviado") is True

    def test_recordatorio_idempotente_no_reenvía(self, db, orchestrator):
        eid = _crear_exp(
            db, Estado.ENTREGADO,
            meta={"recordatorio_vencimiento_enviado": True},
        )
        self._crear_recordatorio_expirado(db, eid)

        resultado = orchestrator._enviar_recordatorios()

        assert resultado == 0
        orchestrator.whatsapp.notificar_estado.assert_not_called()

    def test_error_en_whatsapp_no_crashea_loop(self, db, orchestrator):
        eid = _crear_exp(db, Estado.ENTREGADO)
        self._crear_recordatorio_expirado(db, eid)

        orchestrator.whatsapp.notificar_estado.side_effect = Exception("timeout")

        # No debe lanzar
        resultado = orchestrator._enviar_recordatorios()
        assert resultado == 0

    def test_tick_incluye_recordatorios_en_stats(self, db, orchestrator):
        eid = _crear_exp(db, Estado.ENTREGADO)
        self._crear_recordatorio_expirado(db, eid)

        stats = orchestrator.tick()

        assert stats["recordatorios"] == 1
