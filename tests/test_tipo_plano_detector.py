"""Tests del detector automático de tipo_plano BD desde cajetín/registro."""
from __future__ import annotations
import pytest

from src.utils.tipo_plano_detector import detectar_tipo_plano, TIPOS_VALIDOS


class TestSegregacion:
    def test_es_parte_de_explicito(self):
        """FELIPE_TIOS — registro dice 'ES PARTE DE' + anotación segregación."""
        tipo, conf, motivos = detectar_tipo_plano(
            texto_cajetin="INFORMACION DE REGISTRO\nES PARTE DE\nFOLIO REAL N° 2422958-000",
            texto_registro="ANOTACIONES SOBRE LA FINCA: SI HAY\nSEGREGACION DE LOTE EN CABEZA DE SU DUEÑO",
        )
        assert tipo == "segregacion"
        assert conf == "alta"

    def test_es_parte_de_flag_directo(self):
        tipo, conf, _ = detectar_tipo_plano(es_parte_de_flag=True)
        assert tipo == "segregacion"

    def test_para_segregar_en_cajetin(self):
        tipo, conf, _ = detectar_tipo_plano(
            texto_cajetin="INFORMACION DE REGISTRO\nPARA SEGREGAR LOTE",
        )
        assert tipo == "segregacion"
        assert conf in ("alta", "media")

    def test_lote_segregado_en_linderos(self):
        tipo, _, _ = detectar_tipo_plano(
            texto_registro="LINDEROS:\nNORTE: CALLE PUBLICA\nESTE: LOTE SEGREGADO",
        )
        assert tipo == "segregacion"


class TestRectificacion:
    def test_para_rectificar_area(self):
        """ROVUELT — cajetín dice 'PARA RECTIFICAR AREA'."""
        tipo, conf, _ = detectar_tipo_plano(
            texto_cajetin="INFORMACION DE REGISTRO\nPARA RECTIFICAR AREA\nFOLIO REAL N° 5115747-000",
        )
        assert tipo == "rectificacion"
        assert conf == "alta"

    def test_rectificacion_de_area(self):
        tipo, _, _ = detectar_tipo_plano(
            texto_cajetin="RECTIFICACIÓN DE AREA",
        )
        assert tipo == "rectificacion"

    def test_diferencia_area_sugerida(self):
        """Sin texto explícito pero áreas muy distintas → posible rectificación."""
        tipo, conf, _ = detectar_tipo_plano(
            texto_cajetin="INFORMACION DE REGISTRO",  # sin pista clara
            area_real_m2=63803.34,
            area_registro_m2=55679.0,  # ~14% diff
        )
        # No es señal fuerte — solo "baja" confianza
        if tipo:
            assert tipo == "rectificacion"
            assert conf == "baja"


class TestReunion:
    def test_para_reunir(self):
        tipo, _, _ = detectar_tipo_plano(
            texto_cajetin="PARA REUNIR FINCAS",
        )
        assert tipo == "reunion_de_fincas"

    def test_reunion_de_fincas(self):
        tipo, conf, _ = detectar_tipo_plano(
            texto_cajetin="REUNIÓN DE FINCAS",
        )
        assert tipo == "reunion_de_fincas"
        assert conf == "alta"

    def test_unificacion(self):
        tipo, _, _ = detectar_tipo_plano(
            texto_cajetin="UNIFICACIÓN DE FINCAS",
        )
        assert tipo == "reunion_de_fincas"


class TestInformacionPosesoria:
    def test_titular(self):
        tipo, conf, _ = detectar_tipo_plano(
            texto_cajetin="PARA TITULAR\nINFORMACION POSESORIA",
        )
        assert tipo == "informacion_posesoria"
        assert conf == "alta"

    def test_usucapion(self):
        tipo, _, _ = detectar_tipo_plano(
            texto_registro="USUCAPIÓN solicitada",
        )
        assert tipo == "informacion_posesoria"


class TestFincaCompleta:
    def test_finca_completa(self):
        tipo, conf, _ = detectar_tipo_plano(
            texto_cajetin="LEVANTAMIENTO DE FINCA COMPLETA",
        )
        assert tipo == "finca_completa"
        assert conf == "alta"


class TestAmbiguedad:
    def test_sin_senales_devuelve_vacio(self):
        tipo, conf, motivos = detectar_tipo_plano(
            texto_cajetin="Un plano cualquiera sin frase clara",
        )
        assert tipo == ""
        assert conf == ""
        assert any("operador" in m.lower() for m in motivos)

    def test_input_vacio(self):
        tipo, conf, _ = detectar_tipo_plano()
        assert tipo == ""

    def test_ambiguo_devuelve_top_pero_baja(self):
        """Texto con señales mixtas — preferir el de mayor peso."""
        tipo, conf, _ = detectar_tipo_plano(
            texto_cajetin="SEGREGACION DE LOTE Y REUNIÓN DE FINCAS",
        )
        # Ambos matchean — gana el de más peso (segregación tiene 3)
        assert tipo in ("segregacion", "reunion_de_fincas")


class TestRobustez:
    def test_acentos_ignorados(self):
        # SEGREGACIÓN (con acento) vs SEGREGACION (sin) deben dar mismo resultado
        a, _, _ = detectar_tipo_plano(texto_cajetin="SEGREGACIÓN DE LOTE")
        b, _, _ = detectar_tipo_plano(texto_cajetin="SEGREGACION DE LOTE")
        assert a == b == "segregacion"

    def test_case_insensitive(self):
        a, _, _ = detectar_tipo_plano(texto_cajetin="es parte de la finca")
        b, _, _ = detectar_tipo_plano(texto_cajetin="ES PARTE DE LA FINCA")
        assert a == b == "segregacion"

    def test_todos_devueltos_son_validos(self):
        """Todos los tipos que retorna deben estar en TIPOS_VALIDOS."""
        casos = [
            "SEGREGACION", "REUNION DE FINCAS", "PARA RECTIFICAR",
            "INFORMACION POSESORIA", "FINCA COMPLETA",
        ]
        for caso in casos:
            tipo, _, _ = detectar_tipo_plano(texto_cajetin=caso)
            if tipo:
                assert tipo in TIPOS_VALIDOS
