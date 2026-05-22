"""Almacenamiento de secretos con dos backends intercambiables.

Backends:

  - **WindowsCredentialStore** — wrapper sobre Windows Credential Manager.
    Per-user: solo el SID que escribió puede leer. Ideal cuando el bot
    corre como usuario interactivo.

  - **DPAPIMachineStore** — cifra cada entrada con DPAPI usando el flag
    `CRYPTPROTECT_LOCAL_MACHINE`, persiste en `data/secrets.enc`. Cualquier
    proceso de la máquina puede leer. Ideal cuando el bot corre como
    servicio bajo `LocalSystem` / `NetworkService`.

Auto-selección (`get_default_store()`):

  1. Si la env var `CATASTRO_BOT_SECRET_BACKEND` está seteada → ese backend.
     Valores: "credman" | "dpapi".
  2. Si `data/secrets.enc` ya existe → DPAPI (data lives there).
  3. Default → "credman" (compat con código existente).

Threat model DPAPI machine-scope:
  El blob queda atado a la máquina (no portable). Cualquier proceso local
  del usuario o del sistema puede descifrar. Equivalente al threat model
  de Credential Manager para malware local. Requiere proteger el host
  (BitLocker, antivirus, control de acceso físico).
"""
from __future__ import annotations

import base64
import json
import logging
import os
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.core.exceptions import (
    CredentialAccessError,
    CredentialNotFoundError,
)

log = logging.getLogger("catastro.secret_store")


# ── Backend abstracto ──────────────────────────────────────────────────

class SecretStore(ABC):
    """API mínima para almacenar (username, password) bajo un nombre."""

    @abstractmethod
    def set(self, name: str, username: str, password: str) -> None: ...

    @abstractmethod
    def get(self, name: str) -> tuple[str, str]:
        """Devuelve (username, password). Lanza CredentialNotFoundError si falta."""
        ...

    @abstractmethod
    def delete(self, name: str) -> None:
        """Borra. No falla si no existe."""
        ...

    @abstractmethod
    def list_names(self) -> list[str]: ...

    def exists(self, name: str) -> bool:
        try:
            self.get(name)
            return True
        except CredentialNotFoundError:
            return False


# ── Backend 1: Windows Credential Manager ──────────────────────────────

class WindowsCredentialStore(SecretStore):
    """Almacena en Windows Credential Manager (per-user, CRED_PERSIST_LOCAL_MACHINE)."""

    _ERR_NOT_FOUND = 1168

    def __init__(self, prefix: str):
        self._prefix = prefix
        try:
            import pywintypes
            import win32cred
        except ImportError as exc:
            raise ImportError(
                "pywin32 requerido para WindowsCredentialStore"
            ) from exc
        self._pywintypes = pywintypes
        self._win32cred = win32cred

    def _target(self, name: str) -> str:
        return f"{self._prefix}:{name}"

    def set(self, name: str, username: str, password: str) -> None:
        target = self._target(name)
        cred = {
            "Type": self._win32cred.CRED_TYPE_GENERIC,
            "TargetName": target,
            "UserName": username,
            "CredentialBlob": password,
            "Persist": self._win32cred.CRED_PERSIST_LOCAL_MACHINE,
        }
        try:
            self._win32cred.CredWrite(cred, 0)
        except self._pywintypes.error as exc:
            raise CredentialAccessError(
                f"no se pudo escribir {target!r}: {exc.strerror}"
            ) from exc

    def get(self, name: str) -> tuple[str, str]:
        target = self._target(name)
        try:
            cred = self._win32cred.CredRead(
                target, self._win32cred.CRED_TYPE_GENERIC, 0,
            )
        except self._pywintypes.error as exc:
            if exc.winerror == self._ERR_NOT_FOUND:
                raise CredentialNotFoundError(target) from exc
            raise CredentialAccessError(
                f"no se pudo leer {target!r}: {exc.strerror}"
            ) from exc
        username = cred.get("UserName") or ""
        blob: bytes = cred.get("CredentialBlob") or b""
        password = blob.decode("utf-16-le") if blob else ""
        return username, password

    def delete(self, name: str) -> None:
        target = self._target(name)
        try:
            self._win32cred.CredDelete(
                target, self._win32cred.CRED_TYPE_GENERIC, 0,
            )
        except self._pywintypes.error as exc:
            if exc.winerror == self._ERR_NOT_FOUND:
                return
            raise CredentialAccessError(
                f"no se pudo borrar {target!r}: {exc.strerror}"
            ) from exc

    def list_names(self) -> list[str]:
        try:
            creds = self._win32cred.CredEnumerate(f"{self._prefix}:*", 0)
        except self._pywintypes.error as exc:
            if exc.winerror == self._ERR_NOT_FOUND:
                return []
            raise CredentialAccessError(
                f"enum falló: {exc.strerror}"
            ) from exc
        plen = len(self._prefix) + 1
        return [
            c["TargetName"][plen:]
            for c in creds
            if c.get("TargetName", "").startswith(f"{self._prefix}:")
        ]


# ── Backend 2: DPAPI machine-scope con archivo ──────────────────────────

_DPAPI_DESCRIPTION = "catastro-bot:secrets"
_CRYPTPROTECT_LOCAL_MACHINE = 0x4
_CRYPTPROTECT_UI_FORBIDDEN  = 0x1


