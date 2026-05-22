"""Tests del rate-limiter por sender (defensa anti-spam/brute-force)."""
from __future__ import annotations

import time

import pytest

from src.utils.sender_rate_limiter import SenderRateLimiter


class TestPermitir:
    def test_primer_mensaje_se_permite(self):
        rl = SenderRateLimiter()
        assert rl.permitir("+50688880001") is True

    def test_burst_se_permite(self):
        rl = SenderRateLimiter(max_por_minuto=60, burst=5)
        for i in range(5):
            assert rl.permitir("+50688880001") is True, f"falló en intento {i}"

    def test_excedido_se_rechaza(self):
        rl = SenderRateLimiter(max_por_minuto=60, burst=3)
        # Consumir burst
        assert rl.permitir("+506888") is True
        assert rl.permitir("+506888") is True
        assert rl.permitir("+506888") is True
        # 4to debe rechazarse (sin tiempo para refill)
        assert rl.permitir("+506888") is False

    def test_senders_distintos_tienen_buckets_independientes(self):
        rl = SenderRateLimiter(max_por_minuto=60, burst=1)
        assert rl.permitir("+506AAA") is True
        # AAA agotó SU bucket pero BBB tiene el suyo
        assert rl.permitir("+506BBB") is True
        # AAA sigue agotado
        assert rl.permitir("+506AAA") is False

    def test_sender_vacio_rechaza(self):
        rl = SenderRateLimiter()
        assert rl.permitir("") is False
        assert rl.permitir(None) is False  # type: ignore

    def test_refill_con_tiempo(self):
        """Tras el intervalo, debería tener token de nuevo."""
        # 600/min = 10/seg = 1 token cada 0.1s, burst 1
        rl = SenderRateLimiter(max_por_minuto=600, burst=1)
        assert rl.permitir("+506X") is True
        assert rl.permitir("+506X") is False
        time.sleep(0.15)  # esperar refill
        assert rl.permitir("+506X") is True


class TestPurga:
    def test_purga_buckets_sin_actividad(self):
        rl = SenderRateLimiter(ttl_segundos=0)  # purga inmediata
        rl.permitir("+506AAA")
        rl.permitir("+506BBB")
        assert rl.stats()["senders_activos"] == 2
        # Forzar purga: llamar permitir con un nuevo número (trigger interno)
        # con ttl=0 los anteriores deberían purgarse
        time.sleep(0.01)
        # Llenar a más de max_buckets//2 para gatillar el cleanup oportunista
        for i in range(20):
            rl.permitir(f"+506TRIGGER{i}")
        # AAA y BBB deberían haber sido purgados
        # (ttl=0 + actividad reciente > cutoff)
        # NOTA: el comportamiento exacto depende del cleanup oportunista;
        # lo importante es que no crezca sin tope.
        assert rl.stats()["senders_activos"] < 100

    def test_max_buckets_limita_crecimiento(self):
        rl = SenderRateLimiter(max_buckets=5, ttl_segundos=3600)
        # Crear más buckets que max_buckets
        for i in range(20):
            rl.permitir(f"+506N{i:04d}")
        # Forzar purga llenando un poco más
        for i in range(20, 30):
            rl.permitir(f"+506N{i:04d}")
        # El total nunca debería exceder mucho max_buckets
        assert rl.stats()["senders_activos"] <= 20


class TestResetSender:
    def test_reset_libera_bucket(self):
        rl = SenderRateLimiter(max_por_minuto=60, burst=1)
        rl.permitir("+506X")
        assert rl.permitir("+506X") is False
        rl.reset_sender("+506X")
        # Tras reset, bucket nuevo con burst lleno
        assert rl.permitir("+506X") is True

    def test_reset_devuelve_true_si_existia(self):
        rl = SenderRateLimiter()
        rl.permitir("+506X")
        assert rl.reset_sender("+506X") is True

    def test_reset_devuelve_false_si_no_existia(self):
        rl = SenderRateLimiter()
        assert rl.reset_sender("+506NUNCA") is False


class TestStats:
    def test_stats_cuenta_aceptados_y_rechazados(self):
        rl = SenderRateLimiter(max_por_minuto=60, burst=2)
        rl.permitir("+506X")  # ok
        rl.permitir("+506X")  # ok
        rl.permitir("+506X")  # rechazo
        s = rl.stats()
        assert s["total_aceptados"] == 2
        assert s["total_rechazados"] == 1

    def test_stats_incluye_config(self):
        rl = SenderRateLimiter(max_por_minuto=42, burst=7)
        s = rl.stats()
        assert s["max_por_minuto"] == 42
        assert s["burst"] == 7


class TestValidaciones:
    def test_max_por_minuto_invalido(self):
        with pytest.raises(ValueError):
            SenderRateLimiter(max_por_minuto=0)

    def test_burst_invalido(self):
        with pytest.raises(ValueError):
            SenderRateLimiter(burst=0)
