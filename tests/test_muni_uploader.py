"""Tests del módulo muni_uploader.

Estos tests no requieren un browser real — testan las funciones puras
de soporte. Los tests de integración con Playwright corren aparte.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.utils.muni_uploader import (
    cerrar_pickers_residuales,
    coords_boton_picker,
    coords_examinar_picker,
    doble_chequeo_form_muni,
    subir_archivo_a_picker,
)


class _FakeFrame:
    def __init__(self, url):
        self.url = url


class _FakePage:
    """Mock simple de page Playwright para tests puros."""
    def __init__(self, *, frames=None, eval_result=None,
                 file_chooser_works=True, files_existing=None):
        self.frames = frames or []
        self._eval_results = eval_result if isinstance(eval_result, list) else [eval_result]
        self._eval_index = 0
        self.mouse = MagicMock()
        self.keyboard = MagicMock()
        self._loc_files = []

    def evaluate(self, script, *args):
        if self._eval_index < len(self._eval_results):
            r = self._eval_results[self._eval_index]
            self._eval_index += 1
            return r
        return self._eval_results[-1] if self._eval_results else None


# ── cerrar_pickers_residuales ──────────────────────────────────────────

class TestCerrarPickers:
    def test_sin_pickers_OK_inmediato(self):
        page = _FakePage(frames=[_FakeFrame("https://example.com")])
        r = cerrar_pickers_residuales(page)
        assert r["ok"] is True
        assert r["pickers_iniciales"] == 0
        assert r["pickers_finales"] == 0
        assert r["estrategias_usadas"] == []

    def test_picker_blank_no_cuenta(self):
        page = _FakePage(frames=[_FakeFrame("about:blank")])
        r = cerrar_pickers_residuales(page)
        assert r["pickers_iniciales"] == 0
        assert r["ok"] is True

    def test_devuelve_dict_con_keys_correctas(self):
        page = _FakePage(frames=[])
        r = cerrar_pickers_residuales(page)
        assert "ok" in r
        assert "pickers_iniciales" in r
        assert "pickers_finales" in r
        assert "estrategias_usadas" in r

    def test_pickers_persistentes_usa_iframe_remove(self):
        """Si Escape no funciona, escala a iframe.remove()."""
        page = _FakePage(frames=[
            _FakeFrame("https://docs.google.com/picker?x"),
        ])
        # Ningún Escape cierra los pickers
        page.evaluate = lambda *_: 1  # remove() ejecutado
        r = cerrar_pickers_residuales(page)
        assert r["pickers_iniciales"] == 1
        # Pero como el FakePage no actualiza frames, sigue mostrándolos
        # Las estrategias deben incluir escape + close_btn + iframe_remove
        assert "escape" in r["estrategias_usadas"]
        assert any("iframe_remove" in s for s in r["estrategias_usadas"])


# ── coords_boton_picker / coords_examinar_picker ───────────────────────

class TestCoords:
    def test_coords_boton_devuelve_dict(self):
        page = _FakePage(eval_result={"x": 100, "y": 200})
        r = coords_boton_picker(page, "DOCUMENTOS")
        assert r == {"x": 100, "y": 200}

    def test_coords_boton_no_encontrado_None(self):
        page = _FakePage(eval_result=None)
        assert coords_boton_picker(page, "INEXISTENTE") is None

    def test_coords_examinar_devuelve_dict(self):
        page = _FakePage(eval_result={"x": 300, "y": 400})
        assert coords_examinar_picker(page) == {"x": 300, "y": 400}


# ── doble_chequeo_form_muni ────────────────────────────────────────────

class TestDobleChequeo:
    def test_todos_OK(self):
        # evaluate devuelve LISTA entera, no item por item
        page = _FakePage()
        page.evaluate = lambda *_: ["2025", "81701", "1223951", "6825.57", "1-2-3-4"]
        r = doble_chequeo_form_muni(page, esperados={
            "tomo": "2025", "asiento": "81701", "tramite": "1223951",
        })
        assert r["ok"] is True
        assert len(r["faltantes"]) == 0

    def test_faltan_campos(self):
        page = _FakePage()
        page.evaluate = lambda *_: ["2025", "1223951"]
        r = doble_chequeo_form_muni(page, esperados={
            "tomo": "2025", "asiento": "81701", "tramite": "1223951",
        })
        assert r["ok"] is False
        faltantes_campos = {f["campo"] for f in r["faltantes"]}
        assert "asiento" in faltantes_campos

    def test_substring_match(self):
        """Si el valor esperado es substring de algún valor del form, cuenta."""
        page = _FakePage()
        page.evaluate = lambda *_: ["luis alonso rojas herrera"]
        r = doble_chequeo_form_muni(page, esperados={"nombre": "rojas"})
        assert r["ok"] is True

    def test_vacio_si_no_hay_esperados(self):
        page = _FakePage()
        page.evaluate = lambda *_: []
        r = doble_chequeo_form_muni(page, esperados={})
        assert r["ok"] is True


# ── subir_archivo_a_picker (error handling) ────────────────────────────

class TestSubirArchivo:
    def test_archivo_no_existe(self, tmp_path):
        page = _FakePage()
        r = subir_archivo_a_picker(
            page, titulo_listitem="DOCUMENTOS",
            ruta_archivo=str(tmp_path / "no_existe.pdf"),
        )
        assert r["ok"] is False
        assert "no existe" in r["error"]

    def test_sin_coords_boton_falla(self, tmp_path):
        # Crear archivo dummy
        f = tmp_path / "dummy.pdf"
        f.write_bytes(b"%PDF-1.4\n")
        # Page que devuelve None para coords
        page = _FakePage(eval_result=None)
        r = subir_archivo_a_picker(
            page, titulo_listitem="DOCUMENTOS",
            ruta_archivo=str(f),
        )
        assert r["ok"] is False
        assert "no encontré" in r["error"] or "no encontre" in r["error"]
