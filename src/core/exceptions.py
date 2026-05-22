"""Jerarquía de excepciones de catastro-bot."""


class CatastroBotError(Exception):
    """Base para todas las excepciones del proyecto."""


# ---------- credenciales ----------

class CredentialError(CatastroBotError):
    """Error genérico de credenciales."""


class CredentialNotFoundError(CredentialError):
    """La credencial solicitada no existe en Windows Credential Manager."""


class CredentialAccessError(CredentialError):
    """No se pudo leer/escribir/borrar la credencial."""


# ---------- base de datos ----------

class DatabaseError(CatastroBotError):
    """Error genérico de base de datos."""


class DatabaseKeyError(DatabaseError):
    """La llave provista no abre la base de datos cifrada."""


# ---------- audit log ----------

class AuditLogError(DatabaseError):
    """Error genérico del audit log."""


class AuditLogTamperError(AuditLogError):
    """La cadena de hashes del audit log no verifica — posible alteración."""


# ---------- workflows / agentes ----------

class WorkflowError(CatastroBotError):
    """Error en la ejecución de un workflow."""


class AgentError(CatastroBotError):
    """Error en la ejecución de un agente."""


# ---------- confirmaciones WhatsApp ----------

class ConfirmationError(CatastroBotError):
    """Error genérico de confirmación."""


class ConfirmationTimeoutError(ConfirmationError):
    """La confirmación venció antes de recibir respuesta del cliente."""


class ConfirmationRejectedError(ConfirmationError):
    """El cliente rechazó la acción propuesta."""
