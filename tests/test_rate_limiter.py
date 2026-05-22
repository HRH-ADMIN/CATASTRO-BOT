"""Tests del rate limiter."""
from __future__ import annotations
import threading
import time

import pytest

from src.utils.rate_limiter import RateLimiter


class TestTryAcquire:
    def test_capacidad_inicial_completa(self):
        rl = RateLimiter(capacidad=3, intervalo_segundos=1.0)
        # 3 adquisiciones inmediatas OK
        for _ in range(3):
            assert rl.try_acquire() is True
        # 4ta falla (sin esperar refill)
        assert rl.try_acquire() is False

    def test_capacidad_invalida_lanza(self):
        with pytest.raises(ValueError):
            RateLimiter(capacidad=0, intervalo_segundos=1.0)
        with pytest.raises(ValueError):
            RateLimiter(capacidad=5, intervalo_segundos=0)

    def test_tokens_multiples(self):
        rl = RateLimiter(capacidad=5, intervalo_segundos=1.0)
        assert rl.try_acquire(3) is True
        assert rl.try_acquire(3) is False  # quedaban 2
        assert rl.try_acquire(2) is True

    def test_refill_con_tiempo(self):
        rl = RateLimiter(capacidad=2, intervalo_segundos=0.1)
        rl.try_acquire()
        rl.try_acquire()
        assert rl.try_acquire() is False
        time.sleep(0.15)
        # Debió refillar 1+ tokens
        assert rl.try_acquire() is True


class TestAcquireBloqueante:
    def test_espera_si_vacío(self):
        rl = RateLimiter(capacidad=1, intervalo_segundos=0.1)
        rl.try_acquire()  # consumir el único token
        inicio = time.monotonic()
        ok = rl.acquire(timeout=1.0)
        duracion = time.monotonic() - inicio
        assert ok is True
        # Debió esperar al menos ~0.05s (refill rate)
        assert duracion > 0.04

    def test_timeout_devuelve_false(self):
        rl = RateLimiter(capacidad=1, intervalo_segundos=10.0)
        rl.try_acquire()  # consumir el único
        ok = rl.acquire(timeout=0.1)
        assert ok is False

    def test_acquire_inmediato_si_hay_tokens(self):
        rl = RateLimiter(capacidad=5, intervalo_segundos=1.0)
        inicio = time.monotonic()
        ok = rl.acquire()
        duracion = time.monotonic() - inicio
        assert ok is True
        assert duracion < 0.05  # casi instantáneo


class TestContextManager:
    def test_with_statement(self):
        rl = RateLimiter(capacidad=3, intervalo_segundos=1.0)
        with rl:
            pass
        stats = rl.stats()
        assert stats["total_acquired"] == 1


class TestStats:
    def test_stats_acumula(self):
        rl = RateLimiter(capacidad=5, intervalo_segundos=1.0)
        rl.try_acquire()
        rl.try_acquire()
        rl.try_acquire(10)  # falla (excede capacidad)
        s = rl.stats()
        assert s["total_acquired"] == 2
        assert s["total_rejected"] == 10

    def test_reset(self):
        rl = RateLimiter(capacidad=2, intervalo_segundos=1.0)
        rl.try_acquire()
        rl.try_acquire()
        assert rl.try_acquire() is False
        rl.reset()
        # Después de reset, vuelven los tokens
        assert rl.try_acquire() is True


class TestThreadSafety:
    def test_concurrencia_no_excede_capacidad(self):
        """20 threads pidiendo a la vez, capacidad=10 — sólo 10 OK."""
        rl = RateLimiter(capacidad=10, intervalo_segundos=100.0)
        resultados = []
        def worker():
            resultados.append(rl.try_acquire())
        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads: t.start()
        for t in threads: t.join()
        # Exactamente 10 trues
        assert sum(resultados) == 10
        assert resultados.count(False) == 10
