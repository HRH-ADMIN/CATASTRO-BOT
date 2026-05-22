"""Tests de los helpers _llenar_seccion_* del APTAgent.

Verifican que las REGLAS DE OFICINA (interés social desmarcado, naturaleza=2,
contratante=propietario, etc.) se aplican correctamente cuando el bot llena
el formulario Nuevo Contrato de APT.

Todos los tests usan MagicMock para `page` — no abren navegador real.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

pytest.importorskip("win32cred", reason="solo Windows")
pytest.importorskip("playwright", reason="playwright requerido")

from tests.conftest import FakeCredentialManager, TestDatabase  # noqa: E402
from src.agents.apt_agent import APTAgent  # noqa: E402


def _make_agent(tmp_path: Path) -> APTAgent:
    creds = FakeCredentialManager()
    db = TestDatabase(path=tmp_path / "t.db", credentials=creds)
    db.initialize_schema()
    return APTAgent(db, creds)


def _mock_page() -> MagicMock:
    """Construye un MagicMock que se comporta como una page Playwright para los
    helpers _set_input/_set_select/_set_check/_fill_if_exists.

    Notas:
      - `evaluate(...)` devuelve True para que la estrategia "JS directo" del
        código se considere exitosa.
      - Para el evaluate de lectura RNP/TSE devuelve un dict no-vacío para
        que el flujo no aborte por "RNP no respondió".
      - `is_disabled()` devuelve False para que `_set_input` no salte.
    """
    page = MagicMock()
    locator = MagicMock()
    locator.count.return_value = 1
    locator.first = locator
    locator.input_value.return_value = ""
    locator.is_disabled.return_value = False
    locator.is_checked.return_value = False
    locator.evaluate.return_value = True       # JS directo "exitoso"
    page.locator.return_value = locator
    def _eval(js, *args, **kw):
        # Lectura del RNP/TSE: devolver un nombre simulado para que la
        # validación tenga algo con qué comparar (sin disparar discrepancia
        # ya que los tests no declaran nombre del registro).
        if "nombre:" in js and "ap1:" in js and "ap2:" in js:
            return {"nombre": "MOCK", "ap1": "TEST", "ap2": "USER"}
        return True
    page.evaluate.side_effect = _eval
    return page


def _selectores_locados(page) -> list[str]:
    """Devuelve la lista de selectores con los que se llamó page.locator()."""
    return [c.args[0] for c in page.locator.call_args_list if c.args]


# ─── Sección Propietario ─────────────────────────────────────────────────

class TestLlenarPropietario:
    def test_fisica_no_llena_nombre_apellidos(self, tmp_path):
        """Para FÍSICA, APT auto-completa nombre/apellidos del RNP — bot no toca."""
        agent = _make_agent(tmp_path)
        page = _mock_page()
        datos = {
            "tipo_cedula": "1",
            "cedula":      "2-0281-0882",
            "correo":      "test@example.com",
        }
        agent._llenar_seccion_propietario(page, datos)

        sels = _selectores_locados(page)
        # Debe haber tocado tipo_cedula, cédula y correo
        assert "#dllcedulasPropietario" in sels
        assert "#txtcedulaPropietario" in sels
        assert "#txtcorreo" in sels
        # NO debe haber tocado los campos de nombre/apellidos para FÍSICA
        assert "#txtnombrePropietario" not in sels
        assert "#txtApellido1Propietario" not in sels
        assert "#txtApellido2Propietario" not in sels

    def test_juridica_si_llena_nombre(self, tmp_path):
        """Para JURÍDICA, sí hay que llenar el nombre (razón social)."""
        agent = _make_agent(tmp_path)
        page = _mock_page()
        datos = {
            "tipo_cedula": "2",
            "cedula":      "3-101-12345",
            "nombre":      "EMPRESA S.A.",
        }
        agent._llenar_seccion_propietario(page, datos)
        # Verificar que se llama al locator del nombre para llenar
        sels = [c.args[0] for c in page.locator.call_args_list if c.args]
        assert "#txtnombrePropietario" in sels


# ─── Sección Contratante ─────────────────────────────────────────────────

class TestLlenarContratante:
    def test_default_marca_es_propietario(self, tmp_path):
        """Regla: contratante_es_propietario=true por default — marca el checkbox."""
        agent = _make_agent(tmp_path)
        page = _mock_page()
        agent._llenar_seccion_contratante(page, {"propietario": {}})

        sels = _selectores_locados(page)
        assert "#checkContratante" in sels
        # JS-eval debe haberse llamado con want=True (estrategia preferida del helper)
        eval_calls = page.locator.return_value.evaluate.call_args_list
        assert any(True in c.args for c in eval_calls), \
               f"esperaba evaluate(..., True); calls={eval_calls}"

    def test_explicito_false_llena_datos(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = _mock_page()
        agent._llenar_seccion_contratante(page, {
            "contratante_es_propietario": False,
            "contratante": {
                "tipo_cedula": "1",
                "cedula": "1-0001-0001",
                "nombre": "Otro",
                "correo": "x@x.com",
            },
        })
        sels = [c.args[0] for c in page.locator.call_args_list if c.args]
        assert "#txtcedulaContratante" in sels
        assert "#txtnombrecontratante" in sels


# ─── Sección Profesional ─────────────────────────────────────────────────

class TestLlenarProfesional:
    def test_marca_notificacion_profesional_siempre(self, tmp_path):
        """Regla: 'enviar notificación al profesional' siempre marcado."""
        agent = _make_agent(tmp_path)
        page = _mock_page()
        agent._llenar_seccion_profesional(page, {"correo": "topo@oficina.com"})

        sels = _selectores_locados(page)
        assert "#ChkNotificaProfesional" in sels
        # Debe haber llamado evaluate con want=True
        eval_calls = page.locator.return_value.evaluate.call_args_list
        assert any(True in c.args for c in eval_calls)

    def test_sobreescribe_correo(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = _mock_page()
        agent._llenar_seccion_profesional(page, {"correo": "topografia@hrh.com"})

        sels = [c.args[0] for c in page.locator.call_args_list if c.args]
        assert "#txtcorreoprofesional" in sels


# ─── Sección Proyecto ────────────────────────────────────────────────────

class TestLlenarProyecto:
    def test_naturaleza_default_equidad(self, tmp_path):
        """Regla: si no se especifica, naturaleza = Equidad (rbEquidad)."""
        agent = _make_agent(tmp_path)
        page = _mock_page()
        agent._llenar_seccion_proyecto(page, {
            "tipo_plano_apt": "27",
            "provincia": "2",
        })
        sels = [c.args[0] for c in page.locator.call_args_list if c.args]
        # Debe seleccionar #rbEquidad (no #rbDerecho ni #rbPericial)
        assert "#rbEquidad" in sels

    def test_firma_copia_de_ubicacion_si_no_se_da(self, tmp_path):
        """Regla: firma_provincia/canton/distrito = ubicación del terreno."""
        agent = _make_agent(tmp_path)
        page = _mock_page()
        agent._llenar_seccion_proyecto(page, {
            "tipo_plano_apt": "27",
            "provincia": "2",
            "canton": "5",
            "distrito": "10",
        })
        sels = _selectores_locados(page)
        # Tanto el dropdown del terreno como el de firma se tocan
        assert "#dllProvincia" in sels
        assert "#ddlFirmaProvincia" in sels
        assert "#ddlCanton" in sels
        assert "#ddlFirmaCanton" in sels

    def test_naturaleza_explicita_pericial(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = _mock_page()
        agent._llenar_seccion_proyecto(page, {
            "tipo_plano_apt": "27",
            "naturaleza": "3",
        })
        sels = [c.args[0] for c in page.locator.call_args_list if c.args]
        assert "#rbPericial" in sels


# ─── Sección General ─────────────────────────────────────────────────────

class TestLlenarGeneral:
    def test_interes_social_siempre_desmarcado(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = _mock_page()
        agent._llenar_seccion_general(page, {"area_predio": "1000"})

        sels = _selectores_locados(page)
        assert "#checkInteresSocial" in sels
        # JS-eval con want=False (desmarcar)
        eval_calls = page.locator.return_value.evaluate.call_args_list
        assert any(False in c.args for c in eval_calls)

    def test_notificacion_cliente_siempre_desmarcada(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = _mock_page()
        agent._llenar_seccion_general(page, {"area_predio": "1000"})

        sels = [c.args[0] for c in page.locator.call_args_list if c.args]
        assert "#ChkNotificaCliente" in sels

    def test_composicion_default_unipersonal(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = _mock_page()
        agent._llenar_seccion_general(page, {"area_predio": "1000"})

        sels = [c.args[0] for c in page.locator.call_args_list if c.args]
        assert "#ChkComposicionUnipersonal" in sels


# ─── Sección Firmas ──────────────────────────────────────────────────────

class TestLlenarFirmas:
    def test_fecha_default_es_hoy(self, tmp_path):
        from datetime import date
        agent = _make_agent(tmp_path)
        page = _mock_page()
        agent._llenar_seccion_firmas(page, {})

        # evaluate fue llamado con la fecha de hoy (nuevo código JS-first)
        hoy = date.today().isoformat()
        eval_calls = page.locator.return_value.evaluate.call_args_list
        assert any(hoy in str(c) for c in eval_calls), \
               f"Esperaba evaluate con fecha {hoy}; calls={eval_calls}"

    def test_fecha_explicita_se_respeta(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = _mock_page()
        agent._llenar_seccion_firmas(page, {"fecha": "2026-12-31"})

        eval_calls = [str(c) for c in page.locator.return_value.evaluate.call_args_list]
        assert any("2026-12-31" in c for c in eval_calls)
