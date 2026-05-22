"""Tests del módulo healthcheck."""
from __future__ import annotations
import json
import threading
import time
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.utils.healthcheck import (
    check_chrome_cdp, check_db, check_db_reachable,
    check_anomalias_pendientes, verificar_salud,
    start_healthcheck_server, ensure_chrome_running,
)


class TestCheckChromeCdp:
    def test_chrome_vivo(self):
        class _FakeResp:
            status = 200
            def read(self): return b'{"Browser": "Chrome/148.0"}'
            def __enter__(self): return self
            def __exit__(self, *args): pass
        with patch("src.utils.healthcheck.urllib.request.urlopen",
                   return_value=_FakeResp()):
            r = check_chrome_cdp()
        assert r["alive"] is True
        assert "148.0" in r["browser"]

    def test_chrome_offline(self):
        with patch("src.utils.healthcheck.urllib.request.urlopen",
                   side_effect=ConnectionRefusedError("no chrome")):
            r = check_chrome_cdp()
        assert r["alive"] is False
        assert "no chrome" in r["error"]


class TestCheckDb:
    def test_db_existe(self, tmp_path):
        db = tmp_path / "test.db"
        db.write_bytes(b"x" * 1024)
        r = check_db(db)
        assert r["ok"] is True
        assert r["size_kb"] >= 1.0

    def test_db_no_existe(self, tmp_path):
        r = check_db(tmp_path / "no_existe.db")
        assert r["ok"] is False


class TestCheckDbReachable:
    def test_db_responde(self):
        db = MagicMock()
        db.listar_expedientes.return_value = [{"id": "x"}, {"id": "y"}]
        r = check_db_reachable(db)
        assert r["reachable"] is True
        assert r["expedientes_count"] == 2

    def test_db_none(self):
        r = check_db_reachable(None)
        assert r["reachable"] is False

    def test_db_query_falla(self):
        db = MagicMock()
        db.listar_expedientes.side_effect = Exception("DB locked")
        r = check_db_reachable(db)
        assert r["reachable"] is False
        assert "DB locked" in r["error"]


class TestVerificarSalud:
    def test_todo_ok(self, tmp_path):
        db_file = tmp_path / "db"
        db_file.write_bytes(b"x")
        db = MagicMock()
        db.listar_expedientes.return_value = []

        with patch("src.utils.healthcheck.check_chrome_cdp",
                   return_value={"alive": True, "browser": "Chrome/148"}), \
             patch("src.utils.healthcheck.check_db",
                   return_value={"ok": True, "size_kb": 10.0}):
            salud = verificar_salud(db)
        assert salud["status"] == "ok"
        assert salud["chrome_cdp"]["alive"] is True

    def test_chrome_caido_es_error(self, tmp_path):
        db = MagicMock()
        db.listar_expedientes.return_value = []
        with patch("src.utils.healthcheck.check_chrome_cdp",
                   return_value={"alive": False, "error": "x"}), \
             patch("src.utils.healthcheck.check_db",
                   return_value={"ok": True}):
            salud = verificar_salud(db)
        assert salud["status"] == "error"

    def test_anomalias_pendientes_es_warn(self):
        db = MagicMock()
        db.listar_expedientes.return_value = [
            {"id": "x", "metadata_json": json.dumps({
                "apt_anomalias": [{"ts": "2026-01-01", "contexto": "bC5",
                                    "descripcion": "x"}]
            }), "numero_expediente": "X", "cancelado": 0},
        ]
        with patch("src.utils.healthcheck.check_chrome_cdp",
                   return_value={"alive": True}), \
             patch("src.utils.healthcheck.check_db",
                   return_value={"ok": True}):
            salud = verificar_salud(db)
        assert salud["status"] == "warn"
        assert salud["anomalias"]["count"] >= 1


class TestHttpServer:
    def test_server_responde_health(self):
        """Levanta el server en puerto random, hace GET, lo cierra."""
        # Mocks para health
        with patch("src.utils.healthcheck.check_chrome_cdp",
                   return_value={"alive": True}), \
             patch("src.utils.healthcheck.check_db",
                   return_value={"ok": True}):
            # Puerto random alto para evitar colisión
            import random
            port = random.randint(15000, 19000)
            server = start_healthcheck_server(db=None, port=port)
            assert server is not None
            try:
                time.sleep(0.3)  # dar tiempo a iniciar
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/health", timeout=3,
                ) as r:
                    assert r.status == 200
                    body = json.loads(r.read().decode("utf-8"))
                    assert "status" in body
                    assert "chrome_cdp" in body
            finally:
                server.shutdown()
                server.server_close()

    def test_server_404_en_path_invalido(self):
        with patch("src.utils.healthcheck.check_chrome_cdp",
                   return_value={"alive": True}), \
             patch("src.utils.healthcheck.check_db",
                   return_value={"ok": True}):
            import random
            port = random.randint(15000, 19000)
            server = start_healthcheck_server(db=None, port=port)
            try:
                time.sleep(0.3)
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/inexistente",
                )
                with pytest.raises(urllib.error.HTTPError) as exc:
                    urllib.request.urlopen(req, timeout=3)
                assert exc.value.code == 404
            finally:
                server.shutdown()
                server.server_close()


class TestEnsureChromeRunning:
    def test_ya_corriendo_no_lanza(self):
        with patch("src.utils.healthcheck.check_chrome_cdp",
                   return_value={"alive": True}), \
             patch("src.utils.healthcheck.subprocess.Popen") as mock_popen:
            ok = ensure_chrome_running()
        assert ok is True
        mock_popen.assert_not_called()

    def test_caido_intenta_relanzar(self):
        # Primera llamada caída, después de Popen sigue caído (timeout)
        with patch("src.utils.healthcheck.check_chrome_cdp",
                   return_value={"alive": False, "error": "x"}), \
             patch("src.utils.healthcheck.subprocess.Popen") as mock_popen, \
             patch("src.utils.healthcheck.time.sleep"):
            ok = ensure_chrome_running(timeout_s=0.1)
        # Popen sí fue llamado
        mock_popen.assert_called_once()
