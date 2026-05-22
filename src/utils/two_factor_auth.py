"""2FA para acciones sensibles del bot.

Cuando el operador solicita una acción crítica (ENVIAR CFIA, RECHAZAR
expediente, APROBAR halt, etc.), el bot:

  1. Genera un código de 6 dígitos
  2. Persiste en memoria con TTL configurable (default 5 min)
  3. Envía el código al operador
  4. El operador responde `CONFIRMAR <codigo>` para autorizar
  5. Si el código matchea (mismo número, dentro del TTL) → ejecuta la
     acción pendiente

Diseño:
  - Almacenamiento in-memory (dict) — sobrevive hasta reinicio del bot
  - Códigos numéricos de 6 dígitos generados con secrets.randbelow
  - Por defecto TTL 5 min, max 5 intentos por código
  - Limpia códigos expirados automáticamente al consultar

USO:
    from src.utils.two_factor_auth import TwoFactorAuth, AccionPendiente
    auth = TwoFactorAuth()

    # 1) Operador pide acción
    codigo = auth.crear_codigo(
        accion=AccionPendiente(
            tipo="enviar_cfia",
            actor="+50688887310",
            payload={"expediente_id": "abc"},
        ),
    )
    # Bot envía el código al operador
    enviar_whatsapp(actor, f"Código 2FA: {codigo}")

    # 2) Operador confirma
    accion = auth.validar_codigo(codigo, actor="+50688887310")
    if accion:
        # Ejecutar la acción
        ejecutar_envio(**accion.payload)
"""
from __future__ import annotations
import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

log = logging.getLogger("catastro.two_factor")


# Acciones que típicamente requieren 2FA
ACCION_ENVIAR_CFIA       = "enviar_cfia"
ACCION_RECHAZAR_EXP      = "rechazar_expediente"
ACCION_APROBAR_HALT      = "aprobar_halt"
ACCION_BORRAR_EXP        = "borrar_expediente"
ACCION_RESET_DB          = "reset_db"

ACCIONES_SENSIBLES = {
    ACCION_ENVIAR_CFIA, ACCION_RECHAZAR_EXP, ACCION_APROBAR_HALT,
    ACCION_BORRAR_EXP, ACCION_RESET_DB,
}


@dataclass
class AccionPendiente:
    """Una acción que espera confirmación 2FA del operador."""
    tipo:     str                   # uno de ACCIONES_SENSIBLES
    actor:    str                   # teléfono del operador que pidió
    payload:  dict[str, Any] = field(default_factory=dict)
    creada:   datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    intentos: int = 0


@dataclass
class _CodigoActivo:
    """Estado interno de un código en circulación."""
    codigo:    str
    accion:    AccionPendiente
    expira_en: datetime


class TwoFactorAuth:
    """Manager in-memory de códigos 2FA.

    Args:
        ttl_segundos: tiempo de vida de un código antes de expirar.
        max_intentos: cuántas veces se puede equivocar antes de invalidar.
        longitud_codigo: cantidad de dígitos (default 6).
    """

    def __init__(
        self, *,
        ttl_segundos: int = 300,
        max_intentos: int = 5,
        longitud_codigo: int = 6,
    ):
        self._codigos: dict[str, _CodigoActivo] = {}
        self._ttl = ttl_segundos
        self._max_intentos = max_intentos
        self._longitud = longitud_codigo

    def _generar(self) -> str:
        """Genera un código numérico de N dígitos via `secrets`."""
        rango = 10 ** self._longitud
        return f"{secrets.randbelow(rango):0{self._longitud}d}"

    def _purgar_expirados(self) -> int:
        """Borra códigos vencidos. Devuelve cantidad borrada."""
        ahora = datetime.now(timezone.utc)
        a_borrar = [c for c, v in self._codigos.items() if v.expira_en < ahora]
        for c in a_borrar:
            del self._codigos[c]
        return len(a_borrar)

    def crear_codigo(self, *, accion: AccionPendiente) -> str:
        """Genera un código nuevo asociado a la acción. Devuelve el código."""
        if accion.tipo not in ACCIONES_SENSIBLES:
            log.warning(
                "creando 2FA para acción no-sensible %r — está bien pero raro",
                accion.tipo,
            )
        self._purgar_expirados()
        # Evitar colisión (muy improbable pero defensivo)
        for _ in range(100):
            cod = self._generar()
            if cod not in self._codigos:
                break
        else:
            raise RuntimeError("no se pudo generar código único")
        self._codigos[cod] = _CodigoActivo(
            codigo=cod,
            accion=accion,
            expira_en=datetime.now(timezone.utc) +
                      timedelta(seconds=self._ttl),
        )
        log.info(
            "2FA código creado para %s (actor=%s, expira en %ds)",
            accion.tipo, accion.actor, self._ttl,
        )
        return cod

    def validar_codigo(
        self, codigo: str, *, actor: str,
    ) -> Optional[AccionPendiente]:
        """Valida un código contra el actor que lo envió.

        Returns:
            La AccionPendiente si el código matchea y no expiró.
            None en cualquier otro caso (código inválido, expirado, actor
            distinto, max intentos alcanzado).

        Side-effect: si valida correctamente, elimina el código (one-shot).
        """
        codigo = (codigo or "").strip()
        if not codigo:
            return None
        self._purgar_expirados()
        entry = self._codigos.get(codigo)
        if not entry:
            log.warning("2FA código %s no existe o expiró (actor=%s)", codigo, actor)
            return None
        # Verificar actor
        if entry.accion.actor != actor:
            entry.accion.intentos += 1
            log.warning(
                "2FA código usado por actor distinto (esperado=%s, recibido=%s)",
                entry.accion.actor, actor,
            )
            if entry.accion.intentos >= self._max_intentos:
                del self._codigos[codigo]
                log.warning("2FA código invalidado por exceso de intentos")
            return None
        # OK — eliminar (one-shot) y devolver
        accion = entry.accion
        del self._codigos[codigo]
        log.info("2FA código %s validado para %s", codigo, actor)
        return accion

    def listar_pendientes_de(self, actor: str) -> list[AccionPendiente]:
        """Acciones pendientes de un actor (sin revelar códigos)."""
        self._purgar_expirados()
        return [
            v.accion for v in self._codigos.values()
            if v.accion.actor == actor
        ]

    def cancelar_pendiente(self, codigo: str) -> bool:
        """Borra un código sin validarlo. Devuelve True si existía."""
        if codigo in self._codigos:
            del self._codigos[codigo]
            return True
        return False

    def __len__(self) -> int:
        self._purgar_expirados()
        return len(self._codigos)


__all__ = [
    "TwoFactorAuth", "AccionPendiente",
    "ACCION_ENVIAR_CFIA", "ACCION_RECHAZAR_EXP", "ACCION_APROBAR_HALT",
    "ACCION_BORRAR_EXP", "ACCION_RESET_DB", "ACCIONES_SENSIBLES",
]
