"""Tests de notificación de escritorio + handler de anomalías APT."""
from __future__ import annotations
import json
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("win32cred", reason="solo Windows")

from src.utils.desktop_notify import notificar_escritorio  # noqa: E402
from src.agents.apt_agent import APTAnomalyError  # noqa: E402
from src.agents.apt_anomaly_handler import handle_anomaly, _formatear_mensaje_whatsapp  # noqa: E402


class TestDesktopNotify:
    def test_urgencia_normal_usa_winotify_primero(self):
        with patch("src.utils.desktop_notify._notificar_via_winotify", return_value=True):
            metodo = notificar_escritorio(titulo="t", mensaje="m", urgencia="normal")
        assert metodo == "winotify"

    def test_urgencia_normal_fallback_a_balloon(self):
        with patch("src.utils.desktop_notify._notificar_via_winotify", return_value=False), \
             patch("src.utils.desktop_notify._notificar_via_powershell_balloon", return_value=True):
            metodo = notificar_escritorio(titulo="t", mensaje="m", urgencia="normal")
        assert metodo == "ps-balloon"

    def test_urgencia_normal_no_usa_messagebox(self):
        """Las notificaciones normales NO deben mostrar MessageBox bloqueante."""
        with patch("src.utils.desktop_notify._notificar_via_winotify", return_value=False), \
             patch("src.utils.desktop_notify._notificar_via_powershell_balloon", return_value=False), \
             patch("src.utils.desktop_notify._notificar_via_messagebox", return_value=True) as mb:
            metodo = notificar_escritorio(titulo="t", mensaje="m", urgencia="normal")
        assert metodo == ""
        assert not mb.called  # MessageBox no se invoca para urgencia normal

    def test_urgencia_alta_usa_messagebox_PRIMERO(self):
        """Anomalías deben mostrar MessageBox centrada de inmediato — NO toast."""
        with patch("src.utils.desktop_notify._notificar_via_messagebox", return_value=True) as mb, \
             patch("src.utils.desktop_notify._notificar_via_winotify", return_value=True) as toast:
            metodo = notificar_escritorio(titulo="t", mensaje="m", urgencia="alta")
        assert metodo == "messagebox"
        assert mb.called
        # Toast NO se usa cuando MessageBox tuvo éxito
        assert not toast.called

    def test_urgencia_alta_fallback_a_ps_messagebox(self):
        with patch("src.utils.desktop_notify._notificar_via_messagebox", return_value=False), \
             patch("src.utils.desktop_notify._notificar_via_powershell_messagebox", return_value=True):
            metodo = notificar_escritorio(titulo="t", mensaje="m", urgencia="alta")
        assert metodo == "ps-messagebox"

    def test_urgencia_alta_ultimo_fallback_a_toast(self):
        with patch("src.utils.desktop_notify._notificar_via_messagebox", return_value=False), \
             patch("src.utils.desktop_notify._notificar_via_powershell_messagebox", return_value=False), \
             patch("src.utils.desktop_notify._notificar_via_winotify", return_value=True):
            metodo = notificar_escritorio(titulo="t", mensaje="m", urgencia="alta")
        assert metodo == "winotify-fallback"

    def test_titulo_muy_largo_se_recorta(self):
        # No debe crashear con un título de 500 chars
        with patch("src.utils.desktop_notify._notificar_via_winotify", return_value=True) as m:
            notificar_escritorio(titulo="x" * 500, mensaje="y" * 1000, urgencia="normal")
        assert m.called
        args, kwargs = m.call_args
        assert len(args[0]) <= 120


class TestAPTAnomalyError:
    def test_excepcion_lleva_contexto_y_detalle(self):
        e = APTAnomalyError("bC5 falló", contexto="bC5 PROYECTO", detalle="canton vacío")
        assert e.descripcion == "bC5 falló"
        assert e.contexto == "bC5 PROYECTO"
        assert e.detalle == "canton vacío"
        assert "bC5 PROYECTO" in str(e)
        assert "bC5 falló" in str(e)

    def test_sin_contexto_str_solo_descripcion(self):
        e = APTAnomalyError("algo raro")
        assert "algo raro" in str(e)


