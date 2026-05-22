"""Tests del módulo de métricas + dashboard."""
from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

pytest.importorskip("win32cred", reason="solo Windows")

from src.utils.metricas import (
    resumen_por_estado, resumen_por_tipo, resumen_por_topografo,
    tiempo_promedio_ciclo, ratio_exoneracion_honorarios,
    discrepancias_frecuentes, anomalias_recientes,
    alertas_proactivas, por_estado_apt, resumen_completo,
)
from tests.conftest import FakeCredentialManager, TestDatabase  # noqa: E402


def _make_db(tmp_path):
    creds = FakeCredentialManager()
    db = TestDatabase(path=tmp_path / "t.db", credentials=creds)
    db.initialize_schema()
    return db


def _crear_exp(db, *, numero, tipo="rectificacion", topografo="JUAN PEREZ",
               estado="recibido", meta: dict | None = None):
    eid = db.crear_expediente(
        numero_expediente=numero, tipo_plano=tipo,
        nombre_topografo=topografo,
        telefono_cliente="50611111111", actor="test",
    )
    if estado != "recibido":
        db.cambiar_estado(eid, estado, actor="test")
    if meta:
        db.actualizar_metadata(eid, meta, actor="test")
    return eid


class TestResumenPorEstado:
    def test_empty_db(self, tmp_path):
        db = _make_db(tmp_path)
        assert resumen_por_estado(db) == {}

    def test_agrupa_correctamente(self, tmp_path):
        db = _make_db(tmp_path)
        _crear_exp(db, numero="A1", estado="recibido")
        _crear_exp(db, numero="A2", estado="recibido")
        _crear_exp(db, numero="A3", estado="formato_validado")
        r = resumen_por_estado(db)
        assert r["recibido"] == 2
        assert r["formato_validado"] == 1

    def test_db_none_vacio(self):
        assert resumen_por_estado(None) == {}


class TestResumenPorTipo:
    def test_agrupa_por_tipo(self, tmp_path):
        db = _make_db(tmp_path)
        _crear_exp(db, numero="A1", tipo="rectificacion")
        _crear_exp(db, numero="A2", tipo="segregacion")
        _crear_exp(db, numero="A3", tipo="rectificacion")
        r = resumen_por_tipo(db)
        assert r["rectificacion"] == 2
        assert r["segregacion"] == 1


class TestResumenPorTopografo:
    def test_agrupa_por_nombre(self, tmp_path):
        db = _make_db(tmp_path)
        _crear_exp(db, numero="A1", topografo="LUIS ROJAS")
        _crear_exp(db, numero="A2", topografo="LUIS ROJAS")
        _crear_exp(db, numero="A3", topografo="MARIA SOTO")
        r = resumen_por_topografo(db)
        assert r["LUIS ROJAS"] == 2
        assert r["MARIA SOTO"] == 1


class TestTiempoCiclo:
    def test_calcula_promedio(self, tmp_path):
        db = _make_db(tmp_path)
        # Crear 2 exps con apt_envio_fecha
        _crear_exp(db, numero="A1", meta={
            "apt_envio_fecha": (datetime.now(timezone.utc) +
                                timedelta(days=2)).isoformat(),
        })
        _crear_exp(db, numero="A2", meta={
            "apt_envio_fecha": (datetime.now(timezone.utc) +
                                timedelta(days=4)).isoformat(),
        })
        t = tiempo_promedio_ciclo(db)
        assert t is not None
        # Promedio entre ~2 y ~4 días → ~3
        assert 1 < t < 5

    def test_sin_envio_devuelve_none(self, tmp_path):
        db = _make_db(tmp_path)
        _crear_exp(db, numero="A1")
        assert tiempo_promedio_ciclo(db) is None


class TestRatioExoneracion:
    def test_calcula_porcentaje(self, tmp_path):
        db = _make_db(tmp_path)
        # 3 con honorarios=0, 1 con honorarios=147705
        for n in ("A1", "A2", "A3"):
            _crear_exp(db, numero=n, meta={
                "datos_apt": {"general": {"honorarios": "0"}},
            })
        _crear_exp(db, numero="A4", meta={
            "datos_apt": {"general": {"honorarios": "147705"}},
        })
        ratio = ratio_exoneracion_honorarios(db)
        assert ratio == 75.0  # 3/4

    def test_sin_datos(self, tmp_path):
        db = _make_db(tmp_path)
        _crear_exp(db, numero="A1")
        # No tiene metadata.datos_apt.general.honorarios → no cuenta
        assert ratio_exoneracion_honorarios(db) is None


