"""Base de datos SQLCipher cifrada AES-256 para catastro-bot.

Tablas:
  - expedientes               (datos principales del trámite)
  - estados_historial         (cada cambio de estado con timestamp + actor)
  - archivos                  (archivos por fase con sha256 de integridad)
  - acciones_pendientes       (acciones que requieren confirmación WhatsApp)
  - audit_log                 (inmutable, hash chain, triggers que vetan
                              UPDATE/DELETE)

El audit log se escribe en la misma transacción que la operación auditada,
de modo que ninguna acción queda fuera del registro.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import sqlite3
import uuid
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

def _dev_mode_env() -> bool:
    return os.environ.get("CATASTRO_BOT_DEV_MODE", "").lower() in (
        "1", "true", "yes",
    )


# En modo dev (Windows sin wheels de sqlcipher3, Python 3.14, etc.) ni
# siquiera intentamos importar sqlcipher3 — la importación misma puede
# explotar con OSError/DLL-not-found, no sólo ImportError, dejando el
# módulo inservible. Sólo se carga cuando se va a usar cifrado de verdad.
if _dev_mode_env():
    sqlcipher = None  # type: ignore[assignment]
else:
    try:
        from sqlcipher3 import dbapi2 as sqlcipher
    except Exception:
        try:
            from pysqlcipher3 import dbapi2 as sqlcipher  # type: ignore[no-redef]
        except Exception:
            sqlcipher = None  # type: ignore[assignment]
            # No fallar al importar — algunos tests pasan una BD sin cifrar.
            # El error real ocurre al intentar `connect()` sin SQLCipher.

from config.settings import DATABASE_PATH, SQLCIPHER_COMPATIBILITY
from src.core.audit_log import compute_entry_hash, verify_chain
from src.core.credential_manager import CredentialManager
from src.core.exceptions import DatabaseError, DatabaseKeyError
from src.models.estado import Estado
from src.models.plano import TipoPlano

_HEX_KEY_RE = re.compile(r"^[0-9a-fA-F]{64}$")

_SCHEMA_SQL = r"""
CREATE TABLE IF NOT EXISTS expedientes (
    id                  TEXT PRIMARY KEY,
    numero_expediente   TEXT UNIQUE NOT NULL,
    tipo_plano          TEXT NOT NULL,
    nombre_topografo    TEXT NOT NULL,
    cedula_topografo    TEXT,
    telefono_cliente    TEXT NOT NULL,
    nombre_cliente      TEXT,
    estado_actual       TEXT NOT NULL,
    municipalidad       TEXT NOT NULL DEFAULT 'San Ramón',
    fecha_creacion      TEXT NOT NULL,
    fecha_actualizacion TEXT NOT NULL,
    metadata_json       TEXT,
    completado          INTEGER NOT NULL DEFAULT 0,
    cancelado           INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_expedientes_estado ON expedientes(estado_actual);
CREATE INDEX IF NOT EXISTS idx_expedientes_tipo   ON expedientes(tipo_plano);

CREATE TABLE IF NOT EXISTS estados_historial (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    expediente_id     TEXT NOT NULL,
    estado_anterior   TEXT,
    estado_nuevo      TEXT NOT NULL,
    timestamp         TEXT NOT NULL,
    actor             TEXT NOT NULL,
    detalles          TEXT,
    FOREIGN KEY (expediente_id) REFERENCES expedientes(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_estados_expediente ON estados_historial(expediente_id);

CREATE TABLE IF NOT EXISTS archivos (
    id              TEXT PRIMARY KEY,
    expediente_id   TEXT NOT NULL,
    fase            TEXT NOT NULL,
    tipo_archivo    TEXT NOT NULL,
    nombre_original TEXT NOT NULL,
    ruta_local      TEXT,
    drive_file_id   TEXT,
    sha256          TEXT NOT NULL,
    tamano_bytes    INTEGER,
    fecha_subida    TEXT NOT NULL,
    FOREIGN KEY (expediente_id) REFERENCES expedientes(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_archivos_expediente ON archivos(expediente_id);
CREATE INDEX IF NOT EXISTS idx_archivos_fase       ON archivos(fase);

CREATE TABLE IF NOT EXISTS acciones_pendientes (
    id                  TEXT PRIMARY KEY,
    expediente_id       TEXT NOT NULL,
    tipo_accion         TEXT NOT NULL,
    descripcion         TEXT NOT NULL,
    payload_json        TEXT,
    estado              TEXT NOT NULL,
    fecha_solicitud     TEXT NOT NULL,
    fecha_respuesta     TEXT,
    whatsapp_message_id TEXT,
    whatsapp_response   TEXT,
    expira_en           TEXT,
    FOREIGN KEY (expediente_id) REFERENCES expedientes(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_acciones_estado ON acciones_pendientes(estado);

CREATE TABLE IF NOT EXISTS audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,
    actor           TEXT NOT NULL,
    expediente_id   TEXT,
    accion          TEXT NOT NULL,
    detalles_json   TEXT NOT NULL,
    hash_anterior   TEXT,
    hash_actual     TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_audit_expediente ON audit_log(expediente_id);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp  ON audit_log(timestamp);

-- ── Idempotencia de notificaciones WhatsApp ───────────────────────────────────
-- Cada idMessage que Green API entrega se registra acá ANTES de procesarlo.
-- Si el bot crashea entre `procesar` y `deleteNotification`, la próxima
-- ejecución detecta el id ya registrado y hace skip — el cliente nunca verá
-- su acción resuelta dos veces.
CREATE TABLE IF NOT EXISTS whatsapp_processed (
    id_message      TEXT PRIMARY KEY,
    fecha_proceso   TEXT NOT NULL DEFAULT (datetime('now')),
    accion_id       TEXT,
    resultado       TEXT
);
CREATE INDEX IF NOT EXISTS idx_wa_processed_fecha ON whatsapp_processed(fecha_proceso);

CREATE TRIGGER IF NOT EXISTS audit_log_no_update
BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(FAIL, 'audit_log entries are immutable');
END;

CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(FAIL, 'audit_log entries are immutable');
END;

-- ── Usuarios y roles ──────────────────────────────────────────────────────────
-- Roles: admin (todo), topografo (crear+aprobar propios), asistente (crear+ver)
CREATE TABLE IF NOT EXISTS usuarios (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre      TEXT NOT NULL,
    telefono    TEXT NOT NULL UNIQUE,
    rol         TEXT NOT NULL CHECK (rol IN ('admin','topografo','asistente')),
    carne_cfia  TEXT,
    -- Datos del topógrafo para llenado automático en APT (multi-topógrafo):
    protocolo_activo      TEXT,    -- número de protocolo activo del año (CFIA)
    correo_apt            TEXT,    -- correo registrado en CFIA (puede ser distinto del de WhatsApp)
    cedula                TEXT,    -- cédula del topógrafo (para resolver desde nombre_topografo)
    cert_bcr_fingerprint  TEXT,    -- thumbprint del cert Firma Digital BCR (opcional)
    activo      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_usuarios_telefono ON usuarios(telefono);
CREATE INDEX IF NOT EXISTS idx_usuarios_rol      ON usuarios(rol);

-- ── Memoria operativa del operador ────────────────────────────────────────────
-- Reglas y silenciamientos que el operador enseña al bot vía WhatsApp.
-- Tipos:
--   regla       → texto libre que el bot incluye en reportes pre-flight
--   ignora      → patrón (string) que silencia discrepancias matcheantes
CREATE TABLE IF NOT EXISTS apt_memoria_operador (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo            TEXT NOT NULL CHECK (tipo IN ('regla','ignora')),
    patron          TEXT NOT NULL,
    descripcion     TEXT,
    operador        TEXT,         -- teléfono operador que creó
    activa          INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    -- Versionado (agregado 2026-05-14):
    caso_real       TEXT,         -- ej "TILMAN SEG-2026-003 — 2026-05-13"
    evidencia_paths TEXT,         -- JSON list de paths a screenshots
    superseded_by   INTEGER,      -- id de la regla que reemplaza a esta
    version         INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_apt_memoria_tipo ON apt_memoria_operador(tipo, activa);

-- Historia de cambios de reglas (agregado 2026-05-14)
CREATE TABLE IF NOT EXISTS apt_reglas_historia (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    regla_id        INTEGER NOT NULL,
    patron          TEXT NOT NULL,
    descripcion_old TEXT,
    descripcion_new TEXT,
    activa_old      INTEGER,
    activa_new      INTEGER,
    cambio_por      TEXT,
    razon_cambio    TEXT,
    ts              TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_reglas_historia_regla ON apt_reglas_historia(regla_id);

-- ── Lotes (procesamiento en cola serial) ──────────────────────────────────────
-- Permite ejecutar una acción (apt-crear, apt-plano) sobre varios expedientes
-- como una sola unidad lógica con confirmación 2FA única y reporte final.
-- Persistente: si el bot se reinicia a mitad de un lote, retoma desde el
-- siguiente item pendiente.
CREATE TABLE IF NOT EXISTS lotes (
    id              TEXT PRIMARY KEY,
    accion          TEXT NOT NULL,         -- 'apt-crear' | 'apt-plano'
    creado_por      TEXT NOT NULL,         -- teléfono del operador
    estado          TEXT NOT NULL,         -- 'pendiente_2fa' | 'ejecutando' | 'completo' | 'cancelado'
    ts_creado       TEXT NOT NULL,
    ts_completado   TEXT,
    resumen_json    TEXT                   -- {ok:[],fallos:[{exp,error}],cancelados:[]}
);
CREATE INDEX IF NOT EXISTS idx_lotes_estado ON lotes(estado);

CREATE TABLE IF NOT EXISTS lotes_items (
    lote_id         TEXT NOT NULL,
    expediente_id   TEXT NOT NULL,
    orden           INTEGER NOT NULL,
    estado          TEXT NOT NULL,         -- 'pendiente' | 'ejecutando' | 'ok' | 'fallo' | 'cancelado'
    error           TEXT,                  -- razón si falló
    ts_inicio       TEXT,
    ts_fin          TEXT,
    PRIMARY KEY (lote_id, expediente_id),
    FOREIGN KEY (lote_id) REFERENCES lotes(id)
);
CREATE INDEX IF NOT EXISTS idx_lotes_items_lote   ON lotes_items(lote_id, orden);
CREATE INDEX IF NOT EXISTS idx_lotes_items_estado ON lotes_items(estado);
"""

_VALID_ACCION_ESTADOS = {"pendiente", "confirmada", "rechazada", "expirada"}
_VALID_ROLES = {"admin", "topografo", "asistente"}


def _normalize_phone(phone: str) -> str:
    """Deja solo dígitos; agrega prefijo 506 para números CR de 8 dígitos."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 8:
        digits = "506" + digits
    return digits


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_hex_key(hex_key: str) -> str:
    if not _HEX_KEY_RE.match(hex_key):
        raise DatabaseKeyError("formato de llave SQLCipher inválido")
    return hex_key.lower()


class Database:
    """Cliente para la base de datos cifrada del proyecto."""

    def __init__(
        self,
        path: Path = DATABASE_PATH,
        credentials: Optional[CredentialManager] = None,
        cipher_compatibility: int = SQLCIPHER_COMPATIBILITY,
        cipher: Optional[bool] = None,
    ):
        """
        cipher : bool, optional
            True (default en producción): exige SQLCipher y cifra AES-256.
            False: usa sqlite3 estándar SIN cifrar — sólo para desarrollo
            en plataformas donde SQLCipher no está disponible (e.g. Windows
            sin compilación). Lanza warning ruidoso. Si es None, lee la
            variable de entorno CATASTRO_BOT_DEV_MODE: si vale "1"/"true",
            se desactiva el cifrado.
        """
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if cipher is None:
            cipher = not _dev_mode_env()
        self._cipher = cipher
        if cipher:
            self._cm = credentials or CredentialManager()
        else:
            self._cm = credentials  # puede ser None — no hace falta llave
            warnings.warn(
                "Database iniciado con cipher=False — la base de datos NO "
                "ESTARÁ CIFRADA. Sólo apto para desarrollo/testing en "
                "Windows sin SQLCipher. NUNCA usar en producción.",
                RuntimeWarning,
                stacklevel=2,
            )
        self._cipher_compat = cipher_compatibility
        self._key_hex: Optional[str] = None

    # ---------- conexión ----------

    def _key(self) -> str:
        if self._key_hex is None:
            self._key_hex = _validate_hex_key(self._cm.get_or_create_db_key().hex())
        return self._key_hex

    @contextlib.contextmanager
    def connect(self):
        if not self._cipher:
            # Modo dev: sqlite3 estándar SIN cifrar
            conn = sqlite3.connect(str(self.path), timeout=30.0)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA foreign_keys = ON")
                yield conn
            finally:
                conn.close()
            return

        if sqlcipher is None:
            raise ImportError(
                "SQLCipher no disponible — instalar sqlcipher3-binary "
                "o pysqlcipher3 para abrir la base de datos cifrada. "
                "En Windows sin SQLCipher: usar cipher=False (modo dev) "
                "o setear CATASTRO_BOT_DEV_MODE=1."
            )
        conn = sqlcipher.connect(str(self.path), timeout=30.0)
        conn.row_factory = sqlcipher.Row
        try:
            cur = conn.cursor()
            cur.execute(f"PRAGMA key = \"x'{self._key()}'\"")
            cur.execute(f"PRAGMA cipher_compatibility = {self._cipher_compat}")
            try:
                cur.execute("SELECT count(*) FROM sqlite_master").fetchone()
            except sqlcipher.DatabaseError as e:
                raise DatabaseKeyError("la llave no abre la base de datos") from e
            cur.execute("PRAGMA foreign_keys = ON")
            cur.execute("PRAGMA journal_mode = WAL")
            yield conn
        finally:
            conn.close()

    @contextlib.contextmanager
    def _transaction(self):
        with self.connect() as conn:
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    # ---------- esquema ----------

    def initialize_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(_SCHEMA_SQL)
            # Migraciones idempotentes para BDs existentes
            self._migrate_usuarios_columns(conn)
        with self._transaction() as conn:
            self._audit(conn, actor="system", expediente_id=None,
                        accion="schema.initialize", detalles={"version": 2})

    def _migrate_usuarios_columns(self, conn) -> None:
        """Agrega columnas multi-topógrafo si faltan en BDs existentes."""
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(usuarios)")}
        except Exception:
            return
        nuevas = [
            ("protocolo_activo",     "TEXT"),
            ("correo_apt",           "TEXT"),
            ("cedula",               "TEXT"),
            ("cert_bcr_fingerprint", "TEXT"),
        ]
        for nombre, tipo in nuevas:
            if nombre not in cols:
                try:
                    conn.execute(f"ALTER TABLE usuarios ADD COLUMN {nombre} {tipo}")
                except Exception as exc:
                    # Si falla la migración no rompemos el bot
                    import logging
                    logging.getLogger("catastro.db").warning(
                        "no se pudo agregar columna usuarios.%s: %s", nombre, exc,
                    )

    def rekey(self, new_key_bytes: bytes) -> None:
        from config.settings import CRED_DB_KEY
        new_hex = _validate_hex_key(new_key_bytes.hex())
        with self.connect() as conn:
            conn.execute(f"PRAGMA rekey = \"x'{new_hex}'\"")
        self._key_hex = new_hex
        self._cm.set_secret(CRED_DB_KEY, new_hex)

    # ---------- expedientes ----------

    def crear_expediente(
        self,
        *,
        numero_expediente: str,
        tipo_plano: str,
        nombre_topografo: str,
        telefono_cliente: str,
        cedula_topografo: Optional[str] = None,
        nombre_cliente: Optional[str] = None,
        municipalidad: str = "San Ramón",
        metadata: Optional[dict] = None,
        actor: str = "system",
    ) -> str:
        if tipo_plano not in TipoPlano.values():
            raise DatabaseError(f"tipo_plano inválido: {tipo_plano!r}")
        eid = str(uuid.uuid4())
        now = _now_iso()
        meta_json = json.dumps(metadata or {}, ensure_ascii=False)
        with self._transaction() as conn:
            conn.execute(
                """INSERT INTO expedientes
                   (id, numero_expediente, tipo_plano, nombre_topografo, cedula_topografo,
                    telefono_cliente, nombre_cliente, estado_actual, municipalidad,
                    fecha_creacion, fecha_actualizacion, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (eid, numero_expediente, tipo_plano, nombre_topografo, cedula_topografo,
                 telefono_cliente, nombre_cliente, Estado.RECIBIDO.value, municipalidad,
                 now, now, meta_json),
            )
            conn.execute(
                """INSERT INTO estados_historial
                   (expediente_id, estado_anterior, estado_nuevo, timestamp, actor, detalles)
                   VALUES (?, NULL, ?, ?, ?, ?)""",
                (eid, Estado.RECIBIDO.value, now, actor, "creación de expediente"),
            )
            self._audit(conn, actor=actor, expediente_id=eid, accion="expediente.crear",
                        detalles={"numero": numero_expediente, "tipo": tipo_plano})
        return eid

    def obtener_expediente(self, expediente_id: str) -> Optional[dict]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM expedientes WHERE id = ?", (expediente_id,)
            ).fetchone()
            return dict(row) if row else None

    def buscar_por_numero(self, numero_expediente: str) -> Optional[dict]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM expedientes WHERE numero_expediente = ?",
                (numero_expediente,),
            ).fetchone()
            return dict(row) if row else None

    def listar_expedientes(
        self,
        *,
        estado: Optional[str] = None,
        tipo_plano: Optional[str] = None,
        completados: Optional[bool] = None,
    ) -> list[dict]:
        sql = "SELECT * FROM expedientes WHERE 1=1"
        args: list[Any] = []
        if estado:
            sql += " AND estado_actual = ?"
            args.append(estado)
        if tipo_plano:
            sql += " AND tipo_plano = ?"
            args.append(tipo_plano)
        if completados is not None:
            sql += " AND completado = ?"
            args.append(1 if completados else 0)
        sql += " ORDER BY fecha_actualizacion DESC"
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]

    def cambiar_estado(
        self,
        expediente_id: str,
        nuevo_estado: str,
        *,
        actor: str,
        detalles: Optional[str] = None,
    ) -> None:
        if nuevo_estado not in Estado.values():
            raise DatabaseError(f"estado inválido: {nuevo_estado!r}")
        now = _now_iso()
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT estado_actual FROM expedientes WHERE id = ?",
                (expediente_id,),
            ).fetchone()
            if not row:
                raise DatabaseError(f"expediente {expediente_id!r} no encontrado")
            estado_anterior = row["estado_actual"]
            if estado_anterior == nuevo_estado:
                return
            conn.execute(
                """UPDATE expedientes
                      SET estado_actual = ?,
                          fecha_actualizacion = ?,
                          completado = CASE WHEN ? = ? THEN 1 ELSE completado END,
                          cancelado  = CASE WHEN ? = ? THEN 1 ELSE cancelado  END
                    WHERE id = ?""",
                (nuevo_estado, now,
                 nuevo_estado, Estado.ENTREGADO.value,
                 nuevo_estado, Estado.CANCELADO.value,
                 expediente_id),
            )
            conn.execute(
                """INSERT INTO estados_historial
                   (expediente_id, estado_anterior, estado_nuevo, timestamp, actor, detalles)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (expediente_id, estado_anterior, nuevo_estado, now, actor, detalles),
            )
            self._audit(
                conn, actor=actor, expediente_id=expediente_id,
                accion="expediente.cambiar_estado",
                detalles={"de": estado_anterior, "a": nuevo_estado, "detalles": detalles},
            )

    def historial_estados(self, expediente_id: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT * FROM estados_historial
                    WHERE expediente_id = ?
                    ORDER BY id ASC""",
                (expediente_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def expedientes_stale(self, horas: int = 48) -> list[dict]:
        """Expedientes activos sin actualización en las últimas `horas` horas.

        Excluye terminales (ENTREGADO, CANCELADO) y estados que requieren
        acciones externas sin SLA definido (CARTA_AGUA_REQUERIDA,
        APT_CORRECCIONES, APT_TRASLAPES).

        Devuelve lista de dicts con los datos completos del expediente,
        ordenados por fecha_actualizacion ASC (más antiguos primero).
        """
        from datetime import datetime, timedelta, timezone
        from src.models.estado import Estado
        umbral = (
            datetime.now(timezone.utc) - timedelta(hours=horas)
        ).isoformat()
        # Estados que tienen SLA y deben alertarse si se pasan
        estados_excluidos = list(Estado.terminales()) + [
            Estado.CARTA_AGUA_REQUERIDA.value,
            Estado.APT_CORRECCIONES.value,
            Estado.APT_TRASLAPES.value,
        ]
        placeholders = ",".join("?" * len(estados_excluidos))
        sql = f"""
            SELECT * FROM expedientes
             WHERE completado = 0
               AND cancelado  = 0
               AND fecha_actualizacion < ?
               AND estado_actual NOT IN ({placeholders})
             ORDER BY fecha_actualizacion ASC
        """
        with self.connect() as conn:
            rows = conn.execute(sql, [umbral] + estados_excluidos).fetchall()
            return [dict(r) for r in rows]

    def resumen_diario(self) -> dict:
        """Estadísticas rápidas para el reporte semanal/diario.

        Devuelve:
          activos    — expedientes no terminados ni cancelados
          bloqueados — expedientes en estado halt con SLA
          completados_semana — entregados en los últimos 7 días
          por_tipo   — dict {tipo_plano: count} de activos
        """
        from datetime import datetime, timedelta, timezone
        from src.models.estado import Estado
        ahora = datetime.now(timezone.utc)
        hace_7d = (ahora - timedelta(days=7)).isoformat()
        halts_con_sla = [
            Estado.FORMATO_INVALIDO.value,
            Estado.VISADO_RECHAZADO.value,
        ]
        with self.connect() as conn:
            activos = conn.execute(
                "SELECT COUNT(*) FROM expedientes WHERE completado=0 AND cancelado=0"
            ).fetchone()[0]

            bloqueados = conn.execute(
                f"SELECT COUNT(*) FROM expedientes WHERE estado_actual IN "
                f"({','.join('?' * len(halts_con_sla))}) AND cancelado=0",
                halts_con_sla,
            ).fetchone()[0]

            completados_semana = conn.execute(
                "SELECT COUNT(*) FROM expedientes "
                "WHERE completado=1 AND fecha_actualizacion >= ?",
                (hace_7d,),
            ).fetchone()[0]

            por_tipo_rows = conn.execute(
                "SELECT tipo_plano, COUNT(*) as cnt FROM expedientes "
                "WHERE completado=0 AND cancelado=0 GROUP BY tipo_plano"
            ).fetchall()
            por_tipo = {r["tipo_plano"]: r["cnt"] for r in por_tipo_rows}

        return {
            "activos": activos,
            "bloqueados": bloqueados,
            "completados_semana": completados_semana,
            "por_tipo": por_tipo,
        }

    def get_mega_path(self, expediente_id: str) -> Optional[str]:
        """Devuelve la ruta Mega del expediente guardada en metadata_json, o None."""
        exp = self.obtener_expediente(expediente_id)
        if not exp:
            return None
        meta = json.loads(exp.get("metadata_json") or "{}")
        return meta.get("mega_path")

    def set_mega_path(
        self,
        expediente_id: str,
        mega_path: str,
        *,
        actor: str = "system",
    ) -> None:
        """Guarda la ruta Mega del expediente en metadata_json."""
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT metadata_json FROM expedientes WHERE id = ?",
                (expediente_id,),
            ).fetchone()
            if not row:
                raise DatabaseError(f"expediente {expediente_id!r} no encontrado")
            meta = json.loads(row["metadata_json"] or "{}")
            meta["mega_path"] = mega_path
            conn.execute(
                "UPDATE expedientes SET metadata_json = ?, fecha_actualizacion = ? WHERE id = ?",
                (json.dumps(meta, ensure_ascii=False), _now_iso(), expediente_id),
            )
            self._audit(
                conn, actor=actor, expediente_id=expediente_id,
                accion="expediente.set_mega_path",
                detalles={"mega_path": mega_path},
            )

    def actualizar_metadata(
        self,
        expediente_id: str,
        nuevos_campos: dict,
        *,
        actor: str = "system",
    ) -> None:
        """Fusiona `nuevos_campos` en el metadata_json del expediente.

        Campos existentes no mencionados en `nuevos_campos` se preservan.
        Util para guardar valores como apt_tramite, numero_muni, etc.
        """
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT metadata_json FROM expedientes WHERE id = ?",
                (expediente_id,),
            ).fetchone()
            if not row:
                raise DatabaseError(f"expediente {expediente_id!r} no encontrado")
            meta = json.loads(row["metadata_json"] or "{}")
            meta.update(nuevos_campos)
            conn.execute(
                "UPDATE expedientes SET metadata_json = ?, fecha_actualizacion = ? WHERE id = ?",
                (json.dumps(meta, ensure_ascii=False), _now_iso(), expediente_id),
            )
            self._audit(
                conn, actor=actor, expediente_id=expediente_id,
                accion="expediente.actualizar_metadata",
                detalles={"campos": list(nuevos_campos.keys())},
            )

    def buscar_por_mega_path(self, mega_path: str) -> Optional[dict]:
        """Busca un expediente cuyo metadata_json["mega_path"] sea el dado.

        Útil para que el watchdog identifique el expediente a partir de la
        ruta de una carpeta detectada en Mega.
        Usa LIKE para tolerar sub-rutas (ej: la ruta incluye la subcarpeta SUBIR).
        """
        # Normaliza separadores para búsqueda robusta
        normalized = mega_path.replace("\\", "/").rstrip("/")
        with self.connect() as conn:
            # Buscar por coincidencia exacta o sub-ruta
            rows = conn.execute(
                """SELECT * FROM expedientes
                    WHERE cancelado = 0
                      AND metadata_json LIKE ?
                    ORDER BY fecha_actualizacion DESC
                    LIMIT 10""",
                (f'%"mega_path":%{normalized}%',),
            ).fetchall()
            for row in rows:
                d = dict(row)
                meta = json.loads(d.get("metadata_json") or "{}")
                stored = (meta.get("mega_path") or "").replace("\\", "/")
                if stored and normalized.startswith(stored):
                    return d
        return None

    def buscar_expedientes(
        self,
        texto: str,
        *,
        max_resultados: int = 15,
    ) -> list[dict]:
        """Búsqueda por número de expediente, nombre de topógrafo o cliente.

        No incluye expedientes cancelados. Devuelve hasta `max_resultados`
        ordenados por fecha de actualización descendente.
        """
        pat = f"%{texto}%"
        sql = """
            SELECT * FROM expedientes
             WHERE (   numero_expediente LIKE ?
                    OR nombre_topografo  LIKE ?
                    OR nombre_cliente    LIKE ?)
               AND cancelado = 0
             ORDER BY fecha_actualizacion DESC
             LIMIT ?
        """
        with self.connect() as conn:
            return [dict(r) for r in
                    conn.execute(sql, (pat, pat, pat, max_resultados)).fetchall()]

    # ---------- archivos ----------

    def registrar_archivo(
        self,
        *,
        expediente_id: str,
        fase: str,
        tipo_archivo: str,
        nombre_original: str,
        sha256: str,
        ruta_local: Optional[str] = None,
        drive_file_id: Optional[str] = None,
        tamano_bytes: Optional[int] = None,
        actor: str = "system",
    ) -> str:
        fid = str(uuid.uuid4())
        with self._transaction() as conn:
            conn.execute(
                """INSERT INTO archivos
                   (id, expediente_id, fase, tipo_archivo, nombre_original,
                    ruta_local, drive_file_id, sha256, tamano_bytes, fecha_subida)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (fid, expediente_id, fase, tipo_archivo, nombre_original,
                 ruta_local, drive_file_id, sha256, tamano_bytes, _now_iso()),
            )
            self._audit(
                conn, actor=actor, expediente_id=expediente_id,
                accion="archivo.registrar",
                detalles={"id": fid, "fase": fase, "tipo": tipo_archivo,
                          "nombre": nombre_original, "sha256": sha256},
            )
        return fid

    def archivos_de(
        self, expediente_id: str, *, fase: Optional[str] = None
    ) -> list[dict]:
        sql = "SELECT * FROM archivos WHERE expediente_id = ?"
        args: list[Any] = [expediente_id]
        if fase:
            sql += " AND fase = ?"
            args.append(fase)
        sql += " ORDER BY fecha_subida ASC"
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]

    # ---------- acciones pendientes (confirmación WhatsApp) ----------

    def crear_accion_pendiente(
        self,
        *,
        expediente_id: str,
        tipo_accion: str,
        descripcion: str,
        payload: Optional[dict] = None,
        whatsapp_message_id: Optional[str] = None,
        expira_en: Optional[str] = None,
        actor: str = "system",
    ) -> str:
        aid = str(uuid.uuid4())
        with self._transaction() as conn:
            conn.execute(
                """INSERT INTO acciones_pendientes
                   (id, expediente_id, tipo_accion, descripcion, payload_json,
                    estado, fecha_solicitud, whatsapp_message_id, expira_en)
                   VALUES (?, ?, ?, ?, ?, 'pendiente', ?, ?, ?)""",
                (aid, expediente_id, tipo_accion, descripcion,
                 json.dumps(payload or {}, ensure_ascii=False),
                 _now_iso(), whatsapp_message_id, expira_en),
            )
            self._audit(
                conn, actor=actor, expediente_id=expediente_id,
                accion="accion.solicitar",
                detalles={"id": aid, "tipo": tipo_accion, "descripcion": descripcion},
            )
        return aid

    def resolver_accion(
        self,
        accion_id: str,
        nuevo_estado: str,
        *,
        whatsapp_response: Optional[str] = None,
        actor: str = "system",
    ) -> None:
        if nuevo_estado not in (_VALID_ACCION_ESTADOS - {"pendiente"}):
            raise DatabaseError(f"estado de acción inválido: {nuevo_estado!r}")
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT expediente_id, estado FROM acciones_pendientes WHERE id = ?",
                (accion_id,),
            ).fetchone()
            if not row:
                raise DatabaseError(f"acción {accion_id!r} no encontrada")
            if row["estado"] != "pendiente":
                raise DatabaseError(
                    f"acción {accion_id!r} ya resuelta ({row['estado']})"
                )
            conn.execute(
                """UPDATE acciones_pendientes
                      SET estado = ?, fecha_respuesta = ?, whatsapp_response = ?
                    WHERE id = ?""",
                (nuevo_estado, _now_iso(), whatsapp_response, accion_id),
            )
            self._audit(
                conn, actor=actor, expediente_id=row["expediente_id"],
                accion="accion.resolver",
                detalles={"id": accion_id, "estado": nuevo_estado,
                          "respuesta": whatsapp_response},
            )

    def acciones_pendientes(
        self, *, expediente_id: Optional[str] = None
    ) -> list[dict]:
        sql = "SELECT * FROM acciones_pendientes WHERE estado = 'pendiente'"
        args: list[Any] = []
        if expediente_id:
            sql += " AND expediente_id = ?"
            args.append(expediente_id)
        sql += " ORDER BY fecha_solicitud ASC"
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]

    # ── Idempotencia notificaciones WhatsApp ──────────────────────────

    def whatsapp_message_already_processed(self, id_message: str) -> bool:
        """¿Ya vimos este idMessage de Green API?"""
        if not id_message:
            return False
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM whatsapp_processed WHERE id_message = ?",
                (id_message,),
            ).fetchone()
            return row is not None

    def mark_whatsapp_message_processed(
        self,
        id_message: str,
        *,
        accion_id: Optional[str] = None,
        resultado: Optional[str] = None,
    ) -> bool:
        """Marca un idMessage como procesado. Devuelve True si fue nuevo.

        Idempotente: si ya existía la fila, retorna False sin error.
        """
        if not id_message:
            return False
        with self._transaction() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO whatsapp_processed "
                "(id_message, accion_id, resultado) VALUES (?, ?, ?)",
                (id_message, accion_id, resultado),
            )
            return cur.rowcount > 0

    def purgar_whatsapp_processed(self, dias: int = 30) -> int:
        """Borra entradas más viejas que `dias`. Devuelve cuántas filas borró."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat()
        with self._transaction() as conn:
            cur = conn.execute(
                "DELETE FROM whatsapp_processed WHERE fecha_proceso < ?",
                (cutoff,),
            )
            return cur.rowcount

    def ultima_accion(
        self, *, expediente_id: str, tipo_accion: str
    ) -> Optional[dict]:
        """Devuelve la acción más reciente del tipo dado (cualquier estado)."""
        with self.connect() as conn:
            row = conn.execute(
                """SELECT * FROM acciones_pendientes
                    WHERE expediente_id = ? AND tipo_accion = ?
                    ORDER BY fecha_solicitud DESC
                    LIMIT 1""",
                (expediente_id, tipo_accion),
            ).fetchone()
            return dict(row) if row else None

    def accion_confirmada(
        self,
        *,
        expediente_id: str,
        tipo_accion: str,
        dentro_de_segundos: int = 86_400,
    ) -> Optional[dict]:
        """Devuelve la acción confirmada más reciente del tipo dado, si la hay
        en la ventana de tiempo. Usada como salvaguarda antes de operaciones
        irreversibles para confirmar que el cliente autorizó la acción."""
        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=dentro_de_segundos)
        ).isoformat()
        with self.connect() as conn:
            row = conn.execute(
                """SELECT * FROM acciones_pendientes
                    WHERE expediente_id = ? AND tipo_accion = ?
                      AND estado = 'confirmada'
                      AND fecha_respuesta IS NOT NULL
                      AND fecha_respuesta >= ?
                    ORDER BY fecha_respuesta DESC
                    LIMIT 1""",
                (expediente_id, tipo_accion, cutoff),
            ).fetchone()
            return dict(row) if row else None

    def invalidar_confirmacion(
        self,
        expediente_id: str,
        tipo_accion: str,
        *,
        actor: str = "system",
    ) -> int:
        """Marca como 'expirada' todas las acciones confirmadas de ese tipo.

        Se usa cuando un flujo se reinicia (p.ej. APT correcciones → re-subida):
        la confirmación anterior ya no es válida para autorizar la nueva operación.
        Retorna el número de registros actualizados.
        """
        with self._transaction() as conn:
            rows_updated = conn.execute(
                """UPDATE acciones_pendientes
                      SET estado = 'expirada',
                          fecha_respuesta = ?
                    WHERE expediente_id = ? AND tipo_accion = ?
                      AND estado = 'confirmada'""",
                (_now_iso(), expediente_id, tipo_accion),
            ).rowcount
            if rows_updated:
                self._audit(
                    conn,
                    actor=actor,
                    expediente_id=expediente_id,
                    accion="accion.invalidar",
                    detalles={
                        "tipo_accion": tipo_accion,
                        "registros": rows_updated,
                    },
                )
        return rows_updated

    def buscar_acciones(self, *, tipo_accion: str, estado: str) -> list[dict]:
        """Retorna todas las acciones del tipo y estado dados, ordenadas por fecha."""
        with self.connect() as conn:
            return [
                dict(r)
                for r in conn.execute(
                    """SELECT * FROM acciones_pendientes
                        WHERE tipo_accion = ? AND estado = ?
                        ORDER BY fecha_solicitud ASC""",
                    (tipo_accion, estado),
                ).fetchall()
            ]

    # ---------- usuarios ----------

    def crear_usuario(
        self,
        *,
        nombre: str,
        telefono: str,
        rol: str,
        carne_cfia: Optional[str] = None,
        protocolo_activo: Optional[str] = None,
        correo_apt: Optional[str] = None,
        cedula: Optional[str] = None,
        cert_bcr_fingerprint: Optional[str] = None,
        actor: str = "system",
    ) -> int:
        """Crea un usuario con rol. Devuelve el id insertado.

        Para topógrafos, los campos opcionales `protocolo_activo`, `correo_apt`,
        `cedula` y `cert_bcr_fingerprint` permiten que el bot llene APT con
        los datos correctos según el topógrafo del expediente (multi-user).

        Lanza DatabaseError si el teléfono ya existe o el rol es inválido.
        """
        if rol not in _VALID_ROLES:
            raise DatabaseError(f"rol inválido: {rol!r}. Válidos: {_VALID_ROLES}")
        tel = _normalize_phone(telefono)
        if not tel:
            raise DatabaseError("teléfono vacío")
        with self._transaction() as conn:
            try:
                conn.execute(
                    """INSERT INTO usuarios (
                        nombre, telefono, rol, carne_cfia,
                        protocolo_activo, correo_apt, cedula, cert_bcr_fingerprint
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        nombre.strip(), tel, rol, carne_cfia,
                        protocolo_activo, correo_apt, cedula, cert_bcr_fingerprint,
                    ),
                )
                uid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            except Exception as e:
                if "UNIQUE" in str(e).upper():
                    raise DatabaseError(
                        f"teléfono {tel} ya registrado"
                    ) from e
                raise
            self._audit(
                conn, actor=actor, expediente_id=None,
                accion="usuario.crear",
                detalles={"nombre": nombre, "telefono": tel, "rol": rol},
            )
        return uid

    def actualizar_usuario_apt(
        self,
        *,
        telefono: str,
        protocolo_activo: Optional[str] = None,
        correo_apt: Optional[str] = None,
        cedula: Optional[str] = None,
        cert_bcr_fingerprint: Optional[str] = None,
        actor: str = "system",
    ) -> bool:
        """Actualiza los campos APT de un usuario existente.

        Solo se actualizan los campos pasados (None → no cambia). Útil para
        ajustar el `protocolo_activo` cuando CFIA emite uno nuevo al
        topógrafo (típicamente una vez al año).

        Returns True si encontró el usuario y lo actualizó.
        """
        tel = _normalize_phone(telefono)
        if not tel:
            return False
        sets = []
        args: list[Any] = []
        if protocolo_activo is not None:
            sets.append("protocolo_activo = ?")
            args.append(protocolo_activo)
        if correo_apt is not None:
            sets.append("correo_apt = ?")
            args.append(correo_apt)
        if cedula is not None:
            sets.append("cedula = ?")
            args.append(cedula)
        if cert_bcr_fingerprint is not None:
            sets.append("cert_bcr_fingerprint = ?")
            args.append(cert_bcr_fingerprint)
        if not sets:
            return False
        args.append(tel)
        with self._transaction() as conn:
            cur = conn.execute(
                f"UPDATE usuarios SET {', '.join(sets)} WHERE telefono = ?",
                args,
            )
            if cur.rowcount > 0:
                self._audit(
                    conn, actor=actor, expediente_id=None,
                    accion="usuario.actualizar_apt",
                    detalles={"telefono": tel, "campos": [s.split(" =")[0] for s in sets]},
                )
                return True
        return False

    def obtener_usuario_por_telefono(self, telefono: str) -> Optional[dict]:
        """Busca un usuario activo por teléfono (normalizado).

        Intenta con el teléfono tal como viene y también con prefijo 506
        para cubrir distintos formatos (8 dígitos, 11 dígitos, con +, etc.).
        """
        tel = _normalize_phone(telefono)
        candidates = {tel}
        if len(tel) == 8:
            candidates.add("506" + tel)
        elif len(tel) == 11 and tel.startswith("506"):
            candidates.add(tel[3:])
        with self.connect() as conn:
            for candidate in candidates:
                row = conn.execute(
                    "SELECT * FROM usuarios WHERE telefono = ? AND activo = 1",
                    (candidate,),
                ).fetchone()
                if row:
                    return dict(row)
        return None

    def listar_usuarios(
        self,
        *,
        rol: Optional[str] = None,
        activo: Optional[bool] = None,
    ) -> list[dict]:
        sql = "SELECT * FROM usuarios WHERE 1=1"
        args: list[Any] = []
        if rol:
            sql += " AND rol = ?"
            args.append(rol)
        if activo is not None:
            sql += " AND activo = ?"
            args.append(1 if activo else 0)
        sql += " ORDER BY nombre ASC"
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]

    # ---------- memoria operativa (reglas + ignores) ----------

    def agregar_memoria_operador(
        self,
        *,
        tipo: str,           # "regla" | "ignora"
        patron: str,
        descripcion: str = "",
        operador: str = "",
        actor: str = "system",
    ) -> int:
        """Guarda una regla o un ignora del operador. Devuelve el id."""
        if tipo not in ("regla", "ignora"):
            raise DatabaseError(f"tipo inválido: {tipo!r}")
        patron = (patron or "").strip()
        if not patron:
            raise DatabaseError("patrón vacío")
        with self._transaction() as conn:
            conn.execute(
                """INSERT INTO apt_memoria_operador
                       (tipo, patron, descripcion, operador)
                   VALUES (?, ?, ?, ?)""",
                (tipo, patron, (descripcion or "").strip(), operador),
            )
            mid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            self._audit(
                conn, actor=actor, expediente_id=None,
                accion=f"apt_memoria.crear.{tipo}",
                detalles={"patron": patron, "operador": operador},
            )
        return mid

    def listar_memoria_operador(
        self, *, tipo: Optional[str] = None, activa: Optional[bool] = True,
    ) -> list[dict]:
        sql = "SELECT * FROM apt_memoria_operador WHERE 1=1"
        args: list[Any] = []
        if tipo:
            sql += " AND tipo = ?"
            args.append(tipo)
        if activa is not None:
            sql += " AND activa = ?"
            args.append(1 if activa else 0)
        sql += " ORDER BY id DESC"
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]

    def desactivar_memoria_operador(
        self, mid: int, *, actor: str = "system",
    ) -> bool:
        """Marca como inactiva una entrada de memoria (no la borra)."""
        with self._transaction() as conn:
            cur = conn.execute(
                "UPDATE apt_memoria_operador SET activa = 0 WHERE id = ?",
                (mid,),
            )
            if cur.rowcount > 0:
                self._audit(
                    conn, actor=actor, expediente_id=None,
                    accion="apt_memoria.desactivar",
                    detalles={"id": mid},
                )
                return True
        return False

    def cambiar_rol(
        self,
        telefono: str,
        nuevo_rol: str,
        *,
        actor: str = "system",
    ) -> None:
        if nuevo_rol not in _VALID_ROLES:
            raise DatabaseError(f"rol inválido: {nuevo_rol!r}")
        tel = _normalize_phone(telefono)
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT id, rol FROM usuarios WHERE telefono = ?", (tel,)
            ).fetchone()
            if not row:
                raise DatabaseError(f"usuario {tel} no encontrado")
            if row["rol"] == nuevo_rol:
                return
            conn.execute(
                "UPDATE usuarios SET rol = ? WHERE telefono = ?", (nuevo_rol, tel)
            )
            self._audit(
                conn, actor=actor, expediente_id=None,
                accion="usuario.cambiar_rol",
                detalles={"telefono": tel, "rol_anterior": row["rol"], "rol_nuevo": nuevo_rol},
            )

    def desactivar_usuario(self, telefono: str, *, actor: str = "system") -> None:
        """Desactiva un usuario (soft delete — no se borra de la BD)."""
        tel = _normalize_phone(telefono)
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT id FROM usuarios WHERE telefono = ? AND activo = 1", (tel,)
            ).fetchone()
            if not row:
                raise DatabaseError(f"usuario activo {tel} no encontrado")
            conn.execute(
                "UPDATE usuarios SET activo = 0 WHERE telefono = ?", (tel,)
            )
            self._audit(
                conn, actor=actor, expediente_id=None,
                accion="usuario.desactivar",
                detalles={"telefono": tel},
            )

    # ---------- audit log ----------

    def _audit(
        self,
        conn,
        *,
        actor: str,
        expediente_id: Optional[str],
        accion: str,
        detalles: Optional[dict] = None,
    ) -> None:
        timestamp = _now_iso()
        detalles_json = json.dumps(detalles or {}, ensure_ascii=False, sort_keys=True)
        prev = conn.execute(
            "SELECT hash_actual FROM audit_log ORDER BY id DESC LIMIT 1"
        ).fetchone()
        prev_hash = prev["hash_actual"] if prev else ""
        current_hash = compute_entry_hash(
            prev_hash=prev_hash,
            timestamp=timestamp,
            actor=actor,
            expediente_id=expediente_id,
            accion=accion,
            detalles_json=detalles_json,
        )
        conn.execute(
            """INSERT INTO audit_log
               (timestamp, actor, expediente_id, accion, detalles_json,
                hash_anterior, hash_actual)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (timestamp, actor, expediente_id, accion, detalles_json,
             prev_hash or None, current_hash),
        )

    def verify_audit_chain(self) -> int:
        """Recorre el audit_log y verifica la cadena de hashes. Devuelve filas verificadas."""
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT timestamp, actor, expediente_id, accion, detalles_json,
                          hash_anterior, hash_actual
                     FROM audit_log
                    ORDER BY id ASC"""
            ).fetchall()
            return verify_chain(dict(r) for r in rows)

    def audit_log(
        self,
        *,
        expediente_id: Optional[str] = None,
        limit: int = 1000,
    ) -> list[dict]:
        sql = "SELECT * FROM audit_log"
        args: list[Any] = []
        if expediente_id:
            sql += " WHERE expediente_id = ?"
            args.append(expediente_id)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(limit)
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]
