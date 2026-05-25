"""Cola de revisión visual pre-envío (Sprint 5 / N-02).

Captura el estado del portal CFIA + PDF anverso + diff de campos críticos
ANTES de hacer click irreversible en "Enviar al CFIA". El operador revisa
en `/expediente/<id>/revisar-envio` y aprueba/rechaza explícitamente.

Flujo:
  1. `crear_revision(expediente_id, page, datos_apt) → rev_id`
     - Captura snapshot DOM de los campos bP1-bP4 del portal.
     - Toma screenshot PNG (data/revisiones/<rev_id>.png).
     - Localiza el PDF anverso del expediente.
     - Calcula diff seed vs portal.
     - Persiste fila 'pendiente' en revisiones_pre_envio.
     - Publica SSE event 'revision_pendiente'.
     - Devuelve rev_id (UUID).

  2. Operador abre /expediente/<exp_id>/revisar-envio.
     - Side-by-side: PDF a la izq, screenshot a la der.
     - Checklist de campos críticos con check verde/rojo según diff.
     - Botones aprobar/rechazar.

  3. Aprobado → caller original (apt-enviar) hace el click real.
     Rechazado → audit log entry, workflow se mantiene en estado previo.

Diseño:
  - Sin modificar el `apt-enviar` actual. Este módulo provee la
    capacidad; integración en CLI es opt-in (paso D).
  - Snapshot DOM es un dict serializable, no usa Playwright en runtime
    de lectura (caller lo extrae con su propia Page).
  - PDF y screenshot se sirven via endpoints estáticos (paso B).

Plan: PLAN_MEJORAS Sprint 5 / N-02.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("catastro.pre_envio_snapshot")


# Campos críticos del seed que SIEMPRE deben matchear con el portal.
# Lista derivada del docs/BOT_PLAYBOOK.md y las 10 reglas de oro:
#   - tamanno (R4: tamaño físico)
#   - tipo_uso, tipo_zona
#   - area_real, area_registro
#   - fincas + titulares
#   - entero (numero + total_reg + total_cfia + pagado + cit)
#   - descripcion
CAMPOS_CRITICOS_PLANO = (
    "descripcion",
    "tamanno",
    "tipo_uso",
    "tipo_zona",
    "area_real",
    "area_registro",
)

CAMPOS_CRITICOS_CONTRATO = (
    "area_real",
    "area_predio",
    "max_planos",
    "honorarios",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _open(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _revisiones_dir(root: Path) -> Path:
    d = root / "data" / "revisiones"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── Diff: seed vs portal ───────────────────────────────────────────────

def comparar_campos_criticos(
    seed: dict,
    snapshot_dom: dict,
    *,
    campos: tuple[str, ...] = CAMPOS_CRITICOS_PLANO,
    tolerancia_numerica: float = 0.01,
) -> list[dict]:
    """Compara los campos críticos del seed con los leídos del portal.

    Devuelve lista de discrepancias. Cada item:
        {"campo": str, "seed": Any, "portal": Any, "ok": bool, "tipo": "missing"|"mismatch"}

    Para campos numéricos, usa tolerancia (default 1%). Para strings,
    matching exacto (case-insensitive, sin espacios extra).
    """
    diffs: list[dict] = []
    for campo in campos:
        seed_val = seed.get(campo)
        portal_val = snapshot_dom.get(campo)

        if seed_val is None and portal_val is None:
            continue

        if portal_val is None and seed_val is not None:
            diffs.append({
                "campo": campo, "seed": seed_val, "portal": None,
                "ok": False, "tipo": "missing_in_portal",
            })
            continue

        ok = _campos_iguales(seed_val, portal_val, tolerancia_numerica)
        if not ok:
            diffs.append({
                "campo": campo, "seed": seed_val, "portal": portal_val,
                "ok": False, "tipo": "mismatch",
            })

    return diffs


def _campos_iguales(a: Any, b: Any, tol: float) -> bool:
    """Compara dos valores con tolerancia numérica para floats."""
    if a is None or b is None:
        return a == b
    # Numérico
    try:
        fa, fb = float(a), float(b)
        if fa == 0 and fb == 0:
            return True
        denom = max(abs(fa), abs(fb))
        return abs(fa - fb) / denom <= tol
    except (TypeError, ValueError):
        pass
    # String
    sa = str(a).strip().lower()
    sb = str(b).strip().lower()
    return sa == sb


# ── Captura de screenshot ──────────────────────────────────────────────

def capturar_screenshot(page: Any, output_path: Path) -> bool:
    """Toma screenshot full-page del portal CFIA.

    Retorna True si OK. False con log si Playwright/page falla.
    `page` es duck-typed: cualquier objeto con .screenshot(path=, full_page=).
    """
    try:
        page.screenshot(path=str(output_path), full_page=True)
        return True
    except Exception:
        log.exception("capturar_screenshot falló (page=%r path=%s)",
                      type(page).__name__, output_path)
        return False


# ── Crear revisión ─────────────────────────────────────────────────────

def crear_revision(
    db_path: Path,
    root: Path,
    *,
    expediente_id: str,
    seed_plano: dict,
    snapshot_dom: dict,
    page: Optional[Any] = None,
    pdf_anverso_path: Optional[str] = None,
    plano_id_apt: Optional[str] = None,
    campos_criticos: tuple[str, ...] = CAMPOS_CRITICOS_PLANO,
) -> str:
    """Crea una revisión pendiente. Devuelve el rev_id (UUID).

    Args:
      db_path: BD donde persistir.
      root: raíz del proyecto (para resolver paths relativos).
      expediente_id: FK al expediente.
      seed_plano: dict con los valores que el bot iba a enviar.
      snapshot_dom: dict con los valores leídos del portal CFIA.
      page: opcional, objeto Playwright Page para tomar screenshot.
      pdf_anverso_path: opcional, path al PDF anverso (relativo o absoluto).
      plano_id_apt: opcional, id del plano APT si aplica.
      campos_criticos: campos a comparar (default CAMPOS_CRITICOS_PLANO).
    """
    rev_id = str(uuid.uuid4())

    # Screenshot (best-effort)
    screenshot_path_rel: Optional[str] = None
    if page is not None:
        out = _revisiones_dir(root) / f"{rev_id}.png"
        if capturar_screenshot(page, out):
            screenshot_path_rel = str(out.relative_to(root)).replace("\\", "/")

    # Diff
    diffs = comparar_campos_criticos(
        seed_plano, snapshot_dom, campos=campos_criticos,
    )

    # PDF path relativo si vino absoluto
    pdf_rel = None
    if pdf_anverso_path:
        try:
            p = Path(pdf_anverso_path)
            if p.is_absolute():
                pdf_rel = str(p.relative_to(root)).replace("\\", "/")
            else:
                pdf_rel = str(p).replace("\\", "/")
        except ValueError:
            # PDF fuera de root — guardar absoluto pero loggear warning
            log.warning("pdf_anverso_path fuera de ROOT: %s", pdf_anverso_path)
            pdf_rel = str(pdf_anverso_path).replace("\\", "/")

    with _open(db_path) as conn:
        conn.execute(
            """INSERT INTO revisiones_pre_envio
               (id, expediente_id, plano_id_apt, estado, creado_at,
                pdf_anverso_path, screenshot_path,
                snapshot_dom_json, seed_json, diff_json, n_discrepancias)
               VALUES (?, ?, ?, 'pendiente', ?, ?, ?, ?, ?, ?, ?)""",
            (rev_id, expediente_id, plano_id_apt, _now_iso(),
             pdf_rel, screenshot_path_rel,
             json.dumps(snapshot_dom, ensure_ascii=False, default=str),
             json.dumps(seed_plano, ensure_ascii=False, default=str),
             json.dumps(diffs, ensure_ascii=False, default=str),
             len(diffs)),
        )
        conn.commit()

    log.info(
        "[pre_envio] revisión creada %s (exp=%s, %d discrepancias)",
        rev_id, expediente_id, len(diffs),
    )

    # Publish SSE (best-effort)
    try:
        from src.utils.event_bus import publish
        publish({
            "type": "revision_pendiente",
            "id": rev_id,
            "expediente_id": expediente_id,
            "n_discrepancias": len(diffs),
        })
    except Exception:
        pass

    return rev_id


# ── Lectura ────────────────────────────────────────────────────────────

def listar_pendientes(db_path: Path) -> list[dict]:
    """Lista de revisiones en estado 'pendiente', orden por creado_at ASC."""
    with _open(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM revisiones_pre_envio "
            "WHERE estado = 'pendiente' ORDER BY creado_at ASC"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def read_revision(db_path: Path, rev_id: str) -> Optional[dict]:
    """Snapshot completo de una revisión. None si no existe."""
    with _open(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM revisiones_pre_envio WHERE id = ?", (rev_id,),
        ).fetchone()
    return _row_to_dict(row) if row else None


def listar_por_expediente(db_path: Path, expediente_id: str,
                          limit: int = 20) -> list[dict]:
    with _open(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM revisiones_pre_envio WHERE expediente_id = ? "
            "ORDER BY creado_at DESC LIMIT ?",
            (expediente_id, limit),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _row_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    # Deserializar campos JSON
    for k in ("snapshot_dom_json", "seed_json", "diff_json"):
        v = d.get(k)
        if v:
            try:
                d[k.replace("_json", "")] = json.loads(v)
            except Exception:
                d[k.replace("_json", "")] = None
    return d


# ── Mutaciones (resolución) ────────────────────────────────────────────

def aprobar(db_path: Path, rev_id: str, *,
            resuelto_por: str = "web_dashboard") -> dict:
    """Marca una revisión como aprobada. Idempotente: si ya está aprobada,
    no hace nada y devuelve el snapshot actual."""
    return _resolver(db_path, rev_id, "aprobado", resuelto_por, None)


def rechazar(db_path: Path, rev_id: str, *,
             razon: str,
             resuelto_por: str = "web_dashboard") -> dict:
    """Marca como rechazada con razón obligatoria."""
    if not razon or not razon.strip():
        raise ValueError("razon es obligatoria para rechazar")
    return _resolver(db_path, rev_id, "rechazado", resuelto_por, razon[:500])


def _resolver(db_path: Path, rev_id: str, nuevo_estado: str,
              actor: str, razon: Optional[str]) -> dict:
    now = _now_iso()
    with _open(db_path) as conn:
        row = conn.execute(
            "SELECT estado FROM revisiones_pre_envio WHERE id = ?", (rev_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"revisión {rev_id!r} no existe")
        if row["estado"] == nuevo_estado:
            log.info("[pre_envio] revisión %s ya estaba %s — no-op",
                     rev_id, nuevo_estado)
        elif row["estado"] != "pendiente":
            raise ValueError(
                f"revisión {rev_id!r} en estado {row['estado']!r} "
                f"no se puede transicionar a {nuevo_estado!r}"
            )
        else:
            conn.execute(
                """UPDATE revisiones_pre_envio
                      SET estado = ?, resuelto_at = ?, resuelto_por = ?,
                          razon_rechazo = ?
                    WHERE id = ?""",
                (nuevo_estado, now, actor, razon, rev_id),
            )
            conn.commit()

    # Publish SSE
    try:
        from src.utils.event_bus import publish
        publish({
            "type": "revision_resuelta",
            "id": rev_id,
            "estado": nuevo_estado,
            "actor": actor,
        })
    except Exception:
        pass

    log.info("[pre_envio] revisión %s → %s (por %s)", rev_id, nuevo_estado, actor)
    return read_revision(db_path, rev_id) or {}


__all__ = [
    "CAMPOS_CRITICOS_PLANO",
    "CAMPOS_CRITICOS_CONTRATO",
    "comparar_campos_criticos",
    "capturar_screenshot",
    "crear_revision",
    "listar_pendientes",
    "listar_por_expediente",
    "read_revision",
    "aprobar",
    "rechazar",
]
