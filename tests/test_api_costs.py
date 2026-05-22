"""Tests del módulo api_costs (Sprint 4 / O-08).

Cubre:
  - Schema (tabla + 3 vistas + tabla api_budget singleton).
  - calcular_costo_usd con modelos conocidos y fallback.
  - record_call persiste + emite evento SSE.
  - track_anthropic_call ejecuta función + registra automáticamente.
  - Reads agregados (mensual, diario, por expediente, recientes).
  - Budget: read/set/current_month_spend/check_budget_alert.
  - check_budget_alert es idempotente por mes (no spam).

Plan: PLAN_MEJORAS Sprint 4 / O-08.
"""
from __future__ import annotations
import sqlite3
import threading
import time
from unittest.mock import MagicMock

import pytest

from src.core.credential_manager import CredentialManager
from src.core.database import Database
from src.utils import api_costs as ac


@pytest.fixture(autouse=True)
def _reset_bus():
    from src.utils.event_bus import reset_bus_for_testing
    reset_bus_for_testing()
    yield
    reset_bus_for_testing()


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    monkeypatch.setenv("CATASTRO_BOT_DEV_MODE", "1")
    p = tmp_path / "test.db"
    monkeypatch.setattr("config.settings.DATABASE_PATH", p)
    d = Database(path=p, credentials=CredentialManager())
    d.initialize_schema()
    return p


# ─── Schema ─────────────────────────────────────────────────────────

class TestSchema:
    def test_tabla_api_costs_existe(self, db_path):
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='api_costs'"
            ).fetchone()
        assert row is not None

    def test_vistas_creadas(self, db_path):
        with sqlite3.connect(db_path) as conn:
            views = sorted(r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='view' "
                "AND name LIKE 'v_api_costs%'"))
        assert "v_api_costs_mensual" in views
        assert "v_api_costs_diario" in views
        assert "v_api_costs_por_expediente" in views

    def test_budget_singleton_inicial(self, db_path):
        b = ac.read_budget(db_path)
        assert b["id"] == 1
        assert b["monthly_usd"] == 50.0
        assert b["alert_threshold"] == 0.80

    def test_budget_singleton_constraint(self, db_path):
        """No se puede insertar fila con id != 1."""
        with pytest.raises(sqlite3.IntegrityError):
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    "INSERT INTO api_budget (id, monthly_usd) VALUES (2, 100)"
                )
                conn.commit()


# ─── calcular_costo_usd ──────────────────────────────────────────────

class TestCalcularCosto:
    def test_modelo_conocido_sonnet(self):
        # Sonnet 3.5: $3/M in + $15/M out
        c = ac.calcular_costo_usd(
            "claude-3-5-sonnet-20241022",
            input_tokens=1_000_000, output_tokens=0,
        )
        assert c == 3.0

    def test_modelo_conocido_haiku(self):
        c = ac.calcular_costo_usd(
            "claude-3-5-haiku-20241022",
            input_tokens=1_000_000, output_tokens=1_000_000,
        )
        # 0.80 + 4.00 = 4.80
        assert c == 4.80

    def test_cache_tokens_son_mas_baratos(self):
        sin_cache = ac.calcular_costo_usd(
            "claude-3-5-sonnet-20241022",
            input_tokens=1_000_000, output_tokens=0,
        )
        con_cache = ac.calcular_costo_usd(
            "claude-3-5-sonnet-20241022",
            input_tokens=0, output_tokens=0,
            cache_read_tokens=1_000_000,
        )
        # cache_read es 10x más barato que input regular
        assert con_cache < sin_cache
        assert con_cache == 0.30

    def test_modelo_desconocido_usa_default(self):
        """No debe explotar — usa tarifa default conservadora."""
        c = ac.calcular_costo_usd(
            "modelo-inventado",
            input_tokens=1_000, output_tokens=500,
        )
        assert c > 0  # algún valor calculado

    def test_zero_tokens_es_zero_costo(self):
        c = ac.calcular_costo_usd(
            "claude-3-5-sonnet-20241022",
            input_tokens=0, output_tokens=0,
        )
        assert c == 0.0


# ─── record_call ─────────────────────────────────────────────────────

class TestRecordCall:
    def test_persiste_fila_y_calcula_costo(self, db_path):
        r = ac.record_call(
            db_path, model="claude-3-5-sonnet-20241022",
            tipo="vision_plano", expediente_id="EXP-001",
            input_tokens=2000, output_tokens=500,
            duration_ms=1234,
        )
        assert r["cost_usd"] > 0
        assert r["expediente_id"] == "EXP-001"
        assert r["tipo"] == "vision_plano"

        # Verificar persistencia
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM api_costs WHERE id = ?", (r["id"],),
            ).fetchone()
        assert row["model"] == "claude-3-5-sonnet-20241022"
        assert row["duration_ms"] == 1234

    def test_record_publica_evento_sse(self, db_path):
        from src.utils.event_bus import get_bus
        bus = get_bus()
        received = []
        ready = threading.Event()

        def consumer():
            for ev in bus.subscribe():
                if not ready.is_set():
                    ready.set()
                received.append(ev)
                if any(e.get("type") == "api_cost_recorded" for e in received):
                    break

        t = threading.Thread(target=consumer, daemon=True)
        t.start()
        bus.publish({"type": "kickstart"})
        ready.wait(timeout=2.0)

        ac.record_call(
            db_path, model="claude-3-5-haiku-20241022",
            tipo="minuta_analisis",
            input_tokens=100, output_tokens=50,
        )

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if any(e.get("type") == "api_cost_recorded" for e in received):
                break
            time.sleep(0.02)
        t.join(timeout=2.0)

        evs = [e for e in received if e.get("type") == "api_cost_recorded"]
        assert len(evs) >= 1
        assert evs[0]["tipo"] == "minuta_analisis"


