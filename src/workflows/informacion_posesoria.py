"""Workflow para planos de Información Posesoria (derechos posesorios).

Sin municipalidad, sólo una ronda APT — la primera ronda inscribe
directamente si no hay errores. No requiere carta de agua.
"""
from src.models.estado import Estado
from src.models.plano import TipoPlano
from src.workflows.base_workflow import BaseWorkflow


class InformacionPosesoriaWorkflow(BaseWorkflow):
    tipo_plano = TipoPlano.INFORMACION_POSESORIA.value

    def _post_carta_agua_estado(self) -> Estado:
        return Estado.INSCRITO
