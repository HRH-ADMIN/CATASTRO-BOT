"""Validador de archivos para catastro-bot — Opción D.

Validaciones implementadas:
  DWG/DXF  — estructura de capas mínimas exigidas por CFIA/Catastro Nacional.
  PDF      — apertura sin error, mínimo 1 página, presencia de firma digital.

Las validaciones "profundas" (DWG + firma PDF) requieren librerías opcionales:
  - ezdxf   (pip install ezdxf)      para DWG/DXF
  - pypdf   (pip install pypdf)      para PDF

Si las librerías no están disponibles el validador cae al modo básico
(existencia de archivo + extensión), igual que antes.

Uso desde base_workflow:
    from src.agents.file_validator import FileValidator
    ok, errores = FileValidator.validar(archivo_dict)
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from src.utils.logger import get_logger

_log = get_logger("file_validator")

# ── Capas requeridas CFIA para planos topográficos (Ley 6545) ─────────────────
# Fuente: Instructivo Técnico CFIA — Presentación de Planos en Medio Digital
# Las capas son case-insensitive en la verificación.
CAPAS_REQUERIDAS_DWG: frozenset[str] = frozenset({
    "LINDERO",         # línea de linderos (obligatoria en todos los tipos)
    "VIAS",            # vías de acceso
    "NORTE",           # indicador norte
    "ESCALA",          # barra de escala o indicador de escala
    "SELLO",           # sello profesional y cuadro de datos
    "COORDENADAS",     # puntos de coordenadas CRTM05 o CR05
})

# Capas opcionales que no bloquean la validación pero se registran como
# advertencia si se espera que estén (para segregacion y reunion_de_fincas).
CAPAS_RECOMENDADAS_DWG: frozenset[str] = frozenset({
    "CURVAS_NIVEL",    # curvas de nivel (útil pero no siempre obligatoria)
    "CONSTRUCCIONES",  # construcciones existentes
    "SERVIDUMBRE",     # servidumbres de paso
})

# ── Resultado de validación ────────────────────────────────────────────────────

class ValidationResult:
    """Resultado de una validación de archivo."""

    __slots__ = ("ok", "errores", "advertencias", "metadatos")

    def __init__(
        self,
        ok: bool,
        errores: Optional[list[str]] = None,
        advertencias: Optional[list[str]] = None,
        metadatos: Optional[dict] = None,
    ):
        self.ok          = ok
        self.errores     = errores or []
        self.advertencias = advertencias or []
        self.metadatos   = metadatos or {}

    def __bool__(self) -> bool:
        return self.ok

    def __repr__(self) -> str:
        return (
            f"ValidationResult(ok={self.ok}, errores={self.errores}, "
            f"advertencias={self.advertencias})"
        )


# ── Validador central ──────────────────────────────────────────────────────────

class FileValidator:
    """Valida archivos PDF y DWG/DXF para planos topográficos."""

    @staticmethod
    def validar(archivo: dict) -> ValidationResult:
        """Punto de entrada principal.

        `archivo` es el dict que devuelve `db.archivos_de()`:
          {"ruta_local": "...", "nombre_original": "...", ...}

        Devuelve ValidationResult con ok=True si el archivo es aceptable.
        """
        ruta_raw = archivo.get("ruta_local") or ""
        ruta = Path(ruta_raw)

        if not ruta_raw:
            return ValidationResult(False, ["ruta_local vacía"])
        if not ruta.is_file():
            return ValidationResult(False, [f"archivo no encontrado: {ruta}"])

        ext = ruta.suffix.lower()
        if ext in (".dwg", ".dxf"):
            return FileValidator._validar_dwg(ruta)
        if ext == ".pdf":
            return FileValidator._validar_pdf(ruta)
        if ext == ".zip":
            return FileValidator._validar_zip_shape(ruta)

        return ValidationResult(
            False,
            [f"extensión no soportada: {ext!r} (se esperaba .pdf, .dwg, .dxf o .zip)"],
        )

    # ── DWG / DXF ─────────────────────────────────────────────────────────────

    @staticmethod
    def _validar_dwg(ruta: Path) -> ValidationResult:
        errores: list[str] = []
        advertencias: list[str] = []
        meta: dict = {}

        try:
            import ezdxf  # noqa: PLC0415
        except ImportError:
            _log.warning("ezdxf no instalado — validación DWG básica (solo existencia)")
            return ValidationResult(True, advertencias=["ezdxf no disponible — validación básica"])

        try:
            doc = ezdxf.readfile(str(ruta))
        except Exception as exc:
            return ValidationResult(False, [f"DWG/DXF no se puede leer: {exc}"])

        # Capas presentes (normalizadas a mayúsculas)
        capas_presentes: set[str] = {
            layer.dxf.name.upper() for layer in doc.layers
        }
        meta["capas"] = sorted(capas_presentes)
        meta["version_dxf"] = doc.dxfversion

        # Verificar capas requeridas
        faltantes = CAPAS_REQUERIDAS_DWG - capas_presentes
        if faltantes:
            errores.append(
                f"capas CFIA faltantes: {', '.join(sorted(faltantes))}"
            )

        # Advertencias por capas recomendadas
        recomendadas_faltantes = CAPAS_RECOMENDADAS_DWG - capas_presentes
        if recomendadas_faltantes:
            advertencias.append(
                f"capas opcionales no presentes: "
                f"{', '.join(sorted(recomendadas_faltantes))}"
            )

        # Verificar que hay entidades en el modelspace
        msp = doc.modelspace()
        num_entidades = len(list(msp))
        meta["entidades_modelspace"] = num_entidades
        if num_entidades == 0:
            errores.append("modelspace vacío — el plano no tiene entidades")

        ok = len(errores) == 0
        _log.info(
            "DWG %s — ok=%s | capas=%d | entidades=%d | errores=%s",
            ruta.name, ok, len(capas_presentes), num_entidades, errores or "ninguno",
        )
        return ValidationResult(ok, errores, advertencias, meta)

    # ── ZIP shape ─────────────────────────────────────────────────────────────

    @staticmethod
    def _validar_zip_shape(ruta: Path) -> ValidationResult:
        """Valida un archivo .zip que contiene el shape (SHP/SHX/DBF) del plano.

        CFIA/Catastro Nacional acepta el shape en formato .zip.
        Validación: que sea un zip válido y no vacío.
        """
        import zipfile
        errores: list[str] = []
        advertencias: list[str] = []
        meta: dict = {}

        try:
            with zipfile.ZipFile(ruta, "r") as zf:
                nombres = zf.namelist()
                meta["archivos_dentro"] = nombres
                meta["cantidad"] = len(nombres)
                if not nombres:
                    errores.append("ZIP vacío — no contiene archivos")
                else:
                    # Verificar que al menos hay un .shp o que hay archivos reconocibles
                    exts_dentro = {Path(n).suffix.lower() for n in nombres}
                    tiene_shape = bool(exts_dentro & {".shp", ".shx", ".dbf", ".prj"})
                    if not tiene_shape:
                        # Puede ser shape de otro formato o exportación CFIA — solo advertir
                        advertencias.append(
                            f"ZIP no contiene .shp/.shx/.dbf reconocibles "
                            f"(archivos: {', '.join(Path(n).name for n in nombres[:5])})"
                        )
        except zipfile.BadZipFile as exc:
            return ValidationResult(False, [f"ZIP inválido o corrupto: {exc}"])
        except Exception as exc:
            return ValidationResult(False, [f"error leyendo ZIP: {exc}"])

        ok = len(errores) == 0
        _log.info(
            "ZIP shape %s — ok=%s | archivos=%d",
            ruta.name, ok, meta.get("cantidad", 0),
        )
        return ValidationResult(ok, errores, advertencias, meta)

    # ── PDF ───────────────────────────────────────────────────────────────────

    @staticmethod
    def _validar_pdf(ruta: Path) -> ValidationResult:
        errores: list[str] = []
        advertencias: list[str] = []
        meta: dict = {}

        try:
            from pypdf import PdfReader  # noqa: PLC0415
        except ImportError:
            _log.warning("pypdf no instalado — validación PDF básica (solo existencia)")
            return ValidationResult(True, advertencias=["pypdf no disponible — validación básica"])

        try:
            reader = PdfReader(str(ruta))
        except Exception as exc:
            return ValidationResult(False, [f"PDF no se puede abrir: {exc}"])

        # Páginas
        num_paginas = len(reader.pages)
        meta["paginas"] = num_paginas
        if num_paginas == 0:
            errores.append("PDF sin páginas")

        # Firma digital — busca campo /Sig en el catálogo del documento
        firma_encontrada = _detectar_firma_pdf(reader)
        meta["tiene_firma_digital"] = firma_encontrada
        nombre_lower = ruta.name.lower()
        es_entero = "entero" in nombre_lower or "comprobante" in nombre_lower or "bcr" in nombre_lower
        if not firma_encontrada and not es_entero:
            # Solo advertir para planos (no para comprobantes de pago BCR)
            advertencias.append(
                "PDF sin firma digital detectable "
                "(requerida para planos CFIA según Art. 17 Reglamento Digital)"
            )

        # Verificar que no está cifrado (si está cifrado no podemos leerlo)
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                errores.append("PDF cifrado con contraseña — no se puede procesar")

        ok = len(errores) == 0
        _log.info(
            "PDF %s — ok=%s | páginas=%d | firma=%s",
            ruta.name, ok, num_paginas, firma_encontrada,
        )
        return ValidationResult(ok, errores, advertencias, meta)


# ── helpers ────────────────────────────────────────────────────────────────────

def _detectar_firma_pdf(reader) -> bool:
    """Detecta la presencia de firma digital en un PDF.

    Estrategias (en orden):
    1. Campos de firma en el formulario AcroForm (/Sig)
    2. Extensión DocMDP (/DocMDP en /Perms del catálogo)
    3. Búsqueda de cadena "/Type /Sig" en las páginas
    """
    try:
        # Estrategia 1: AcroForm con campos /Sig
        trailer = reader.trailer
        if "/Root" in trailer:
            root = trailer["/Root"]
            if "/AcroForm" in root:
                acroform = root["/AcroForm"]
                fields = acroform.get("/Fields", [])
                for field_ref in fields:
                    try:
                        field = field_ref.get_object()
                        if field.get("/FT") == "/Sig":
                            return True
                    except Exception:
                        continue

            # Estrategia 2: DocMDP en /Perms
            if "/Perms" in root:
                perms = root["/Perms"]
                if "/DocMDP" in perms:
                    return True

        # Estrategia 3: búsqueda en el stream del PDF (fallback)
        with open(reader.stream.name if hasattr(reader.stream, "name") else "", "rb") as f:
            content = f.read(min(1_000_000, 5 * 1024 * 1024))
            if b"/Type /Sig" in content or b"/Type/Sig" in content:
                return True

    except Exception:
        pass

    return False


# ── Función de conveniencia para base_workflow ─────────────────────────────────

def validar_archivos(archivos: list[dict]) -> tuple[bool, list[str]]:
    """Valida una lista de archivos (dicts de DB).

    Devuelve (todo_ok, lista_de_errores).
    """
    todos_ok = True
    todos_errores: list[str] = []

    for a in archivos:
        resultado = FileValidator.validar(a)
        if not resultado.ok:
            todos_ok = False
            nombre = a.get("nombre_original", str(a.get("ruta_local", "?")))
            for e in resultado.errores:
                todos_errores.append(f"{nombre}: {e}")
        # Loguear advertencias pero no bloquear
        for w in resultado.advertencias:
            _log.warning("advertencia archivo %s: %s",
                         a.get("nombre_original", "?"), w)

    return todos_ok, todos_errores
