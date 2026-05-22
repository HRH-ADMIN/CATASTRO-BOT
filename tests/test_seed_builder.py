"""Tests del seed_builder — verifica que las reglas de oficina se aplican
correctamente y los campos derivados (centroide, honorarios, códigos de
ubicación) se calculan bien.
"""
from __future__ import annotations
import pytest

from src.utils.plano_vision_extractor import CajetinData, RegistroData, EnteroData
from src.utils.seed_builder import build_datos_apt, _parsear_finca, _parsear_plano_previo
from src.utils.ubicacion_cr import (
    codigo_provincia, codigo_canton, codigo_distrito, resolver_ubicacion,
)


class TestUbicacionCR:
    def test_provincia_alajuela(self):
        assert codigo_provincia("ALAJUELA") == "2"
        assert codigo_provincia("alajuela") == "2"  # case-insensitive

    def test_provincia_con_acento(self):
        assert codigo_provincia("San José") == "1"
        assert codigo_provincia("LIMÓN") == "7"

    def test_canton_san_ramon_zero_padded(self):
        assert codigo_canton("ALAJUELA", "SAN RAMON") == "02"
        assert codigo_canton("ALAJUELA", "San Ramón") == "02"
        assert codigo_canton("ALAJUELA", "SAN_RAMON") == "02"

    def test_canton_rio_cuarto(self):
        assert codigo_canton("ALAJUELA", "RIO CUARTO") == "16"

    def test_distrito_alfaro(self):
        assert codigo_distrito("ALAJUELA", "SAN RAMON", "ALFARO") == "09"

    def test_distrito_san_isidro(self):
        assert codigo_distrito("ALAJUELA", "SAN RAMON", "SAN ISIDRO") == "07"

    def test_distrito_santa_isabel(self):
        assert codigo_distrito("ALAJUELA", "RIO CUARTO", "SANTA ISABEL") == "03"

    def test_resolver_completo(self):
        r = resolver_ubicacion("ALAJUELA", "SAN RAMON", "ALFARO")
        assert r["provincia"] == "2"
        assert r["canton"] == "02"
        assert r["distrito"] == "09"
        assert r["incompleto"] is False

    def test_resolver_incompleto_flag(self):
        r = resolver_ubicacion("ALAJUELA", "CIUDAD INVENTADA", "X")
        assert r["incompleto"] is True
        assert r["provincia"] == "2"
        assert r["canton"] == ""
        assert r["distrito"] == ""


class TestParsearFinca:
    def test_folio_real_7_digitos_con_provincia(self):
        # FELIPE_TIOS: cajetín "2422958-000" → finca 422958, derecho 000
        numero, derecho = _parsear_finca("2422958-000")
        assert numero == "422958"
        assert derecho == "000"

    def test_folio_real_sin_provincia(self):
        # OMAR: registro mostraba "605596" sin el primer dígito de provincia
        numero, derecho = _parsear_finca("605596")
        assert numero == "605596"
        assert derecho == "000"

    def test_folio_real_con_derecho_distinto(self):
        numero, derecho = _parsear_finca("2422958-002")
        assert numero == "422958"
        assert derecho == "002"


class TestParsearPlanoPrevio:
    def test_a_1095215_2006(self):
        """FELIPE_TIOS — A = ALAJUELA = provincia 2."""
        r = _parsear_plano_previo("A-1095215-2006")
        assert r == {"provincia": "2", "numero": "1095215", "anno": "2006"}

    def test_san_jose(self):
        r = _parsear_plano_previo("S-0123456-2020")
        assert r["provincia"] == "1"

    def test_invalido_devuelve_none(self):
        assert _parsear_plano_previo("XYZ") is None
        assert _parsear_plano_previo("") is None


