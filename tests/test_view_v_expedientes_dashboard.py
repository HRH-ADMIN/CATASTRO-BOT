"""Test de la vista v_expedientes_dashboard (U-04 paso 4).

Verifica el SSOT calculado: columnas extraídas de metadata_json,
contadores agregados de estados_historial, y la columna `divergencia`
que detecta cuando estado_actual quedó atrás respecto a apt/muni.

Plan: PLAN_MEJORAS_catastro-bot_3.md Sprint 1 / U-04 paso 4.
Documento normativo: docs/SCHEMA.md §5.
"""
from __future__ import annotations
import json
import sqlite3
import uuid

import pytest

from src.core.credential_manager import CredentialManager
from src.core.database import Database


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CATASTRO_BOT_DEV_MODE", "1")
    db_path = tmp_path / "test_catastro.db"
    monkeypatch.setattr("config.settings.DATABASE_PATH", db_path)
    d = Database(path=db_path, credentials=CredentialManager())
    d.initialize_schema()
    return d


def _crear_expediente(
    db: Database,
    *,
    numero: str | None = None,
    estado: str = "recibido",
    metadata: dict | None = None,
) -> str:
    """Inserta un expediente directo (sin pasar por crear_expediente)
    para poder forzar combinaciones específicas de estado + metadata."""
    exp_id = str(uuid.uuid4())
    numero = numero or f"TEST-{exp_id[:8]}"
    with sqlite3.connect(db.path) as conn:
        conn.execute(
            """INSERT INTO expedientes
               (id, numero_expediente, tipo_plano, nombre_topografo,
                telefono_cliente, estado_actual, municipalidad,
                fecha_creacion, fecha_actualizacion, metadata_json,
                completado, cancelado)
               VALUES (?, ?, 'segregacion', 'Test',
                       '50612345678', ?, 'San Ramón',
                       '2026-01-01T00:00:00.000Z',
                       '2026-01-01T00:00:00.000Z',
                       ?, 0, 0)""",
            (exp_id, numero, estado, json.dumps(metadata or {})),
        )
        conn.commit()
    return exp_id


def _query_view(db: Database, where_id: str | None = None) -> list[sqlite3.Row]:
    with sqlite3.connect(db.path) as conn:
        conn.row_factory = sqlite3.Row
        sql = "SELECT * FROM v_expedientes_dashboard"
        params: tuple = ()
        if where_id:
            sql += " WHERE id = ?"
            params = (where_id,)
        return list(conn.execute(sql, params))


class TestVistaExiste:
    def test_vista_creada_al_inicializar_schema(self, db):
        with sqlite3.connect(db.path) as conn:
            views = [
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='view'"
                )
            ]
        assert "v_expedientes_dashboard" in views

    def test_vista_se_recrea_idempotente(self, db):
        """Re-inicializar el schema no debe romper la vista."""
        db.initialize_schema()
        db.initialize_schema()
        # Sin excepción, la vista sigue queryable
        rows = _query_view(db)
        assert isinstance(rows, list)


class TestExtraccionDeMetadata:
    def test_apt_estado_se_extrae(self, db):
        exp_id = _crear_expediente(
            db,
            metadata={"apt_estado": "Calificación RN", "apt_tramite": "1234567"},
        )
        row = _query_view(db, where_id=exp_id)[0]
        assert row["apt_estado"] == "Calificación RN"
        assert row["apt_tramite"] == "1234567"

    def test_muni_estado_se_extrae(self, db):
        exp_id = _crear_expediente(
            db,
            estado="formulario_muni_enviado",
            metadata={"muni_estado": "morosidad", "muni_monto_pendiente": "125000"},
        )
        row = _query_view(db, where_id=exp_id)[0]
        assert row["muni_estado"] == "morosidad"
        assert row["muni_monto_pendiente"] == "125000"

    def test_metadata_json_NO_expuesto_directamente(self, db):
        """Defensa contra PII: la vista NO debe tener columna metadata_json."""
        exp_id = _crear_expediente(
            db,
            metadata={"cedula_cliente_secreta": "1-2345-6789"},
        )
        row = _query_view(db, where_id=exp_id)[0]
        assert "metadata_json" not in row.keys(), (
            "v_expedientes_dashboard NO debe exponer metadata_json crudo"
        )
        assert "cedula_cliente_secreta" not in row.keys()


