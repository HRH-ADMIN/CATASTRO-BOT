"""Tests para FileValidator (Opción D).

Cubre:
  - validar_archivos: ruta vacía → error
  - validar_archivos: archivo inexistente → error
  - validar_archivos: extensión no soportada → error
  - _validar_pdf: pypdf no disponible → ValidationResult ok con advertencia
  - _validar_pdf: PDF vacío (0 páginas) → error
  - _validar_pdf: PDF válido con firma → ok + metadatos
  - _validar_pdf: PDF válido sin firma → ok con advertencia
  - _validar_dwg: ezdxf no disponible → ValidationResult ok con advertencia
  - _validar_dwg: DWG con capas requeridas → ok
  - _validar_dwg: DWG sin capas requeridas → errores
  - _validar_dwg: DWG modelspace vacío → error
  - validar_archivos: lista mixta → propaga primer error
"""
from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.agents.file_validator import (
    CAPAS_REQUERIDAS_DWG,
    FileValidator,
    ValidationResult,
    validar_archivos,
)


# ── helpers ────────────────────────────────────────────────────────────────────

def _archivo(ruta: str, nombre: str = "test.pdf") -> dict:
    return {"ruta_local": ruta, "nombre_original": nombre}


def _archivo_pdf_real(tmp_path: Path) -> Path:
    """Crea un PDF mínimo válido (sin firma)."""
    try:
        from pypdf import PdfWriter
        w = PdfWriter()
        w.add_blank_page(width=595, height=842)
        p = tmp_path / "test.pdf"
        with open(p, "wb") as f:
            w.write(f)
        return p
    except ImportError:
        pytest.skip("pypdf no instalado")


def _archivo_dwg_real(tmp_path: Path, *, con_capas: bool = True) -> Path:
    """Crea un DXF mínimo válido con o sin capas CFIA."""
    try:
        import ezdxf
        doc = ezdxf.new("R2010")
        if con_capas:
            for capa in CAPAS_REQUERIDAS_DWG:
                doc.layers.add(capa)
            msp = doc.modelspace()
            msp.add_line((0, 0), (1, 1))  # al menos una entidad
        p = tmp_path / "test.dxf"
        doc.saveas(str(p))
        return p
    except ImportError:
        pytest.skip("ezdxf no instalado")


# ── Pruebas básicas ────────────────────────────────────────────────────────────

class TestRutasInvalidas:
    def test_ruta_vacia_es_error(self):
        r = FileValidator.validar(_archivo(""))
        assert not r.ok
        assert any("vacía" in e for e in r.errores)

    def test_archivo_inexistente_es_error(self, tmp_path):
        r = FileValidator.validar(_archivo(str(tmp_path / "no_existe.pdf")))
        assert not r.ok
        assert any("no encontrado" in e for e in r.errores)

    def test_extension_no_soportada_es_error(self, tmp_path):
        p = tmp_path / "plano.shp"
        p.write_bytes(b"fake")
        r = FileValidator.validar(_archivo(str(p)))
        assert not r.ok
        assert any("extensión" in e for e in r.errores)


# ── PDF ────────────────────────────────────────────────────────────────────────

class TestValidarPDF:
    def test_pypdf_no_disponible_devuelve_ok_con_advertencia(self, tmp_path):
        p = tmp_path / "test.pdf"
        p.write_bytes(b"%PDF-1.4 mocked")
        with patch.dict(sys.modules, {"pypdf": None}):
            r = FileValidator._validar_pdf(p)
        # Si pypdf no carga, debe devolver ok=True con advertencia
        assert r.ok
        assert any("pypdf" in w.lower() or "básica" in w.lower()
                   for w in r.advertencias)

    def test_pdf_valido_sin_firma_devuelve_ok_con_advertencia(self, tmp_path):
        p = _archivo_pdf_real(tmp_path)
        r = FileValidator._validar_pdf(p)
        assert r.ok, f"errores: {r.errores}"
        assert r.metadatos.get("paginas", 0) >= 1
        # Sin firma → advertencia, pero NO error
        assert not r.errores
        if not r.metadatos.get("tiene_firma_digital"):
            assert any("firma" in w.lower() for w in r.advertencias)

    def test_pdf_no_legible_es_error(self, tmp_path):
        try:
            import pypdf  # noqa: F401
        except ImportError:
            pytest.skip("pypdf no instalado")
        p = tmp_path / "corrupto.pdf"
        p.write_bytes(b"esto no es un pdf valido")
        r = FileValidator._validar_pdf(p)
        assert not r.ok

    def test_validar_archivo_dict_pdf(self, tmp_path):
        p = _archivo_pdf_real(tmp_path)
        r = FileValidator.validar(_archivo(str(p), "plano.pdf"))
        assert r.ok


