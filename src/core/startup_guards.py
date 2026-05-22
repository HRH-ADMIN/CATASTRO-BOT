"""Guards de arranque — chequeos que deben fallar el proceso si la
configuración está peligrosa.

Llamados desde `main.py` antes de cualquier construcción de agentes /
scheduler. Salen con `sys.exit(N)` y mensaje claro si detectan estado
inseguro. Cero side-effects salvo logging y exit.
"""
from __future__ import annotations

import logging
import os
import socket
import sys
from typing import Sequence

log = logging.getLogger("catastro.startup_guards")

# Hosts donde está permitido correr con CATASTRO_BOT_SIMULAR_APT=1.
# Override con env var (CSV): `CATASTRO_BOT_TEST_HOSTS=mi-pc-test,otro-host`.
DEFAULT_TEST_HOSTS = ("ci-runner", "test-machine")

_ENV_SIMULAR     = "CATASTRO_BOT_SIMULAR_APT"
_ENV_TEST_HOSTS  = "CATASTRO_BOT_TEST_HOSTS"
_ENV_DEV_MODE    = "CATASTRO_BOT_DEV_MODE"


def _allowed_test_hosts() -> Sequence[str]:
    raw = os.environ.get(_ENV_TEST_HOSTS, "")
    extra = [h.strip().lower() for h in raw.split(",") if h.strip()]
    return tuple(list(DEFAULT_TEST_HOSTS) + extra)


def assert_safe_simular_apt() -> None:
    """Si SIMULAR_APT=1 y el host no es de test → exit(1).

    Bug histórico documentado en `.env` (2026-05-15): SIMULAR_APT=1 hace
    que el workflow avance estados sin que CFIA haya respondido realmente.
    Si se queda encendido en producción por descuido, se corrompen
    expedientes silenciosamente.

    Reglas:
      - SIMULAR=0 → OK siempre.
      - SIMULAR=1 + hostname en whitelist test → OK.
      - SIMULAR=1 + DEV_MODE=1 → OK (modo dev explícito).
      - SIMULAR=1 en cualquier otro caso → exit(1).
    """
    simular = (os.environ.get(_ENV_SIMULAR) or "0").strip()
    if simular not in ("1", "true", "True", "yes"):
        return  # No simular — OK
    # SIMULAR ON — chequear contexto
    dev_mode = (os.environ.get(_ENV_DEV_MODE) or "0").strip() in ("1", "true", "yes")
    hostname = socket.gethostname().lower()
    allowed = _allowed_test_hosts()
    if dev_mode:
        log.warning(
            "SIMULAR_APT=1 permitido por CATASTRO_BOT_DEV_MODE=1 — "
            "NO HABILITAR EN PRODUCCIÓN"
        )
        return
    if hostname in allowed:
        log.warning(
            "SIMULAR_APT=1 permitido en host de test %r",
            hostname,
        )
        return
    # Bloquear
    msg = (
        "\n"
        "================================================================\n"
        "ABORTANDO: CATASTRO_BOT_SIMULAR_APT=1 en un host NO autorizado.\n"
        "\n"
        f"  hostname:        {hostname}\n"
        f"  hosts de test:   {', '.join(allowed) or '(ninguno)'}\n"
        f"  DEV_MODE:        {os.environ.get(_ENV_DEV_MODE, '0')}\n"
        "\n"
        "SIMULAR_APT=1 simula respuestas del Catastro Nacional sin tocar el\n"
        "portal real → si está activo en producción, los expedientes avanzan\n"
        "de estado sin confirmación CFIA y se corrompen silenciosamente.\n"
        "\n"
        "Cómo resolver:\n"
        "  1. (preferido) Editá .env y poné CATASTRO_BOT_SIMULAR_APT=0\n"
        "  2. Si REALMENTE necesitás simular en este host, agregalo a la\n"
        "     whitelist: set CATASTRO_BOT_TEST_HOSTS=" + hostname + "\n"
        "================================================================\n"
    )
    sys.stderr.write(msg)
    log.critical("Abortado por guard SIMULAR_APT en host no autorizado")
    sys.exit(1)


__all__ = ["assert_safe_simular_apt"]
