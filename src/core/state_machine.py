"""ControlStateMachine — máquina de estados explícita para módulos del bot.

Reemplaza el booleano simple de `data/control.json` (ver
`src/core/control_state.py`) con estados explícitos que el dashboard
puede mostrar como "Apagando…" / "Iniciando…" en vez de saltos binarios.

Estados:

    STOPPED ──start──▶ STARTING ──ready──▶ RUNNING
       ▲                  │                   │
       │                  │                   │
       └─stopped─ STOPPING ◀──stop───────────┘
       │                  │
       │                  ▼
       └────reset──── ERROR (puede llegar desde STARTING o STOPPING)

Transiciones permitidas:

    STOPPED  → STARTING  (start solicitado)
    STARTING → RUNNING   (boot OK)
    STARTING → ERROR     (boot falló)
    RUNNING  → STOPPING  (stop solicitado)
    STOPPING → STOPPED   (cierre OK)
    STOPPING → ERROR     (cierre falló — proceso colgado)
    ERROR    → STOPPED   (reset manual)

Cualquier otra combinación lanza `InvalidTransition`.

Persistencia: tabla `module_state` (ver docs/SCHEMA.md). Idempotencia
total: si el módulo no existe en BD, la primera lectura lo crea como
STOPPED.

Cooldown: tras un emergency_stop, el módulo queda con `cooldown_until`
en el futuro. Los `start()` programáticos durante ese período son
rechazados con `CooldownActive`. El operador puede forzar con
`force=True`.

Thread-safety: la BD SQLite es thread-safe en cada conexión; este
módulo abre/cierra conexiones por operación (sin pool propio). Lock
adicional NO necesario porque las transiciones son atómicas a nivel
fila (UPDATE WHERE condition).

Publishers SSE: cada transición exitosa publica un evento al event_bus
para que el dashboard reaccione (chip "live ✓" lo cubre).

Plan: PLAN_MEJORAS Sprint 1 / U-03 paso 2.
"""
from __future__ import annotations

import dataclasses
import logging
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

log = logging.getLogger("catastro.state_machine")


# ── Constantes ─────────────────────────────────────────────────────────

STATES = ("STOPPED", "STARTING", "RUNNING", "STOPPING", "ERROR")

# Módulos conocidos. 'global' es el master kill switch.
KNOWN_MODULES = (
    "global",
    "apt",
    "muni",
    "whatsapp",
    "scheduler",
    "drive_backup",
    "rnp",
)

# Transiciones permitidas (origen → destinos válidos).
_TRANSITIONS: dict[str, frozenset[str]] = {
    "STOPPED":  frozenset({"STARTING"}),
    "STARTING": frozenset({"RUNNING", "ERROR"}),
    "RUNNING":  frozenset({"STOPPING"}),
    "STOPPING": frozenset({"STOPPED", "ERROR"}),
    "ERROR":    frozenset({"STOPPED"}),
}

DEFAULT_COOLDOWN_SECONDS = 300  # 5 min post-emergency-stop


# ── Excepciones ────────────────────────────────────────────────────────

class StateMachineError(Exception):
    """Base para errores de la máquina de estados."""


class InvalidTransition(StateMachineError):
    """Intento de transición no permitida (ej. STOPPED → RUNNING directo)."""


class CooldownActive(StateMachineError):
    """Módulo en cooldown post-emergency-stop. Usar force=True para forzar."""


class UnknownModule(StateMachineError):
    """Módulo no está en KNOWN_MODULES."""


# ── Modelo de estado ───────────────────────────────────────────────────

@dataclasses.dataclass(frozen=True)
class ModuleState:
    """Snapshot inmutable del estado de un módulo."""

    module_name: str
    state: str
    last_transition_at: str
    last_transition_reason: Optional[str] = None
    last_transition_actor: Optional[str] = None
    last_transition_id: Optional[str] = None
    last_heartbeat_at: Optional[str] = None
    error_message: Optional[str] = None
    cooldown_until: Optional[str] = None

    def is_final(self) -> bool:
        """Estado donde la transición esperada terminó (caller puede dejar de pollear)."""
        return self.state in ("STOPPED", "RUNNING", "ERROR")

    def is_in_cooldown(self) -> bool:
        if not self.cooldown_until:
            return False
        try:
            until = datetime.fromisoformat(self.cooldown_until.replace("Z", "+00:00"))
            return datetime.now(timezone.utc) < until
        except Exception:
            return False

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


