"""Tests para FileManager — watchdog y validación de archivos.

Diseño:
  - Clasificación y reporte: sin archivos reales (puras funciones)
  - Validación de PDF: usa PyMuPDF para crear un PDF mínimo en tmp_path
  - Validación de ZIP: crea un ZIP real en tmp_path
  - Watchdog: se verifica start/stop (sin archivo real necesario)
  - _procesar_subir: integración con DB usando FolderManager + FileManager
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import List, Tuple
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip(
    "win32cred",
    reason="pywin32 requerido (este proyecto sólo corre en Windows)",
)

from tests.conftest import FakeCredentialManager, TestDatabase  # noqa: E402
from src.agents.file_manager import (  # noqa: E402
    FileManager,
    ValidacionResultado,
    _clasificar_pdf,
    _clasificar_respuesta,
    formatear_reporte,
    validar_pdf_anverso,
    validar_pdf_entero,
    validar_zip_shape,
)
from src.agents.folder_manager import FolderManager  # noqa: E402

fitz = pytest.importorskip("fitz", reason="PyMuPDF requerido para tests de PDF")


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


@pytest.fixture
def notificaciones() -> List[Tuple[str, str]]:
    return []


@pytest.fixture
def file_manager(db, fm, notificaciones, mega_root) -> FileManager:
    def _notify(phone, msg):
        notificaciones.append((phone, msg))

    return FileManager(
        db,
        fm,
        notify_fn=_notify,
        mega_root=mega_root,
        operador_phone="50699990000",
        delay_segundos=0,  # sin espera en tests
    )


def _crear_expediente(db, tipo="segregacion"):
    return db.crear_expediente(
        numero_expediente="TEST-001",
        tipo_plano=tipo,
        nombre_topografo="Juan Mora Rojas",
        telefono_cliente="50688880001",
        nombre_cliente="Cliente Test",
        municipalidad="San Ramón",
        actor="test",
    )


def _pdf_bn_pequeno(path: Path) -> Path:
    """Crea un PDF de 1 página B&N < 600KB."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 100), "TEST B&N", fontsize=12)
    doc.save(str(path), garbage=4, deflate=True)
    doc.close()
    return path


def _pdf_con_color(path: Path) -> Path:
    """Crea un PDF de 1 página con un elemento de color."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    # Insertar rectángulo rojo (color)
    rect = fitz.Rect(50, 50, 200, 200)
    page.draw_rect(rect, color=(1, 0, 0), fill=(1, 0, 0))
    page.insert_text((50, 250), "Con color", fontsize=12)
    doc.save(str(path), garbage=4, deflate=True)
    doc.close()
    return path


def _zip_shape_valido(path: Path) -> Path:
    """Crea un ZIP con .shp, .dbf, .shx."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mapa.shp", b"fake shp data" * 100)
        zf.writestr("mapa.dbf", b"fake dbf data" * 100)
        zf.writestr("mapa.shx", b"fake shx data" * 100)
    path.write_bytes(buf.getvalue())
    return path


