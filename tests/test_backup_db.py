"""Tests del módulo backup_db."""
from __future__ import annotations
import time
from pathlib import Path

from src.utils.backup_db import (
    hacer_backup, listar_backups, podar_viejos, backup_diario,
)


def _crear_db_fake(path: Path, size_bytes: int = 1024) -> Path:
    path.write_bytes(b"x" * size_bytes)
    return path


class TestHacerBackup:
    def test_copia_archivo(self, tmp_path):
        db = _crear_db_fake(tmp_path / "catastro.db", 512)
        backups = tmp_path / "backups"
        result = hacer_backup(db_path=db, backups_dir=backups)
        assert result is not None
        assert result.exists()
        assert result.parent == backups
        assert result.read_bytes() == db.read_bytes()

    def test_nombre_con_timestamp(self, tmp_path):
        db = _crear_db_fake(tmp_path / "catastro.db")
        result = hacer_backup(db_path=db, backups_dir=tmp_path / "b")
        assert result.name.startswith("catastro_")
        assert result.suffix == ".db"

    def test_sufijo_personalizado(self, tmp_path):
        db = _crear_db_fake(tmp_path / "catastro.db")
        result = hacer_backup(
            db_path=db, backups_dir=tmp_path / "b",
            sufijo="_pre_migration",
        )
        assert "_pre_migration" in result.name

    def test_db_no_existe(self, tmp_path):
        result = hacer_backup(
            db_path=tmp_path / "no_existe.db",
            backups_dir=tmp_path / "b",
        )
        assert result is None

    def test_crea_dir_destino(self, tmp_path):
        db = _crear_db_fake(tmp_path / "catastro.db")
        destino = tmp_path / "nueva" / "carpeta" / "backups"
        result = hacer_backup(db_path=db, backups_dir=destino)
        assert result is not None
        assert destino.exists()


class TestListarBackups:
    def test_vacio(self, tmp_path):
        assert listar_backups(tmp_path / "no_existe") == []

    def test_orden_descendente_por_mtime(self, tmp_path):
        backups = tmp_path / "b"
        backups.mkdir()
        # Crear 3 backups con timestamps distintos
        a = backups / "catastro_2026-01-01.db"
        a.write_bytes(b"x")
        time.sleep(0.05)
        b = backups / "catastro_2026-02-01.db"
        b.write_bytes(b"x")
        time.sleep(0.05)
        c = backups / "catastro_2026-03-01.db"
        c.write_bytes(b"x")
        lista = listar_backups(backups)
        # Más reciente primero
        assert lista[0].name == "catastro_2026-03-01.db"
        assert lista[-1].name == "catastro_2026-01-01.db"

    def test_ignora_archivos_no_backup(self, tmp_path):
        backups = tmp_path / "b"
        backups.mkdir()
        (backups / "readme.txt").write_text("x")
        (backups / "otro.sql").write_text("x")
        (backups / "catastro_real.db").write_bytes(b"x")
        lista = listar_backups(backups)
        assert len(lista) == 1
        assert lista[0].name == "catastro_real.db"


class TestPodarViejos:
    def test_no_borra_si_menos_que_retener(self, tmp_path):
        backups = tmp_path / "b"
        backups.mkdir()
        (backups / "catastro_1.db").write_bytes(b"x")
        (backups / "catastro_2.db").write_bytes(b"x")
        borrados = podar_viejos(retener=5, backups_dir=backups)
        assert borrados == []
        # Todos siguen ahí
        assert len(listar_backups(backups)) == 2

    def test_borra_los_mas_viejos(self, tmp_path):
        backups = tmp_path / "b"
        backups.mkdir()
        archivos = []
        for i in range(5):
            f = backups / f"catastro_2026-0{i+1}-01.db"
            f.write_bytes(b"x")
            archivos.append(f)
            time.sleep(0.02)
        # Mantener 2 → borra los 3 más viejos
        borrados = podar_viejos(retener=2, backups_dir=backups)
        assert len(borrados) == 3
        restantes = listar_backups(backups)
        assert len(restantes) == 2

    def test_retener_0_no_hace_nada(self, tmp_path):
        """Salvaguarda: retener < 1 no debe borrar nada."""
        backups = tmp_path / "b"
        backups.mkdir()
        (backups / "catastro_1.db").write_bytes(b"x")
        borrados = podar_viejos(retener=0, backups_dir=backups)
        assert borrados == []


class TestBackupDiario:
    def test_crea_y_poda(self, tmp_path):
        db = _crear_db_fake(tmp_path / "catastro.db")
        backups = tmp_path / "b"
        backups.mkdir()
        # Pre-cargar 5 viejos
        for i in range(5):
            (backups / f"catastro_old_{i}.db").write_bytes(b"x")
            time.sleep(0.02)
        # Backup diario con retener=3 → crea 1, borra los excedentes
        res = backup_diario(retener=3, db_path=db, backups_dir=backups)
        assert res["creado"]
        # Después de crear: tenemos 6 → poda hasta 3 → borra 3
        assert res["borrados"] == 3
        assert len(listar_backups(backups)) == 3
