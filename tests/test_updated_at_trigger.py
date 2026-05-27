"""Test del trigger expedientes_touch_fecha_actualizacion (U-04 paso 3).

Garantiza que el trigger SQL mantiene `fecha_actualizacion` consistente
incluso cuando un caller hace UPDATE crudo y olvida tocarla.

Plan: PLAN_MEJORAS_catastro-bot_3.md Sprint 1 / U-04 paso 3.
Documento normativo: docs/SCHEMA.md §3.
"""
from __future__ import annotations
import json
import sqlite3
import time
import uuid

import pytest

from src.core.credential_manager import CredentialManager
from src.core.database import Database


@pytest.fixture
def db(tmp_path, monkeypatch):
    """BD efímera con schema completo aplicado. Usa DEV_MODE (SQLite plano)."""
    monkeypatch.setenv("CATASTRO_BOT_DEV_MODE", "1")
    db_path = tmp_path / "test_catastro.db"
    monkeypatch.setattr("config.settings.DATABASE_PATH", db_path)
    d = Database(path=db_path, credentials=CredentialManager())
    d.initialize_schema()
    return d


def _crear_expediente_minimal(db: Database) -> str:
    """Crea un expediente de prueba y devuelve su id."""
    exp_id = str(uuid.uuid4())
    with sqlite3.connect(db.path) as conn:
        conn.execute(
            """INSERT INTO expedientes
               (id, numero_expediente, tipo_plano, nombre_topografo,
                telefono_cliente, estado_actual, municipalidad,
                fecha_creacion, fecha_actualizacion, metadata_json,
                completado, cancelado)
               VALUES (?, ?, 'segregacion', 'Test Topog',
                       '50612345678', 'recibido', 'San Ramón',
                       '2026-01-01T00:00:00.000Z',
                       '2026-01-01T00:00:00.000Z',
                       '{}', 0, 0)""",
            (exp_id, f"TEST-{exp_id[:8]}"),
        )
        conn.commit()
    return exp_id


def _leer_fecha(db: Database, exp_id: str) -> str:
    with sqlite3.connect(db.path) as conn:
        row = conn.execute(
            "SELECT fecha_actualizacion FROM expedientes WHERE id = ?",
            (exp_id,),
        ).fetchone()
    return row[0]


class TestTriggerExisteEnSchema:
    def test_trigger_creado_al_inicializar_schema(self, db):
        with sqlite3.connect(db.path) as conn:
            triggers = [
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger' "
                    "ORDER BY name"
                )
            ]
        assert "expedientes_touch_fecha_actualizacion" in triggers


class TestTriggerSeDisparaEnUpdateCrudo:
    """El trigger debe disparar cuando un caller hace UPDATE crudo y
    olvida tocar fecha_actualizacion."""

    def test_update_estado_actual_sin_fecha_dispara_trigger(self, db):
        exp_id = _crear_expediente_minimal(db)
        fecha_antes = _leer_fecha(db, exp_id)

        # UPDATE crudo SIN setear fecha_actualizacion
        with sqlite3.connect(db.path) as conn:
            conn.execute(
                "UPDATE expedientes SET estado_actual = ? WHERE id = ?",
                ("listo_para_apt", exp_id),
            )
            conn.commit()

        fecha_despues = _leer_fecha(db, exp_id)
        assert fecha_despues != fecha_antes, (
            "Trigger debió actualizar fecha_actualizacion"
        )
        # El formato del trigger es strftime ISO con Z
        assert fecha_despues.endswith("Z"), (
            f"Trigger debe usar formato ISO con Z, vio {fecha_despues!r}"
        )

    def test_update_metadata_json_sin_fecha_dispara_trigger(self, db):
        exp_id = _crear_expediente_minimal(db)
        fecha_antes = _leer_fecha(db, exp_id)

        # UPDATE crudo de metadata_json sin tocar fecha
        with sqlite3.connect(db.path) as conn:
            conn.execute(
                "UPDATE expedientes SET metadata_json = ? WHERE id = ?",
                (json.dumps({"apt_estado": "Calificación RN"}), exp_id),
            )
            conn.commit()

        fecha_despues = _leer_fecha(db, exp_id)
        assert fecha_despues != fecha_antes

    def test_update_completado_dispara_trigger(self, db):
        exp_id = _crear_expediente_minimal(db)
        fecha_antes = _leer_fecha(db, exp_id)

        with sqlite3.connect(db.path) as conn:
            conn.execute(
                "UPDATE expedientes SET completado = 1 WHERE id = ?",
                (exp_id,),
            )
            conn.commit()

        fecha_despues = _leer_fecha(db, exp_id)
        assert fecha_despues != fecha_antes