class TestDiscrepanciasFrecuentes:
    def test_top_n(self, tmp_path):
        db = _make_db(tmp_path)
        _crear_exp(db, numero="A1", meta={
            "apt_discrepancias_rnp": [
                {"tipo": "rnp_tse_mismatch", "cedula": "X"},
                {"tipo": "protocolo_diferente_al_activo"},
            ]
        })
        _crear_exp(db, numero="A2", meta={
            "apt_discrepancias_rnp": [
                {"tipo": "rnp_tse_mismatch", "cedula": "Y"},
                {"tipo": "rnp_tse_mismatch", "cedula": "Z"},
            ]
        })
        top = discrepancias_frecuentes(db, top=5)
        assert top[0] == ("rnp_tse_mismatch", 3)
        assert top[1] == ("protocolo_diferente_al_activo", 1)


class TestAnomaliasRecientes:
    def test_ordena_descendente(self, tmp_path):
        db = _make_db(tmp_path)
        _crear_exp(db, numero="A1", meta={
            "apt_anomalias": [
                {"ts": "2026-01-01T10:00:00", "contexto": "bC5", "descripcion": "x1"},
                {"ts": "2026-03-01T10:00:00", "contexto": "bC7", "descripcion": "x3"},
            ]
        })
        _crear_exp(db, numero="A2", meta={
            "apt_anomalias": [
                {"ts": "2026-02-01T10:00:00", "contexto": "bP2", "descripcion": "x2"},
            ]
        })
        anom = anomalias_recientes(db)
        # Más reciente primero
        assert anom[0]["descripcion"] == "x3"
        assert anom[1]["descripcion"] == "x2"
        assert anom[2]["descripcion"] == "x1"

    def test_incluye_numero_expediente(self, tmp_path):
        db = _make_db(tmp_path)
        _crear_exp(db, numero="A1", meta={
            "apt_anomalias": [{"ts": "2026-01-01", "contexto": "x", "descripcion": "y"}],
            "nombre_proyecto": "PROYECTO_X",
        })
        anom = anomalias_recientes(db)
        assert anom[0]["numero_expediente"] == "A1"
        assert "PROYECTO_X" in anom[0]["display"]


class TestAlertasProactivas:
    def test_detecta_patron_repetido(self, tmp_path):
        """3+ anomalías en mismo contexto → alerta."""
        db = _make_db(tmp_path)
        for n in ("A1", "A2", "A3", "A4"):
            _crear_exp(db, numero=n, meta={
                "apt_anomalias": [
                    {"ts": "2026-01-01", "contexto": "bC5 PROYECTO",
                     "descripcion": "validación falló"},
                ]
            })
        alerts = alertas_proactivas(db, umbral_anomalias_mismo_contexto=3)
        assert any("bC5 PROYECTO" in a for a in alerts)

    def test_sin_patron_sin_alertas(self, tmp_path):
        db = _make_db(tmp_path)
        _crear_exp(db, numero="A1", meta={
            "apt_anomalias": [{"ts": "2026-01-01", "contexto": "X"}],
        })
        # Solo 1 anomalía — no llega al umbral de 3
        assert alertas_proactivas(db) == []


class TestPorEstadoApt:
    def test_agrupa_por_apt_estado(self, tmp_path):
        db = _make_db(tmp_path)
        _crear_exp(db, numero="A1", meta={"apt_estado": "enviado_cfia"})
        _crear_exp(db, numero="A2", meta={"apt_estado": "enviado_cfia"})
        _crear_exp(db, numero="A3", meta={"apt_tramite": "1258126"})  # sin estado
        _crear_exp(db, numero="A4")  # sin contrato APT
        r = por_estado_apt(db)
        assert r["enviado_cfia"] == 2
        assert r["(con trámite, sin estado)"] == 1
        assert r["(sin contrato APT)"] == 1


class TestResumenCompleto:
    def test_estructura_completa(self, tmp_path):
        db = _make_db(tmp_path)
        _crear_exp(db, numero="A1", tipo="rectificacion",
                   topografo="LUIS ROJAS", estado="presentado_apt_r1",
                   meta={"apt_estado": "enviado_cfia", "apt_tramite": "X"})
        r = resumen_completo(db)
        # Llaves esperadas
        for k in ("total_expedientes", "por_estado", "por_tipo",
                  "por_topografo", "por_estado_apt",
                  "tiempo_promedio_creacion_a_envio_dias",
                  "ratio_exoneracion_pct", "discrepancias_frecuentes",
                  "anomalias_recientes", "alertas_proactivas"):
            assert k in r
        assert r["total_expedientes"] == 1
        assert r["por_topografo"]["LUIS ROJAS"] == 1
