"""Tests del wrapper _gated() migrado a state machine (U-03 paso 2.4).

Verifica que:
  - Si la state machine tiene el módulo en RUNNING → el job se ejecuta.
  - Si está en cualquier otro estado → skip.
  - Si scheduler master está STOPPED/ERROR → todo skip aunque el módulo
    esté RUNNING.
  - Si la state machine falla, cae al legacy control_state (sin romper).
  - Si el módulo es desconocido para la SM, también cae al legacy.

Plan: PLAN_MEJORAS Sprint 1 / U-03 paso 2.4.
"""
from __future__ import annotations
from unittest.mock import MagicMock

import pytest

from src.core.credential_manager import CredentialManager
from src.core.control_state import ControlStateManager
from src.core.database import Database
from src.core.state_machine import (
    get_state_machine,
    reset_state_machine_for_testing,
)
from src.scheduler.tasks import _gated


@pytest.fixture(autouse=True)
def _reset_singletons():
    reset_state_machine_for_testing()
    from src.utils.event_bus import reset_bus_for_testing
    reset_bus_for_testing()
    yield
    reset_state_machine_for_testing()
    reset_bus_for_testing()


@pytest.fixture
def sm(tmp_path, monkeypatch):
    monkeypatch.setenv("CATASTRO_BOT_DEV_MODE", "1")
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("config.settings.DATABASE_PATH", db_path)
    d = Database(path=db_path, credentials=CredentialManager())
    d.initialize_schema()
    return get_state_machine(db_path)


@pytest.fixture
def legacy_mgr(tmp_path):
    return ControlStateManager(tmp_path / "control.json")


class TestStateMachineGate:
    def test_modulo_running_pasa(self, sm, legacy_mgr):
        sm.start("apt", actor="bootstrap", force=True)
        sm.mark_running("apt")
        # scheduler también debe estar RUNNING para que el master no bloquee
        sm.start("scheduler", actor="bootstrap", force=True)
        sm.mark_running("scheduler")

        called = []
        def myjob(): called.append("ran")
        wrapped = _gated(myjob, module="apt", manager=legacy_mgr,
                         job_id="test-job")
        wrapped()
        assert called == ["ran"]

    def test_modulo_stopped_no_ejecuta(self, sm, legacy_mgr):
        # apt sigue en STOPPED por default
        sm.start("scheduler", actor="bootstrap", force=True)
        sm.mark_running("scheduler")

        called = []
        def myjob(): called.append("ran")
        wrapped = _gated(myjob, module="apt", manager=legacy_mgr,
                         job_id="test-job")
        wrapped()
        assert called == []

    def test_scheduler_master_stopped_bloquea_todo(self, sm, legacy_mgr):
        # apt RUNNING pero scheduler STOPPED → el job NO corre
        sm.start("apt", actor="bootstrap", force=True)
        sm.mark_running("apt")
        # scheduler queda STOPPED

        called = []
        def myjob(): called.append("ran")
        wrapped = _gated(myjob, module="apt", manager=legacy_mgr,
                         job_id="test-job")
        wrapped()
        assert called == []

    def test_modulo_starting_no_ejecuta(self, sm, legacy_mgr):
        sm.start("scheduler", actor="bootstrap", force=True)
        sm.mark_running("scheduler")
        sm.start("apt", actor="test", force=True)  # queda en STARTING

        called = []
        def myjob(): called.append("ran")
        wrapped = _gated(myjob, module="apt", manager=legacy_mgr,
                         job_id="test-job")
        wrapped()
        assert called == []

    def test_modulo_error_no_ejecuta(self, sm, legacy_mgr):
        sm.start("scheduler", actor="bootstrap", force=True)
        sm.mark_running("scheduler")
        sm.start("apt", actor="test", force=True)
        sm.mark_error("apt", actor="test", error_message="CDP no responde")

        called = []
        def myjob(): called.append("ran")
        wrapped = _gated(myjob, module="apt", manager=legacy_mgr,
                         job_id="test-job")
        wrapped()
        assert called == []


class TestFallbackLegacy:
    def test_modulo_desconocido_para_sm_cae_a_legacy(self, sm, legacy_mgr):
        """Si el caller pasa un módulo que no está en SM_KNOWN_MODULES,
        la SM no lo bloquea y se consulta el legacy."""
        # Forzar scheduler RUNNING (sino el master corta)
        sm.start("scheduler", actor="bootstrap", force=True)
        sm.mark_running("scheduler")

        # Legacy: enabled=True por default → debe pasar
        called = []
        def myjob(): called.append("ran")
        wrapped = _gated(myjob, module="modulo_inventado",
                         manager=legacy_mgr, job_id="test-job")
        wrapped()
        assert called == ["ran"]

    def test_legacy_disabled_bloquea_modulo_desconocido(self, sm, legacy_mgr):
        sm.start("scheduler", actor="bootstrap", force=True)
        sm.mark_running("scheduler")
        legacy_mgr.write({"enabled": False}, set_by="test", reason="paused")

        called = []
        def myjob(): called.append("ran")
        wrapped = _gated(myjob, module="modulo_inventado",
                         manager=legacy_mgr, job_id="test-job")
        wrapped()
        assert called == []
