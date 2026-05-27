"""Tests del redactor de PII en logs (Sprint 4 / S-08).

Cubre:
  - redact(): cubre cédula nacional (con/sin separadores), jurídica,
    DIMEX, teléfono CR (con/sin +506), email.
  - Cosas que NO deben redactarse: números de expediente, IDs APT,
    fechas, identificadores prediales.
  - PIIRedactor filter: aplica al record vía record.getMessage(),
    captura args (logger.info("foo %s", cedula)), idempotente.
  - install_on_root_logger: idempotente, no agrega dos veces.
  - CATASTRO_LOG_REDACT_PII=0 desactiva.
  - Integration: logger.info con cédula → archivo no la contiene.

Plan: PLAN_MEJORAS Sprint 4 / S-08.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import pytest

from src.utils import pii_redactor as pr


# ─── redact() puro ───────────────────────────────────────────────────


class TestRedactCedulaNacional:
    def test_con_guiones(self):
        assert "CEDULA_REDACTED" in pr.redact("Cliente 1-1234-5678 firmó")

    def test_con_espacios(self):
        assert "CEDULA_REDACTED" in pr.redact("Cliente 1 1234 5678 firmó")

    def test_sin_separadores_9_digitos(self):
        assert "CEDULA_REDACTED" in pr.redact("Cedula 112345678 OK")

    def test_no_matchea_8_digitos(self):
        # 8 dígitos podría ser teléfono, pero NO cédula
        out = pr.redact("Codigo 12345678 random")
        assert "CEDULA_REDACTED" not in out

    def test_no_matchea_10_digitos(self):
        # 10 dígitos podría ser cédula jurídica, no nacional
        out = pr.redact("Codigo 1234567890 random")
        assert "CEDULA_REDACTED" not in out

    def test_no_matchea_fechas(self):
        # 2026-05-27 — 4-2-2 no cuadra con 1-4-4
        out = pr.redact("Fecha 2026-05-27 corrida")
        assert out == "Fecha 2026-05-27 corrida"

    def test_provincia_1_a_9(self):
        for p in range(1, 10):
            inp = f"Cedula {p}-1234-5678 firma"
            assert "CEDULA_REDACTED" in pr.redact(inp), f"falló para {p}"


class TestRedactCedulaJuridica:
    def test_con_guiones(self):
        out = pr.redact("Empresa 3-101-123456 paga")
        assert "CED_JUR_REDACTED" in out

    def test_sin_separadores_10_digitos(self):
        out = pr.redact("Empresa 3101123456 paga")
        assert "CED_JUR_REDACTED" in out

    def test_no_matchea_si_no_empieza_con_3(self):
        # Cédulas jurídicas en CR siempre empiezan con 3
        out = pr.redact("Codigo 4-101-123456 random")
        assert "CED_JUR_REDACTED" not in out


class TestRedactDimex:
    def test_12_digitos(self):
        out = pr.redact("Extranjero 118200012345 atendido")
        assert "DIMEX_REDACTED" in out

    def test_no_matchea_otros_largos(self):
        out = pr.redact("Codigo 12345 random")
        assert "DIMEX_REDACTED" not in out


class TestRedactTelefono:
    def test_con_prefijo_506_y_espacios(self):
        out = pr.redact("WhatsApp +506 8888 8888")
        assert "TEL_REDACTED" in out
        assert "8888" not in out

    def test_con_prefijo_506_sin_separadores(self):
        out = pr.redact("WhatsApp +50688888888")
        assert "TEL_REDACTED" in out

    def test_con_prefijo_506_guion(self):
        out = pr.redact("WhatsApp +506-8888-8888")
        assert "TEL_REDACTED" in out

    def test_sin_prefijo_8_digitos(self):
        out = pr.redact("Llamar al 88888888 hoy")
        assert "TEL_REDACTED" in out

    def test_sin_prefijo_separadores(self):
        out = pr.redact("Llamar al 8888 8888 hoy")
        assert "TEL_REDACTED" in out

    def test_no_matchea_si_empieza_con_1_o_3(self):
        # 1 = cédula provincia, 3 = jurídica. Teléfonos CR no empiezan así.
        out = pr.redact("Codigo 12345678 random")
        assert "TEL_REDACTED" not in out


class TestRedactEmail:
    def test_email_gmail(self):
        out = pr.redact("Enviar a topografia@gmail.com hoy")
        assert "EMAIL_REDACTED" in out
        assert "topografia" not in out

    def test_email_con_punto(self):
        out = pr.redact("luis.alonso@empresa.co.cr")
        assert "EMAIL_REDACTED" in out

    def test_email_con_mas(self):
        out = pr.redact("hr+test@dominio.com")
        assert "EMAIL_REDACTED" in out


class TestNoRedactaLoQueDebeMantenerSe:
    def test_numero_expediente(self):
        for s in ("RDF-2026-001", "SEG-2026-005", "DIV-2025-012"):
            assert s in pr.redact(s), f"redactó {s} accidentalmente"

    def test_id_tramite_apt(self):
        # 7 dígitos típico de APT
        out = pr.redact("Tramite 1258460 OK")
        assert "1258460" in out

    def test_folio_real(self):
        # Formato típico folio CR: 1-1234-5678 ¡pero es 1-4-4 igual que cédula!
        # Esto es ambiguo — el patrón los confunde. Documentamos como limitación.
        # Acá testeamos lo que SÍ funciona: identificador predial 1-12-3456-7890
        out = pr.redact("Predio 1-12-3456-7890 inscrito")
        # Patrones no matchean 1-12-3456-7890 (formato 1-2-4-4)
        assert "1-12-3456-7890" in out

    def test_timestamps_no_se_tocan(self):
        out = pr.redact("2026-05-27T10:30:00.123Z")
        assert "2026" in out

    def test_idempotente(self):
        s = "Cliente 1-1234-5678 con email a@b.com"
        once = pr.redact(s)
        twice = pr.redact(once)
        assert once == twice


# ─── PIIRedactor filter ──────────────────────────────────────────────


class TestPIIRedactorFilter:
    def test_filter_redacta_msg_simple(self):
        filt = pr.PIIRedactor()
        rec = logging.LogRecord(
            "test", logging.INFO, "f.py", 1,
            "Cedula 1-1234-5678", None, None,
        )
        filt.filter(rec)
        assert "CEDULA_REDACTED" in rec.getMessage()

    def test_filter_redacta_args_formateados(self):
        """logger.info('foo %s', cedula) → args debe redactarse al formatear."""
        filt = pr.PIIRedactor()
        rec = logging.LogRecord(
            "test", logging.INFO, "f.py", 1,
            "Cliente %s firmó", ("1-1234-5678",), None,
        )
        filt.filter(rec)
        # Después del filter, args debe estar vacío y msg ya formateado+redactado
        assert rec.args == ()
        assert "CEDULA_REDACTED" in rec.getMessage()

    def test_filter_no_modifica_si_no_hay_pii(self):
        filt = pr.PIIRedactor()
        rec = logging.LogRecord(
            "test", logging.INFO, "f.py", 1,
            "Nada sensible aquí", None, None,
        )
        filt.filter(rec)
        assert rec.getMessage() == "Nada sensible aquí"

    def test_filter_devuelve_true_siempre(self):
        """Un filtro de redacción nunca debe filtrar (devolver False)."""
        filt = pr.PIIRedactor()
        rec = logging.LogRecord("t", logging.INFO, "f.py", 1, "msg", None, None)
        assert filt.filter(rec) is True

    def test_filter_no_explota_con_msg_no_string(self):
        """Logger acepta record.msg como object — debe sobrevivir."""
        filt = pr.PIIRedactor()
        rec = logging.LogRecord("t", logging.INFO, "f.py", 1,
                                {"datos": "x"}, None, None)
        # No debe lanzar
        assert filt.filter(rec) is True

    def test_disabled_via_env_var(self, monkeypatch):
        monkeypatch.setenv("CATASTRO_LOG_REDACT_PII", "0")
        filt = pr.PIIRedactor()
        rec = logging.LogRecord(
            "test", logging.INFO, "f.py", 1,
            "Cedula 1-1234-5678", None, None,
        )
        filt.filter(rec)
        # Con redactor desactivado, msg queda igual
        assert "1-1234-5678" in rec.getMessage()


# ─── install_on_root_logger ──────────────────────────────────────────


class TestInstallOnRoot:
    def test_agrega_filtro_al_logger(self):
        name = "catastro_test_s08"
        root = logging.getLogger(name)
        for f in list(root.filters):
            root.removeFilter(f)
        pr.install_on_root_logger(name)
        assert any(isinstance(f, pr.PIIRedactor) for f in root.filters)
        root.removeFilter(next(f for f in root.filters
                               if isinstance(f, pr.PIIRedactor)))

    def test_idempotente(self):
        name = "catastro_test_s08_idem"
        root = logging.getLogger(name)
        for f in list(root.filters):
            root.removeFilter(f)
        pr.install_on_root_logger(name)
        pr.install_on_root_logger(name)
        pr.install_on_root_logger(name)
        n = sum(1 for f in root.filters if isinstance(f, pr.PIIRedactor))
        assert n == 1


# ─── Integration: write a log file and verify redaction ──────────────


class TestIntegrationConFileHandler:
    def test_archivo_no_contiene_cedula_ni_telefono(self, tmp_path):
        log_file = tmp_path / "test.log"

        # Setup logger limpio
        name = "catastro_test_s08_integration"
        log = logging.getLogger(name)
        log.handlers.clear()
        log.filters.clear()
        log.setLevel(logging.DEBUG)
        handler = logging.FileHandler(log_file, encoding="utf-8")
        handler.setLevel(logging.DEBUG)
        log.addHandler(handler)
        pr.install_on_root_logger(name)

        try:
            # Emitir mensajes con PII
            log.info("Cliente 1-1234-5678 con telefono +506 8888 8888")
            log.warning("Email a topografia@gmail.com fallido")
            log.error("Empresa 3-101-123456 sin pagar")
            # Forzar flush
            handler.flush()

            contenido = log_file.read_text(encoding="utf-8")
            # PII NO debe aparecer
            assert "1-1234-5678" not in contenido
            assert "+506" not in contenido
            assert "8888 8888" not in contenido
            assert "topografia@gmail.com" not in contenido
            assert "3-101-123456" not in contenido
            # Placeholders SÍ deben aparecer
            assert "CEDULA_REDACTED" in contenido
            assert "TEL_REDACTED" in contenido
            assert "EMAIL_REDACTED" in contenido
            assert "CED_JUR_REDACTED" in contenido
        finally:
            log.handlers.clear()
            log.filters.clear()
            handler.close()
