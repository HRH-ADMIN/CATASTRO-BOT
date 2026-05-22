"""Tests del guard SIMULAR_APT."""
from __future__ import annotations

import socket

import pytest

from src.core.startup_guards import assert_safe_simular_apt


def test_simular_off_passes(monkeypatch):
    monkeypatch.setenv("CATASTRO_BOT_SIMULAR_APT", "0")
    assert_safe_simular_apt()  # no raise


def test_simular_unset_passes(monkeypatch):
    monkeypatch.delenv("CATASTRO_BOT_SIMULAR_APT", raising=False)
    assert_safe_simular_apt()


def test_simular_on_in_production_exits(monkeypatch):
    monkeypatch.setenv("CATASTRO_BOT_SIMULAR_APT", "1")
    monkeypatch.delenv("CATASTRO_BOT_DEV_MODE", raising=False)
    monkeypatch.setenv("CATASTRO_BOT_TEST_HOSTS", "nope-not-this-host")
    with pytest.raises(SystemExit) as exc:
        assert_safe_simular_apt()
    assert exc.value.code == 1


def test_simular_on_with_dev_mode_passes(monkeypatch):
    monkeypatch.setenv("CATASTRO_BOT_SIMULAR_APT", "1")
    monkeypatch.setenv("CATASTRO_BOT_DEV_MODE", "1")
    monkeypatch.delenv("CATASTRO_BOT_TEST_HOSTS", raising=False)
    assert_safe_simular_apt()


def test_simular_on_with_host_in_whitelist_passes(monkeypatch):
    hostname = socket.gethostname()
    monkeypatch.setenv("CATASTRO_BOT_SIMULAR_APT", "1")
    monkeypatch.delenv("CATASTRO_BOT_DEV_MODE", raising=False)
    monkeypatch.setenv("CATASTRO_BOT_TEST_HOSTS", hostname)
    assert_safe_simular_apt()


def test_simular_truthy_variants(monkeypatch):
    """Verifica que valores tipo `true`, `yes` también disparan el guard."""
    monkeypatch.delenv("CATASTRO_BOT_DEV_MODE", raising=False)
    monkeypatch.setenv("CATASTRO_BOT_TEST_HOSTS", "fake-test-host")
    for val in ("1", "true", "yes"):
        monkeypatch.setenv("CATASTRO_BOT_SIMULAR_APT", val)
        with pytest.raises(SystemExit):
            assert_safe_simular_apt()
