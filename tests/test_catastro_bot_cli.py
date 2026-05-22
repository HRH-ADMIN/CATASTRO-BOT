"""Tests del CLI unificado catastro-bot."""
from __future__ import annotations
import subprocess
import sys
from pathlib import Path

import pytest


_CLI = Path(__file__).resolve().parents[1] / "tools" / "catastro_bot.py"


def _run(args: list[str]) -> subprocess.CompletedProcess:
    """Lanza el CLI con args y captura stdout/stderr."""
    return subprocess.run(
        [sys.executable, str(_CLI)] + args,
        capture_output=True, text=True, timeout=30,
        encoding="utf-8", errors="replace",
    )


class TestAyuda:
    def test_sin_args_imprime_ayuda(self):
        r = _run([])
        assert r.returncode == 0
        assert "CLI unificado" in r.stdout
        assert "Subcomandos:" in r.stdout

    def test_help_explicito(self):
        r = _run(["--help"])
        assert r.returncode == 0
        assert "Subcomandos:" in r.stdout

    def test_subcomando_invalido(self):
        r = _run(["subcomando-inventado"])
        assert r.returncode == 1
        assert "desconocido" in r.stdout

    def test_listado_incluye_todos(self):
        r = _run([])
        for sub in ("crear", "extraer", "listar", "dashboard", "backup",
                    "apt-crear", "apt-plano", "apt-guardar", "lote",
                    "health", "enviar-digest"):
            assert sub in r.stdout
