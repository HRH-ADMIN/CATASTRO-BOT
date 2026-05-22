"""Tests para src/scheduler/tasks.py — jobs Semana 9 + Semana 10.

Cubre:
  - _verify_audit_safe: llama verify_audit_chain + loguea
  - _verify_audit_safe: excepción no se propaga
  - _backup_db: crea archivo en _BACKUP_DIR con nombre correcto
  - _backup_db: BD no encontrada → warning sin excepción
  - _backup_db: purga backups con más de _BACKUP_KEEP días
  - _stale_alert: sin expedientes stale → no envía mensaje
  - _stale_alert: con expedientes stale → envía a primer admin
  - _stale_alert: sin admins → no explota
  - _weekly_report: construye mensaje y envía a admin
  - _weekly_report: sin admins → no explota
  - _horas_desde: timestamp válido → int aproximado
  - _horas_desde: timestamp inválido → -1
  - _renotificar_correcciones: expediente reciente → no re-notifica
  - _renotificar_correcciones: expediente antiguo APT_CORRECCIONES → re-notifica
  - _renotificar_correcciones: expediente antiguo APT_TRASLAPES → re-notifica
  - _renotificar_correcciones: excepción interna no se propaga
  - register_jobs: registra exactamente 6 jobs
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

from src.scheduler.tasks import (
    _backup_db,
    _horas_desde,
    _renotificar_correcciones,
    _stale_alert,
    _verify_audit_safe,
    _weekly_report,
    register_jobs,
    _BACKUP_DIR,
    _BACKUP_KEEP,
)


# ── helpers ────────────────────────────────────────────────────────────────────

def _make_orchestrator(
    *,
    stale=None,
    resumen=None,
    admins=None,
    db_path=None,
    expedientes_correcciones=None,
    expedientes_traslapes=None,
):
    orc = MagicMock()
    orc.db.verify_audit_chain.return_value = 42
    orc.db.expedientes_stale.return_value = stale or []
    orc.db.resumen_diario.return_value = resumen or {
        "activos": 5,
        "bloqueados": 1,
        "completados_semana": 2,
        "por_tipo": {"segregacion": 3, "rectificacion": 2},
    }
    orc.db.listar_usuarios.return_value = (
        admins if admins is not None
        else [{"nombre": "Admin Test", "telefono": "50688887777"}]
    )
    if db_path is not None:
        orc.db.path = db_path
    else:
        orc.db.path = Path("/nonexistent/catastro.db")

    # Para _renotificar_correcciones: listar_expedientes devuelve según estado
    def _listar(*, estado=None, completados=None, **_kw):
        from src.models.estado import Estado
        if estado == Estado.APT_CORRECCIONES.value:
            return expedientes_correcciones or []
        if estado == Estado.APT_TRASLAPES.value:
            return expedientes_traslapes or []
        return []
    orc.db.listar_expedientes.side_effect = _listar

    return orc


def _stale_exp_renotif(
    numero: str = "EXP-001",
    estado: str = "apt_correcciones",
    horas_sin_actividad: int = 30,
) -> dict:
    """Expediente mock para tests de _renotificar_correcciones."""
    from src.scheduler.tasks import _horas_desde
    from datetime import datetime, timedelta, timezone
    ts = (datetime.now(timezone.utc) - timedelta(hours=horas_sin_actividad)).isoformat()
    return {
        "id": f"id-{numero}",
        "numero_expediente": numero,
        "tipo_plano": "segregacion",
        "estado_actual": estado,
        "telefono_cliente": "50688887777",
        "fecha_actualizacion": ts,
        "metadata_json": "{}",
    }


def _stale_exp(numero: str = "EXP-001", estado: str = "enteros_pagados") -> dict:
    ts_old = (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat()
    return {
        "numero_expediente": numero,
        "tipo_plano": "segregacion",
        "estado_actual": estado,
        "fecha_actualizacion": ts_old,
    }


# ── _verify_audit_safe ────────────────────────────────────────────────────────

class TestVerifyAuditSafe:
    def test_llama_verify_y_loguea(self):
        orc = _make_orchestrator()
        _verify_audit_safe(orc)
        orc.db.verify_audit_chain.assert_called_once()

    def test_excepcion_no_se_propaga(self):
        orc = _make_orchestrator()
        orc.db.verify_audit_chain.side_effect = RuntimeError("DB error")
        # No debe lanzar
        _verify_audit_safe(orc)


# ── _backup_db ────────────────────────────────────────────────────────────────

class TestBackupDb:
    def test_crea_backup_en_dir_correcto(self, tmp_path):
        # Crear una BD real para backupear
        db_file = tmp_path / "catastro.db"
        db_file.write_bytes(b"fake db content")

        orc = _make_orchestrator(db_path=db_file)
        # VACUUM INTO solo funciona con sqlite3 real — usar shutil fallback
        orc.db.connect.return_value.__enter__ = MagicMock(
            side_effect=Exception("vacuum not supported")
        )
        orc.db.connect.return_value.__exit__ = MagicMock(return_value=False)

        backup_dir_override = tmp_path / "backups"

        with patch("src.scheduler.tasks._BACKUP_DIR", backup_dir_override):
            _backup_db(orc)

        archivos = list(backup_dir_override.glob("catastro-*.db"))
        assert len(archivos) == 1
        hoy = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert f"catastro-{hoy}.db" == archivos[0].name

    def test_bd_no_encontrada_no_explota(self, tmp_path):
        orc = _make_orchestrator(db_path=tmp_path / "no_existe.db")
        # No debe lanzar
        with patch("src.scheduler.tasks._BACKUP_DIR", tmp_path / "backups"):
            _backup_db(orc)

    def test_purga_backups_viejos(self, tmp_path):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        # Crear un backup "antiguo" (más de _BACKUP_KEEP días)
        old_file = backup_dir / "catastro-2020-01-01.db"
        old_file.write_bytes(b"old backup")
        # Modificar mtime para que sea muy viejo
        ancient = time.time() - (_BACKUP_KEEP + 1) * 86_400
        import os
        os.utime(old_file, (ancient, ancient))

        db_file = tmp_path / "catastro.db"
        db_file.write_bytes(b"fake db")
        orc = _make_orchestrator(db_path=db_file)
        orc.db.connect.return_value.__enter__ = MagicMock(
            side_effect=Exception("vacuum not supported")
        )
        orc.db.connect.return_value.__exit__ = MagicMock(return_value=False)

        with patch("src.scheduler.tasks._BACKUP_DIR", backup_dir):
            _backup_db(orc)

        # El archivo viejo debe haber sido eliminado
        assert not old_file.exists()


# ── _stale_alert ──────────────────────────────────────────────────────────────

class TestStaleAlert:
    def test_sin_stale_no_envia(self):
        orc = _make_orchestrator(stale=[])
        _stale_alert(orc)
        orc.whatsapp.enviar_mensaje.assert_not_called()

    def test_con_stale_envia_al_admin(self):
        stale = [_stale_exp("EXP-001"), _stale_exp("EXP-002")]
        orc = _make_orchestrator(stale=stale)
        _stale_alert(orc)
        orc.whatsapp.enviar_mensaje.assert_called_once()
        args = orc.whatsapp.enviar_mensaje.call_args[0]
        assert "50688887777" in args[0]
        assert "EXP-001" in args[1] or "2 expediente" in args[1]

    def test_sin_admins_no_explota(self):
        orc = _make_orchestrator(
            stale=[_stale_exp()],
            admins=[],
        )
        # No debe lanzar
        _stale_alert(orc)
        orc.whatsapp.enviar_mensaje.assert_not_called()

    def test_excepcion_interna_no_se_propaga(self):
        orc = _make_orchestrator()
        orc.db.expedientes_stale.side_effect = RuntimeError("DB error")
        _stale_alert(orc)  # no debe lanzar

    def test_trunca_a_10_en_mensaje(self):
        stale = [_stale_exp(f"EXP-{i:03d}") for i in range(15)]
        orc = _make_orchestrator(stale=stale)
        _stale_alert(orc)
        msg = orc.whatsapp.enviar_mensaje.call_args[0][1]
        assert "15 expediente" in msg
        # El mensaje no debe listar los 15 sino máx 10 + el "... y N más"
        assert msg.count("EXP-") <= 10


# ── _weekly_report ────────────────────────────────────────────────────────────

class TestWeeklyReport:
    def test_envia_a_admin(self):
        orc = _make_orchestrator()
        _weekly_report(orc)
        orc.whatsapp.enviar_mensaje.assert_called_once()
        args = orc.whatsapp.enviar_mensaje.call_args[0]
        assert "50688887777" in args[0]
        assert "Reporte" in args[1] or "reporte" in args[1]

    def test_mensaje_incluye_stats(self):
        orc = _make_orchestrator()
        _weekly_report(orc)
        msg = orc.whatsapp.enviar_mensaje.call_args[0][1]
        assert "5" in msg   # activos
        assert "2" in msg   # completados

    def test_sin_admins_no_explota(self):
        orc = _make_orchestrator(admins=[])
        _weekly_report(orc)  # no debe lanzar

    def test_excepcion_no_se_propaga(self):
        orc = _make_orchestrator()
        orc.db.resumen_diario.side_effect = RuntimeError("error")
        _weekly_report(orc)  # no debe lanzar

    def test_multiples_admins_envia_a_todos(self):
        admins = [
            {"nombre": "Admin1", "telefono": "50688880001"},
            {"nombre": "Admin2", "telefono": "50688880002"},
        ]
        orc = _make_orchestrator(admins=admins)
        _weekly_report(orc)
        assert orc.whatsapp.enviar_mensaje.call_count == 2


# ── _horas_desde ──────────────────────────────────────────────────────────────

class TestHorasDesde:
    def test_timestamp_reciente(self):
        ts = datetime.now(timezone.utc).isoformat()
        h = _horas_desde(ts)
        assert 0 <= h <= 1

    def test_timestamp_antiguo(self):
        ts = (datetime.now(timezone.utc) - timedelta(hours=50)).isoformat()
        h = _horas_desde(ts)
        assert 49 <= h <= 51

    def test_timestamp_invalido(self):
        assert _horas_desde("no-es-fecha") == -1

    def test_cadena_vacia(self):
        assert _horas_desde("") == -1


# ── _renotificar_correcciones ─────────────────────────────────────────────────

class TestRenotificarCorrecciones:
    def test_expediente_reciente_no_renotifica(self):
        """Si la última notificación fue hace < 24 h → no envía mensaje."""
        exp = _stale_exp_renotif("EXP-001", horas_sin_actividad=10)
        orc = _make_orchestrator(expedientes_correcciones=[exp])
        _renotificar_correcciones(orc)
        orc.whatsapp.enviar_mensaje.assert_not_called()

    def test_expediente_antiguo_correcciones_renotifica(self):
        """Si llevan > 24 h sin resolver → re-notifica al cliente."""
        exp = _stale_exp_renotif("EXP-002", horas_sin_actividad=30)
        orc = _make_orchestrator(expedientes_correcciones=[exp])
        _renotificar_correcciones(orc)
        orc.whatsapp.enviar_mensaje.assert_called_once()
        args = orc.whatsapp.enviar_mensaje.call_args[0]
        assert "50688887777" in args[0]
        assert "EXP-002" in args[1]

    def test_expediente_antiguo_traslapes_renotifica(self):
        """APT_TRASLAPES también se re-notifica."""
        exp = _stale_exp_renotif("EXP-003", estado="apt_traslapes", horas_sin_actividad=50)
        orc = _make_orchestrator(expedientes_traslapes=[exp])
        _renotificar_correcciones(orc)
        orc.whatsapp.enviar_mensaje.assert_called()
        msg = orc.whatsapp.enviar_mensaje.call_args[0][1]
        assert "traslape" in msg.lower() or "EXP-003" in msg

    def test_excepcion_interna_no_se_propaga(self):
        orc = MagicMock()
        orc.db.listar_expedientes.side_effect = RuntimeError("DB error")
        _renotificar_correcciones(orc)  # no debe lanzar

    def test_actualiza_metadata_tras_renotificacion(self):
        """Después de re-notificar, debe actualizar 'correcciones_ultima_notif'."""
        exp = _stale_exp_renotif("EXP-004", horas_sin_actividad=30)
        orc = _make_orchestrator(expedientes_correcciones=[exp])
        _renotificar_correcciones(orc)
        orc.db.actualizar_metadata.assert_called()
        kw = orc.db.actualizar_metadata.call_args[0]
        # Primer arg es expediente_id, segundo es el dict de metadata
        assert "correcciones_ultima_notif" in kw[1]


# ── register_jobs ─────────────────────────────────────────────────────────────

class TestRegisterJobs:
    def test_registra_7_jobs(self):
        scheduler = MagicMock()
        orc = _make_orchestrator()
        register_jobs(scheduler, orc)
        assert scheduler.add_job.call_count == 7

    def test_ids_de_los_jobs(self):
        scheduler = MagicMock()
        orc = _make_orchestrator()
        register_jobs(scheduler, orc)
        ids_usados = {
            kw.get("id") or call_args[1].get("id")
            for call_args in scheduler.add_job.call_args_list
            for kw in [call_args[1]]
        }
        assert "orchestrator-tick"    in ids_usados
        assert "audit-verify"         in ids_usados
        assert "db-backup"            in ids_usados
        assert "stale-alert"          in ids_usados
        assert "weekly-report"        in ids_usados
        assert "correcciones-renotif" in ids_usados
        assert "apt-sync-estados"     in ids_usados
