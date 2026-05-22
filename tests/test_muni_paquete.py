"""Tests del módulo muni_paquete (combinar PDFs para subir a Muni)."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.utils.muni_paquete import (
    extraer_numero_citas,
    detectar_archivos_paquete,
    combinar_pdfs_muni,
)


def _crear_pdf_con_texto(path: Path, texto: str) -> Path:
    """Crea un PDF simple con el texto dado (para tests)."""
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((50, 100), texto)
    doc.save(str(path))
    doc.close()
    return path


@pytest.fixture
def carpeta_muni(tmp_path):
    """Crea una carpeta con los 4 PDFs típicos de una segregación."""
    folder = tmp_path / "SEG1"
    folder.mkdir()
    _crear_pdf_con_texto(
        folder / "1018281_minuta.pdf",
        "MINUTA DE CALIFICACION\nCITAS DE PRESENTACION\n2025 - 81701 - C",
    )
    _crear_pdf_con_texto(folder / "1018281_imagenminuta.pdf", "imagen")
    _crear_pdf_con_texto(folder / "PLANO1F.pdf", "plano firmado")
    _crear_pdf_con_texto(folder / "agua1.pdf", "carta de agua")
    return folder


# ── extraer_numero_citas ──────────────────────────────────────────────

class TestExtraerNumeroCitas:
    def test_formato_estandar(self, tmp_path):
        pdf = _crear_pdf_con_texto(
            tmp_path / "minuta.pdf",
            "MINUTA DE CALIFICACION\nCITAS DE PRESENTACION\n2025 - 81701 - C\n",
        )
        assert extraer_numero_citas(pdf) == "2025 - 81701 - C"

    def test_distintos_separadores(self, tmp_path):
        """El formato real CFIA usa guion ASCII - con espacios."""
        pdf = _crear_pdf_con_texto(
            tmp_path / "minuta.pdf", "2025 - 81702 - C",
        )
        assert extraer_numero_citas(pdf) == "2025 - 81702 - C"

    def test_pdf_inexistente(self, tmp_path):
        assert extraer_numero_citas(tmp_path / "no_existe.pdf") is None

    def test_pdf_sin_patron(self, tmp_path):
        pdf = _crear_pdf_con_texto(tmp_path / "x.pdf", "texto sin citas")
        assert extraer_numero_citas(pdf) is None


# ── detectar_archivos_paquete ─────────────────────────────────────────

class TestDetectarArchivos:
    def test_detecta_los_4(self, carpeta_muni):
        archivos = detectar_archivos_paquete(carpeta_muni)
        assert archivos["minuta"] is not None
        assert "minuta" in archivos["minuta"].name.lower()
        assert "imagen" not in archivos["minuta"].name.lower()
        assert archivos["imagen_minuta"] is not None
        assert "imagenminuta" in archivos["imagen_minuta"].name.lower()
        assert archivos["plano"] is not None
        assert archivos["carta_agua"] is not None
        assert "agua" in archivos["carta_agua"].name.lower()

    def test_no_confunde_imagen_con_minuta(self, tmp_path):
        """imagenminuta NO debe matchear como 'minuta'."""
        f = tmp_path / "X"; f.mkdir()
        _crear_pdf_con_texto(f / "1018281_imagenminuta.pdf", "imagen")
        r = detectar_archivos_paquete(f)
        assert r["minuta"] is None  # solo imagen, no minuta sola
        assert r["imagen_minuta"] is not None

    def test_carpeta_inexistente(self, tmp_path):
        r = detectar_archivos_paquete(tmp_path / "no_existe")
        assert all(v is None for v in r.values())

    def test_planof_se_detecta_como_plano(self, tmp_path):
        f = tmp_path / "X"; f.mkdir()
        _crear_pdf_con_texto(f / "planof 1.pdf", "plano firmado")
        r = detectar_archivos_paquete(f)
        assert r["plano"] is not None


# ── combinar_pdfs_muni ────────────────────────────────────────────────

class TestCombinarPdfs:
    def test_combina_4_pdfs(self, carpeta_muni, tmp_path):
        salida = tmp_path / "out.pdf"
        archivos = detectar_archivos_paquete(carpeta_muni)
        res = combinar_pdfs_muni(
            minuta=archivos["minuta"],
            imagen_minuta=archivos["imagen_minuta"],
            plano=archivos["plano"],
            carta_agua=archivos["carta_agua"],
            salida=salida,
        )
        assert res["pdf_path"] == salida
        assert salida.exists()
        assert res["n_paginas_total"] == 4
        assert len(res["archivos_incluidos"]) == 4

    def test_combina_solo_minuta(self, carpeta_muni, tmp_path):
        archivos = detectar_archivos_paquete(carpeta_muni)
        salida = tmp_path / "solo_minuta.pdf"
        res = combinar_pdfs_muni(minuta=archivos["minuta"], salida=salida)
        assert res["n_paginas_total"] == 1
        assert len(res["archivos_incluidos"]) == 1

    def test_minuta_no_existe_lanza(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            combinar_pdfs_muni(
                minuta=tmp_path / "no_existe.pdf",
                salida=tmp_path / "out.pdf",
            )

    def test_archivo_opcional_inexistente_se_skip(self, carpeta_muni, tmp_path):
        archivos = detectar_archivos_paquete(carpeta_muni)
        salida = tmp_path / "out.pdf"
        res = combinar_pdfs_muni(
            minuta=archivos["minuta"],
            carta_agua=tmp_path / "no_existe.pdf",  # no existe
            salida=salida,
        )
        # Solo se incluye minuta, no falla
        assert res["n_paginas_total"] == 1
        assert any("minuta" in a for a in res["archivos_incluidos"])

    def test_orden_de_paginas(self, tmp_path):
        """El PDF combinado mantiene el orden: minuta, imagen, plano, agua."""
        from pypdf import PdfReader
        f = tmp_path / "X"; f.mkdir()
        pdfs = {}
        for nombre, texto in [
            ("minuta.pdf", "PAGE_MINUTA"),
            ("imagen.pdf", "PAGE_IMAGEN"),
            ("plano.pdf",  "PAGE_PLANO"),
            ("agua.pdf",   "PAGE_AGUA"),
        ]:
            pdfs[nombre] = _crear_pdf_con_texto(f / nombre, texto)
        salida = tmp_path / "combo.pdf"
        combinar_pdfs_muni(
            minuta=pdfs["minuta.pdf"],
            imagen_minuta=pdfs["imagen.pdf"],
            plano=pdfs["plano.pdf"],
            carta_agua=pdfs["agua.pdf"],
            salida=salida,
        )
        reader = PdfReader(str(salida))
        textos = [p.extract_text() for p in reader.pages]
        assert "PAGE_MINUTA" in textos[0]
        assert "PAGE_IMAGEN" in textos[1]
        assert "PAGE_PLANO"  in textos[2]
        assert "PAGE_AGUA"   in textos[3]
