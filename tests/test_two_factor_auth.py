"""Tests del módulo two_factor_auth — 2FA para acciones sensibles."""
from __future__ import annotations
import time
from datetime import datetime, timedelta, timezone

import pytest

from src.utils.two_factor_auth import (
    TwoFactorAuth, AccionPendiente,
    ACCION_ENVIAR_CFIA, ACCION_RECHAZAR_EXP, ACCIONES_SENSIBLES,
)


class TestCrearCodigo:
    def test_codigo_es_6_digitos(self):
        auth = TwoFactorAuth()
        cod = auth.crear_codigo(accion=AccionPendiente(
            tipo=ACCION_ENVIAR_CFIA, actor="+506111",
            payload={"x": 1},
        ))
        assert len(cod) == 6
        assert cod.isdigit()

    def test_codigos_unicos(self):
        auth = TwoFactorAuth()
        codigos = set()
        for _ in range(20):
            cod = auth.crear_codigo(accion=AccionPendiente(
                tipo=ACCION_ENVIAR_CFIA, actor="+506111",
            ))
            codigos.add(cod)
        assert len(codigos) == 20

    def test_longitud_configurable(self):
        auth = TwoFactorAuth(longitud_codigo=4)
        cod = auth.crear_codigo(accion=AccionPendiente(
            tipo=ACCION_ENVIAR_CFIA, actor="+506111",
        ))
        assert len(cod) == 4


class TestValidarCodigo:
    def test_valida_correcto(self):
        auth = TwoFactorAuth()
        accion = AccionPendiente(
            tipo=ACCION_ENVIAR_CFIA, actor="+506111",
            payload={"expediente_id": "abc"},
        )
        cod = auth.crear_codigo(accion=accion)
        result = auth.validar_codigo(cod, actor="+506111")
        assert result is not None
        assert result.tipo == ACCION_ENVIAR_CFIA
        assert result.payload["expediente_id"] == "abc"

    def test_es_one_shot_segundo_uso_falla(self):
        auth = TwoFactorAuth()
        cod = auth.crear_codigo(accion=AccionPendiente(
            tipo=ACCION_ENVIAR_CFIA, actor="+506111",
        ))
        # Primer uso OK
        assert auth.validar_codigo(cod, actor="+506111") is not None
        # Segundo uso falla
        assert auth.validar_codigo(cod, actor="+506111") is None

    def test_actor_incorrecto_no_valida(self):
        auth = TwoFactorAuth()
        cod = auth.crear_codigo(accion=AccionPendiente(
            tipo=ACCION_ENVIAR_CFIA, actor="+506111",
        ))
        # Otro actor
        assert auth.validar_codigo(cod, actor="+506222") is None
        # El original aún funciona (no fue invalidado todavía)
        assert auth.validar_codigo(cod, actor="+506111") is not None

    def test_codigo_inexistente_no_valida(self):
        auth = TwoFactorAuth()
        assert auth.validar_codigo("999999", actor="+506111") is None

    def test_codigo_vacio(self):
        auth = TwoFactorAuth()
        assert auth.validar_codigo("", actor="+506111") is None
        assert auth.validar_codigo(None, actor="+506111") is None


class TestExpiracion:
    def test_ttl_corto_expira(self):
        """TTL=1s, esperamos 1.1s y debe expirar."""
        auth = TwoFactorAuth(ttl_segundos=1)
        cod = auth.crear_codigo(accion=AccionPendiente(
            tipo=ACCION_ENVIAR_CFIA, actor="+506111",
        ))
        time.sleep(1.1)
        assert auth.validar_codigo(cod, actor="+506111") is None

    def test_dentro_de_ttl_funciona(self):
        auth = TwoFactorAuth(ttl_segundos=60)
        cod = auth.crear_codigo(accion=AccionPendiente(
            tipo=ACCION_ENVIAR_CFIA, actor="+506111",
        ))
        # Inmediatamente debe funcionar
        assert auth.validar_codigo(cod, actor="+506111") is not None


class TestMaxIntentos:
    def test_max_intentos_invalida_codigo(self):
        auth = TwoFactorAuth(max_intentos=2)
        cod = auth.crear_codigo(accion=AccionPendiente(
            tipo=ACCION_ENVIAR_CFIA, actor="+506111",
        ))
        # 2 intentos fallidos con actor incorrecto → invalida
        assert auth.validar_codigo(cod, actor="+506999") is None  # intento 1
        assert auth.validar_codigo(cod, actor="+506999") is None  # intento 2 → borra
        # Ahora hasta el actor correcto falla
        assert auth.validar_codigo(cod, actor="+506111") is None


class TestListarPendientes:
    def test_filtra_por_actor(self):
        auth = TwoFactorAuth()
        auth.crear_codigo(accion=AccionPendiente(
            tipo=ACCION_ENVIAR_CFIA, actor="+506111",
        ))
        auth.crear_codigo(accion=AccionPendiente(
            tipo=ACCION_RECHAZAR_EXP, actor="+506111",
        ))
        auth.crear_codigo(accion=AccionPendiente(
            tipo=ACCION_ENVIAR_CFIA, actor="+506222",
        ))
        pendientes_111 = auth.listar_pendientes_de("+506111")
        assert len(pendientes_111) == 2
        pendientes_222 = auth.listar_pendientes_de("+506222")
        assert len(pendientes_222) == 1


class TestCancelarPendiente:
    def test_cancela_codigo(self):
        auth = TwoFactorAuth()
        cod = auth.crear_codigo(accion=AccionPendiente(
            tipo=ACCION_ENVIAR_CFIA, actor="+506111",
        ))
        assert auth.cancelar_pendiente(cod) is True
        assert auth.validar_codigo(cod, actor="+506111") is None

    def test_cancelar_inexistente_devuelve_false(self):
        auth = TwoFactorAuth()
        assert auth.cancelar_pendiente("999999") is False


class TestLen:
    def test_cuenta_activos(self):
        auth = TwoFactorAuth()
        assert len(auth) == 0
        auth.crear_codigo(accion=AccionPendiente(
            tipo=ACCION_ENVIAR_CFIA, actor="+506111",
        ))
        auth.crear_codigo(accion=AccionPendiente(
            tipo=ACCION_RECHAZAR_EXP, actor="+506222",
        ))
        assert len(auth) == 2


class TestPurga:
    def test_purga_automatica_al_consultar(self):
        """Códigos expirados se borran al hacer cualquier operación."""
        auth = TwoFactorAuth(ttl_segundos=1)
        cod = auth.crear_codigo(accion=AccionPendiente(
            tipo=ACCION_ENVIAR_CFIA, actor="+506111",
        ))
        assert len(auth) == 1
        time.sleep(1.1)
        # Al consultar len, purga internamente
        assert len(auth) == 0