class TestHandleAnomaly:
    def _mock_db(self):
        db = MagicMock()
        db.obtener_expediente.return_value = {
            "id": "abc",
            "numero_expediente": "RDF-2026-003",
            "metadata_json": json.dumps({"nombre_proyecto": "ROVUELT"}),
            "nombre_topografo": "X",
            "cedula_topografo": "1-2",
        }
        return db

    def test_notifica_escritorio_y_persiste(self):
        db = self._mock_db()
        with patch("src.agents.apt_anomaly_handler.notificar_escritorio",
                   return_value="winotify") as mock_notif:
            res = handle_anomaly(
                db=db, expediente_id="abc", numero_expediente="RDF-2026-003",
                descripcion="bC5 validación falló", contexto="bC5 PROYECTO",
            )
        assert res["notif_escritorio"] == "winotify"
        assert res["persistida"] is True
        mock_notif.assert_called_once()
        # Llamada a actualizar_metadata con apt_anomalias
        args = db.actualizar_metadata.call_args[0]
        assert "apt_anomalias" in args[1]

    def test_acumula_anomalias_previas(self):
        db = MagicMock()
        db.obtener_expediente.return_value = {
            "id": "abc", "numero_expediente": "X",
            "metadata_json": json.dumps({
                "apt_anomalias": [{"ts": "2026-01-01", "descripcion": "previa"}]
            }),
        }
        with patch("src.agents.apt_anomaly_handler.notificar_escritorio", return_value=""):
            handle_anomaly(
                db=db, expediente_id="abc", numero_expediente="X",
                descripcion="nueva", contexto="ctx",
            )
        guardadas = db.actualizar_metadata.call_args[0][1]["apt_anomalias"]
        assert len(guardadas) == 2

    def test_notifica_topografo_y_admins(self):
        db = self._mock_db()
        send_fn = MagicMock()
        with patch("src.agents.apt_anomaly_handler.notificar_escritorio", return_value=""):
            res = handle_anomaly(
                db=db, expediente_id="abc", numero_expediente="X",
                descripcion="X", contexto="Y",
                whatsapp_send_fn=send_fn,
                topografo_phone="+506111",
                admins_phones=["+506222", "+506333"],
            )
        assert res["notif_topografo"] is True
        assert res["notif_admin"] == 2
        assert send_fn.call_count == 3

    def test_mensaje_whatsapp_contiene_proyecto_y_descripcion(self):
        msg = _formatear_mensaje_whatsapp("ROVUELT (RDF-2026-003)", "bC5 falló", "bC5 PROYECTO")
        assert "ANOMALÍA APT" in msg
        assert "ROVUELT (RDF-2026-003)" in msg
        assert "bC5 PROYECTO" in msg
        assert "bC5 falló" in msg
        assert "DETUVO el ciclo" in msg

    def test_sin_expediente_id_no_persiste_pero_notifica(self):
        with patch("src.agents.apt_anomaly_handler.notificar_escritorio", return_value="winotify"):
            res = handle_anomaly(
                db=None, expediente_id=None, numero_expediente="X",
                descripcion="error", contexto="ctx",
            )
        assert res["persistida"] is False
        assert res["notif_escritorio"] == "winotify"


class TestDetectarAnomaliaModal:
    """El detector de modales debe distinguir entre OK, confirmación esperada,
    y errores que disparan anomalía.
    """

    def _mock_page(self, modal_data):
        page = MagicMock()
        page.evaluate.return_value = modal_data
        return page

    def _make_agent(self, tmp_path):
        from tests.conftest import FakeCredentialManager, TestDatabase
        from src.agents.apt_agent import APTAgent
        creds = FakeCredentialManager()
        db = TestDatabase(path=tmp_path / "t.db", credentials=creds)
        db.initialize_schema()
        return APTAgent(db, creds)

    def test_modal_exito_no_es_anomalia(self, tmp_path):
        agent = self._make_agent(tmp_path)
        page = self._mock_page({"title": "Éxito", "html": "Guardado con éxito", "icon": ""})
        assert agent._detectar_anomalia_modal(page) is None

    def test_modal_confirmacion_no_es_anomalia(self, tmp_path):
        agent = self._make_agent(tmp_path)
        page = self._mock_page({"title": "¿Desea enviar?", "html": "", "icon": ""})
        assert agent._detectar_anomalia_modal(page) is None

    def test_modal_error_es_anomalia(self, tmp_path):
        agent = self._make_agent(tmp_path)
        page = self._mock_page({
            "title": "Error", "html": "No se pudo guardar",
            "icon": "swal2-icon swal2-error"
        })
        result = agent._detectar_anomalia_modal(page)
        assert result is not None
        assert "Error" in result["title"]

    def test_modal_warning_es_anomalia(self, tmp_path):
        agent = self._make_agent(tmp_path)
        page = self._mock_page({
            "title": "Atención", "html": "Debe indicar los siguientes datos",
            "icon": "swal2-icon swal2-warning"
        })
        assert agent._detectar_anomalia_modal(page) is not None

    def test_sin_modal_visible(self, tmp_path):
        agent = self._make_agent(tmp_path)
        page = self._mock_page(None)
        assert agent._detectar_anomalia_modal(page) is None
