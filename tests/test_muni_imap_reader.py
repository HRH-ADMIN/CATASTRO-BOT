"""Tests del clasificador IMAP de respuestas muni.

Cubre los 5 tipos: ACUSE_GOOGLE, APROBADO, MOROSIDAD, RECHAZADO, DESCONOCIDO.
"""
from __future__ import annotations

import pytest

from src.utils.muni_imap_reader import (
    clasificar_email,
    extraer_numero_tramite,
    extraer_monto_pendiente,
    TIPO_ACUSE_GOOGLE, TIPO_APROBADO, TIPO_MOROSIDAD,
    TIPO_RECHAZADO, TIPO_DESCONOCIDO,
)


# ── clasificar_email ───────────────────────────────────────────────────

class TestClasificarEmail:

    def test_acuse_google_por_remitente(self):
        t = clasificar_email(
            from_addr="forms-receipts-noreply@google.com",
            subject="Gracias por rellenar este formulario: Formulario APT-PUBLICO",
            body="Has recibido este correo porque has rellenado el siguiente formulario...",
        )
        assert t == TIPO_ACUSE_GOOGLE

    def test_aprobado_por_keyword(self):
        t = clasificar_email(
            from_addr="mgamboa@sanramon.go.cr",
            subject="Visado aprobado APT 1223951",
            body="Le informamos que el plano ha sido aprobado y visado.",
        )
        assert t == TIPO_APROBADO

    def test_aprobado_variantes(self):
        for body in [
            "Su plano se aprueba para registro",
            "Se le otorga el visto bueno municipal",
            "Plano visado y listo",
            "visado municipal otorgado",
            "visado municipal emitido",
        ]:
            t = clasificar_email(
                "mgamboa@sanramon.go.cr", "respuesta APT", body,
            )
            assert t == TIPO_APROBADO, f"falló: {body!r}"

    def test_morosidad_impuestos_pendientes(self):
        t = clasificar_email(
            from_addr="mgamboa@sanramon.go.cr",
            subject="Trámite APT 1223951 - Notificación",
            body="El propietario tiene impuestos municipales pendientes. "
                 "Debe normalizar antes de continuar.",
        )
        assert t == TIPO_MOROSIDAD

    def test_morosidad_declaracion_bienes(self):
        t = clasificar_email(
            from_addr="mgamboa@sanramon.go.cr",
            subject="APT 123",
            body="Falta la declaración de bienes inmuebles vigente.",
        )
        assert t == TIPO_MOROSIDAD

    def test_morosidad_obligaciones(self):
        t = clasificar_email(
            "muni@sanramon.go.cr", "x",
            "El propietario no está al día con las obligaciones municipales.",
        )
        assert t == TIPO_MOROSIDAD

    def test_rechazado_por_correccion(self):
        t = clasificar_email(
            from_addr="mgamboa@sanramon.go.cr",
            subject="Plano rechazado APT 1223951",
            body="Falta indicar el acceso. Debe aportar nueva versión.",
        )
        assert t == TIPO_RECHAZADO

    def test_rechazado_no_procede(self):
        t = clasificar_email(
            "mgamboa@sanramon.go.cr", "x",
            "El visado no procede por las siguientes razones...",
        )
        assert t == TIPO_RECHAZADO

    def test_rechazado_denegado(self):
        t = clasificar_email(
            "mgamboa@sanramon.go.cr", "Visado denegado", "x",
        )
        assert t == TIPO_RECHAZADO

    def test_desconocido_remitente_externo(self):
        t = clasificar_email(
            from_addr="random@example.com",
            subject="otra cosa",
            body="texto sin keywords muni",
        )
        assert t == TIPO_DESCONOCIDO

    def test_desconocido_muni_sin_keywords(self):
        """Email de la muni pero sin patrones reconocidos."""
        t = clasificar_email(
            from_addr="mgamboa@sanramon.go.cr",
            subject="Saludo cordial",
            body="Buenos días, le comento que...",
        )
        assert t == TIPO_DESCONOCIDO

    def test_aprobado_tiene_prioridad_sobre_rechazado(self):
        """Si menciona 'aprobado' y 'rechazado', gana aprobado."""
        t = clasificar_email(
            "mgamboa@sanramon.go.cr",
            "Visado aprobado",
            "Aprobado pese a que inicialmente se rechazaba...",
        )
        assert t == TIPO_APROBADO

    def test_morosidad_prioridad_sobre_rechazado(self):
        """Morosidad se evalúa antes del rechazado (la muni notifica
        morosidad como primer paso, no es rechazo técnico)."""
        t = clasificar_email(
            "mgamboa@sanramon.go.cr", "x",
            "El propietario tiene impuestos pendientes. "
            "Falta normalizar para continuar.",
        )
        assert t == TIPO_MOROSIDAD  # NO rechazado aunque tenga "falta"

    def test_case_insensitive(self):
        t = clasificar_email(
            "MGAMBOA@SANRAMON.GO.CR", "VISADO APROBADO",
            "TODO EN MAYÚSCULAS",
        )
        assert t == TIPO_APROBADO


# ── extraer_numero_tramite ─────────────────────────────────────────────

class TestExtraerNumero:

    def test_apt_con_numero(self):
        assert extraer_numero_tramite("APT 1223951 aprobado") == "1223951"

    def test_tramite_palabra(self):
        assert extraer_numero_tramite("Trámite #1223951") == "1223951"

    def test_expediente(self):
        assert extraer_numero_tramite("expediente Nº 9876543") == "9876543"

    def test_caso_real_tilman(self):
        body = ("Su proyecto presentado al CFIA bajo el número 1223951 "
                "fue recibido.")
        assert extraer_numero_tramite(body) == "1223951"

    def test_sin_numero_devuelve_None(self):
        assert extraer_numero_tramite("sin trámite") is None

    def test_vacio(self):
        assert extraer_numero_tramite("") is None
        assert extraer_numero_tramite(None) is None


# ── extraer_monto_pendiente ────────────────────────────────────────────

class TestExtraerMonto:

    def test_monto_con_simbolo(self):
        m = extraer_monto_pendiente("Debe pagar ₡ 50,000 antes del...")
        assert m == 50000.0

    def test_monto_simple(self):
        m = extraer_monto_pendiente("Monto adeudado: ₡125000")
        assert m == 125000.0

    def test_sin_monto(self):
        assert extraer_monto_pendiente("No hay número") is None


# ── Casos reales documentados ──────────────────────────────────────────

class TestCasosRealesTILMAN:
    """Casos extraídos del acuse oficial TILMAN 2026-05-13."""

    def test_acuse_tilman_oficial(self):
        body = (
            "Gracias por rellenar este formulario: Formulario APT-PUBLICO "
            "Has recibido este correo porque has rellenado el siguiente "
            "formulario con tu dirección de correo. This form is owned by "
            "Municipalidad de San Ramón."
        )
        t = clasificar_email(
            "forms-receipts-noreply@google.com",
            "Gracias por rellenar este formulario: Formulario APT-PUBLICO",
            body,
        )
        assert t == TIPO_ACUSE_GOOGLE

    def test_morosidad_etapa_1_muni(self):
        """Texto típico del acuse cuando muni detecta morosidad en etapa 1."""
        body = (
            "Se verificará que el propietario del inmueble esté al día con "
            "las obligaciones municipales. De no cumplirse se le notificará "
            "al profesional. El propietario no está al día con impuestos."
        )
        t = clasificar_email("mgamboa@sanramon.go.cr", "Notificación", body)
        assert t == TIPO_MOROSIDAD
