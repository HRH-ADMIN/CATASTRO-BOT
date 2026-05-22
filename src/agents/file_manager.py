"""Vigilancia de carpetas Mega con Watchdog + validación de archivos.

Monitorea en tiempo real dos tipos de carpetas dentro de ACTIVOS\:
  - SUBIR\     → topógrafo copió archivos nuevos para procesar
  - RESPUESTA\ → sistema (APT o municipalidad) depositó una respuesta

Al detectar un archivo nuevo en SUBIR\:
  1. Espera 30 segundos (para que termine de copiarse)
  2. Identifica el expediente por ruta (mega_path en BD)
  3. Clasifica y valida los archivos
  4. Notifica al operador por WhatsApp con el reporte
  5. Registra los archivos en la BD

Al detectar un archivo en RESPUESTA\:
  1. Clasifica por regex (minuta, inscripción, visado, correcciones)
  2. Identifica expediente
  3. Notifica al orchestrator para avanzar el flujo

Seguridad:
  - NUNCA modifica los archivos originales sin respaldo
  - La versión procesada se guarda como {nombre}_bot.pdf
  - Todos los errores se loggean y notifican por WhatsApp sin hacer crash
"""
from __future__ import annotations

import re
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from config.settings import MEGA_ROOT
from src.utils.display import display_proyecto
from src.utils.logger import get_logger

_log = get_logger("file_manager")

# ── Clasificación de archivos ─────────────────────────────────────────────────

# Nombres EXACTOS (sin extensión, minúsculas) → tipo de archivo.
# Prioridad máxima: plano.pdf → anverso, entero.pdf → entero, etc.
# El usuario coloca estos archivos con nombres exactos en la carpeta del plano.
_EXACT_NAMES: dict[str, str] = {
    "plano":         "anverso",      # plano.pdf = anverso catastral
    "entero":        "entero",       # entero.pdf = comprobante BCR
    "imagenminuta":  "imagen_minuta",# imagenminuta.pdf = imagen de la minuta APT
    "minuta":        "minuta_apt",   # minuta.pdf = minuta de observaciones APT
    "amberso":       "amberso",      # amberso.pdf = plano corregido para R2 + muni
    "visado":        "visado",       # visado.pdf = visado municipal aprobado
    "carta_agua":    "carta_agua",   # carta_agua.pdf = carta AyA
    "cartaagua":     "carta_agua",   # alias sin guión bajo
    "croquis":       "croquis",      # croquis.pdf = croquis de ubicación
}

# Nombres exactos para ZIPs (sin extensión, minúsculas) → tipo
_EXACT_ZIP_NAMES: dict[str, str] = {
    "shape": "shape",   # shape.zip = shapefile catastral
}

# Patrones de nombre de archivo (en minúsculas) — fallback cuando no hay exact match
_PDF_ANVERSO_TAGS  = ("_final", "_f_", "_f.")
_PDF_ENTERO_TAGS   = ("entero",)
_PDF_CARTA_TAGS    = ("carta", "agua")
_PDF_CROQUIS_TAGS  = ("croquis",)
_PDF_VISADO_TAGS   = ("visado",)

# Clasificación de archivos en RESPUESTA\
_RESP_PATTERNS = [
    ("imagen_minuta",    re.compile(r"imagenminuta", re.I)),
    ("minuta_apt",       re.compile(r"minuta",        re.I)),
    ("inscripcion_apt",  re.compile(r"inscripcion",   re.I)),
    ("visado_municipal", re.compile(r"visado",        re.I)),
    ("correcciones_apt", re.compile(r"\d{4}[\s\-]+\d+[\s\-]+c\.pdf$", re.I)),
]

# Límites de tamaño
_MAX_PDF_BYTES  = 600 * 1024   # 600 KB
_MAX_ZIP_BYTES  = 1_500 * 1024  # 1.5 MB


@dataclass
class ValidacionResultado:
    ok: bool
    tipo: str
    path: Path
    original_bytes: int
    final_bytes: int
    procesado_path: Optional[Path] = None
    tenia_color: bool = False
    mensaje: str = ""
    errores: list[str] = field(default_factory=list)

    @property
    def original_kb(self) -> int:
        return self.original_bytes // 1024

    @property
    def final_kb(self) -> int:
        return self.final_bytes // 1024


