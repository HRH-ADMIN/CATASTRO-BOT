"""Loader de configuración de municipalidades desde YAML.

Permite agregar muni nueva con UN archivo de configuración, sin modificar
código. Centraliza lo que antes estaba hardcoded en `municipality_agent.py`.

USO:
    from src.utils.muni_config import cargar_muni, listar_munis

    config = cargar_muni("san_ramon")
    config["email_correcciones"]  # "mgamboa@sanramon.go.cr"
    config["google_form"]["form_id"]
    config["distritos"]["05 Piedades Sur"]  # "05"

    # Para multi-muni
    for slug in listar_munis():
        print(slug, cargar_muni(slug)["nombre_legal"])

    # Formatear campos según reglas de la muni
    from src.utils.muni_config import formatear_finca
    formatear_finca([
        {"provincia": "2", "numero": "642038", "derecho": "000"},
    ], muni="san_ramon")
    # → "2 642038-000"
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("catastro.muni_config")

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "munis.yaml"


@lru_cache(maxsize=1)
def _load_all(path: Optional[Path] = None) -> dict:
    """Lee y cachea el YAML completo. Returns dict canton_slug → config."""
    p = path or _DEFAULT_CONFIG_PATH
    if not p.exists():
        log.warning("munis.yaml no encontrado en %s — devuelvo vacío", p)
        return {}
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        log.warning("pyyaml no instalado — muni_config no funciona "
                    "(pip install pyyaml)")
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        log.exception("error parseando munis.yaml: %s", exc)
        return {}


def listar_munis(*, config_path: Optional[Path] = None) -> list[str]:
    """Devuelve slugs de munis configuradas (ej. ['san_ramon', 'poas'])."""
    return list(_load_all(config_path).keys())


def cargar_muni(
    slug: str, *, config_path: Optional[Path] = None,
) -> Optional[dict]:
    """Devuelve la config de una muni o None si no existe."""
    return _load_all(config_path).get(slug)


def muni_para_canton(
    canton: str, *, config_path: Optional[Path] = None,
) -> Optional[str]:
    """Resuelve canton (texto libre) → slug de muni.

    Ej: 'SAN RAMÓN' / 'san_ramon' / 'SAN_RAMON' → 'san_ramon'.
    """
    if not canton:
        return None
    norm = canton.lower().replace(" ", "_").strip()
    # Quitar acentos manualmente para los casos comunes
    norm = (norm
            .replace("á", "a").replace("é", "e").replace("í", "i")
            .replace("ó", "o").replace("ú", "u").replace("ñ", "n"))
    if norm in _load_all(config_path):
        return norm
    # Fallback: buscar match parcial en config
    for slug, cfg in _load_all(config_path).items():
        canton_cfg = (cfg.get("canton") or "").lower().replace(" ", "_")
        if canton_cfg == norm:
            return slug
    return None


# ── Helpers de formato (R-M3 de TILMAN) ─────────────────────────────────

def formatear_finca(
    fincas: list[dict], *,
    muni: str = "san_ramon",
    config_path: Optional[Path] = None,
) -> str:
    """Devuelve string de fincas según el formato de la muni.

    San Ramón: "<prov> <numero>-<derecho>"  (varias separadas por "/")
    """
    if not fincas:
        return ""
    cfg = cargar_muni(muni, config_path=config_path) or {}
    fmt_cfg = (cfg.get("formato_campos") or {}).get("finca") or {}
    patron = fmt_cfg.get("patron", "{provincia} {numero}-{derecho}")
    sep    = fmt_cfg.get("separador_varias", "/")
    out = []
    for f in fincas:
        if not f.get("numero"):
            continue
        out.append(patron.format(
            provincia=f.get("provincia", "2"),
            numero=f["numero"],
            derecho=f.get("derecho", "000"),
        ))
    return sep.join(out)


def formatear_carne_topografo(
    carne_raw: str, *,
    muni: str = "san_ramon",
    config_path: Optional[Path] = None,
) -> str:
    """Devuelve carné según formato muni.

    San Ramón: "it-NNNNN" (minúsculas, guion). Acepta input "IT 10676",
    "I.T. 10676", "10676" — todos producen "it-10676".
    """
    if not carne_raw:
        return ""
    import re as _re
    cfg = cargar_muni(muni, config_path=config_path) or {}
    fmt_cfg = (cfg.get("formato_campos") or {}).get("carne") or {}
    patron = fmt_cfg.get("patron", "it-{numero}")
    transformar = fmt_cfg.get("transformar", "")
    digits = _re.sub(r"[^\d]", "", str(carne_raw))
    if not digits:
        result = str(carne_raw).strip()
    else:
        result = patron.format(numero=digits)
    if transformar == "lowercase":
        result = result.lower()
    elif transformar == "uppercase":
        result = result.upper()
    return result


def formatear_nombre_profesional(
    nombre_cfia: str, *,
    muni: str = "san_ramon",
    config_path: Optional[Path] = None,
) -> str:
    """Convierte 'ROJAS HERRERA LUIS ALONSO' → 'luis alonso rojas herrera'.

    Formato muni (SR): minúsculas, nombres primero.
    """
    if not nombre_cfia:
        return ""
    cfg = cargar_muni(muni, config_path=config_path) or {}
    fmt_cfg = (cfg.get("formato_campos") or {}).get("nombre_profesional") or {}
    orden = fmt_cfg.get("orden", "")
    caso = fmt_cfg.get("caso", "")
    partes = nombre_cfia.strip().split()
    if orden == "nombres_primero" and len(partes) >= 3:
        if len(partes) >= 4:
            ap1, ap2, *nombres = partes
            result = " ".join(nombres + [ap1, ap2])
        else:
            ap1, ap2, nom = partes
            result = f"{nom} {ap1} {ap2}"
    else:
        result = nombre_cfia
    if caso == "lowercase":
        result = result.lower()
    elif caso == "uppercase":
        result = result.upper()
    return result


def asunto_correcciones(
    tipo: str, tramite: str, *,
    muni: str = "san_ramon",
    config_path: Optional[Path] = None,
) -> str:
    """Devuelve asunto formateado para email de correcciones/resello.

    Args:
        tipo: 'corregido' | 'resello'
        tramite: número del trámite APT (ej '1223951')
    """
    cfg = cargar_muni(muni, config_path=config_path) or {}
    key = f"asunto_{tipo.lower()}"
    plantilla = cfg.get(key)
    if not plantilla:
        # Fallback genérico
        plantilla = f"Trámite {tipo.upper()} APT - {{tramite}}"
    return plantilla.format(tramite=tramite)


def reset_cache() -> None:
    """Limpia el cache (útil para tests o tras modificar el YAML)."""
    _load_all.cache_clear()


__all__ = [
    "cargar_muni", "listar_munis", "muni_para_canton",
    "formatear_finca", "formatear_carne_topografo",
    "formatear_nombre_profesional", "asunto_correcciones",
    "reset_cache",
]
