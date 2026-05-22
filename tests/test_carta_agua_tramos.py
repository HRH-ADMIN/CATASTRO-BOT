"""Tests para la lógica de carta de agua del AyA — 3 tramos por área.

Regla operativa Muni San Ramón (aprendida 2026-05-15):

  - Área < 1000 m² (1-999)   → CARTA OBLIGATORIA para visado
  - Área 1000-5000 m²        → CARTA OPCIONAL (carta o nota del plano)
  - Área > 5000 m²           → CARTA NO APLICA

El operador puede override con:
  - metadata.carta_agua_requerida (true/false) → fuerza obligatoria/no_aplica
  - metadata.carta_agua_opcional (true) → fuerza opcional
"""
from __future__ import annotations

import json

import pytest

from src.workflows.segregacion import SegregacionWorkflow


def _hacer_workflow():
    """Crea un workflow mock para llamar a las funciones puras."""
    class _MockDb: pass
    return SegregacionWorkflow(db=_MockDb(), agents={})


def _exp(area_m2, tipo="segregacion", **extra_meta):
    """Crea un expediente fake para tests."""
    meta = {"area_m2": area_m2, **extra_meta}
    return {
        "id": "test-id",
        "tipo_plano": tipo,
        "metadata_json": json.dumps(meta),
    }


# ── Tramo 1: < 1000 m² obligatoria ──────────────────────────────────────

class TestTramoObligatoria:
    @pytest.mark.parametrize("area", [1, 100, 500, 800, 999, 999.99])
    def test_menor_1000_es_obligatoria(self, area):
        wf = _hacer_workflow()
        assert wf._evaluar_carta_agua(_exp(area)) == wf.CARTA_AGUA_OBLIGATORIA
        assert wf._requiere_carta_agua(_exp(area)) is True

    def test_1000_exacto_NO_es_obligatoria_es_opcional(self):
        # 1000 m² entra en el tramo opcional (>= 1000 y <= 5000)
        wf = _hacer_workflow()
        assert wf._evaluar_carta_agua(_exp(1000)) == wf.CARTA_AGUA_OPCIONAL


# ── Tramo 2: 1000-5000 m² opcional ──────────────────────────────────────

class TestTramoOpcional:
    @pytest.mark.parametrize("area", [1000, 1500, 2000, 3500, 5000])
    def test_entre_1000_5000_es_opcional(self, area):
        wf = _hacer_workflow()
        assert wf._evaluar_carta_agua(_exp(area)) == wf.CARTA_AGUA_OPCIONAL

    def test_opcional_no_requiere_carta_obligatoria(self):
        # _requiere_carta_agua() devuelve False en tramo opcional
        # (porque el operador decide entre carta y nota)
        wf = _hacer_workflow()
        assert wf._requiere_carta_agua(_exp(2500)) is False


# ── Tramo 3: > 5000 m² no aplica ────────────────────────────────────────

class TestTramoSoloNota:
    """Tramo 3: > 5000 m² — no carta pero NOTA obligatoria."""

    @pytest.mark.parametrize("area", [5001, 7000, 11836.23, 33440, 100000])
    def test_mayor_5000_es_solo_nota(self, area):
        wf = _hacer_workflow()
        assert wf._evaluar_carta_agua(_exp(area)) == wf.CARTA_AGUA_SOLO_NOTA
        # _requiere_carta_agua devuelve False (no es obligatoria como carta)
        assert wf._requiere_carta_agua(_exp(area)) is False

    def test_javsal_7000_solo_nota(self):
        # SEG-2026-005 JAVSAL: 7000 m² — caso real
        wf = _hacer_workflow()
        assert wf._evaluar_carta_agua(_exp(7000.00)) == wf.CARTA_AGUA_SOLO_NOTA

    def test_rolesq_11836_solo_nota(self):
        # SEG-2026-006 ROLESQ: 11836.23 m² — caso real
        wf = _hacer_workflow()
        assert wf._evaluar_carta_agua(_exp(11836.23)) == wf.CARTA_AGUA_SOLO_NOTA

    def test_solo_nota_no_es_obligatoria_pero_requiere_nota(self):
        """SOLO_NOTA significa: sin carta, pero la NOTA en plano es obligatoria.
        El workflow debe avisar al operador para verificar la nota."""
        wf = _hacer_workflow()
        # No es obligatoria como carta
        assert wf._requiere_carta_agua(_exp(8000)) is False
        # Pero sí es un estado distinto a NO_APLICA
        assert wf._evaluar_carta_agua(_exp(8000)) != wf.CARTA_AGUA_NO_APLICA


# ── Tipos NO segregación ────────────────────────────────────────────────

