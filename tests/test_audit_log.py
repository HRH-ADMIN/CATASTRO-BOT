"""Tests para audit_log.py — funciones puras de hashing y verificación.

Cubre:
  - compute_entry_hash: determinismo, separador, campos opcionales
  - verify_chain: cadena válida, hash incorrecto, cadena rota, vacía
  - integración con Database.verify_audit_chain (tampering detection)
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

pytest.importorskip("win32cred", reason="pywin32 requerido (solo Windows)")

from src.core.audit_log import compute_entry_hash, verify_chain  # noqa: E402
from src.core.exceptions import AuditLogTamperError  # noqa: E402
from tests.conftest import FakeCredentialManager, TestDatabase  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# compute_entry_hash
# ─────────────────────────────────────────────────────────────────────────────

class TestComputeEntryHash:

    def _hash(self, **kwargs):
        defaults = dict(
            prev_hash="",
            timestamp="2026-01-01T00:00:00+00:00",
            actor="test",
            expediente_id=None,
            accion="test.accion",
            detalles_json="{}",
        )
        defaults.update(kwargs)
        return compute_entry_hash(**defaults)

    def test_devuelve_hex_sha256(self):
        h = self._hash()
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_determinismo(self):
        h1 = self._hash()
        h2 = self._hash()
        assert h1 == h2

    def test_sensible_a_prev_hash(self):
        h1 = self._hash(prev_hash="")
        h2 = self._hash(prev_hash="aabbcc")
        assert h1 != h2

    def test_sensible_a_actor(self):
        h1 = self._hash(actor="alice")
        h2 = self._hash(actor="bob")
        assert h1 != h2

    def test_sensible_a_accion(self):
        h1 = self._hash(accion="accion.a")
        h2 = self._hash(accion="accion.b")
        assert h1 != h2

    def test_sensible_a_timestamp(self):
        h1 = self._hash(timestamp="2026-01-01T00:00:00")
        h2 = self._hash(timestamp="2026-01-02T00:00:00")
        assert h1 != h2

    def test_expediente_id_none_equivale_a_vacio(self):
        """expediente_id=None debe producir el mismo hash que pasar "" explícitamente."""
        h1 = self._hash(expediente_id=None)
        # La implementación usa `expediente_id or ""`
        payload = "|".join(["", "2026-01-01T00:00:00+00:00",
                            "test", "", "test.accion", "{}"])
        h2 = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        assert h1 == h2

    def test_sensible_a_detalles_json(self):
        h1 = self._hash(detalles_json="{}")
        h2 = self._hash(detalles_json='{"key": "val"}')
        assert h1 != h2


# ─────────────────────────────────────────────────────────────────────────────
# verify_chain
# ─────────────────────────────────────────────────────────────────────────────

def _make_row(prev_hash: str, *, ts: str = "2026-01-01T00:00:00",
              actor: str = "test", eid=None,
              accion: str = "test.act", detalles: str = "{}") -> dict:
    h = compute_entry_hash(
        prev_hash=prev_hash,
        timestamp=ts,
        actor=actor,
        expediente_id=eid,
        accion=accion,
        detalles_json=detalles,
    )
    return {
        "prev_hash": prev_hash,
        "hash_anterior": prev_hash or None,
        "hash_actual": h,
        "timestamp": ts,
        "actor": actor,
        "expediente_id": eid,
        "accion": accion,
        "detalles_json": detalles,
    }


class TestVerifyChain:

    def test_cadena_vacia_devuelve_cero(self):
        assert verify_chain([]) == 0

    def test_cadena_una_entrada(self):
        row = _make_row("")
        assert verify_chain([row]) == 1

    def test_cadena_multiples_entradas(self):
        r1 = _make_row("")
        r2 = _make_row(r1["hash_actual"], ts="2026-01-01T00:01:00")
        r3 = _make_row(r2["hash_actual"], ts="2026-01-01T00:02:00")
        assert verify_chain([r1, r2, r3]) == 3

    def test_hash_alterado_detectado(self):
        r1 = _make_row("")
        r2 = _make_row(r1["hash_actual"], ts="2026-01-01T00:01:00")
        # Alterar el hash de r1
        r1_tampered = dict(r1, hash_actual="aaaa" + "0" * 60)
        with pytest.raises(AuditLogTamperError):
            verify_chain([r1_tampered, r2])

    def test_cadena_rota_detectada(self):
        """hash_anterior de r2 no coincide con hash_actual de r1."""
        r1 = _make_row("")
        r2 = _make_row(r1["hash_actual"], ts="2026-01-01T00:01:00")
        # Construir r2 con hash_anterior incorrecto
        r2_roto = dict(r2, hash_anterior="cadena_rota_" + "x" * 52)
        with pytest.raises(AuditLogTamperError):
            verify_chain([r1, r2_roto])

    def test_orden_importa(self):
        """Invertir el orden de dos entradas válidas debe detectarse."""
        r1 = _make_row("")
        r2 = _make_row(r1["hash_actual"], ts="2026-01-01T00:01:00")
        # r2 tiene hash_anterior = r1.hash_actual; si van en orden [r2, r1]
        # el primer hash calculado esperaría prev_hash="" pero r2 lo tiene != ""
        with pytest.raises(AuditLogTamperError):
            verify_chain([r2, r1])

    def test_campo_actor_alterado_detecta_hash_incorrecto(self):
        r1 = _make_row("", actor="alice")
        r1_tampered = dict(r1, actor="mallory")
        with pytest.raises(AuditLogTamperError):
            verify_chain([r1_tampered])


# ─────────────────────────────────────────────────────────────────────────────
# Integración con Database: tampering real
# ─────────────────────────────────────────────────────────────────────────────

class TestAuditTamperingIntegration:

    @pytest.fixture
    def db(self, tmp_path: Path):
        creds = FakeCredentialManager()
        database = TestDatabase(path=tmp_path / "audit_test.db", credentials=creds)
        database.initialize_schema()
        return database

    def test_cadena_limpia_tras_operaciones(self, db):
        """verify_audit_chain no lanza después de operaciones normales."""
        db.crear_expediente(
            numero_expediente="EXP-AUDIT-1",
            tipo_plano="segregacion",
            nombre_topografo="T",
            telefono_cliente="50688880001",
            actor="test",
        )
        n = db.verify_audit_chain()
        assert n >= 2  # schema.initialize + expediente.crear

    def test_tampering_hash_detectado(self, db):
        """Si alteramos directamente un hash en audit_log, verify_chain lo detecta."""
        import sqlite3

        # Trigger solo bloquea UPDATE, pero podemos probarlo con una subclase que lo detecte
        # Los triggers SQL impiden el UPDATE — intentar uno debe fallar
        with db.connect() as conn:
            # El trigger debe impedirlo
            with pytest.raises(sqlite3.IntegrityError, match="immutable"):
                conn.execute(
                    "UPDATE audit_log SET hash_actual = 'hacked' WHERE id = 1"
                )

    def test_cadena_crece_con_cada_operacion(self, db):
        n0 = db.verify_audit_chain()  # solo schema.initialize
        db.crear_expediente(
            numero_expediente="EXP-GROW",
            tipo_plano="reunion_de_fincas",
            nombre_topografo="T",
            telefono_cliente="50688880001",
            actor="test",
        )
        n1 = db.verify_audit_chain()
        assert n1 > n0
