"""Gestión de estructura de carpetas en Mega para catastro-bot.

Soporta dos estructuras:

  Estructura nueva (v2) — usada cuando el expediente tiene campos de ubicación:
    ACTIVOS\{Provincia}\{Canton}\{Distrito}\{NombreDueño}\{Proyecto}\{Año}\{Num}\
    Todos los archivos van directamente en {Num}\ (sin subcarpetas).
    El número {Num} es secuencial por proyecto: 01, 02, 03, …

  Estructura legado (v1) — usada cuando no hay campos de ubicación:
    ACTIVOS\{APELLIDO}_{NOMBRE}\{NUMERO_EXPEDIENTE}_{TIPO}\
    Con subcarpetas 01_CAMPO, 03_APT_R1\SUBIR, etc.

Regla de seguridad: esta clase NUNCA llama a APIs externas ni modifica la BD
sin ser explícitamente instruida. Solo crea carpetas y escribe archivos locales.
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config.settings import ACTIVOS_DIR, COMPLETADOS_DIR, MEGA_ROOT
from src.core.exceptions import AgentError
from src.models.plano import TipoPlano
from src.utils.logger import get_logger

# Tipos que requieren Municipalidad (paso 4) — Ley 6545
_TIPOS_CON_MUNI = {TipoPlano.SEGREGACION.value, TipoPlano.REUNION_DE_FINCAS.value}

# Tipos que NO requieren segunda ronda APT (paso 5)
_TIPOS_SIN_APT_R2 = {TipoPlano.RECTIFICACION.value, TipoPlano.INFORMACION_POSESORIA.value}

# Nombre legible del tipo de plano
_TIPO_NOMBRE_ES = {
    TipoPlano.SEGREGACION.value:           "Segregación",
    TipoPlano.RECTIFICACION.value:         "Rectificación",
    TipoPlano.INFORMACION_POSESORIA.value: "Información Posesoria",
    TipoPlano.REUNION_DE_FINCAS.value:     "Reunión de Fincas",
    TipoPlano.FINCAS_COMPLETAS.value:      "Fincas Completas",
}

# Fases con watchdog (tienen SUBIR\ y RESPUESTA\)
_FASES_CON_WATCHDOG = {
    "apt_r1":    "03_APT_R1",
    "municipal": "04_MUNICIPAL",
    "apt_r2":    "05_APT_R2",
}


def _ascii_safe(texto: str) -> str:
    """Convierte a mayúsculas, quita tildes y reemplaza espacios por _."""
    sin_tildes = unicodedata.normalize("NFD", texto)
    sin_tildes = "".join(c for c in sin_tildes if unicodedata.category(c) != "Mn")
    resultado = re.sub(r"[^\w\s]", "", sin_tildes.upper())
    return re.sub(r"\s+", "_", resultado.strip())


def _carpeta_cliente(nombre_topografo: str) -> str:
    """
    De 'Luis Alonso Rojas Herrera' → 'ROJAS_LUIS'
    Toma último apellido + primer nombre.
    """
    partes = nombre_topografo.strip().split()
    if len(partes) >= 4:
        # nombre + apellido1 + apellido2 (última forma)
        # Heurística: último token + primer token
        return _ascii_safe(f"{partes[-1]}_{partes[0]}")
    elif len(partes) == 2:
        return _ascii_safe(f"{partes[1]}_{partes[0]}")
    return _ascii_safe(nombre_topografo)[:30]


def _carpeta_proyecto(numero_expediente: str, tipo_plano: str) -> str:
    """De '12345-2026' + 'segregacion' → '12345-2026_SEGREGACION'."""
    return f"{numero_expediente}_{_ascii_safe(tipo_plano)}"


class FolderManager:
    """Crea y mantiene la estructura de carpetas Mega de cada expediente."""

    def __init__(self, db, *, mega_root: Path = MEGA_ROOT):
        self.db = db
        self._root = Path(mega_root)
        self._activos = self._root / "ACTIVOS"
        self._completados = self._root / "COMPLETADOS"
        self._log = get_logger("folder_manager")

    # ── creación ─────────────────────────────────────────────────────────────

    def crear_estructura_expediente(self, expediente_id: str) -> Path:
        """Crea la estructura de carpetas para el expediente.

        Detecta automáticamente si usar la estructura nueva (v2) o legado (v1):
          - v2: metadata tiene 'provincia' → carpeta plana única sin subfases
          - v1: sin provincia → estructura con 01_CAMPO, 03_APT_R1/SUBIR, etc.

        Retorna el Path de la carpeta raíz del expediente.
        Idempotente: si la carpeta ya existe no falla.
        """
        exp = self.db.obtener_expediente(expediente_id)
        if not exp:
            raise AgentError(f"expediente {expediente_id!r} no encontrado")

        meta = json.loads(exp.get("metadata_json") or "{}")

        if meta.get("provincia"):
            # ── Estructura v2 (nueva): carpeta plana ─────────────────────────
            raiz = self._raiz_expediente_v2(exp, meta)
            raiz.mkdir(parents=True, exist_ok=True)
        else:
            # ── Estructura v1 (legado): fases con SUBIR/RESPUESTA ────────────
            tipo = exp["tipo_plano"]
            raiz = self._raiz_expediente(exp)

            for sub in ("01_CAMPO", "02_DISENO", "06_INSCRITO"):
                (raiz / sub).mkdir(parents=True, exist_ok=True)
            for sub in ("SUBIR", "RESPUESTA"):
                (raiz / "03_APT_R1" / sub).mkdir(parents=True, exist_ok=True)
            if tipo in _TIPOS_CON_MUNI:
                for sub in ("SUBIR", "RESPUESTA"):
                    (raiz / "04_MUNICIPAL" / sub).mkdir(parents=True, exist_ok=True)
            if tipo not in _TIPOS_SIN_APT_R2:
                for sub in ("SUBIR", "RESPUESTA"):
                    (raiz / "05_APT_R2" / sub).mkdir(parents=True, exist_ok=True)

        # Guardar ruta en BD
        mega_path = str(raiz).replace("\\", "/")
        self.db.set_mega_path(expediente_id, mega_path)

        # EXPEDIENTE.txt
        self._escribir_expediente_txt(raiz, exp)

        self._log.info("estructura creada: %s", raiz)
        return raiz

    # ── actualización ─────────────────────────────────────────────────────────

    def actualizar_expediente_txt(self, expediente_id: str) -> None:
        """Regenera EXPEDIENTE.txt con el estado actual del expediente."""
        exp = self.db.obtener_expediente(expediente_id)
        if not exp:
            raise AgentError(f"expediente {expediente_id!r} no encontrado")
        mega_path = self.db.get_mega_path(expediente_id)
        if not mega_path:
            raise AgentError(
                f"expediente {expediente_id!r} no tiene ruta Mega — "
                "¿se llamó crear_estructura_expediente()?"
            )
        raiz = Path(mega_path)
        if raiz.exists():
            self._escribir_expediente_txt(raiz, exp)

    # ── completar ─────────────────────────────────────────────────────────────

    def completar_expediente(self, expediente_id: str) -> Path:
        """Mueve la carpeta del expediente de ACTIVOS\ a COMPLETADOS\{año}\.

        Retorna la ruta destino.
        """
        exp = self.db.obtener_expediente(expediente_id)
        if not exp:
            raise AgentError(f"expediente {expediente_id!r} no encontrado")

        mega_path = self.db.get_mega_path(expediente_id)
        if not mega_path:
            raise AgentError(
                f"expediente {expediente_id!r} sin ruta Mega asignada"
            )

        origen = Path(mega_path)
        if not origen.exists():
            raise AgentError(f"carpeta no encontrada: {origen}")

        anio = datetime.now(timezone.utc).year
        destino_dir = self._completados / str(anio)
        destino_dir.mkdir(parents=True, exist_ok=True)
        destino = destino_dir / origen.name

        if destino.exists():
            # Renombrar con sufijo si ya existe
            destino = destino_dir / f"{origen.name}_completado"

        origen.rename(destino)

        # Actualizar ruta en BD
        nueva_ruta = str(destino).replace("\\", "/")
        self.db.set_mega_path(expediente_id, nueva_ruta)
        self._log.info("expediente completado: %s → %s", origen, destino)
        return destino

    # ── utilidades ────────────────────────────────────────────────────────────

    def get_subir_folder(self, expediente_id: str, fase: str) -> Path:
        """Retorna la ruta donde el topógrafo deposita archivos para la fase.

        - Estructura v2 (plana): devuelve la carpeta raíz del plano directamente.
        - Estructura v1 (legado): devuelve la subcarpeta SUBIR\ de la fase.

        fase: 'apt_r1', 'municipal', 'apt_r2'
        """
        mega_path = self.db.get_mega_path(expediente_id)
        if not mega_path:
            raise AgentError(
                f"expediente {expediente_id!r} sin ruta Mega — "
                "llame crear_estructura_expediente() primero"
            )
        raiz = Path(mega_path)
        # v2: la carpeta raíz ya es la carpeta del plano
        if self._es_estructura_v2(expediente_id):
            return raiz
        # v1: subcarpeta de fase
        if fase not in _FASES_CON_WATCHDOG:
            raise AgentError(
                f"fase {fase!r} no tiene carpeta SUBIR. "
                f"Válidas: {sorted(_FASES_CON_WATCHDOG)}"
            )
        return raiz / _FASES_CON_WATCHDOG[fase] / "SUBIR"

    def get_respuesta_folder(self, expediente_id: str, fase: str) -> Path:
        """Retorna la ruta donde el bot deposita archivos de respuesta.

        - Estructura v2 (plana): devuelve la carpeta raíz del plano.
        - Estructura v1 (legado): devuelve la subcarpeta RESPUESTA\ de la fase.
        """
        mega_path = self.db.get_mega_path(expediente_id)
        if not mega_path:
            raise AgentError(
                f"expediente {expediente_id!r} sin ruta Mega"
            )
        raiz = Path(mega_path)
        if self._es_estructura_v2(expediente_id):
            return raiz
        if fase not in _FASES_CON_WATCHDOG:
            raise AgentError(
                f"fase {fase!r} no tiene carpeta RESPUESTA. "
                f"Válidas: {sorted(_FASES_CON_WATCHDOG)}"
            )
        return raiz / _FASES_CON_WATCHDOG[fase] / "RESPUESTA"

    def listar_activos(self) -> list[dict]:
        """Devuelve info básica de cada subcarpeta en ACTIVOS\."""
        result = []
        if not self._activos.exists():
            return result
        for p in sorted(self._activos.rglob("EXPEDIENTE.txt")):
            try:
                texto = p.read_text(encoding="utf-8", errors="replace")
                result.append({"path": str(p.parent), "expediente_txt": texto[:200]})
            except Exception:
                pass
        return result

    # ── privados ──────────────────────────────────────────────────────────────

    def _es_estructura_v2(self, expediente_id: str) -> bool:
        """True si el expediente usa estructura v2 (plana por ubicación)."""
        exp = self.db.obtener_expediente(expediente_id)
        if not exp:
            return False
        meta = json.loads(exp.get("metadata_json") or "{}")
        return bool(meta.get("provincia"))

    def _raiz_expediente(self, exp: dict) -> Path:
        """Estructura v1 (legado): ACTIVOS/{APELLIDO}_{NOMBRE}/{NUMERO}_{TIPO}/"""
        cliente_dir  = _carpeta_cliente(exp["nombre_topografo"])
        proyecto_dir = _carpeta_proyecto(exp["numero_expediente"], exp["tipo_plano"])
        return self._activos / cliente_dir / proyecto_dir

    def _raiz_expediente_v2(self, exp: dict, meta: dict) -> Path:
        """Estructura v2 (nueva): ACTIVOS/Provincia/Canton/Distrito/Dueño/Proyecto/Año/Num/

        Los valores se sacan de metadata_json:
          provincia, canton, distrito, nombre_dueno (o nombre_cliente),
          proyecto, anio, numero_plano (default '01')
        """
        provincia   = _ascii_safe(meta.get("provincia", "SIN_PROVINCIA"))
        canton      = _ascii_safe(meta.get("canton", "SIN_CANTON"))
        distrito    = _ascii_safe(meta.get("distrito", "SIN_DISTRITO"))
        dueno       = _ascii_safe(
            meta.get("nombre_dueno") or
            exp.get("nombre_cliente") or
            "SIN_DUENO"
        )
        proyecto    = _ascii_safe(meta.get("proyecto") or exp["numero_expediente"])
        anio        = str(meta.get("anio") or datetime.now(timezone.utc).year)
        num_plano   = str(meta.get("numero_plano", "01")).zfill(2)

        return self._activos / provincia / canton / distrito / dueno / proyecto / anio / num_plano

    def _escribir_expediente_txt(self, raiz: Path, exp: dict) -> None:
        meta   = json.loads(exp.get("metadata_json") or "{}")
        tipo   = _TIPO_NOMBRE_ES.get(exp["tipo_plano"], exp["tipo_plano"])
        fecha  = exp.get("fecha_creacion", "")[:10]
        estado = exp.get("estado_actual", "")

        lineas = [
            f"Expediente:    {exp['numero_expediente']}",
            f"Tipo:          {tipo}",
            f"Cliente:       {exp.get('nombre_cliente') or '(sin nombre)'}",
            f"Teléfono:      {exp.get('telefono_cliente', '')}",
            f"Cédula top.:   {exp.get('cedula_topografo') or '(pendiente)'}",
            f"Protocolo APT: {meta.get('protocolo') or '(pendiente)'}",
            f"Finca:         {meta.get('finca') or '(pendiente)'}",
            f"Creado:        {fecha}",
            f"Estado actual: {estado}",
            f"Topógrafo:     {exp.get('nombre_topografo', '')}",
            f"Municipalidad: {exp.get('municipalidad', 'San Ramón')}",
        ]
        # Agregar ubicación si está disponible (estructura v2)
        if meta.get("provincia"):
            lineas += [
                f"Provincia:     {meta.get('provincia', '')}",
                f"Cantón:        {meta.get('canton', '')}",
                f"Distrito:      {meta.get('distrito', '')}",
                f"Dueño proy.:   {meta.get('nombre_dueno') or exp.get('nombre_cliente', '')}",
                f"Proyecto:      {meta.get('proyecto', '')}",
                f"Plano N°:      {meta.get('numero_plano', '01')}",
            ]
        txt = "\n".join(lineas) + "\n"
        (raiz / "EXPEDIENTE.txt").write_text(txt, encoding="utf-8")