class TestTipoNoSegregacion:
    @pytest.mark.parametrize("tipo", [
        "rectificacion", "reunion_de_fincas", "informacion_posesoria",
        "fincas_completas",
    ])
    def test_solo_segregacion_evalua(self, tipo):
        wf = _hacer_workflow()
        # Aunque sea < 1000 m², si no es segregación → NO_APLICA
        assert wf._evaluar_carta_agua(_exp(500, tipo=tipo)) == wf.CARTA_AGUA_NO_APLICA


# ── Sin área (precaución) ───────────────────────────────────────────────

class TestSinArea:
    def test_sin_area_es_opcional(self):
        # Si no hay área → opcional (operador decide)
        wf = _hacer_workflow()
        exp = {
            "id": "test",
            "tipo_plano": "segregacion",
            "metadata_json": json.dumps({}),
        }
        assert wf._evaluar_carta_agua(exp) == wf.CARTA_AGUA_OPCIONAL

    def test_area_invalida_es_opcional(self):
        wf = _hacer_workflow()
        exp = _exp(area_m2="no es un número")
        assert wf._evaluar_carta_agua(exp) == wf.CARTA_AGUA_OPCIONAL


# ── Override del operador ───────────────────────────────────────────────

class TestOverrideOperador:
    def test_carta_agua_requerida_true_fuerza_obligatoria(self):
        wf = _hacer_workflow()
        # Aunque sea 100,000 m², si operador dice "requerida=true" → obligatoria
        exp = _exp(area_m2=100000, carta_agua_requerida=True)
        assert wf._evaluar_carta_agua(exp) == wf.CARTA_AGUA_OBLIGATORIA

    def test_carta_agua_requerida_false_fuerza_no_aplica(self):
        wf = _hacer_workflow()
        # Aunque sea 500 m², si operador dice "requerida=false" → no aplica
        exp = _exp(area_m2=500, carta_agua_requerida=False)
        assert wf._evaluar_carta_agua(exp) == wf.CARTA_AGUA_NO_APLICA

    def test_carta_agua_opcional_true_fuerza_opcional(self):
        wf = _hacer_workflow()
        # Aunque sea 100 m² (obligatoria normalmente), si "opcional=true" → opcional
        exp = _exp(area_m2=100, carta_agua_opcional=True)
        assert wf._evaluar_carta_agua(exp) == wf.CARTA_AGUA_OPCIONAL


# ── Casos límite (boundaries) ───────────────────────────────────────────

class TestBoundaries:
    def test_999_99_obligatoria(self):
        wf = _hacer_workflow()
        assert wf._evaluar_carta_agua(_exp(999.99)) == wf.CARTA_AGUA_OBLIGATORIA

    def test_1000_00_opcional(self):
        wf = _hacer_workflow()
        assert wf._evaluar_carta_agua(_exp(1000.00)) == wf.CARTA_AGUA_OPCIONAL

    def test_5000_00_opcional(self):
        wf = _hacer_workflow()
        assert wf._evaluar_carta_agua(_exp(5000.00)) == wf.CARTA_AGUA_OPCIONAL

    def test_5000_01_es_solo_nota(self):
        """> 5000 m² — sin carta pero CON nota obligatoria."""
        wf = _hacer_workflow()
        assert wf._evaluar_carta_agua(_exp(5000.01)) == wf.CARTA_AGUA_SOLO_NOTA


# ── Nota textual debe estar en plano siempre que no haya carta ──────────

class TestNotaTextualSiempreObligatoria:
    """La nota del plano es OBLIGATORIA en todo tramo que no use carta de agua."""

    def test_nota_constante_existe(self):
        from src.workflows.base_workflow import BaseWorkflow
        assert hasattr(BaseWorkflow, "NOTA_AGUA_MUNI_SR")
        assert hasattr(BaseWorkflow, "NOTA_AGUA_OPCIONAL")  # alias retro-compatible
        assert BaseWorkflow.NOTA_AGUA_MUNI_SR == BaseWorkflow.NOTA_AGUA_OPCIONAL

    def test_nota_texto_completo(self):
        from src.workflows.base_workflow import BaseWorkflow
        nota = BaseWorkflow.NOTA_AGUA_MUNI_SR
        # Verificar palabras clave de la nota oficial
        for clave in [
            "MUNICIPALIDAD", "OTORGARÁ", "PERMISO DE CONSTRUCCIÓN",
            "OPERADORES", "NORMATIVA", "SERVICIOS PÚBLICOS INDISPENSABLES",
        ]:
            assert clave in nota, f"falta '{clave}' en NOTA"


# ── Constante NOTA_AGUA_OPCIONAL (texto exacto del operador) ────────────

class TestNotaAguaOpcional:
    def test_nota_contiene_texto_clave(self):
        from src.workflows.base_workflow import BaseWorkflow
        nota = BaseWorkflow.NOTA_AGUA_OPCIONAL
        assert "MUNICIPALIDAD" in nota
        assert "PERMISO DE CONSTRUCCIÓN" in nota
        assert "OPERADORES" in nota
        assert "SERVICIOS PÚBLICOS INDISPENSABLES" in nota
