"""Tests para src/utils/muni_notificacion.py.

Verifica:
  - Composición de los 3 tipos de mensajes
  - Monto se formatea bien (incl. None)
  - Nombre del cliente se acorta a primer nombre, Title-Case
  - notificar_cliente_morosidad llama al agent y devuelve ok/error
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.utils.muni_notificacion import (
    componer_mensaje_morosidad,
    componer_mensaje_aprobado,
    componer_mensaje_rechazado,
    notificar_cliente_morosidad,
)


# ── Composición ─────────────────────────────────────────────────────────

class TestComponerMorosidad:
    def test_incluye_nombre_primer_titulado(self):
        msg = componer_mensaje_morosidad(
            nombre_cliente="VICTOR JIMENEZ ROJAS",
            numero_expediente="SEG-2026-003",
            monto_pendiente=125000.0,
        )
        assert "Victor" in msg
        # No incluir nombre completo
        assert "ROJAS" not in msg

    def test_incluye_monto_formateado(self):
        msg = componer_mensaje_morosidad(
            nombre_cliente="ALONSO",
            numero_expediente="SEG-2026-001",
            monto_pendiente=125000.50,
        )
        assert "₡125,000.50" in msg

    def test_sin_monto_dice_no_especificado(self):
        msg = componer_mensaje_morosidad(
            nombre_cliente="ANA",
            numero_expediente="SEG-2026-005",
            monto_pendiente=None,
        )
        assert "no especificado" in msg.lower()

    def test_incluye_expediente_y_email_muni(self):
        msg = componer_mensaje_morosidad(
            nombre_cliente="JUAN",
            numero_expediente="SEG-2026-007",
            monto_pendiente=50_000,
            email_muni="mgamboa@sanramon.go.cr",
        )
        assert "SEG-2026-007" in msg
        assert "mgamboa@sanramon.go.cr" in msg

    def test_tramite_apt_opcional(self):
        sin = componer_mensaje_morosidad(
            nombre_cliente="X", numero_expediente="E1", monto_pendiente=1000,
        )
        con = componer_mensaje_morosidad(
            nombre_cliente="X", numero_expediente="E1", monto_pendiente=1000,
            tramite_apt="1258460",
        )
        assert "1258460" not in sin
        assert "1258460" in con

    def test_nombre_vacio_no_crashea(self):
        msg = componer_mensaje_morosidad(
            nombre_cliente="", numero_expediente="E", monto_pendiente=0,
        )
        assert msg  # no error


class TestComponerAprobado:
    def test_aprobado_mensaje_positivo(self):
        msg = componer_mensaje_aprobado(
            nombre_cliente="MARIA",
            numero_expediente="SEG-2026-003",
        )
        assert "Maria" in msg
        assert "SEG-2026-003" in msg
        assert "aprobó" in msg.lower() or "aprobo" in msg.lower()
        # No menciona morosidad
        assert "moros" not in msg.lower()
        # Menciona el siguiente paso (CFIA)
        assert "CFIA" in msg


class TestComponerRechazado:
    def test_rechazado_sin_motivo(self):
        msg = componer_mensaje_rechazado(
            nombre_cliente="PEDRO",
            numero_expediente="SEG-2026-009",
        )
        assert "Pedro" in msg
        assert "SEG-2026-009" in msg

    def test_rechazado_con_motivo(self):
        msg = componer_mensaje_rechazado(
            nombre_cliente="PEDRO",
            numero_expediente="SEG-2026-009",
            motivo="Acceso no es ruta cantonal sino servidumbre",
        )
        assert "servidumbre" in msg.lower()
        assert "Motivo" in msg


# ── Envío ──────────────────────────────────────────────────────────────

class TestNotificarClienteMorosidad:
    def test_envio_ok_devuelve_id(self):
        wa = MagicMock()
        wa.enviar_mensaje.return_value = "ABC123"
        res = notificar_cliente_morosidad(
            whatsapp_agent=wa, telefono="+50688881234",
            nombre_cliente="JUAN", numero_expediente="SEG-2026-001",
            monto_pendiente=50_000,
        )
        assert res["ok"] is True
        assert res["id_message"] == "ABC123"
        wa.enviar_mensaje.assert_called_once()
        args, _ = wa.enviar_mensaje.call_args
        assert args[0] == "+50688881234"
        assert "Juan" in args[1]
        assert "₡50,000.00" in args[1]

    def test_envio_falla_devuelve_error(self):
        wa = MagicMock()
        wa.enviar_mensaje.side_effect = RuntimeError("green api caído")
        res = notificar_cliente_morosidad(
            whatsapp_agent=wa, telefono="+50688881234",
            nombre_cliente="JUAN", numero_expediente="SEG-2026-001",
            monto_pendiente=None,
        )
        assert res["ok"] is False
        assert "green api caído" in res["error"]
        # El mensaje compuesto se devuelve igual (útil para log)
        assert "mensaje" in res
        assert res["mensaje"]
