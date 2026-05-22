"""Conversor de número a letras en español (Costa Rica) para montos en colones.

Útil para llenar el campo "honorarios en letras" del contrato APT.
Maneja enteros hasta 999,999,999. Suficiente para honorarios típicos.
"""
from __future__ import annotations


_UNIDADES = ["", "UNO", "DOS", "TRES", "CUATRO", "CINCO", "SEIS", "SIETE", "OCHO", "NUEVE"]
_DIEZ_DIECINUEVE = [
    "DIEZ", "ONCE", "DOCE", "TRECE", "CATORCE", "QUINCE",
    "DIECISÉIS", "DIECISIETE", "DIECIOCHO", "DIECINUEVE",
]
_DECENAS = ["", "", "VEINTE", "TREINTA", "CUARENTA", "CINCUENTA", "SESENTA", "SETENTA", "OCHENTA", "NOVENTA"]
_VEINTITANTOS = [
    "VEINTE", "VEINTIUNO", "VEINTIDÓS", "VEINTITRÉS", "VEINTICUATRO",
    "VEINTICINCO", "VEINTISÉIS", "VEINTISIETE", "VEINTIOCHO", "VEINTINUEVE",
]
_CENTENAS = [
    "", "CIENTO", "DOSCIENTOS", "TRESCIENTOS", "CUATROCIENTOS", "QUINIENTOS",
    "SEISCIENTOS", "SETECIENTOS", "OCHOCIENTOS", "NOVECIENTOS",
]


def _hasta_99(n: int) -> str:
    if n == 0:
        return ""
    if n < 10:
        return _UNIDADES[n]
    if 10 <= n < 20:
        return _DIEZ_DIECINUEVE[n - 10]
    if 20 <= n < 30:
        return _VEINTITANTOS[n - 20]
    decena = n // 10
    unidad = n % 10
    if unidad == 0:
        return _DECENAS[decena]
    return f"{_DECENAS[decena]} Y {_UNIDADES[unidad]}"


def _hasta_999(n: int) -> str:
    if n == 0:
        return ""
    if n == 100:
        return "CIEN"
    centena = n // 100
    resto = n % 100
    parte_c = _CENTENAS[centena] if centena else ""
    parte_resto = _hasta_99(resto)
    if parte_c and parte_resto:
        return f"{parte_c} {parte_resto}"
    return parte_c or parte_resto


def _miles(n: int) -> str:
    """Convierte 1..999_999_999 a letras."""
    if n == 0:
        return "CERO"
    millones = n // 1_000_000
    resto_m = n % 1_000_000
    miles = resto_m // 1000
    resto = resto_m % 1000

    partes = []
    if millones:
        if millones == 1:
            partes.append("UN MILLÓN")
        else:
            partes.append(f"{_hasta_999(millones)} MILLONES")
    if miles:
        if miles == 1:
            partes.append("MIL")
        else:
            partes.append(f"{_hasta_999(miles)} MIL")
    if resto:
        partes.append(_hasta_999(resto))
    return " ".join(partes)


def numero_a_letras_colones(monto: float | int | str) -> str:
    """Devuelve el monto en letras + 'COLONES' (sin centavos).

    Ejemplos:
      295352  → "DOSCIENTOS NOVENTA Y CINCO MIL TRESCIENTOS CINCUENTA Y DOS COLONES"
      4878680 → "CUATRO MILLONES OCHOCIENTOS SETENTA Y OCHO MIL SEISCIENTOS OCHENTA COLONES"
      0       → "CERO COLONES"
    """
    try:
        n = int(round(float(str(monto).replace(",", "").strip())))
    except (ValueError, TypeError):
        return ""
    if n < 0:
        return ""  # no soportamos negativos
    if n > 999_999_999:
        return ""  # fuera de rango
    return f"{_miles(n)} COLONES"


__all__ = ["numero_a_letras_colones"]
