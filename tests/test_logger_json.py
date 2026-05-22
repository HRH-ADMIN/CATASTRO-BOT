"""Tests del JsonFormatter y configuración via env vars."""
from __future__ import annotations
import json
import logging

import pytest

from src.utils.logger import JsonFormatter


def _make_record(
    name: str = "catastro.test",
    level: int = logging.INFO,
    msg: str = "hola mundo",
    **extras,
) -> logging.LogRecord:
    """Crea un LogRecord para pasar al formatter."""
    rec = logging.LogRecord(
        name=name, level=level, pathname="test.py", lineno=42,
        msg=msg, args=(), exc_info=None, func="test_func",
    )
    for k, v in extras.items():
        setattr(rec, k, v)
    return rec


class TestJsonFormatter:
    def test_emite_json_valido(self):
        fmt = JsonFormatter()
        out = fmt.format(_make_record(msg="hola"))
        parsed = json.loads(out)
        assert parsed["msg"] == "hola"
        assert parsed["logger"] == "catastro.test"
        assert parsed["level"] == "INFO"
        assert parsed["func"] == "test_func"
        assert parsed["line"] == 42
        assert "ts" in parsed

    def test_ts_iso_utc(self):
        fmt = JsonFormatter()
        out = fmt.format(_make_record())
        parsed = json.loads(out)
        # Debe tener timezone (UTC)
        assert "+00:00" in parsed["ts"] or "Z" in parsed["ts"]

    def test_args_formateados_en_msg(self):
        """Mensajes con args (`%s`) deben formatearse antes de serializar."""
        rec = logging.LogRecord(
            name="catastro.x", level=logging.INFO, pathname="x.py",
            lineno=1, msg="Hola %s, son las %d", args=("Luis", 14),
            exc_info=None,
        )
        out = JsonFormatter().format(rec)
        parsed = json.loads(out)
        assert parsed["msg"] == "Hola Luis, son las 14"

    def test_extras_personalizados_en_extra(self):
        rec = _make_record(msg="evento")
        rec.expediente = "RDF-2026-004"
        rec.tramite = "1258126"
        out = JsonFormatter().format(rec)
        parsed = json.loads(out)
        assert parsed["extra"]["expediente"] == "RDF-2026-004"
        assert parsed["extra"]["tramite"] == "1258126"

    def test_excepcion_serializada(self):
        try:
            raise ValueError("oops")
        except ValueError:
            import sys
            rec = logging.LogRecord(
                name="catastro.x", level=logging.ERROR, pathname="x.py",
                lineno=1, msg="error capturado", args=(),
                exc_info=sys.exc_info(),
            )
        out = JsonFormatter().format(rec)
        parsed = json.loads(out)
        assert "exc" in parsed
        assert "ValueError" in parsed["exc"]

    def test_extras_no_serializables_se_convierten_a_string(self):
        rec = _make_record()
        # MagicMock no es JSON-serializable
        from unittest.mock import MagicMock
        rec.objeto_complejo = MagicMock()
        out = JsonFormatter().format(rec)
        parsed = json.loads(out)
        assert "extra" in parsed
        # Debe estar como string
        assert isinstance(parsed["extra"]["objeto_complejo"], str)

    def test_unicode_acentos(self):
        rec = _make_record(msg="señor PIÑEIRO")
        out = JsonFormatter().format(rec)
        parsed = json.loads(out)
        assert parsed["msg"] == "señor PIÑEIRO"


class TestConfiguracionPorEnvVar:
    def test_env_format_json_activa_jsonformatter(self, monkeypatch, tmp_path):
        from src.utils import logger as logger_mod
        monkeypatch.setenv("CATASTRO_LOG_FORMAT", "json")
        monkeypatch.setattr(logger_mod, "LOGS_DIR", tmp_path)
        logger_mod.reset_for_tests()
        log = logger_mod.get_logger("test_json")
        # Verificar que el formatter del file handler es JsonFormatter
        root = logging.getLogger("catastro")
        assert any(
            isinstance(h.formatter, JsonFormatter) for h in root.handlers
        )
        logger_mod.reset_for_tests()

    def test_env_format_default_es_text(self, monkeypatch, tmp_path):
        from src.utils import logger as logger_mod
        monkeypatch.delenv("CATASTRO_LOG_FORMAT", raising=False)
        monkeypatch.setattr(logger_mod, "LOGS_DIR", tmp_path)
        logger_mod.reset_for_tests()
        log = logger_mod.get_logger("test_text")
        root = logging.getLogger("catastro")
        # Formatter es Formatter regular (no JsonFormatter)
        for h in root.handlers:
            assert not isinstance(h.formatter, JsonFormatter)
        logger_mod.reset_for_tests()

    def test_env_log_level_debug(self, monkeypatch, tmp_path):
        from src.utils import logger as logger_mod
        monkeypatch.setenv("CATASTRO_LOG_LEVEL", "DEBUG")
        monkeypatch.setattr(logger_mod, "LOGS_DIR", tmp_path)
        logger_mod.reset_for_tests()
        log = logger_mod.get_logger("test_lvl")
        assert logging.getLogger("catastro").level == logging.DEBUG
        logger_mod.reset_for_tests()
