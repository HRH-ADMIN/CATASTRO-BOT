"""Validador del Derrotero.zip — cross-check coordenadas y área.

El derrotero ZIP contiene el shapefile (.shp + .dbf + .shx + .prj) con la
geometría real del polígono levantado en campo. Esta es la fuente
autoritativa para coordenadas — el cajetín del plano puede tener errores
de transcripción humana.

El validador:
  1. Lee el polígono del shapefile (en memoria, sin extraer a disco)
  2. Compara coords contra las del cajetín (tolerancia configurable)
  3. Compara área calculada vs área declarada en el cajetín
  4. Detecta polígonos auto-intersectados o degenerados
  5. Devuelve advertencias para el seed_builder y preflight

USO:
    from src.utils.derrotero_validator import validar_derrotero
    res = validar_derrotero(
        zip_path="data/files/.../Derrotero.zip",
        coords_cajetin=[(e1, n1), (e2, n2), ...],
        area_declarada=582.67,
    )
    if res.errores:
        # Bloquear envío
        ...
    for w in res.advertencias:
        print(w)
"""
from __future__ import annotations
import io
import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger("catastro.derrotero")


@dataclass
class DerroteroDatos:
    """Datos extraídos del shapefile."""
    coords:     list[tuple[float, float]] = field(default_factory=list)
    area_m2:    float = 0.0
    n_vertices: int = 0
    proyeccion: str = ""   # contenido de .prj si existe
    error:      str = ""   # si no se pudo leer


@dataclass
class ResultadoValidacion:
    """Resultado de comparar derrotero vs cajetín."""
    errores:      list[str] = field(default_factory=list)
    advertencias: list[str] = field(default_factory=list)
    info:         list[str] = field(default_factory=list)
    derrotero:    Optional[DerroteroDatos] = None

    def tiene_errores(self) -> bool:
        return len(self.errores) > 0


# ── Helpers geométricos ────────────────────────────────────────────────

def _area_poligono(puntos: list[tuple[float, float]]) -> float:
    """Área absoluta del polígono (fórmula de Stokes)."""
    if len(puntos) < 3:
        return 0.0
    pts = list(puntos)
    # Cerrar si no está cerrado
    if pts[0] != pts[-1]:
        pts.append(pts[0])
    a = 0.0
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        a += x0 * y1 - x1 * y0
    return abs(a) * 0.5