class TestColumnaDivergenciaApt:
    def test_apt_defectuoso_detectado(self, db):
        exp_id = _crear_expediente(
            db,
            estado="presentado_apt_r1",
            metadata={"apt_estado": "Público y Defectuoso"},
        )
        row = _query_view(db, where_id=exp_id)[0]
        assert row["divergencia"] == "apt:defectuoso"

    def test_apt_inscrito_detectado(self, db):
        exp_id = _crear_expediente(
            db,
            estado="presentado_apt_r1",
            metadata={"apt_estado": "Público e Inscrito"},
        )
        row = _query_view(db, where_id=exp_id)[0]
        assert row["divergencia"] == "apt:inscrito"

    def test_apt_calificacion_NO_es_divergencia(self, db):
        """estado_actual=presentado_apt_r1 + apt_estado='Calificación RN'
        es el caso consistente — no debe marcar divergencia."""
        exp_id = _crear_expediente(
            db,
            estado="presentado_apt_r1",
            metadata={"apt_estado": "Calificación RN"},
        )
        row = _query_view(db, where_id=exp_id)[0]
        assert row["divergencia"] is None

    def test_apt_estado_sin_estado_actual_relevante_no_diverge(self, db):
        """Si el estado_actual del bot es 'recibido' (early), apt_estado
        no debería disparar divergencia — el bot todavía no envió nada."""
        exp_id = _crear_expediente(
            db,
            estado="recibido",
            metadata={"apt_estado": "Público y Defectuoso"},  # no debería pasar pero defensa
        )
        row = _query_view(db, where_id=exp_id)[0]
        assert row["divergencia"] is None


class TestColumnaDivergenciaMuni:
    def test_muni_aprobado_detectado(self, db):
        exp_id = _crear_expediente(
            db,
            estado="formulario_muni_enviado",
            metadata={"muni_estado": "aprobado"},
        )
        row = _query_view(db, where_id=exp_id)[0]
        assert row["divergencia"] == "muni:aprobado"

    def test_muni_morosidad_detectado(self, db):
        exp_id = _crear_expediente(
            db,
            estado="formulario_muni_enviado",
            metadata={"muni_estado": "morosidad"},
        )
        row = _query_view(db, where_id=exp_id)[0]
        assert row["divergencia"] == "muni:morosidad"


class TestAgregaciones:
    def test_n_transiciones_es_0_si_no_hay_historial(self, db):
        exp_id = _crear_expediente(db)
        row = _query_view(db, where_id=exp_id)[0]
        assert row["n_transiciones"] == 0
        assert row["ultimo_evento_ts"] is None

    def test_n_transiciones_cuenta_eventos(self, db):
        from src.models.estado import Estado

        exp_id = _crear_expediente(db, estado=Estado.RECIBIDO.value)
        # 2 transiciones
        db.cambiar_estado(exp_id, Estado.FORMATO_VALIDADO.value, actor="test")
        db.cambiar_estado(exp_id, Estado.PAGO_CLIENTE_PENDIENTE.value, actor="test")

        row = _query_view(db, where_id=exp_id)[0]
        assert row["n_transiciones"] == 2
        assert row["ultimo_evento_ts"] is not None


class TestPasthroughDeCampos:
    def test_campos_directos_de_expedientes_pasan_tal_cual(self, db):
        exp_id = _crear_expediente(db, numero="EXP-PASS-001")
        row = _query_view(db, where_id=exp_id)[0]
        assert row["id"] == exp_id
        assert row["numero_expediente"] == "EXP-PASS-001"
        assert row["tipo_plano"] == "segregacion"
        assert row["completado"] == 0
        assert row["cancelado"] == 0
