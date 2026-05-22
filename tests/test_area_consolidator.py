"""Tests de consolidación de áreas para contratos APT multi-plano.

Cubre la regla operativa: cuando un contrato APT tiene varios planos,
'area_real' y 'area_predio' del bC7 son SUMAS de los hermanos.
"""
from __future__ import annotations

import json

import pytest

from src.utils.area_consolidator import (
    consolidar_areas_contrato,
    aplicar_consolidacion_a_datos_apt,
    encontrar_hermanos_de_contrato,
    consolidar_area_registro_plano,
)


# ── consolidar_areas_contrato ─────────────────────────────────────────

class TestConsolidar:
    def test_dos_planos_suma_correcta(self):
        """Caso VICTOR #2 v2: con fincas declaradas en cada plano (dedup)."""
        exps = [
            {"numero_expediente": "REU",
             "datos_apt": {"plano": {
                 "area_real": "2751.30",
                 "area_registro": "10582.81",
                 "fincas": [
                     {"numero": "642038", "area_registro_m2": "2500.00"},
                     {"numero": "161099", "area_registro_m2": "8082.81"},
                 ],
             }}},
            {"numero_expediente": "SEG",
             "datos_apt": {"plano": {
                 "area_real": "2097.00",
                 "area_registro": "8082.81",
                 "fincas": [
                     {"numero": "161099", "area_registro_m2": "8082.81"},
                 ],
             }}},
        ]
        t = consolidar_areas_contrato(exps)
        assert t["n_planos"] == 2
        assert t["area_real_m2"] == 4848.30
        # area_predio = SUMA fincas únicas (no doble cuenta 161099)
        assert t["area_predio_m2"] == 10582.81
        # Confirmar que la dedup funcionó
        numeros_unicos = {f["numero"] for f in t["fincas_unicas"]}
        assert numeros_unicos == {"642038", "161099"}

    def test_tres_planos(self):
        """Cada plano con su propia finca única — sin dedup necesario."""
        exps = [
            {"numero_expediente": "A",
             "datos_apt": {"plano": {
                 "area_real": 100, "area_registro": 200,
                 "fincas": [{"numero": "111", "area_registro_m2": "200"}],
             }}},
            {"numero_expediente": "B",
             "datos_apt": {"plano": {
                 "area_real": 200, "area_registro": 300,
                 "fincas": [{"numero": "222", "area_registro_m2": "300"}],
             }}},
            {"numero_expediente": "C",
             "datos_apt": {"plano": {
                 "area_real": 300, "area_registro": 400,
                 "fincas": [{"numero": "333", "area_registro_m2": "400"}],
             }}},
        ]
        t = consolidar_areas_contrato(exps)
        assert t["n_planos"] == 3
        assert t["area_real_m2"] == 600
        assert t["area_predio_m2"] == 900

    def test_un_solo_plano_devuelve_su_propia_area(self):
        exps = [
            {"numero_expediente": "X",
             "datos_apt": {"plano": {
                 "area_real": "1500.50", "area_registro": "1500.00",
                 "fincas": [{"numero": "999", "area_registro_m2": "1500"}],
             }}},
        ]
        t = consolidar_areas_contrato(exps)
        assert t["n_planos"] == 1
        assert t["area_real_m2"] == 1500.50
        assert t["area_predio_m2"] == 1500.00

    def test_finca_compartida_se_cuenta_una_sola_vez(self):
        """REGLA CRÍTICA aprendida en VICTOR #2: si una finca aparece en
        varios planos, area_registro se cuenta UNA SOLA VEZ.
        """
        exps = [
            {"numero_expediente": "REU",
             "datos_apt": {"plano": {
                 "area_real": "100", "area_registro": "300",
                 "fincas": [
                     {"numero": "100", "area_registro_m2": "100"},
                     {"numero": "200", "area_registro_m2": "200"},
                 ],
             }}},
            {"numero_expediente": "SEG",
             "datos_apt": {"plano": {
                 "area_real": "50", "area_registro": "200",
                 "fincas": [
                     {"numero": "200", "area_registro_m2": "200"},  # MISMA finca
                 ],
             }}},
        ]
        t = consolidar_areas_contrato(exps)
        # area_real SÍ suma por plano: 100 + 50 = 150
        assert t["area_real_m2"] == 150
        # area_predio = fincas únicas (100 + 200) = 300, NO 500
        assert t["area_predio_m2"] == 300
        # Solo 2 fincas únicas
        assert len({f["numero"] for f in t["fincas_unicas"]}) == 2

    def test_acepta_string_con_comas(self):
        """area_real='1,234.56' debe parsearse a 1234.56."""
        exps = [
            {"numero_expediente": "X",
             "datos_apt": {"plano": {
                 "area_real": "1,234.56", "area_registro": "1,000.00",
                 "fincas": [{"numero": "1", "area_registro_m2": "1,000"}],
             }}},
        ]
        t = consolidar_areas_contrato(exps)
        assert t["area_real_m2"] == 1234.56

    def test_expediente_sin_datos_apt_cuenta_como_cero(self):
        exps = [
            {"numero_expediente": "OK",
             "datos_apt": {"plano": {"area_real": 100, "area_registro": 100}}},
            {"numero_expediente": "MALO"},  # sin datos_apt
        ]
        t = consolidar_areas_contrato(exps)
        assert t["n_planos"] == 2
        assert t["area_real_m2"] == 100
        # MALO se incluye con error
        errores = [d for d in t["detalle"] if "error" in d]
        assert len(errores) == 1
        assert errores[0]["numero_expediente"] == "MALO"

    def test_area_no_parseable_cuenta_como_cero(self):
        exps = [
            {"numero_expediente": "X",
             "datos_apt": {"plano": {"area_real": "abc",
                                     "area_registro": "xyz"}}},
        ]
        t = consolidar_areas_contrato(exps)
        assert t["area_real_m2"] == 0.0
        assert t["area_predio_m2"] == 0.0

    def test_acepta_metadata_json_string(self):
        """Caller pasa el JSON string sin parsear."""
        exps = [
            {"numero_expediente": "X",
             "metadata_json": json.dumps({
                 "datos_apt": {"plano": {
                     "area_real": "500", "area_registro": "600",
                     "fincas": [{"numero": "1", "area_registro_m2": "600"}],
                 }}})},
        ]
        t = consolidar_areas_contrato(exps)
        assert t["area_real_m2"] == 500
        assert t["area_predio_m2"] == 600

    def test_detalle_preserva_orden(self):
        exps = [
            {"numero_expediente": "Z",
             "datos_apt": {"plano": {"area_real": 1, "area_registro": 1}}},
            {"numero_expediente": "A",
             "datos_apt": {"plano": {"area_real": 2, "area_registro": 2}}},
        ]
        t = consolidar_areas_contrato(exps)
        # Mantiene el orden de entrada, no alfabetiza
        assert [d["numero_expediente"] for d in t["detalle"]] == ["Z", "A"]


