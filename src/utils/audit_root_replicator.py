"""Replicación externa del hash root del audit_log (Sprint 2 / N-10).

Cada noche, después de que `audit-verify` valida que la cadena de hashes
está intacta, replicamos el hash del último registro a dos destinos
independientes:

  1. **Email** al operador (Gmail vía SMTP, credenciales muni-san-ramon).
  2. **Drive** — append a `audit_roots.txt` en `/catastro-bot/audit-roots/`.

Lo que esto compra:
  - Si un atacante tampera con la BD local y "ajusta" todos los hashes
    para que la cadena cuadre, los hashes guardados externamente NO
    coincidirán → el operador puede detectar el tampering comparando
    la BD actual contra los emails/Drive.
  - Como los destinos son independientes (email vs Drive), comprometer
    los dos a la vez requiere comprometer DOS cuentas distintas del
    operador, no solo el dispositivo local.

Flujo:
  obtener_hash_root(db) → (audit_log_id, root_hash) o None si BD vacía
  replicar(db, credentials, drive_agent=None) → dict con resultados

Idempotente: si el último hash es el MISMO que el último registro de
`audit_root_replicas`, no replica de nuevo (no spamea email al operador
con el mismo hash diario si no hubo nueva actividad).

Plan: PLAN_MEJORAS Sprint 2 / N-10.
"""
from __future__ import annotations

import io
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_log = logging.getLogger("catastro.audit_root_replicator")

DRIVE_FOLDER = "audit-roots"
DRIVE_FILE = "audit_roots.txt"
EMAIL_SUBJECT_TPL = "[catastro-bot] Audit root {fecha}: {hash_short}"


# ───────────────────────── lectura del hash root ──────────────────────

def obtener_hash_root(db_or_path) -> Optional[dict]:
    """Devuelve {id, hash_actual, timestamp} del último audit_log, o None.

    Acepta tanto un `Database` como una ruta de BD (para tests rápidos
    sin instanciar Database completo).
    """
    if hasattr(db_or_path, "connect"):
        ctx = db_or_path.connect()
    else:
        # Path/str — abrir conexión cruda
        path = Path(db_or_path)
        if not path.exists():
            return None
        ctx = _path_ctx(path)

    with ctx as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, hash_actual, timestamp FROM audit_log "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


class _path_ctx:
    """Mini context manager para abrir sqlite3 plano cuando recibimos un Path."""
    def __init__(self, path: Path):
        self.path = path
    def __enter__(self):
        self.conn = sqlite3.connect(str(self.path))
        return self.conn
    def __exit__(self, *args):
        self.conn.close()


# ───────────────────────── replicación principal ─────────────────────

def replicar(
    db,
    credentials,
    drive_agent=None,
    *,
    force: bool = False,
    email_destinatario: Optional[str] = None,
) -> dict:
    """Replica el hash root a email + Drive. Idempotente.

    Args:
        db: instancia de Database.
        credentials: CredentialManager (para get_muni_san_ramon).
        drive_agent: instancia opcional de DriveAgent. Si None, skip Drive.
        force: si True, replica aunque el hash sea el mismo que el último
               registrado en audit_root_replicas.
        email_destinatario: override del destinatario (default = el mismo
                            usuario muni-san-ramon).

    Returns:
        dict con shape:
          {
            "ok": bool,
            "skipped": bool,        # True si no había nada nuevo
            "root_hash": str|None,
            "audit_log_id": int|None,
            "destinos": {
                "email": bool|str,  # True = enviado, str = error
                "drive": bool|str   # True = appended, str = error
            }
          }
    """
    out: dict = {
        "ok": False,
        "skipped": False,
        "root_hash": None,
        "audit_log_id": None,
        "destinos": {"email": False, "drive": False},
    }

    root = obtener_hash_root(db)
    if not root:
        _log.info("audit-root-replicate: BD vacía, nada que replicar")
        out["skipped"] = True
        out["ok"] = True  # no es error
        return out

    out["root_hash"] = root["hash_actual"]
    out["audit_log_id"] = root["id"]

    # Idempotencia: ¿el último replica registrado tiene este mismo hash?
    if not force:
        ultimo = _ultimo_replica(db)
        if ultimo and ultimo.get("root_hash") == root["hash_actual"]:
            _log.info("audit-root-replicate: hash sin cambios desde "
                      "último envío, skip")
            out["skipped"] = True
            out["ok"] = True
            return out

    # ── 1. Email ──────────────────────────────────────────────────────
    email_res = _replicar_email(
        credentials, root, destinatario=email_destinatario
    )
    out["destinos"]["email"] = email_res

    # ── 2. Drive ──────────────────────────────────────────────────────
    if drive_agent is not None:
        drive_res = _replicar_drive(drive_agent, root)
        out["destinos"]["drive"] = drive_res
    else:
        out["destinos"]["drive"] = "drive_agent_no_disponible"

    # ── 3. Registrar en BD ────────────────────────────────────────────
    al_menos_uno_ok = (
        out["destinos"]["email"] is True
        or out["destinos"]["drive"] is True
    )
    out["ok"] = al_menos_uno_ok
    _registrar(db, root, out["destinos"], al_menos_uno_ok)

    return out


