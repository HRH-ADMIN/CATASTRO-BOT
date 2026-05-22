"""Corrección automática de texto en archivos DWG/DXF — catastro-bot.

Cuando el APT detecta errores menores de texto (área incorrecta, número de
cantón/provincia, notas de época, etc.) este módulo intenta corregirlos
directamente en el archivo DWG del topógrafo usando ezdxf.

Limitaciones conocidas
──────────────────────
* ezdxf puede LEER archivos DWG (R2000+) y DXF, pero solo ESCRIBE DXF.
  El archivo corregido se guarda como ``{stem}_corr.dxf`` junto al original.
* Valores sanitizados antes de llegar a la IA (número de finca, cédula,
  número de plano) no son recuperables de la minuta analizada. Para esos
  casos, el operador o topógrafo debe usar el comando WhatsApp:
    CORREGIR <expediente> <campo> <valor_incorrecto> <valor_correcto>
* Si ezdxf no está instalado, todas las funciones devuelven un
  ``ResultadoCorreccion`` con ``modificado=False`` y ``error`` informativo.

Entidades procesadas
────────────────────
TEXT, MTEXT, ATTDEF, ATTRIB en el modelspace y todos los bloques del
documento (el plano catastral suele tener bloques de carátula con los datos).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from src.utils.logger import get_logger

_log = get_logger("dwg_corrector")


# ── modelos de datos ──────────────────────────────────────────────────────────

@dataclass
class CorreccionTexto:
    """Par (valor_actual → valor_correcto) para una entidad TEXT en DWG.

    ``campo`` es solo descriptivo (p.ej. "area", "canton", "nota") y se usa
    únicamente para el log — no filtra ninguna entidad.
    """
    campo: str          # nombre descriptivo del campo que se corrige
    valor_actual: str   # texto incorrecto tal como aparece en el DWG
    valor_correcto: str # texto correcto que debe quedar


@dataclass
class ResultadoCorreccion:
    """Resultado de intentar aplicar correcciones a un DWG/DXF."""

    modificado: bool = False
    """True si se aplicó al menos una corrección y se guardó el DXF."""

    archivo_corregido: Path | None = None
    """Ruta del DXF corregido ({stem}_corr.dxf). None si no hubo cambios."""

    aplicadas: list[tuple[str, str]] = field(default_factory=list)
    """Pares (valor_incorrecto, valor_correcto) que se encontraron y cambiaron."""

    no_encontradas: list[tuple[str, str]] = field(default_factory=list)
    """Pares cuyo valor_incorrecto no se encontró en ninguna entidad."""

    error: str | None = None
    """Mensaje de error si algo falló antes de intentar correcciones."""

    @property
    def resumen(self) -> str:
        """Resumen legible para enviar por WhatsApp al topógrafo."""
        if self.error:
            return f"⚠️ No fue posible corregir automáticamente: {self.error}"
        partes: list[str] = []
        if self.aplicadas:
            detalles = ", ".join(f"«{v}»→«{n}»" for v, n in self.aplicadas)
            partes.append(f"✅ Corregido en DWG: {detalles}")
        if self.no_encontradas:
            faltantes = ", ".join(f"«{v}»" for v, _ in self.no_encontradas)
            partes.append(
                f"⚠️ No encontrado en el DWG (corrija manualmente): {faltantes}"
            )
        return "\n".join(partes) if partes else "Sin cambios detectados."


# ── función principal ─────────────────────────────────────────────────────────

def aplicar_correcciones(
    dwg_path: Path,
    correcciones: list[CorreccionTexto],
    *,
    ignorar_mayusculas: bool = True,
) -> ResultadoCorreccion:
    """Aplica correcciones de texto a un DWG/DXF y guarda el resultado como DXF.

    Args:
        dwg_path: Ruta al archivo ``.dwg`` o ``.dxf`` original.
        correcciones: Lista de pares ``(campo, valor_actual, valor_correcto)``.
            Entradas con ``valor_actual`` o ``valor_correcto`` vacíos son
            ignoradas silenciosamente.
        ignorar_mayusculas: Si True, la búsqueda es case-insensitive pero la
            sustitución preserva el case del texto encontrado (p.ej.
            ``"1250.50 M²"`` → ``"1300.00 M²"``).

    Returns:
        :class:`ResultadoCorreccion` con detalle de lo aplicado y lo pendiente.
    """
    # Filtrar entradas vacías / idempotentes
    correcciones = [
        c for c in correcciones
        if c.valor_actual and c.valor_correcto
           and c.valor_actual != c.valor_correcto
    ]
    if not correcciones:
        return ResultadoCorreccion()

    # Comprobar existencia del archivo ANTES de importar ezdxf, para que el
    # error "no encontrado" sea el que llega al caller en ese caso.
    if not dwg_path.is_file():
        return ResultadoCorreccion(error=f"archivo no encontrado: {dwg_path}")

    try:
        import ezdxf  # noqa: PLC0415
    except ImportError:
        _log.warning("dwg_corrector: ezdxf no instalado — corrección omitida")
        return ResultadoCorreccion(error="ezdxf no instalado")

    try:
        doc = ezdxf.readfile(str(dwg_path))
    except Exception as exc:  # noqa: BLE001
        _log.exception("dwg_corrector: no se puede leer %s", dwg_path.name)
        return ResultadoCorreccion(error=f"no se puede leer el DWG: {exc}")

    aplicadas: list[tuple[str, str]] = []
    no_encontradas: list[tuple[str, str]] = []

    for corr in correcciones:
        encontrado = _reemplazar_en_doc(
            doc, corr.valor_actual, corr.valor_correcto,
            ignorar_mayusculas=ignorar_mayusculas,
        )
        if encontrado:
            aplicadas.append((corr.valor_actual, corr.valor_correcto))
            _log.info(
                "dwg_corrector: campo=%s  «%s» → «%s»",
                corr.campo, corr.valor_actual, corr.valor_correcto,
            )
        else:
            no_encontradas.append((corr.valor_actual, corr.valor_correcto))
            _log.warning(
                "dwg_corrector: «%s» no encontrado en %s (campo=%s)",
                corr.valor_actual, dwg_path.name, corr.campo,
            )

    if not aplicadas:
        return ResultadoCorreccion(no_encontradas=no_encontradas)

    # Guardar como DXF (ezdxf no soporta escritura en .dwg)
    destino = dwg_path.with_name(dwg_path.stem + "_corr.dxf")
    try:
        doc.saveas(str(destino))
        _log.info(
            "dwg_corrector: guardado %s (%d correccion(es) aplicadas)",
            destino.name, len(aplicadas),
        )
    except Exception as exc:  # noqa: BLE001
        _log.exception("dwg_corrector: error guardando %s", destino)
        return ResultadoCorreccion(
            error=f"no se pudo guardar el DXF corregido: {exc}",
            aplicadas=aplicadas,
            no_encontradas=no_encontradas,
        )

    return ResultadoCorreccion(
        modificado=True,
        archivo_corregido=destino,
        aplicadas=aplicadas,
        no_encontradas=no_encontradas,
    )


# ── lógica interna ────────────────────────────────────────────────────────────

def _reemplazar_en_doc(
    doc, old: str, new: str, *, ignorar_mayusculas: bool
) -> bool:
    """Reemplaza ``old`` por ``new`` en TEXT/MTEXT/ATTDEF/ATTRIB del documento.

    Busca en el modelspace y en todos los bloques (la carátula del plano suele
    estar en un bloque de título).

    Returns:
        True si encontró y reemplazó al menos una ocurrencia.
    """
    encontrado = False

    flags = re.IGNORECASE if ignorar_mayusculas else 0
    patron = re.compile(re.escape(old), flags)

    def _coincide(texto: str) -> bool:
        return bool(patron.search(texto))

    def _sustituir(texto: str) -> str:
        return patron.sub(new, texto)

    # modelspace + todos los bloques del documento
    espacios = [doc.modelspace()]
    try:
        for blk in doc.blocks:
            espacios.append(blk)
    except Exception:  # noqa: BLE001
        pass  # en algunos DWG los bloques no son iterables directamente

    for espacio in espacios:
        for entity in espacio:
            try:
                dxftype = entity.dxftype()
                if dxftype == "TEXT":
                    texto = entity.dxf.get("text", "")
                    if _coincide(texto):
                        entity.dxf.text = _sustituir(texto)
                        encontrado = True
                elif dxftype == "MTEXT":
                    texto = entity.text
                    if _coincide(texto):
                        entity.text = _sustituir(texto)
                        encontrado = True
                elif dxftype in ("ATTDEF", "ATTRIB"):
                    texto = entity.dxf.get("text", "")
                    if _coincide(texto):
                        entity.dxf.text = _sustituir(texto)
                        encontrado = True
            except Exception:  # noqa: BLE001
                # Entidades sin atributo text o con formato especial — ignorar
                pass

    return encontrado