# ── aplicar_consolidacion_a_datos_apt ──────────────────────────────────

class TestAplicar:
    def test_sobreescribe_general(self):
        datos = {"general": {"area_real": "100", "area_predio": "100",
                             "honorarios": "5000"}}
        totales = {"area_real_m2": 200, "area_predio_m2": 300, "n_planos": 2}
        aplicar_consolidacion_a_datos_apt(datos, totales)
        assert datos["general"]["area_real"] == "200.00"
        assert datos["general"]["area_predio"] == "300.00"
        # Honorarios NO se tocan
        assert datos["general"]["honorarios"] == "5000"

    def test_marca_auditoria(self):
        datos = {"general": {}}
        totales = {"area_real_m2": 1, "area_predio_m2": 2, "n_planos": 3}
        aplicar_consolidacion_a_datos_apt(datos, totales)
        assert datos["general"]["_areas_consolidadas_n_planos"] == 3

    def test_crea_general_si_no_existe(self):
        datos = {}  # sin general
        totales = {"area_real_m2": 10, "area_predio_m2": 20, "n_planos": 1}
        aplicar_consolidacion_a_datos_apt(datos, totales)
        assert "general" in datos
        assert datos["general"]["area_real"] == "10.00"

    def test_devuelve_el_mismo_dict(self):
        datos = {"general": {}}
        totales = {"area_real_m2": 1, "area_predio_m2": 1, "n_planos": 1}
        ret = aplicar_consolidacion_a_datos_apt(datos, totales)
        assert ret is datos  # mutate-friendly


# ── encontrar_hermanos_de_contrato (necesita db real) ─────────────────

