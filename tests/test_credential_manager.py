"""Tests para CredentialManager (Windows Credential Manager).

Estrategia:
  - Todos los tests usan un prefijo único basado en UUID para que nunca
    colisionen con credenciales reales del usuario ni entre tests
    paralelos.
  - Cleanup garantizado con fixture autouse que borra las entradas al
    terminar cada test.
  - Se saltan automáticamente si pywin32 no está disponible.

Credenciales que se escriben/leen (todas efímeras):
  - {PREFIX}/db-key        → bytes (llave maestra SQLCipher)
  - {PREFIX}/secret-test   → string arbitrario
  - {PREFIX}/json-test     → dict JSON
  - {PREFIX}/operators     → set de teléfonos
"""
from __future__ import annotations

import json
import secrets
import uuid

import pytest

pytest.importorskip("win32cred", reason="pywin32 requerido (solo Windows)")

import win32cred  # noqa: E402  (ya sabemos que está disponible)

from src.core.credential_manager import CredentialManager  # noqa: E402
from src.core.exceptions import CredentialNotFoundError  # noqa: E402

# Prefijo único por sesión de test (no colisiona con producción)
_TEST_PREFIX = f"catastro-bot-test-{uuid.uuid4().hex[:8]}"

# CredentialManager usa formato "{prefix}:{name}" (con dos puntos)
def _target(name: str) -> str:
    """Nombre completo de la credencial en Windows Credential Manager."""
    return f"{_TEST_PREFIX}:{name}"


@pytest.fixture(autouse=True)
def limpiar_credenciales():
    """Borra todas las credenciales de test al finalizar cada test."""
    yield
    # CRED_DB_KEY = "db-master-key", CRED_OPERATORS = "operators"
    for name in ("db-master-key", "operators", "secret-test", "json-test"):
        try:
            win32cred.CredDelete(_target(name), win32cred.CRED_TYPE_GENERIC, 0)
        except Exception:
            pass  # no existía — ok


@pytest.fixture
def cm():
    """CredentialManager apuntando a nuestro prefijo de test."""
    return CredentialManager(prefix=_TEST_PREFIX)


# ─────────────────────────────────────────────────────────────────────────────
# get_or_create_db_key
# ─────────────────────────────────────────────────────────────────────────────

class TestDbKey:

    def test_crea_llave_aleatoria(self, cm):
        key = cm.get_or_create_db_key()
        assert isinstance(key, bytes)
        assert len(key) == 32

    def test_get_or_create_idempotente(self, cm):
        k1 = cm.get_or_create_db_key()
        k2 = cm.get_or_create_db_key()
        assert k1 == k2

    def test_get_db_key_requiere_existencia(self, cm):
        """get_db_key lanza si no existe — get_or_create la crea primero."""
        with pytest.raises(CredentialNotFoundError):
            cm.get_db_key()

    def test_get_db_key_devuelve_la_misma(self, cm):
        k1 = cm.get_or_create_db_key()
        k2 = cm.get_db_key()
        assert k1 == k2

    def test_llaves_diferentes_por_instancia(self):
        """Dos CredentialManager con distintos prefijos tienen llaves distintas."""
        prefix_a = f"catastro-bot-test-{uuid.uuid4().hex[:8]}"
        prefix_b = f"catastro-bot-test-{uuid.uuid4().hex[:8]}"
        cm_a = CredentialManager(prefix=prefix_a)
        cm_b = CredentialManager(prefix=prefix_b)
        ka = cm_a.get_or_create_db_key()
        kb = cm_b.get_or_create_db_key()
        # Cleanup manual (separador es ":")
        for p in (prefix_a, prefix_b):
            try:
                win32cred.CredDelete(f"{p}:db-master-key", win32cred.CRED_TYPE_GENERIC, 0)
            except Exception:
                pass
        assert ka != kb


# ─────────────────────────────────────────────────────────────────────────────
# set_secret / get_secret
# ─────────────────────────────────────────────────────────────────────────────

class TestSecrets:

    def test_set_get_string(self, cm):
        cm.set_secret("secret-test", "valor-secreto")
        assert cm.get_secret("secret-test") == "valor-secreto"

    def test_get_secret_inexistente_lanza(self, cm):
        with pytest.raises(CredentialNotFoundError):
            cm.get_secret("no-existe-jamás-xyz")

    def test_set_sobrescribe(self, cm):
        cm.set_secret("secret-test", "primero")
        cm.set_secret("secret-test", "segundo")
        assert cm.get_secret("secret-test") == "segundo"

    def test_set_get_json(self, cm):
        data = {"instance_id": "12345", "token": "tok-abc"}
        cm.set_json("json-test", data)
        leido = cm.get_json("json-test")
        assert leido == data

    def test_get_json_inexistente_lanza(self, cm):
        with pytest.raises(CredentialNotFoundError):
            cm.get_json("json-test-inexistente")


# ─────────────────────────────────────────────────────────────────────────────
# Operadores
# ─────────────────────────────────────────────────────────────────────────────

class TestOperadores:

    def test_operators_vacios_al_inicio(self, cm):
        assert cm.get_operators() == set()

    def test_is_operator_false_si_no_hay_ninguno(self, cm):
        assert not cm.is_operator("50688880001")

    def test_add_operator_y_is_operator(self, cm):
        cm.add_operator("50688880001")
        assert cm.is_operator("50688880001")

    def test_add_operator_normaliza_8_digitos(self, cm):
        cm.add_operator("88880001")
        assert cm.is_operator("50688880001")

    def test_add_operator_invalido_lanza(self, cm):
        with pytest.raises(ValueError):
            cm.add_operator("123")  # muy corto

    def test_remove_operator(self, cm):
        cm.add_operator("50688880001")
        cm.remove_operator("50688880001")
        assert not cm.is_operator("50688880001")

    def test_remove_operator_inexistente_devuelve_false(self, cm):
        resultado = cm.remove_operator("50699990099")
        assert resultado is False

    def test_get_operators_devuelve_set(self, cm):
        cm.add_operator("50688880001")
        cm.add_operator("50688880002")
        ops = cm.get_operators()
        assert "50688880001" in ops
        assert "50688880002" in ops

    def test_operadores_persisten_entre_instancias(self):
        """Dos instancias con mismo prefijo comparten credenciales."""
        prefix = f"catastro-bot-test-{uuid.uuid4().hex[:8]}"
        cm1 = CredentialManager(prefix=prefix)
        cm2 = CredentialManager(prefix=prefix)
        cm1.add_operator("50688880001")
        try:
            assert cm2.is_operator("50688880001")
        finally:
            try:
                win32cred.CredDelete(
                    f"{prefix}:operators", win32cred.CRED_TYPE_GENERIC, 0
                )
            except Exception:
                pass
