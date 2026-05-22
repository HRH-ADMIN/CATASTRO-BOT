"""Estados del expediente. Cada estado representa un punto verificable
del flujo de trámite definido por la Ley 6545 (Catastro Nacional CR).

El workflow asociado al `tipo_plano` decide la siguiente transición. Los
estados marcados como "halt" requieren intervención externa (cliente,
topógrafo, municipalidad) para avanzar. Los estados terminales no avanzan."""
from enum import Enum


class Estado(str, Enum):
    # --- inicio ---
    RECIBIDO = "recibido"

    # --- paso 2: pago del cliente ---
    PAGO_CLIENTE_PENDIENTE = "pago_cliente_pendiente"
    PAGO_CLIENTE_CONFIRMADO = "pago_cliente_confirmado"

    # --- paso 3: validación de formato PDF/DWG ---
    FORMATO_VALIDADO = "formato_validado"
    FORMATO_INVALIDO = "formato_invalido"  # halt: topógrafo debe corregir

    # --- paso 4: pagar enteros al Registro
    # (FORMATO_VALIDADO ya solicita la confirmación; al confirmarse pasa
    # directamente a ENTEROS_PAGADOS)
    ENTEROS_PAGADOS = "enteros_pagados"

    # --- paso 5: subir a APT primera ronda
    # (ENTEROS_PAGADOS solicita la confirmación; al confirmarse y subir
    # el plano pasa a PRESENTADO_APT_R1)
    PRESENTADO_APT_R1 = "presentado_apt_r1"

    # --- pasos 6-7: monitoreo + descarga de minuta ---
    APT_R1_RESPONDIO = "apt_r1_respondio"

    # --- paso 8: análisis con IA (transient — branches en handler) ---
    APT_R1_ANALIZADO = "apt_r1_analizado"

    # --- pasos 9a/9b/9c ---
    APT_CORRECCIONES = "apt_correcciones"  # halt: topógrafo corrige
    APT_TRASLAPES = "apt_traslapes"        # halt: apelación
    APROBADO_R1 = "aprobado_r1"

    # --- paso 10: carta de agua ---
    CARTA_AGUA_REQUERIDA = "carta_agua_requerida"  # halt: cliente envía
    CARTA_AGUA_OK = "carta_agua_ok"

    # --- pasos 11-15: municipalidad (sólo segregacion/reunion_de_fincas) ---
    LISTO_PAQUETE_MUNI = "listo_paquete_muni"
    FORMULARIO_MUNI_ENVIADO = "formulario_muni_enviado"     # 12 hecho, esperando aviso
    AVISO_MUNI_RECIBIDO = "aviso_muni_recibido"             # 13: impuestos
    PAGO_MUNI_CLIENTE_CONFIRMADO = "pago_muni_cliente_confirmado"
    CORREO_MUNI_ENVIADO = "correo_muni_enviado"             # 14b: esperando visado
    VISADO_APROBADO = "visado_aprobado"
    VISADO_RECHAZADO = "visado_rechazado"  # halt

    # --- paso 16: APT segunda ronda (segregacion/reunion_de_fincas/fincas_completas) ---
    LISTO_APT_R2 = "listo_apt_r2"
    PRESENTADO_APT_R2 = "presentado_apt_r2"

    # --- pasos 17-18: inscripción final ---
    INSCRITO = "inscrito"
    INSCRITO_DESCARGADO = "inscrito_descargado"

    # --- paso 19: entrega al cliente ---
    ENTREGADO = "entregado"

    # --- terminales ---
    CANCELADO = "cancelado"
    ERROR = "error"

    @classmethod
    def values(cls) -> list[str]:
        return [e.value for e in cls]

    @classmethod
    def terminales(cls) -> set[str]:
        return {cls.ENTREGADO.value, cls.CANCELADO.value}

    @classmethod
    def halts(cls) -> set[str]:
        """Estados que requieren intervención externa (manual) para avanzar.

        Estados que requieren confirmación WhatsApp NO son halts — el handler
        del workflow gestiona la confirmación activamente y avanza al recibir
        respuesta del cliente.
        """
        return {
            cls.APT_CORRECCIONES.value,        # topógrafo debe corregir
            cls.APT_TRASLAPES.value,           # apelación
            cls.CARTA_AGUA_REQUERIDA.value,    # cliente debe enviar carta
            cls.FORMATO_INVALIDO.value,        # topógrafo debe corregir
            cls.VISADO_RECHAZADO.value,        # muni rechazó
        }
