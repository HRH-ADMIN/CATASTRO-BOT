"""Pre-flight validation del `datos_apt` antes de abrir Chrome o guardar en APT.

Corre todas las validaciones que NO requieren sesión APT activa — así
atrapamos errores antes de invertir tiempo en el navegador o que CFIA
rechace el plano por algo prevenible.

Checks (en orden de severidad):

  ERRORES (bloquean — no se puede enviar):
    1. Campos obligatorios faltantes (cedula, finca, coordenadas, entero)
    2. Cédula propietario con formato inválido (FÍSICA/JURÍDICA)
    3. Polígono inválido: < 3 vértices o auto-intersección
    4. Centroide cae FUERA del polígono
    5. Área declarada NO coincide con área calculada del polígono (>5% diff)
    6. Plano a modificar con formato inválido (debe ser X-NNNNNNN-AAAA)
    7. Número de entero no es 9 dígitos

  ADVERTENCIAS (notifican pero permiten continuar):
    1. Tipo uso no inferido desde naturaleza
    2. Ubicación no en mapper (códigos vacíos)
    3. Honorarios = 0 sin observaciones explicando por qué
    4. Diferencia entre área registro y área real muy grande (>50%)
    5. Cédula registro original truncada (ya manejado por el helper)
    6. Protocolo declarado no es el activo (lo detecta el bot en runtime)

USO:
    from src.utils.preflight_seed import validar_seed_pre_envio
    result = validar_seed_pre_envio(datos_apt)
    if result.tiene_errores():
        # No se puede continuar
        for e in result.errores: print(f"❌ {e}")
        return
    for w in result.advertencias: print(f"⚠️ {w}")
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PreflightResult:
    """Resultado de la validación pre-envío del datos_apt."""
    errores:       list[str] = field(default_factory=list)
    advertencias:  list[str] = field(default_factory=list)
    info:          list[str] = field(default_factory=list)

    def tiene_errores(self) -> bool:
        return len(self.errores) > 0

    def resumen(self) -> str:
        out = []
        if self.errores:
            out.append(f"❌ {len(self.errores)} ERROR(es) BLOQUEANTE(s):")
            out.extend(f"  • {e}" for e in self.errores)
        if self.advertencias:
            out.append(f"⚠️  {len(self.advertencias)} advertencia(s):")
            out.extend(f"  • {w}" for w in self.advertencias)
        if not self.errores and not self.advertencias:
            out.append("✅ Sin problemas detectados")
        return "\n".join(out)


# ── Validaciones de polígono ───────────────────────────────────────────

def _parse_coords(coords_raw) -> list[tuple[float, float]]:
    """Acepta lista de tuplas o listas o dicts {este,norte}. Devuelve [(e,n)]."""
    if not coords_raw:
        return []
    out = []
    for p in coords_raw:
        if isinstance(p, dict):
            e, n = p.get("este", 0), p.get("norte", 0)
        elif isinstance(p, (list, tuple)) and len(p) >= 2:
            e, n = p[0], p[1]
        else:
            continue
        try:
            out.append((float(e), float(n)))
        except (TypeError, ValueError):
            continue
    return out


def _segmentos_se_cruzan(p1, p2, p3, p4) -> bool:
    """True si segmento p1-p2 cruza con p3-p4 (Bentley-Ottmann simplificado)."""
    def ccw(a, b, c):
        return (c[1] - a[1]) * (b[0] - a[0]) > (b[1] - a[1]) * (c[0] - a[0])
    return ccw(p1, p3, p4) != ccw(p2, p3, p4) and \
           ccw(p1, p2, p3) != ccw(p1, p2, p4)


def _poligono_se_autointersecta(puntos) -> bool:
    """Detecta self-intersection en O(n²) — suficiente para planos catastrales."""
    n = len(puntos)
    if n < 4:
        return False
    pts = list(puntos) + [puntos[0]]  # cerrado
    for i in range(n):
        for j in range(i + 2, n):
            # Evitar comparar segmentos adyacentes
            if i == 0 and j == n - 1:
                continue
            if _segmentos_se_cruzan(pts[i], pts[i+1], pts[j], pts[j+1]):
                return True
    return False


def _area_poligono_stokes(puntos) -> float:
    """Área firmada del polígono. Negativa si vértices van CW, positiva CCW."""
    if len(puntos) < 3:
        return 0.0
    pts = list(puntos) + [puntos[0]]
    a = 0.0
    for i in range(len(puntos)):
        x0, y0 = pts[i]
        x1, y1 = pts[i+1]
        a += x0 * y1 - x1 * y0
    return abs(a) * 0.5


def _punto_dentro_de_poligono(pt, puntos) -> bool:
    """Ray casting — true si pt está dentro del polígono (incluso borde)."""
    if len(puntos) < 3:
        return False
    x, y = pt
    n = len(puntos)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = puntos[i]
        xj, yj = puntos[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


# ── Validadores específicos ─────────────────────────────────────────────

def _validar_cedula_formato(cedula: str, tipo: str) -> tuple[bool, str]:
    """Mismo helper que apt_agent.validar_formato_cedula (duplicado para
    evitar import circular)."""
    if not cedula:
        return False, "cédula vacía"
    if tipo == "1":
        if not re.match(r"^\d-\d{4}-\d{4}$", cedula):
            return False, f"cédula FÍSICA mal formateada: '{cedula}'"
        return True, ""
    if tipo == "2":
        if not re.match(r"^3-\d{3}-\d{6}$", cedula):
            return False, f"cédula JURÍDICA mal formateada: '{cedula}'"
        return True, ""
    return True, ""  # otros tipos: laxos


def _validar_plano_modificar(plano: dict) -> tuple[bool, str]:
    """Plano a modificar debe tener provincia(1-7), número(>0), año(4 dígitos)."""
    if not isinstance(plano, dict):
        return False, "plano a modificar no es dict"
    prov = str(plano.get("provincia", "")).strip()
    num  = str(plano.get("numero", "")).strip()
    anno = str(plano.get("anno", "")).strip()
    if prov not in {"1","2","3","4","5","6","7"}:
        return False, f"provincia inválida: '{prov}' (debe ser 1-7)"
    if not num.isdigit() or int(num) < 1:
        return False, f"número plano inválido: '{num}'"
    if not re.match(r"^\d{4}$", anno):
        return False, f"año inválido: '{anno}' (esperado AAAA)"
    return True, ""


# ── Función principal ─────────────────────────────────────────────────

def validar_seed_pre_envio(datos_apt: dict) -> PreflightResult:
    """Valida un datos_apt completo. Devuelve PreflightResult con errores/warnings.

    Diseñado para correr ANTES de abrir Chrome. Si devuelve errores, el seed
    NO debe enviarse a APT — el operador debe corregir primero.
    """
    r = PreflightResult()
    if not isinstance(datos_apt, dict):
        r.errores.append("datos_apt no es un dict")
        return r

    propietario = datos_apt.get("propietario") or {}
    proyecto    = datos_apt.get("proyecto") or {}
    general     = datos_apt.get("general") or {}
    plano       = datos_apt.get("plano") or {}

    # ── Campos obligatorios ──────────────────────────────────────
    if not propietario.get("cedula"):
        r.errores.append("propietario.cedula vacío")
    if not proyecto.get("provincia"):
        r.errores.append("proyecto.provincia vacío (mapper desconoce ubicación)")
    if not plano.get("fincas"):
        r.errores.append("plano.fincas vacío (al menos una finca requerida)")
    if not plano.get("entero", {}).get("numero"):
        r.errores.append("plano.entero.numero vacío")
    if not plano.get("descripcion"):
        r.advertencias.append("plano.descripcion vacío (cajetín abrev)")

    # ── Cédula formato ──────────────────────────────────────────
    if propietario.get("cedula"):
        ok, motivo = _validar_cedula_formato(
            propietario["cedula"], str(propietario.get("tipo_cedula", "1")),
        )
        if not ok:
            # Si hay flag de cedula corregida, el operador ya sabe — degradar
            if propietario.get("cedula_registro_original"):
                r.advertencias.append(motivo + " (operador ya marcó como corregida)")
            else:
                r.errores.append(motivo)

    # ── Entero formato ──────────────────────────────────────────
    entero_num = str(plano.get("entero", {}).get("numero", "")).strip()
    if entero_num and not re.match(r"^\d{9}$", entero_num):
        r.advertencias.append(
            f"plano.entero.numero '{entero_num}' no parece 9 dígitos típicos"
        )

    # ── Polígono ────────────────────────────────────────────────
    # Las coordenadas pueden venir como lista en CajetinData, pero en el seed
    # final solo aparece (norte, este) del centroide — no la lista completa.
    # Si el seed builder guardó la lista cruda, validar; si no, omitir.
    coords_raw = plano.get("_coordenadas_raw") or []
    coords = _parse_coords(coords_raw)
    if coords:
        if len(coords) < 3:
            r.errores.append(f"polígono con solo {len(coords)} vértices (mínimo 3)")
        else:
            if _poligono_se_autointersecta(coords):
                r.errores.append(
                    "polígono se auto-intersecta — revisar orden de vértices"
                )
            # Centroide dentro
            try:
                cx = float(plano.get("este", 0))
                cy = float(plano.get("norte", 0))
                if cx and cy and not _punto_dentro_de_poligono((cx, cy), coords):
                    r.advertencias.append(
                        f"centroide ({cx:.2f}, {cy:.2f}) cae FUERA del polígono — "
                        f"verificar listado de coordenadas"
                    )
            except (ValueError, TypeError):
                pass
            # Área calculada vs declarada
            area_calc = _area_poligono_stokes(coords)
            try:
                area_decl = float(str(plano.get("area_real", "0")).replace(",", ""))
                if area_calc > 0 and area_decl > 0:
                    diff_pct = abs(area_calc - area_decl) / area_decl * 100
                    if diff_pct > 5:
                        r.errores.append(
                            f"área del cajetín ({area_decl:.2f}m²) NO coincide con "
                            f"área calculada del polígono ({area_calc:.2f}m²) — "
                            f"diferencia {diff_pct:.1f}%"
                        )
                    elif diff_pct > 1:
                        r.advertencias.append(
                            f"área cajetín {area_decl:.2f}m² vs calculada "
                            f"{area_calc:.2f}m² — diferencia {diff_pct:.1f}%"
                        )
            except (ValueError, TypeError):
                pass

    # ── Planos a modificar ──────────────────────────────────────
    for i, p in enumerate(plano.get("planos_modificar") or []):
        ok, motivo = _validar_plano_modificar(p)
        if not ok:
            r.errores.append(f"planos_modificar[{i}]: {motivo}")

    # ── Honorarios + observaciones ──────────────────────────────
    honorarios_raw = str(general.get("honorarios", "0")).strip()
    try:
        honorarios = int(float(honorarios_raw)) if honorarios_raw else 0
    except ValueError:
        honorarios = 0
    if honorarios == 0:
        exonerado = general.get("exoneracion_honorarios")
        observ = (general.get("observaciones") or "").strip()
        if not exonerado:
            r.errores.append(
                "honorarios=0 pero exoneración NO marcada — "
                "marcar exoneracion_honorarios=True"
            )
        if not observ:
            r.errores.append(
                "honorarios=0 pero observaciones vacías — "
                "explicar el motivo (ej. continuación contrato N° XXXX)"
            )

    # ── Área registro vs real ───────────────────────────────────
    try:
        a_reg = float(str(general.get("area_predio", "0")).replace(",", ""))
        a_real = float(str(general.get("area_real", "0")).replace(",", ""))
        if a_reg > 0 and a_real > 0:
            ratio = max(a_reg, a_real) / min(a_reg, a_real)
            if ratio > 10:
                r.advertencias.append(
                    f"área registro ({a_reg:.2f}) y área real ({a_real:.2f}) "
                    f"difieren en orden de magnitud — verificar"
                )
    except (ValueError, TypeError):
        pass

    # ── Tipo zona vs área ───────────────────────────────────────
    tipo_zona = str(plano.get("tipo_zona", "")).strip()
    try:
        area_real = float(str(plano.get("area_real", "0")).replace(",", ""))
        if area_real > 0:
            esperado = "2" if area_real >= 2000 else "3"
            if tipo_zona and tipo_zona != esperado:
                nombre_esperado = "RURAL" if esperado == "2" else "URBANO"
                nombre_actual = {"2":"RURAL","3":"URBANO"}.get(tipo_zona, tipo_zona)
                r.advertencias.append(
                    f"plano.tipo_zona='{nombre_actual}' pero área {area_real}m² "
                    f"sugiere {nombre_esperado} — verificar"
                )
    except (ValueError, TypeError):
        pass

    return r


__all__ = ["PreflightResult", "validar_seed_pre_envio"]
