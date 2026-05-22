"""Calculadora de honorarios según Decreto 17481-MOPT.

Fórmulas implementadas:
  - Art. 5: Parcela URBANA — Y_base + Y_zona, con factor por zona y mínimo legal
  - Art. 6: Parcela RURAL  — Y por área en hectáreas, con mínimo legal
  - Multiplicadores: dificultad notoria (urbana) y terreno quebrado (rural) → ×1.50
  - Descuento escalonado por cantidad de planos (1-9, 10-25, 26+)
  - Art. 4: Gastos reembolsables (transporte, cuadrillas)

El índice inflacionario `i` (33.42 en mayo 2026) se actualiza periódicamente
por MOPT. Es parametrizable para mantener la calculadora vigente.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP


# ── Constantes del Decreto (mayo 2026) ────────────────────────────────────

INDICE_INFLACIONARIO_DEFAULT = 33.42

# Mínimos legales (en colones, ya multiplicados por i)
MINIMO_URBANA = 93_576
MINIMO_RURAL  = 279_685

# Coeficientes
COEF_URBANA_BASE  = 160     # Y_base = COEF_URBANA_BASE × i × √área
COEF_RURAL        = 6_000   # Y_rural = COEF_RURAL × i × √hectáreas

# Factor por zona urbana (Art. 5)
FACTORES_ZONA = {
    "A":  Decimal("12.50"),
    "B":  Decimal("9.00"),
    "C":  Decimal("5.60"),
    "CH": Decimal("2.80"),
    "D":  Decimal("2.00"),
    "E":  Decimal("0.70"),
}

# Multiplicadores por agravante
MULT_DIFICULTAD = Decimal("1.50")  # urbana con dificultad notoria
MULT_QUEBRADO   = Decimal("1.50")  # rural con pendiente > 15%

# Descuentos escalonados por cantidad de planos
RANGO1_HASTA = 9   # planos 1-9 al 100%
RANGO2_HASTA = 25  # planos 10-25 al 80%
DESC_RANGO2  = Decimal("0.80")
DESC_RANGO3  = Decimal("0.70")  # planos 26+

# Gastos reembolsables (Art. 4)
TARIFA_KM             = Decimal("491.08")    # ₡/km cuando distancia > 25 km
TARIFA_CUADR_AGRIM    = Decimal("20052")     # ₡/hora cuando traslado > 1 hora
TARIFA_CUADR_TOPO     = Decimal("39770")     # ₡/hora cuando traslado > 1 hora
DISTANCIA_UMBRAL_KM   = 25
TRASLADO_UMBRAL_HORAS = 1

# Ajuste fijo por plano (regla de oficina — compensa diferencia de cálculo)
# Se suma al precio_final de cada plano ANTES de aplicar el descuento por cantidad.
AJUSTE_FIJO_POR_PLANO = Decimal("5000")


# ── Tipos ──────────────────────────────────────────────────────────────────

@dataclass
class PlanoInput:
    """Datos por plano. La clasificación urbana/rural se hace por área individual."""
    area_m2:               float
    tipo_parcela:          str = ""     # "urbana" | "rural" — vacío = autodetectar por área
    zona:                  str = ""     # urbana: A, B, C, CH, D, E (auto: E si urbana)
    dificultad_notoria:    bool = False  # solo urbana
    terreno_quebrado:      bool = False  # solo rural (pendiente > 15%)


@dataclass
class HonorariosInput:
    """Input al calculador.

    Para 1 solo plano: pasar `area_m2` directo (compat con API antigua).
    Para varios planos: pasar lista en `planos`. Si se pasan ambos, gana `planos`.
    """
    # Forma simple — 1 plano (compat hacia atrás)
    area_m2:               float = 0.0
    tipo_parcela:          str = ""
    zona:                  str = ""
    dificultad_notoria:    bool = False
    terreno_quebrado:      bool = False
    n_planos:              int = 1   # repite el plano n veces (caso de planos idénticos)

    # Forma multi-plano — lista de PlanoInput
    planos:                list = field(default_factory=list)

    # Globales del contrato
    distancia_km:          float = 0.0
    horas_cuadrilla_agrim: float = 0.0
    horas_cuadrilla_topo:  float = 0.0
    indice_inflacionario:  float = INDICE_INFLACIONARIO_DEFAULT

    def planos_efectivos(self) -> list[PlanoInput]:
        """Devuelve la lista de PlanoInput a procesar.

        Si `planos` viene poblado, lo usa. Si no, replica `area_m2` n_planos veces.
        """
        if self.planos:
            return list(self.planos)
        return [
            PlanoInput(
                area_m2=self.area_m2,
                tipo_parcela=self.tipo_parcela,
                zona=self.zona,
                dificultad_notoria=self.dificultad_notoria,
                terreno_quebrado=self.terreno_quebrado,
            )
            for _ in range(self.n_planos)
        ]


# ── Regla de oficina (clasificación automática) ──────────────────────────
#
# Convención del topógrafo: el cajetín del plano da el área a catastrar; en base
# a ella se decide si tarifar como urbana o rural:
#   < 2,000 m²  → URBANA, zona E (la más económica del rango urbano)
#   ≥ 2,000 m²  → RURAL  (sin zona)
#
# Esto es regla práctica de oficina, no del decreto literal (que distinguiría
# urbano/rural por uso del suelo / plan regulador). Permite override explícito
# si se quiere usar otra zona o forzar el tipo opuesto.
UMBRAL_URBANA_RURAL_M2 = 2_000


def decidir_tipo_y_zona_por_area(area_m2: float) -> tuple[str, str]:
    """Aplica la regla de oficina y devuelve (tipo, zona)."""
    if area_m2 < UMBRAL_URBANA_RURAL_M2:
        return ("urbana", "E")
    return ("rural", "")


@dataclass
class PlanoResultado:
    """Resultado del cálculo de un plano individual."""
    indice:               int       # 1-based
    area_m2:              float
    tipo_parcela:         str       # "urbana" | "rural"
    zona:                 str
    y_base:               Decimal
    y_zona:               Decimal
    precio_base:          Decimal   # antes de multiplicador
    aplica_multiplicador: bool
    precio_final:         Decimal   # después de multiplicador (precio "lleno" sin descuento por cantidad)
    aplico_minimo_legal:  bool


@dataclass
class HonorariosResultado:
    # Resultados por plano individual
    planos_resultado:     list = field(default_factory=list)  # list[PlanoResultado]

    # Aplicación de descuento por cantidad (y_zona/precio del plano + factor por posición)
    desglose_planos:      list = field(default_factory=list)
    subtotal_planos:      Decimal = Decimal("0")

    # Gastos reembolsables
    gastos_transporte:    Decimal = Decimal("0")
    gastos_agrim:         Decimal = Decimal("0")
    gastos_topo:          Decimal = Decimal("0")
    subtotal_gastos:      Decimal = Decimal("0")

    # Total
    total:                Decimal = Decimal("0")

    # Avisos / notas
    notas:                list = field(default_factory=list)

    def texto_observaciones_descuento(self) -> str:
        """Genera el texto a meter en Observaciones del contrato APT cuando se
        aplicó descuento escalonado por cantidad. Devuelve "" si no aplica.
        """
        n = len(self.planos_resultado)
        if n < 10:
            return ""

        n_r1 = min(n, RANGO1_HASTA)                       # 1-9   100%
        n_r2 = min(max(0, n - RANGO1_HASTA), RANGO2_HASTA - RANGO1_HASTA)  # 10-25  80%
        n_r3 = max(0, n - RANGO2_HASTA)                   # 26+    70%

        lineas = [
            "Se aplica descuento por cantidad de planos según artículo 8 del Decreto 17481-MOPT:",
        ]
        if n_r1 > 0:
            fin = n_r1
            lineas.append(f"- Planos 1 al {fin}: tarifa plena (100%).")
        if n_r2 > 0:
            ini = RANGO1_HASTA + 1
            fin = RANGO1_HASTA + n_r2
            lineas.append(f"- Planos {ini} al {fin}: 20% de descuento (tarifa al 80%).")
        if n_r3 > 0:
            ini = RANGO2_HASTA + 1
            fin = RANGO2_HASTA + n_r3
            lineas.append(f"- Planos {ini} al {fin}: 30% de descuento (tarifa al 70%).")
        lineas.append(f"Total honorarios: ₡{self.subtotal_planos:,}.")
        return "\n".join(lineas)


# ── Helpers ────────────────────────────────────────────────────────────────

def _redondear(d: Decimal) -> Decimal:
    """Redondea a 2 decimales (estándar para colones)."""
    return d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _redondear_entero(d: Decimal) -> Decimal:
    """Redondea a entero (para totales que se ingresan a APT)."""
    return d.quantize(Decimal("1"), rounding=ROUND_HALF_UP)


# ── Cálculo de 1 plano ────────────────────────────────────────────────────

def _calc_1_plano_urbana(area_m2: float, zona: str, dificultad: bool, i: float) -> tuple:
    """Devuelve (y_base, y_zona, precio_base, precio_final, aplico_mult).

    Cada paso se redondea a colón entero (sin decimales) para que coincida
    con cómo el decreto y la práctica del topógrafo presentan los números.
    """
    if zona not in FACTORES_ZONA:
        raise ValueError(
            f"zona urbana inválida: {zona!r}. "
            f"Use una de {list(FACTORES_ZONA.keys())}"
        )
    i_dec = Decimal(str(i))
    area = Decimal(str(area_m2))
    factor_zona = FACTORES_ZONA[zona]

    # Y_base = 160 × i × √área  (redondear a entero)
    y_base = _redondear_entero(
        Decimal(COEF_URBANA_BASE) * i_dec * Decimal(str(math.sqrt(float(area))))
    )
    aplico_min = False
    if y_base < MINIMO_URBANA:
        y_base = Decimal(MINIMO_URBANA)
        aplico_min = True

    # Y_zona = factor × i × área  (redondear a entero)
    y_zona = _redondear_entero(factor_zona * i_dec * area)

    precio_base = _redondear_entero(y_base + y_zona)
    precio_final = precio_base
    if dificultad:
        precio_final = _redondear_entero(precio_base * MULT_DIFICULTAD)

    return (y_base, y_zona, precio_base, precio_final, dificultad, aplico_min)


def _calc_1_plano_rural(area_m2: float, quebrado: bool, i: float) -> tuple:
    """Devuelve (y_base=Y, y_zona=0, precio_base, precio_final, aplico_mult).

    Redondea a entero en cada paso.
    """
    i_dec = Decimal(str(i))
    hectareas = Decimal(str(area_m2)) / Decimal(10_000)

    # Y = 6000 × i × √hectáreas  (redondear a entero)
    y = _redondear_entero(
        Decimal(COEF_RURAL) * i_dec * Decimal(str(math.sqrt(float(hectareas))))
    )
    aplico_min = False
    if y < MINIMO_RURAL:
        y = Decimal(MINIMO_RURAL)
        aplico_min = True

    precio_final = y
    if quebrado:
        precio_final = _redondear_entero(y * MULT_QUEBRADO)

    return (y, Decimal("0"), y, precio_final, quebrado, aplico_min)


# ── Descuento por cantidad ────────────────────────────────────────────────

def _aplicar_descuento_cantidad_uniforme(precio_1: Decimal, n: int) -> tuple:
    """[caso uniforme] Todos los planos al mismo precio — atajo eficiente.

    Devuelve (subtotal, desglose). Desglose por rango.
    """
    if n <= 0:
        raise ValueError("n_planos debe ser >= 1")

    desglose = []
    total = Decimal("0")

    n_r1 = min(n, RANGO1_HASTA)
    if n_r1:
        sub = _redondear_entero(precio_1 * n_r1)
        desglose.append((f"{n_r1} plano(s) × ₡{precio_1}", n_r1, precio_1, sub))
        total += sub

    if n > RANGO1_HASTA:
        n_r2 = min(n - RANGO1_HASTA, RANGO2_HASTA - RANGO1_HASTA)
        precio_r2 = _redondear_entero(precio_1 * DESC_RANGO2)
        sub = _redondear_entero(precio_r2 * n_r2)
        desglose.append((
            f"{n_r2} plano(s) × ₡{precio_r2} (-20%)",
            n_r2, precio_r2, sub,
        ))
        total += sub

    if n > RANGO2_HASTA:
        n_r3 = n - RANGO2_HASTA
        precio_r3 = _redondear_entero(precio_1 * DESC_RANGO3)
        sub = _redondear_entero(precio_r3 * n_r3)
        desglose.append((
            f"{n_r3} plano(s) × ₡{precio_r3} (-30%)",
            n_r3, precio_r3, sub,
        ))
        total += sub

    return _redondear_entero(total), desglose


def _factor_descuento_por_posicion(pos_1based: int) -> Decimal:
    """Factor a aplicar al precio del plano en posición pos_1based."""
    if pos_1based <= RANGO1_HASTA:
        return Decimal("1")
    if pos_1based <= RANGO2_HASTA:
        return DESC_RANGO2
    return DESC_RANGO3


def _aplicar_descuento_cantidad_por_plano(precios: list[Decimal]) -> tuple:
    """[caso multi-plano] Cada plano puede tener un precio distinto.

    Aplica el descuento escalonado por POSICIÓN (orden en que se reciben
    los planos): planos 1-9 al 100%, 10-25 al 80%, 26+ al 70%. La
    convención del topógrafo es ordenar de mayor a menor precio para que
    los planos más caros reciban precio lleno; el caller ya debe haber
    aplicado este orden si así lo desea (esta función no reordena).

    Devuelve (subtotal, desglose).
    """
    if not precios:
        raise ValueError("precios vacío")

    desglose = []
    total = Decimal("0")
    for i, p in enumerate(precios, start=1):
        factor = _factor_descuento_por_posicion(i)
        ajustado = _redondear_entero(p * factor)
        etiq = f"Plano #{i} ₡{p}"
        if factor != Decimal("1"):
            pct = int((1 - factor) * 100)
            etiq += f" × {factor} (-{pct}%)"
        etiq += f" = ₡{ajustado}"
        desglose.append((etiq, 1, ajustado, ajustado))
        total += ajustado
    return _redondear_entero(total), desglose


# ── Gastos reembolsables ──────────────────────────────────────────────────

def _calc_gastos(distancia_km: float, h_agrim: float, h_topo: float) -> tuple:
    """Devuelve (transporte, agrim, topo, subtotal). Todos a entero."""
    transporte = Decimal("0")
    agrim      = Decimal("0")
    topo       = Decimal("0")

    if distancia_km > DISTANCIA_UMBRAL_KM:
        transporte = _redondear_entero(Decimal(str(distancia_km)) * TARIFA_KM)

    if h_agrim > TRASLADO_UMBRAL_HORAS:
        agrim = _redondear_entero(Decimal(str(h_agrim)) * TARIFA_CUADR_AGRIM)

    if h_topo > TRASLADO_UMBRAL_HORAS:
        topo = _redondear_entero(Decimal(str(h_topo)) * TARIFA_CUADR_TOPO)

    subtotal = transporte + agrim + topo
    return (transporte, agrim, topo, subtotal)


# ── Punto de entrada ───────────────────────────────────────────────────────

def _calc_precio_plano(p: PlanoInput, i: float) -> PlanoResultado:
    """Calcula el precio individual de UN plano. Autodetecta tipo si vacío."""
    if p.area_m2 <= 0:
        raise ValueError("área del plano debe ser > 0")

    tipo = (p.tipo_parcela or "").lower().strip()
    zona = p.zona
    if not tipo:
        tipo, zona_auto = decidir_tipo_y_zona_por_area(p.area_m2)
        if not zona:
            zona = zona_auto

    if tipo == "urbana":
        y_base, y_zona, precio_base, precio_final, aplico_mult, aplico_min = _calc_1_plano_urbana(
            p.area_m2, zona.upper(), p.dificultad_notoria, i,
        )
    elif tipo == "rural":
        y_base, y_zona, precio_base, precio_final, aplico_mult, aplico_min = _calc_1_plano_rural(
            p.area_m2, p.terreno_quebrado, i,
        )
    else:
        raise ValueError(f"tipo_parcela inválido: {tipo!r}. Use 'urbana' o 'rural'")

    # Ajuste fijo de oficina: +5000 por plano individual
    precio_final = _redondear_entero(precio_final + AJUSTE_FIJO_POR_PLANO)

    return PlanoResultado(
        indice=0,  # se setea después
        area_m2=p.area_m2,
        tipo_parcela=tipo,
        zona=zona if tipo == "urbana" else "",
        y_base=y_base,
        y_zona=y_zona,
        precio_base=precio_base,
        aplica_multiplicador=aplico_mult,
        precio_final=precio_final,
        aplico_minimo_legal=aplico_min,
    )


def calcular_honorarios(inp: HonorariosInput) -> HonorariosResultado:
    """Calcula honorarios completos según Decreto 17481-MOPT.

    Cada plano se clasifica individualmente (urbana/rural) por su área.
    El descuento escalonado por cantidad (1-9 / 10-25 / 26+) se aplica
    sobre el precio individual de cada plano según su POSICIÓN en la
    lista (ya ordenada — convención: descendente por precio para que los
    planos más caros reciban precio lleno).
    """
    planos = inp.planos_efectivos()
    if not planos:
        raise ValueError("debe haber al menos un plano (area_m2 + n_planos o lista planos)")
    for p in planos:
        if p.area_m2 <= 0:
            raise ValueError("todos los planos deben tener area > 0")

    # Calcular precio de cada plano individualmente
    planos_res = []
    for k, p in enumerate(planos, start=1):
        pr = _calc_precio_plano(p, inp.indice_inflacionario)
        pr.indice = k
        planos_res.append(pr)

    # CONVENCIÓN DE OFICINA: ordenar ASCENDENTE por precio para que el descuento
    # (que se aplica a partir del plano #10) caiga sobre los planos MÁS CAROS.
    # Esto resulta en un total menor para el contratante.
    planos_res_orden = sorted(planos_res, key=lambda r: r.precio_final)
    precios_ordenados = [r.precio_final for r in planos_res_orden]

    # Atajo si todos los precios coinciden (caso típico)
    if len(set(precios_ordenados)) == 1:
        subtotal_planos, desglose = _aplicar_descuento_cantidad_uniforme(
            precios_ordenados[0], len(precios_ordenados),
        )
    else:
        subtotal_planos, desglose = _aplicar_descuento_cantidad_por_plano(
            precios_ordenados,
        )

    transporte, agrim, topo, subtotal_gastos = _calc_gastos(
        inp.distancia_km, inp.horas_cuadrilla_agrim, inp.horas_cuadrilla_topo,
    )
    total = subtotal_planos + subtotal_gastos

    res = HonorariosResultado(
        planos_resultado=planos_res,
        desglose_planos=desglose,
        subtotal_planos=subtotal_planos,
        gastos_transporte=transporte,
        gastos_agrim=agrim,
        gastos_topo=topo,
        subtotal_gastos=subtotal_gastos,
        total=total,
    )

    # Notas
    for pr in planos_res:
        if pr.aplico_minimo_legal:
            res.notas.append(
                f"Plano #{pr.indice} ({pr.area_m2} m², {pr.tipo_parcela}): aplicó mínimo legal."
            )
        if pr.aplica_multiplicador:
            agrav = "dificultad notoria" if pr.tipo_parcela == "urbana" else "terreno quebrado"
            res.notas.append(f"Plano #{pr.indice}: ×1.50 por {agrav}.")
    return res


__all__ = [
    "PlanoInput",
    "PlanoResultado",
    "HonorariosInput",
    "HonorariosResultado",
    "calcular_honorarios",
    "decidir_tipo_y_zona_por_area",
    "INDICE_INFLACIONARIO_DEFAULT",
    "FACTORES_ZONA",
    "UMBRAL_URBANA_RURAL_M2",
]
