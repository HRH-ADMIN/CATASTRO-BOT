"""Regression test del hotfix dashboard-root-undefined (2026-05-22).

`_lanzar_proceso` en src/utils/dashboard_web.py usaba `ROOT` sin definirla
en el módulo. El error `NameError: name 'ROOT' is not defined` solo se
manifestaba al hacer click en los botones 'Encender' del panel /config,
lo que dejaba esos botones inútiles.

Este test garantiza que:
  - ROOT está definida y apunta a la raíz del proyecto.
  - DB_PATH es absoluta (no relativa, que rompía si cwd != raíz).
  - _lanzar_proceso construye correctamente el subprocess.Popen sin
    NameError.
"""
from __future__ import annotations
from unittest.mock import MagicMock, patch

import pytest

from src.utils.dashboard_web import ROOT, DB_PATH, _lanzar_proceso


class TestRootDefinido:
    def test_root_apunta_a_raiz_del_proyecto(self):
        # La raíz debe contener src/, tests/, tools/, requirements.txt
        assert (ROOT / "src").is_dir(), f"{ROOT} debe contener src/"
        assert (ROOT / "tests").is_dir(), f"{ROOT} debe contener tests/"
        assert (ROOT / "tools").is_dir(), f"{ROOT} debe contener tools/"
        assert (ROOT / "requirements.txt").is_file()

    def test_db_path_es_absoluto(self):
        # DB_PATH antes era 'data/catastro.db' relativo, que fallaba si
        # el proceso corría con cwd distinto. Ahora debe ser absoluto.
        assert DB_PATH.is_absolute(), (
            f"DB_PATH debe ser absoluto, no relativo: {DB_PATH}"
        )
        assert str(DB_PATH).endswith("data\\catastro.db") or \
               str(DB_PATH).endswith("data/catastro.db")


class TestLanzarProcesoNoExploraConNameError:
    def test_lanzar_proceso_no_explota_con_module_arg(self):
        """Regression: _lanzar_proceso('-m foo.bar') debía explotar
        con NameError antes del fix. Ahora debe ejecutar sin error.

        Mockeamos subprocess.Popen para no lanzar procesos reales.
        Tambien mockeamos _proceso_ya_corriendo y time.sleep para que
        el flujo llegue rapido al Popen real (sin esperar a que WMI
        detecte el proceso falso).
        """
        fake_proc = MagicMock()
        fake_proc.pid = 99999
        with patch("src.utils.dashboard_web._proceso_ya_corriendo",
                   return_value=None), \
             patch("time.sleep", return_value=None), \
             patch("subprocess.Popen",
                   return_value=fake_proc) as mock_popen:
            result = _lanzar_proceso("-m src.utils.healthcheck", "--interval", "60")

        assert result["ok"] is True, f"Resultado inesperado: {result}"
        assert result["pid"] == 99999
        # Verificar que cwd se pasó correctamente — antes hubiera levantado
        # NameError en este punto.
        call_kwargs = mock_popen.call_args.kwargs
        assert "cwd" in call_kwargs
        assert str(ROOT) == call_kwargs["cwd"]

    def test_lanzar_proceso_no_explota_con_script_path(self):
        fake_proc = MagicMock()
        fake_proc.pid = 88888
        with patch("src.utils.dashboard_web._proceso_ya_corriendo",
                   return_value=None), \
             patch("time.sleep", return_value=None), \
             patch("subprocess.Popen",
                   return_value=fake_proc):
            result = _lanzar_proceso("tools/start_chrome_bot.py")
        assert result["ok"] is True
        assert result["pid"] == 88888

    def test_lanzar_proceso_idempotente_si_ya_corre(self):
        """Si _proceso_ya_corriendo devuelve un PID, NO se lanza otra
        instancia. Devuelve {pid, ya_corria=True}."""
        with patch("src.utils.dashboard_web._proceso_ya_corriendo",
                   return_value=42424), \
             patch("subprocess.Popen") as mock_popen:
            result = _lanzar_proceso("-m src.utils.healthcheck")
        assert result["ok"] is True
        assert result["pid"] == 42424
        assert result.get("ya_corria") is True
        mock_popen.assert_not_called()  # NO debe haber Popen

    def test_lanzar_proceso_captura_excepciones_internas(self):
        """Si Popen sí falla (ej. ejecutable inexistente), _lanzar_proceso
        debe devolver ok=False con el error truncado, NO propagar."""
        with patch("subprocess.Popen",
                   side_effect=FileNotFoundError("python not found")):
            result = _lanzar_proceso("-m no.existe")
        assert result["ok"] is False
        assert "python not found" in result["error"]


class TestImportabilidad:
    """Garantiza que el módulo se puede importar sin side-effects
    inesperados (ROOT no debe requerir BD ni nada externo)."""

    def test_modulo_importa_sin_errores(self):
        # Si llegamos a este punto, el módulo importó OK.
        from src.utils import dashboard_web  # noqa: F401
        assert hasattr(dashboard_web, "ROOT")
        assert hasattr(dashboard_web, "_lanzar_proceso")
