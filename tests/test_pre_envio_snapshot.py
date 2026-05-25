"""Tests del módulo pre_envio_snapshot (N-02 sub-paso A).

Cubre:
  - Schema (tabla + CHECK constraint).
  - comparar_campos_criticos: matching exacto, tolerancia numérica,
    detección de mismatch + missing_in_portal.
  - crear_revision: persiste fila, calcula diff, publica SSE, maneja
    screenshot opcional, PDF opcional, paths absolutos vs relativos.
  - Lectura: listar_pendientes, read_revision, listar_por_expediente.
  - Resolución: aprobar/rechazar idempotente + validaciones.

Plan: PLAN_MEJORAS Sprint 5 / N-02.
"""
from __future__ import annotations
import json
import sqlite3
import threading
import time
from unittest.mock import MagicMock

import pytest

from src.core.credential_manager import CredentialManager
from src.core.database import Database
from src.utils import pre_envio_snapshot as pes


@pytest.fixture(autouse=True)
def _reset_bus():
    from src.utils.event_bus import reset_bus_for_testing
    reset_bus_for_testing()
    yield
    reset_bus_for_testing()


@pytest.fixture
def db_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("CATASTRO_BOT_DEV_MODE", "1")
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("config.settings.DATABASE_PATH", db_path)
    d = Database(path=db_path, credentials=CredentialManager())
    d.initialize_schema()
    # Crear un expediente dummy para que la FK no falle
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """INSERT INTO expedientes
               (id, numero_expediente, tipo_plano, nombre_topografo,
                telefono_cliente, estado_actual, municipalidad,
                fecha_creacion, fecha_actualizacion, metadata_json,
                completado, cancelado)
               VALUES ('EXP-1', 'TEST-1', 'segregacion', 'Test',
                       '50612345678', 'recibido', 'San Ramón',
                       '2026-01-01T00:00:00.000Z',
                       '2026-01-01T00:00:00.000Z', '{}', 0, 0)"""
        )
        conn.commit()
    return {"db": db_path, "root": tmp_path}


# ─── Schema ─────────────────────────────────────────────────────────

class TestSchema:
    def test_tabla_existe(self, db_paths):
        with sqlite3.connect(db_paths["db"]) as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='revisiones_pre_envio'"
            ).fetchone()
        assert row is not None

    def test_estado_invalido_rechazado(self, db_paths):
        with pytest.raises(sqlite3.IntegrityError):
            with sqlite3.connect(db_paths["db"]) as conn:
                conn.execute(
                    "INSERT INTO revisiones_pre_envio "
                    "(id, expediente_id, estado, pdf_anverso_path) "
                    "VALUES ('x', 'EXP-1', 'INVALID', 'p.pdf')"
                )
                conn.commit()


# ─── comparar_campos_criticos ────────────────────────────────────────

class TestDiff:
    def test_seed_y_portal_iguales_sin_diff(self):
        seed = {"descripcion": "ALEPOA (1)", "tamanno": 1, "area_real": 2751.30}
        portal = {"descripcion": "ALEPOA (1)", "tamanno": 1, "area_real": 2751.30}
        diffs = pes.comparar_campos_criticos(seed, portal)
        assert diffs == []

    def test_tolerancia_numerica_por_defecto_1pct(self):
        seed = {"area_real": 2751.30}
        portal = {"area_real": 2751.35}  # diferencia < 1%
        diffs = pes.comparar_campos_criticos(seed, portal)
        assert diffs == []

    def test_diferencia_grande_detectada(self):
        seed = {"area_real": 2751.30}
        portal = {"area_real": 2800.0}  # +1.8% — mayor a tolerancia
        diffs = pes.comparar_campos_criticos(seed, portal)
        assert len(diffs) == 1
        assert diffs[0]["campo"] == "area_real"
        assert diffs[0]["tipo"] == "mismatch"

    def test_campo_falta_en_portal(self):
        seed = {"tamanno": 1, "descripcion": "X"}
        portal = {"tamanno": 1}  # falta descripcion
        diffs = pes.comparar_campos_criticos(seed, portal)
        assert len(diffs) == 1
        assert diffs[0]["campo"] == "descripcion"
        assert diffs[0]["tipo"] == "missing_in_portal"

    def test_string_matching_case_insensitive(self):
        seed = {"descripcion": "  ALEPOA (1)  "}
        portal = {"descripcion": "alepoa (1)"}
        diffs = pes.comparar_campos_criticos(seed, portal)
        assert diffs == []  # match tras strip + lower

    def test_ambos_None_no_es_diff(self):
        seed = {"tamanno": None}
        portal = {"tamanno": None}
        diffs = pes.comparar_campos_criticos(seed, portal)
        assert diffs == []


# ─── crear_revision ──────────────────────────────────────────────────