class TestEncontrarHermanos:
    @pytest.fixture
    def db(self, tmp_path):
        from tests.conftest import FakeCredentialManager, TestDatabase
        creds = FakeCredentialManager()
        d = TestDatabase(path=tmp_path / "t.db", credentials=creds)
        d.initialize_schema()
        return d

    def _crear_par_hermanos(self, db):
        """Crea SEG + REU como contrato compartido."""
        reu = db.crear_expediente(
            numero_expediente="REU-X",
            tipo_plano="reunion_de_fincas",
            nombre_topografo="Test", telefono_cliente="+5068888",
            metadata={
                "contrato_apt_compartido": True,
                "orden_plano": 1,
                "expedientes_hermanos": ["SEG-X"],
            },
        )
        seg = db.crear_expediente(
            numero_expediente="SEG-X",
            tipo_plano="segregacion",
            nombre_topografo="Test", telefono_cliente="+5068888",
            metadata={
                "contrato_apt_compartido": True,
                "orden_plano": 2,
                "expedientes_hermanos": ["REU-X"],
            },
        )
        return reu, seg

    def test_encuentra_hermano(self, db):
        self._crear_par_hermanos(db)
        hermanos = encontrar_hermanos_de_contrato(db, "REU-X")
        assert len(hermanos) == 2
        numeros = [h["numero_expediente"] for h in hermanos]
        assert "REU-X" in numeros
        assert "SEG-X" in numeros

    def test_ordenado_por_orden_plano(self, db):
        self._crear_par_hermanos(db)
        # Pedir desde el secundario — debe devolver ordenado por orden_plano asc
        hermanos = encontrar_hermanos_de_contrato(db, "SEG-X")
        assert hermanos[0]["numero_expediente"] == "REU-X"  # orden=1
        assert hermanos[1]["numero_expediente"] == "SEG-X"  # orden=2

    def test_expediente_sin_hermanos_devuelve_solo_a_si_mismo(self, db):
        db.crear_expediente(
            numero_expediente="SOLO-X",
            tipo_plano="segregacion",
            nombre_topografo="Test", telefono_cliente="+5068888",
        )
        hermanos = encontrar_hermanos_de_contrato(db, "SOLO-X")
        assert len(hermanos) == 1
        assert hermanos[0]["numero_expediente"] == "SOLO-X"

    def test_expediente_inexistente_devuelve_lista_vacia(self, db):
        hermanos = encontrar_hermanos_de_contrato(db, "NO-EXISTE")
        assert hermanos == []


# ── Integración: caso VICTOR #2 ────────────────────────────────────────

class TestVictor2RealCase:
    """Caso real que originó la regla — debe seguir funcionando."""

    def test_victor2_areas_4848_y_10582_con_dedup(self):
        """Caso VICTOR #2 CORREGIDO: finca 161099 está en AMBOS planos."""
        exps = [
            {"numero_expediente": "RDF-2026-005",
             "datos_apt": {"plano": {
                 "area_real": "2751.30",
                 "area_registro": "10582.81",
                 "fincas": [
                     {"numero": "642038", "area_registro_m2": "2500.00"},
                     {"numero": "161099", "area_registro_m2": "8082.81"},
                 ],
             }}},
            {"numero_expediente": "SEG-2026-002",
             "datos_apt": {"plano": {
                 "area_real": "2097.00",
                 "area_registro": "8082.81",
                 "fincas": [
                     {"numero": "161099", "area_registro_m2": "8082.81"},
                 ],
             }}},
        ]
        t = consolidar_areas_contrato(exps)
        assert t["area_real_m2"] == 4848.30, (
            "bC7.area_real = SUMA por plano (incluso si fincas se comparten)"
        )
        assert t["area_predio_m2"] == 10582.81, (
            "bC7.area_predio = SUMA fincas únicas (dedup 161099) — "
            "NO el doble conteo 18665.62"
        )

    def test_victor2_honorarios_recalculados(self):
        """Caso real: 1 plano calculaba ¢284,685 pero son 2 → ¢569,370."""
        datos_apt = {
            "general": {
                "area_real":   "2751.30",
                "area_predio": "2500.00",
                "honorarios":  "284685",
                "max_planos":  "1",
            }
        }
        totales = consolidar_areas_contrato([
            {"numero_expediente": "RDF-2026-005",
             "datos_apt": {"plano": {
                 "area_real": "2751.30", "area_registro": "10582.81",
                 "fincas": [
                     {"numero": "642038", "area_registro_m2": "2500"},
                     {"numero": "161099", "area_registro_m2": "8082.81"},
                 ],
             }}},
            {"numero_expediente": "SEG-2026-002",
             "datos_apt": {"plano": {
                 "area_real": "2097.00", "area_registro": "8082.81",
                 "fincas": [{"numero": "161099", "area_registro_m2": "8082.81"}],
             }}},
        ])
        aplicar_consolidacion_a_datos_apt(datos_apt, totales)
        assert int(datos_apt["general"]["honorarios"]) == 569370
        assert datos_apt["general"]["max_planos"] == "2"
        assert datos_apt["general"]["n_planos_catastrar"] == "2"