def _zip_incompleto(path: Path) -> Path:
    """Crea un ZIP que falta .shx."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mapa.shp", b"data")
        zf.writestr("mapa.dbf", b"data")
    path.write_bytes(buf.getvalue())
    return path


# ── Clasificación de archivos en SUBIR\ ──────────────────────────────────────


class TestClasificarPdf:
    def _p(self, nombre: str) -> Path:
        return Path(f"/fake/SUBIR/{nombre}")

    def test_anverso_por_tag_final(self):
        p = self._p("plano_FINAL.pdf")
        assert _clasificar_pdf(p, [p]) == "anverso"

    def test_anverso_unico_pdf(self):
        p = self._p("cualquier_nombre.pdf")
        assert _clasificar_pdf(p, [p]) == "anverso"

    def test_entero(self):
        p = self._p("entero_fiscal.pdf")
        assert _clasificar_pdf(p, [p, self._p("otro.pdf")]) == "entero"

    def test_carta_agua(self):
        p = self._p("carta_agua.pdf")
        assert _clasificar_pdf(p, [p]) == "carta_agua"

    def test_croquis(self):
        p = self._p("croquis_finca.pdf")
        assert _clasificar_pdf(p, [p]) == "croquis"

    def test_visado_local(self):
        p = self._p("visado_municipal.pdf")
        assert _clasificar_pdf(p, [p]) == "visado"

    def test_desconocido_cuando_hay_varios(self):
        p1 = self._p("archivo_a.pdf")
        p2 = self._p("archivo_b.pdf")
        # sin tags → desconocido
        assert _clasificar_pdf(p1, [p1, p2]) == "desconocido"

    def test_anverso_tag_f_guion_bajo(self):
        # _f_ en el stem: "plano_f_001.pdf" → stem "plano_f_001" contiene "_f_"
        p = self._p("plano_f_001.pdf")
        assert _clasificar_pdf(p, [p, self._p("entero.pdf")]) == "anverso"


# ── Clasificación de archivos en RESPUESTA\ ───────────────────────────────────


class TestClasificarRespuesta:
    def test_minuta_apt(self):
        assert _clasificar_respuesta("minuta_2026.pdf") == "minuta_apt"

    def test_imagen_minuta_tiene_prioridad(self):
        assert _clasificar_respuesta("imagenminuta_123.pdf") == "imagen_minuta"

    def test_inscripcion_apt(self):
        assert _clasificar_respuesta("inscripcion_plano.pdf") == "inscripcion_apt"

    def test_visado_municipal(self):
        assert _clasificar_respuesta("visado_muni.pdf") == "visado_municipal"

    def test_correcciones_patron_numerico(self):
        assert _clasificar_respuesta("2026-12345-C.pdf") == "correcciones_apt"

    def test_sin_match_devuelve_none(self):
        assert _clasificar_respuesta("otro_archivo.pdf") is None

    def test_case_insensitive(self):
        assert _clasificar_respuesta("MINUTA_DEFINITIVA.PDF") == "minuta_apt"


# ── Validación de PDF anverso ─────────────────────────────────────────────────


class TestValidarPdfAnverso:
    def test_pdf_bn_pequeno_es_valido(self, tmp_path):
        p = _pdf_bn_pequeno(tmp_path / "plano.pdf")
        r = validar_pdf_anverso(p)
        assert r.ok
        assert not r.tenia_color
        assert r.final_bytes <= 600 * 1024

    def test_pdf_con_color_se_convierte(self, tmp_path):
        p = _pdf_con_color(tmp_path / "plano_color.pdf")
        r = validar_pdf_anverso(p)
        assert r.ok
        assert r.tenia_color
        # Versión procesada existe
        assert r.procesado_path is not None
        assert r.procesado_path.exists()
        assert r.procesado_path.name.endswith("_bot.pdf")

    def test_archivo_invalido_falla(self, tmp_path):
        p = tmp_path / "fake.pdf"
        p.write_bytes(b"esto no es un PDF")
        r = validar_pdf_anverso(p)
        assert not r.ok

    def test_tipo_es_anverso(self, tmp_path):
        p = _pdf_bn_pequeno(tmp_path / "plano.pdf")
        r = validar_pdf_anverso(p)
        assert r.tipo == "anverso"


# ── Validación de PDF entero ──────────────────────────────────────────────────


class TestValidarPdfEntero:
    def test_pdf_valido_ok(self, tmp_path):
        p = tmp_path / "entero.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((50, 100), "Número: 123456789012  Monto: 150,000.00")
        doc.save(str(p))
        doc.close()
        r = validar_pdf_entero(p)
        assert r.ok
        assert r.tipo == "entero"

    def test_pdf_invalido_falla(self, tmp_path):
        p = tmp_path / "entero.pdf"
        p.write_bytes(b"no es PDF")
        r = validar_pdf_entero(p)
        assert not r.ok


# ── Validación de ZIP shape ────────────────────────────────────────────────────


class TestValidarZipShape:
    def test_zip_completo_es_valido(self, tmp_path):
        p = _zip_shape_valido(tmp_path / "shape.zip")
        r = validar_zip_shape(p)
        assert r.ok
        assert r.tipo == "shape"

    def test_zip_sin_shx_falla(self, tmp_path):
        p = _zip_incompleto(tmp_path / "shape.zip")
        r = validar_zip_shape(p)
        assert not r.ok
        assert any("shx" in e for e in r.errores)

    def test_archivo_no_zip_falla(self, tmp_path):
        p = tmp_path / "fake.zip"
        p.write_bytes(b"esto no es un ZIP")
        r = validar_zip_shape(p)
        assert not r.ok

    def test_zip_grande_falla(self, tmp_path):
        p = tmp_path / "grande.zip"
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("a.shp", b"x" * (600 * 1024))
            zf.writestr("a.dbf", b"x" * (600 * 1024))
            zf.writestr("a.shx", b"x" * (600 * 1024))
        p.write_bytes(buf.getvalue())
        r = validar_zip_shape(p)
        assert not r.ok


# ── Formato del reporte WhatsApp ──────────────────────────────────────────────


class TestFormatearReporte:
    def _resultado(self, tipo, ok=True, kb=100, tenia_color=False, errores=None):
        path = Path(f"/fake/SUBIR/{tipo}.pdf")
        return ValidacionResultado(
            ok=ok,
            tipo=tipo,
            path=path,
            original_bytes=kb * 1024,
            final_bytes=kb * 1024,
            tenia_color=tenia_color,
            errores=errores or [],
            mensaje=f"{tipo} OK" if ok else f"{tipo} ERROR",
        )

    def test_contiene_cabecera(self):
        r = formatear_reporte(
            nombre_cliente="Ana Mora",
            numero_expediente="TEST-001",
            fase="03_APT_R1",
            resultados=[self._resultado("anverso")],
        )
        assert "Ana Mora" in r
        assert "TEST-001" in r
        assert "APT Ronda 1" in r

    def test_contiene_aprobar_si_todo_ok(self):
        r = formatear_reporte(
            nombre_cliente="Ana Mora",
            numero_expediente="TEST-001",
            fase="03_APT_R1",
            resultados=[self._resultado("anverso"), self._resultado("entero")],
        )
        assert "APROBAR" in r

    def test_contiene_error_si_hay_fallo(self):
        r = formatear_reporte(
            nombre_cliente="Ana Mora",
            numero_expediente="TEST-001",
            fase="03_APT_R1",
            resultados=[self._resultado("anverso", ok=False)],
        )
        assert "❌" in r

    def test_advertencia_color(self):
        r = formatear_reporte(
            nombre_cliente="Ana Mora",
            numero_expediente="TEST-001",
            fase="03_APT_R1",
            resultados=[self._resultado("anverso", tenia_color=True)],
        )
        assert "color" in r.lower() or "B&N" in r


# ── Watchdog start/stop ────────────────────────────────────────────────────────


class TestFileManagerWatchdog:
    def test_no_running_antes_de_start(self, file_manager):
        assert not file_manager.is_running()

    def test_running_despues_de_start(self, file_manager, mega_root):
        pytest.importorskip("watchdog", reason="watchdog no instalado")
        file_manager.start()
        try:
            assert file_manager.is_running()
        finally:
            file_manager.stop()

    def test_stop_idempotente(self, file_manager):
        # stop sin start no debe lanzar error
        file_manager.stop()
        assert not file_manager.is_running()

    def test_start_idempotente(self, file_manager):
        pytest.importorskip("watchdog", reason="watchdog no instalado")
        file_manager.start()
        file_manager.start()  # segunda llamada no debe fallar
        try:
            assert file_manager.is_running()
        finally:
            file_manager.stop()


# ── Integración: _procesar_subir ──────────────────────────────────────────────


class TestProcesarSubir:
    def test_procesar_subir_notifica_operador(self, db, fm, file_manager, notificaciones, tmp_path):
        """Detector de archivos en SUBIR envía reporte al operador."""
        eid = _crear_expediente(db)
        raiz = fm.crear_estructura_expediente(eid)

        subir_dir = raiz / "03_APT_R1" / "SUBIR"
        pdf_path = subir_dir / "plano_FINAL.pdf"
        _pdf_bn_pequeno(pdf_path)

        # Llamar directamente (sin watchdog real)
        file_manager._procesar_subir(pdf_path)

        # Debe haber notificado al operador
        assert len(notificaciones) == 1
        phone, msg = notificaciones[0]
        assert phone == "50699990000"
        assert "TEST-001" in msg

    def test_procesar_subir_registra_archivo_en_bd(self, db, fm, file_manager, tmp_path):
        """Archivo detectado en SUBIR queda registrado en la BD."""
        eid = _crear_expediente(db)
        raiz = fm.crear_estructura_expediente(eid)

        subir_dir = raiz / "03_APT_R1" / "SUBIR"
        pdf_path = subir_dir / "plano_FINAL.pdf"
        _pdf_bn_pequeno(pdf_path)

        file_manager._procesar_subir(pdf_path)

        archivos = db.archivos_de(eid)
        assert len(archivos) >= 1
        tipos = [a["tipo_archivo"] for a in archivos]
        assert "anverso" in tipos

    def test_procesar_subir_sin_expediente_no_crashea(self, file_manager, tmp_path):
        """Si no se identifica expediente, no hay crash."""
        pdf_path = tmp_path / "SUBIR" / "plano.pdf"
        pdf_path.parent.mkdir(parents=True)
        _pdf_bn_pequeno(pdf_path)
        # No debe lanzar excepción
        file_manager._procesar_subir(pdf_path)

    def test_procesar_respuesta_registra_en_bd(self, db, fm, file_manager, tmp_path):
        """Archivo en RESPUESTA queda registrado en la BD."""
        eid = _crear_expediente(db)
        raiz = fm.crear_estructura_expediente(eid)

        resp_dir = raiz / "03_APT_R1" / "RESPUESTA"
        minuta = resp_dir / "minuta_2026.pdf"
        _pdf_bn_pequeno(minuta)

        file_manager._procesar_respuesta(minuta)

        archivos = db.archivos_de(eid)
        tipos = [a["tipo_archivo"] for a in archivos]
        assert "minuta_apt" in tipos
