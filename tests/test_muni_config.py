"""Tests del loader de configuración muni."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.utils.muni_config import (
    cargar_muni, listar_munis, muni_para_canton,
    formatear_finca, formatear_carne_topografo,
    formatear_nombre_profesional, asunto_correcciones, reset_cache,
)


@pytest.fixture(autouse=True)
def clear_cache():
    """Limpia cache antes y después de cada test."""
    reset_cache()
    yield
    reset_cache()


@pytest.fixture
def yaml_tmp(tmp_path):
    """YAML temporal con 2 munis configuradas."""
    p = tmp_path / "munis.yaml"
    p.write_text("""
san_ramon:
  nombre_legal: "Municipalidad de San Ramón"
  canton: "SAN_RAMON"
  flujo_envio: "google_form"
  google_form:
    form_id: "ABC123"
  email_correcciones: "mgamboa@sanramon.go.cr"
  asunto_corregido: "Trámite CORREGIDO APT 2016 - {tramite}"
  asunto_resello:   "Trámite RESELLO APT 2016 - {tramite}"
  distritos:
    "05 Piedades Sur": "05"
  formato_campos:
    finca:
      patron: "{provincia} {numero}-{derecho}"
      separador_varias: "/"
    carne:
      patron: "it-{numero}"
      transformar: "lowercase"
    nombre_profesional:
      orden: "nombres_primero"
      caso: "lowercase"

poas:
  nombre_legal: "Municipalidad de Poás"
  canton: "POAS"
  flujo_envio: null
