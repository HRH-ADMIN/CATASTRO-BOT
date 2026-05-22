"""Core: base de datos cifrada, credenciales, audit log, excepciones."""
from src.core.credential_manager import CredentialManager
from src.core.database import Database
from src.core.exceptions import (
    AuditLogTamperError,
    CatastroBotError,
    CredentialAccessError,
    CredentialError,
    CredentialNotFoundError,
    DatabaseError,
    DatabaseKeyError,
)

__all__ = [
    "CredentialManager",
    "Database",
    "CatastroBotError",
    "CredentialError",
    "CredentialNotFoundError",
    "CredentialAccessError",
    "DatabaseError",
    "DatabaseKeyError",
    "AuditLogTamperError",
]
