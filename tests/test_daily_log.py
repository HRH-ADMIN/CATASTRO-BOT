"""Tests del módulo daily_log (Sprint 5 / N-09).

Cubre:
  - recopilar: agrega filas de cada tabla en buckets, calcula resúmenes.
  - formatear_markdown / formatear_texto / formatear_html: outputs válidos.
  - guardar_bitacora / leer_bitacora / listar_bitacoras: round-trip filesystem.
  - Día sin actividad → resumen vacío pero no explota.
  - Tolera tablas inexistentes (runtime_processes, external_services).

Plan: PLAN_MEJORAS Sprint 5 / N-09.
"""
from __future__ import annotations
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.core.credential_manager import CredentialManager
from src.core.database import Database
from src.utils import api_costs
from src.utils import pre_envio_snapshot as pes
from src.utils import daily_log


@pytest.fixture
def db_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("CATASTRO_BOT_DEV_MODE", "1")
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("config.settings.DATABASE_PATH", db_path)
    d = Database(path=db_path, credentials=CredentialManager())
    d.initialize_schema()
    # Crear expediente dummy
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


# ─── recopilar ──────────────────────────────────────────────────────

class TestRecopilar:
    def test_dia_sin_actividad_devuelve_estructura_vacia(self, db_paths):
        # Fecha futura sin nada
        data = daily_log.recopilar(db_paths["db"], "2099-01-01")
        assert data["fecha"] == "2099-01-01"
        r = data["resumen"]
        assert r["n_eventos_estado"] == 0
        assert r["n_llamadas_anthropic"] == 0
        assert r["total_usd_anthropic"] == 0
        assert data["estados"] == []

    def test_cuenta_cambios_estado_del_dia(self, db_paths):
        hoy = daily_log._fecha_default()
        # Insertar 2 cambios de estado para hoy
        from datetime import datetime, timezone
        ts1 = f"{hoy}T10:00:00"
        ts2 = f"{hoy}T15:30:00"
        ts_otro_dia = "2099-12-31T10:00:00"
        with sqlite3.connect(db_paths["db"]) as conn:
            for ts, nuevo in [(ts1, "listo_para_apt"),
                               (ts2, "presentado_apt_r1"),
                               (ts_otro_dia, "inscrito")]:
                conn.execute(
                    """INSERT INTO estados_historial
                       (expediente_id, estado_anterior, estado_nuevo,
                        timestamp, actor)
                       VALUES ('EXP-1', 'recibido', ?, ?, 'test')""",
                    (nuevo, ts),
                )
            conn.commit()

        data = daily_log.recopilar(db_paths["db"], hoy)
        assert data["resumen"]["n_eventos_estado"] == 2  # solo los 2 de hoy
        assert data["resumen"]["n_enviados_cfia"] == 1
        # El de día futuro NO está
        otro = daily_log.recopilar(db_paths["db"], "2099-12-31")
        assert otro["resumen"]["n_eventos_estado"] == 1
        assert otro["resumen"]["n_inscritos"] == 1

    def test_costos_se_agregan(self, db_paths):
        hoy = daily_log._fecha_default()
        api_costs.record_call(
            db_paths["db"], model="claude-3-5-haiku-20241022",
            tipo="vision_plano", expediente_id="EXP-1",
            input_tokens=1000, output_tokens=200,
        )
        api_costs.record_call(
            db_paths["db"], model="claude-3-5-sonnet-20241022",
            tipo="minuta_analisis", expediente_id="EXP-1",
            input_tokens=500, output_tokens=100,
        )
        data = daily_log.recopilar(db_paths["db"], hoy)
        assert data["resumen"]["n_llamadas_anthropic"] == 2
        assert data["resumen"]["total_usd_anthropic"] > 0
        assert len(data["costos_resumen"]) == 2

    def test_revisiones_se_agregan(self, db_paths):
        hoy = daily_log._fecha_default()
        pes.crear_revision(
            db_paths["db"], db_paths["root"],
            expediente_id="EXP-1",
            seed_plano={"tamanno": 1}, snapshot_dom={"tamanno": 2},
        )
        data = daily_log.recopilar(db_paths["db"], hoy)
        assert data["resumen"]["n_revisiones"] == 1
        assert data["revisiones"][0]["n_discrepancias"] == 1