# ── Validación ────────────────────────────────────────────────────────────────

def _clasificar_pdf(path: Path, todos_pdfs: list[Path]) -> str:
    """Clasifica un PDF por su nombre. Devuelve el tipo.

    Prioridad:
      1. Nombre exacto en _EXACT_NAMES (plano.pdf, entero.pdf, …)
      2. Patrones de tag en el nombre del archivo
      3. Si es el único PDF → anverso
    """
    nombre = path.stem.lower()

    # 1. Nombre exacto (prioridad máxima)
    if nombre in _EXACT_NAMES:
        return _EXACT_NAMES[nombre]

    # 2. Patrones de tag
    for tag in _PDF_ENTERO_TAGS:
        if tag in nombre:
            return "entero"
    for tag in _PDF_CARTA_TAGS:
        if tag in nombre:
            return "carta_agua"
    for tag in _PDF_CROQUIS_TAGS:
        if tag in nombre:
            return "croquis"
    for tag in _PDF_VISADO_TAGS:
        if tag in nombre:
            return "visado"
    for tag in _PDF_ANVERSO_TAGS:
        if tag in nombre:
            return "anverso"

    # 3. Si es el único PDF, es el anverso
    if len(todos_pdfs) == 1:
        return "anverso"
    return "desconocido"


