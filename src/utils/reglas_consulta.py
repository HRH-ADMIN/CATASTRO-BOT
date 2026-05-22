"""Consulta de reglas operativas aprendidas (apt_memoria_operador).

Sirve para que el bot (en runtime) o un humano (CLI) consulte qué reglas
están activas, busque por patrón, o liste por categoría.

Modelo conceptual:
  - Las reglas se categorizan implícitamente por prefijo del `patron`:
      bp1_*, bp2_*, bp4_*, bp6_*  → reglas de bP individual
      areas_*, honorarios_*, n_planos_* → consolidación contrato
      R<N>_*                            → versiones corregidas de reglas viejas
      verificar_*, doble_chequeo_*      → meta-reglas de validación
      monto_*, entero_*                 → reglas de enteros BCR
  - Cada regla tiene una `descripcion` libre con el caso real.

USO programático:
    from src.utils.reglas_consulta import (
        listar_reglas_activas, buscar_regla, consultar_por_categoria,
    )

    # Antes de tocar un campo, consultar reglas relevantes
    reglas = buscar_regla("monto_pagado")
    for r in reglas:
        print(r["descripcion"])

USO CLI:
    catastro-bot reglas listar
    catastro-bot reglas buscar monto
    catastro-bot reglas categoria bp6
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional


_DB_DEFAULT = Path("data/catastro.db")


def _conectar(db_path: Optional[Path] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or _DB_DEFAULT))
    conn.row_factory = sqlite3.Row
    return conn


def listar_reglas_activas(
    *, db_path: Optional[Path] = None,
    incluir_inactivas: bool = False,
) -> list[dict]:
    """Devuelve todas las reglas activas (o todas si incluir_inactivas=True)."""
    conn = _conectar(db_path)
    try:
        sql = ("SELECT patron, descripcion, activa, created_at "
               "FROM apt_memoria_operador WHERE tipo = 'regla'")
        if not incluir_inactivas:
            sql += " AND activa = 1"
        sql += " ORDER BY id"
        return [dict(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()


def buscar_regla(
    palabra_clave: str, *, db_path: Optional[Path] = None,
) -> list[dict]:
    """Busca reglas cuyo patron o descripción contengan `palabra_clave`."""
    if not palabra_clave:
        return []
    conn = _conectar(db_path)
    try:
        pattern = f"%{palabra_clave.lower()}%"
        return [dict(r) for r in conn.execute(
            "SELECT patron, descripcion FROM apt_memoria_operador "
            "WHERE tipo = 'regla' AND activa = 1 "
            "AND (LOWER(patron) LIKE ? OR LOWER(descripcion) LIKE ?) "
            "ORDER BY id",
            (pattern, pattern),
        ).fetchall()]
    finally:
        conn.close()


_CATEGORIAS = {
    "bp1":   ["bp1_", "tamanno_", "tipo_uso_"],
    "bp2":   ["bp2_"],
    "bp4":   ["bp4_", "titular"],
    "bp6":   ["bp6_", "monto_", "entero_"],
    "bp7":   ["bp7_", "archivo"],
    "contrato": ["areas_contrato", "honorarios_contrato", "n_planos_",
                 "contrato_multi", "cross_check"],
    "doble_chequeo": ["doble_chequeo", "verificar_", "revisar_"],
    "navegacion":  ["navegar_", "crear_segundo"],
    "meta":  ["cuando_corrijas", "preguntar_", "nunca_reincidir",
              "R1_corregida", "R16_corregida"],
}


def consultar_por_categoria(
    categoria: str, *, db_path: Optional[Path] = None,
) -> list[dict]:
    """Devuelve reglas de una categoría.

    Categorías válidas: bp1, bp2, bp4, bp6, bp7, contrato, doble_chequeo,
    navegacion, meta.
    """
    cat = categoria.lower().strip()
    if cat not in _CATEGORIAS:
        return []
    prefijos = _CATEGORIAS[cat]
    conn = _conectar(db_path)
    try:
        clauses = " OR ".join(["LOWER(patron) LIKE ?"] * len(prefijos))
        sql = (
            f"SELECT patron, descripcion FROM apt_memoria_operador "
            f"WHERE tipo = 'regla' AND activa = 1 AND ({clauses}) ORDER BY id"
        )
        params = [f"%{p.lower()}%" for p in prefijos]
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def categorias_disponibles() -> list[str]:
    return sorted(_CATEGORIAS.keys())


def insertar_regla_nueva(
    *,
    patron: str,
    descripcion: str,
    operador: str = "claude",
    caso_real: Optional[str] = None,
    evidencia_paths: Optional[list[str]] = None,
    db_path: Optional[Path] = None,
) -> int:
    """Inserta una regla nueva. Si el patrón ya existe, ACTUALIZA + registra historia.

    Args:
        patron: identificador único (snake_case).
        descripcion: cuerpo de la regla.
        operador: quién la creó (default "claude").
        caso_real: ej "TILMAN SEG-2026-003 — 2026-05-13".
        evidencia_paths: lista de paths a screenshots/snapshots como evidencia.

    Returns: id de la regla.
    """
    if not patron or not descripcion:
        raise ValueError("patron y descripcion son obligatorios")
    import json as _json
    conn = _conectar(db_path)
    try:
        existing = conn.execute(
            "SELECT id, descripcion, activa, version FROM apt_memoria_operador "
            "WHERE patron = ? AND tipo = 'regla'",
            (patron,),
        ).fetchone()
        evidencia_json = (
            _json.dumps(evidencia_paths, ensure_ascii=False)
            if evidencia_paths else None
        )
        if existing:
            # Registrar cambio en historia
            try:
                conn.execute(
                    "INSERT INTO apt_reglas_historia "
                    "(regla_id, patron, descripcion_old, descripcion_new, "
                    " activa_old, activa_new, cambio_por, razon_cambio) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (existing["id"], patron, existing["descripcion"],
                     descripcion, existing["activa"], 1, operador,
                     "update via insertar_regla_nueva"),
                )
            except Exception:
                pass  # tabla puede no existir en BDs antiguas
            new_version = (existing["version"] or 1) + 1
            conn.execute(
                "UPDATE apt_memoria_operador SET descripcion = ?, activa = 1, "
                "version = ?, caso_real = COALESCE(?, caso_real), "
                "evidencia_paths = COALESCE(?, evidencia_paths) "
                "WHERE id = ?",
                (descripcion, new_version, caso_real, evidencia_json,
                 existing["id"]),
            )
            conn.commit()
            return existing["id"]
        cur = conn.execute(
            "INSERT INTO apt_memoria_operador "
            "(tipo, patron, descripcion, operador, activa, caso_real, "
            " evidencia_paths, version) "
            "VALUES ('regla', ?, ?, ?, 1, ?, ?, 1)",
            (patron, descripcion, operador, caso_real, evidencia_json),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def desactivar_regla(
    *,
    patron: str,
    razon: str,
    superseded_by_patron: Optional[str] = None,
    operador: str = "claude",
    db_path: Optional[Path] = None,
) -> bool:
    """Desactiva una regla (activa=0) y registra el motivo en historia.

    Returns: True si se desactivó, False si no existía.
    """
    conn = _conectar(db_path)
    try:
        r = conn.execute(
            "SELECT id, descripcion, activa FROM apt_memoria_operador "
            "WHERE patron = ? AND tipo = 'regla'",
            (patron,),
        ).fetchone()
        if not r:
            return False
        superseded_id = None
        if superseded_by_patron:
            row = conn.execute(
                "SELECT id FROM apt_memoria_operador WHERE patron = ?",
                (superseded_by_patron,),
            ).fetchone()
            superseded_id = row["id"] if row else None
        conn.execute(
            "UPDATE apt_memoria_operador SET activa = 0, "
            "superseded_by = COALESCE(?, superseded_by) WHERE id = ?",
            (superseded_id, r["id"]),
        )
        try:
            conn.execute(
                "INSERT INTO apt_reglas_historia "
                "(regla_id, patron, descripcion_old, descripcion_new, "
                " activa_old, activa_new, cambio_por, razon_cambio) "
                "VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
                (r["id"], patron, r["descripcion"], r["descripcion"],
                 r["activa"], operador, razon),
            )
        except Exception:
            pass
        conn.commit()
        return True
    finally:
        conn.close()


def historial_regla(
    patron: str, *, db_path: Optional[Path] = None,
) -> list[dict]:
    """Devuelve historial de cambios de una regla por patrón."""
    conn = _conectar(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM apt_reglas_historia WHERE patron = ? "
            "ORDER BY ts DESC",
            (patron,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def estadisticas(*, db_path: Optional[Path] = None) -> dict:
    """Resumen: total reglas, activas, por categoría."""
    conn = _conectar(db_path)
    try:
        total = conn.execute(
            "SELECT COUNT(*) FROM apt_memoria_operador WHERE tipo='regla'"
        ).fetchone()[0]
        activas = conn.execute(
            "SELECT COUNT(*) FROM apt_memoria_operador "
            "WHERE tipo='regla' AND activa=1"
        ).fetchone()[0]
    finally:
        conn.close()

    por_categoria = {}
    for cat in _CATEGORIAS:
        por_categoria[cat] = len(consultar_por_categoria(cat, db_path=db_path))
    return {
        "total": total,
        "activas": activas,
        "por_categoria": por_categoria,
    }


__all__ = [
    "listar_reglas_activas", "buscar_regla", "consultar_por_categoria",
    "categorias_disponibles", "insertar_regla_nueva", "estadisticas",
    "desactivar_regla", "historial_regla",
]