""", encoding="utf-8")
    return p


# ── Carga básica ───────────────────────────────────────────────────────

class TestCargarMuni:

    def test_cargar_san_ramon(self, yaml_tmp):
        cfg = cargar_muni("san_ramon", config_path=yaml_tmp)
        assert cfg is not None
        assert cfg["nombre_legal"] == "Municipalidad de San Ramón"
        assert cfg["google_form"]["form_id"] == "ABC123"

    def test_cargar_inexistente_devuelve_None(self, yaml_tmp):
        assert cargar_muni("nicaragua", config_path=yaml_tmp) is None

    def test_listar_munis(self, yaml_tmp):
        slugs = listar_munis(config_path=yaml_tmp)
        assert set(slugs) == {"san_ramon", "poas"}

    def test_yaml_inexistente_devuelve_vacio(self, tmp_path):
        assert listar_munis(config_path=tmp_path / "no_existe.yaml") == []


# ── muni_para_canton ───────────────────────────────────────────────────

class TestMuniParaCanton:

    def test_match_exacto_slug(self, yaml_tmp):
        assert muni_para_canton("san_ramon", config_path=yaml_tmp) == "san_ramon"

    def test_normaliza_espacios(self, yaml_tmp):
        assert muni_para_canton("SAN RAMON", config_path=yaml_tmp) == "san_ramon"

    def test_normaliza_acentos(self, yaml_tmp):
        assert muni_para_canton("San Ramón", config_path=yaml_tmp) == "san_ramon"

    def test_canton_text_libre(self, yaml_tmp):
        assert muni_para_canton("SAN_RAMON", config_path=yaml_tmp) == "san_ramon"

    def test_no_existe(self, yaml_tmp):
        assert muni_para_canton("liberia", config_path=yaml_tmp) is None

    def test_vacio(self, yaml_tmp):
        assert muni_para_canton("", config_path=yaml_tmp) is None


# ── formatear_finca ────────────────────────────────────────────────────

class TestFormatearFinca:

    def test_una_finca(self, yaml_tmp):
        fincas = [{"provincia": "2", "numero": "642038", "derecho": "000"}]
        assert formatear_finca(fincas, muni="san_ramon", config_path=yaml_tmp) == "2 642038-000"

    def test_varias_fincas(self, yaml_tmp):
        fincas = [
            {"provincia": "2", "numero": "111111", "derecho": "000"},
            {"provincia": "2", "numero": "222222", "derecho": "001"},
        ]
        assert formatear_finca(fincas, muni="san_ramon", config_path=yaml_tmp) == "2 111111-000/2 222222-001"

    def test_lista_vacia(self, yaml_tmp):
        assert formatear_finca([], muni="san_ramon", config_path=yaml_tmp) == ""

    def test_finca_sin_numero_se_omite(self, yaml_tmp):
        fincas = [
            {"provincia": "2", "numero": "111", "derecho": "000"},
            {"provincia": "2", "derecho": "000"},  # sin numero
        ]
        assert formatear_finca(fincas, muni="san_ramon", config_path=yaml_tmp) == "2 111-000"


# ── formatear_carne_topografo ──────────────────────────────────────────

class TestFormatearCarne:

    def test_it_con_espacio(self, yaml_tmp):
        assert formatear_carne_topografo("IT 10676", muni="san_ramon", config_path=yaml_tmp) == "it-10676"

    def test_it_con_puntos(self, yaml_tmp):
        assert formatear_carne_topografo("I.T. 10676", muni="san_ramon", config_path=yaml_tmp) == "it-10676"

    def test_solo_numero(self, yaml_tmp):
        assert formatear_carne_topografo("10676", muni="san_ramon", config_path=yaml_tmp) == "it-10676"

    def test_vacio(self, yaml_tmp):
        assert formatear_carne_topografo("", muni="san_ramon", config_path=yaml_tmp) == ""


# ── formatear_nombre_profesional ───────────────────────────────────────

class TestFormatearNombre:

    def test_cfia_a_muni_4_palabras(self, yaml_tmp):
        # CFIA: APELLIDO1 APELLIDO2 NOMBRE1 NOMBRE2
        # Muni: nombre1 nombre2 apellido1 apellido2 (minúsculas)
        r = formatear_nombre_profesional(
            "ROJAS HERRERA LUIS ALONSO",
            muni="san_ramon", config_path=yaml_tmp,
        )
        assert r == "luis alonso rojas herrera"

    def test_3_palabras(self, yaml_tmp):
        r = formatear_nombre_profesional(
            "PEREZ GONZALEZ MARIA",
            muni="san_ramon", config_path=yaml_tmp,
        )
        assert r == "maria perez gonzalez"

    def test_vacio(self, yaml_tmp):
        assert formatear_nombre_profesional("", muni="san_ramon", config_path=yaml_tmp) == ""


# ── asunto_correcciones ────────────────────────────────────────────────

class TestAsuntoCorrecciones:

    def test_corregido(self, yaml_tmp):
        s = asunto_correcciones("corregido", "1223951",
                                muni="san_ramon", config_path=yaml_tmp)
        assert s == "Trámite CORREGIDO APT 2016 - 1223951"

    def test_resello(self, yaml_tmp):
        s = asunto_correcciones("resello", "1223951",
                                muni="san_ramon", config_path=yaml_tmp)
        assert s == "Trámite RESELLO APT 2016 - 1223951"

    def test_tipo_desconocido_fallback(self, yaml_tmp):
        s = asunto_correcciones("rechazado", "1234567",
                                muni="san_ramon", config_path=yaml_tmp)
        assert "1234567" in s

    def test_muni_inexistente_fallback(self, yaml_tmp):
        s = asunto_correcciones("corregido", "99",
                                muni="liberia", config_path=yaml_tmp)
        assert "99" in s


# ── Caso real TILMAN ───────────────────────────────────────────────────

class TestCasoTilman:

    def test_yaml_real_san_ramon_completo(self):
        """Usa el YAML real del proyecto (no temporal)."""
        from pathlib import Path
        real_yaml = Path("config/munis.yaml")
        if not real_yaml.exists():
            pytest.skip("YAML real no presente")
        cfg = cargar_muni("san_ramon", config_path=real_yaml)
        assert cfg is not None
        assert cfg["email_correcciones"] == "mgamboa@sanramon.go.cr"
        assert "05 Piedades Sur" in cfg["distritos"]
        assert cfg["distritos"]["05 Piedades Sur"] == "05"

    def test_formatear_real_tilman(self):
        """Caso TILMAN real."""
        from pathlib import Path
        real_yaml = Path("config/munis.yaml")
        if not real_yaml.exists():
            pytest.skip("YAML real no presente")
        fincas = [{"provincia": "2", "numero": "629270", "derecho": "000"}]
        assert formatear_finca(fincas, muni="san_ramon", config_path=real_yaml) == "2 629270-000"
        assert formatear_carne_topografo("IT 10676", muni="san_ramon", config_path=real_yaml) == "it-10676"
        assert formatear_nombre_profesional(
            "ROJAS HERRERA LUIS ALONSO",
            muni="san_ramon", config_path=real_yaml,
        ) == "luis alonso rojas herrera"