class TestTriggerRespetaLlamadosExplicitos:
    """Si el caller setea fecha_actualizacion explícitamente, el trigger
    NO debe sobrescribirla. Esto es el camino común actual."""

    def test_update_con_fecha_explicita_no_es_sobreescrito(self, db):
        exp_id = _crear_expediente_minimal(db)

        fecha_caller = "2099-12-31T23:59:59.000Z"
        with sqlite3.connect(db.path) as conn:
            conn.execute(
                """UPDATE expedientes
                      SET estado_actual = ?,
                          fecha_actualizacion = ?
                    WHERE id = ?""",
                ("listo_para_apt", fecha_caller, exp_id),
            )
            conn.commit()

        fecha_despues = _leer_fecha(db, exp_id)
        assert fecha_despues == fecha_caller, (
            "El trigger NO debe pisar el valor que el caller pasó "
            "explícitamente"
        )

    def test_database_cambiar_estado_sigue_funcionando(self, db):
        """El método público no debe romperse por el trigger."""
        from src.models.estado import Estado

        exp_id = _crear_expediente_minimal(db)
        fecha_antes = _leer_fecha(db, exp_id)

        # Pequeña pausa para garantizar timestamp distinto
        time.sleep(0.01)
        # Usar un estado válido del enum (no hardcode)
        db.cambiar_estado(exp_id, Estado.FORMATO_VALIDADO.value, actor="test")

        fecha_despues = _leer_fecha(db, exp_id)
        assert fecha_despues != fecha_antes


class TestTriggerNoEntraEnRecursion:
    """El trigger interno hace UPDATE de fecha_actualizacion; eso NO debe
    re-disparar el trigger (gracias a UPDATE OF <cols> + WHEN clause)."""

    def test_no_infinite_loop(self, db):
        exp_id = _crear_expediente_minimal(db)

        # Si hubiera recursión, esto colgaría o explotaría el stack
        with sqlite3.connect(db.path) as conn:
            conn.execute(
                "UPDATE expedientes SET estado_actual = ? WHERE id = ?",
                ("listo_para_apt", exp_id),
            )
            conn.commit()

        # Si llegamos acá, no hubo recursión. Verificación de sanidad:
        fecha = _leer_fecha(db, exp_id)
        assert fecha.endswith("Z")


class TestTriggerNoSeDisparaParaColumnasIrrelevantes:
    """El trigger usa UPDATE OF <cols>. Updates a columnas no incluidas
    NO deben disparar (ej. updates a fecha_creacion son raros pero no
    deberían refrescar fecha_actualizacion)."""

    def test_update_fecha_creacion_no_dispara(self, db):
        exp_id = _crear_expediente_minimal(db)
        fecha_antes = _leer_fecha(db, exp_id)

        with sqlite3.connect(db.path) as conn:
            conn.execute(
                "UPDATE expedientes SET fecha_creacion = ? WHERE id = ?",
                ("2030-01-01T00:00:00.000Z", exp_id),
            )
            conn.commit()

        fecha_despues = _leer_fecha(db, exp_id)
        assert fecha_despues == fecha_antes, (
            "UPDATE de fecha_creacion NO debe disparar el trigger "
            "(no está en UPDATE OF)"
        )