class DPAPIMachineStore(SecretStore):
    """Almacena entradas en `data/secrets.enc`, cifradas con DPAPI machine-scope.

    Cada entrada es cifrada individualmente con `CryptProtectData` + flag
    `CRYPTPROTECT_LOCAL_MACHINE`. El blob resultante queda atado a la máquina
    (no portable a otra) pero legible por cualquier proceso de la máquina,
    incluyendo servicios bajo `LocalSystem`.

    Formato de archivo (JSON):
        {
          "schema_version": 1,
          "entries": {
            "<name>": {
              "username":   "<plaintext>",   // los nombres de usuario no son secretos
              "blob_b64":   "<base64>",      // CryptProtectData(password.utf-8)
              "updated_at": "<iso8601>"
            }
          }
        }
    """

    def __init__(self, file_path: Path):
        self._path = Path(file_path)
        self._lock = threading.RLock()
        try:
            import pywintypes
            import win32crypt
        except ImportError as exc:
            raise ImportError(
                "pywin32 requerido para DPAPIMachineStore"
            ) from exc
        self._pywintypes = pywintypes
        self._win32crypt = win32crypt

    # ---------- file I/O ----------

    def _load(self) -> dict:
        if not self._path.exists():
            return {"schema_version": 1, "entries": {}}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise CredentialAccessError(
                f"secrets.enc ilegible: {exc}"
            ) from exc

    def _save_atomic(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, self._path)

    # ---------- DPAPI primitives ----------

    def _encrypt(self, plaintext: str) -> str:
        """Cifra con DPAPI machine-scope. Devuelve base64."""
        try:
            blob = self._win32crypt.CryptProtectData(
                plaintext.encode("utf-8"),
                _DPAPI_DESCRIPTION,
                None,          # optional entropy — podríamos añadir por defensa
                None,
                None,
                _CRYPTPROTECT_LOCAL_MACHINE | _CRYPTPROTECT_UI_FORBIDDEN,
            )
        except self._pywintypes.error as exc:
            raise CredentialAccessError(
                f"DPAPI encrypt falló: {exc.strerror}"
            ) from exc
        return base64.b64encode(blob).decode("ascii")

    def _decrypt(self, blob_b64: str) -> str:
        try:
            blob = base64.b64decode(blob_b64)
            _desc, plain = self._win32crypt.CryptUnprotectData(
                blob, None, None, None,
                _CRYPTPROTECT_LOCAL_MACHINE | _CRYPTPROTECT_UI_FORBIDDEN,
            )
        except self._pywintypes.error as exc:
            raise CredentialAccessError(
                f"DPAPI decrypt falló: {exc.strerror}"
            ) from exc
        return plain.decode("utf-8")

    # ---------- API ----------

    def set(self, name: str, username: str, password: str) -> None:
        with self._lock:
            data = self._load()
            data.setdefault("entries", {})[name] = {
                "username":   username,
                "blob_b64":   self._encrypt(password),
                "updated_at": datetime.now(timezone.utc).isoformat(
                    timespec="seconds"
                ),
            }
            self._save_atomic(data)

    def get(self, name: str) -> tuple[str, str]:
        with self._lock:
            data = self._load()
            entry = data.get("entries", {}).get(name)
            if entry is None:
                raise CredentialNotFoundError(name)
            return entry.get("username", ""), self._decrypt(entry["blob_b64"])

    def delete(self, name: str) -> None:
        with self._lock:
            data = self._load()
            entries = data.get("entries", {})
            if name in entries:
                del entries[name]
                self._save_atomic(data)

    def list_names(self) -> list[str]:
        with self._lock:
            return sorted((self._load().get("entries") or {}).keys())


# ── Factoría ────────────────────────────────────────────────────────────

_ENV_BACKEND = "CATASTRO_BOT_SECRET_BACKEND"   # "credman" | "dpapi"


def get_default_store(
    *,
    prefix: str,
    dpapi_path: Optional[Path] = None,
) -> SecretStore:
    """Devuelve el backend según env var o autodetección.

    Reglas:
      1. Env var `CATASTRO_BOT_SECRET_BACKEND` override explícito.
      2. Si existe `data/secrets.enc` → DPAPI (data ya vive ahí).
      3. Default → WindowsCredentialStore.
    """
    explicit = (os.environ.get(_ENV_BACKEND) or "").strip().lower()
    if explicit == "dpapi":
        return DPAPIMachineStore(dpapi_path or _default_dpapi_path())
    if explicit == "credman":
        return WindowsCredentialStore(prefix=prefix)
    # Autodetección
    target = dpapi_path or _default_dpapi_path()
    if target.exists():
        log.info("secrets.enc detectado en %s — usando DPAPI", target)
        return DPAPIMachineStore(target)
    return WindowsCredentialStore(prefix=prefix)


def _default_dpapi_path() -> Path:
    # Import diferido para evitar ciclo de imports con config.settings
    from config.settings import DATA_DIR
    return DATA_DIR / "secrets.enc"


__all__ = [
    "SecretStore",
    "WindowsCredentialStore",
    "DPAPIMachineStore",
    "get_default_store",
]