# ─── Formatters ─────────────────────────────────────────────────────

class TestFormatters:
    def test_markdown_dia_vacio_tiene_estructura_minima(self, db_paths):
        data = daily_log.recopilar(db_paths["db"], "2099-01-01")
        md = daily_log.formatear_markdown(data)
        assert "# Bitácora del bot — 2099-01-01" in md
        assert "## Resumen del día" in md
        assert "Día sin actividad registrada" in md
        assert md.endswith("Generado automáticamente por `catastro-bot` (N-09).")

    def test_markdown_con_costos_tiene_tabla(self, db_paths):
        hoy = daily_log._fecha_default()
        api_costs.record_call(
            db_paths["db"], model="claude-3-5-haiku-20241022",
            tipo="vision_plano", input_tokens=1000, output_tokens=200,
        )
        data = daily_log.recopilar(db_paths["db"], hoy)
        md = daily_log.formatear_markdown(data)
        assert "## Costos Anthropic" in md
        assert "vision_plano" in md
        assert "Total del día:" in md

    def test_texto_compacto(self, db_paths):
        data = daily_log.recopilar(db_paths["db"], "2099-01-01")
        txt = daily_log.formatear_texto(data)
        assert "📋 Bitácora 2099-01-01" in txt
        assert "Cambios de estado: 0" in txt
        assert "http://localhost:9224" in txt

    def test_html_envuelve_markdown(self, db_paths):
        data = daily_log.recopilar(db_paths["db"], "2099-01-01")
        html = daily_log.formatear_html(data)
        assert html.startswith("<!DOCTYPE html>")
        assert "<pre>" in html
        assert "Bitácora catastro-bot — 2099-01-01" in html

    def test_markdown_escapa_no_explota_con_caracteres_raros(self, db_paths):
        """Si un detalle tiene caracteres exóticos, no debe romper el render."""
        hoy = daily_log._fecha_default()
        with sqlite3.connect(db_paths["db"]) as conn:
            conn.execute(
                """INSERT INTO estados_historial
                   (expediente_id, estado_anterior, estado_nuevo, timestamp,
                    actor, detalles)
                   VALUES ('EXP-1', 'a', 'b', ?, 'test', '|raro| con \\n')""",
                (f"{hoy}T12:00:00",),
            )
            conn.commit()
        data = daily_log.recopilar(db_paths["db"], hoy)
        md = daily_log.formatear_markdown(data)
        assert "## Cambios de estado" in md


# ─── Persistencia FS ───────────────────────────────────────────────

class TestPersistencia:
    def test_guardar_y_leer_round_trip(self, db_paths):
        contenido = "# Test bitácora\n\nHola mundo"
        path = daily_log.guardar_bitacora(db_paths["root"], "2026-05-22", contenido)
        assert path.exists()
        assert path.name == "2026-05-22.md"
        leido = daily_log.leer_bitacora(db_paths["root"], "2026-05-22")
        assert leido == contenido

    def test_leer_inexistente_devuelve_None(self, db_paths):
        assert daily_log.leer_bitacora(db_paths["root"], "2099-12-31") is None

    def test_listar_bitacoras_ordena_por_fecha(self, db_paths):
        daily_log.guardar_bitacora(db_paths["root"], "2026-05-20", "x")
        daily_log.guardar_bitacora(db_paths["root"], "2026-05-22", "y")
        daily_log.guardar_bitacora(db_paths["root"], "2026-05-21", "z")
        lista = daily_log.listar_bitacoras(db_paths["root"])
        assert lista == ["2026-05-20", "2026-05-21", "2026-05-22"]

    def test_listar_dir_vacio_devuelve_lista_vacia(self, tmp_path):
        assert daily_log.listar_bitacoras(tmp_path) == []
