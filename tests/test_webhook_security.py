"""Tests de utilities criptográficas para endpoints HTTP."""
from __future__ import annotations

import pytest

from src.utils.webhook_security import (
    compute_hmac_signature, verify_hmac_signature,
    verify_bearer_token, generate_secret,
    verify_timestamp_freshness,
)


SECRET = "test-secret-12345"


class TestComputeHmac:
    def test_devuelve_formato_algoritmo_igual_hex(self):
        sig = compute_hmac_signature(b"hola", secret=SECRET)
        assert sig.startswith("sha256=")
        algo, _, hex_str = sig.partition("=")
        assert algo == "sha256"
        assert len(hex_str) == 64  # SHA-256 = 32 bytes = 64 hex chars

    def test_string_se_codifica_a_utf8(self):
        sig_bytes = compute_hmac_signature(b"hola", secret=SECRET)
        sig_str = compute_hmac_signature("hola", secret=SECRET)
        assert sig_bytes == sig_str

    def test_secret_vacio_lanza(self):
        with pytest.raises(ValueError, match="vacío"):
            compute_hmac_signature(b"x", secret="")

    def test_algoritmo_invalido_lanza(self):
        with pytest.raises(ValueError, match="no soportado"):
            compute_hmac_signature(b"x", secret=SECRET, algoritmo="md5")

    def test_sha512_funciona(self):
        sig = compute_hmac_signature(b"x", secret=SECRET, algoritmo="sha512")
        assert sig.startswith("sha512=")
        _, _, hex_str = sig.partition("=")
        assert len(hex_str) == 128  # SHA-512 = 64 bytes = 128 hex

    def test_bodies_distintos_dan_firmas_distintas(self):
        a = compute_hmac_signature(b"x", secret=SECRET)
        b = compute_hmac_signature(b"y", secret=SECRET)
        assert a != b

    def test_secrets_distintos_dan_firmas_distintas(self):
        a = compute_hmac_signature(b"x", secret="s1")
        b = compute_hmac_signature(b"x", secret="s2")
        assert a != b


class TestVerifyHmac:
    def test_firma_valida_returns_true(self):
        body = b'{"event":"message"}'
        sig = compute_hmac_signature(body, secret=SECRET)
        assert verify_hmac_signature(body, sig, secret=SECRET) is True

    def test_firma_alterada_returns_false(self):
        body = b'{"event":"message"}'
        sig = compute_hmac_signature(body, secret=SECRET)
        # Alterar 1 carácter
        sig_mala = sig[:-1] + ("0" if sig[-1] != "0" else "1")
        assert verify_hmac_signature(body, sig_mala, secret=SECRET) is False

    def test_body_alterado_returns_false(self):
        sig = compute_hmac_signature(b"original", secret=SECRET)
        assert verify_hmac_signature(b"alterado", sig, secret=SECRET) is False

    def test_secret_incorrecto_returns_false(self):
        sig = compute_hmac_signature(b"x", secret=SECRET)
        assert verify_hmac_signature(b"x", sig, secret="otro-secret") is False

    def test_signature_vacia_returns_false(self):
        assert verify_hmac_signature(b"x", "", secret=SECRET) is False
        assert verify_hmac_signature(b"x", "   ", secret=SECRET) is False

    def test_secret_vacio_returns_false(self):
        sig = compute_hmac_signature(b"x", secret=SECRET)
        assert verify_hmac_signature(b"x", sig, secret="") is False

    def test_formato_solo_hex_usa_default(self):
        """Si signature no trae `algoritmo=`, asume sha256."""
        sig = compute_hmac_signature(b"x", secret=SECRET)
        _, _, solo_hex = sig.partition("=")
        assert verify_hmac_signature(b"x", solo_hex, secret=SECRET) is True

    def test_algoritmo_desconocido_returns_false(self):
        assert verify_hmac_signature(
            b"x", "md5=abc", secret=SECRET,
        ) is False

    def test_hex_invalido_returns_false(self):
        assert verify_hmac_signature(
            b"x", "sha256=NO-ES-HEX", secret=SECRET,
        ) is False

    def test_case_insensitive_en_hex(self):
        """SHA-256 hex puede venir uppercase y debe matchear."""
        body = b"hola"
        sig = compute_hmac_signature(body, secret=SECRET)
        sig_upper = sig.replace(sig.split("=")[1], sig.split("=")[1].upper())
        assert verify_hmac_signature(body, sig_upper, secret=SECRET) is True


class TestVerifyBearerToken:
    def test_token_correcto_returns_true(self):
        assert verify_bearer_token(
            "Bearer abc123", expected="abc123",
        ) is True

    def test_token_incorrecto_returns_false(self):
        assert verify_bearer_token(
            "Bearer abc123", expected="xyz789",
        ) is False

    def test_scheme_incorrecto_returns_false(self):
        assert verify_bearer_token(
            "Basic abc123", expected="abc123",
        ) is False

    def test_sin_scheme_returns_false(self):
        assert verify_bearer_token("abc123", expected="abc123") is False

    def test_header_vacio_returns_false(self):
        assert verify_bearer_token("", expected="abc") is False
        assert verify_bearer_token(None, expected="abc") is False  # type: ignore

    def test_expected_vacio_returns_false(self):
        """Defensa: token vacío no debe matchear nada."""
        assert verify_bearer_token("Bearer x", expected="") is False

    def test_case_insensitive_en_scheme(self):
        assert verify_bearer_token(
            "bearer abc", expected="abc",
        ) is True
        assert verify_bearer_token(
            "BEARER abc", expected="abc",
        ) is True


class TestGenerateSecret:
    def test_devuelve_string_urlsafe(self):
        s = generate_secret()
        # tokens urlsafe son [A-Za-z0-9_-]
        import re
        assert re.match(r"^[A-Za-z0-9_-]+$", s)

    def test_longitud_minima_16_bytes(self):
        with pytest.raises(ValueError, match=">= 16"):
            generate_secret(length_bytes=8)

    def test_no_repite(self):
        """Dos llamadas dan secrets distintos (probabilidad colisión ~0)."""
        s1 = generate_secret()
        s2 = generate_secret()
        assert s1 != s2


class TestVerifyTimestampFreshness:
    def test_timestamp_actual_es_ok(self):
        import time
        assert verify_timestamp_freshness(time.time()) is True

    def test_timestamp_viejo_returns_false(self):
        import time
        viejo = time.time() - 600  # 10 min atrás
        assert verify_timestamp_freshness(viejo, max_age_seconds=300) is False

    def test_timestamp_futuro_returns_false(self):
        """Defensa contra clock skew exagerado o replay del futuro."""
        import time
        futuro = time.time() + 1000
        assert verify_timestamp_freshness(futuro) is False

    def test_clock_skew_pequeno_se_acepta(self):
        """60s de skew adelantado se tolera."""
        import time
        skew = time.time() + 30
        assert verify_timestamp_freshness(skew) is True

    def test_none_returns_false(self):
        assert verify_timestamp_freshness(None) is False

    def test_string_numerico_funciona(self):
        import time
        assert verify_timestamp_freshness(str(time.time())) is True

    def test_string_no_numerico_returns_false(self):
        assert verify_timestamp_freshness("no-es-numero") is False

    def test_custom_now_fn_para_tests(self):
        fake_now = 1_000_000.0
        assert verify_timestamp_freshness(
            fake_now - 100, now_fn=lambda: fake_now, max_age_seconds=300,
        ) is True
        assert verify_timestamp_freshness(
            fake_now - 1000, now_fn=lambda: fake_now, max_age_seconds=300,
        ) is False
