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
                    "health", "enviar-digest", "install-shortcut"):
            assert sub in r.stdout

    def test_install_shortcut_registrado(self):
        """U-01: el subcomando install-shortcut debe estar en el router.

        No ejecutamos el script PS acá (efecto colateral en el FS del
        usuario). Solo verificamos que el comando es reconocido y que
        existe el script PS que delegaría.
        """
        # Comando reconocido en el listado
        r = _run([])
        assert "install-shortcut" in r.stdout
        # Script PowerShell instalador existe
        ps_script = Path(__file__).resolve().parents[1] / "tools" / "install_desktop_shortcut.ps1"
        assert ps_script.exists(), f"{ps_script} debe existir"
        # Smoke check del script (sintaxis válida — sin caracteres Unicode que
        # PowerShell 5.1 malinterprete):
        contenido = ps_script.read_text(encoding="utf-8")
        # No debe haber em-dash (—) ni caracteres unicode comunes que rompen PS 5.1
        for ch in ("—", "–", "’", "“", "”"):
            assert ch not in contenido, f"caracter unicode {ch!r} romperá PowerShell 5.1"