# ─────────────────────── helpers privados ────────────────────────────

def _ultimo_replica(db) -> Optional[dict]:
    """Lee el último audit_root_replicas (o None)."""
    with db.connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM audit_root_replicas ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def _registrar(db, root: dict, destinos: dict, ok: bool) -> None:
    """Inserta la fila en audit_root_replicas."""
    ts = datetime.now(timezone.utc).isoformat()
    with db._transaction() as conn:
        conn.execute(
            """INSERT INTO audit_root_replicas
                   (timestamp, root_hash, audit_log_id, destinos_json, ok)
               VALUES (?, ?, ?, ?, ?)""",
            (ts, root["hash_actual"], root["id"],
             json.dumps(destinos, ensure_ascii=False), 1 if ok else 0),
        )


def _replicar_email(
    credentials,
    root: dict,
    *,
    destinatario: Optional[str] = None,
):
    """Envía el hash por email. Devuelve True o str con error."""
    try:
        from src.utils.email_digest import enviar_email_smtp
        user, password = credentials.get_muni_san_ramon()
        to = destinatario or user
        hash_short = (root["hash_actual"] or "")[:12]
        fecha = root["timestamp"][:10]  # YYYY-MM-DD
        subject = EMAIL_SUBJECT_TPL.format(fecha=fecha, hash_short=hash_short)
        body = _formato_email(root)
        ok = enviar_email_smtp(
            from_addr=user, password=password, to=to,
            subject=subject, body_text=body,
        )
        return True if ok else "smtp_send_failed"
    except Exception as exc:
        _log.exception("audit-root email falló")
        return f"{type(exc).__name__}: {str(exc)[:200]}"


def _replicar_drive(drive_agent, root: dict):
    """Append al archivo audit_roots.txt en Drive. Devuelve True o str con error."""
    try:
        linea = _formato_linea_drive(root)
        result = drive_agent.subir_audit_root(linea)
        if isinstance(result, dict) and result.get("ok"):
            return True
        return result.get("error", "drive_upload_failed") if isinstance(result, dict) else str(result)
    except Exception as exc:
        _log.exception("audit-root drive falló")
        return f"{type(exc).__name__}: {str(exc)[:200]}"


# ─────────────────────── formateadores ───────────────────────────────

def _formato_email(root: dict) -> str:
    """Cuerpo del email."""
    return (
        f"Replicación diaria del hash root del audit_log\n"
        f"==============================================\n\n"
        f"Fecha:             {datetime.now(timezone.utc).isoformat()}\n"
        f"Audit log entry:   {root['id']}\n"
        f"Timestamp registro: {root['timestamp']}\n\n"
        f"Hash:\n"
        f"  {root['hash_actual']}\n\n"
        f"GUARDAR ESTE EMAIL. Si en el futuro la BD local muestra un\n"
        f"hash distinto para esta misma entrada (id={root['id']}), eso\n"
        f"indica tampering posterior y la BD NO se debe confiar.\n\n"
        f"-- catastro-bot (Sprint 2 / N-10)\n"
    )


def _formato_linea_drive(root: dict) -> str:
    """Línea para append a audit_roots.txt en Drive (TSV)."""
    ahora = datetime.now(timezone.utc).isoformat()
    return f"{ahora}\t{root['id']}\t{root['timestamp']}\t{root['hash_actual']}\n"


# ─────────────────────── consultas para tests / dashboard ────────────

def listar_replicas(db, limit: int = 50) -> list[dict]:
    """Lista las últimas N replicaciones."""
    with db.connect() as conn:
        conn.row_factory = sqlite3.Row
        return [
            dict(r) for r in conn.execute(
                "SELECT * FROM audit_root_replicas "
                "ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        ]