# ─── track_anthropic_call ────────────────────────────────────────────

class TestTrackAnthropicCall:
    def test_ejecuta_y_registra(self, db_path):
        fake_response = MagicMock()
        fake_response.usage = MagicMock(
            input_tokens=1000, output_tokens=200,
            cache_read_input_tokens=50, cache_creation_input_tokens=0,
        )

        def fake_call():
            return fake_response

        result = ac.track_anthropic_call(
            fake_call, db_path=db_path,
            model="claude-3-5-sonnet-20241022",
            tipo="vision_test", expediente_id="EXP-T1",
        )

        assert result is fake_response

        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = list(conn.execute(
                "SELECT * FROM api_costs WHERE tipo='vision_test'"))
        assert len(rows) == 1
        assert rows[0]["expediente_id"] == "EXP-T1"
        assert rows[0]["input_tokens"] == 1000
        assert rows[0]["cache_read_tokens"] == 50

    def test_response_sin_usage_no_explora(self, db_path):
        """Si la respuesta no tiene .usage, NO registra pero NO falla."""
        fake_response = MagicMock(spec=[])  # sin .usage

        result = ac.track_anthropic_call(
            lambda: fake_response, db_path=db_path,
            model="x", tipo="test",
        )
        assert result is fake_response
        # No debe haber persistido
        with sqlite3.connect(db_path) as conn:
            n = conn.execute("SELECT COUNT(*) FROM api_costs").fetchone()[0]
        assert n == 0

    def test_excepcion_en_call_se_propaga(self, db_path):
        def boom():
            raise RuntimeError("API failed")

        with pytest.raises(RuntimeError):
            ac.track_anthropic_call(
                boom, db_path=db_path, model="x", tipo="test",
            )


# ─── Reads ───────────────────────────────────────────────────────────

class TestReads:
    def test_read_monthly_summary(self, db_path):
        ac.record_call(db_path, model="claude-3-5-sonnet-20241022",
                       tipo="t1", input_tokens=1000, output_tokens=200)
        ac.record_call(db_path, model="claude-3-5-haiku-20241022",
                       tipo="t2", input_tokens=500, output_tokens=100)
        summary = ac.read_monthly_summary(db_path)
        assert len(summary) >= 2
        modelos = {s["model"] for s in summary}
        assert "claude-3-5-sonnet-20241022" in modelos

    def test_read_recent_calls_orden_descendente(self, db_path):
        ac.record_call(db_path, model="x", tipo="a", input_tokens=1, output_tokens=1)
        time.sleep(0.01)
        ac.record_call(db_path, model="x", tipo="b", input_tokens=1, output_tokens=1)
        recent = ac.read_recent_calls(db_path, limit=10)
        assert len(recent) == 2
        # Primero el más nuevo (id descendente)
        assert recent[0]["tipo"] == "b"


# ─── Budget ──────────────────────────────────────────────────────────

class TestBudget:
    def test_set_budget_actualiza(self, db_path):
        b = ac.set_budget(db_path, monthly_usd=100.0, alert_threshold=0.90)
        assert b["monthly_usd"] == 100.0
        assert b["alert_threshold"] == 0.90

    def test_set_budget_rechaza_negativo(self, db_path):
        with pytest.raises(ValueError):
            ac.set_budget(db_path, monthly_usd=-1)

    def test_set_budget_rechaza_threshold_invalido(self, db_path):
        with pytest.raises(ValueError):
            ac.set_budget(db_path, monthly_usd=50, alert_threshold=0)
        with pytest.raises(ValueError):
            ac.set_budget(db_path, monthly_usd=50, alert_threshold=2.0)

    def test_current_month_spend_acumula(self, db_path):
        ac.record_call(db_path, model="claude-3-5-haiku-20241022",
                       tipo="t", input_tokens=1_000_000, output_tokens=0)
        # haiku in = $0.80 por 1M tokens
        spend = ac.current_month_spend(db_path)
        assert 0.79 <= spend <= 0.81

    def test_check_budget_alert_dispara_a_threshold(self, db_path):
        ac.set_budget(db_path, monthly_usd=1.0, alert_threshold=0.50)
        # 1M tokens haiku = $0.80 (80% del budget $1)
        ac.record_call(db_path, model="claude-3-5-haiku-20241022",
                       tipo="t", input_tokens=1_000_000, output_tokens=0)

        alert = ac.check_budget_alert(db_path)
        assert alert is not None
        assert alert["spend_usd"] >= 0.80
        assert alert["ratio"] >= 0.80

    def test_check_budget_alert_no_dispara_bajo_threshold(self, db_path):
        ac.set_budget(db_path, monthly_usd=100.0, alert_threshold=0.50)
        ac.record_call(db_path, model="claude-3-5-haiku-20241022",
                       tipo="t", input_tokens=1000, output_tokens=0)
        # spend ~ 0.0008 vs budget 100 → muy por debajo
        alert = ac.check_budget_alert(db_path)
        assert alert is None

    def test_check_budget_alert_idempotente_por_mes(self, db_path):
        """Una vez alertado este mes, NO vuelve a alertar (anti-spam)."""
        ac.set_budget(db_path, monthly_usd=1.0, alert_threshold=0.50)
        ac.record_call(db_path, model="claude-3-5-haiku-20241022",
                       tipo="t", input_tokens=1_000_000, output_tokens=0)

        a1 = ac.check_budget_alert(db_path)
        a2 = ac.check_budget_alert(db_path)
        a3 = ac.check_budget_alert(db_path)
        assert a1 is not None
        assert a2 is None
        assert a3 is None
