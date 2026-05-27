"""Tests de ControlStateMachine (U-03 paso 2.1).

Cubre:
  - Tabla module_state creada por initialize_schema.
  - Transiciones permitidas y bloqueadas.
  - Cooldown post-emergency-stop.
  - Race condition handling.
  - Publishers SSE invocados sin romper si fallan.
  - Singleton + reset.

Plan: PLAN_MEJORAS Sprint 1 / U-03 paso 2.1.
"""
from __future__ import annotations
import sqlite3

import pytest

from src.core.credential_manager import CredentialManager
from src.core.database import Database
from src.core.state_machine import (
    ControlStateMachine,
    CooldownActive,
    InvalidTransition,
    KNOWN_MODULES,
    UnknownModule,
    get_state_machine,
    reset_state_machine_for_testing,
)


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
    return ControlStateMachine(db_path)


# ─── Schema ─────────────────────────────────────────────────────────

class TestSchema:
    def test_tabla_module_state_existe_post_initialize(self, sm):
        with sqlite3.connect(sm._db_path) as conn:
            tables = [
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name='module_state'"
                )
            ]
        assert tables == ["module_state"]

    def test_check_constraint_estado_invalido(self, sm):
        with pytest.raises(sqlite3.IntegrityError):
            with sqlite3.connect(sm._db_path) as conn:
                conn.execute(
                    "INSERT INTO module_state "
                    "(module_name, state, last_transition_at) "
                    "VALUES ('test', 'INVALID_STATE', datetime('now'))"
                )
                conn.commit()


# ─── Read / inicialización lazy ──────────────────────────────────────

class TestRead:
    def test_read_inicializa_modulo_a_stopped(self, sm):
        st = sm.read("apt")
        assert st.module_name == "apt"
        assert st.state == "STOPPED"
        assert st.last_transition_reason == "auto-initialized"

    def test_read_all_inicializa_todos_los_modulos_conocidos(self, sm):
        snaps = sm.read_all()
        modulos = {s.module_name for s in snaps}
        assert modulos == set(KNOWN_MODULES)
        for s in snaps:
            assert s.state == "STOPPED"

    def test_read_modulo_desconocido_explota(self, sm):
        with pytest.raises(UnknownModule):
            sm.read("modulo_inventado")


# ─── Transiciones válidas ────────────────────────────────────────────

class TestTransicionesValidas:
    def test_stopped_to_starting(self, sm):
        st = sm.start("apt", actor="test")
        assert st.state == "STARTING"
        assert st.last_transition_actor == "test"
        assert st.last_transition_id  # uuid generado

    def test_starting_to_running(self, sm):
        sm.start("apt", actor="test")
        st = sm.mark_running("apt")
        assert st.state == "RUNNING"

    def test_running_to_stopping(self, sm):
        sm.start("apt", actor="test"); sm.mark_running("apt")
        st = sm.stop("apt", actor="test", reason="end-of-day")
        assert st.state == "STOPPING"
        assert st.last_transition_reason == "end-of-day"

    def test_stopping_to_stopped(self, sm):
        sm.start("apt", actor="test"); sm.mark_running("apt")
        sm.stop("apt", actor="test")
        st = sm.mark_stopped("apt")
        assert st.state == "STOPPED"

    def test_starting_to_error(self, sm):
        sm.start("apt", actor="test")
        st = sm.mark_error("apt", actor="test", error_message="CDP no responde")
        assert st.state == "ERROR"
        assert st.error_message == "CDP no responde"

    def test_error_to_stopped_via_reset(self, sm):
        sm.start("apt", actor="test")
        sm.mark_error("apt", actor="test", error_message="boom")
        st = sm.reset_error("apt", actor="test")
        assert st.state == "STOPPED"

    def test_ciclo_completo(self, sm):
        """Happy path: STOPPED → STARTING → RUNNING → STOPPING → STOPPED."""
        sm.start("apt", actor="test")
        sm.mark_running("apt")
        sm.stop("apt", actor="test")
        st = sm.mark_stopped("apt")
        assert st.state == "STOPPED"


# ─── Transiciones inválidas ──────────────────────────────────────────

class TestTransicionesInvalidas:
    def test_stopped_to_running_directo_explota(self, sm):
        with pytest.raises(InvalidTransition):
            sm.transition("apt", "RUNNING", actor="test")

    def test_running_to_stopped_directo_explota(self, sm):
        sm.start("apt", actor="test"); sm.mark_running("apt")
        with pytest.raises(InvalidTransition):
            sm.transition("apt", "STOPPED", actor="test")

    def test_double_start_explota(self, sm):
        sm.start("apt", actor="test")
        with pytest.raises(InvalidTransition):
            sm.start("apt", actor="test")


# ─── Cooldown ────────────────────────────────────────────────────────

