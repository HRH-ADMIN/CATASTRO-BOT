"""Captura snapshot de la página APT cuando se detecta una anomalía.

Guarda en `data/anomaly_snapshots/<expediente>/<timestamp>/`:
  - `page.html`     — DOM completo (page.content())
  - `screenshot.png` — captura de pantalla full-page
  - `metadata.json`  — URL, título, viewport, user agent, cookies (no sensitive)
  - `anomalia.json`  — info de la anomalía: contexto, descripción, detalle
  - `console_errors.txt` — últimos errores JS si disponibles

Esto permite debugging post-mortem:
  - Reproducir el estado exacto cuando algo falló
  - Comparar contra una versión "buena" anterior
  - Detectar cambios de selectores en APT

USO:
    from src.utils.anomaly_snapshot import capturar_snapshot
    path = capturar_snapshot(
        page,
        expediente_numero="RDF-2026-004",
        contexto="bC5 PROYECTO",
        descripcion="canton vacío",
        detalle="...",
    )
    # path = Path("data/anomaly_snapshots/RDF-2026-004/2026-05-11T14_22_31/")
"""
from __future__ import annotations
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("catastro.anomaly_snapshot")


# Raíz donde se guardan todos los snapshots
SNAPSHOT_ROOT = Path("data/anomaly_snapshots")


def _safe_dir_name(s: str) -> str:
    """Convierte un string a algo seguro para nombre de carpeta."""
    if not s:
        return "no-exp"
    # Reemplazar caracteres prohibidos en Windows
    return re.sub(r"[^A-Za-z0-9._-]", "_", s)[:60]


def capturar_snapshot(
    page: Any,
    *,
    expediente_numero: str = "",
    contexto: str = "",
    descripcion: str = "",
    detalle: str = "",
    raiz: Path | None = None,
) -> Path | None:
    """Captura snapshot de la página APT actual.

    Args:
        page: Playwright Page (o mock para tests).
        expediente_numero: ej. "RDF-2026-004" (subdir).
        contexto: ej. "bC5 PROYECTO".
        descripcion: línea corta de qué pasó.
        detalle: info técnica adicional (puede ser largo).
        raiz: override de la carpeta raíz (default `data/anomaly_snapshots/`).

    Returns:
        Path a la carpeta del snapshot, o None si todo falló.

    NO lanza excepciones — el snapshot es best-effort. Si falla, devuelve None
    y loggea el error pero NO interrumpe el flujo de manejo de anomalías.
    """
    ts = datetime.now().strftime("%Y-%m-%dT%H_%M_%S")
    root = raiz or SNAPSHOT_ROOT
    folder_name = _safe_dir_name(expediente_numero) or "no-exp"
    target = Path(root) / folder_name / ts
    try:
        target.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        log.warning("no se pudo crear carpeta snapshot %s: %s", target, exc)
        return None

    # 1. HTML
    try:
        html = page.content() if hasattr(page, "content") else ""
        if html:
            (target / "page.html").write_text(html, encoding="utf-8")
    except Exception as exc:
        log.debug("snapshot HTML falló: %s", exc)

    # 2. Screenshot
    try:
        if hasattr(page, "screenshot"):
            page.screenshot(path=str(target / "screenshot.png"), full_page=True)
    except Exception as exc:
        log.debug("snapshot PNG falló: %s", exc)
        # Intentar viewport-only si full_page falla
        try:
            page.screenshot(path=str(target / "screenshot.png"))
        except Exception:
            pass

    # 3. Metadata del navegador
    try:
        meta = {}
        if hasattr(page, "url"):
            meta["url"] = page.url
        if hasattr(page, "title"):
            try:
                meta["title"] = page.title()
            except Exception:
                pass
        if hasattr(page, "viewport_size"):
            try:
                meta["viewport"] = page.viewport_size
            except Exception:
                pass
        meta["timestamp"] = ts
        meta["expediente"] = expediente_numero
        (target / "metadata.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    except Exception as exc:
        log.debug("snapshot metadata falló: %s", exc)

    # 4. Info de la anomalía
    try:
        anom = {
            "contexto":    contexto,
            "descripcion": descripcion,
            "detalle":     detalle,
            "ts":          ts,
        }
        (target / "anomalia.json").write_text(
            json.dumps(anom, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass

    # 5. Modal swal2 actual (si hay)
    try:
        if hasattr(page, "evaluate"):
            modal = page.evaluate(
                """() => {
                    const p = document.querySelector('.swal2-popup');
                    if (!p || p.offsetParent === null) return null;
                    return {
                        title: (document.querySelector('.swal2-title')?.innerText || '').trim(),
                        html:  (document.querySelector('.swal2-html-container')?.innerText || '').trim(),
                        icon:  (p.querySelector('.swal2-icon')?.className || '').trim(),
                    };
                }"""
            )
            if modal and isinstance(modal, dict):
                (target / "modal_actual.json").write_text(
                    json.dumps(modal, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
    except Exception:
        pass

    log.info("snapshot guardado en %s", target)
    return target


def listar_snapshots_de_expediente(expediente_numero: str) -> list[Path]:
    """Devuelve lista de carpetas de snapshot ordenadas por timestamp (más reciente primero)."""
    if not expediente_numero:
        return []
    folder = SNAPSHOT_ROOT / _safe_dir_name(expediente_numero)
    if not folder.exists():
        return []
    return sorted((p for p in folder.iterdir() if p.is_dir()), reverse=True)


__all__ = ["capturar_snapshot", "listar_snapshots_de_expediente", "SNAPSHOT_ROOT"]
