"""Tests del selector resilience — fallback chain cuando APT cambia IDs."""
from __future__ import annotations
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("win32cred", reason="solo Windows")
pytest.importorskip("playwright", reason="playwright requerido")

from tests.conftest import FakeCredentialManager, TestDatabase  # noqa: E402
from src.agents.apt_agent import APTAgent, FALLBACKS_CAMPOS_APT  # noqa: E402


def _make_agent(tmp_path):
    creds = FakeCredentialManager()
    db = TestDatabase(path=tmp_path / "t.db", credentials=creds)
    db.initialize_schema()
    return APTAgent(db, creds)


def _make_page_with_locators(found_for: list[str]):
    """Mock page que pretende que cierto subset de selectores tiene match.

    Args:
        found_for: lista de selectores que devolverán count() == 1.
            Los demás devolverán count() == 0.
    """
    page = MagicMock()

    def _locator(sel):
        loc = MagicMock()
        loc.first = loc
        if sel in found_for:
            loc.count.return_value = 1
        else:
            loc.count.return_value = 0
        loc.is_disabled.return_value = False
        loc.is_checked.return_value = False
        loc.input_value.return_value = ""
        loc.evaluate.return_value = True
        return loc

    page.locator.side_effect = _locator
    page.evaluate.return_value = True
    return page


class TestLocalizarResilient:
    def test_devuelve_primer_match(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = _make_page_with_locators(["[name='Propietario.Cedula']"])
        elem, sel = agent._localizar_resilient(
            page,
            "#txtcedulaPropietario",
            "[name='Propietario.Cedula']",
        )
        assert elem is not None
        assert sel == "[name='Propietario.Cedula']"

    def test_primer_selector_si_match(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = _make_page_with_locators([
            "#txtcedulaPropietario",
            "[name='Propietario.Cedula']",
        ])
        elem, sel = agent._localizar_resilient(
            page, "#txtcedulaPropietario", "[name='Propietario.Cedula']",
        )
        # Devuelve el primero que matchea
        assert sel == "#txtcedulaPropietario"

    def test_todos_fallan(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = _make_page_with_locators([])
        elem, sel = agent._localizar_resilient(page, "#a", "#b", "#c")
        assert elem is None
        assert sel == ""

    def test_skip_vacios(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = _make_page_with_locators(["#existe"])
        elem, sel = agent._localizar_resilient(page, "", None, "#existe")
        assert sel == "#existe"


class TestSetInputConFallbacks:
    def test_id_primario_funciona_no_usa_fallback(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = _make_page_with_locators(["#txtcedulaPropietario"])
        with patch("src.agents.apt_agent.time.sleep"):
            ok = agent._set_input(page, "#txtcedulaPropietario", "2-0466-0095")
        assert ok is True

    def test_id_falla_pero_fallback_explicito_funciona(self, tmp_path):
        agent = _make_agent(tmp_path)
        # ID principal no existe, pero el fallback explícito sí
        page = _make_page_with_locators(["[name='Propietario.Cedula']"])
        with patch("src.agents.apt_agent.time.sleep"):
            ok = agent._set_input(
                page, "#id_inventado_que_no_existe", "2-0466-0095",
                fallbacks=["[name='Propietario.Cedula']"],
            )
        assert ok is True

    def test_id_falla_y_catalogo_resuelve(self, tmp_path):
        """Si el ID está en FALLBACKS_CAMPOS_APT, lo usa automáticamente."""
        agent = _make_agent(tmp_path)
        # ID conocido (en catálogo) no existe, pero su fallback por name sí
        page = _make_page_with_locators(["[name='Propietario.Cedula']"])
        with patch("src.agents.apt_agent.time.sleep"):
            ok = agent._set_input(page, "#txtcedulaPropietario", "2-0466-0095")
        assert ok is True

    def test_todo_falla(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = _make_page_with_locators([])
        with patch("src.agents.apt_agent.time.sleep"):
            ok = agent._set_input(page, "#no_existe", "x")
        assert ok is False


class TestSetSelectConFallbacks:
    def test_catalogo_resuelve_canton(self, tmp_path):
        """Si #ddlCanton no existe pero [name='General.CantonUbicacion'] sí."""
        agent = _make_agent(tmp_path)
        page = _make_page_with_locators(["[name='General.CantonUbicacion']"])
        with patch("src.agents.apt_agent.time.sleep"):
            ok = agent._set_select(page, "#ddlCanton", "02")
        assert ok is True

    def test_value_vacio_no_intenta(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = _make_page_with_locators([])
        with patch("src.agents.apt_agent.time.sleep"):
            ok = agent._set_select(page, "#ddlCanton", "")
        assert ok is False


class TestFallbacksCampo:
    def test_dict_cubre_campos_criticos(self):
        """Verifica que los campos más usados tienen fallback en el catálogo."""
        criticos = [
            "#txtcedulaPropietario",
            "#dllprotocolo",
            "#ddlCanton",
            "#ddlDistrito",
            "#ddlTipoZona",
            "#ddlTipoUbicacion",
            "#ddlTipoUso",
            "#ddlProvinciaFinca",
            "#txtNumFinca",
            "#txtNumPlanoModificar",
            "#txtNumEntero",
        ]
        for sel in criticos:
            assert sel in FALLBACKS_CAMPOS_APT, f"falta fallback para {sel}"
            assert len(FALLBACKS_CAMPOS_APT[sel]) > 0
