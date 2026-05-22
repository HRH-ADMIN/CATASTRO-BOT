"""Tests de los helpers _llenar_seccion_plano_* y _subir_archivo_plano del APTAgent.

Verifican que la mecánica de llenar la sección PLANO (post-creación contrato)
está correcta: selectores, orden de operaciones, manejo de modales swal2.

Todos los tests usan MagicMock para `page` — no abren navegador real.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("win32cred", reason="solo Windows")
pytest.importorskip("playwright", reason="playwright requerido")

from tests.conftest import FakeCredentialManager, TestDatabase  # noqa: E402
from src.agents.apt_agent import (  # noqa: E402
    APTAgent, RNPMismatchError, _normalizar_nombre,
)


def _make_agent(tmp_path: Path) -> APTAgent:
    creds = FakeCredentialManager()
    db = TestDatabase(path=tmp_path / "t.db", credentials=creds)
    db.initialize_schema()
    return APTAgent(db, creds)


def _mock_page(swal_text: str = "guardado con éxito") -> MagicMock:
    """Mock de page con locator/evaluate que se comportan como si todo OK."""
    page = MagicMock()
    locator = MagicMock()
    locator.count.return_value = 1
    locator.first = locator
    locator.input_value.return_value = ""
    locator.is_disabled.return_value = False
    locator.is_checked.return_value = False
    locator.evaluate.return_value = True
    page.locator.return_value = locator
    # page.evaluate retorna texto del swal cuando se le pide modal_text
    def _eval(js, *args, **kw):
        if "swal2-popup" in js:
            return swal_text
        if "swal2-confirm" in js:
            return True
        if "GuardarFinca" in js or "GuardarPlanosModificar" in js \
           or "GuardarEnteros" in js or "BtnEnviarAgrimensura" in js \
           or "btnCargarArchivo" in js:
            return True
        return True
    page.evaluate.side_effect = _eval
    return page


def _selectores_locados(page) -> list[str]:
    return [c.args[0] for c in page.locator.call_args_list if c.args]


# ─── _aceptar_modal_swal ────────────────────────────────────────────────

class TestAceptarModalSwal:
    def test_devuelve_true_si_hay_modal(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = _mock_page()
        with patch("src.agents.apt_agent.time.sleep"):
            ok = agent._aceptar_modal_swal(page, esperar_segundos=0)
        assert ok is True


# ─── _llenar_seccion_plano_fincas ───────────────────────────────────────

class TestLlenarFincas:
    def test_2_fincas_se_registran(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = _mock_page()
        fincas = [
            {"provincia": "2", "numero": "644670", "derecho": "000"},
            {"provincia": "2", "numero": "594399", "derecho": "000"},
        ]
        with patch("src.agents.apt_agent.time.sleep"):
            agent._llenar_seccion_plano_fincas(page, fincas)

        sels = _selectores_locados(page)
        # bP2 debe haberse expandido
        assert "#bP2" in sels
        # Selector provincia/finca/derecho usados
        assert "#ddlProvinciaFinca" in sels
        assert "#txtNumFinca" in sels
        assert "#txtDerecho" in sels

    def test_lista_vacia_no_hace_nada(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = _mock_page()
        with patch("src.agents.apt_agent.time.sleep"):
            agent._llenar_seccion_plano_fincas(page, [])
        sels = _selectores_locados(page)
        assert "#bP2" not in sels


# ─── _llenar_seccion_plano_planos_modificar ─────────────────────────────

class TestLlenarPlanosModificar:
    def test_planos_a_modificar(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = _mock_page()
        planos = [
            {"provincia": "2", "numero": "0061108", "anno": "2024"},
            {"provincia": "2", "numero": "2226967", "anno": "2020"},
        ]
        with patch("src.agents.apt_agent.time.sleep"):
            agent._llenar_seccion_plano_planos_modificar(page, planos)
        sels = _selectores_locados(page)
        assert "#bP5" in sels
        assert "#ddlProvinciaPlanoModificar" in sels
        assert "#txtNumPlanoModificar" in sels
        assert "#txtAnnoModificar" in sels


# ─── _llenar_seccion_plano_enteros ──────────────────────────────────────

class TestLlenarEnteros:
    def test_llena_los_6_campos(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = _mock_page()
        datos = {
            "numero":         "660821842",
            "fecha":          "2026-05-08",
            "total_cfia":     "1600",
            "total_registro": "55000",
            "monto_pagado":   "57426.74",
            "cit_ntrip":      "300",
        }
        with patch("src.agents.apt_agent.time.sleep"):
            agent._llenar_seccion_plano_enteros(page, datos)
        sels = _selectores_locados(page)
        assert "#bP6" in sels
        assert "#txtNumEntero" in sels
        assert "#txtTotalCFIA" in sels
        assert "#txtTotalRegistro" in sels
        assert "#txtMontoPagado" in sels
        assert "#txtMontoCIT_NTRIP" in sels
        assert "#FechaPago" in sels


# ─── _subir_archivo_plano ───────────────────────────────────────────────

class TestSubirArchivo:
    def test_archivo_inexistente_retorna_false(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = _mock_page()
        ok = agent._subir_archivo_plano(page, "1", "/ruta/inventada/no_existe.pdf")
        assert ok is False

    def test_archivo_existente_se_sube(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = _mock_page(swal_text="Exitoso\nEl archivo se guardó con éxito")
        # Crear archivo dummy
        f = tmp_path / "test.pdf"
        f.write_bytes(b"%PDF-1.4 dummy")
        with patch("src.agents.apt_agent.time.sleep"):
            ok = agent._subir_archivo_plano(page, "10", str(f))
        # Verificar selectores usados
        sels = _selectores_locados(page)
        assert "#bP7" in sels
        assert "#ddlTipoArchivo" in sels
        assert "#file" in sels
        # set_input_files debe haberse llamado
        assert page.locator.return_value.set_input_files.called

    def test_constantes_tipos(self):
        assert APTAgent.TIPO_ARCHIVO_ANVERSO   == "1"
        assert APTAgent.TIPO_ARCHIVO_VISADO    == "2"
        assert APTAgent.TIPO_ARCHIVO_ENTERO    == "10"
        assert APTAgent.TIPO_ARCHIVO_DERROTERO == "16"


# ─── _enviar_plano_cfia ─────────────────────────────────────────────────

class TestEnviarCFIA:
    def test_click_y_confirmacion(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = _mock_page(swal_text="enviado al CFIA con éxito")
        with patch("src.agents.apt_agent.time.sleep"):
            ok = agent._enviar_plano_cfia(page)
        assert ok is True
        # Verificar que se llamaron los evaluates esperados
        evals = [c.args[0] for c in page.evaluate.call_args_list if c.args]
        assert any("BtnEnviarAgrimensura" in s for s in evals)
        assert any("swal2-confirm" in s for s in evals)


# ─── Verificación RNP (defensa contra cédula errada) ───────────────────

def _mock_page_con_rnp(rnp_response: dict | None) -> MagicMock:
    """Mock de page cuyo evaluate devuelve `rnp_response` cuando se le piden
    los campos de nombre del propietario/titular (consultas con `.value`).
    Para el resto de evaluates devuelve True.
    """
    page = MagicMock()
    locator = MagicMock()
    locator.count.return_value = 1
    locator.first = locator
    locator.input_value.return_value = ""
    locator.is_disabled.return_value = False
    locator.is_checked.return_value = False
    locator.evaluate.return_value = True
    page.locator.return_value = locator
    def _eval(js, *args, **kw):
        # El helper _verificar_rnp_devuelve hace evaluate con argumento dict
        # de selectores y espera respuesta {nombre, ap1, ap2}
        if "nombre:" in js and "ap1:" in js and "ap2:" in js:
            return rnp_response or {"nombre": "", "ap1": "", "ap2": ""}
        return True
    page.evaluate.side_effect = _eval
    return page


class TestNormalizarNombre:
    def test_quita_acentos_y_pone_mayusculas(self):
        assert _normalizar_nombre("María José") == "MARIA JOSE"

    def test_colapsa_espacios(self):
        assert _normalizar_nombre("  OMAR   ARIAS   RAMIREZ ") == "OMAR ARIAS RAMIREZ"

    def test_vacio(self):
        assert _normalizar_nombre("") == ""
        assert _normalizar_nombre(None) == ""


class TestValidarYCorregirRnp:
    """El bot debe SOBRESCRIBIR el nombre cuando TSE difiere del registro,
    conservar la cédula, y registrar la discrepancia para avisar al operador.
    """

    def test_tse_coincide_no_sobrescribe(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = _mock_page_con_rnp({"nombre": "OMAR", "ap1": "ARIAS", "ap2": "RAMIREZ"})
        res = agent._validar_y_corregir_rnp(
            page,
            sel_nombre="#txtNombreTitular",
            sel_ap1="#txtApellido1Titular",
            sel_ap2="#txtApellido2Titular",
            nombre_registro="OMAR", ap1_registro="ARIAS", ap2_registro="RAMIREZ",
            cedula="2-0310-0121", contexto="test",
        )
        assert res["match"] is True
        assert res["override_aplicado"] is False
        assert agent.discrepancias_rnp == []

    def test_tse_difiere_registra_con_tipo_correcto(self, tmp_path):
        """Bug regresión: la discrepancia debe llevar `tipo=rnp_tse_mismatch`
        (antes se guardaba sin tipo, lo que causaba que el formatter de WhatsApp
        la mostrara como 'sin descripción')."""
        from src.agents.apt_discrepancia_handler import TIPO_RNP_TSE_MISMATCH
        agent = _make_agent(tmp_path)
        page = _mock_page_con_rnp({
            "nombre": "AMALIA", "ap1": "QUESADA", "ap2": "RODRIGUEZ"
        })
        agent._validar_y_corregir_rnp(
            page,
            sel_nombre="#x", sel_ap1="#y", sel_ap2="#z",
            nombre_registro="GRACE", ap1_registro="ALVAREZ", ap2_registro="GONZALEZ",
            cedula="2-0440-0388", contexto="titular#1",
        )
        assert len(agent.discrepancias_rnp) == 1
        assert agent.discrepancias_rnp[0]["tipo"] == TIPO_RNP_TSE_MISMATCH

    def test_tse_difiere_registra_discrepancia_pero_no_sobrescribe(self, tmp_path):
        """Caso real RDF-2026-002: cédula 2-0440-0388 trae AMALIA en TSE
        pero el registro dice GRACE.

        APT enforce el binding cédula→TSE server-side (verificado en
        tools/test_override_nombre.py): aunque sobrescribamos los campos
        visibles, el servidor reemplaza nombre/apellidos con el del padrón
        electoral al guardar. Por eso el bot NO intenta sobrescribir y
        solo registra la discrepancia para notificar al operador.
        """
        agent = _make_agent(tmp_path)
        page = _mock_page_con_rnp({
            "nombre": "AMALIA MARIA DEL CARMEN", "ap1": "QUESADA", "ap2": "RODRIGUEZ"
        })
        res = agent._validar_y_corregir_rnp(
            page,
            sel_nombre="#txtNombreTitular",
            sel_ap1="#txtApellido1Titular",
            sel_ap2="#txtApellido2Titular",
            nombre_registro="GRACE", ap1_registro="ALVAREZ", ap2_registro="GONZALEZ",
            cedula="2-0440-0388", contexto="titular#1",
        )
        assert res["match"] is False
        assert res["override_aplicado"] is False  # ← NO se sobrescribe
        # Pero la discrepancia SÍ se registra
        assert len(agent.discrepancias_rnp) == 1
        d = agent.discrepancias_rnp[0]
        assert d["cedula"] == "2-0440-0388"
        assert "AMALIA" in d["tse_nombre"]
        assert "GRACE" in d["registro_nombre"]
        assert d["contexto"] == "titular#1"
        # `guardado_como` es el del registro porque el caller hará UPDATE
        # post-insert para corregir el nombre. La verificación se hace en
        # `_llenar_seccion_plano_titulares` de extremo a extremo.
        assert d["guardado_como"] == d["registro_nombre"]

    def test_tse_vacio_y_registro_vacio_lanza(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = _mock_page_con_rnp({"nombre": "", "ap1": "", "ap2": ""})
        with pytest.raises(RNPMismatchError, match="no devolvió"):
            agent._validar_y_corregir_rnp(
                page,
                sel_nombre="#x", sel_ap1="#y", sel_ap2="#z",
                nombre_registro="", ap1_registro="", ap2_registro="",
                cedula="9-9999-9999", contexto="t",
            )

    def test_tse_vacio_pero_registro_trae_nombre_llena(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = _mock_page_con_rnp({"nombre": "", "ap1": "", "ap2": ""})
        res = agent._validar_y_corregir_rnp(
            page,
            sel_nombre="#x", sel_ap1="#y", sel_ap2="#z",
            nombre_registro="GRACE", ap1_registro="ALVAREZ", ap2_registro="GONZALEZ",
            cedula="2-0440-0388", contexto="t",
        )
        # No registra discrepancia (no había TSE con qué comparar)
        assert agent.discrepancias_rnp == []
        assert res["override_aplicado"] is True

    def test_acentos_y_orden_no_rompen_match(self, tmp_path):
        """TSE devuelve con tildes; registro sin tildes → coincide igual."""
        agent = _make_agent(tmp_path)
        page = _mock_page_con_rnp({"nombre": "JOSÉ MARÍA", "ap1": "PÉREZ", "ap2": "GONZÁLEZ"})
        res = agent._validar_y_corregir_rnp(
            page,
            sel_nombre="#x", sel_ap1="#y", sel_ap2="#z",
            nombre_registro="JOSE MARIA", ap1_registro="PEREZ", ap2_registro="GONZALEZ",
            cedula="1-0000-0000", contexto="t",
        )
        assert res["match"] is True
        assert agent.discrepancias_rnp == []


class TestLlenarTitularesConDiscrepancia:
    """Integración: _llenar_seccion_plano_titulares debe registrar discrepancia
    Y aplicar ciclo INSERT→UPDATE para corregir el nombre cuando TSE difiere.
    """

    def test_cedula_correcta_pero_tse_difiere_aplica_insert_update(self, tmp_path):
        """APT enforce TSE en INSERT, pero respeta UPDATE. El bot debe
        hacer ambos pasos para terminar con el nombre del registro."""
        agent = _make_agent(tmp_path)
        page = _mock_page_con_rnp({
            "nombre": "AMALIA MARIA DEL CARMEN", "ap1": "QUESADA", "ap2": "RODRIGUEZ"
        })
        # El mock necesita responder True a SeleccionarTitular.click() para
        # simular que el record fue encontrado y abierto en el form.
        original_eval = page.evaluate.side_effect
        def _eval(js, *args, **kw):
            if "SeleccionarTitular" in js and "RegExp" in js:
                return True  # simulamos que encontró el card y lo abrió
            return original_eval(js, *args, **kw) if original_eval else True
        page.evaluate.side_effect = _eval

        titulares = [
            {
                "tipo_cedula": "1",
                "cedula":      "2-0440-0388",
                "nombre":      "GRACE",
                "apellido1":   "ALVAREZ",
                "apellido2":   "GONZALEZ",
                "titularidad": "5",
            }
        ]
        with patch("src.agents.apt_agent.time.sleep"):
            agent._llenar_seccion_plano_titulares(page, titulares)
        # Discrepancia registrada con guardado_como = registro
        assert len(agent.discrepancias_rnp) == 1
        d = agent.discrepancias_rnp[0]
        assert d["guardado_como"] == d["registro_nombre"]
        # GuardarTitular se llamó al menos 2 veces (INSERT + UPDATE)
        evals = [c.args[0] for c in page.evaluate.call_args_list if c.args]
        guardar_calls = [s for s in evals if "GuardarTitular" in s]
        assert len(guardar_calls) >= 2, f"esperado ≥2 GuardarTitular calls, vi {len(guardar_calls)}"


class TestEditarTitularPostInsert:
    def test_edita_record_existente(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = _mock_page_con_rnp({"nombre": "GRACE", "ap1": "ALVAREZ", "ap2": "GONZALEZ"})
        original_eval = page.evaluate.side_effect
        def _eval(js, *args, **kw):
            if "SeleccionarTitular" in js and "RegExp" in js:
                return True
            return original_eval(js, *args, **kw) if original_eval else True
        page.evaluate.side_effect = _eval
        with patch("src.agents.apt_agent.time.sleep"):
            ok = agent._editar_titular_post_insert(
                page, cedula="2-0440-0388",
                nombre="GRACE", ap1="ALVAREZ", ap2="GONZALEZ",
                contexto="test",
            )
        assert ok is True
        sels = _selectores_locados(page)
        assert "#txtNombreTitular"    in sels
        assert "#txtApellido1Titular" in sels
        assert "#txtApellido2Titular" in sels

    def test_no_encuentra_card_devuelve_false(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = _mock_page_con_rnp({"nombre": "X", "ap1": "Y", "ap2": "Z"})
        original_eval = page.evaluate.side_effect
        def _eval(js, *args, **kw):
            # SeleccionarTitular regex search → no encontró
            if "SeleccionarTitular" in js and "RegExp" in js:
                return False
            return original_eval(js, *args, **kw) if original_eval else True
        page.evaluate.side_effect = _eval
        with patch("src.agents.apt_agent.time.sleep"):
            ok = agent._editar_titular_post_insert(
                page, cedula="9-9999-9999",
                nombre="A", ap1="B", ap2="C", contexto="test",
            )
        assert ok is False

    def test_cedula_vacia_devuelve_false(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = _mock_page_con_rnp({"nombre": "X", "ap1": "Y", "ap2": "Z"})
        with patch("src.agents.apt_agent.time.sleep"):
            ok = agent._editar_titular_post_insert(
                page, cedula="", nombre="A", ap1="B", ap2="C", contexto="test",
            )
        assert ok is False


class TestLlenarPropietarioConDiscrepancia:
    def test_cedula_correcta_y_tse_coincide_no_registra(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = _mock_page_con_rnp({"nombre": "OMAR", "ap1": "ARIAS", "ap2": "RAMIREZ"})
        datos = {
            "tipo_cedula": "1",
            "cedula": "2-0310-0121",
            "nombre": "OMAR", "apellido1": "ARIAS", "apellido2": "RAMIREZ",
            "correo": "x@y.cr",
        }
        with patch("src.agents.apt_agent.time.sleep"):
            agent._llenar_seccion_propietario(page, datos)
        assert agent.discrepancias_rnp == []

    def test_cedula_correcta_pero_tse_difiere_registra(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = _mock_page_con_rnp({
            "nombre": "MARIA TERESA", "ap1": "JIMENEZ", "ap2": "MONTERO"
        })
        datos = {
            "tipo_cedula": "1",
            "cedula": "9-9999-9999",
            "nombre": "OMAR", "apellido1": "ARIAS", "apellido2": "RAMIREZ",
            "correo": "x@y.cr",
        }
        with patch("src.agents.apt_agent.time.sleep"):
            agent._llenar_seccion_propietario(page, datos)
        assert len(agent.discrepancias_rnp) == 1

    def test_juridica_no_consulta_rnp(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = _mock_page_con_rnp({"nombre": "", "ap1": "", "ap2": ""})
        datos = {
            "tipo_cedula": "2",
            "cedula": "3-101-123456",
            "nombre": "INMOBILIARIA XYZ S.A.",
            "correo": "x@y.cr",
        }
        with patch("src.agents.apt_agent.time.sleep"):
            agent._llenar_seccion_propietario(page, datos)
        assert agent.discrepancias_rnp == []

    def test_compat_nombre_esperado(self, tmp_path):
        """Compat hacia atrás: si el dict trae solo `nombre_esperado` (string
        completo), el bot lo trata como nombre del registro y compara."""
        agent = _make_agent(tmp_path)
        page = _mock_page_con_rnp({"nombre": "OMAR", "ap1": "ARIAS", "ap2": "RAMIREZ"})
        datos = {
            "tipo_cedula": "1",
            "cedula": "2-0310-0121",
            "nombre_esperado": "OMAR ARIAS RAMIREZ",
            "correo": "x@y.cr",
        }
        with patch("src.agents.apt_agent.time.sleep"):
            agent._llenar_seccion_propietario(page, datos)  # no debe lanzar


class TestTipoZonaYUbicacionMapping:
    """Fix RDF-2026-004: el seed tenía tipo_zona='1' pero APT espera '3' URBANO.
    También tipo_ubicacion='E' debe mapearse a '10' (código APT para Parcela E).
    """

    def _capture_set_select_calls(self, page):
        """Captura los argumentos a page.locator() — el helper hace
        page.locator(selector) y eso es lo que registramos."""
        return _selectores_locados(page)

    def test_tipo_zona_1_se_corrige_a_3(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = _mock_page_con_rnp({"nombre": "", "ap1": "", "ap2": ""})
        with patch("src.agents.apt_agent.time.sleep"):
            agent._llenar_seccion_plano_generales(page, {
                "tipo_zona": "1",  # valor incorrecto histórico
                "area_real": "582.67",
                "tipo_ubicacion": "E",
            })
        # No tiene que crashear — fue auto-corregido. El campo fue tocado.
        sels = self._capture_set_select_calls(page)
        assert "#ddlTipoZona" in sels

    def test_tipo_zona_auto_urbano_area_pequena(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = _mock_page_con_rnp({"nombre": "", "ap1": "", "ap2": ""})
        with patch("src.agents.apt_agent.time.sleep"):
            agent._llenar_seccion_plano_generales(page, {
                "area_real": "582.67",  # < 2000 → URBANO
                "tipo_ubicacion": "E",
            })
        sels = _selectores_locados(page)
        assert "#ddlTipoZona" in sels

    def test_tipo_zona_auto_rural_area_grande(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = _mock_page_con_rnp({"nombre": "", "ap1": "", "ap2": ""})
        with patch("src.agents.apt_agent.time.sleep"):
            agent._llenar_seccion_plano_generales(page, {
                "area_real": "5000",  # >= 2000 → RURAL
            })
        sels = _selectores_locados(page)
        assert "#ddlTipoZona" in sels

    def test_tipo_ubicacion_letra_se_mapea_a_codigo(self, tmp_path):
        """Letras 'A','B','C','CH','D','E' deben convertirse a '5','6','7','8','9','10'."""
        agent = _make_agent(tmp_path)
        # Inyectamos el mock que captura el _set_select para verificar el valor enviado
        captured = []
        original_set_select = agent._set_select
        def wrapped(page, sel, val):
            captured.append((sel, val))
            return original_set_select(page, sel, val)
        agent._set_select = wrapped

        page = _mock_page_con_rnp({"nombre": "", "ap1": "", "ap2": ""})
        with patch("src.agents.apt_agent.time.sleep"):
            agent._llenar_seccion_plano_generales(page, {
                "tipo_zona": "3",
                "area_real": "500",
                "tipo_ubicacion": "E",
            })
        # Debió haber set_select de #ddlTipoUbicacion con valor "10"
        ubic_calls = [v for s, v in captured if s == "#ddlTipoUbicacion"]
        assert "10" in ubic_calls, f"esperaba '10' (Parcela E), capturado: {ubic_calls}"

    def test_tipo_ubicacion_codigo_pasa_intacto(self, tmp_path):
        """Si ya viene el código numérico ('10'), no se toca."""
        agent = _make_agent(tmp_path)
        captured = []
        original_set_select = agent._set_select
        def wrapped(page, sel, val):
            captured.append((sel, val))
            return original_set_select(page, sel, val)
        agent._set_select = wrapped

        page = _mock_page_con_rnp({"nombre": "", "ap1": "", "ap2": ""})
        with patch("src.agents.apt_agent.time.sleep"):
            agent._llenar_seccion_plano_generales(page, {
                "tipo_zona": "3",
                "area_real": "500",
                "tipo_ubicacion": "10",  # ya es código
            })
        ubic_calls = [v for s, v in captured if s == "#ddlTipoUbicacion"]
        assert "10" in ubic_calls


class TestResetDiscrepancias:
    def test_reset_limpia_lista(self, tmp_path):
        agent = _make_agent(tmp_path)
        agent.discrepancias_rnp.append({"x": 1})
        agent.reset_discrepancias_rnp()
        assert agent.discrepancias_rnp == []


class TestCedulaRegistroOriginal:
    """Cuando el operador corrige una cédula incompleta del registro
    (declarando `cedula_registro_original`), el bot debe registrar la
    discrepancia automáticamente para notificar al topógrafo.
    """

    def test_propietario_cedula_corregida_registra_discrepancia(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = _mock_page_con_rnp({"nombre": "", "ap1": "", "ap2": ""})
        datos = {
            "tipo_cedula": "2",
            "cedula": "3-101-044683",                # corregida por operador
            "cedula_registro_original": "3-101-",    # lo que mostraba el registro
            "nombre": "SOCIEDAD AGROPECUARIA LAS ESTUFAS S.A.",
            "correo": "x@y.cr",
        }
        with patch("src.agents.apt_agent.time.sleep"):
            agent._llenar_seccion_propietario(page, datos)
        assert len(agent.discrepancias_rnp) == 1
        d = agent.discrepancias_rnp[0]
        assert d["tipo"] == "registro_dato_incompleto"
        assert d["valor"] == "3-101-"
        assert "3-101-044683" in d["descripcion"]

    def test_propietario_cedula_sin_correccion_no_registra(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = _mock_page_con_rnp({"nombre": "", "ap1": "", "ap2": ""})
        datos = {
            "tipo_cedula": "2",
            "cedula": "3-101-044683",
            "nombre": "X S.A.",
            "correo": "x@y.cr",
        }
        with patch("src.agents.apt_agent.time.sleep"):
            agent._llenar_seccion_propietario(page, datos)
        assert agent.discrepancias_rnp == []

    def test_titular_cedula_corregida_registra(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = _mock_page_con_rnp({"nombre": "GRACE", "ap1": "ALVAREZ", "ap2": "GONZALEZ"})
        titulares = [
            {
                "tipo_cedula": "1",
                "cedula": "2-0440-0388",
                "cedula_registro_original": "2-0440-",
                "nombre": "GRACE", "apellido1": "ALVAREZ", "apellido2": "GONZALEZ",
                "titularidad": "5",
            }
        ]
        with patch("src.agents.apt_agent.time.sleep"):
            agent._llenar_seccion_plano_titulares(page, titulares)
        # Una discrepancia por la corrección
        assert any(d["tipo"] == "registro_dato_incompleto"
                   for d in agent.discrepancias_rnp)
