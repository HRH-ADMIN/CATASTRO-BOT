"""Tests del preflight validator del datos_apt."""
from __future__ import annotations
import pytest

from src.utils.preflight_seed import (
    PreflightResult,
    validar_seed_pre_envio,
    _poligono_se_autointersecta,
    _area_poligono_stokes,
    _punto_dentro_de_poligono,
    _validar_plano_modificar,
)


# Coordenadas reales de FELIPE_TIOS (8 vértices, polígono válido)
_COORDS_FELIPE = [
    (445579.27, 1114039.40), (445588.57, 1114036.72), (445581.79, 1114022.64),
    (445570.22, 1113998.74), (445563.04, 1114002.01), (445556.67, 1114005.31),
    (445564.70, 1114025.27), (445577.79, 1114036.00),
]


def _seed_valido_minimo() -> dict:
    """Seed mínimo válido para los tests — basado en FELIPE_TIOS."""
    return {
        "propietario": {
            "tipo_cedula": "1",
            "cedula": "2-0466-0095",
            "correo": "x@y.cr",
        },
        "contratante_es_propietario": True,
        "profesional": {"correo": "x@y.cr"},
        "protocolo": {"numero": "23549", "folio": "178"},
        "proyecto": {
            "provincia": "2", "canton": "02", "distrito": "09",
            "naturaleza": "2",
        },
        "general": {
            "area_predio": "741.68",
            "area_real": "582.67",
            "moneda": "1",
            "honorarios": "147705",
            "exoneracion_honorarios": False,
            "observaciones": "",
        },
        "plano": {
            "descripcion": "FELIPETIOS(4)",
            "area_real": "582.67",
            "area_registro": "741.68",
            "tipo_zona": "3",
            "tipo_uso": "23",
            "tipo_coordenada": "3",
            "norte": "1114018.99",
            "este": "445571.99",
            "vertices": "8",
            "fincas": [{"provincia": "2", "numero": "422958", "derecho": "000"}],
            "planos_modificar": [{"provincia": "2", "numero": "1095215", "anno": "2006"}],
            "entero": {"numero": "660822113", "total_cfia": "1600"},
            "archivos": {},
        },
    }


# ── Helpers geométricos ────────────────────────────────────────

class TestPoligonoChecks:
    def test_poligono_convexo_no_se_autointersecta(self):
        assert _poligono_se_autointersecta(_COORDS_FELIPE) is False

    def test_bowtie_se_autointersecta(self):
        # Polígono en forma de "moño" (4 vértices que se cruzan en el medio)
        bowtie = [(0, 0), (10, 10), (10, 0), (0, 10)]
        assert _poligono_se_autointersecta(bowtie) is True

    def test_area_cuadrado_unitario(self):
        cuadrado = [(0, 0), (1, 0), (1, 1), (0, 1)]
        assert abs(_area_poligono_stokes(cuadrado) - 1.0) < 1e-6

    def test_punto_dentro_de_cuadrado(self):
        cuadrado = [(0, 0), (10, 0), (10, 10), (0, 10)]
        assert _punto_dentro_de_poligono((5, 5), cuadrado) is True
        assert _punto_dentro_de_poligono((20, 5), cuadrado) is False

    def test_centroide_de_felipe_esta_dentro(self):
        # El centroide computed (445571.99, 1114018.99) debe estar dentro
        assert _punto_dentro_de_poligono((445571.99, 1114018.99), _COORDS_FELIPE) is True


class TestValidarPlanoModificar:
    def test_valido(self):
        ok, _ = _validar_plano_modificar({"provincia": "2", "numero": "1095215", "anno": "2006"})
        assert ok is True

    def test_provincia_invalida(self):
        ok, motivo = _validar_plano_modificar({"provincia": "9", "numero": "1", "anno": "2006"})
        assert ok is False
        assert "provincia inválida" in motivo

    def test_anno_invalido(self):
        ok, motivo = _validar_plano_modificar({"provincia": "2", "numero": "1", "anno": "06"})
        assert ok is False
        assert "año inválido" in motivo

    def test_numero_vacio(self):
        ok, motivo = _validar_plano_modificar({"provincia": "2", "numero": "", "anno": "2006"})
        assert ok is False


# ── Validar seed completo ────────────────────────────────────