class TestCrearRevision:
    def test_crea_fila_pendiente(self, db_paths):
        seed = {"descripcion": "ALEPOA", "tamanno": 1, "area_real": 2751.0}
        portal = {"descripcion": "ALEPOA", "tamanno": 1, "area_real": 2751.0}
        rev_id = pes.crear_revision(
            db_paths["db"], db_paths["root"],
            expediente_id="EXP-1",
            seed_plano=seed, snapshot_dom=portal,
        )
        assert rev_id  # uuid generado

        rev = pes.read_revision(db_paths["db"], rev_id)
        assert rev["estado"] == "pendiente"
        assert rev["n_discrepancias"] == 0
        assert rev["expediente_id"] == "EXP-1"
        assert rev["diff"] == []  # deserializado

    def test_diff_se_persiste_correctamente(self, db_paths):
        seed = {"tamanno": 1, "area_real": 2751.0}
        portal = {"tamanno": 2, "area_real": 2751.0}  # tamanno difiere
        rev_id = pes.crear_revision(
            db_paths["db"], db_paths["root"],
            expediente_id="EXP-1",
            seed_plano=seed, snapshot_dom=portal,
        )
        rev = pes.read_revision(db_paths["db"], rev_id)
        assert rev["n_discrepancias"] == 1
        assert rev["diff"][0]["campo"] == "tamanno"
        assert rev["diff"][0]["seed"] == 1
        assert rev["diff"][0]["portal"] == 2

    def test_screenshot_se_toma_si_se_pasa_page(self, db_paths):
        fake_page = MagicMock()
        # Simular que page.screenshot crea el archivo
        def fake_screenshot(path, full_page=True):
            from pathlib import Path
            Path(path).write_bytes(b"fake png data")
        fake_page.screenshot.side_effect = fake_screenshot

        rev_id = pes.crear_revision(
            db_paths["db"], db_paths["root"],
            expediente_id="EXP-1",
            seed_plano={}, snapshot_dom={},
            page=fake_page,
        )
        rev = pes.read_revision(db_paths["db"], rev_id)
        assert rev["screenshot_path"] is not None
        assert rev["screenshot_path"].startswith("data/revisiones/")
        # Verificar que el archivo realmente existe
        full = db_paths["root"] / rev["screenshot_path"]
        assert full.exists()
        fake_page.screenshot.assert_called_once()

    def test_publica_evento_sse(self, db_paths):
        from src.utils.event_bus import get_bus
        bus = get_bus()
        received = []
        ready = threading.Event()

        def consumer():
            for ev in bus.subscribe():
                if not ready.is_set():
                    ready.set()
                received.append(ev)
                if any(e.get("type") == "revision_pendiente" for e in received):
                    break

        t = threading.Thread(target=consumer, daemon=True)
        t.start()
        bus.publish({"type": "kickstart"})
        ready.wait(timeout=2.0)

        rev_id = pes.crear_revision(
            db_paths["db"], db_paths["root"],
            expediente_id="EXP-1",
            seed_plano={}, snapshot_dom={},
        )

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if any(e.get("type") == "revision_pendiente" for e in received):
                break
            time.sleep(0.02)
        t.join(timeout=2.0)

        evs = [e for e in received if e.get("type") == "revision_pendiente"]
        assert len(evs) >= 1
        assert evs[0]["id"] == rev_id


# ─── Lectura ─────────────────────────────────────────────────────────

class TestLectura:
    def test_listar_pendientes_filtra_resueltas(self, db_paths):
        r1 = pes.crear_revision(db_paths["db"], db_paths["root"],
                                expediente_id="EXP-1",
                                seed_plano={}, snapshot_dom={})
        r2 = pes.crear_revision(db_paths["db"], db_paths["root"],
                                expediente_id="EXP-1",
                                seed_plano={}, snapshot_dom={})
        pes.aprobar(db_paths["db"], r1)

        pendientes = pes.listar_pendientes(db_paths["db"])
        assert len(pendientes) == 1
        assert pendientes[0]["id"] == r2

    def test_read_revision_None_si_no_existe(self, db_paths):
        assert pes.read_revision(db_paths["db"], "no-existe") is None


# ─── Resolución ──────────────────────────────────────────────────────

class TestResolucion:
    def test_aprobar_marca_estado_y_actor(self, db_paths):
        rev_id = pes.crear_revision(
            db_paths["db"], db_paths["root"],
            expediente_id="EXP-1",
            seed_plano={}, snapshot_dom={},
        )
        result = pes.aprobar(db_paths["db"], rev_id,
                             resuelto_por="test_actor")
        assert result["estado"] == "aprobado"
        assert result["resuelto_por"] == "test_actor"
        assert result["resuelto_at"] is not None

    def test_rechazar_exige_razon(self, db_paths):
        rev_id = pes.crear_revision(
            db_paths["db"], db_paths["root"],
            expediente_id="EXP-1",
            seed_plano={}, snapshot_dom={},
        )
        with pytest.raises(ValueError):
            pes.rechazar(db_paths["db"], rev_id, razon="")

    def test_rechazar_persiste_razon(self, db_paths):
        rev_id = pes.crear_revision(
            db_paths["db"], db_paths["root"],
            expediente_id="EXP-1",
            seed_plano={}, snapshot_dom={},
        )
        pes.rechazar(db_paths["db"], rev_id, razon="Falta carta agua")
        rev = pes.read_revision(db_paths["db"], rev_id)
        assert rev["estado"] == "rechazado"
        assert rev["razon_rechazo"] == "Falta carta agua"

    def test_aprobar_idempotente(self, db_paths):
        rev_id = pes.crear_revision(
            db_paths["db"], db_paths["root"],
            expediente_id="EXP-1",
            seed_plano={}, snapshot_dom={},
        )
        pes.aprobar(db_paths["db"], rev_id)
        # Segundo aprobar no debe explotar (no-op)
        pes.aprobar(db_paths["db"], rev_id)

    def test_no_se_puede_aprobar_rechazada(self, db_paths):
        rev_id = pes.crear_revision(
            db_paths["db"], db_paths["root"],
            expediente_id="EXP-1",
            seed_plano={}, snapshot_dom={},
        )
        pes.rechazar(db_paths["db"], rev_id, razon="No")
        with pytest.raises(ValueError):
            pes.aprobar(db_paths["db"], rev_id)

    def test_aprobar_revision_inexistente_lanza_keyerror(self, db_paths):
        with pytest.raises(KeyError):
            pes.aprobar(db_paths["db"], "no-existe")
