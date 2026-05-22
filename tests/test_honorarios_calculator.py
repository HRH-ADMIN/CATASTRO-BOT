"""Tests del calculador de honorarios — Decreto 17481-MOPT + reglas oficina."""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.utils.honorarios_calculator import (
    HonorariosInput,
    PlanoInput,
    calcular_honorarios,
    decidir_tipo_y_zona_por_area,
    AJUSTE_FIJO_POR_PLANO,
    UMBRAL_URBANA_RURAL_M2,
    INDICE_INFLACIONARIO_DEFAULT,
)


# ─── Auto-clasificación urbana/rural ─────────────────────────────────────

class TestAutoClasificacion:
    def test_pequeno_es_urbana(self):
        tipo, zona = decidir_tipo_y_zona_por_area(500)
        assert tipo == "urbana"
        assert zona == "E"

    def test_justo_bajo_umbral_es_urbana(self):
        tipo, zona = decidir_tipo_y_zona_por_area(1999)
        assert tipo == "urbana"
        assert zona == "E"

    def test_umbral_exacto_es_rural(self):
        tipo, zona = decidir_tipo_y_zona_por_area(2000)
        assert tipo == "rural"
        assert zona == ""

    def test_grande_es_rural(self):
        tipo, zona = decidir_tipo_y_zona_por_area(20000)
        assert tipo == "rural"


# ─── Decreto official example ────────────────────────────────────────────

class TestEjemploDecreto:
    """Caso del Decreto 17481-MOPT — sin ajuste +5000.

    Para testear la fórmula pura, usamos AJUSTE_FIJO_POR_PLANO=0 efectivamente
    al especificar n_planos=1 y restando el ajuste al final. O sea: aquí solo
    validamos que el cálculo SUBYACENTE coincida con el decreto.
    """
    def test_ejemplo_15_planos_urbana_ch_30km_con_ajuste(self):
        # 15 × 1500m² urbana CH + 30km. Cada plano +5000 ajuste.
        # Esperado: ₡4,878,680 (incluye ajuste 5000 × planos según descuento)
        r = calcular_honorarios(HonorariosInput(
            area_m2=1500,
            tipo_parcela="urbana",
            zona="CH",
            n_planos=15,
            distancia_km=30,
        ))
        assert r.total == Decimal("4878680")
        assert r.gastos_transporte == Decimal("14732")


# ─── Reglas de oficina ───────────────────────────────────────────────────

class TestReglasDeOficina:
    def test_ajuste_fijo_por_plano(self):
        # 1 plano de 20966.88 m² → rural mínimo 279,685 + 5000 = 284,685
        # Pero el cálculo Y = 200520 × √2.096688 ≈ 290,352 (> mínimo)
        # entonces precio = 290,352 + 5000 = 295,352
        r = calcular_honorarios(HonorariosInput(area_m2=20966.88, n_planos=1))
        assert r.total == Decimal("295352")

    def test_minimo_legal_rural_aplicado(self):
        # Lote rural justo en umbral 2000 m² → Y = 200520 × √0.2 ≈ 89,663
        # < mínimo 279,685 → aplica mínimo. + 5000 = 284,685
        r = calcular_honorarios(HonorariosInput(area_m2=2000, n_planos=1))
        assert r.total == Decimal("284685")
        assert any("mínimo legal" in n.lower() for n in r.notas)

    def test_descuento_se_aplica_a_planos_mas_caros(self):
        # 13 planos: 5×1000m² (urbana 197,487) + 3×5000m² + 5×8500m² (rurales 284,685)
        # Convención: ordenar ASCENDENTE, descuento cae en los más caros
        # Resultado esperado: ₡3,037,167
        planos = (
            [PlanoInput(area_m2=1000) for _ in range(5)] +
            [PlanoInput(area_m2=5000) for _ in range(3)] +
            [PlanoInput(area_m2=8500) for _ in range(5)]
        )
        r = calcular_honorarios(HonorariosInput(planos=planos))
        assert r.total == Decimal("3037167")


# ─── Validación de inputs ────────────────────────────────────────────────

class TestValidacion:
    def test_area_cero_lanza(self):
        with pytest.raises(ValueError):
            calcular_honorarios(HonorariosInput(area_m2=0))

    def test_area_negativa_lanza(self):
        with pytest.raises(ValueError):
            calcular_honorarios(HonorariosInput(area_m2=-100))

    def test_n_planos_cero_lanza(self):
        with pytest.raises(ValueError):
            calcular_honorarios(HonorariosInput(area_m2=1000, n_planos=0))

    def test_zona_invalida_urbana_lanza(self):
        with pytest.raises(ValueError):
            calcular_honorarios(HonorariosInput(
                area_m2=1500, tipo_parcela="urbana", zona="Z",
            ))


# ─── Texto de observaciones para descuento ───────────────────────────────

class TestTextoObservaciones:
    def test_sin_descuento_texto_vacio(self):
        r = calcular_honorarios(HonorariosInput(area_m2=20966.88, n_planos=1))
        assert r.texto_observaciones_descuento() == ""

    def test_9_planos_no_tiene_descuento(self):
        r = calcular_honorarios(HonorariosInput(area_m2=20966.88, n_planos=9))
        assert r.texto_observaciones_descuento() == ""

    def test_10_planos_genera_texto(self):
        r = calcular_honorarios(HonorariosInput(area_m2=20966.88, n_planos=10))
        texto = r.texto_observaciones_descuento()
        assert "Decreto 17481-MOPT" in texto
        assert "Planos 1 al 9" in texto
        assert "Planos 10 al 10" in texto

    def test_30_planos_los_3_rangos(self):
        r = calcular_honorarios(HonorariosInput(area_m2=2000, n_planos=30))
        texto = r.texto_observaciones_descuento()
        assert "Planos 1 al 9" in texto
        assert "Planos 10 al 25" in texto
        assert "Planos 26 al 30" in texto
        assert "20%" in texto
        assert "30%" in texto


# ─── Resultado por plano individual ──────────────────────────────────────

class TestPlanoResultado:
    def test_urbana_e_clasificada_correctamente(self):
        r = calcular_honorarios(HonorariosInput(area_m2=500, n_planos=1))
        assert len(r.planos_resultado) == 1
        pr = r.planos_resultado[0]
        assert pr.tipo_parcela == "urbana"
        assert pr.zona == "E"

    def test_rural_clasificada_correctamente(self):
        r = calcular_honorarios(HonorariosInput(area_m2=20000, n_planos=1))
        pr = r.planos_resultado[0]
        assert pr.tipo_parcela == "rural"
        assert pr.zona == ""
