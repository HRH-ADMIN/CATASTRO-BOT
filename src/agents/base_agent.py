"""Clase base para agentes de catastro-bot."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.core.credential_manager import CredentialManager
    from src.core.database import Database


class BaseAgent(ABC):
    name: str = "base"

    def __init__(self, db: "Database", credentials: "CredentialManager"):
        self.db = db
        self.credentials = credentials

    @abstractmethod
    def run(self, expediente_id: str) -> None:
        ...