class TestAplicarHonorarios:
    """La consolidación debe recalcular honorarios cuando n_planos > 1."""

    def test_no_recalcula_si_un_solo_plano(self):
        datos = {"general": {"honorarios": "100000", "max_planos": "1"}}
        totales = {
            "n_planos": 1,
            "area_real_m2":   500.0,
            "area_predio_m2": 500.0,
            "detalle": [{"numero_expediente": "X",
                         "area_real": 500.0, "area_registro": 500.0}],
        }
        aplicar_consolidacion_a_datos_apt(datos, totales)
        # Sin tocar honorarios — solo 1 plano
        assert datos["general"]["honorarios"] == "100000"
        assert datos["general"]["max_planos"] == "1"

    def test_actualiza_max_planos_a_n(self):
        datos = {"general": {"max_planos": "1"}}
        totales = {
            "n_planos": 3,
            "area_real_m2": 0.0, "area_predio_m2": 0.0,
            "detalle": [
                {"numero_expediente": "A", "area_real": 0, "area_registro": 0},
                {"numero_expediente": "B", "area_real": 0, "area_registro": 0},
                {"numero_expediente": "C", "area_real": 0, "area_registro": 0},
            ],
        }
        aplicar_consolidacion_a_datos_apt(datos, totales)
        assert datos["general"]["max_planos"] == "3"
        assert datos["general"]["n_planos_catastrar"] == "3"

    def test_flag_recalcular_honorarios_false_skip(self):
        """Placeholder histórico — el test real está abajo con otro nombre."""
        pass


# ── consolidar_area_registro_plano (plano multi-finca) ─────────────────

class TestConsolidarAreaRegistroPlano:
    """Regla: el area_registro de UN plano = SUMA de areas de TODAS sus fincas."""

    def test_una_finca_devuelve_su_area(self):
        fincas = [{"numero": "642038", "area_registro_m2": "2500.00"}]
        assert consolidar_area_registro_plano(fincas) == 2500.00

    def test_dos_fincas_suma(self):
        """Caso VICTOR #2 plano reunión."""
        fincas = [
            {"numero": "642038", "area_registro_m2": "2500.00"},
            {"numero": "161099", "area_registro_m2": "8082.81"},
        ]
        assert consolidar_area_registro_plano(fincas) == 10582.81

    def test_acepta_area_registro_sin_sufijo_m2(self):
        fincas = [
            {"numero": "A", "area_registro": "100"},
            {"numero": "B", "area_registro": "200"},
        ]
        assert consolidar_area_registro_plano(fincas) == 300.00

    def test_lista_vacia_devuelve_cero(self):
        assert consolidar_area_registro_plano([]) == 0.0
        assert consolidar_area_registro_plano(None) == 0.0

    def test_strings_con_comas_fincas(self):
        fincas = [
            {"area_registro_m2": "1,234.56"},
            {"area_registro_m2": "2,765.44"},
        ]
        assert consolidar_area_registro_plano(fincas) == 4000.00

    def test_finca_sin_area_cuenta_como_cero(self):
        fincas = [
            {"area_registro_m2": "100"},
            {"numero": "X"},  # sin área
        ]
        assert consolidar_area_registro_plano(fincas) == 100.00


    def test_flag_recalcular_honorarios_false(self):
        """Si caller pide no recalcular, preserva honorarios viejos."""
        datos = {"general": {"honorarios": "999"}}
        totales = {
            "n_planos": 2,
            "area_real_m2": 5000, "area_predio_m2": 5000,
            "detalle": [
                {"numero_expediente": "A", "area_real": 2500, "area_registro": 2500},
                {"numero_expediente": "B", "area_real": 2500, "area_registro": 2500},
            ],
        }
        aplicar_consolidacion_a_datos_apt(
            datos, totales, recalcular_honorarios=False,
        )
        assert datos["general"]["honorarios"] == "999"  # intacto
        # Áreas + max_planos sí se aplicaron
        assert datos["general"]["max_planos"] == "2"
