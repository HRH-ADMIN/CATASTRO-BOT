"""Tests del apt_auditor — verificaciones obligatorias post-llenado.

Caso real VICTOR #2 originó las reglas.
"""
from __future__ import annotations

import pytest

from src.utils.apt_auditor import (
    parsear_nombre_servidor_apt,
    verificar_archivos_subidos_por_plano,
    verificar_titulares_vs_fincas,
    verificar_seed_vs_cajetin_pdf,
)


# ── parsear_nombre_servidor_apt ───────────────────────────────────────

class TestParsearNombre:
    def test_anverso_pdf(self):
        r = parsear_nombre_servidor_apt("639141960687378456_1075911_anverso.pdf")
        assert r["plano_id"] == 1075911
        assert r["tipo"] == "anverso"

    def test_entero_pdf(self):
        r = parsear_nombre_servidor_apt("xxx_1075920_entero.pdf")
        assert r["plano_id"] == 1075920
        assert r["tipo"] == "entero"

    def test_derrotero_zip(self):
        r = parsear_nombre_servidor_apt("a_1234567_derrotero.zip")
        assert r["plano_id"] == 1234567
        assert r["tipo"] == "derrotero"

    def test_visado_pdf(self):
        r = parsear_nombre_servidor_apt("b_999_visado.pdf")
        assert r["plano_id"] == 999
        assert r["tipo"] == "visado"

    def test_nombre_no_apt(self):
        r = parsear_nombre_servidor_apt("Derrotero 1 .zip")
        assert r["plano_id"] is None
        assert r["tipo"] is None

    def test_case_insensitive_tipo(self):
        r = parsear_nombre_servidor_apt("x_111_ANVERSO.PDF")
        assert r["tipo"] == "anverso"  # normalizado lowercase

    def test_string_vacio(self):
        r = parsear_nombre_servidor_apt("")
        assert r["plano_id"] is None


# ── verificar_archivos_subidos_por_plano ──────────────────────────────

class TestVerificarArchivos:
    def test_los_3_correctos_OK(self):
        """Caso VICTOR #2 plano 1 correcto."""
        archivos = [
            {"nombre_servidor": "x_1075911_anverso.pdf"},
            {"nombre_servidor": "y_1075911_entero.pdf"},
            {"nombre_servidor": "z_1075911_derrotero.zip"},
        ]
        r = verificar_archivos_subidos_por_plano(
            archivos_actuales=archivos, plano_id_esperado=1075911,
        )
        assert r["ok"] is True
        assert r["errores"] == []
        assert r["tipos_presentes"] == {"anverso", "entero", "derrotero"}

    def test_archivo_mezclado_de_otro_plano(self):
        """Caso real VICTOR #2: plano 1 tenía archivos del plano 2."""
        archivos = [
            {"nombre_servidor": "x_1075911_anverso.pdf"},  # OK
            {"nombre_servidor": "y_1075920_entero.pdf"},   # DEL PLANO 2!
            {"nombre_servidor": "z_1075911_derrotero.zip"},
        ]
        r = verificar_archivos_subidos_por_plano(
            archivos_actuales=archivos, plano_id_esperado=1075911,
        )
        assert r["ok"] is False
        assert any("MEZCLADOS" in e for e in r["errores"])
        assert any("1075920" in e for e in r["errores"])

    def test_falta_anverso(self):
        """Caso real VICTOR #2 plano 2 inicial: faltaba anverso."""
        archivos = [
            {"nombre_servidor": "y_1075920_entero.pdf"},
            {"nombre_servidor": "z_1075920_derrotero.zip"},
        ]
        r = verificar_archivos_subidos_por_plano(
            archivos_actuales=archivos, plano_id_esperado=1075920,
        )
        assert r["ok"] is False
        assert any("FALTA" in e and "anverso" in e for e in r["errores"])

    def test_archivo_no_apt_warning(self):
        """Si el nombre no matchea patrón APT, da advertencia."""
        archivos = [
            {"nombre_servidor": "Derrotero 1 .zip"},  # nombre local, no servidor
        ]
        r = verificar_archivos_subidos_por_plano(
            archivos_actuales=archivos, plano_id_esperado=1075911,
        )
        assert any("no matchea patrón" in a for a in r["advertencias"])

    def test_tipos_extranos_advertencia(self):
        archivos = [
            {"nombre_servidor": "a_111_anverso.pdf"},
            {"nombre_servidor": "b_111_entero.pdf"},
            {"nombre_servidor": "c_111_derrotero.zip"},
            {"nombre_servidor": "d_111_visado.pdf"},  # extra
        ]
        r = verificar_archivos_subidos_por_plano(
            archivos_actuales=archivos, plano_id_esperado=111,
        )
        # los 3 esperados están, y hay 1 extra → advertencia
        assert any("visado" in a for a in r["advertencias"])


# ── verificar_titulares_vs_fincas ─────────────────────────────────────

