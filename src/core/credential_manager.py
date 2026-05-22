"""Capa de credenciales tipadas para catastro-bot.

El almacenamiento real lo provee `SecretStore` (`src/core/secret_store.py`),
que tiene dos backends intercambiables (Cred Manager per-user vs DPAPI
machine-scope). Esta clase preserva la API pública previa para no romper
los call-sites existentes, pero delega todo el I/O al store.

Los valores nunca se loggean, nunca se escriben a disco fuera del store
y nunca se incluyen en mensajes de error visibles.
"""
from __future__ import annotations

import getpass
import json
import re
import secrets

from config.settings import (
    CRED_ANTHROPIC,
    CRED_APT,
    CRED_DB_KEY,
    CRED_DRIVE_TOKEN,
    CRED_GOOGLE_OAUTH,
    CRED_GREEN_API,
    CRED_MUNI_SAN_RAMON,
    CRED_OPERATORS,
    CRED_RNP,
    CREDENTIAL_PREFIX,
)
from src.core.exceptions import (
    CredentialAccessError,
    CredentialNotFoundError,
)
from src.core.secret_store import SecretStore, get_default_store


def _phone_digits(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


class CredentialManager:
    """Capa tipada sobre `SecretStore`. Mantiene la API pública previa."""

    def __init__(
        self,
        prefix: str = CREDENTIAL_PREFIX,
        *,
        store: SecretStore | None = None,
    ):
        self.prefix = prefix
        self._store = store or get_default_store(prefix=prefix)

    @property
    def store(self) -> SecretStore:
        """Acceso al backend (útil para tests + introspección)."""
        return self._store

    # ---------- API genérica ----------

    def set_credential(self, name: str, username: str, password: str) -> None:
        """Guarda (username, password) en el store."""
        self._store.set(name, username, password)

    def get_credential(self, name: str) -> tuple[str, str]:
        """Devuelve (username, password). Lanza CredentialNotFoundError si falta."""
        return self._store.get(name)

    def delete_credential(self, name: str) -> None:
        """Borra la credencial. No falla si ya no existe."""
        self._store.delete(name)

    def list_names(self) -> list[str]:
        """Lista los nombres almacenados (sin el prefijo)."""
        return self._store.list_names()

    def exists(self, name: str) -> bool:
        return self._store.exists(name)

    # ---------- helpers para secretos simples (sin username) ----------

    def set_secret(self, name: str, value: str) -> None:
        self.set_credential(name, username="_", password=value)

    def get_secret(self, name: str) -> str:
        return self.get_credential(name)[1]

    def set_json(self, name: str, data: dict) -> None:
        self.set_secret(name, json.dumps(data, ensure_ascii=False))

    def get_json(self, name: str) -> dict:
        return json.loads(self.get_secret(name))

    # ---------- llave maestra de SQLCipher ----------

    def get_or_create_db_key(self) -> bytes:
        """Devuelve la llave de 32 bytes (256 bits), generándola en el primer arranque."""
        try:
            hex_key = self.get_secret(CRED_DB_KEY)
            return bytes.fromhex(hex_key)
        except CredentialNotFoundError:
            key = secrets.token_bytes(32)
            self.set_secret(CRED_DB_KEY, key.hex())
            return key

    def get_db_key(self) -> bytes:
        return bytes.fromhex(self.get_secret(CRED_DB_KEY))

    # ---------- credenciales tipadas ----------

    def set_apt(self, username: str, password: str) -> None:
        self.set_credential(CRED_APT, username, password)

    def get_apt(self) -> tuple[str, str]:
        return self.get_credential(CRED_APT)

    def set_muni_san_ramon(self, username: str, password: str) -> None:
        self.set_credential(CRED_MUNI_SAN_RAMON, username, password)

    def get_muni_san_ramon(self) -> tuple[str, str]:
        return self.get_credential(CRED_MUNI_SAN_RAMON)

    def set_green_api(self, instance_id: str, token: str) -> None:
        self.set_json(CRED_GREEN_API, {"instance_id": instance_id, "token": token})

    def get_green_api(self) -> dict:
        return self.get_json(CRED_GREEN_API)

    def set_google_oauth(self, client_secret_json: str) -> None:
        self.set_secret(CRED_GOOGLE_OAUTH, client_secret_json)

    def get_google_oauth(self) -> str:
        return self.get_secret(CRED_GOOGLE_OAUTH)

    def set_anthropic_key(self, api_key: str) -> None:
        self.set_secret(CRED_ANTHROPIC, api_key)

    def get_anthropic_key(self) -> str:
        return self.get_secret(CRED_ANTHROPIC)

    # ---------- tokens Google Drive (OAuth runtime) ----------

    def set_drive_token(self, token_data: dict) -> None:
        """Guarda el JSON de tokens OAuth de Google Drive (access + refresh)."""
        self.set_json(CRED_DRIVE_TOKEN, token_data)

    def get_drive_token(self) -> dict:
        """Devuelve el JSON de tokens. Lanza CredentialNotFoundError si falta."""
        return self.get_json(CRED_DRIVE_TOKEN)

    # ---------- RNP digital (portal Registro Nacional) ----------

    def set_rnp(self, username: str, password: str) -> None:
        """Guarda las credenciales del portal rnpdigital.com."""
        self.set_credential(CRED_RNP, username, password)

    def get_rnp(self) -> tuple[str, str]:
        """Devuelve (username, password). Lanza CredentialNotFoundError si falta."""
        return self.get_credential(CRED_RNP)

    # ---------- operadores autorizados ----------

    def get_operators(self) -> set[str]:
        """Devuelve los teléfonos autorizados (sólo dígitos)."""
        try:
            data = self.get_json(CRED_OPERATORS)
            return set(data.get("phones", []))
        except CredentialNotFoundError:
            return set()

    def is_operator(self, phone: str) -> bool:
        """True si el teléfono coincide con algún operador autorizado.

        Compara sólo dígitos. Acepta tanto el formato con código de país
        (506XXXXXXXX) como sin (XXXXXXXX) — uno debe ser sufijo del otro.
        """
        sender = _phone_digits(phone)
        if not sender:
            return False
        for op in self.get_operators():
            if op == sender:
                return True
            if sender.endswith(op) or op.endswith(sender):
                return True
        return False

    def add_operator(self, phone: str) -> str:
        """Añade un teléfono. Devuelve la forma normalizada almacenada."""
        digits = _phone_digits(phone)
        if len(digits) < 8:
            raise ValueError(f"teléfono inválido: {phone!r}")
        # Normalizar a formato CR: si tiene 8 dígitos, anteponer 506
        if len(digits) == 8:
            digits = "506" + digits
        ops = self.get_operators()
        ops.add(digits)
        self.set_json(CRED_OPERATORS, {"phones": sorted(ops)})
        return digits

    def remove_operator(self, phone: str) -> bool:
        digits = _phone_digits(phone)
        if len(digits) == 8:
            digits = "506" + digits
        ops = self.get_operators()
        if digits in ops:
            ops.discard(digits)
            self.set_json(CRED_OPERATORS, {"phones": sorted(ops)})
            return True
        return False


# ---------- CLI interactivo ----------

def _cli() -> None:
    """CLI mínimo para configurar credenciales: python -m src.core.credential_manager."""
    cm = CredentialManager()

    def _set_apt() -> None:
        cm.set_apt(input("usuario APT: ").strip(), getpass.getpass("password APT: "))

    def _set_muni() -> None:
        cm.set_muni_san_ramon(
            input("usuario muni San Ramón: ").strip(),
            getpass.getpass("password muni San Ramón: "),
        )

    def _set_green() -> None:
        cm.set_green_api(
            input("Green API instance_id: ").strip(),
            getpass.getpass("Green API token: "),
        )

    def _set_anthropic() -> None:
        cm.set_anthropic_key(getpass.getpass("Anthropic API key: "))

    def _add_operator() -> None:
        phone = input("teléfono operador (e.g. +50688887777): ").strip()
        normalizado = cm.add_operator(phone)
        print(f"agregado: {normalizado}")

    def _remove_operator() -> None:
        phone = input("teléfono a remover: ").strip()
        ok = cm.remove_operator(phone)
        print("removido" if ok else "no estaba en la lista")

    def _list_operators() -> None:
        ops = sorted(cm.get_operators())
        if not ops:
            print("  (ningún operador autorizado)")
            return
        for op in ops:
            print(f"  - {op}")

    def _list() -> None:
        names = cm.list_names()
        print("\n".join(f"  - {n}" for n in names) if names else "  (ninguna)")

    def _delete() -> None:
        name = input("nombre a borrar: ").strip()
        cm.delete_credential(name)

    options = {
        "1": ("APT (Catastro Nacional)", _set_apt),
        "2": ("Municipalidad de San Ramón", _set_muni),
        "3": ("Green API (WhatsApp)", _set_green),
        "4": ("Anthropic API key", _set_anthropic),
        "5": ("Agregar operador autorizado", _add_operator),
        "6": ("Remover operador", _remove_operator),
        "7": ("Listar operadores", _list_operators),
        "8": ("Listar credenciales", _list),
        "9": ("Borrar credencial", _delete),
        "0": ("Salir", None),
    }

    while True:
        print("\n--- catastro-bot · credenciales ---")
        for k, (label, _) in options.items():
            print(f"  {k}) {label}")
        choice = input("opción: ").strip()
        if choice == "0":
            break
        action = options.get(choice)
        if not action or action[1] is None:
            print("opción inválida")
            continue
        try:
            action[1]()
            print("OK")
        except KeyboardInterrupt:
            print("\ncancelado")
        except Exception as e:
            print(f"error: {e}")


if __name__ == "__main__":
    _cli()