# ── Máquina de estados ─────────────────────────────────────────────────

class ControlStateMachine:
    """Máquina de estados persistente para los módulos del bot.

    Cada instancia apunta a la misma BD; no hay estado local en RAM.
    Operaciones atómicas a nivel fila gracias a SQLite.
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)

    # ---------- helpers ----------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _validate_module(module: str) -> None:
        if module not in KNOWN_MODULES:
            raise UnknownModule(
                f"Módulo desconocido: {module!r}. "
                f"Esperado uno de: {list(KNOWN_MODULES)}"
            )

    def _ensure_row(self, conn: sqlite3.Connection, module: str) -> None:
        """Si el módulo no tiene fila, la crea como STOPPED."""
        row = conn.execute(
            "SELECT 1 FROM module_state WHERE module_name = ?",
            (module,),
        ).fetchone()
        if row is None:
            conn.execute(
                """INSERT INTO module_state
                   (module_name, state, last_transition_at,
                    last_transition_reason, last_transition_actor)
                   VALUES (?, 'STOPPED', ?, 'auto-initialized', 'system')""",
                (module, self._now_iso()),
            )

    # ---------- read ----------

    def read(self, module: str) -> ModuleState:
        self._validate_module(module)
        with self._connect() as conn:
            self._ensure_row(conn, module)
            conn.commit()
            row = conn.execute(
                "SELECT * FROM module_state WHERE module_name = ?",
                (module,),
            ).fetchone()
        return ModuleState(**dict(row))

    def read_all(self) -> list[ModuleState]:
        """Snapshot de todos los módulos conocidos (creándolos si faltan)."""
        with self._connect() as conn:
            for m in KNOWN_MODULES:
                self._ensure_row(conn, m)
            conn.commit()
            rows = conn.execute(
                "SELECT * FROM module_state ORDER BY module_name"
            ).fetchall()
        return [ModuleState(**dict(r)) for r in rows]

    def get_transition(self, transition_id: str) -> Optional[ModuleState]:
        """Devuelve el módulo cuyo last_transition_id matchea (para polling)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM module_state WHERE last_transition_id = ?",
                (transition_id,),
            ).fetchone()
        return ModuleState(**dict(row)) if row else None

    # ---------- transitions ----------

    def transition(
        self,
        module: str,
        new_state: str,
        *,
        actor: str,
        reason: Optional[str] = None,
        error: Optional[str] = None,
        transition_id: Optional[str] = None,
        force: bool = False,
    ) -> ModuleState:
        """Aplica una transición. Atómica vía UPDATE … WHERE state = OLD.

        Args:
            module: módulo a transicionar.
            new_state: estado destino. Debe estar en STATES.
            actor: quién dispara (ej. 'web_dashboard', 'scheduler').
            reason: texto libre del motivo.
            error: solo cuando new_state='ERROR'.
            transition_id: si lo pasa el caller, lo usa; si no, genera uuid.
            force: ignora cooldown.

        Returns:
            ModuleState resultante.

        Raises:
            InvalidTransition: si OLD → new_state no está permitida.
            CooldownActive: si new_state == STARTING y cooldown activo.
            UnknownModule: si module no existe.
        """
        if new_state not in STATES:
            raise StateMachineError(f"Estado destino inválido: {new_state!r}")
        self._validate_module(module)

        tid = transition_id or uuid.uuid4().hex
        now = self._now_iso()

        with self._connect() as conn:
            self._ensure_row(conn, module)
            row = conn.execute(
                "SELECT * FROM module_state WHERE module_name = ?",
                (module,),
            ).fetchone()
            current_state = row["state"]

            # Validación de transición permitida
            allowed = _TRANSITIONS[current_state]
            if new_state not in allowed:
                raise InvalidTransition(
                    f"{module}: transición {current_state} → {new_state} "
                    f"no permitida (permitidas desde {current_state}: "
                    f"{sorted(allowed)})"
                )

            # Cooldown check (solo aplica al pedir STARTING)
            if new_state == "STARTING" and not force:
                cooldown = row["cooldown_until"]
                if cooldown:
                    try:
                        until = datetime.fromisoformat(
                            cooldown.replace("Z", "+00:00")
                        )
                        if datetime.now(timezone.utc) < until:
                            raise CooldownActive(
                                f"{module}: en cooldown hasta {cooldown} "
                                "(usar force=True para forzar)"
                            )
                    except CooldownActive:
                        raise
                    except Exception:
                        # Cooldown malformado — ignorarlo
                        pass

            # Resolver error_message
            new_error = error if new_state == "ERROR" else None

            conn.execute(
                """UPDATE module_state
                      SET state = ?,
                          last_transition_at = ?,
                          last_transition_reason = ?,
                          last_transition_actor = ?,
                          last_transition_id = ?,
                          error_message = ?
                    WHERE module_name = ? AND state = ?""",
                (new_state, now, reason, actor, tid, new_error,
                 module, current_state),
            )
            if conn.total_changes == 0:
                # Race condition: alguien más cambió el estado entre nuestro
                # read y nuestro write.
                conn.rollback()
                raise InvalidTransition(
                    f"{module}: race condition — estado cambió entre read y write"
                )
            conn.commit()

            new_row = conn.execute(
                "SELECT * FROM module_state WHERE module_name = ?",
                (module,),
            ).fetchone()

        new_module_state = ModuleState(**dict(new_row))

        log.info(
            "[state_machine] %s: %s → %s (actor=%s reason=%s tid=%s)",
            module, current_state, new_state, actor, reason, tid,
        )

        # Publisher SSE — best-effort
        try:
            from src.utils.event_bus import publish
            publish({
                "type": "module_state_changed",
                "module": module,
                "from": current_state,
                "to": new_state,
                "actor": actor,
                "reason": reason,
                "transition_id": tid,
                "error": new_error,
            })
        except Exception:
            pass

        return new_module_state

    # ---------- helpers de alto nivel ----------

    def start(
        self,
        module: str,
        *,
        actor: str,
        reason: Optional[str] = None,
        force: bool = False,
    ) -> ModuleState:
        """Inicia transición STOPPED → STARTING. Genera transition_id nuevo."""
        return self.transition(
            module, "STARTING",
            actor=actor, reason=reason, force=force,
        )

    def mark_running(
        self,
        module: str,
        *,
        actor: str = "system",
        transition_id: Optional[str] = None,
    ) -> ModuleState:
        """STARTING → RUNNING (boot completado OK)."""
        return self.transition(
            module, "RUNNING",
            actor=actor, transition_id=transition_id,
        )

    def stop(
        self,
        module: str,
        *,
        actor: str,
        reason: Optional[str] = None,
    ) -> ModuleState:
        """RUNNING → STOPPING. Genera transition_id nuevo."""
        return self.transition(
            module, "STOPPING",
            actor=actor, reason=reason,
        )

    def mark_stopped(
        self,
        module: str,
        *,
        actor: str = "system",
        transition_id: Optional[str] = None,
    ) -> ModuleState:
        """STOPPING → STOPPED (cierre OK)."""
        return self.transition(
            module, "STOPPED",
            actor=actor, transition_id=transition_id,
        )

    def mark_error(
        self,
        module: str,
        *,
        actor: str,
        error_message: str,
        transition_id: Optional[str] = None,
    ) -> ModuleState:
        """STARTING/STOPPING → ERROR."""
        return self.transition(
            module, "ERROR",
            actor=actor, error=error_message,
            transition_id=transition_id,
        )

    def reset_error(
        self,
        module: str,
        *,
        actor: str,
        reason: Optional[str] = None,
    ) -> ModuleState:
        """ERROR → STOPPED (reset manual)."""
        return self.transition(
            module, "STOPPED",
            actor=actor, reason=reason or "manual reset from ERROR",
        )

    # ---------- emergency stop ----------

    def emergency_stop(
        self,
        *,
        actor: str,
        reason: str = "manual emergency stop",
        cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
        modules: Optional[Iterable[str]] = None,
    ) -> list[ModuleState]:
        """Kill switch global.

        Para cada módulo:
          - Si está en RUNNING: transiciona a STOPPING (caller debe luego
            matar el proceso real + llamar mark_stopped).
          - Si está en STARTING: marca como ERROR.
          - Si está en STOPPING o ERROR: solo aplica cooldown.
          - Si está en STOPPED: solo aplica cooldown.

        Setea cooldown_until en todas las filas afectadas (incluso STOPPED)
        para bloquear reinicios durante la ventana.

        Returns:
            list[ModuleState] con el estado final de cada módulo.
        """
        modules = list(modules) if modules else list(KNOWN_MODULES)
        cooldown_until = (
            datetime.now(timezone.utc) + timedelta(seconds=cooldown_seconds)
        ).isoformat()
        now = self._now_iso()
        tid = uuid.uuid4().hex
        results: list[ModuleState] = []

        with self._connect() as conn:
            for module in modules:
                self._validate_module(module)
                self._ensure_row(conn, module)
                row = conn.execute(
                    "SELECT * FROM module_state WHERE module_name = ?",
                    (module,),
                ).fetchone()
                cur = row["state"]

                if cur == "RUNNING":
                    next_state = "STOPPING"
                    err = None
                elif cur == "STARTING":
                    next_state = "ERROR"
                    err = "emergency_stop interrupted boot"
                else:
                    # STOPPED, STOPPING, ERROR — solo cooldown, no transición
                    next_state = cur
                    err = row["error_message"]

                conn.execute(
                    """UPDATE module_state
                          SET state = ?,
                              last_transition_at = ?,
                              last_transition_reason = ?,
                              last_transition_actor = ?,
                              last_transition_id = ?,
                              cooldown_until = ?,
                              error_message = ?
                        WHERE module_name = ?""",
                    (next_state, now, reason, actor, tid,
                     cooldown_until, err, module),
                )
            conn.commit()
            rows = conn.execute(
                "SELECT * FROM module_state WHERE module_name IN "
                f"({','.join('?' * len(modules))}) ORDER BY module_name",
                modules,
            ).fetchall()
            results = [ModuleState(**dict(r)) for r in rows]

        log.warning(
            "[state_machine] EMERGENCY STOP por %s (%s) — cooldown=%ds tid=%s",
            actor, reason, cooldown_seconds, tid,
        )

        # Publisher SSE
        try:
            from src.utils.event_bus import publish
            publish({
                "type": "emergency_stop",
                "actor": actor,
                "reason": reason,
                "cooldown_until": cooldown_until,
                "transition_id": tid,
                "modules": [r.module_name for r in results],
            })
        except Exception:
            pass

        return results

    # ---------- heartbeat (opcional, para workers que reportan vida) ----------

    def write_heartbeat(self, module: str) -> None:
        """Actualiza last_heartbeat_at del módulo. No cambia state."""
        self._validate_module(module)
        with self._connect() as conn:
            self._ensure_row(conn, module)
            conn.execute(
                "UPDATE module_state SET last_heartbeat_at = ? "
                "WHERE module_name = ?",
                (self._now_iso(), module),
            )
            conn.commit()


# ── Singleton del proceso ──────────────────────────────────────────────

_GLOBAL_SM: Optional[ControlStateMachine] = None


def get_state_machine(db_path: Optional[Path] = None) -> ControlStateMachine:
    """Devuelve el singleton de la máquina de estados del proceso."""
    global _GLOBAL_SM
    if _GLOBAL_SM is None or db_path is not None:
        if db_path is None:
            from config.settings import DATABASE_PATH
            db_path = DATABASE_PATH
        _GLOBAL_SM = ControlStateMachine(db_path)
    return _GLOBAL_SM


def reset_state_machine_for_testing() -> None:
    global _GLOBAL_SM
    _GLOBAL_SM = None


__all__ = [
    "ControlStateMachine",
    "ModuleState",
    "STATES",
    "KNOWN_MODULES",
    "DEFAULT_COOLDOWN_SECONDS",
    "InvalidTransition",
    "CooldownActive",
    "UnknownModule",
    "StateMachineError",
    "get_state_machine",
    "reset_state_machine_for_testing",
]
