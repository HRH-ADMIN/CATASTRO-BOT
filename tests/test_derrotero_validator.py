"""Tests del derrotero_validator — usa el Derrotero.zip real de FELIPE_TIOS."""
from __future__ import annotations
from pathlib import Path

import pytest

pytest.importorskip("shapefile", reason="pyshp requerido")

from src.utils.derrotero_validator import (
    leer_derrotero_zip, validar_derrotero,
    _area_poligono, _quitar_cierre, _coords_aproximadamente_iguales,
)


# Path al derrotero real de FELIPE_TIOS (verificado durante validación)
_DERROTERO_FELIPE = Path(
    "data/files/ALAJUELA/SAN_RAMON/ALFARO/FELIPE_TIOS/01_Campo/Derrotero.zip"
)

# Coords del cajetín de FELIPE_TIOS (8 vértices)
_COORDS_FELIPE_CAJETIN = [
    (445579.27, 1114039.40), (445588.57, 1114036.72), (445581.79, 1114022.64),
    (445570.22, 1113998.74), (445563.04, 1114002.01), (445556.67, 1114005.31),
    (445564.70, 1114025.27), (445577.79, 1114036.00),
]


class TestHelpers:
    def test_area_cuadrado_unitario(self):
        cuadrado = [(0, 0), (1, 0), (1, 1), (0, 1)]
        assert abs(_area_poligono(cuadrado) - 1.0) < 1e-6

    def test_area_cierra_automaticamente(self):
        # No cerrado
        abierto = [(0, 0), (10, 0), (10, 10), (0, 10)]
        assert abs(_area_poligono(abierto) - 100.0) < 1e-6
        # Cerrado explícitamente
        cerrado = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
        assert abs(_area_poligono(cerrado) - 100.0) < 1e-6

    def test_quitar_cierre(self):
        cerrado = [(0, 0), (1, 0), (1, 1), (0, 0)]
        assert _quitar_cierre(cerrado) == [(0, 0), (1, 0), (1, 1)]
        abierto = [(0, 0), (1, 0), (1, 1)]
        assert _quitar_cierre(abierto) == [(0, 0), (1, 0), (1, 1)]

    def test_coords_aproximadamente_iguales(self):
        assert _coords_aproximadamente_iguales(
            (100.0, 200.0), (100.3, 200.3), tol=0.5,
        ) is True
        assert _coords_aproximadamente_iguales(
            (100.0, 200.0), (101.0, 200.0), tol=0.5,
        ) is False


@pytest.mark.skipif(not _DERROTERO_FELIPE.exists(),
                    reason="Derrotero.zip de FELIPE_TIOS no presente")
class TestDerroteroReal:
    """Tests contra el Derrotero.zip real de FELIPE_TIOS."""

    def test_lee_polygon_correcto(self):
        d = leer_derrotero_zip(_DERROTERO_FELIPE)
        assert d.error == ""
        assert d.n_vertices == 8
        # Área debe ser ~582.67 m² (lo declarado en el cajetín)
        assert 580 < d.area_m2 < 585
        # Proyección CRTM05
        assert "CRTM05" in d.proyeccion or "CR-SIRGAS" in d.proyeccion

    def test_validacion_exitosa_contra_cajetin(self):
        res = validar_derrotero(
            zip_path=_DERROTERO_FELIPE,
            coords_cajetin=_COORDS_FELIPE_CAJETIN,
            area_declarada=582.67,
        )
        assert not res.tiene_errores()
        assert not res.advertencias

    def test_area_errada_es_error(self):
        res = validar_derrotero(
            zip_path=_DERROTERO_FELIPE,
            area_declarada=100.0,  # muy distinta de la real (582)
        )
        assert res.tiene_errores()
        assert any("Área cajetín" in e for e in res.errores)

    def test_area_pequena_diferencia_es_warn(self):
        # Diferencia ~2% → warning (entre 1% y 5%)
        res = validar_derrotero(
            zip_path=_DERROTERO_FELIPE,
            area_declarada=594.0,  # ~2% off
        )
        assert not res.tiene_errores()
        assert any("difieren" in w for w in res.advertencias)

    def test_coords_no_matcheantes_warning(self):
        res = validar_derrotero(
            zip_path=_DERROTERO_FELIPE,
            coords_cajetin=[(99999.0, 99999.0)],  # inventado
            area_declarada=582.67,
        )
        # No error, pero sí advertencias
        assert any("sin match" in w for w in res.advertencias)


class TestEdgeCases:
    def test_zip_inexistente(self):
        d = leer_derrotero_zip("/no/existe.zip")
        assert d.error != ""
        assert d.n_vertices == 0

    def test_zip_path_none(self):
        res = validar_derrotero(zip_path=None)
        assert not res.tiene_errores()
        assert any("omitida" in i for i in res.info)

    def test_zip_archivo_no_es_zip(self, tmp_path):
        fake = tmp_path / "fake.zip"
        fake.write_text("not a zip")
        d = leer_derrotero_zip(fake)
        assert d.error != ""

    def test_zip_sin_shapefile_dentro(self, tmp_path):
        import zipfile
        fake = tmp_path / "empty.zip"
        with zipfile.ZipFile(fake, "w") as zf:
            zf.writestr("readme.txt", "no es un derrotero")
        d = leer_derrotero_zip(fake)
        assert "incompleto" in d.error or "falta" in d.error
