"""Tests del módulo control_state."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.core.control_state import (
    KNOWN_MODULES,
    SCHEMA_VERSION,
    ControlState,
    ControlStateManager,
)


@pytest.fixture
def manager(tmp_path):
    return ControlStateManager(tmp_path / "control.json")


def test_read_creates_file_on_first_call(manager, tmp_path):
    assert not (tmp_path / "control.json").exists()
    state = manager.read()
    assert state.enabled is True
    assert (tmp_path / "control.json").exists()
    assert state.schema_version == SCHEMA_VERSION


def test_default_state_has_all_known_modules(manager):
    state = manager.read()
    for module in KNOWN_MODULES:
        assert module in state.modules
        assert state.modules[module] is True


def test_write_persists_changes(manager, tmp_path):
    manager.write({"enabled": False}, set_by="web", reason="manual pause")
    raw = json.loads((tmp_path / "control.json").read_text())
    assert raw["enabled"] is False
    assert raw["set_by"] == "web"
    assert raw["reason"] == "manual pause"


def test_write_merges_modules(manager):
    manager.write({"modules": {"apt": False}}, set_by="test")
    state = manager.read(force=True)
    assert state.modules["apt"] is False
    assert state.modules["whatsapp"] is True  # no afectado


def test_is_module_enabled_respects_master_toggle(manager):
    manager.write({"enabled": False}, set_by="test")
    state = manager.read(force=True)
    for m in KNOWN_MODULES:
        assert not state.is_module_enabled(m)


def test_is_module_enabled_respects_individual_toggle(manager):
    manager.write({"modules": {"apt": False}}, set_by="test")
    state = manager.read(force=True)
    assert not state.is_module_enabled("apt")
    assert state.is_module_enabled("whatsapp")


def test_pause_until_future_blocks_modules(manager):
    future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    manager.write({"pause_until": future}, set_by="test")
    state = manager.read(force=True)
    assert not state.is_module_enabled("apt")


def test_pause_until_past_does_not_block(manager):
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    manager.write({"pause_until": past}, set_by="test")
    state = manager.read(force=True)
    assert state.is_module_enabled("apt")


def test_corrupt_file_returns_failsafe(manager, tmp_path):
    # Inicializar
    manager.read()
    # Corromper
    (tmp_path / "control.json").write_text("{ NOT JSON", encoding="utf-8")
    state = manager.read(force=True)
    assert state.enabled is False  # fail-closed
    assert "fail-safe" in state.reason


def test_atomic_write_does_not_leave_tmp(manager, tmp_path):
    manager.write({"enabled": False}, set_by="test")
    tmps = list(tmp_path.glob("*.tmp"))
    assert tmps == []


def test_cache_avoids_repeated_disk_reads(manager, tmp_path):
    manager.read()  # populate cache
    path = tmp_path / "control.json"
    original_mtime = path.stat().st_mtime
    # Llamar 100 veces no debería re-leer el disco
    for _ in range(100):
        manager.read()
    assert path.stat().st_mtime == original_mtime


def test_force_bypasses_cache(manager, tmp_path):
    manager.read()
    # Reemplazar el archivo manualmente
    (tmp_path / "control.json").write_text(
        json.dumps({
            "enabled": False, "modules": {}, "pause_until": None,
            "reason": "external edit", "set_by": "x",
            "updated_at": "", "heartbeat_at": "",
            "schema_version": SCHEMA_VERSION,
        }),
        encoding="utf-8",
    )
    state = manager.read(force=True)
    assert state.enabled is False
    assert state.reason == "external edit"


def test_heartbeat_updates_only_that_field(manager):
    manager.write({"enabled": False, "reason": "test"}, set_by="x")
    before = manager.read(force=True)
    manager.write_heartbeat()
    after = manager.read(force=True)
    assert after.heartbeat_at != before.heartbeat_at
    assert after.enabled is False  # preservado
    assert after.reason == "test"  # preservado


def test_tolerates_missing_fields_in_file(manager, tmp_path):
    """Archivo viejo sin todos los campos no debe romper."""
    (tmp_path / "control.json").write_text(
        json.dumps({"enabled": True}), encoding="utf-8",
    )
    state = manager.read(force=True)
    assert state.enabled is True
    assert state.modules == {m: True for m in KNOWN_MODULES}


def test_rejects_future_schema_version(manager, tmp_path):
    (tmp_path / "control.json").write_text(
        json.dumps({"enabled": True, "schema_version": 999}),
        encoding="utf-8",
    )
    state = manager.read(force=True)
    assert state.enabled is False  # fail-safe
    assert "fail-safe" in state.reason