class TestVerificarTitulares:
    def test_titular_falta_caso_victor2(self):
        """Plano 2 segregación: finca 161099 de Rodolfo SA, pero bP4 solo tenía María."""
        r = verificar_titulares_vs_fincas(
            fincas_bp2=[{"numero": "161099", "derecho": "000"}],
            titulares_bp4=[{"cedula": "1-0940-0768", "nombre": "MARIA"}],
            propietarios_por_finca={"161099": "3-101-155297"},  # Rodolfo
        )
        assert r["ok"] is False
        assert any("3101155297" in e for e in r["errores"])

    def test_dos_fincas_dos_titulares_OK(self):
        """Caso VICTOR #2 plano 1 corregido."""
        r = verificar_titulares_vs_fincas(
            fincas_bp2=[
                {"numero": "642038", "derecho": "000"},
                {"numero": "161099", "derecho": "000"},
            ],
            titulares_bp4=[
                {"cedula": "1-0940-0768", "nombre": "MARIA"},
                {"cedula": "3-101-155297", "nombre": "RODOLFO SA"},
            ],
            propietarios_por_finca={
                "642038": "1-0940-0768",
                "161099": "3-101-155297",
            },
        )
        assert r["ok"] is True

    def test_sin_propietarios_provistos_solo_advertencia(self):
        """Si caller no provee tabla de propietarios, no puede errar, solo advierte."""
        r = verificar_titulares_vs_fincas(
            fincas_bp2=[{"numero": "111"}, {"numero": "222"}],
            titulares_bp4=[{"cedula": "1-1111-1111"}],
        )
        assert r["ok"] is True  # no error sin info
        assert any("verificar" in a.lower() for a in r["advertencias"])


# ── verificar_seed_vs_cajetin_pdf ─────────────────────────────────────

class TestVerificarSeedCajetin:
    def test_match_perfecto(self):
        seed = {
            "descripcion": "ALEPOA (1)",
            "area_real": "2751.30",
            "protocolo": {"numero": "24162", "folio": "104"},
            "entero": {"numero": "661177998"},
            "profesional_carne": "IT10676",
        }
        cajetin = {
            "descripcion": "ALEPOA (1)",
            "area_real": "2751.30",
            "protocolo_tomo": "24162",
            "protocolo_folio": "104",
            "numero_entero": "661177998",
            "profesional_carne": "IT10676",
        }
        r = verificar_seed_vs_cajetin_pdf(
            seed_plano=seed, cajetin_extraido=cajetin,
        )
        assert r["ok"] is True
        assert r["errores"] == []

    def test_area_discrepancia_detectada(self):
        seed = {"area_real": "2751.30"}
        cajetin = {"area_real": "2097.00"}  # MUY diferente
        r = verificar_seed_vs_cajetin_pdf(
            seed_plano=seed, cajetin_extraido=cajetin,
        )
        assert r["ok"] is False
        assert any("area_real" in e for e in r["errores"])

    def test_protocolo_distinto(self):
        seed = {"protocolo": {"numero": "24162", "folio": "104"}}
        cajetin = {"protocolo_tomo": "99999", "protocolo_folio": "1"}
        r = verificar_seed_vs_cajetin_pdf(
            seed_plano=seed, cajetin_extraido=cajetin,
        )
        assert r["ok"] is False
        assert any("protocolo" in e for e in r["errores"])

    def test_descripcion_distinta(self):
        seed = {"descripcion": "ALEPOA (1)"}
        cajetin = {"descripcion": "MARRO (3)"}
        r = verificar_seed_vs_cajetin_pdf(
            seed_plano=seed, cajetin_extraido=cajetin,
        )
        assert r["ok"] is False
        assert any("descripcion" in e for e in r["errores"])

    def test_tolerancia_area_pct_pequena_OK(self):
        """Diferencia 0.05% es aceptable (default tolerancia 0.1%)."""
        seed    = {"area_real": "2751.30"}
        cajetin = {"area_real": "2752.50"}  # 0.04% diff
        r = verificar_seed_vs_cajetin_pdf(
            seed_plano=seed, cajetin_extraido=cajetin,
        )
        assert r["ok"] is True

    def test_profesional_solo_advertencia(self):
        seed    = {"profesional_carne": "IT10676"}
        cajetin = {"profesional_carne": "IT99999"}
        r = verificar_seed_vs_cajetin_pdf(
            seed_plano=seed, cajetin_extraido=cajetin,
        )
        # diferencia en profesional NO es bloqueante (solo advertencia)
        assert r["ok"] is True
        assert any("profesional" in a for a in r["advertencias"])


# ── doble_chequeo_plano (regla META) ──────────────────────────────────

from src.utils.apt_auditor import (
    doble_chequeo_plano, doble_chequeo_contrato_multi_plano,
)