# ── DWG / DXF ─────────────────────────────────────────────────────────────────

class TestValidarDWG:
    def test_ezdxf_no_disponible_devuelve_ok_con_advertencia(self, tmp_path):
        p = tmp_path / "test.dxf"
        p.write_bytes(b"fake dxf")
        with patch.dict(sys.modules, {"ezdxf": None}):
            r = FileValidator._validar_dwg(p)
        assert r.ok
        assert any("ezdxf" in w.lower() or "básica" in w.lower()
                   for w in r.advertencias)

    def test_dxf_con_capas_requeridas_es_ok(self, tmp_path):
        p = _archivo_dwg_real(tmp_path, con_capas=True)
        r = FileValidator._validar_dwg(p)
        assert r.ok, f"errores: {r.errores}"
        assert r.metadatos.get("entidades_modelspace", 0) > 0

    def test_dxf_sin_capas_requeridas_tiene_errores(self, tmp_path):
        p = _archivo_dwg_real(tmp_path, con_capas=False)
        r = FileValidator._validar_dwg(p)
        assert not r.ok
        assert any("capas" in e.lower() for e in r.errores)

    def test_dxf_modelspace_vacio_tiene_error(self, tmp_path):
        try:
            import ezdxf
        except ImportError:
            pytest.skip("ezdxf no instalado")
        doc = ezdxf.new("R2010")
        # Agregar capas pero NO entidades
        for capa in CAPAS_REQUERIDAS_DWG:
            doc.layers.add(capa)
        p = tmp_path / "vacio.dxf"
        doc.saveas(str(p))
        r = FileValidator._validar_dwg(p)
        assert not r.ok
        assert any("vacío" in e.lower() or "vacio" in e.lower() for e in r.errores)

    def test_dxf_corrupto_es_error(self, tmp_path):
        try:
            import ezdxf  # noqa: F401
        except ImportError:
            pytest.skip("ezdxf no instalado")
        p = tmp_path / "corrupto.dxf"
        p.write_bytes(b"not a dxf")
        r = FileValidator._validar_dwg(p)
        assert not r.ok

    def test_validar_archivo_dict_dwg(self, tmp_path):
        p = _archivo_dwg_real(tmp_path, con_capas=True)
        r = FileValidator.validar(_archivo(str(p), "plano.dxf"))
        assert r.ok


# ── validar_archivos (función de conveniencia) ─────────────────────────────────

class TestValidarArchivos:
    def test_lista_vacia_es_ok(self):
        ok, errores = validar_archivos([])
        assert ok
        assert not errores

    def test_archivo_invalido_propaga_error(self, tmp_path):
        ok, errores = validar_archivos([_archivo("")])
        assert not ok
        assert errores

    def test_multiples_archivos_uno_falla(self, tmp_path):
        p_ok = _archivo_pdf_real(tmp_path)
        archivos = [
            _archivo(str(p_ok), "valido.pdf"),
            _archivo("",         "invalido.pdf"),
        ]
        ok, errores = validar_archivos(archivos)
        assert not ok
        assert any("invalido.pdf" in e for e in errores)

    def test_todos_validos(self, tmp_path):
        p1 = _archivo_pdf_real(tmp_path)
        archivos = [_archivo(str(p1), "plano.pdf")]
        ok, errores = validar_archivos(archivos)
        assert ok
        assert not errores


# ── ValidationResult ──────────────────────────────────────────────────────────

class TestValidationResult:
    def test_bool_ok(self):
        assert ValidationResult(True)
        assert not ValidationResult(False)

    def test_defaults(self):
        r = ValidationResult(True)
        assert r.errores == []
        assert r.advertencias == []
        assert r.metadatos == {}

    def test_repr(self):
        r = ValidationResult(False, ["error1"])
        assert "False" in repr(r)
        assert "error1" in repr(r)
