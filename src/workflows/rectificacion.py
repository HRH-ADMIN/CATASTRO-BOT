"""Workflow para Rectificación de Medida o Linderos.

Sin municipalidad, sólo una ronda APT — la primera ronda inscribe
directamente si no hay errores. No requiere carta de agua.
"""
from src.models.estado import Estado
from src.models.plano import TipoPlano
from src.workflows.base_workflow import BaseWorkflow


class RectificacionWorkflow(BaseWorkflow):
    tipo_plano = TipoPlano.RECTIFICACION.value

    def _post_carta_agua_estado(self) -> Estado:
        return Estado.INSCRITO
