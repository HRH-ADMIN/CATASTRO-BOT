"""Workflow para planos de segregación (Ley 6545).

Flujo completo (20 pasos):
  1-10  pasos comunes (recibir, pago cliente, formato, enteros, APT R1,
        análisis IA, carta agua)
  11-15 municipalidad (formulario, aviso, pago muni, correo, visado)
  16    APT segunda ronda (con visado)
  17-20 inscripción + entrega + recordatorio
"""
from src.models.estado import Estado
from src.models.plano import TipoPlano
from src.workflows.base_workflow import BaseWorkflow


class SegregacionWorkflow(BaseWorkflow):
    tipo_plano = TipoPlano.SEGREGACION.value

    def _post_carta_agua_estado(self) -> Estado:
        return Estado.LISTO_PAQUETE_MUNI
