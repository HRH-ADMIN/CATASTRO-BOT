"""Cadena de hashes inmutable para el audit log.

Cada entrada incluye `hash_actual = sha256(hash_anterior || timestamp ||
actor || expediente_id || accion || detalles_json)`. Junto con triggers
SQL que rechazan UPDATE/DELETE sobre `audit_log`, esto permite detectar
cualquier alteración posterior.

La escritura ocurre dentro de `Database` para que el insert del audit y
la operación auditada se cometan en la misma transacción. Este módulo
provee únicamente las funciones puras de hashing y verificación.
"""
from __future__ import annotations

import hashlib
from typing import Iterable, Optional

from src.core.exceptions import AuditLogTamperError

_SEP = "|"


def compute_entry_hash(
    *,
    prev_hash: str,
    timestamp: str,
    actor: str,
    expediente_id: Optional[str],
    accion: str,
    detalles_json: str,
) -> str:
    """Hash SHA-256 hex de una entrada de audit log."""
    payload = _SEP.join(
        [prev_hash, timestamp, actor, expediente_id or "", accion, detalles_json]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def verify_chain(rows: Iterable[dict]) -> int:
    """Verifica filas de audit_log en orden ascendente. Devuelve la cantidad verificada.

    Lanza AuditLogTamperError ante el primer fallo (cadena rota o hash incorrecto).
    """
    prev_hash = ""
    count = 0
    for r in rows:
        expected = compute_entry_hash(
            prev_hash=prev_hash,
            timestamp=r["timestamp"],
            actor=r["actor"],
            expediente_id=r.get("expediente_id"),
            accion=r["accion"],
            detalles_json=r["detalles_json"],
        )
        if expected != r["hash_actual"]:
            raise AuditLogTamperError(
                f"hash inválido en entrada con timestamp {r['timestamp']!r}"
            )
        if (r.get("hash_anterior") or "") != prev_hash:
            raise AuditLogTamperError(
                f"cadena rota en entrada con timestamp {r['timestamp']!r}"
            )
        prev_hash = r["hash_actual"]
        count += 1
    return count
