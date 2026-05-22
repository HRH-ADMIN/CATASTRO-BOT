"""Tests para FolderManager — estructura de carpetas Mega.

Todos los tests usan tmp_path como mega_root: cero efectos sobre A:\\mega real.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip(
    "win32cred",
    reason="pywin32 requerido (este proyecto sólo corre en Windows)",
)

from tests.conftest import FakeCredentialManager, TestDatabase  # noqa: E402
from src.agents.folder_manager import FolderManager  # noqa: E402
from src.core.exceptions import AgentError  # noqa: E402


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mega_root(tmp_path: Path) -> Path:
    root = tmp_path / "mega"
    root.mkdir()
    return root


@pytest.fixture
def db(tmp_path: Path):
    creds = FakeCredentialManager()
    database = TestDatabase(path=tmp_path / "test.db", credentials=creds)
    database.initialize_schema()
    return database


@pytest.fixture
def fm(db, mega_root) -> FolderManager:
    return FolderManager(db, mega_root=mega_root)


def _crear_expediente(db, tipo="segregacion", nombre_topografo="Luis Rojas Herrera"):
    return db.crear_expediente(
        numero_expediente="12345-2026",
        tipo_plano=tipo,
        nombre_topografo=nombre_topografo,
        telefono_cliente="50688880001",
        nombre_cliente="Ana Mora",
        municipalidad="San Ramón",
        actor="test",
    )


# ── Tests de creación de estructura ──────────────────────────────────────────


class TestCrearEstructura:
    def test_retorna_path_raiz(self, fm, db):
        eid = _crear_expediente(db)
        raiz = fm.crear_estructura_expediente(eid)
        assert isinstance(raiz, Path)
        assert raiz.exists()

    def test_carpetas_base_siempre_creadas(self, fm, db):
        eid = _crear_expediente(db)
        raiz = fm.crear_estructura_expediente(eid)
        for sub in ("01_CAMPO", "02_DISENO", "06_INSCRITO"):
            assert (raiz / sub).is_dir(), f"falta {sub}"

    def test_apt_r1_siempre_creada(self, fm, db):
        eid = _crear_expediente(db)
        raiz = fm.crear_estructura_expediente(eid)
        assert (raiz / "03_APT_R1" / "SUBIR").is_dir()
        assert (raiz / "03_APT_R1" / "RESPUESTA").is_dir()

    def test_segregacion_tiene_municipal(self, fm, db):
        eid = _crear_expediente(db, tipo="segregacion")
        raiz = fm.crear_estructura_expediente(eid)
        assert (raiz / "04_MUNICIPAL" / "SUBIR").is_dir()
        assert (raiz / "04_MUNICIPAL" / "RESPUESTA").is_dir()

    def test_reunion_de_fincas_tiene_municipal(self, fm, db):
        eid = _crear_expediente(db, tipo="reunion_de_fincas")
        raiz = fm.crear_estructura_expediente(eid)
        assert (raiz / "04_MUNICIPAL").is_dir()

    def test_fincas_completas_no_tiene_municipal(self, fm, db):
        eid = _crear_expediente(db, tipo="fincas_completas")
        raiz = fm.crear_estructura_expediente(eid)
        assert not (raiz / "04_MUNICIPAL").exists()

    def test_rectificacion_no_tiene_municipal(self, fm, db):
        eid = _crear_expediente(db, tipo="rectificacion")
        raiz = fm.crear_estructura_expediente(eid)
        assert not (raiz / "04_MUNICIPAL").exists()

    def test_informacion_posesoria_no_tiene_municipal(self, fm, db):
        eid = _crear_expediente(db, tipo="informacion_posesoria")
        raiz = fm.crear_estructura_expediente(eid)
        assert not (raiz / "04_MUNICIPAL").exists()

    def test_segregacion_tiene_apt_r2(self, fm, db):
        eid = _crear_expediente(db, tipo="segregacion")
        raiz = fm.crear_estructura_expediente(eid)
        assert (raiz / "05_APT_R2" / "SUBIR").is_dir()
        assert (raiz / "05_APT_R2" / "RESPUESTA").is_dir()

    def test_fincas_completas_tiene_apt_r2(self, fm, db):
        eid = _crear_expediente(db, tipo="fincas_completas")
        raiz = fm.crear_estructura_expediente(eid)
        assert (raiz / "05_APT_R2").is_dir()

    def test_rectificacion_no_tiene_apt_r2(self, fm, db):
        eid = _crear_expediente(db, tipo="rectificacion")
        raiz = fm.crear_estructura_expediente(eid)
        assert not (raiz / "05_APT_R2").exists()

    def test_informacion_posesoria_no_tiene_apt_r2(self, fm, db):
        eid = _crear_expediente(db, tipo="informacion_posesoria")
        raiz = fm.crear_estructura_expediente(eid)
        assert not (raiz / "05_APT_R2").exists()

    def test_idempotente(self, fm, db):
        """Llamar dos veces no falla ni duplica carpetas."""
        eid = _crear_expediente(db)
        raiz1 = fm.crear_estructura_expediente(eid)
        raiz2 = fm.crear_estructura_expediente(eid)
        assert raiz1 == raiz2

    def test_guarda_mega_path_en_bd(self, fm, db):
        eid = _crear_expediente(db)
        raiz = fm.crear_estructura_expediente(eid)
        stored = db.get_mega_path(eid)
        assert stored is not None
        assert "12345-2026" in stored

    def test_expediente_inexistente_lanza_error(self, fm):
        with pytest.raises(AgentError):
            fm.crear_estructura_expediente("no-existe")


# ── Tests de ruta de carpeta ──────────────────────────────────────────────────


class TestRutaCarpeta:
    def test_ruta_contiene_apellido_nombre(self, fm, db, mega_root):
        eid = _crear_expediente(db, nombre_topografo="Juan Carlos Mora Rojas")
        raiz = fm.crear_estructura_expediente(eid)
        # último apellido + primer nombre → ROJAS_JUAN
        assert "ROJAS_JUAN" in str(raiz)

    def test_ruta_contiene_numero_expediente(self, fm, db):
        eid = _crear_expediente(db)
        raiz = fm.crear_estructura_expediente(eid)
        assert "12345-2026" in str(raiz)

    def test_tipo_en_ruta_en_mayusculas(self, fm, db):
        eid = _crear_expediente(db, tipo="segregacion")
        raiz = fm.crear_estructura_expediente(eid)
        assert "SEGREGACION" in str(raiz)


# ── Tests de EXPEDIENTE.txt ────────────────────────────────────────────────────


class TestExpedienteTxt:
    def test_se_crea_expediente_txt(self, fm, db):
        eid = _crear_expediente(db)
        raiz = fm.crear_estructura_expediente(eid)
        assert (raiz / "EXPEDIENTE.txt").exists()

    def test_contiene_numero_expediente(self, fm, db):
        eid = _crear_expediente(db)
        raiz = fm.crear_estructura_expediente(eid)
        txt = (raiz / "EXPEDIENTE.txt").read_text(encoding="utf-8")
        assert "12345-2026" in txt

    def test_contiene_tipo_plano(self, fm, db):
        eid = _crear_expediente(db, tipo="segregacion")
        raiz = fm.crear_estructura_expediente(eid)
        txt = (raiz / "EXPEDIENTE.txt").read_text(encoding="utf-8")
        assert "Segregación" in txt or "segregacion" in txt.lower()

    def test_contiene_nombre_cliente(self, fm, db):
        eid = _crear_expediente(db)
        raiz = fm.crear_estructura_expediente(eid)
        txt = (raiz / "EXPEDIENTE.txt").read_text(encoding="utf-8")
        assert "Ana Mora" in txt

    def test_actualizar_expediente_txt(self, fm, db):
        eid = _crear_expediente(db)
        raiz = fm.crear_estructura_expediente(eid)
        # Cambiar estado y regenerar
        db.cambiar_estado(eid, "presentado_apt_r1", actor="test")
        fm.actualizar_expediente_txt(eid)
        txt = (raiz / "EXPEDIENTE.txt").read_text(encoding="utf-8")
        assert "presentado_apt_r1" in txt

    def test_actualizar_sin_mega_path_lanza_error(self, fm, db):
        eid = _crear_expediente(db)
        # No crear estructura → no hay mega_path
        with pytest.raises(AgentError):
            fm.actualizar_expediente_txt(eid)


# ── Tests de get_subir_folder / get_respuesta_folder ─────────────────────────


class TestGetFolders:
    def test_get_subir_apt_r1(self, fm, db):
        eid = _crear_expediente(db)
        fm.crear_estructura_expediente(eid)
        p = fm.get_subir_folder(eid, "apt_r1")
        assert p.name == "SUBIR"
        assert "03_APT_R1" in str(p)

    def test_get_respuesta_apt_r1(self, fm, db):
        eid = _crear_expediente(db)
        fm.crear_estructura_expediente(eid)
        p = fm.get_respuesta_folder(eid, "apt_r1")
        assert p.name == "RESPUESTA"

    def test_fase_invalida_lanza_error(self, fm, db):
        eid = _crear_expediente(db)
        fm.crear_estructura_expediente(eid)
        with pytest.raises(AgentError):
            fm.get_subir_folder(eid, "inexistente")

    def test_sin_mega_path_lanza_error(self, fm, db):
        eid = _crear_expediente(db)
        with pytest.raises(AgentError):
            fm.get_subir_folder(eid, "apt_r1")


# ── Tests de completar_expediente ─────────────────────────────────────────────


class TestCompletarExpediente:
    def test_mueve_a_completados(self, fm, db):
        eid = _crear_expediente(db)
        raiz = fm.crear_estructura_expediente(eid)
        destino = fm.completar_expediente(eid)
        assert not raiz.exists()
        assert destino.exists()
        anio = datetime.now(timezone.utc).year
        assert str(anio) in str(destino)

    def test_actualiza_mega_path_en_bd(self, fm, db):
        eid = _crear_expediente(db)
        fm.crear_estructura_expediente(eid)
        destino = fm.completar_expediente(eid)
        nueva_ruta = db.get_mega_path(eid)
        assert nueva_ruta is not None
        assert "COMPLETADOS" in nueva_ruta.upper()

    def test_completar_sin_mega_path_lanza_error(self, fm, db):
        eid = _crear_expediente(db)
        with pytest.raises(AgentError):
            fm.completar_expediente(eid)

    def test_completar_expediente_inexistente(self, fm):
        with pytest.raises(AgentError):
            fm.completar_expediente("no-existe")
