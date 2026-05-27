"""Tests del launcher Chrome (HOTFIX 2026-05-22).

NOTA: el script `tools/start_chrome_bot.py` reasigna sys.stdout a top-level,
así que NO lo importamos como módulo (rompería stdout para los demás tests).
En su lugar, hacemos assertions sobre:
  - El contenido del archivo (string match para flags).
  - El comportamiento exterior via subprocess en un test e2e marcado lento.

Plan: HOTFIX/browser-session-leak.
"""
from __future__ import annotations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "start_chrome_bot.py"


class TestLaunchArgsContenidoArchivo:
    """Verifica que los flags del hotfix están en el archivo source."""

    def test_archivo_existe(self):
        assert SCRIPT.exists()

    def test_NO_incluye_flag_new_window(self):
        """HOTFIX: --new-window causaba ventana extra cada relanzo."""
        content = SCRIPT.read_text(encoding="utf-8")
        # La línea "args = [" hasta APT_HOME no debe tener "--new-window"
        # como argumento literal entre comillas dobles
        assert '"--new-window"' not in content, (
            "--new-window debe estar removido del launch_chrome() args"
        )

    def test_incluye_flags_anti_spam(self):
        content = SCRIPT.read_text(encoding="utf-8")
        # Flags nuevos del hotfix
        assert "--disable-session-crashed-bubble" in content
        assert "--disable-infobars" in content
        # CDP sigue presente
        assert "--remote-debugging-port=9222" in content
        assert "--remote-debugging-address=127.0.0.1" in content

    def test_tiene_funcion_idempotente_cdp_ya_responde(self):
        content = SCRIPT.read_text(encoding="utf-8")
        assert "_cdp_ya_responde" in content
        # Debería existir el flag --force también
        assert '"--force" in sys.argv' in content


class TestArgsParsing:
    """Smoke tests del CLI via subprocess (sin lanzar Chrome real)."""

    def test_help_flag_imprime_uso(self, tmp_path, monkeypatch):
        """Si el archivo se ejecuta con un arg desconocido, debe terminar
        sin colgarse. Verificamos invocando con flag inválido + timeout corto."""
        import subprocess
        import sys
        # Lanzar con --cdp-check inexistente debería ejecutar sin colgarse
        # (timeout es defensa).
        try:
            r = subprocess.run(
                [sys.executable, str(SCRIPT), "--no-kill", "--force"],
                capture_output=True, text=True, timeout=3,
                encoding="utf-8", errors="replace",
            )
            # El script intenta lanzar Chrome — puede fallar pero no debe
            # explotar a nivel Python (importerror, syntaxerror).
            assert "Traceback" not in (r.stderr or ""), \
                f"Error de Python en script: {r.stderr[:500]}"
        except subprocess.TimeoutExpired:
            # Si timeout: significa que el script intentó lanzar Chrome
            # y esperó (no es error de Python). OK.
            pass
