"""Backup automático de la BD del bot.

Hace una copia del archivo `data/catastro.db` a `data/backups/<timestamp>.db`
con retención configurable (default: 30 backups más recientes).

Uso típico:
  - Diario via APScheduler (`src/scheduler/tasks.py`)
  - Antes de migraciones de esquema
  - Bajo demanda via CLI: `python tools/backup_db.py`

NO incluye los archivos de `data/files/` (planos) — esos están en disco
del topógrafo. Solo `catastro.db` (estado del bot).

Si la BD está cifrada con SQLCipher, el backup se hace a nivel de archivo
(copy binario) — sigue cifrado en destino.

USO:
    from src.utils.backup_db import hacer_backup, listar_backups, podar_viejos
    path = hacer_backup()  # devuelve Path al backup creado
    podar_viejos(retener=30)  # elimina los más viejos, mantiene 30
"""
from __future__ import annotations
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger("catastro.backup")


# Defaults
DB_PATH_DEFAULT     = Path("data/catastro.db")
BACKUPS_DIR_DEFAULT = Path("data/backups")
RETENER_DEFAULT     = 30


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H_%M_%S")


def hacer_backup(
    *,
    db_path: Path = DB_PATH_DEFAULT,
    backups_dir: Path = BACKUPS_DIR_DEFAULT,
    sufijo: str = "",
) -> Optional[Path]:
    """Copia `db_path` a `backups_dir/catastro_<timestamp><sufijo>.db`.

    Args:
        db_path: ruta del archivo de BD a respaldar.
        backups_dir: carpeta destino. Se crea si no existe.
        sufijo: agregado opcional al nombre (ej. '_pre_migration').

    Returns:
        Path al archivo creado, o None si falló.
    """
    db_path = Path(db_path)
    backups_dir = Path(backups_dir)
    if not db_path.exists():
        log.warning("backup: db no existe en %s", db_path)
        return None
    try:
        backups_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        log.warning("no se pudo crear %s: %s", backups_dir, exc)
        return None
    nombre = f"catastro_{_ts()}{sufijo}.db"
    destino = backups_dir / nombre
    try:
        # copy2 preserva metadata (mtime) — útil para auditoría
        shutil.copy2(db_path, destino)
        size_kb = destino.stat().st_size / 1024
        log.info("backup creado: %s (%.1f KB)", destino, size_kb)
        return destino
    except Exception as exc:
        log.exception("error copiando BD: %s", exc)
        return None


def listar_backups(
    backups_dir: Path = BACKUPS_DIR_DEFAULT,
) -> list[Path]:
    """Lista backups ordenados de más reciente a más viejo (por mtime)."""
    backups_dir = Path(backups_dir)
    if not backups_dir.exists():
        return []
    archivos = [
        f for f in backups_dir.iterdir()
        if f.is_file() and f.name.startswith("catastro_") and f.suffix == ".db"
    ]
    return sorted(archivos, key=lambda p: p.stat().st_mtime, reverse=True)


def podar_viejos(
    *, retener: int = RETENER_DEFAULT,
    backups_dir: Path = BACKUPS_DIR_DEFAULT,
) -> list[Path]:
    """Borra los backups más viejos manteniendo los `retener` más recientes.

    Returns: lista de paths borrados.
    """
    if retener < 1:
        log.warning("retener=%d inválido, abortando poda", retener)
        return []
    todos = listar_backups(backups_dir)
    if len(todos) <= retener:
        return []
    a_borrar = todos[retener:]
    borrados: list[Path] = []
    for f in a_borrar:
        try:
            f.unlink()
            borrados.append(f)
            log.info("backup viejo eliminado: %s", f.name)
        except Exception as exc:
            log.warning("no se pudo borrar %s: %s", f, exc)
    return borrados


def backup_diario(
    *, retener: int = RETENER_DEFAULT,
    db_path: Path = DB_PATH_DEFAULT,
    backups_dir: Path = BACKUPS_DIR_DEFAULT,
) -> dict:
    """Job diario: hace backup + poda viejos. Para usar desde scheduler.

    Returns dict con `creado` (path) y `borrados` (count).
    """
    creado = hacer_backup(db_path=db_path, backups_dir=backups_dir)
    borrados = podar_viejos(retener=retener, backups_dir=backups_dir)
    return {
        "creado":   str(creado) if creado else None,
        "borrados": len(borrados),
    }


__all__ = [
    "hacer_backup", "listar_backups", "podar_viejos", "backup_diario",
    "DB_PATH_DEFAULT", "BACKUPS_DIR_DEFAULT", "RETENER_DEFAULT",
]
