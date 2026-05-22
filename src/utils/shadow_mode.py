"""Shadow mode / rollout gradual — decide cuándo el bot guarda automáticamente
y cuándo pausa para que el operador apruebe manualmente.

Hay 3 niveles de trust por acción crítica:

  - **manual**         → bot llena pero NO guarda. Pausa con instrucciones.
                         Para usar en rollout inicial cuando aún no se confía
                         en que el bot acierta el 100% del tiempo.

  - **auto_if_clean**  → bot guarda automáticamente SOLO si:
                           * no hay discrepancias detectadas (RNP/TSE, protocolo, etc.)
                           * no hay anomalías reportadas (modal de error)
                         Si algo no está limpio, pausa para revisión.

  - **auto**           → bot guarda siempre. Confianza máxima — usar sólo
                         tras meses de validación.

Las 3 acciones críticas son:

  - **guardar_contrato**   (click GUARDAR del Contrato Nuevo, antes existía
                            como `--save` flag en `run_apt_crear_auto.py`)
  - **guardar_seccion_plano** (cada Guardar...() en bP1-bP7)
  - **enviar_cfia**        (BtnEnviarAgrimensura — irreversible, default manual)

Settings (env vars override defaults):
  - BOT_SAVE_MODE_CONTRATO       (default: "manual")
  - BOT_SAVE_MODE_PLANO_SECCION  (default: "auto_if_clean")
  - BOT_SAVE_MODE_ENVIAR_CFIA    (default: "manual" — irreversible)

USO:
    from src.utils.shadow_mode import should_proceed, get_mode_for_action
    if should_proceed(
        "guardar_contrato",
        discrepancias=agent.discrepancias_rnp,
        has_anomaly=False,
    ):
        page.click("#BtnGuardar")
    else:
        print("Pausa — modo manual o discrepancias presentes")
"""
from __future__ import annotations
import os
from enum import Enum
from typing import Iterable


class SaveMode(str, Enum):
    MANUAL        = "manual"
    AUTO_IF_CLEAN = "auto_if_clean"
    AUTO          = "auto"


# Acciones soportadas
ACCION_GUARDAR_CONTRATO       = "guardar_contrato"
ACCION_GUARDAR_SECCION_PLANO  = "guardar_seccion_plano"
ACCION_ENVIAR_CFIA            = "enviar_cfia"

_ACCIONES_VALIDAS = {
    ACCION_GUARDAR_CONTRATO,
    ACCION_GUARDAR_SECCION_PLANO,
    ACCION_ENVIAR_CFIA,
}


# Defaults conservadores — empezar manual / auto_if_clean para no perder
# el progreso ganado en validación humana de los primeros expedientes.
_DEFAULTS: dict[str, SaveMode] = {
    ACCION_GUARDAR_CONTRATO:      SaveMode.MANUAL,
    ACCION_GUARDAR_SECCION_PLANO: SaveMode.AUTO_IF_CLEAN,
    ACCION_ENVIAR_CFIA:           SaveMode.MANUAL,
}

# Mapeo accion → env var override
_ENV_VARS: dict[str, str] = {
    ACCION_GUARDAR_CONTRATO:      "BOT_SAVE_MODE_CONTRATO",
    ACCION_GUARDAR_SECCION_PLANO: "BOT_SAVE_MODE_PLANO_SECCION",
    ACCION_ENVIAR_CFIA:           "BOT_SAVE_MODE_ENVIAR_CFIA",
}


def get_mode_for_action(accion: str) -> SaveMode:
    """Resuelve el modo de la acción.

    Orden: env var override > settings.py override > default conservador.
    """
    if accion not in _ACCIONES_VALIDAS:
        raise ValueError(
            f"acción '{accion}' no es válida. Válidas: {_ACCIONES_VALIDAS}"
        )
    # Env var override
    env_name = _ENV_VARS[accion]
    raw = os.environ.get(env_name, "").strip().lower()
    if raw:
        try:
            return SaveMode(raw)
        except ValueError:
            pass  # ignorar valor inválido, cae al settings
    # settings.py
    try:
        from config import settings
        attr = getattr(settings, env_name, None)
        if isinstance(attr, str) and attr.strip():
            try:
                return SaveMode(attr.strip().lower())
            except ValueError:
                pass
    except Exception:
        pass
    return _DEFAULTS[accion]


def should_proceed(
    accion: str,
    *,
    discrepancias: Iterable | None = None,
    has_anomaly: bool = False,
) -> bool:
    """Decide si el bot debe proceder con la acción crítica.

    Args:
        accion: una de las constantes `ACCION_*`.
        discrepancias: lista (o iterable) de discrepancias detectadas.
            Si trae al menos un elemento, NO se considera "clean".
        has_anomaly: True si se detectó una APTAnomalyError o modal raro.

    Returns:
        True si el bot debe proceder. False si debe pausar para revisión.

    Semántica por modo:
      - MANUAL         → siempre False (operador siempre confirma)
      - AUTO_IF_CLEAN  → True solo si discrepancias=[] AND not has_anomaly
      - AUTO           → siempre True (excepto si has_anomaly — eso es
                         circuit breaker y siempre debe respetarse)
    """
    mode = get_mode_for_action(accion)
    if has_anomaly:
        # Anomalías SIEMPRE paran al bot — independiente del modo
        return False
    if mode is SaveMode.MANUAL:
        return False
    if mode is SaveMode.AUTO_IF_CLEAN:
        # Hay discrepancias → pausa
        n = len(list(discrepancias)) if discrepancias else 0
        return n == 0
    if mode is SaveMode.AUTO:
        return True
    return False


def explicar_decision(
    accion: str,
    *,
    discrepancias: Iterable | None = None,
    has_anomaly: bool = False,
) -> str:
    """Devuelve string legible explicando la decisión y el modo activo.

    Útil para logs / output del runner.
    """
    mode = get_mode_for_action(accion)
    n_disc = len(list(discrepancias)) if discrepancias else 0
    proceder = should_proceed(
        accion, discrepancias=discrepancias, has_anomaly=has_anomaly,
    )
    flag = "✅ PROCEDER" if proceder else "⏸️  PAUSA"
    motivo = ""
    if has_anomaly:
        motivo = "anomalía activa (circuit breaker)"
    elif mode is SaveMode.MANUAL:
        motivo = "modo manual (operador confirma)"
    elif mode is SaveMode.AUTO_IF_CLEAN and n_disc > 0:
        motivo = f"{n_disc} discrepancia(s) detectada(s)"
    elif mode is SaveMode.AUTO_IF_CLEAN:
        motivo = "auto_if_clean y sin discrepancias"
    elif mode is SaveMode.AUTO:
        motivo = "modo auto"
    return f"{flag} [{accion}] modo={mode.value} — {motivo}"


__all__ = [
    "SaveMode",
    "ACCION_GUARDAR_CONTRATO",
    "ACCION_GUARDAR_SECCION_PLANO",
    "ACCION_ENVIAR_CFIA",
    "get_mode_for_action",
    "should_proceed",
    "explicar_decision",
]