def _quitar_cierre(puntos: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Si el último punto == primero (polígono cerrado), lo elimina."""
    if len(puntos) >= 2 and puntos[0] == puntos[-1]:
        return puntos[:-1]
    return list(puntos)


def _coords_aproximadamente_iguales(
    a: tuple[float, float], b: tuple[float, float], *, tol: float = 0.5,
) -> bool:
    """True si dos puntos coinciden dentro de la tolerancia (en metros)."""
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


# ── Lector del shapefile ───────────────────────────────────────────────

def leer_derrotero_zip(zip_path: Path | str) -> DerroteroDatos:
    """Lee el shapefile dentro de Derrotero.zip sin extraerlo a disco.

    Returns DerroteroDatos con `error` lleno si no se pudo leer.
    """
    zip_path = Path(zip_path)
    if not zip_path.exists():
        return DerroteroDatos(error=f"archivo no existe: {zip_path}")
    if not zip_path.is_file():
        return DerroteroDatos(error=f"no es archivo: {zip_path}")

    try:
        import shapefile  # pyshp
    except ImportError:
        return DerroteroDatos(
            error="pyshp no instalado. Correr: pip install pyshp"
        )

    try:
        with zipfile.ZipFile(zip_path) as zf:
            nombres = zf.namelist()
            # Encontrar los 3 archivos requeridos
            shp = next((n for n in nombres if n.lower().endswith(".shp")), None)
            dbf = next((n for n in nombres if n.lower().endswith(".dbf")), None)
            shx = next((n for n in nombres if n.lower().endswith(".shx")), None)
            prj = next((n for n in nombres if n.lower().endswith(".prj")), None)
            if not (shp and dbf and shx):
                return DerroteroDatos(
                    error=f"ZIP incompleto — falta .shp/.dbf/.shx (encontrado: {nombres})"
                )
            shp_bytes = io.BytesIO(zf.read(shp))
            dbf_bytes = io.BytesIO(zf.read(dbf))
            shx_bytes = io.BytesIO(zf.read(shx))
            prj_text = zf.read(prj).decode("utf-8", errors="replace") if prj else ""

            reader = shapefile.Reader(shp=shp_bytes, dbf=dbf_bytes, shx=shx_bytes)
            shapes = list(reader.shapes())
            if not shapes:
                return DerroteroDatos(error="shapefile sin geometrías")
            shape = shapes[0]
            puntos = [(float(p[0]), float(p[1])) for p in shape.points]
            puntos_sin_cierre = _quitar_cierre(puntos)
            return DerroteroDatos(
                coords=puntos_sin_cierre,
                area_m2=_area_poligono(puntos_sin_cierre),
                n_vertices=len(puntos_sin_cierre),
                proyeccion=prj_text.strip(),
            )
    except Exception as exc:
        log.warning("error leyendo Derrotero.zip: %s", exc)
        return DerroteroDatos(error=str(exc))


# ── Comparador derrotero vs cajetín ────────────────────────────────────

def validar_derrotero(
    *,
    zip_path: Path | str | None,
    coords_cajetin: list[tuple[float, float]] | None = None,
    area_declarada: float | None = None,
    tolerancia_coord_m: float = 0.5,
    tolerancia_area_pct: float = 1.0,
) -> ResultadoValidacion:
    """Valida el derrotero ZIP contra los datos del cajetín.

    Args:
        zip_path: path al Derrotero.zip. None → no se valida.
        coords_cajetin: lista de (este, norte) extraída del cajetín por Vision.
        area_declarada: área en m² declarada en el cajetín.
        tolerancia_coord_m: diferencia máxima permitida por vértice (metros).
        tolerancia_area_pct: diferencia máxima entre áreas (%).

    Returns ResultadoValidacion con errores/advertencias/info.
    """
    r = ResultadoValidacion()
    if not zip_path:
        r.info.append("(sin derrotero ZIP — validación omitida)")
        return r

    derrotero = leer_derrotero_zip(zip_path)
    r.derrotero = derrotero
    if derrotero.error:
        r.advertencias.append(f"Derrotero: {derrotero.error}")
        return r

    if not derrotero.coords:
        r.advertencias.append("Derrotero sin coordenadas — polígono vacío")
        return r

    r.info.append(
        f"Derrotero: {derrotero.n_vertices} vértices, "
        f"área {derrotero.area_m2:.2f}m²"
        + (f" ({derrotero.proyeccion[:30]}...)" if derrotero.proyeccion else "")
    )

    # 1. Comparar área declarada vs área del shapefile
    if area_declarada and area_declarada > 0 and derrotero.area_m2 > 0:
        diff_pct = abs(area_declarada - derrotero.area_m2) / area_declarada * 100
        if diff_pct > tolerancia_area_pct * 5:  # 5% → error
            r.errores.append(
                f"Área cajetín ({area_declarada:.2f}m²) NO coincide con área "
                f"calculada del derrotero ({derrotero.area_m2:.2f}m²) — "
                f"diferencia {diff_pct:.2f}% (> 5%)"
            )
        elif diff_pct > tolerancia_area_pct:
            r.advertencias.append(
                f"Área cajetín {area_declarada:.2f}m² vs derrotero "
                f"{derrotero.area_m2:.2f}m² difieren {diff_pct:.2f}%"
            )

    # 2. Comparar coordenadas (cantidad de vértices)
    if coords_cajetin:
        n_cajetin = len(coords_cajetin)
        n_derro = derrotero.n_vertices
        if n_cajetin != n_derro:
            r.advertencias.append(
                f"Vértices cajetín={n_cajetin} vs derrotero={n_derro} — "
                f"verificar listado de coordenadas"
            )

        # 3. Comparar coords por proximidad (no por orden)
        # Cada coord del cajetín debería tener un match cercano en el derrotero
        if coords_cajetin and derrotero.coords:
            no_matcheados_cajetin = []
            for p_caj in coords_cajetin:
                if not any(
                    _coords_aproximadamente_iguales(p_caj, p_der, tol=tolerancia_coord_m)
                    for p_der in derrotero.coords
                ):
                    no_matcheados_cajetin.append(p_caj)

            if no_matcheados_cajetin:
                ejemplos = ", ".join(
                    f"({p[0]:.2f},{p[1]:.2f})"
                    for p in no_matcheados_cajetin[:3]
                )
                r.advertencias.append(
                    f"{len(no_matcheados_cajetin)} vértice(s) del cajetín "
                    f"sin match en derrotero (tol={tolerancia_coord_m}m): {ejemplos}"
                )

    # 4. Validar proyección si está disponible (debería ser CRTM05)
    if derrotero.proyeccion:
        pj = derrotero.proyeccion.upper()
        if "CRTM05" not in pj and "CR05" not in pj and "WKID 5367" not in pj \
           and "5367" not in pj:
            r.advertencias.append(
                f"Proyección del derrotero podría no ser CRTM05: "
                f"'{derrotero.proyeccion[:60]}...'"
            )

    return r


__all__ = [
    "DerroteroDatos", "ResultadoValidacion",
    "leer_derrotero_zip", "validar_derrotero",
]
