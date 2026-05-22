"""Tests del módulo anomaly_snapshot — captura post-mortem."""
from __future__ import annotations
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.utils.anomaly_snapshot import (
    capturar_snapshot, listar_snapshots_de_expediente, _safe_dir_name,
)


def _mock_page(*, url="https://apt.cfia.or.cr/test",
               html="<html><body>page</body></html>",
               title_val="Test",
               modal=None,
               screenshot_ok=True):
    page = MagicMock()
    page.url = url
    page.content.return_value = html
    page.title.return_value = title_val
    page.viewport_size = {"width": 1280, "height": 720}
    if screenshot_ok:
        page.screenshot.return_value = b"\x89PNG\r\n"
    else:
        page.screenshot.side_effect = Exception("screenshot failed")
    page.evaluate.return_value = modal
    return page


class TestSafeDirName:
    def test_alfa_numericos_intactos(self):
        assert _safe_dir_name("RDF-2026-004") == "RDF-2026-004"

    def test_reemplaza_caracteres_prohibidos(self):
        # Windows prohíbe < > : " / \ | ? *
        nombre = _safe_dir_name('test/with\\bad:chars?')
        assert "/" not in nombre and "\\" not in nombre
        assert ":" not in nombre and "?" not in nombre

    def test_vacio_devuelve_default(self):
        assert _safe_dir_name("") == "no-exp"
        assert _safe_dir_name(None) == "no-exp"

    def test_recorta_largos(self):
        nombre = _safe_dir_name("X" * 200)
        assert len(nombre) <= 60


class TestCapturarSnapshot:
    def test_guarda_html_screenshot_metadata(self, tmp_path):
        page = _mock_page(html="<html>hola</html>")
        result = capturar_snapshot(
            page,
            expediente_numero="RDF-2026-004",
            contexto="bC5",
            descripcion="test",
            raiz=tmp_path,
        )
        assert result is not None
        assert result.exists()
        # Archivos esperados
        assert (result / "page.html").exists()
        html_content = (result / "page.html").read_text(encoding="utf-8")
        assert "hola" in html_content
        # metadata.json
        assert (result / "metadata.json").exists()
        meta = json.loads((result / "metadata.json").read_text(encoding="utf-8"))
        assert meta["url"] == "https://apt.cfia.or.cr/test"
        assert meta["expediente"] == "RDF-2026-004"
        # anomalia.json
        assert (result / "anomalia.json").exists()
        anom = json.loads((result / "anomalia.json").read_text(encoding="utf-8"))
        assert anom["contexto"] == "bC5"
        assert anom["descripcion"] == "test"
        # Screenshot fue intentado
        page.screenshot.assert_called()

    def test_guarda_modal_si_visible(self, tmp_path):
        modal = {
            "title": "Error", "html": "Algo salió mal",
            "icon": "swal2-icon swal2-error",
        }
        page = _mock_page(modal=modal)
        result = capturar_snapshot(
            page,
            expediente_numero="RDF-2026-004",
            contexto="bC5", descripcion="x",
            raiz=tmp_path,
        )
        assert (result / "modal_actual.json").exists()
        m = json.loads((result / "modal_actual.json").read_text(encoding="utf-8"))
        assert m["title"] == "Error"

    def test_no_guarda_modal_si_no_hay(self, tmp_path):
        page = _mock_page(modal=None)
        result = capturar_snapshot(
            page,
            expediente_numero="RDF-2026-004",
            contexto="bC5", descripcion="x",
            raiz=tmp_path,
        )
        assert not (result / "modal_actual.json").exists()

    def test_screenshot_falla_no_explota(self, tmp_path):
        """Si screenshot falla, snapshot sigue funcionando para los otros archivos."""
        page = _mock_page(screenshot_ok=False)
        result = capturar_snapshot(
            page,
            expediente_numero="RDF-2026-004",
            contexto="bC5", descripcion="x",
            raiz=tmp_path,
        )
        assert result is not None
        # HTML y metadata sí están
        assert (result / "page.html").exists()
        assert (result / "metadata.json").exists()

    def test_carpeta_por_expediente_y_timestamp(self, tmp_path):
        page = _mock_page()
        result = capturar_snapshot(
            page, expediente_numero="RDF-2026-004",
            contexto="x", descripcion="y", raiz=tmp_path,
        )
        # Estructura: <tmp_path>/RDF-2026-004/<timestamp>/
        assert "RDF-2026-004" in str(result)
        # Timestamp debe ser ISO-like
        assert result.parent.name == "RDF-2026-004"

    def test_expediente_vacio_usa_no_exp(self, tmp_path):
        page = _mock_page()
        result = capturar_snapshot(
            page, expediente_numero="",
            contexto="x", descripcion="y", raiz=tmp_path,
        )
        assert result.parent.name == "no-exp"

    def test_no_explota_si_page_sin_content(self, tmp_path):
        """page sin .content() no debe romper el snapshot."""
        page = MagicMock(spec=[])  # mock sin métodos
        result = capturar_snapshot(
            page, expediente_numero="X",
            contexto="x", descripcion="y", raiz=tmp_path,
        )
        # Devuelve la carpeta aunque no haya nada que guardar
        assert result is not None


class TestListarSnapshots:
    def test_listar_vacio(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "src.utils.anomaly_snapshot.SNAPSHOT_ROOT",
            tmp_path / "snapshots",
        )
        result = listar_snapshots_de_expediente("RDF-2026-004")
        assert result == []

    def test_listar_ordenado_descendente(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "src.utils.anomaly_snapshot.SNAPSHOT_ROOT", tmp_path,
        )
        base = tmp_path / "RDF-2026-004"
        base.mkdir()
        (base / "2026-01-01T10_00_00").mkdir()
        (base / "2026-02-01T10_00_00").mkdir()
        (base / "2026-03-01T10_00_00").mkdir()
        result = listar_snapshots_de_expediente("RDF-2026-004")
        # Más reciente primero
        assert result[0].name == "2026-03-01T10_00_00"
        assert result[-1].name == "2026-01-01T10_00_00"
