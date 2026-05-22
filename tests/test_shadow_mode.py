"""Tests del shadow_mode — rollout gradual del bot."""
from __future__ import annotations
import pytest

from src.utils.shadow_mode import (
    SaveMode,
    ACCION_GUARDAR_CONTRATO,
    ACCION_GUARDAR_SECCION_PLANO,
    ACCION_ENVIAR_CFIA,
    get_mode_for_action,
    should_proceed,
    explicar_decision,
)


class TestGetModeForAction:
    def test_defaults_conservadores(self, monkeypatch):
        # Limpiar env vars y settings
        monkeypatch.delenv("BOT_SAVE_MODE_CONTRATO", raising=False)
        monkeypatch.delenv("BOT_SAVE_MODE_PLANO_SECCION", raising=False)
        monkeypatch.delenv("BOT_SAVE_MODE_ENVIAR_CFIA", raising=False)
        monkeypatch.setattr("config.settings.BOT_SAVE_MODE_CONTRATO", "manual")
        monkeypatch.setattr("config.settings.BOT_SAVE_MODE_PLANO_SECCION", "auto_if_clean")
        monkeypatch.setattr("config.settings.BOT_SAVE_MODE_ENVIAR_CFIA", "manual")
        assert get_mode_for_action(ACCION_GUARDAR_CONTRATO) == SaveMode.MANUAL
        assert get_mode_for_action(ACCION_GUARDAR_SECCION_PLANO) == SaveMode.AUTO_IF_CLEAN
        assert get_mode_for_action(ACCION_ENVIAR_CFIA) == SaveMode.MANUAL

    def test_env_var_override(self, monkeypatch):
        monkeypatch.setenv("BOT_SAVE_MODE_CONTRATO", "auto")
        assert get_mode_for_action(ACCION_GUARDAR_CONTRATO) == SaveMode.AUTO

    def test_settings_override_cuando_no_hay_env(self, monkeypatch):
        monkeypatch.delenv("BOT_SAVE_MODE_CONTRATO", raising=False)
        monkeypatch.setattr("config.settings.BOT_SAVE_MODE_CONTRATO", "auto_if_clean")
        assert get_mode_for_action(ACCION_GUARDAR_CONTRATO) == SaveMode.AUTO_IF_CLEAN

    def test_env_invalido_cae_a_default(self, monkeypatch):
        monkeypatch.setenv("BOT_SAVE_MODE_CONTRATO", "invalid_mode")
        monkeypatch.setattr("config.settings.BOT_SAVE_MODE_CONTRATO", "manual")
        assert get_mode_for_action(ACCION_GUARDAR_CONTRATO) == SaveMode.MANUAL

    def test_accion_invalida_lanza(self):
        with pytest.raises(ValueError):
            get_mode_for_action("accion_inventada")


class TestShouldProceed:
    def test_manual_siempre_pausa(self, monkeypatch):
        monkeypatch.setenv("BOT_SAVE_MODE_CONTRATO", "manual")
        # Sin discrepancias, sin anomalía → aún así pausa
        assert should_proceed(
            ACCION_GUARDAR_CONTRATO, discrepancias=[], has_anomaly=False
        ) is False

    def test_auto_if_clean_procede_sin_discrepancias(self, monkeypatch):
        monkeypatch.setenv("BOT_SAVE_MODE_CONTRATO", "auto_if_clean")
        assert should_proceed(
            ACCION_GUARDAR_CONTRATO, discrepancias=[], has_anomaly=False
        ) is True

    def test_auto_if_clean_pausa_con_discrepancias(self, monkeypatch):
        monkeypatch.setenv("BOT_SAVE_MODE_CONTRATO", "auto_if_clean")
        assert should_proceed(
            ACCION_GUARDAR_CONTRATO,
            discrepancias=[{"tipo": "rnp_tse_mismatch"}],
            has_anomaly=False,
        ) is False

    def test_auto_procede_aunque_haya_discrepancias(self, monkeypatch):
        monkeypatch.setenv("BOT_SAVE_MODE_CONTRATO", "auto")
        assert should_proceed(
            ACCION_GUARDAR_CONTRATO,
            discrepancias=[{"tipo": "rnp_tse_mismatch"}],
            has_anomaly=False,
        ) is True

    def test_anomalia_siempre_pausa_aunque_mode_auto(self, monkeypatch):
        """Circuit breaker — has_anomaly=True siempre detiene."""
        monkeypatch.setenv("BOT_SAVE_MODE_CONTRATO", "auto")
        assert should_proceed(
            ACCION_GUARDAR_CONTRATO,
            discrepancias=[],
            has_anomaly=True,
        ) is False


class TestExplicarDecision:
    def test_explica_modo_manual(self, monkeypatch):
        monkeypatch.setenv("BOT_SAVE_MODE_CONTRATO", "manual")
        s = explicar_decision(ACCION_GUARDAR_CONTRATO, discrepancias=[])
        assert "PAUSA" in s
        assert "manual" in s.lower()

    def test_explica_anomalia(self, monkeypatch):
        monkeypatch.setenv("BOT_SAVE_MODE_CONTRATO", "auto")
        s = explicar_decision(
            ACCION_GUARDAR_CONTRATO, discrepancias=[], has_anomaly=True,
        )
        assert "PAUSA" in s
        assert "anomalía" in s.lower() or "anomalia" in s.lower()

    def test_explica_discrepancias_en_auto_if_clean(self, monkeypatch):
        monkeypatch.setenv("BOT_SAVE_MODE_CONTRATO", "auto_if_clean")
        s = explicar_decision(
            ACCION_GUARDAR_CONTRATO,
            discrepancias=[{"x": 1}, {"y": 2}],
        )
        assert "PAUSA" in s
        assert "2 discrepancia" in s

    def test_explica_proceder_clean(self, monkeypatch):
        monkeypatch.setenv("BOT_SAVE_MODE_CONTRATO", "auto_if_clean")
        s = explicar_decision(ACCION_GUARDAR_CONTRATO, discrepancias=[])
        assert "PROCEDER" in s
        assert "sin discrepancias" in s.lower()