class TestValidarSeedPreEnvio:
    def test_seed_minimo_valido_pasa(self):
        r = validar_seed_pre_envio(_seed_valido_minimo())
        assert not r.tiene_errores(), f"errores inesperados: {r.errores}"

    def test_cedula_propietario_vacia_es_error(self):
        s = _seed_valido_minimo()
        s["propietario"]["cedula"] = ""
        r = validar_seed_pre_envio(s)
        assert r.tiene_errores()
        assert any("cedula" in e.lower() for e in r.errores)

    def test_cedula_propietario_mal_formateada_es_error(self):
        s = _seed_valido_minimo()
        s["propietario"]["cedula"] = "20466009"  # sin guiones
        r = validar_seed_pre_envio(s)
        assert r.tiene_errores()

    def test_cedula_juridica_truncada_es_warning_si_flag(self):
        """Si cédula_registro_original está, se degrada de error a warning."""
        s = _seed_valido_minimo()
        s["propietario"] = {
            "tipo_cedula": "2",
            "cedula": "3-101-",  # truncada
            "cedula_registro_original": "3-101-",  # flag operador
        }
        r = validar_seed_pre_envio(s)
        # Debe ser warning, no error (el operador ya la marcó)
        # Aunque hay otro check de cédula formato — verificar que no bloquea
        for e in r.errores:
            if "cedula" in e.lower() and "mal formateada" in e.lower():
                pytest.fail(f"cédula corregida no debería ser error: {e}")

    def test_finca_vacia_es_error(self):
        s = _seed_valido_minimo()
        s["plano"]["fincas"] = []
        r = validar_seed_pre_envio(s)
        assert r.tiene_errores()
        assert any("fincas" in e.lower() for e in r.errores)

    def test_entero_vacio_es_error(self):
        s = _seed_valido_minimo()
        s["plano"]["entero"]["numero"] = ""
        r = validar_seed_pre_envio(s)
        assert r.tiene_errores()

    def test_entero_no_9_digitos_es_warning(self):
        s = _seed_valido_minimo()
        s["plano"]["entero"]["numero"] = "12345"
        r = validar_seed_pre_envio(s)
        assert any("9 dígitos" in w for w in r.advertencias)

    def test_plano_modificar_invalido_es_error(self):
        s = _seed_valido_minimo()
        s["plano"]["planos_modificar"] = [{"provincia": "9", "numero": "X", "anno": "AAA"}]
        r = validar_seed_pre_envio(s)
        assert r.tiene_errores()

    def test_honorarios_cero_sin_observaciones_es_error(self):
        """Si honorarios=0, exoneración debe estar marcada Y observaciones llenas."""
        s = _seed_valido_minimo()
        s["general"]["honorarios"] = "0"
        s["general"]["exoneracion_honorarios"] = False
        s["general"]["observaciones"] = ""
        r = validar_seed_pre_envio(s)
        assert r.tiene_errores()
        # Debe haber al menos un error de exoneración Y uno de observaciones
        assert any("exoneración" in e.lower() or "exonerac" in e.lower() for e in r.errores)
        assert any("observaciones" in e.lower() for e in r.errores)

    def test_honorarios_cero_con_exoneracion_y_obs_pasa(self):
        s = _seed_valido_minimo()
        s["general"]["honorarios"] = "0"
        s["general"]["exoneracion_honorarios"] = True
        s["general"]["observaciones"] = "Continuación del contrato 1209447"
        r = validar_seed_pre_envio(s)
        assert not r.tiene_errores()

    def test_area_zona_inconsistente_es_warning(self):
        """Si tipo_zona=RURAL pero área <2000, advertir."""
        s = _seed_valido_minimo()
        s["plano"]["tipo_zona"] = "2"  # RURAL
        s["plano"]["area_real"] = "500"  # menor a 2000
        r = validar_seed_pre_envio(s)
        assert any("tipo_zona" in w.lower() or "sugiere" in w.lower()
                   for w in r.advertencias)

    def test_provincia_vacia_es_error(self):
        s = _seed_valido_minimo()
        s["proyecto"]["provincia"] = ""
        r = validar_seed_pre_envio(s)
        assert r.tiene_errores()
        assert any("provincia" in e.lower() for e in r.errores)


class TestPreflightResultResumen:
    def test_resumen_con_errores(self):
        r = PreflightResult(errores=["err1", "err2"])
        s = r.resumen()
        assert "2 ERROR" in s
        assert "err1" in s

    def test_resumen_vacio(self):
        r = PreflightResult()
        # info vacío también — solo dice "sin problemas"
        assert "Sin problemas detectados" in r.resumen()
