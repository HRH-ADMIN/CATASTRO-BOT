"""Tests para src/utils/dwg_corrector.py

Cubre:
  - aplicar_correcciones: lista vacía → ResultadoCorreccion sin cambios
  - aplicar_correcciones: ezdxf no instalado → error informativo
  - aplicar_correcciones: archivo no encontrado → error informativo
  - aplicar_correcciones: texto encontrado en TEXT → modificado=True
  - aplicar_correcciones: texto encontrado en MTEXT → modificado=True
  - aplicar_correcciones: texto en bloque (ATTDEF) → modificado=True
  - aplicar_correcciones: valor no encontrado → no_encontradas no vacío
  - aplicar_correcciones: múltiples correcciones parciales → separa aplicadas/no_encontradas
  - aplicar_correcciones: ignorar_mayusculas=True → case-insensitive
  - aplicar_correcciones: ignorar_mayusculas=False → case-sensitive
  - aplicar_correcciones: DXF corregido se guarda como {stem}_corr.dxf
  - aplicar_correcciones: valor_actual == valor_correcto → ignorado
  - aplicar_correcciones: valor_actual vacío → ignorado
  - ResultadoCorreccion.resumen: con aplicadas y no_encontradas
  - ResultadoCorreccion.resumen: con error
  - ResultadoCorreccion.resumen: sin cambios
  - _reemplazar_en_doc: reemplaza en modelspace y en bloques
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from src.utils.dwg_corrector import (
    CorreccionTexto,
    ResultadoCorreccion,
    aplicar_correcciones,
)


# ── helpers ────────────────────────────────────────────────────────────────────

def _ezdxf_disponible() -> bool:
    try:
        import ezdxf  # noqa: F401
        return True
    except ImportError:
        return False


def _dxf_con_text(tmp_path: Path, *, texto: str = "1200.50") -> Path:
    """Crea un DXF mínimo con una entidad TEXT en el modelspace."""
    try:
        import ezdxf
    except ImportError:
        pytest.skip("ezdxf no instalado")

    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_text(texto)
    p = tmp_path / "plano.dxf"
    doc.saveas(str(p))
    return p


def _dxf_con_mtext(tmp_path: Path, *, texto: str = "1200.50") -> Path:
    """Crea un DXF mínimo con una entidad MTEXT en el modelspace."""
    try:
        import ezdxf
    except ImportError:
        pytest.skip("ezdxf no instalado")

    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_mtext(texto)
    p = tmp_path / "plano_mtext.dxf"
    doc.saveas(str(p))
    return p


def _dxf_con_bloque_attdef(tmp_path: Path, *, texto: str = "Canton01") -> Path:
    """Crea un DXF con un bloque que contiene un ATTDEF."""
    try:
        import ezdxf
    except ImportError:
        pytest.skip("ezdxf no instalado")

    doc = ezdxf.new("R2010")
    blk = doc.blocks.new("CARATULA")
    attdef = blk.add_attdef("CANTON", (0, 0), text=texto)
    p = tmp_path / "plano_blk.dxf"
    doc.saveas(str(p))
    return p


# ── CorreccionTexto ────────────────────────────────────────────────────────────

class TestCorreccionTexto:
    def test_campos_basicos(self):
        c = CorreccionTexto(campo="area", valor_actual="1200.50", valor_correcto="1250.75")
        assert c.campo == "area"
        assert c.valor_actual == "1200.50"
        assert c.valor_correcto == "1250.75"


# ── ResultadoCorreccion.resumen ────────────────────────────────────────────────

class TestResumen:
    def test_resumen_sin_cambios(self):
        r = ResultadoCorreccion()
        assert "Sin cambios" in r.resumen

    def test_resumen_con_error(self):
        r = ResultadoCorreccion(error="ezdxf no instalado")
        assert "ezdxf" in r.resumen
        assert "No fue posible" in r.resumen

    def test_resumen_con_aplicadas(self):
        r = ResultadoCorreccion(
            modificado=True,
            aplicadas=[("1200.50", "1250.75")],
        )
        assert "Corregido" in r.resumen
        assert "1200.50" in r.resumen
        assert "1250.75" in r.resumen

    def test_resumen_con_no_encontradas(self):
        r = ResultadoCorreccion(
            no_encontradas=[("finca_vieja", "finca_nueva")],
        )
        assert "No encontrado" in r.resumen
        assert "finca_vieja" in r.resumen

    def test_resumen_mixto(self):
        r = ResultadoCorreccion(
            modificado=True,
            aplicadas=[("1200.50", "1250.75")],
            no_encontradas=[("X123", "X456")],
        )
        txt = r.resumen
        assert "Corregido" in txt
        assert "No encontrado" in txt


# ── aplicar_correcciones: casos sin DXF real ─────────────────────────────────

class TestAplicarSinDxf:
    def test_lista_vacia_devuelve_sin_cambios(self, tmp_path):
        p = tmp_path / "plano.dxf"
        p.write_bytes(b"fake")
        r = aplicar_correcciones(p, [])
        assert not r.modificado
        assert r.error is None

    def test_ezdxf_no_instalado(self, tmp_path):
        # El archivo debe existir para que se alcance la comprobación de ezdxf
        p = tmp_path / "plano.dxf"
        p.write_bytes(b"DXF fake content")
        corrs = [CorreccionTexto("area", "1200", "1300")]
        with patch.dict(sys.modules, {"ezdxf": None}):
            r = aplicar_correcciones(p, corrs)
        assert not r.modificado
        assert r.error is not None
        assert "ezdxf" in r.error.lower()

    def test_archivo_no_encontrado(self, tmp_path):
        p = tmp_path / "no_existe.dxf"
        corrs = [CorreccionTexto("area", "1200", "1300")]
        r = aplicar_correcciones(p, corrs)
        assert not r.modificado
        assert r.error is not None
        assert "no encontrado" in r.error

    def test_correcciones_identicas_ignoradas(self, tmp_path):
        """valor_actual == valor_correcto → no se aplica."""
        p = tmp_path / "plano.dxf"
        p.write_bytes(b"fake")
        corrs = [CorreccionTexto("area", "1200", "1200")]
        r = aplicar_correcciones(p, corrs)
        # Se filtra antes de intentar leer el archivo
        assert not r.modificado

    def test_valor_actual_vacio_ignorado(self, tmp_path):
        """valor_actual vacío → se filtra."""
        p = tmp_path / "plano.dxf"
        p.write_bytes(b"fake")
        corrs = [CorreccionTexto("area", "", "1300")]
        r = aplicar_correcciones(p, corrs)
        assert not r.modificado

    def test_valor_correcto_vacio_ignorado(self, tmp_path):
        corrs = [CorreccionTexto("area", "1200", "")]
        p = tmp_path / "plano.dxf"
        p.write_bytes(b"fake")
        r = aplicar_correcciones(p, corrs)
        assert not r.modificado


# ── aplicar_correcciones: con DXF real ────────────────────────────────────────

@pytest.mark.skipif(not _ezdxf_disponible(), reason="ezdxf no instalado")
class TestAplicarConDxf:
    def test_corrige_text_entity(self, tmp_path):
        p = _dxf_con_text(tmp_path, texto="1200.50")
        corrs = [CorreccionTexto("area", "1200.50", "1250.75")]
        r = aplicar_correcciones(p, corrs)

        assert r.modificado
        assert r.archivo_corregido is not None
        assert r.archivo_corregido.exists()
        assert r.archivo_corregido.name == "plano_corr.dxf"
        assert ("1200.50", "1250.75") in r.aplicadas

    def test_corrige_mtext_entity(self, tmp_path):
        p = _dxf_con_mtext(tmp_path, texto="Canton01")
        corrs = [CorreccionTexto("canton", "Canton01", "Palmares")]
        r = aplicar_correcciones(p, corrs)

        assert r.modificado
        assert ("Canton01", "Palmares") in r.aplicadas

    def test_valor_no_encontrado_en_no_encontradas(self, tmp_path):
        p = _dxf_con_text(tmp_path, texto="1200.50")
        corrs = [CorreccionTexto("finca", "finca_INEXISTENTE", "finca_NUEVA")]
        r = aplicar_correcciones(p, corrs)

        assert not r.modificado
        assert ("finca_INEXISTENTE", "finca_NUEVA") in r.no_encontradas

    def test_multiples_correcciones_parciales(self, tmp_path):
        """Una corrección encontrada, otra no."""
        p = _dxf_con_text(tmp_path, texto="area 1200.50")
        corrs = [
            CorreccionTexto("area", "1200.50", "1250.75"),
            CorreccionTexto("finca", "finca_FALSA", "finca_REAL"),
        ]
        r = aplicar_correcciones(p, corrs)

        assert r.modificado
        assert len(r.aplicadas) == 1
        assert len(r.no_encontradas) == 1

    def test_case_insensitive_por_defecto(self, tmp_path):
        """ignorar_mayusculas=True (default) → corrige independiente del case."""
        p = _dxf_con_text(tmp_path, texto="CANTON SAN RAMON")
        corrs = [CorreccionTexto("canton", "san ramon", "Palmares")]
        r = aplicar_correcciones(p, corrs)
        assert r.modificado

    def test_case_sensitive_cuando_flag_false(self, tmp_path):
        """ignorar_mayusculas=False → case exacto requerido."""
        p = _dxf_con_text(tmp_path, texto="CANTON SAN RAMON")
        corrs = [CorreccionTexto("canton", "san ramon", "Palmares")]
        r = aplicar_correcciones(p, corrs, ignorar_mayusculas=False)
        # "san ramon" != "SAN RAMON" (case-sensitive) → no encontrado
        assert not r.modificado
        assert len(r.no_encontradas) == 1

    def test_dxf_corregido_nombre_correcto(self, tmp_path):
        """El archivo corregido usa el sufijo _corr.dxf."""
        p = _dxf_con_text(tmp_path, texto="texto_viejo")
        corrs = [CorreccionTexto("nota", "texto_viejo", "texto_nuevo")]
        r = aplicar_correcciones(p, corrs)

        assert r.archivo_corregido is not None
        assert r.archivo_corregido.stem.endswith("_corr")
        assert r.archivo_corregido.suffix == ".dxf"

    def test_dxf_corregido_contiene_nuevo_valor(self, tmp_path):
        """El DXF guardado realmente contiene el valor nuevo."""
        import ezdxf
        p = _dxf_con_text(tmp_path, texto="area_incorrecta")
        corrs = [CorreccionTexto("area", "area_incorrecta", "area_correcta")]
        r = aplicar_correcciones(p, corrs)

        assert r.modificado
        doc2 = ezdxf.readfile(str(r.archivo_corregido))
        textos = [e.dxf.text for e in doc2.modelspace() if e.dxftype() == "TEXT"]
        assert any("area_correcta" in t for t in textos)
        assert not any("area_incorrecta" in t for t in textos)

    def test_multiples_entidades_text_mismo_valor(self, tmp_path):
        """Si el mismo valor aparece en varias entidades, todas se corrigen."""
        import ezdxf
        doc = ezdxf.new("R2010")
        msp = doc.modelspace()
        for _ in range(3):
            msp.add_text("1200.50")
        p = tmp_path / "multi.dxf"
        doc.saveas(str(p))

        corrs = [CorreccionTexto("area", "1200.50", "1250.00")]
        r = aplicar_correcciones(p, corrs)

        assert r.modificado
        doc2 = ezdxf.readfile(str(r.archivo_corregido))
        textos = [e.dxf.text for e in doc2.modelspace() if e.dxftype() == "TEXT"]
        assert all(t == "1250.00" for t in textos)

    def test_bloque_attdef_corregido(self, tmp_path):
        """Corrección en ATTDEF dentro de un bloque."""
        p = _dxf_con_bloque_attdef(tmp_path, texto="Canton01")
        corrs = [CorreccionTexto("canton", "Canton01", "Palmares")]
        r = aplicar_correcciones(p, corrs)
        assert r.modificado

    def test_no_modifica_archivo_original(self, tmp_path):
        """El DWG original debe quedar intacto."""
        p = _dxf_con_text(tmp_path, texto="1200.50")
        contenido_original = p.read_bytes()
        corrs = [CorreccionTexto("area", "1200.50", "1250.75")]
        aplicar_correcciones(p, corrs)
        assert p.read_bytes() == contenido_original


# ── _reemplazar_en_doc (indirectamente vía aplicar_correcciones) ──────────────

@pytest.mark.skipif(not _ezdxf_disponible(), reason="ezdxf no instalado")
class TestReemplazarEnDoc:
    def test_no_toca_entidades_sin_texto(self, tmp_path):
        """Entidades LINE/CIRCLE no tienen texto — no deben romper la iteración."""
        import ezdxf
        doc = ezdxf.new("R2010")
        msp = doc.modelspace()
        msp.add_line((0, 0), (10, 10))
        msp.add_circle((5, 5), radius=3)
        msp.add_text("1200.50")
        p = tmp_path / "mixto.dxf"
        doc.saveas(str(p))

        corrs = [CorreccionTexto("area", "1200.50", "1250.75")]
        r = aplicar_correcciones(p, corrs)
        assert r.modificado   # El TEXT fue corregido a pesar de las otras entidades
