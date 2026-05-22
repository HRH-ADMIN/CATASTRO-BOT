"""Tests del email digest."""
from __future__ import annotations
from unittest.mock import MagicMock, patch

import pytest

from src.utils.email_digest import (
    _resumen_a_texto, _resumen_a_html,
    enviar_email_smtp, enviar_digest_semanal,
)


_RESUMEN_FAKE = {
    "total_expedientes": 8,
    "por_estado":      {"recibido": 7, "carta_agua_requerida": 1},
    "por_tipo":        {"segregacion": 4, "rectificacion": 2},
    "por_topografo":   {"LUIS ROJAS": 4},
    "por_estado_apt":  {"enviado_cfia": 4, "(sin contrato APT)": 3},
    "tiempo_promedio_creacion_a_envio_dias": 0.1,
    "ratio_exoneracion_pct": 20.0,
    "discrepancias_frecuentes": [("registro_dato_incompleto", 2)],
    "anomalias_recientes": [
        {"ts": "2026-05-11T10:00:00", "contexto": "bC5",
         "descripcion": "validación falló", "display": "ROVUELT (RDF-2026-003)"},
    ],
    "alertas_proactivas": ["⚠️ 3 anomalías en 'bC5'"],
}


class TestRenderText:
    def test_incluye_titulo(self):
        s = _resumen_a_texto(_RESUMEN_FAKE)
        assert "Resumen semanal" in s
        assert "Total expedientes activos: 8" in s

    def test_incluye_estados(self):
        s = _resumen_a_texto(_RESUMEN_FAKE)
        assert "recibido" in s
        assert "carta_agua_requerida" in s

    def test_incluye_tiempo(self):
        s = _resumen_a_texto(_RESUMEN_FAKE)
        assert "0.1 días" in s

    def test_incluye_alertas_proactivas(self):
        s = _resumen_a_texto(_RESUMEN_FAKE)
        assert "ALERTAS PROACTIVAS" in s
        assert "bC5" in s

    def test_resumen_vacio_no_explota(self):
        s = _resumen_a_texto({"total_expedientes": 0})
        assert "Resumen semanal" in s


class TestRenderHtml:
    def test_es_html_valido(self):
        h = _resumen_a_html(_RESUMEN_FAKE)
        assert h.startswith("<html>")
        assert "</html>" in h
        assert "<pre" in h

    def test_escapa_signos_menos_mayor(self):
        """No debe romper HTML si hay caracteres especiales."""
        resumen = {"total_expedientes": 0,
                   "anomalias_recientes": [
                       {"descripcion": "Error: <script>x</script>",
                        "contexto": "x"}
                   ]}
        h = _resumen_a_html(resumen)
        assert "&lt;script&gt;" in h
        assert "<script>" not in h


class TestEnviarEmailSmtp:
    def test_envia_via_smtp(self):
        with patch("src.utils.email_digest.smtplib.SMTP") as mock_smtp:
            instance = mock_smtp.return_value.__enter__.return_value
            ok = enviar_email_smtp(
                from_addr="bot@test.com", password="x",
                to="admin@test.com", subject="hola",
                body_text="hola mundo",
            )
        assert ok is True
        instance.login.assert_called_once_with("bot@test.com", "x")
        instance.sendmail.assert_called_once()

    def test_falla_silencioso(self):
        with patch("src.utils.email_digest.smtplib.SMTP",
                   side_effect=ConnectionRefusedError):
            ok = enviar_email_smtp(
                from_addr="x", password="x", to="x@y.cr",
                subject="x", body_text="x",
            )
        assert ok is False

    def test_html_se_adjunta(self):
        with patch("src.utils.email_digest.smtplib.SMTP") as mock_smtp:
            instance = mock_smtp.return_value.__enter__.return_value
            enviar_email_smtp(
                from_addr="x", password="x", to="x@y.cr",
                subject="x", body_text="texto", body_html="<b>html</b>",
            )
        # sendmail llamó con un msg.as_string() que tiene 2 parts
        call = instance.sendmail.call_args
        body = call[0][2]
        # MIME marca content-type por separado para text y html
        assert "text/plain" in body
        assert "text/html" in body
        assert "multipart/alternative" in body


class TestEnviarDigestSemanal:
    def _make_db_con_admins(self):
        db = MagicMock()
        db.listar_expedientes.return_value = []
        db.listar_usuarios.return_value = [
            {"nombre": "Admin Uno", "telefono": "1", "correo_apt": "a@y.cr"},
            {"nombre": "Admin Dos", "telefono": "2", "correo_apt": "b@y.cr"},
        ]
        return db

    def _make_creds(self):
        creds = MagicMock()
        creds.get_muni_san_ramon.return_value = ("bot@test.com", "app-pass")
        return creds

    def test_envia_a_todos_los_admins(self):
        db = self._make_db_con_admins()
        creds = self._make_creds()
        with patch("src.utils.email_digest.smtplib.SMTP") as mock_smtp:
            mock_smtp.return_value.__enter__.return_value
            res = enviar_digest_semanal(db, creds)
        assert res["enviados"] == 2
        assert "a@y.cr" in res["destinatarios"]
        assert "b@y.cr" in res["destinatarios"]

    def test_admin_sin_email_no_recibe(self):
        db = MagicMock()
        db.listar_expedientes.return_value = []
        db.listar_usuarios.return_value = [
            {"nombre": "Admin Sin Mail", "telefono": "1"},
        ]
        creds = self._make_creds()
        with patch("src.utils.email_digest.smtplib.SMTP"):
            res = enviar_digest_semanal(db, creds)
        assert res["enviados"] == 0
        assert "ningún admin tiene correo" in res["errores"][0]

    def test_creds_no_configuradas(self):
        from src.core.exceptions import CredentialNotFoundError
        db = self._make_db_con_admins()
        creds = MagicMock()
        creds.get_muni_san_ramon.side_effect = CredentialNotFoundError("x")
        res = enviar_digest_semanal(db, creds)
        assert res["enviados"] == 0
        assert any("credenciales" in e.lower() for e in res["errores"])