class TestDobleChequeoPlano:
    def _snap_completo_ok(self):
        return {
            "iconos": {f"bP{i}": "OK" for i in range(1, 8)},
            "bp1": {
                "area_real": "2751.30",
                "area_registro": "10582.81",
                "tamanno": "1",
                "tipo_zona": "2",
                "tipo_uso": "3",
                "tipo_coord": "3",
                "norte": "1120570",
                "este": "478289",
                "vertices": "12",
            },
            "fincas": [
                {"numero": "642038"},
                {"numero": "161099"},
            ],
            "titulares": [
                {"cedula": "1-0940-0768"},
                {"cedula": "3-101-155297"},
            ],
            "archivos": [
                {"nombre_servidor": "x_1075911_anverso.pdf"},
                {"nombre_servidor": "y_1075911_entero.pdf"},
                {"nombre_servidor": "z_1075911_derrotero.zip"},
            ],
        }

    def test_caso_perfecto_OK(self):
        snap = self._snap_completo_ok()
        r = doble_chequeo_plano(
            snap_plano=snap,
            seed_plano={"area_real": "2751.30"},
            plano_id_apt=1075911,
            propietarios_por_finca={
                "642038": "1-0940-0768",
                "161099": "3-101-155297",
            },
        )
        assert r["ok"] is True
        assert r["errores"] == []

    def test_seccion_roja_detectada(self):
        snap = self._snap_completo_ok()
        snap["iconos"]["bP6"] = "ROJO"
        r = doble_chequeo_plano(snap_plano=snap, seed_plano={})
        assert r["ok"] is False
        assert any("ROJ" in e for e in r["errores"])

    def test_campo_bp1_vacio_falla(self):
        snap = self._snap_completo_ok()
        snap["bp1"]["tamanno"] = ""  # vacío
        r = doble_chequeo_plano(snap_plano=snap, seed_plano={})
        assert r["ok"] is False
        assert any("tamanno" in e for e in r["errores"])

    def test_archivos_mezclados_detectados(self):
        snap = self._snap_completo_ok()
        snap["archivos"][0]["nombre_servidor"] = "x_1075920_anverso.pdf"  # del otro plano
        r = doble_chequeo_plano(
            snap_plano=snap, seed_plano={}, plano_id_apt=1075911,
        )
        assert r["ok"] is False
        assert any("MEZCLADOS" in e for e in r["errores"])

    def test_titular_faltante_detectado(self):
        snap = self._snap_completo_ok()
        snap["titulares"] = [{"cedula": "1-0940-0768"}]  # falta Rodolfo
        r = doble_chequeo_plano(
            snap_plano=snap, seed_plano={},
            propietarios_por_finca={
                "642038": "1-0940-0768",
                "161099": "3-101-155297",
            },
        )
        assert r["ok"] is False
        assert any("3101155297" in e for e in r["errores"])

    def test_pocos_archivos_falla(self):
        snap = self._snap_completo_ok()
        snap["archivos"] = snap["archivos"][:2]  # solo 2
        r = doble_chequeo_plano(snap_plano=snap, seed_plano={})
        assert r["ok"] is False
        assert any("3" in e for e in r["errores"])

    def test_seed_vs_cajetin_detecta_area(self):
        snap = self._snap_completo_ok()
        r = doble_chequeo_plano(
            snap_plano=snap,
            seed_plano={"area_real": "2751.30"},
            cajetin_extraido={"area_real": "9999"},  # MAL
        )
        assert r["ok"] is False
        assert any("area_real" in e for e in r["errores"])


class TestDobleChequeoContrato:
    def test_victor2_caso_real_OK(self):
        """Verificación final R1+R3: bC7 contra suma de bP1."""
        planos = [
            {"bp1": {"area_real": "2751.30", "area_registro": "10582.81"}},
            {"bp1": {"area_real": "2097.00", "area_registro": "8082.81"}},
        ]
        bc7 = {
            "area_real":   "4848.30",     # suma de los 2
            "area_predio": "18665.62",    # 10582.81 + 8082.81
            "max_planos":  "2",
        }
        r = doble_chequeo_contrato_multi_plano(
            planos_snap=planos, contrato_bc7=bc7,
        )
        assert r["ok"] is True
        assert r["suma_real"] == 4848.30
        assert r["n_planos"] == 2

    def test_bc7_area_real_no_suma_falla(self):
        planos = [
            {"bp1": {"area_real": "1000", "area_registro": "1000"}},
            {"bp1": {"area_real": "2000", "area_registro": "2000"}},
        ]
        bc7 = {
            "area_real": "1000",     # MAL: solo el primero
            "area_predio": "3000",
            "max_planos": "2",
        }
        r = doble_chequeo_contrato_multi_plano(
            planos_snap=planos, contrato_bc7=bc7,
        )
        assert r["ok"] is False
        assert any("area_real" in e for e in r["errores"])

    def test_max_planos_no_matchea_falla(self):
        planos = [{"bp1": {"area_real": "1000", "area_registro": "1000"}}] * 3
        bc7 = {"area_real": "3000", "area_predio": "3000", "max_planos": "1"}
        r = doble_chequeo_contrato_multi_plano(
            planos_snap=planos, contrato_bc7=bc7,
        )
        assert r["ok"] is False
        assert any("max_planos" in e for e in r["errores"])
