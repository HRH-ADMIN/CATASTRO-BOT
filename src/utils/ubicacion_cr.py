"""Mapeo nombre → código APT para provincia / cantón / distrito de Costa Rica.

Códigos verificados contra el dropdown real de APT (mayo 2026).

Sólo incluye los lugares que el bot ha encontrado en planos reales — extender
cuando aparezcan nuevos. Para los desconocidos, devuelve "" y el seed builder
debe pedirle al operador que confirme.

USO:
    from src.utils.ubicacion_cr import codigo_provincia, codigo_canton, codigo_distrito
    codigo_provincia("ALAJUELA")           # "2"
    codigo_canton("ALAJUELA", "SAN RAMÓN") # "02"
    codigo_distrito("ALAJUELA", "SAN RAMON", "ALFARO")  # "09"
"""
from __future__ import annotations
import unicodedata


# ── Provincias (siempre las 7) ──────────────────────────────────────────
_PROVINCIAS = {
    "SAN JOSE":     "1",
    "ALAJUELA":     "2",
    "CARTAGO":      "3",
    "HEREDIA":      "4",
    "GUANACASTE":   "5",
    "PUNTARENAS":   "6",
    "LIMON":        "7",
}


# ── Cantones (zero-padded a 2 dígitos por APT) ─────────────────────────
# Solo los visitados — extender cuando aparezcan.
_CANTONES: dict[str, dict[str, str]] = {
    "2": {  # ALAJUELA
        "ALAJUELA":    "01",
        "SAN RAMON":   "02",
        "GRECIA":      "03",
        "SAN MATEO":   "04",
        "ATENAS":      "05",
        "NARANJO":     "06",
        "PALMARES":    "07",
        "POAS":        "08",
        "OROTINA":     "09",
        "SAN CARLOS":  "10",
        "ZARCERO":     "11",
        "VALVERDE VEGA": "12",
        "UPALA":       "13",
        "LOS CHILES":  "14",
        "GUATUSO":     "15",
        "RIO CUARTO":  "16",
    },
}


# ── Distritos por (provincia, cantón) ──────────────────────────────────
# Solo los visitados.
_DISTRITOS: dict[tuple[str, str], dict[str, str]] = {
    ("2", "02"): {  # ALAJUELA / SAN RAMON
        "SAN RAMON":      "01",
        "SANTIAGO":       "02",
        "SAN JUAN":       "03",
        "PIEDADES NORTE": "04",
        "PIEDADES SUR":   "05",
        "SAN RAFAEL":     "06",
        "SAN ISIDRO":     "07",
        "ANGELES":        "08",
        "ALFARO":         "09",
        "VOLIO":          "10",
        "CONCEPCION":     "11",
        "ZAPOTAL":        "12",
        "PEÑAS BLANCAS":  "13",
        "SAN LORENZO":    "14",
    },
    ("2", "16"): {  # ALAJUELA / RIO CUARTO
        "RIO CUARTO":     "01",
        "SANTA RITA":     "02",
        "SANTA ISABEL":   "03",
    },
    ("2", "08"): {  # ALAJUELA / POAS
        "SAN PEDRO":       "01",
        "SAN JUAN":        "02",
        "SAN RAFAEL":      "03",
        "CARRILLOS":       "04",
        "SABANA REDONDA":  "05",
    },
}


def _normalizar(s: str) -> str:
    """Quita acentos, mayúsculas, espacios colapsados, sin signos."""
    if not s:
        return ""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.upper().strip()
    s = " ".join(s.split())
    # Aceptar "_" como separador (ej. "SAN_RAMON")
    s = s.replace("_", " ")
    return s


def codigo_provincia(nombre: str) -> str:
    """Nombre de provincia → código numérico ("1"-"7")."""
    return _PROVINCIAS.get(_normalizar(nombre), "")


def codigo_canton(provincia: str, canton: str) -> str:
    """Nombre de cantón → código zero-padded ("01"-"15")."""
    cod_prov = codigo_provincia(provincia) or str(provincia)
    tabla = _CANTONES.get(cod_prov, {})
    return tabla.get(_normalizar(canton), "")


def codigo_distrito(provincia: str, canton: str, distrito: str) -> str:
    """Nombre de distrito → código zero-padded ("01"-...)."""
    cod_prov = codigo_provincia(provincia) or str(provincia)
    cod_cant = codigo_canton(cod_prov, canton) or str(canton).zfill(2)
    tabla = _DISTRITOS.get((cod_prov, cod_cant), {})
    return tabla.get(_normalizar(distrito), "")


def resolver_ubicacion(provincia: str, canton: str, distrito: str) -> dict:
    """Devuelve dict con códigos + nombres normalizados + flags de cobertura.

    Si un nivel no se reconoce, su código queda "" y `incompleto=True`.
    """
    cod_prov = codigo_provincia(provincia)
    cod_cant = codigo_canton(provincia, canton)
    cod_dist = codigo_distrito(provincia, canton, distrito)
    return {
        "provincia_nombre": _normalizar(provincia),
        "canton_nombre":    _normalizar(canton),
        "distrito_nombre":  _normalizar(distrito),
        "provincia":        cod_prov,
        "canton":           cod_cant,
        "distrito":         cod_dist,
        "incompleto":       not (cod_prov and cod_cant and cod_dist),
    }


__all__ = [
    "codigo_provincia", "codigo_canton", "codigo_distrito",
    "resolver_ubicacion",
]
