"""Workflow para planos de Reunión de Fincas.

Flujo: incluye paso de municipalidad (visado) y segunda ronda APT,
igual que segregación — pero sin carta de agua (no es fraccionamiento).
"""
from src.models.estado import Estado
from src.models.plano import TipoPlano
from src.workflows.base_workflow import BaseWorkflow


class ReunionDeFincasWorkflow(BaseWorkflow):
    tipo_plano = TipoPlano.REUNION_DE_FINCAS.value

    def _post_carta_agua_estado(self) -> Estado:
        # Carta de agua no aplica a reunión, pero si el operador la marca
        # manualmente como ok, avanzamos a municipalidad.
        return Estado.LISTO_PAQUETE_MUNI
