"""Estado de control runtime del bot — puente entre web y scheduler.

El bot lee este estado en cada `control_tick` (30s) y decide qué hacer.
La web (Flask) lo muta vía `write_state()` cuando un operador toca el
toggle ON/OFF. Es la única pieza de I/O sincrónica entre los dos lados.

Diseño:
  - **Archivo JSON atómico** (`data/control.json`) — no toca la BD de
    negocio (`catastro.db`) para no introducir contention.
  - **Escritura atómica**: tmp + os.replace (atómico en Windows desde 3.3).
  - **Lectura fail-safe**: si el archivo no existe, está corrupto, o falla
    el JSON parse → devuelve el último estado válido en memoria + flag
    `enabled=False`. Nunca lanza al caller (fail-closed).
  - **Cache 5s**: para evitar I/O en cada tick si el archivo no cambió.
  - **Thread-safe**: lock interno; múltiples threads leyendo OK.
  - **Sin dependencias externas**.

Schema versionado para poder migrar sin romper. `schema_version=1` para v1.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("catastro.control_state")

SCHEMA_VERSION = 1

# ── Módulos controlables individualmente ───────────────────────────────
# Si agregás un módulo nuevo, sumalo acá y a `_default_modules()`.
KNOWN_MODULES = (
    "apt",            # consulta APT, llenado contrato/plano
    "whatsapp",       # polling Green API + comandos operador
    "muni",           # IMAP municipalidad
    "drive_backup",   # backup diario a Google Drive
    "rnp",            # consultas Registro Nacional
    "scheduler",      # toggle global del scheduler (anula los demás si False)
)


def _default_modules() -> dict[str, bool]:
    return {m: True for m in KNOWN_MODULES}


@dataclass
class ControlState:
    """Estado mutable runtime. Inmutable por convención: usar `replace()`."""
    enabled: bool = True
    modules: dict[str, bool] = field(default_factory=_default_modules)
    pause_until: Optional[str] = None      # ISO8601 — pausar hasta esta hora
    reason: str = "default"
    set_by: str = "system"
    updated_at: str = ""
    heartbeat_at: str = ""
    schema_version: int = SCHEMA_VERSION

    def is_module_enabled(self, name: str) -> bool:
        """Un módulo está ON solo si el master `enabled` y su flag local lo permiten."""
        if not self.enabled:
            return False
        if self.pause_until and self._still_paused():
            return False
        return bool(self.modules.get(name, True))

    def _still_paused(self) -> bool:
        if not self.pause_until:
            return False
        try:
            dt = datetime.fromisoformat(self.pause_until.replace("Z", "+00:00"))
            return datetime.now(timezone.utc) < dt
        except Exception:
            return False

    def to_dict(self) -> dict:
        return asdict(self)


# ── Manager (singleton thread-safe) ────────────────────────────────────

class ControlStateManager:
    """Acceso thread-safe a `control.json` con cache + escritura atómica."""

    _CACHE_TTL_SECONDS = 5.0

    def __init__(self, path: Path):
        self._path = Path(path)
        self._lock = threading.RLock()
        self._cache: Optional[ControlState] = None
        self._cache_mtime: float = 0.0
        self._cache_loaded_at: float = 0.0

    # ---------- read ----------

    def read(self, *, force: bool = False) -> ControlState:
        """Devuelve el estado actual. Usa cache de 5s salvo `force=True`.

        NUNCA lanza — si el archivo está corrupto, retorna el último estado
        válido con `enabled=False` (fail-closed).
        """
        import time as _t
        with self._lock:
            now = _t.monotonic()
            cache_age = now - self._cache_loaded_at
            if (
                not force
                and self._cache is not None
                and cache_age < self._CACHE_TTL_SECONDS
            ):
                return self._cache
            try:
                state = self._read_from_disk()
                self._cache = state
                self._cache_loaded_at = now
                return state
            except FileNotFoundError:
                # Primera ejecución — crear con defaults
                default = ControlState(
                    reason="auto-initialized",
                    set_by="control_state_manager",
                    updated_at=_now_iso(),
                )
                try:
                    self._write_to_disk(default)
                except Exception:
                    log.exception("no se pudo crear control.json inicial")
                self._cache = default
                self._cache_loaded_at = now
                return default
            except Exception as exc:
                log.error(
                    "control.json ilegible (%s) — usando último estado válido + enabled=False",
                    exc,
                )
                if self._cache is not None:
                    fallback = ControlState(
                        **{**self._cache.to_dict(), "enabled": False,
                           "reason": f"fail-safe ({exc.__class__.__name__})"}
                    )
                else:
                    fallback = ControlState(
                        enabled=False,
                        reason=f"fail-safe ({exc.__class__.__name__})",
                    )
                return fallback

    def _read_from_disk(self) -> ControlState:
        raw = self._path.read_text(encoding="utf-8")
        data = json.loads(raw)
        # Defensivo: respetar schema_version
        if data.get("schema_version", 1) > SCHEMA_VERSION:
            raise ValueError(
                f"schema_version {data['schema_version']} > soportado {SCHEMA_VERSION}"
            )
        # Merge con defaults para tolerar archivos viejos sin todos los campos
        defaults = ControlState().to_dict()
        merged = {**defaults, **data}
        # Garantizar que `modules` tenga todas las keys conocidas
        merged_modules = {**_default_modules(), **(merged.get("modules") or {})}
        merged["modules"] = merged_modules
        return ControlState(**merged)

    # ---------- write ----------

    def write(
        self,
        patch: dict,
        *,
        set_by: str = "unknown",
        reason: Optional[str] = None,
    ) -> ControlState:
        """Aplica un patch al estado actual. Atómico. Invalida cache.

        `patch` es un dict parcial: solo las keys que querés cambiar.
        Por ejemplo: `{"enabled": False}` o `{"modules": {"apt": False}}`.

        El campo `modules` se mergea (no se reemplaza) para no perder otros
        módulos que no estés tocando.
        """
        with self._lock:
            current = self.read(force=True).to_dict()
            # Merge modular: si el patch trae `modules`, mergear (no reemplazar)
            if "modules" in patch:
                merged_modules = {**current["modules"], **patch["modules"]}
                patch = {**patch, "modules": merged_modules}
            new = {
                **current,
                **patch,
                "set_by":     set_by,
                "reason":     reason or patch.get("reason") or current.get("reason", ""),
                "updated_at": _now_iso(),
                "schema_version": SCHEMA_VERSION,
            }
            state = ControlState(**new)
            self._write_to_disk(state)
            self._cache = state
            import time as _t
            self._cache_loaded_at = _t.monotonic()
            log.info(
                "control_state actualizado por %s (%s) — enabled=%s",
                set_by, state.reason, state.enabled,
            )
            return state

    def _write_to_disk(self, state: ControlState) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, self._path)

    # ---------- heartbeat ----------

    def write_heartbeat(self) -> None:
        """Escribe solo el campo `heartbeat_at` sin tocar el resto.

        Llamado por el `heartbeat` job cada 30s. La web puede chequear
        si el bot está vivo comparando este timestamp contra `now`.
        """
        with self._lock:
            current = self.read(force=False).to_dict()
            current["heartbeat_at"] = _now_iso()
            state = ControlState(**current)
            self._write_to_disk(state)
            self._cache = state
            import time as _t
            self._cache_loaded_at = _t.monotonic()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── Singleton global del bot ───────────────────────────────────────────

_GLOBAL_MANAGER: Optional[ControlStateManager] = None
_GLOBAL_LOCK = threading.Lock()


def get_manager(path: Optional[Path] = None) -> ControlStateManager:
    """Devuelve el manager global, inicializándolo si hace falta.

    Llamado tanto por el bot como por el dashboard (mismo proceso = misma
    instancia, lo que da consistencia en lecturas dentro de la ventana
    de cache).
    """
    global _GLOBAL_MANAGER
    if _GLOBAL_MANAGER is not None and path is None:
        return _GLOBAL_MANAGER
    with _GLOBAL_LOCK:
        if _GLOBAL_MANAGER is None or path is not None:
            from config.settings import DATA_DIR
            _GLOBAL_MANAGER = ControlStateManager(
                path or (DATA_DIR / "control.json")
            )
    return _GLOBAL_MANAGER


__all__ = [
    "ControlState",
    "ControlStateManager",
    "get_manager",
    "KNOWN_MODULES",
    "SCHEMA_VERSION",
]
