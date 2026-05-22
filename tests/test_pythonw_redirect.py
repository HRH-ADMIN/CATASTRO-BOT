"""Test del redirect de stdout/stderr cuando se corre bajo pythonw (U-02 C).

Bajo pythonw.exe, sys.stdout/stderr son None. `_redirect_stdio_if_pythonw()`
debe interceptar y redirigir a logs/scheduler.{stdout,stderr}.log.

Plan: PLAN_MEJORAS Sprint 1 / U-02 paso C.
"""
from __future__ import annotations
import sys
from unittest.mock import patch

import pytest


def test_redirect_no_hace_nada_bajo_python_con_consola(tmp_path, monkeypatch):
    """Bajo python.exe normal, sys.stdout y stderr existen → no redirige."""
    from src.main import _redirect_stdio_if_pythonw

    original_stdout = sys.stdout
    original_stderr = sys.stderr
    _redirect_stdio_if_pythonw()
    assert sys.stdout is original_stdout
    assert sys.stderr is original_stderr


def test_redirect_abre_files_si_stdout_es_None(tmp_path, monkeypatch):
    """Simula entorno pythonw: stdout=None → debe abrir scheduler.stdout.log."""
    logs_dir = tmp_path / "logs"
    monkeypatch.setattr("config.settings.LOGS_DIR", logs_dir)

    orig_stdout = sys.stdout
    orig_stderr = sys.stderr
    try:
        sys.stdout = None  # type: ignore[assignment]
        sys.stderr = None  # type: ignore[assignment]
        from src.main import _redirect_stdio_if_pythonw
        _redirect_stdio_if_pythonw()

        # Los archivos deben existir y los handles deben estar abiertos
        assert (logs_dir / "scheduler.stdout.log").exists()
        assert (logs_dir / "scheduler.stderr.log").exists()
        assert sys.stdout is not None
        assert sys.stderr is not None

        # El stderr debe tener el marker "pythonw startup"
        sys.stderr.flush()
        content = (logs_dir / "scheduler.stderr.log").read_text(encoding="utf-8")
        assert "pythonw startup" in content
    finally:
        # Cerrar y restaurar
        try:
            if sys.stdout is not None and hasattr(sys.stdout, "close"):
                sys.stdout.close()
            if sys.stderr is not None and hasattr(sys.stderr, "close"):
                sys.stderr.close()
        except Exception:
            pass
        sys.stdout = orig_stdout
        sys.stderr = orig_stderr


def test_vbs_existe_y_es_ascii(tmp_path):
    """El .vbs debe ser ASCII puro (evitar problemas de encoding del CScript)."""
    from pathlib import Path
    vbs = Path(__file__).resolve().parents[1] / "tools" / "catastro_bot_autostart.vbs"
    assert vbs.exists(), f"{vbs} debe existir"
    contenido = vbs.read_text(encoding="utf-8")
    # Defensa contra unicode roto en CScript. Usamos codepoints en hex
    # para evitar que las comillas tipográficas se cuelen en este test mismo.
    forbidden_codepoints = [
        0x2014,  # em-dash
        0x2013,  # en-dash
        0x2018,  # left single smart quote
        0x2019,  # right single smart quote
        0x201C,  # left double smart quote
        0x201D,  # right double smart quote
        0x2713,  # check mark
    ]
    for cp in forbidden_codepoints:
        ch = chr(cp)
        assert ch not in contenido, f"caracter unicode U+{cp:04X} en .vbs"

    # Verificaciones de contenido mínimo
    assert "pythonw.exe" in contenido
    assert "src.main" in contenido
    assert "start_chrome_bot.py" in contenido
    assert "healthcheck" in contenido
    # NO debe arrancar el dashboard_web legacy (causaba el bug de doble bind)
    assert "src.utils.dashboard_web" not in contenido