def validar_pdf_anverso(path: Path) -> ValidacionResultado:
    """Valida y procesa un PDF anverso: verifica B&N, comprime si necesario.

    Si tiene color → convierte a escala de grises.
    Si supera 600 KB → comprime reduciendo DPI.
    Guarda versión procesada como {nombre}_bot.pdf junto al original.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return ValidacionResultado(
            ok=False, tipo="anverso", path=path,
            original_bytes=path.stat().st_size,
            final_bytes=path.stat().st_size,
            mensaje="PyMuPDF no disponible — no se puede validar PDF",
        )

    original_bytes = path.stat().st_size
    errores: list[str] = []
    tenia_color = False

    try:
        doc = fitz.open(str(path))
    except Exception as e:
        return ValidacionResultado(
            ok=False, tipo="anverso", path=path,
            original_bytes=original_bytes, final_bytes=original_bytes,
            mensaje=f"PDF inválido: {e}",
        )

    # ── Detectar color en primeras 3 páginas ──────────────────────────────
    paginas_a_revisar = min(3, len(doc))
    for i in range(paginas_a_revisar):
        page = doc[i]
        # Renderiza a baja resolución para checar color rápido
        mat   = fitz.Matrix(0.5, 0.5)
        pix   = page.get_pixmap(matrix=mat, colorspace=fitz.csRGB)
        muestra = pix.samples
        # Si hay diferencia significativa entre canales R/G/B → tiene color
        paso = max(1, len(muestra) // 900)
        for j in range(0, len(muestra) - 2, 3 * paso):
            r, g, b = muestra[j], muestra[j + 1], muestra[j + 2]
            if abs(int(r) - int(g)) > 12 or abs(int(g) - int(b)) > 12:
                tenia_color = True
                break
        if tenia_color:
            break

    # ── Crear documento de salida ─────────────────────────────────────────
    salida_path = path.parent / f"{path.stem}_bot.pdf"
    nuevo_doc = fitz.open()

    necesita_proceso = tenia_color or original_bytes > _MAX_PDF_BYTES

    if not necesita_proceso:
        # Sin cambios — copiar directamente
        doc.close()
        import shutil
        shutil.copy2(path, salida_path)
        return ValidacionResultado(
            ok=True, tipo="anverso", path=path,
            original_bytes=original_bytes,
            final_bytes=salida_path.stat().st_size,
            procesado_path=salida_path,
            tenia_color=False,
            mensaje="PDF OK — B&N, tamaño correcto",
        )

    # Convertir a B&N y/o comprimir
    dpi = 150 if original_bytes > _MAX_PDF_BYTES else 200
    mat = fitz.Matrix(dpi / 72, dpi / 72)

    for page in doc:
        pix  = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
        img  = nuevo_doc.new_page(width=page.rect.width, height=page.rect.height)
        img.insert_image(img.rect, pixmap=pix)

    nuevo_doc.save(str(salida_path), garbage=4, deflate=True)
    nuevo_doc.close()
    doc.close()

    final_bytes = salida_path.stat().st_size

    # Si sigue siendo muy grande, reducir DPI más agresivamente
    if final_bytes > _MAX_PDF_BYTES:
        errores.append(
            f"aún supera {_MAX_PDF_BYTES // 1024}KB tras compresión "
            f"({final_bytes // 1024}KB) — revisar manualmente"
        )

    partes = []
    if tenia_color:
        partes.append(f"convertido a B&N ({original_bytes // 1024}KB→{final_bytes // 1024}KB)")
    if original_bytes > _MAX_PDF_BYTES:
        partes.append(f"comprimido (DPI={dpi})")

    return ValidacionResultado(
        ok=final_bytes <= _MAX_PDF_BYTES or not errores,
        tipo="anverso", path=path,
        original_bytes=original_bytes,
        final_bytes=final_bytes,
        procesado_path=salida_path,
        tenia_color=tenia_color,
        mensaje="; ".join(partes) if partes else "PDF OK",
        errores=errores,
    )


def validar_pdf_entero(path: Path) -> ValidacionResultado:
    """Valida un PDF de enteros: extrae número y monto."""
    original_bytes = path.stat().st_size
    try:
        import fitz
        doc = fitz.open(str(path))
        texto = "".join(page.get_text() for page in doc)
        doc.close()
    except ImportError:
        return ValidacionResultado(
            ok=True, tipo="entero", path=path,
            original_bytes=original_bytes, final_bytes=original_bytes,
            mensaje="PyMuPDF no disponible — PDF aceptado sin verificar contenido",
        )
    except Exception as e:
        return ValidacionResultado(
            ok=False, tipo="entero", path=path,
            original_bytes=original_bytes, final_bytes=original_bytes,
            mensaje=f"PDF inválido: {e}",
        )

    # Buscar número de entero BCR — primero busca la etiqueta específica
    # del comprobante BCR ("Número de entero"), luego el valor que sigue.
    # Si no hay etiqueta, toma todos los números de 9+ dígitos y filtra
    # los que parecen referencias de transacción (van acompañados de fecha BCR).
    m_etiqueta = re.search(
        r"[Nn][uú]mero\s+de\s+entero.*?(\d{9,})",
        texto,
        re.DOTALL | re.IGNORECASE,
    )
    if m_etiqueta:
        nums = [m_etiqueta.group(1)]
    else:
        # Fallback: excluir números que aparecen en líneas con "BCR" y hora
        lineas = texto.split("\n")
        nums = []
        for i, linea in enumerate(lineas):
            if re.search(r"\b\d{9,}\b", linea):
                contexto = " ".join(lineas[max(0, i-2):i+3])
                # Saltar si parece número de transacción BCR (tiene fecha/hora cerca)
                if re.search(r"BCR\s+\d{2}/\d{2}/\d{4}", contexto):
                    continue
                nums += re.findall(r"\b\d{9,}\b", linea)
    # Buscar montos en colones
    montos = re.findall(r"\d[\d,.]{3,}\.\d{2}", texto)

    info = []
    if nums:
        info.append(f"N° entero: {nums[0]}")
    if montos:
        info.append(f"Monto: ₡{montos[0]}")

    return ValidacionResultado(
        ok=True, tipo="entero", path=path,
        original_bytes=original_bytes, final_bytes=original_bytes,
        mensaje=" | ".join(info) if info else "PDF entero OK (sin número extraíble)",
    )


def validar_zip_shape(path: Path) -> ValidacionResultado:
    """Valida un ZIP de shape: verifica .shp, .dbf, .shx y tamaño."""
    original_bytes = path.stat().st_size
    errores: list[str] = []

    if not zipfile.is_zipfile(str(path)):
        return ValidacionResultado(
            ok=False, tipo="shape", path=path,
            original_bytes=original_bytes, final_bytes=original_bytes,
            mensaje="archivo ZIP inválido",
        )

    with zipfile.ZipFile(str(path)) as zf:
        nombres = {n.lower().rsplit(".", 1)[-1] for n in zf.namelist()}

    for ext in ("shp", "dbf", "shx"):
        if ext not in nombres:
            errores.append(f"falta .{ext}")

    if original_bytes > _MAX_ZIP_BYTES:
        errores.append(f"supera {_MAX_ZIP_BYTES // 1024}KB ({original_bytes // 1024}KB)")

    return ValidacionResultado(
        ok=not errores, tipo="shape", path=path,
        original_bytes=original_bytes, final_bytes=original_bytes,
        mensaje="ZIP OK" if not errores else "; ".join(errores),
        errores=errores,
    )


def validar_archivo(path: Path, tipo: str) -> ValidacionResultado:
    """Dispatcher: delega al validador correcto según el tipo."""
    if tipo == "anverso":
        return validar_pdf_anverso(path)
    if tipo == "entero":
        return validar_pdf_entero(path)
    if tipo == "shape":
        return validar_zip_shape(path)
    # Para carta_agua, croquis, visado: validación básica
    original_bytes = path.stat().st_size
    try:
        import fitz
        fitz.open(str(path)).close()
        ok, msg = True, f"PDF {tipo} OK ({original_bytes // 1024}KB)"
    except Exception as e:
        ok, msg = False, f"PDF {tipo} inválido: {e}"
    except ImportError:
        ok, msg = True, f"PDF {tipo} aceptado ({original_bytes // 1024}KB)"
    return ValidacionResultado(
        ok=ok, tipo=tipo, path=path,
        original_bytes=original_bytes, final_bytes=original_bytes,
        mensaje=msg,
    )


# ── Formato de reporte WhatsApp ───────────────────────────────────────────────

_TIPO_EMOJI = {
    "anverso":    "📄",
    "entero":     "📄",
    "shape":      "📦",
    "carta_agua": "📄",
    "croquis":    "📄",
    "visado":     "📄",
    "desconocido": "❓",
}


def formatear_reporte(
    *,
    nombre_cliente: str,
    numero_expediente: str,
    fase: str,
    resultados: list[ValidacionResultado],
) -> str:
    """Genera el mensaje WhatsApp de reporte de archivos detectados."""
    fase_nombre = {
        "03_APT_R1":   "APT Ronda 1",
        "04_MUNICIPAL": "Municipalidad",
        "05_APT_R2":   "APT Ronda 2",
    }.get(fase, fase)

    lineas = [
        f"📁 Archivos detectados — {nombre_cliente} / {numero_expediente}",
        f"Fase: {fase_nombre}",
        "",
    ]

    todos_ok = True
    advertencias = []

    for r in resultados:
        emoji  = _TIPO_EMOJI.get(r.tipo, "📄")
        estado = "✅" if r.ok else "❌"
        linea  = f"{emoji} {r.path.name} — {r.final_kb}KB {estado}"
        if r.mensaje:
            linea += f"\n   {r.mensaje}"
        lineas.append(linea)
        if not r.ok:
            todos_ok = False
        if r.tenia_color:
            advertencias.append(
                f"⚠ Anverso tenía color — convertido a B&N "
                f"({r.original_kb}KB→{r.final_kb}KB)"
            )
        for err in r.errores:
            advertencias.append(f"⚠ {err}")

    if advertencias:
        lineas.append("")
        lineas.extend(advertencias)

    lineas.append("")
    if todos_ok:
        lineas.append("¿APROBAR para continuar? Respondé *APROBAR* o *RECHAZAR*")
    else:
        lineas.append(
            "❌ Hay errores — corrija los archivos y vuelva a copiar a SUBIR\\"
        )

    return "\n".join(lineas)


# ── Watchdog ──────────────────────────────────────────────────────────────────

class FileManager:
    """Vigila carpetas SUBIR\ y RESPUESTA\ en Mega con Watchdog.

    Parámetros:
        db             — instancia de Database
        folder_manager — instancia de FolderManager
        notify_fn      — callback(phone, message) para enviar WhatsApp
        mega_root      — ruta raíz de Mega (por defecto de settings)
        operador_phone — teléfono al que se envían los reportes
        delay_segundos — cuántos segundos esperar después de detectar archivo
    """

    def __init__(
        self,
        db,
        folder_manager,
        notify_fn: Callable[[str, str], None],
        *,
        mega_root: Path = MEGA_ROOT,
        operador_phone: str = "",
        delay_segundos: int = 30,
    ):
        self.db = db
        self.fm = folder_manager
        self.notify = notify_fn
        self._root = Path(mega_root)
        self._activos = self._root / "ACTIVOS"
        self._operador = operador_phone
        self._delay = delay_segundos
        self._observer = None
        self._lock = threading.Lock()

    def start(self) -> None:
        """Inicia el Observer watchdog. Idempotente."""
        if not self._activos.exists():
            _log.warning("carpeta ACTIVOS no existe: %s — creando", self._activos)
            self._activos.mkdir(parents=True, exist_ok=True)

        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler, FileCreatedEvent
        except ImportError:
            _log.error("watchdog no instalado — file_manager desactivado")
            return

        handler = _MegaEventHandler(file_manager=self)
        obs = Observer()
        obs.schedule(handler, str(self._activos), recursive=True)
        obs.start()
        with self._lock:
            self._observer = obs
        _log.info("watchdog iniciado en: %s", self._activos)

    def stop(self) -> None:
        with self._lock:
            obs = self._observer
            self._observer = None
        if obs and obs.is_alive():
            obs.stop()
            obs.join(timeout=5)
            _log.info("watchdog detenido")

    def is_running(self) -> bool:
        with self._lock:
            return self._observer is not None and self._observer.is_alive()

    # ── procesamiento interno ─────────────────────────────────────────────────

    def _procesar_subir(self, path: Path) -> None:
        """Llamado desde el handler cuando se detecta archivo en SUBIR\."""
        _log.info("archivo detectado en SUBIR: %s", path)
        time.sleep(self._delay)

        if not path.exists():
            _log.debug("archivo ya no existe (probablemente movido): %s", path)
            return

        # Identificar expediente
        exp = self._expediente_desde_path(path)
        if not exp:
            _log.warning("no se pudo identificar expediente para: %s", path)
            return

        # Fase (carpeta padre del SUBIR)
        fase_dir = path.parent.parent.name  # e.g. "03_APT_R1"

        # Recolectar todos los archivos en el mismo directorio (SUBIR\ o carpeta plano)
        subir_dir   = path.parent
        todos_pdfs  = sorted(subir_dir.glob("*.pdf"))
        todos_zips  = sorted(subir_dir.glob("*.zip"))
        archivos    = todos_pdfs + todos_zips

        if not archivos:
            return

        # Clasificar y validar
        resultados: list[ValidacionResultado] = []
        for f in archivos:
            # Ignorar archivos _bot.pdf (ya procesados)
            if f.stem.endswith("_bot"):
                continue
            if f.suffix.lower() == ".pdf":
                tipo = _clasificar_pdf(f, todos_pdfs)
            elif f.suffix.lower() == ".zip":
                # Exact name for ZIPs (shape.zip) else fallback to "shape"
                tipo = _EXACT_ZIP_NAMES.get(f.stem.lower(), "shape")
            else:
                tipo = "shape"
            resultado = validar_archivo(f, tipo)
            resultados.append(resultado)

        if not resultados:
            return

        # Registrar en BD
        for r in resultados:
            try:
                ruta = str(r.procesado_path or r.path)
                self.db.registrar_archivo(
                    expediente_id=exp["id"],
                    fase=fase_dir,
                    tipo_archivo=r.tipo,
                    nombre_original=r.path.name,
                    sha256=_sha256(r.procesado_path or r.path),
                    ruta_local=ruta,
                    tamano_bytes=r.final_bytes,
                    actor="file_manager",
                )
            except Exception:
                _log.exception("error registrando archivo %s en BD", r.path)

        # Auto-extraer número de entero del PDF entero.pdf (si existe)
        entero_numero: Optional[str] = None
        for r in resultados:
            if r.tipo == "entero":
                m = re.search(r"N°\s*entero:\s*(\d+)", r.mensaje)
                if m:
                    entero_numero = m.group(1)
                    try:
                        self.db.actualizar_metadata(
                            exp["id"],
                            {"numero_entero_detectado": entero_numero},
                            actor="file_manager",
                        )
                        _log.info(
                            "número de entero detectado para exp %s: %s",
                            exp["id"], entero_numero,
                        )
                    except Exception:
                        _log.exception("error guardando número de entero en metadata")
                break

        # Enviar reporte WhatsApp
        if self._operador:
            reporte = formatear_reporte(
                nombre_cliente=exp.get("nombre_cliente") or exp["numero_expediente"],
                numero_expediente=exp["numero_expediente"],
                fase=fase_dir,
                resultados=resultados,
            )
            # Añadir sugerencia del comando PAGAR si se detectó número de entero
            if entero_numero:
                numero_exp = exp["numero_expediente"]
                reporte += (
                    f"\n\n💡 *Número de entero detectado: {entero_numero}*\n"
                    f"Si el número es correcto, confirme con:\n"
                    f"  *PAGAR {numero_exp} {entero_numero}*"
                )
            try:
                self.notify(self._operador, reporte)
            except Exception:
                _log.exception("error enviando reporte WhatsApp")

    def _procesar_archivo_directo(self, path: Path) -> None:
        """Procesa un archivo colocado directamente en la carpeta del plano.

        Usado para la estructura plana (provincia/canton/.../01/) donde el
        topógrafo deposita los archivos directamente sin subcarpetas SUBIR/RESPUESTA.
        Clasifica por nombre exacto y registra en BD.
        """
        _log.info("archivo directo detectado: %s", path)
        time.sleep(self._delay)

        if not path.exists():
            _log.debug("archivo ya no existe: %s", path)
            return

        exp = self._expediente_desde_path(path)
        if not exp:
            _log.warning("no se pudo identificar expediente para: %s", path)
            return

        # Ignorar archivos generados por el bot (_bot.pdf o _muni.pdf)
        if path.stem.endswith("_bot") or path.stem.endswith("_muni"):
            return

        # Clasificar por nombre exacto
        nombre_lower = path.stem.lower()
        ext = path.suffix.lower()
        if ext == ".pdf":
            tipo = _EXACT_NAMES.get(nombre_lower) or _clasificar_pdf(path, [path])
        elif ext == ".zip":
            tipo = _EXACT_ZIP_NAMES.get(nombre_lower, "shape")
        elif ext in (".dxf", ".dwg"):
            tipo = "plano_cad"
        else:
            return  # ignorar otros tipos (EXPEDIENTE.txt, etc.)

        resultado = validar_archivo(path, tipo)

        # Registrar en BD (fase "campo" por defecto para archivos del topógrafo)
        try:
            self.db.registrar_archivo(
                expediente_id=exp["id"],
                fase="campo",
                tipo_archivo=tipo,
                nombre_original=path.name,
                sha256=_sha256(path),
                ruta_local=str(path),
                tamano_bytes=resultado.final_bytes,
                actor="file_manager.directo",
            )
        except Exception:
            _log.exception("error registrando archivo directo %s en BD", path)

        # Auto-extraer número de entero de entero.pdf
        entero_numero: Optional[str] = None
        if tipo == "entero":
            m = re.search(r"N°\s*entero:\s*(\d+)", resultado.mensaje)
            if m:
                entero_numero = m.group(1)
                try:
                    self.db.actualizar_metadata(
                        exp["id"],
                        {"numero_entero_detectado": entero_numero},
                        actor="file_manager",
                    )
                except Exception:
                    _log.exception("error guardando número de entero en metadata")

        # Notificar al operador
        if self._operador:
            emoji = _TIPO_EMOJI.get(tipo, "📄")
            ok_mark = "✅" if resultado.ok else "❌"
            numero_exp = exp["numero_expediente"]
            ident = display_proyecto(exp)
            kb = resultado.final_bytes // 1024
            msg = (
                f"{emoji} *{path.name}* — {ident}\n"
                f"{kb}KB {ok_mark}  {resultado.mensaje}"
            )
            if not resultado.ok and resultado.errores:
                msg += "\n" + "\n".join(f"  ⚠ {e}" for e in resultado.errores)
            if entero_numero:
                # PAGAR usa numero_expediente (no display) porque es lo que
                # el comando parsea — el comando se mantiene como antes.
                msg += (
                    f"\n\n💡 *Número de entero: {entero_numero}*\n"
                    f"Confirme con: *PAGAR {numero_exp} {entero_numero}*"
                )
            try:
                self.notify(self._operador, msg)
            except Exception:
                _log.exception("error notificando archivo directo")

    def _procesar_respuesta(self, path: Path) -> None:
        """Llamado cuando se detecta archivo en RESPUESTA\."""
        _log.info("respuesta detectada: %s", path)

        tipo_resp = _clasificar_respuesta(path.name)
        if not tipo_resp:
            _log.debug("respuesta no clasificada: %s", path.name)
            return

        exp = self._expediente_desde_path(path)
        if not exp:
            return

        fase_dir = path.parent.parent.name

        try:
            self.db.registrar_archivo(
                expediente_id=exp["id"],
                fase=fase_dir,
                tipo_archivo=tipo_resp,
                nombre_original=path.name,
                sha256=_sha256(path),
                ruta_local=str(path),
                tamano_bytes=path.stat().st_size,
                actor="file_manager.respuesta",
            )
        except Exception:
            _log.exception("error registrando respuesta %s", path)

        if self._operador:
            msg = (
                f"📨 Respuesta recibida — *{display_proyecto(exp)}*\n"
                f"Tipo: {tipo_resp}\n"
                f"Archivo: {path.name}\n"
                f"Fase: {fase_dir}\n\n"
                "El bot procesará la respuesta en el próximo ciclo."
            )
            try:
                self.notify(self._operador, msg)
            except Exception:
                _log.exception("error notificando respuesta")

    def _expediente_desde_path(self, path: Path) -> Optional[dict]:
        """Identifica el expediente a partir de la ruta del archivo."""
        # Subir a través de los padres hasta encontrar coincidencia en BD
        for parent in path.parents:
            mega_str = str(parent).replace("\\", "/")
            exp = self.db.buscar_por_mega_path(mega_str)
            if exp:
                return exp
        return None


# ── Handler watchdog ──────────────────────────────────────────────────────────

class _MegaEventHandler:
    """FileSystemEventHandler que delega al FileManager."""

    def __init__(self, file_manager: FileManager):
        self._fm = file_manager

    def dispatch(self, event) -> None:
        """Interfaz mínima compatible con watchdog Observer."""
        self.on_any_event(event)

    def on_any_event(self, event) -> None:
        if event.is_directory:
            return
        src = Path(getattr(event, "src_path", ""))
        event_type = getattr(event, "event_type", "")
        if event_type not in ("created", "moved"):
            return
        # Determinar si es SUBIR\, RESPUESTA\, o carpeta plana (estructura nueva)
        partes = [p.upper() for p in src.parts]
        if "SUBIR" in partes:
            threading.Thread(
                target=self._fm._procesar_subir,
                args=(src,),
                daemon=True,
            ).start()
        elif "RESPUESTA" in partes:
            threading.Thread(
                target=self._fm._procesar_respuesta,
                args=(src,),
                daemon=True,
            ).start()
        else:
            # Estructura plana: archivo depositado directamente en carpeta del plano.
            # Solo procesar PDFs, ZIPs y archivos CAD (ignorar .txt, .json, etc.)
            if src.suffix.lower() in (".pdf", ".zip", ".dxf", ".dwg"):
                threading.Thread(
                    target=self._fm._procesar_archivo_directo,
                    args=(src,),
                    daemon=True,
                ).start()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _sha256(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(64 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _clasificar_respuesta(nombre: str) -> Optional[str]:
    nombre_lower = nombre.lower()
    for tipo, patron in _RESP_PATTERNS:
        if patron.search(nombre_lower):
            return tipo
    return None
