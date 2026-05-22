"""Tests del módulo de consulta de reglas."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.utils.reglas_consulta import (
    listar_reglas_activas, buscar_regla, consultar_por_categoria,
    categorias_disponibles, insertar_regla_nueva, estadisticas,
    desactivar_regla, historial_regla,
)


@pytest.fixture
def db_temp(tmp_path):
    """BD temporal con la tabla apt_memoria_operador + datos de prueba."""
    db_path = tmp_path / "test_reglas.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE apt_memoria_operador (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo TEXT NOT NULL,
            patron TEXT NOT NULL,
            descripcion TEXT,
            operador TEXT,
            activa INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            caso_real TEXT,
            evidencia_paths TEXT,
            superseded_by INTEGER,
            version INTEGER DEFAULT 1
        )
    """)
    conn.execute("""
        CREATE TABLE apt_reglas_historia (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            regla_id INTEGER NOT NULL,
            patron TEXT NOT NULL,
            descripcion_old TEXT,
            descripcion_new TEXT,
            activa_old INTEGER,
            activa_new INTEGER,
            cambio_por TEXT,
            razon_cambio TEXT,
            ts TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    # Datos de prueba simulando lo real
    reglas = [
        ("regla", "monto_pagado_es_TASADO", "El monto pagado del entero es tasado", "op", 1),
        ("regla", "bp6_panel_es_P6", "bP6 del plano vive en panel #P6", "op", 1),
        ("regla", "bp4_titulares_uno_por_finca", "Un titular por propietario", "op", 1),
        ("regla", "regla_vieja_inactiva", "Estaba mal", "op", 0),
        ("regla", "doble_chequeo_obligatorio", "Doble chequear todo", "op", 1),
        ("regla", "areas_contrato_multi", "Dedup fincas únicas", "op", 1),
    ]
    for tipo, patron, desc, oper, activa in reglas:
        conn.execute(
            "INSERT INTO apt_memoria_operador (tipo, patron, descripcion, operador, activa) "
            "VALUES (?, ?, ?, ?, ?)",
            (tipo, patron, desc, oper, activa),
        )
    conn.commit()
    conn.close()
    return db_path


class TestListar:
    def test_solo_activas_default(self, db_temp):
        rs = listar_reglas_activas(db_path=db_temp)
        assert len(rs) == 5  # 6 totales, 1 inactiva
        assert all(r["activa"] == 1 for r in rs)

    def test_incluir_inactivas(self, db_temp):
        rs = listar_reglas_activas(db_path=db_temp, incluir_inactivas=True)
        assert len(rs) == 6

    def test_orden_por_id(self, db_temp):
        rs = listar_reglas_activas(db_path=db_temp)
        patrones = [r["patron"] for r in rs]
        # Orden de inserción
        assert patrones[0] == "monto_pagado_es_TASADO"


class TestBuscar:
    def test_busca_por_patron(self, db_temp):
        rs = buscar_regla("monto", db_path=db_temp)
        assert len(rs) == 1
        assert "monto" in rs[0]["patron"].lower()

    def test_busca_por_descripcion(self, db_temp):
        rs = buscar_regla("tasado", db_path=db_temp)
        assert len(rs) == 1
        assert "tasado" in rs[0]["descripcion"].lower()

    def test_busca_case_insensitive(self, db_temp):
        rs1 = buscar_regla("BP6", db_path=db_temp)
        rs2 = buscar_regla("bp6", db_path=db_temp)
        assert len(rs1) == len(rs2) > 0

    def test_palabra_vacia(self, db_temp):
        assert buscar_regla("", db_path=db_temp) == []

    def test_sin_coincidencias(self, db_temp):
        assert buscar_regla("xyzqwerty", db_path=db_temp) == []

    def test_ignora_inactivas(self, db_temp):
        rs = buscar_regla("vieja", db_path=db_temp)
        assert rs == []  # la regla_vieja_inactiva no aparece


class TestCategoria:
    def test_bp6_devuelve_bp6_panel(self, db_temp):
        rs = consultar_por_categoria("bp6", db_path=db_temp)
        patrones = {r["patron"] for r in rs}
        assert "bp6_panel_es_P6" in patrones
        # monto_pagado_es_TASADO también es de bp6 (categoria contiene "monto_")
        assert "monto_pagado_es_TASADO" in patrones

    def test_bp4_devuelve_titulares(self, db_temp):
        rs = consultar_por_categoria("bp4", db_path=db_temp)
        patrones = {r["patron"] for r in rs}
        assert "bp4_titulares_uno_por_finca" in patrones

    def test_doble_chequeo_devuelve_meta(self, db_temp):
        rs = consultar_por_categoria("doble_chequeo", db_path=db_temp)
        assert any("doble_chequeo" in r["patron"] for r in rs)

    def test_categoria_invalida_devuelve_vacio(self, db_temp):
        assert consultar_por_categoria("inexistente", db_path=db_temp) == []

    def test_categorias_disponibles_no_vacio(self):
        cats = categorias_disponibles()
        assert "bp1" in cats
        assert "bp6" in cats
        assert "meta" in cats


class TestInsertarRegla:
    def test_inserta_nueva(self, db_temp):
        rid = insertar_regla_nueva(
            patron="regla_test_nueva",
            descripcion="Descripción de la regla nueva",
            db_path=db_temp,
        )
        assert rid > 0
        rs = buscar_regla("regla_test_nueva", db_path=db_temp)
        assert len(rs) == 1

    def test_actualiza_existente(self, db_temp):
        # Insertar la misma regla 2 veces — segunda actualiza
        rid1 = insertar_regla_nueva(
            patron="regla_dup", descripcion="v1", db_path=db_temp,
        )
        rid2 = insertar_regla_nueva(
            patron="regla_dup", descripcion="v2", db_path=db_temp,
        )
        assert rid1 == rid2  # mismo id, descripción actualizada
        rs = buscar_regla("regla_dup", db_path=db_temp)
        assert rs[0]["descripcion"] == "v2"

    def test_reactiva_inactiva(self, db_temp):
        # La regla_vieja_inactiva está con activa=0; insertar la actualiza activa=1
        rid = insertar_regla_nueva(
            patron="regla_vieja_inactiva",
            descripcion="Reactivada",
            db_path=db_temp,
        )
        rs = listar_reglas_activas(db_path=db_temp)
        patrones = {r["patron"] for r in rs}
        assert "regla_vieja_inactiva" in patrones

    def test_patron_vacio_lanza(self, db_temp):
        with pytest.raises(ValueError):
            insertar_regla_nueva(patron="", descripcion="x", db_path=db_temp)

    def test_descripcion_vacia_lanza(self, db_temp):
        with pytest.raises(ValueError):
            insertar_regla_nueva(patron="x", descripcion="", db_path=db_temp)


class TestEstadisticas:
    def test_stats_total_y_activas(self, db_temp):
        s = estadisticas(db_path=db_temp)
        assert s["total"] == 6
        assert s["activas"] == 5

    def test_stats_por_categoria(self, db_temp):
        s = estadisticas(db_path=db_temp)
        assert "bp6" in s["por_categoria"]
        assert s["por_categoria"]["bp4"] >= 1


# ── Versionado y desactivación (agregado 2026-05-14) ──────────────────

class TestVersionado:
    def test_insertar_con_caso_real_y_evidencia(self, db_temp):
        rid = insertar_regla_nueva(
            patron="regla_con_caso",
            descripcion="x",
            caso_real="TILMAN SEG-2026-003 — 2026-05-13",
            evidencia_paths=["snap1.png", "snap2.png"],
            db_path=db_temp,
        )
        rs = buscar_regla("regla_con_caso", db_path=db_temp)
        assert len(rs) == 1
        # Verificar que caso_real está guardado
        import sqlite3
        conn = sqlite3.connect(str(db_temp))
        r = conn.execute("SELECT caso_real, evidencia_paths, version FROM apt_memoria_operador WHERE id=?", (rid,)).fetchone()
        conn.close()
        assert "TILMAN" in r[0]
        assert "snap1.png" in r[1]
        assert r[2] == 1

    def test_actualizar_existente_aumenta_version(self, db_temp):
        rid1 = insertar_regla_nueva(
            patron="regla_v", descripcion="v1", db_path=db_temp,
        )
        rid2 = insertar_regla_nueva(
            patron="regla_v", descripcion="v2", db_path=db_temp,
        )
        assert rid1 == rid2
        import sqlite3
        conn = sqlite3.connect(str(db_temp))
        v = conn.execute("SELECT version FROM apt_memoria_operador WHERE id=?", (rid1,)).fetchone()[0]
        conn.close()
        assert v == 2

    def test_actualizar_registra_historia(self, db_temp):
        insertar_regla_nueva(patron="regla_h", descripcion="v1", db_path=db_temp)
        insertar_regla_nueva(patron="regla_h", descripcion="v2",
                             operador="op-test", db_path=db_temp)
        hist = historial_regla("regla_h", db_path=db_temp)
        assert len(hist) == 1
        assert hist[0]["descripcion_old"] == "v1"
        assert hist[0]["descripcion_new"] == "v2"
        assert hist[0]["cambio_por"] == "op-test"

    def test_desactivar_marca_activa_0(self, db_temp):
        rid = insertar_regla_nueva(patron="a_desactivar", descripcion="x",
                                   db_path=db_temp)
        ok = desactivar_regla(patron="a_desactivar",
                              razon="ya no aplica", db_path=db_temp)
        assert ok is True
        rs = listar_reglas_activas(db_path=db_temp)
        patrones = {r["patron"] for r in rs}
        assert "a_desactivar" not in patrones

    def test_desactivar_con_superseded_by(self, db_temp):
        insertar_regla_nueva(patron="vieja", descripcion="v", db_path=db_temp)
        insertar_regla_nueva(patron="nueva", descripcion="n", db_path=db_temp)
        desactivar_regla(patron="vieja", razon="reemplazada",
                         superseded_by_patron="nueva", db_path=db_temp)
        import sqlite3
        conn = sqlite3.connect(str(db_temp))
        nueva_id = conn.execute("SELECT id FROM apt_memoria_operador WHERE patron='nueva'").fetchone()[0]
        super_id = conn.execute("SELECT superseded_by FROM apt_memoria_operador WHERE patron='vieja'").fetchone()[0]
        conn.close()
        assert super_id == nueva_id

    def test_desactivar_inexistente_devuelve_false(self, db_temp):
        ok = desactivar_regla(patron="nunca_existio", razon="x", db_path=db_temp)
        assert ok is False
