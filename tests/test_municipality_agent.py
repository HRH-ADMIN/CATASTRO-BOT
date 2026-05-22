"""Tests para MunicipalityAgent.

Cubre:
  - construir_url_formulario: URL pre-rellenada con parámetros correctos
  - generar_instrucciones_formulario: mensaje WhatsApp con URL
  - enviar_formulario: requiere confirmación, guarda en metadata
  - consultar_aviso_impuestos: IMAP mock con regexes
  - enviar_correo_pago: SMTP mock, requiere confirmación
  - consultar_visado: IMAP mock con regexes
"""
from __future__ import annotations

import contextlib
import email.mime.text as _mime_text
import imaplib
import json
import secrets
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("win32cred", reason="pywin32 requerido (solo Windows)")

from src.agents.municipality_agent import (  # noqa: E402
    MunicipalityAgent,
    DISTRITOS,
    PROCESOS,
    TIPOS_ACCESO,
)
from src.core.database import Database  # noqa: E402
from src.core.exceptions import AgentError, ConfirmationError  # noqa: E402
from src.models.plano import TipoPlano  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# TestDatabase (sin SQLCipher)
# ─────────────────────────────────────────────────────────────────────────────

class TestDatabase(Database):
    @contextlib.contextmanager
    def connect(self):
        conn = sqlite3.connect(str(self.path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
        finally:
            conn.close()


class FakeCredentialManager:
    def __init__(self):
        self._db_key = secrets.token_bytes(32)

    def get_or_create_db_key(self) -> bytes:
        return self._db_key

    def get_db_key(self) -> bytes:
        return self._db_key

    def get_muni_san_ramon(self) -> tuple[str, str]:
        return ("bot@example.com", "apppassword123")

    def get_operators(self) -> set[str]:
        return set()

    def is_operator(self, phone: str) -> bool:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    cm = FakeCredentialManager()
    database = TestDatabase(path=tmp_path / "test.db", credentials=cm)
    database.initialize_schema()
    return database


@pytest.fixture
def agent(db):
    cm = FakeCredentialManager()
    return MunicipalityAgent(
        db, cm,
        muni_email="catastral@sanramon.go.cr",
        muni_catastral="mgamboa@sanramondigital.net",
        smtp_host="smtp.gmail.com",
        smtp_port=587,
        imap_host="imap.gmail.com",
        imap_port=993,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _crear_exp(
    db,
    *,
    meta: dict | None = None,
    tipo_plano: str | None = None,
) -> str:
    return db.crear_expediente(
        numero_expediente="EXP-TEST-0001",
        tipo_plano=tipo_plano or TipoPlano.SEGREGACION.value,
        nombre_topografo="Luis Pérez",
        telefono_cliente="50688880001",
        nombre_cliente="Cliente Test",
        cedula_topografo="1-0001-0001",
        metadata=meta or {},
        actor="test",
    )


def _confirmar(db, eid: str, tipo_accion: str) -> None:
    """Crea y resuelve una acción como confirmada."""
    aid = db.crear_accion_pendiente(
        expediente_id=eid,
        tipo_accion=tipo_accion,
        descripcion=f"confirmacion de {tipo_accion}",
        actor="test",
    )
    db.resolver_accion(aid, "confirmada", whatsapp_response="SI", actor="test")


def _meta(db, eid: str) -> dict:
    return json.loads(db.obtener_expediente(eid).get("metadata_json") or "{}")


def _make_raw_email(body: str, subject: str = "EXP-TEST-0001") -> bytes:
    """Construye bytes de un email simple para mockear IMAP."""
    msg = _mime_text.MIMEText(body, "plain", "utf-8")
    msg["From"] = "mgamboa@sanramondigital.net"
    msg["To"] = "bot@example.com"
    msg["Subject"] = subject
    return msg.as_bytes()


def _imap_mock_con_email(body: str) -> MagicMock:
    """Mock de IMAP4_SSL que simula un inbox con un email."""
    raw = _make_raw_email(body)
    imap = MagicMock()
    imap.__enter__ = MagicMock(return_value=imap)
    imap.__exit__ = MagicMock(return_value=False)
    imap.search.return_value = (None, [b"1"])
    imap.fetch.return_value = (None, [(b"header flags", raw)])
    return imap


def _imap_mock_vacio() -> MagicMock:
    """Mock de IMAP4_SSL con inbox vacío."""
    imap = MagicMock()
    imap.__enter__ = MagicMock(return_value=imap)
    imap.__exit__ = MagicMock(return_value=False)
    imap.search.return_value = (None, [b""])
    return imap


def _smtp_mock() -> MagicMock:
    """Mock de smtplib.SMTP como context manager."""
    smtp = MagicMock()
    smtp.__enter__ = MagicMock(return_value=smtp)
    smtp.__exit__ = MagicMock(return_value=False)
    return smtp


# ─────────────────────────────────────────────────────────────────────────────
# construir_url_formulario
# ─────────────────────────────────────────────────────────────────────────────

class TestConstruirUrlFormulario:

    def test_url_contiene_entry_ids_principales(self, db, agent):
        eid = _crear_exp(db, meta={
            "apt_tomo": "5",
            "apt_asiento": "100",
            "apt_tramite": "9999001",
            "area_m2": "500",
            "finca_numero": "123456",
            "vertices": "8",
        })
        url = agent.construir_url_formulario(eid)

        assert "entry.413935745" in url    # tomo
        assert "entry.112406539" in url    # asiento
        assert "entry.1184863793" in url   # proyecto_apt
        assert "entry.2015524080" in url   # area
        assert "entry.2090596021" in url   # finca

    def test_url_incluye_datos_del_expediente(self, db, agent):
        eid = _crear_exp(db)
        url = agent.construir_url_formulario(eid)

        # URL debe ser del formulario Google Forms correcto
        assert "docs.google.com/forms/d/e/" in url
        assert "1FAIpQLSejhSph15X_RPtT39SjiLUYnCqjg_FB1Gq67A8mjgA-1XVXOQ" in url

    def test_proceso_segregacion(self, db, agent):
        eid = _crear_exp(db, tipo_plano=TipoPlano.SEGREGACION.value)
        url = agent.construir_url_formulario(eid)
        # "Segregación" URL-encoded
        assert "Segregaci" in url

    def test_proceso_reunion_de_fincas(self, db, agent):
        eid = _crear_exp(db, tipo_plano=TipoPlano.REUNION_DE_FINCAS.value)
        url = agent.construir_url_formulario(eid)
        assert "Segregar" in url

    def test_proceso_fincas_completas(self, db, agent):
        eid = _crear_exp(db, tipo_plano=TipoPlano.FINCAS_COMPLETAS.value)
        url = agent.construir_url_formulario(eid)
        assert "Localizar" in url

    def test_fecha_minuta_spliteada_en_partes(self, db, agent):
        eid = _crear_exp(db, meta={"apt_fecha_minuta": "2025-03-15"})
        url = agent.construir_url_formulario(eid)

        # El año debe aparecer en la URL
        assert "2025" in url
        # Los entry IDs de fecha deben aparecer
        assert "entry.1421348955_year" in url
        assert "entry.1421348955_month" in url
        assert "entry.1421348955_day" in url

    def test_fecha_invalida_no_rompe(self, db, agent):
        eid = _crear_exp(db, meta={"apt_fecha_minuta": "fecha-invalida"})
        # No debe lanzar excepción
        url = agent.construir_url_formulario(eid)
        assert "docs.google.com" in url

    def test_distrito_invalido_usa_fallback(self, db, agent):
        eid = _crear_exp(db, meta={"distrito": "99 Cantón Inexistente"})
        url = agent.construir_url_formulario(eid)
        # Debe usar "01 San Ramón" como fallback (URL-encoded)
        assert "01" in url
        assert "San+Ram" in url or "San%20Ram" in url or "San+Ram" in url

    def test_tipo_acceso_invalido_usa_fallback(self, db, agent):
        eid = _crear_exp(db, meta={"tipo_acceso": "Vereda Imposible"})
        url = agent.construir_url_formulario(eid)
        # Debe usar "Ruta Cantonal" como fallback
        assert "Cantonal" in url

    def test_expediente_inexistente_lanza_agent_error(self, db, agent):
        with pytest.raises(AgentError):
            agent.construir_url_formulario("uuid-que-no-existe")

    def test_nombre_topografo_en_url(self, db, agent):
        eid = _crear_exp(db)
        url = agent.construir_url_formulario(eid)
        # nombre_topografo = "Luis Pérez" pero el agent ahora lo formatea
        # a minúsculas para muni (R-M3 aprendida 2026-05-13)
        # Acepta tanto "luis" como "Luis" (con encoding URL).
        assert "luis" in url.lower() or "Luis" in url


# ─────────────────────────────────────────────────────────────────────────────
# generar_instrucciones_formulario
# ─────────────────────────────────────────────────────────────────────────────

class TestGenerarInstruccionesFormulario:

    def test_incluye_numero_expediente(self, db, agent):
        eid = _crear_exp(db)
        msg = agent.generar_instrucciones_formulario(eid)
        assert "EXP-TEST-0001" in msg

    def test_incluye_url_formulario(self, db, agent):
        eid = _crear_exp(db)
        msg = agent.generar_instrucciones_formulario(eid)
        assert "docs.google.com" in msg

    def test_incluye_instrucciones_subida_documentos(self, db, agent):
        eid = _crear_exp(db)
        msg = agent.generar_instrucciones_formulario(eid)
        assert "DOCUMENTOS" in msg

    def test_incluye_instrucciones_shape(self, db, agent):
        eid = _crear_exp(db)
        msg = agent.generar_instrucciones_formulario(eid)
        assert "Shape" in msg

    def test_incluye_confirmacion_si(self, db, agent):
        eid = _crear_exp(db)
        msg = agent.generar_instrucciones_formulario(eid)
        assert "SI" in msg

    def test_incluye_titulo_municipalidad(self, db, agent):
        eid = _crear_exp(db)
        msg = agent.generar_instrucciones_formulario(eid)
        assert "Municipalidad" in msg or "Municipal" in msg

    def test_expediente_inexistente_lanza(self, db, agent):
        with pytest.raises(AgentError):
            agent.generar_instrucciones_formulario("uuid-inexistente")


# ─────────────────────────────────────────────────────────────────────────────
# enviar_formulario
# ─────────────────────────────────────────────────────────────────────────────

class TestEnviarFormulario:

    def test_sin_confirmacion_lanza_confirmation_error(self, db, agent):
        eid = _crear_exp(db)
        with pytest.raises(ConfirmationError):
            agent.enviar_formulario(eid)

    def test_con_confirmacion_devuelve_url(self, db, agent):
        eid = _crear_exp(db)
        _confirmar(db, eid, "enviar_formulario_muni")

        url = agent.enviar_formulario(eid)

        assert isinstance(url, str)
        assert url.startswith("https://docs.google.com/forms/")

    def test_con_confirmacion_guarda_url_en_metadata(self, db, agent):
        eid = _crear_exp(db)
        _confirmar(db, eid, "enviar_formulario_muni")

        agent.enviar_formulario(eid)

        meta = _meta(db, eid)
        assert "muni_formulario_url" in meta
        assert meta["muni_formulario_url"].startswith("https://")

    def test_con_confirmacion_guarda_timestamp_en_metadata(self, db, agent):
        eid = _crear_exp(db)
        _confirmar(db, eid, "enviar_formulario_muni")

        agent.enviar_formulario(eid)

        meta = _meta(db, eid)
        assert "muni_url_generada" in meta

    def test_expediente_inexistente_lanza(self, db, agent):
        """Sin expediente válido, _exigir_confirmacion pasa pero construir_url falla."""
        # Sin confirmación ni expediente → ConfirmationError
        with pytest.raises(Exception):
            agent.enviar_formulario("uuid-inexistente")


# ─────────────────────────────────────────────────────────────────────────────
# consultar_aviso_impuestos
# ─────────────────────────────────────────────────────────────────────────────

class TestConsultarAvisoImpuestos:

    def test_inbox_vacio_devuelve_none(self, db, agent):
        eid = _crear_exp(db)
        imap = _imap_mock_vacio()
        with patch("imaplib.IMAP4_SSL", return_value=imap):
            resultado = agent.consultar_aviso_impuestos(eid)
        assert resultado is None

    def test_email_con_monto_devuelve_dict(self, db, agent):
        eid = _crear_exp(db)
        cuerpo = "Estimado, debe pagar impuesto municipal. Monto a pagar: ₡15.000,00"
        imap = _imap_mock_con_email(cuerpo)
        with patch("imaplib.IMAP4_SSL", return_value=imap):
            resultado = agent.consultar_aviso_impuestos(eid)

        assert resultado is not None
        assert isinstance(resultado, dict)
        assert "monto" in resultado
        assert "detalle" in resultado

    def test_email_con_tributo_reconocido(self, db, agent):
        eid = _crear_exp(db)
        cuerpo = "El tributo generado por el visado es de ₡8500.00."
        imap = _imap_mock_con_email(cuerpo)
        with patch("imaplib.IMAP4_SSL", return_value=imap):
            resultado = agent.consultar_aviso_impuestos(eid)

        assert resultado is not None

    def test_email_con_timbre_reconocido(self, db, agent):
        eid = _crear_exp(db)
        cuerpo = "Timbre fiscal: ₡3200.00 pendiente de pago."
        imap = _imap_mock_con_email(cuerpo)
        with patch("imaplib.IMAP4_SSL", return_value=imap):
            resultado = agent.consultar_aviso_impuestos(eid)

        assert resultado is not None

    def test_email_sin_patron_impuesto_devuelve_none(self, db, agent):
        eid = _crear_exp(db)
        cuerpo = "El formulario fue recibido. Le contactaremos pronto."
        imap = _imap_mock_con_email(cuerpo)
        with patch("imaplib.IMAP4_SSL", return_value=imap):
            resultado = agent.consultar_aviso_impuestos(eid)

        assert resultado is None

    def test_imap_error_devuelve_none_sin_lanzar(self, db, agent):
        eid = _crear_exp(db)
        with patch("imaplib.IMAP4_SSL", side_effect=imaplib.IMAP4.error("conn refused")):
            resultado = agent.consultar_aviso_impuestos(eid)
        assert resultado is None

    def test_os_error_devuelve_none_sin_lanzar(self, db, agent):
        eid = _crear_exp(db)
        with patch("imaplib.IMAP4_SSL", side_effect=OSError("timeout")):
            resultado = agent.consultar_aviso_impuestos(eid)
        assert resultado is None

    def test_expediente_inexistente_lanza_agent_error(self, db, agent):
        with pytest.raises(AgentError):
            agent.consultar_aviso_impuestos("uuid-inexistente")


# ─────────────────────────────────────────────────────────────────────────────
# enviar_correo_pago
# ─────────────────────────────────────────────────────────────────────────────

class TestEnviarCorreoPago:

    def test_sin_confirmacion_lanza_confirmation_error(self, db, agent):
        eid = _crear_exp(db)
        smtp = _smtp_mock()
        with patch("smtplib.SMTP", return_value=smtp):
            with pytest.raises(ConfirmationError):
                agent.enviar_correo_pago(eid)

    def test_con_confirmacion_llama_sendmail(self, db, agent):
        eid = _crear_exp(db, meta={"apt_tramite": "9999001"})
        _confirmar(db, eid, "enviar_correo_muni")

        smtp = _smtp_mock()
        with patch("smtplib.SMTP", return_value=smtp):
            agent.enviar_correo_pago(eid)

        smtp.sendmail.assert_called_once()

    def test_con_confirmacion_destinatario_correcto(self, db, agent):
        eid = _crear_exp(db, meta={"apt_tramite": "9999001"})
        _confirmar(db, eid, "enviar_correo_muni")

        smtp = _smtp_mock()
        with patch("smtplib.SMTP", return_value=smtp):
            agent.enviar_correo_pago(eid)

        args = smtp.sendmail.call_args[0]
        destinatarios = args[1]  # lista de destinatarios
        assert "mgamboa@sanramondigital.net" in destinatarios

    def test_con_confirmacion_asunto_contiene_resello(self, db, agent):
        eid = _crear_exp(db, meta={"apt_tramite": "9999001"})
        _confirmar(db, eid, "enviar_correo_muni")

        smtp = _smtp_mock()
        with patch("smtplib.SMTP", return_value=smtp):
            agent.enviar_correo_pago(eid)

        email_str = smtp.sendmail.call_args[0][2]
        assert "RESELLO" in email_str

    def test_con_confirmacion_guarda_metadata(self, db, agent):
        eid = _crear_exp(db)
        _confirmar(db, eid, "enviar_correo_muni")

        smtp = _smtp_mock()
        with patch("smtplib.SMTP", return_value=smtp):
            agent.enviar_correo_pago(eid)

        meta = _meta(db, eid)
        assert meta.get("muni_pago_enviado") is True
        assert "muni_pago_fecha" in meta

    def test_con_confirmacion_devuelve_numero_expediente(self, db, agent):
        eid = _crear_exp(db)
        _confirmar(db, eid, "enviar_correo_muni")

        smtp = _smtp_mock()
        with patch("smtplib.SMTP", return_value=smtp):
            ref = agent.enviar_correo_pago(eid)

        assert ref == "EXP-TEST-0001"


# ─────────────────────────────────────────────────────────────────────────────
# consultar_visado
# ─────────────────────────────────────────────────────────────────────────────

class TestConsultarVisado:

    def test_inbox_vacio_devuelve_none(self, db, agent):
        eid = _crear_exp(db)
        imap = _imap_mock_vacio()
        with patch("imaplib.IMAP4_SSL", return_value=imap):
            resultado = agent.consultar_visado(eid)
        assert resultado is None

    def test_email_visado_aprobado(self, db, agent):
        eid = _crear_exp(db)
        cuerpo = "El plano ha sido visado aprobado. Puede proceder con la inscripción."
        imap = _imap_mock_con_email(cuerpo)
        with patch("imaplib.IMAP4_SSL", return_value=imap):
            resultado = agent.consultar_visado(eid)
        assert resultado == "aprobado"

    def test_email_plano_visado(self, db, agent):
        eid = _crear_exp(db)
        cuerpo = "El plano visado fue procesado correctamente."
        imap = _imap_mock_con_email(cuerpo)
        with patch("imaplib.IMAP4_SSL", return_value=imap):
            resultado = agent.consultar_visado(eid)
        assert resultado == "aprobado"

    def test_email_visto_bueno(self, db, agent):
        eid = _crear_exp(db)
        cuerpo = "Se otorga el visto bueno al plano presentado."
        imap = _imap_mock_con_email(cuerpo)
        with patch("imaplib.IMAP4_SSL", return_value=imap):
            resultado = agent.consultar_visado(eid)
        assert resultado == "aprobado"

    def test_email_se_aprueba(self, db, agent):
        eid = _crear_exp(db)
        cuerpo = "Se aprueba el plano catastral presentado."
        imap = _imap_mock_con_email(cuerpo)
        with patch("imaplib.IMAP4_SSL", return_value=imap):
            resultado = agent.consultar_visado(eid)
        assert resultado == "aprobado"

    def test_email_visado_rechazado(self, db, agent):
        eid = _crear_exp(db)
        cuerpo = "Estado del trámite: visado rechazado. Debe corregir los errores indicados."
        imap = _imap_mock_con_email(cuerpo)
        with patch("imaplib.IMAP4_SSL", return_value=imap):
            resultado = agent.consultar_visado(eid)
        assert resultado == "rechazado"

    def test_email_se_rechaza(self, db, agent):
        eid = _crear_exp(db)
        cuerpo = "Se rechaza la solicitud de visado por inconsistencias en el área."
        imap = _imap_mock_con_email(cuerpo)
        with patch("imaplib.IMAP4_SSL", return_value=imap):
            resultado = agent.consultar_visado(eid)
        assert resultado == "rechazado"

    def test_email_sin_patron_devuelve_none(self, db, agent):
        eid = _crear_exp(db)
        cuerpo = "Gracias por su envío. Su solicitud está en revisión."
        imap = _imap_mock_con_email(cuerpo)
        with patch("imaplib.IMAP4_SSL", return_value=imap):
            resultado = agent.consultar_visado(eid)
        assert resultado is None

    def test_imap_error_devuelve_none_sin_lanzar(self, db, agent):
        eid = _crear_exp(db)
        with patch("imaplib.IMAP4_SSL", side_effect=imaplib.IMAP4.error("timeout")):
            resultado = agent.consultar_visado(eid)
        assert resultado is None

    def test_os_error_devuelve_none_sin_lanzar(self, db, agent):
        eid = _crear_exp(db)
        with patch("imaplib.IMAP4_SSL", side_effect=OSError("network unreachable")):
            resultado = agent.consultar_visado(eid)
        assert resultado is None

    def test_expediente_inexistente_lanza_agent_error(self, db, agent):
        with pytest.raises(AgentError):
            agent.consultar_visado("uuid-inexistente")
