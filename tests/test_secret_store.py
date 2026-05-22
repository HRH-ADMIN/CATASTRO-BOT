"""Tests del SecretStore (DPAPIMachineStore en particular).

WindowsCredentialStore NO se testea acá porque escribiría en el Cred
Manager real del usuario, contaminando su entorno. Su contrato lo cubren
los tests de CredentialManager que ya existen.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

if sys.platform != "win32":
    pytest.skip("DPAPI solo aplica en Windows", allow_module_level=True)

from src.core.exceptions import CredentialNotFoundError
from src.core.secret_store import (
    DPAPIMachineStore,
    WindowsCredentialStore,
    get_default_store,
)


@pytest.fixture
def store(tmp_path):
    return DPAPIMachineStore(tmp_path / "secrets.enc")


def test_set_get_roundtrip(store):
    store.set("apt", "topografo", "super-secret-42")
    assert store.get("apt") == ("topografo", "super-secret-42")


def test_get_missing_raises(store):
    with pytest.raises(CredentialNotFoundError):
        store.get("never-existed")


def test_list_names_empty(store):
    assert store.list_names() == []


def test_list_names_after_set(store):
    store.set("apt", "u", "p1")
    store.set("green", "_", "p2")
    assert sorted(store.list_names()) == ["apt", "green"]


def test_exists(store):
    assert not store.exists("apt")
    store.set("apt", "u", "p")
    assert store.exists("apt")


def test_delete_is_idempotent(store):
    store.set("apt", "u", "p")
    store.delete("apt")
    store.delete("apt")  # no levanta
    assert not store.exists("apt")


def test_persistence_across_instances(tmp_path):
    path = tmp_path / "secrets.enc"
    a = DPAPIMachineStore(path)
    a.set("apt", "user", "pwd")
    b = DPAPIMachineStore(path)
    assert b.get("apt") == ("user", "pwd")


def test_overwrites_existing_entry(store):
    store.set("apt", "u1", "p1")
    store.set("apt", "u2", "p2")
    assert store.get("apt") == ("u2", "p2")


def test_encrypted_blob_not_readable_as_plaintext(tmp_path):
    """El contenido del archivo NO debe contener el password en claro."""
    path = tmp_path / "secrets.enc"
    store = DPAPIMachineStore(path)
    store.set("apt", "user", "PWD-IN-CLEAR-MARKER-XYZ123")
    raw = path.read_text(encoding="utf-8")
    assert "PWD-IN-CLEAR-MARKER-XYZ123" not in raw, (
        "el password apareció en claro en secrets.enc — DPAPI no cifró"
    )


def test_get_default_store_explicit_dpapi(tmp_path, monkeypatch):
    monkeypatch.setenv("CATASTRO_BOT_SECRET_BACKEND", "dpapi")
    s = get_default_store(prefix="test", dpapi_path=tmp_path / "x.enc")
    assert isinstance(s, DPAPIMachineStore)


def test_get_default_store_explicit_credman(monkeypatch):
    monkeypatch.setenv("CATASTRO_BOT_SECRET_BACKEND", "credman")
    s = get_default_store(prefix="test")
    assert isinstance(s, WindowsCredentialStore)


def test_get_default_store_autodetect_dpapi_when_file_exists(
    tmp_path, monkeypatch,
):
    monkeypatch.delenv("CATASTRO_BOT_SECRET_BACKEND", raising=False)
    p = tmp_path / "secrets.enc"
    DPAPIMachineStore(p).set("k", "_", "v")  # crea el archivo
    s = get_default_store(prefix="test", dpapi_path=p)
    assert isinstance(s, DPAPIMachineStore)


def test_get_default_store_default_is_credman(tmp_path, monkeypatch):
    monkeypatch.delenv("CATASTRO_BOT_SECRET_BACKEND", raising=False)
    s = get_default_store(
        prefix="test", dpapi_path=tmp_path / "nonexistent.enc",
    )
    assert isinstance(s, WindowsCredentialStore)
