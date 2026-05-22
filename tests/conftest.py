"""Fixtures compartidas para los tests de catastro-bot.

Diseño de aislamiento:
  - Cada test recibe una BD en `tmp_path`. Para evitar requerir
    sqlcipher3-binary (no tiene wheels en todas las versiones de
    Python), la fixture `db` usa una subclase TestDatabase que
    hereda toda la lógica pero sustituye la conexión por sqlite3
    estándar SIN cifrar. Es un atajo SOLO de testing — la BD de
    producción sigue siendo SQLCipher cifrada AES-256.
  - FakeCredentialManager reemplaza Windows Credential Manager
    en memoria.
  - DriveAgent apunta a `tmp_path/files` — cero efectos sobre el
    árbol real del proyecto.
  - El `reply_fn` captura mensajes en una lista — cero llamadas a
    Green API.
"""
from __future__ import annotations

import contextlib
import json
import re
import secrets
import sqlite3
from pathlib import Path

import pytest

# pywin32 sí se requiere — la cadena de imports pasa por
# src.core.credential_manager que importa win32cred al top-level.
pytest.importorskip(
    "win32cred",
    reason="pywin32 requerido (este proyecto sólo corre en Windows)",
)

from src.core.database import Database  # noqa: E402
from src.core.exceptions import CredentialNotFoundError  # noqa: E402


class TestDatabase(Database):
    """Database que usa sqlite3 estándar (sin cifrar) — solo para tests.

    Hereda toda la lógica de schema, queries y audit log. Sobrescribe
    `connect()` para evitar SQLCipher (cuyo wheel no siempre está
    disponible en el Python del entorno de desarrollo).

    Sin WAL en tests (Python 3.14 sqlite3 no consume implícitamente la
    fila que devuelve `PRAGMA journal_mode` y rompe los commits).
    """

    @contextlib.contextmanager
    def connect(self):
        conn = sqlite3.connect(str(self.path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
        finally:
            conn.close()


def _phone_digits(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


class FakeCredentialManager:
    """Reemplazo en memoria de CredentialManager para tests.

    Replica exactamente la interfaz que usan Database, DriveAgent y
    WhatsAppCommandRouter. La llave maestra de SQLCipher es 32 bytes
    aleatorios por instancia (cada test obtiene una BD totalmente
    independiente).
    """

    def __init__(self):
        self._secrets: dict[str, str] = {}
        self._operators: set[str] = set()
        self._db_key = secrets.token_bytes(32)

    # ---- llave maestra de SQLCipher ----

    def get_or_create_db_key(self) -> bytes:
        return self._db_key

    def get_db_key(self) -> bytes:
        return self._db_key

    # ---- operadores autorizados ----

    def get_operators(self) -> set[str]:
        return set(self._operators)

    def is_operator(self, phone: str) -> bool:
        sender = _phone_digits(phone)
        if not sender:
            return False
        for op in self._operators:
            if op == sender:
                return True
            if sender.endswith(op) or op.endswith(sender):
                return True
        return False

    def add_operator(self, phone: str) -> str:
        digits = _phone_digits(phone)
        if len(digits) < 8:
            raise ValueError(f"teléfono inválido: {phone!r}")
        if len(digits) == 8:
            digits = "506" + digits
        self._operators.add(digits)
        return digits

    def remove_operator(self, phone: str) -> bool:
        digits = _phone_digits(phone)
        if len(digits) == 8:
            digits = "506" + digits
        if digits in self._operators:
            self._operators.discard(digits)
            return True
        return False

    # ---- secretos genéricos ----

    def get_secret(self, name: str) -> str:
        if name not in self._secrets:
            raise CredentialNotFoundError(name)
        return self._secrets[name]

    def set_secret(self, name: str, value: str) -> None:
        self._secrets[name] = value

    def get_json(self, name: str) -> dict:
        return json.loads(self.get_secret(name))

    def set_json(self, name: str, data: dict) -> None:
        self.set_secret(name, json.dumps(data))

    # Métodos tipados que algunos componentes podrían intentar
    def get_anthropic_key(self) -> str:
        return self.get_secret("anthropic-api")

    def get_green_api(self) -> dict:
        return self.get_json("green-api")

    def get_google_oauth(self) -> str:
        # Por defecto NO está configurado — DriveAgent se queda local
        return self.get_secret("google-oauth")

    def get_drive_token(self) -> dict:
        """Tokens OAuth de Drive. CredentialNotFoundError si no hay tokens."""
        return self.get_json("drive-token")

    def set_drive_token(self, token_data: dict) -> None:
        self.set_json("drive-token", token_data)

    def get_rnp(self) -> tuple[str, str]:
        """Credenciales del portal RNP digital."""
        user = self.get_secret("rnp-user")
        pwd  = self.get_secret("rnp-pass")
        return (user, pwd)

    def set_rnp(self, username: str, password: str) -> None:
        self.set_secret("rnp-user", username)
        self.set_secret("rnp-pass", password)

    def get_apt(self) -> tuple[str, str]:
        return ("test-user", "test-pass")

    def get_muni_san_ramon(self) -> tuple[str, str]:
        return ("test-user", "test-pass")


# ---------- fixtures ----------

OPERATOR_PHONE = "50688880001"
CLIENT_PHONE = "50688881111"


@pytest.fixture
def fake_creds() -> FakeCredentialManager:
    return FakeCredentialManager()


@pytest.fixture
def operator_phone(fake_creds) -> str:
    fake_creds.add_operator(OPERATOR_PHONE)
    return OPERATOR_PHONE


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture
def db(db_path, fake_creds):
    database = TestDatabase(path=db_path, credentials=fake_creds)
    database.initialize_schema()
    return database


@pytest.fixture
def files_root(tmp_path: Path) -> Path:
    root = tmp_path / "files"
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def drive_agent(db, fake_creds, files_root):
    from src.agents.drive_agent import DriveAgent
    return DriveAgent(db, fake_creds, files_root=files_root)


@pytest.fixture
def replies() -> list[tuple[str, str]]:
    """Lista mutable que captura todas las llamadas a reply_fn."""
    return []


@pytest.fixture
def reply_fn(replies):
    def _reply(phone: str, msg: str) -> None:
        replies.append((phone, msg))
    return _reply


@pytest.fixture
def router(db, fake_creds, drive_agent, reply_fn):
    from src.agents.whatsapp_commands import WhatsAppCommandRouter
    return WhatsAppCommandRouter(
        db=db,
        credentials=fake_creds,
        drive_agent=drive_agent,
        reply_fn=reply_fn,
    )