class TestBuildDatosApt:
    """Test end-to-end con datos simulados de FELIPE_TIOS."""

    def _make_caja(self):
        return CajetinData(
            descripcion="FELIPETIOS(4)",
            protocolo_tomo="23549",
            protocolo_folio="178",
            numero_entero="660822113",
            area_real="582.67",
            area_registro="741.68",
            folio_real="2422958-000",
            provincia_nombre="ALAJUELA",
            canton_nombre="SAN RAMON",
            distrito_nombre="ALFARO",
            fecha="MAYO / 2026",
            profesional_carne="IT10676",
            profesional_nombre="LUIS ALONSO ROJAS HERRERA",
            planos_modificar=[{"letra": "A", "numero": "1095215", "anno": "2006"}],
            coordenadas=[
                [445579.27, 1114039.40], [445588.57, 1114036.72],
                [445581.79, 1114022.64], [445570.22, 1113998.74],
                [445563.04, 1114002.01], [445556.67, 1114005.31],
                [445564.70, 1114025.27], [445577.79, 1114036.00],
            ],
        )

    def _make_reg(self):
        return RegistroData(
            finca="422958", derecho="000", duplicado="HORIZONTAL",
            provincia_finca="ALAJUELA", canton_finca="SAN RAMON",
            distrito_finca="ALFARO",
            tipo_propietario="FISICA",
            cedula_propietario="2-0466-0095",
            nombre_propietario="LUIS EMILIO DE LOS ANGELES",
            apellido1_propietario="PIÑEIRO",
            apellido2_propietario="CASTRO",
            naturaleza="TERRENO DE CAFE",
            area_registro_m2="741.68",
            plano_previo="A-1095215-2006",
            es_parte_de=True,
            anotaciones=True, gravamenes=True,
        )

    def _make_ent(self):
        return EnteroData(
            numero="660822113", fecha="2026-05-08",
            monto_tasado="11940.00", monto_pagado="11241.60",
            timbre_cfia="1600", timbre_registro="10000",
            timbre_cit_ntrip="300",
        )

    def test_resultado_completo(self):
        res = build_datos_apt(
            cajetin=self._make_caja(),
            registro=self._make_reg(),
            entero=self._make_ent(),
        )
        d = res["datos_apt"]
        # Ubicación
        assert d["proyecto"]["provincia"] == "2"
        assert d["proyecto"]["canton"] == "02"
        assert d["proyecto"]["distrito"] == "09"
        # Propietario
        assert d["propietario"]["tipo_cedula"] == "1"  # FISICA
        assert d["propietario"]["cedula"] == "2-0466-0095"
        assert d["propietario"]["apellido1"] == "PIÑEIRO"
        # Finca
        assert d["plano"]["fincas"][0]["numero"] == "422958"
        # Zona urbana porque area < 2000
        assert d["plano"]["tipo_zona"] == "3"
        assert d["plano"]["tipo_ubicacion"] == "10"  # Parcela E
        # Tipo uso: café → CULTIVOS_VARIOS (23)
        assert d["plano"]["tipo_uso"] == "23"
        # Centroide computed
        assert d["plano"]["norte"] != ""
        assert d["plano"]["este"] != ""
        assert d["plano"]["vertices"] == "8"
        # Plano modificar: A-1095215-2006 → provincia 2
        assert d["plano"]["planos_modificar"] == [
            {"provincia": "2", "numero": "1095215", "anno": "2006"}
        ]
        # Entero
        assert d["plano"]["entero"]["numero"] == "660822113"
        assert d["plano"]["entero"]["fecha"] == "2026-05-08"
        assert d["plano"]["entero"]["total_cfia"] == "1600"
        # Honorarios computed for urbana E
        assert d["general"]["honorarios"]
        assert int(d["general"]["honorarios"]) > 0

    def test_es_parte_de_advierte_segregacion(self):
        res = build_datos_apt(
            cajetin=self._make_caja(),
            registro=self._make_reg(),
            entero=self._make_ent(),
        )
        # Debe haber advertencia de SEGREGACIÓN
        assert any("SEGREGACIÓN" in a for a in res["advertencias"])

    def test_cedula_truncada_advertencia(self):
        reg = self._make_reg()
        reg.cedula_registro_original = "3-101-"
        reg.cedula_propietario = "3-101-044683"
        reg.tipo_propietario = "JURIDICA"
        res = build_datos_apt(
            cajetin=self._make_caja(), registro=reg, entero=self._make_ent(),
        )
        assert res["datos_apt"]["propietario"]["cedula_registro_original"] == "3-101-"
        assert any("truncada" in a.lower() for a in res["advertencias"])

    def test_area_rural(self):
        """RDF-2026-003 ROVUELT: area 63803 m² → rural."""
        caja = self._make_caja()
        caja.area_real = "63803.34"
        res = build_datos_apt(
            cajetin=caja, registro=self._make_reg(), entero=self._make_ent(),
        )
        d = res["datos_apt"]
        assert d["plano"]["tipo_zona"] == "2"  # RURAL
        assert d["plano"]["tipo_ubicacion"] == ""  # rural no aplica

    def test_ubicacion_no_mapeada_advierte(self):
        caja = self._make_caja()
        caja.distrito_nombre = "DISTRITO INVENTADO"
        reg = self._make_reg()
        reg.distrito_finca = "DISTRITO INVENTADO"
        res = build_datos_apt(cajetin=caja, registro=reg, entero=self._make_ent())
        assert any("Ubicación no mapeada" in a for a in res["advertencias"])
        assert res["datos_apt"]["proyecto"]["distrito"] == ""

    def test_naturaleza_desconocida_advierte(self):
        reg = self._make_reg()
        reg.naturaleza = "TERRENO DE PIÑA EN EMBLAJE INDUSTRIAL"  # no mapeado
        res = build_datos_apt(
            cajetin=self._make_caja(), registro=reg, entero=self._make_ent(),
        )
        assert res["datos_apt"]["plano"]["tipo_uso"] == ""
        assert any("Tipo de uso APT no se pudo inferir" in a for a in res["advertencias"])

    def test_sin_coordenadas_advierte(self):
        caja = self._make_caja()
        caja.coordenadas = []
        res = build_datos_apt(
            cajetin=caja, registro=self._make_reg(), entero=self._make_ent(),
        )
        assert any("listado de coordenadas" in a.lower() for a in res["advertencias"])
        assert "plano.norte" in res["confianza_baja"]