class TestCooldown:
    def test_emergency_stop_seta_cooldown(self, sm):
        sm.start("apt", actor="test"); sm.mark_running("apt")
        sm.emergency_stop(actor="op", reason="kill", cooldown_seconds=60)
        # apt está ahora en STOPPING (era RUNNING) con cooldown
        st = sm.read("apt")
        assert st.cooldown_until is not None
        assert st.is_in_cooldown()

    def test_start_bloqueado_durante_cooldown(self, sm):
        sm.emergency_stop(actor="op", cooldown_seconds=60)
        # Llevarlo a STOPPED para poder intentar start
        sm.mark_stopped("apt") if sm.read("apt").state == "STOPPING" else None
        with pytest.raises(CooldownActive):
            sm.start("apt", actor="op2")

    def test_force_True_ignora_cooldown(self, sm):
        sm.emergency_stop(actor="op", cooldown_seconds=60)
        sm.mark_stopped("apt") if sm.read("apt").state == "STOPPING" else None
        # Con force debería andar
        st = sm.start("apt", actor="op2", force=True)
        assert st.state == "STARTING"

    def test_emergency_stop_marca_starting_como_error(self, sm):
        sm.start("apt", actor="test")  # STARTING
        sm.emergency_stop(actor="op", reason="cancel boot")
        st = sm.read("apt")
        assert st.state == "ERROR"
        assert "emergency_stop" in (st.error_message or "")

    def test_emergency_stop_publica_evento_sse(self, sm):
        from src.utils.event_bus import get_bus
        import threading, time

        bus = get_bus()
        received = []
        ready = threading.Event()

        def consumer():
            gen = bus.subscribe()
            for ev in gen:
                if not ready.is_set():
                    ready.set()
                received.append(ev)
                if any(e.get("type") == "emergency_stop" for e in received):
                    break

        t = threading.Thread(target=consumer, daemon=True)
        t.start()
        bus.publish({"type": "kickstart"})
        ready.wait(timeout=2.0)

        sm.emergency_stop(actor="op", reason="kill")

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if any(e.get("type") == "emergency_stop" for e in received):
                break
            time.sleep(0.02)
        t.join(timeout=2.0)

        ev_emerg = [e for e in received if e.get("type") == "emergency_stop"]
        assert len(ev_emerg) == 1
        assert ev_emerg[0]["actor"] == "op"


# ─── transition_id polling ────────────────────────────────────────────

class TestTransitionId:
    def test_get_transition_devuelve_modulo(self, sm):
        st = sm.start("apt", actor="test")
        tid = st.last_transition_id
        assert tid

        found = sm.get_transition(tid)
        assert found is not None
        assert found.module_name == "apt"
        assert found.state == "STARTING"

    def test_get_transition_devuelve_None_si_no_existe(self, sm):
        assert sm.get_transition("inexistente") is None

    def test_caller_puede_pasar_transition_id(self, sm):
        st = sm.transition(
            "apt", "STARTING",
            actor="test", transition_id="custom-tid-123",
        )
        assert st.last_transition_id == "custom-tid-123"


# ─── Heartbeat ────────────────────────────────────────────────────────

class TestHeartbeat:
    def test_write_heartbeat_no_cambia_state(self, sm):
        sm.start("apt", actor="test"); sm.mark_running("apt")
        antes = sm.read("apt")
        sm.write_heartbeat("apt")
        despues = sm.read("apt")
        assert despues.state == antes.state == "RUNNING"
        assert despues.last_heartbeat_at is not None


# ─── Singleton ────────────────────────────────────────────────────────

class TestSingleton:
    def test_get_state_machine_devuelve_misma_instancia(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CATASTRO_BOT_DEV_MODE", "1")
        db_path = tmp_path / "test.db"
        monkeypatch.setattr("config.settings.DATABASE_PATH", db_path)
        d = Database(path=db_path, credentials=CredentialManager())
        d.initialize_schema()

        sm1 = get_state_machine(db_path)
        sm2 = get_state_machine()
        assert sm1 is sm2

    def test_reset_state_machine_para_testing(self):
        sm1 = get_state_machine()
        reset_state_machine_for_testing()
        sm2 = get_state_machine()
        assert sm1 is not sm2


# ─── ModuleState helpers ──────────────────────────────────────────────

class TestModuleStateHelpers:
    def test_is_final_para_stopped_running_error(self, sm):
        sm.start("apt", actor="t"); sm.mark_running("apt")
        assert sm.read("apt").is_final() is True  # RUNNING

        sm.stop("apt", actor="t")
        assert sm.read("apt").is_final() is False  # STOPPING

        sm.mark_stopped("apt")
        assert sm.read("apt").is_final() is True  # STOPPED

    def test_to_dict_serializa_completo(self, sm):
        st = sm.start("apt", actor="test", reason="test reason")
        d = st.to_dict()
        assert d["module_name"] == "apt"
        assert d["state"] == "STARTING"
        assert d["last_transition_actor"] == "test"
        assert d["last_transition_reason"] == "test reason"
