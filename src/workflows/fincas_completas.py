"""Workflow para planos de Fincas Completas.

Sin municipalidad, sólo dos rondas APT (localización de finca completa).
Después de carta de agua (no aplica en la práctica), va directo a APT R2.
"""
from src.models.estado import Estado
from src.models.plano import TipoPlano
from src.workflows.base_workflow import BaseWorkflow


class FincasCompletasWorkflow(BaseWorkflow):
    tipo_plano = TipoPlano.FINCAS_COMPLETAS.value

    def _post_carta_agua_estado(self) -> Estado:
        return Estado.LISTO_APT_R2
