"""Router de comandos WhatsApp para usuarios autorizados.

Comandos (17 en total):
  NUEVO              crear expediente (multi-línea key:value)
  NUEVO CONTRATO     crear expediente desde contrato (formato alternativo)
  ESTADO <numero>    estado actual + últimos cambios
  ESTADO APT <num>   consultar estado en portal APT
  APROBAR <numero>   desbloquear expediente en estado halt
  RECHAZAR <numero>  cancelar expediente
  BUSCAR <texto>     buscar por número, topógrafo o cliente
  SUBIR <numero>     confirmar que archivos están listos para procesar
  RECIBIR <numero>   marcar que cliente recibió el plano inscrito
  RESUMEN            expedientes activos del día
  CERRAR APT         cerrar sesión Playwright APT (libera token BCR)
  AVISAR <num> <msg> enviar mensaje al cliente del expediente
  AUTORIZAR DRIVE    iniciar flujo OAuth Google Drive en el servidor
  PAGAR <num> <entero>  registrar número de entero BCR (pago hecho en banco)
  CORREGIR <num> <campo> <viejo> <nuevo>  corregir texto en el DWG del expediente
  AYUDA / HELP       listado de comandos y permisos por rol

Sistema de roles:
  admin      → todos los comandos
  topografo  → NUEVO, ESTADO, APROBAR, RECHAZAR, RESUMEN, BUSCAR,
               SUBIR, RECIBIR, ESTADO APT, AVISAR
  asistente  → NUEVO, ESTADO, RESUMEN, BUSCAR, RECIBIR, AYUDA

Autorización:
  1. Busca el número en la tabla `usuarios` de la BD (rol explícito).
  2. Fallback: si `credentials.is_operator()` es True, trata como admin
     (compatibilidad con la Semana 1 antes del sistema de roles).
  3. Si ninguno aplica: rechaza con "Número no autorizado".
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Callable, Optional

from src.agents.drive_agent import FASES
from src.core.exceptions import DatabaseError
from src.models.estado import Estado
from src.models.plano import TipoPlano
from src.utils.display import display_proyecto
from src.utils.logger import get_logger
from src.utils.sender_rate_limiter import SenderRateLimiter
from src.utils.validators import normalizar_telefono_cr

# ── Verbos reconocidos (primera palabra del mensaje) ──────────────────────────
_COMMANDS = {
    "NUEVO", "ESTADO", "APROBAR", "RECHAZAR", "BUSCAR",
    "SUBIR", "RECIBIR", "RESUMEN", "CERRAR", "AVISAR",
    "AUTORIZAR", "PAGAR", "CORREGIR", "APT", "DEBUG",
    "LOTE",
    "AYUDA", "HELP",
}

# ── Permisos por rol ──────────────────────────────────────────────────────────
_ROL_PERMISOS: dict[str, set[str]] = {
    "admin": {
        "NUEVO", "ESTADO", "APROBAR", "RECHAZAR", "BUSCAR",
        "SUBIR", "RECIBIR", "RESUMEN", "CERRAR", "AVISAR",
        "AUTORIZAR", "PAGAR", "CORREGIR", "APT", "DEBUG",
        "LOTE",
        "AYUDA", "HELP",
    },
    "topografo": {
        "NUEVO", "ESTADO", "APROBAR", "RECHAZAR", "BUSCAR",
        "SUBIR", "RECIBIR", "RESUMEN", "AVISAR", "CORREGIR", "APT", "DEBUG",
        "LOTE",
        "AYUDA", "HELP",
    },
    "asistente": {
        "NUEVO", "ESTADO", "RESUMEN", "BUSCAR", "RECIBIR", "DEBUG",
        "AYUDA", "HELP",
    },
}

# ── Transiciones halt → siguiente estado ──────────────────────────────────────
_HALT_TRANSITIONS = {
    Estado.CARTA_AGUA_REQUERIDA.value: Estado.CARTA_AGUA_OK.value,
    Estado.APT_CORRECCIONES.value:     Estado.ENTEROS_PAGADOS.value,
    Estado.APT_TRASLAPES.value:        Estado.ENTEROS_PAGADOS.value,
    Estado.FORMATO_INVALIDO.value:     Estado.RECIBIDO.value,
    Estado.VISADO_RECHAZADO.value:     Estado.LISTO_PAQUETE_MUNI.value,
}

# ── Alias de tipo de plano ────────────────────────────────────────────────────
_TIPO_ALIASES = {
    # Segregación
    "segregacion":                  TipoPlano.SEGREGACION.value,
    "segregación":                  TipoPlano.SEGREGACION.value,
    # Rectificación
    "rectificacion":                TipoPlano.RECTIFICACION.value,
    "rectificación":                TipoPlano.RECTIFICACION.value,
    "rectificacion_medida":         TipoPlano.RECTIFICACION.value,   # alias legado
    "rectificación de medida":      TipoPlano.RECTIFICACION.value,
    "rectificacion de medida":      TipoPlano.RECTIFICACION.value,
    # Información Posesoria
    "informacion_posesoria":        TipoPlano.INFORMACION_POSESORIA.value,
    "información posesoria":        TipoPlano.INFORMACION_POSESORIA.value,
    "informacion posesoria":        TipoPlano.INFORMACION_POSESORIA.value,
    "posesoria":                    TipoPlano.INFORMACION_POSESORIA.value,
    "derecho de posesion":          TipoPlano.INFORMACION_POSESORIA.value,
    "derecho de posesión":          TipoPlano.INFORMACION_POSESORIA.value,
    # Reunión de Fincas
    "reunion_de_fincas":            TipoPlano.REUNION_DE_FINCAS.value,
    "reunion de fincas":            TipoPlano.REUNION_DE_FINCAS.value,
    "reunión de fincas":            TipoPlano.REUNION_DE_FINCAS.value,
    "reunion":                      TipoPlano.REUNION_DE_FINCAS.value,
    "division":                     TipoPlano.REUNION_DE_FINCAS.value,  # alias legado
    "división":                     TipoPlano.REUNION_DE_FINCAS.value,  # alias legado
    # Fincas Completas
    "fincas_completas":             TipoPlano.FINCAS_COMPLETAS.value,
    "fincas completas":             TipoPlano.FINCAS_COMPLETAS.value,
    "finca_completa":               TipoPlano.FINCAS_COMPLETAS.value,   # alias legado
    "finca completa":               TipoPlano.FINCAS_COMPLETAS.value,   # alias legado
    "finca":                        TipoPlano.FINCAS_COMPLETAS.value,
    "situacion":                    TipoPlano.INFORMACION_POSESORIA.value,  # alias legado
    "situación":                    TipoPlano.INFORMACION_POSESORIA.value,  # alias legado
}

_BOOL_TRUE  = {"si", "sí", "yes", "y", "true", "1"}
_BOOL_FALSE = {"no", "n", "false", "0"}

# Aliases para zona del Plan Regulador de San Ramón 2004 (ProDUS-UCR).
# Permite que el operador escriba nombres cortos en el comando NUEVO.
_ZONA_REG_ALIASES: dict[str, str] = {
    # Zonas ciudad San Ramón y periferias
    "sr_residencial":            "SR_RESIDENCIAL",
    "residencial":               "SR_RESIDENCIAL",
    "sr_mixta":                  "SR_MIXTA",
    "mixta":                     "SR_MIXTA",
    "sr_comercial":              "SR_COMERCIAL",
    "comercial":                 "SR_COMERCIAL",
    "sr_institucional":          "SR_INSTITUCIONAL",
    "institucional":             "SR_INSTITUCIONAL",
    "sr_periferia_urbana":       "SR_PERIFERIA_URBANA",
    "periferia":                 "SR_PERIFERIA_URBANA",
    "periferia urbana":          "SR_PERIFERIA_URBANA",
    "sr_comercial_industrial":   "SR_COMERCIAL_INDUSTRIAL",
    "comercial industrial":      "SR_COMERCIAL_INDUSTRIAL",
    "sr_industrial":             "SR_INDUSTRIAL",
    "industrial":                "SR_INDUSTRIAL",
    # Zonas resto cantón
    "sr_nucleo_consolidado":     "SR_NUCLEO_CONSOLIDADO",
    "nucleo consolidado":        "SR_NUCLEO_CONSOLIDADO",
    "consolidado":               "SR_NUCLEO_CONSOLIDADO",
    "sr_crecimiento_largo_plazo": "SR_CRECIMIENTO_LARGO_PLAZO",
    "largo plazo":               "SR_CRECIMIENTO_LARGO_PLAZO",
    "crecimiento":               "SR_CRECIMIENTO_LARGO_PLAZO",
    "sr_nucleo_no_consolidado":  "SR_NUCLEO_NO_CONSOLIDADO",
    "no consolidado":            "SR_NUCLEO_NO_CONSOLIDADO",
    "nucleo no consolidado":     "SR_NUCLEO_NO_CONSOLIDADO",
    # Amortiguamiento
    "sr_amortiguamiento_ciudad": "SR_AMORTIGUAMIENTO_CIUDAD",
    "amortiguamiento":           "SR_AMORTIGUAMIENTO_CIUDAD",
    "amortiguamiento ciudad":    "SR_AMORTIGUAMIENTO_CIUDAD",
    "sr_amortiguamiento_nucleo": "SR_AMORTIGUAMIENTO_NUCLEO",
    "amortiguamiento nucleo":    "SR_AMORTIGUAMIENTO_NUCLEO",
    "amortiguamiento núcleo":    "SR_AMORTIGUAMIENTO_NUCLEO",
    # Agropecuario y restricciones
    "sr_agropecuario":           "SR_AGROPECUARIO",
    "agropecuario":              "SR_AGROPECUARIO",
    "sr_restriccion_pecuaria":   "SR_RESTRICCION_PECUARIA",
    "restriccion pecuaria":      "SR_RESTRICCION_PECUARIA",
    "restricción pecuaria":      "SR_RESTRICCION_PECUARIA",
    "pecuaria":                  "SR_RESTRICCION_PECUARIA",
    # Protección
    "sr_proteccion_recursos":    "SR_PROTECCION_RECURSOS",
    "proteccion recursos":       "SR_PROTECCION_RECURSOS",
    "protección recursos":       "SR_PROTECCION_RECURSOS",
    "proteccion":                "SR_PROTECCION_RECURSOS",
    "recursos naturales":        "SR_PROTECCION_RECURSOS",
    # Carreteras
    "sr_carretera_interamericana": "SR_CARRETERA_INTERAMERICANA",
    "interamericana":            "SR_CARRETERA_INTERAMERICANA",
    "sr_carretera_cambronero":   "SR_CARRETERA_CAMBRONERO",
    "cambronero":                "SR_CARRETERA_CAMBRONERO",
}

_PHONE_RE = re.compile(r"\D")


def _digits(phone: str) -> str:
    return _PHONE_RE.sub("", phone or "")



def _is_command(text: str) -> bool:
    if not text:
        return False
    first = text.strip().split(maxsplit=1)
    return bool(first) and first[0].upper() in _COMMANDS


def _command_verb(text: str) -> str:
    return text.strip().split(maxsplit=1)[0].upper()


class WhatsAppCommandRouter:
    """Procesa comandos de usuarios autorizados.

    No envía mensajes directamente — usa el callback `reply_fn(phone, msg)`.
    """

    def __init__(
        self,
        *,
        db,
        credentials,
        drive_agent,
        reply_fn: Callable[[str, str], None],
        apt_agent=None,
        folder_manager=None,
        rnp_agent=None,
        sender_rate_limiter: Optional[SenderRateLimiter] = None,
    ):
        self.db = db
        self.credentials = credentials
        self.drive = drive_agent
        self.reply = reply_fn
        self.apt = apt_agent          # puede ser None si aún no está disponible
        self.folder_manager = folder_manager  # FolderManager para NUEVO CONTRATO
        self.rnp = rnp_agent          # puede ser None si RNP no está configurado
        self.log = get_logger("whatsapp_commands")
        # Rate-limit por sender para frenar spam/brute-force.
        # Default conservador: 20 msgs/min por número con burst de 5.
        self._rate_limiter = sender_rate_limiter or SenderRateLimiter(
            max_por_minuto=20, burst=5,
        )

    # ── autorización ─────────────────────────────────────────────────────────

    def _get_rol(self, sender_phone: str) -> Optional[tuple[str, str]]:
        """Devuelve (rol, nombre_display) si el número está autorizado, None si no.

        Prioridad:
          1. tabla usuarios de la BD (roles explícitos)
          2. credentials.is_operator() (compatibilidad Semana 1 → rol admin)
        """
        # 1. BD
        usuario = self.db.obtener_usuario_por_telefono(sender_phone)
        if usuario and usuario.get("activo", 1):
            return (usuario["rol"], usuario["nombre"])
        # 2. Fallback credential manager (operadores Semana 1)
        if self.credentials.is_operator(sender_phone):
            return ("admin", "operador")
        return None

    # ── entrada principal ─────────────────────────────────────────────────────

    def is_command(self, text: str) -> bool:
        return _is_command(text)

    def handle(self, *, sender_phone: str, text: str) -> bool:
        """Devuelve True si el mensaje fue interpretado como comando.

        Rechaza:
          - rate-limit por sender (defensa anti-spam/brute-force)
          - números no autorizados
          - comandos fuera del rol del usuario
        """
        if not _is_command(text):
            return False

        # Rate-limit por sender (antes que cualquier query a BD).
        if not self._rate_limiter.permitir(sender_phone):
            self.log.warning(
                "rate-limit excedido — sender: %s | texto: %s",
                sender_phone, text[:80],
            )
            # Solo respondemos si el número está autorizado para evitar
            # filtrar info a atacantes (no confirmamos que el bot escucha).
            if self._get_rol(sender_phone) is not None:
                self.reply(
                    sender_phone,
                    "⚠️ Demasiados comandos en poco tiempo. "
                    "Espera ~1 minuto y vuelve a intentar.",
                )
            return True

        rol_info = self._get_rol(sender_phone)
        if rol_info is None:
            self.reply(
                sender_phone,
                "❌ Número no autorizado.\n"
                "Contacte al administrador para registrarse.",
            )
            self.log.warning(
                "comando rechazado — número no registrado: %s | texto: %s",
                sender_phone, text[:80],
            )
            return True

        rol, nombre_usuario = rol_info
        verb = _command_verb(text)

        if verb not in _ROL_PERMISOS.get(rol, set()):
            self.reply(
                sender_phone,
                f"❌ Sin permisos para *{verb}*.\n"
                f"Tu rol es: *{rol}*\n"
                f"Envía AYUDA para ver los comandos disponibles.",
            )
            self.log.warning(
                "comando sin permisos — usuario: %s | rol: %s | verb: %s",
                sender_phone, rol, verb,
            )
            return True

        try:
            if verb == "NUEVO":
                # Detectar sub-comando: "NUEVO CONTRATO" vs "NUEVO PLANO" / "NUEVO"
                primera_linea = text.strip().splitlines()[0].strip().upper()
                if "CONTRATO" in primera_linea:
                    self._handle_nuevo_contrato(sender_phone, text, rol_info)
                else:
                    self._handle_nuevo(sender_phone, text)
            elif verb == "ESTADO":
                self._handle_estado(sender_phone, text)
            elif verb == "APROBAR":
                self._handle_aprobar(sender_phone, text)
            elif verb == "RECHAZAR":
                self._handle_rechazar(sender_phone, text)
            elif verb == "BUSCAR":
                self._handle_buscar(sender_phone, text)
            elif verb == "SUBIR":
                self._handle_subir(sender_phone, text)
            elif verb == "RECIBIR":
                self._handle_recibir(sender_phone, text)
            elif verb == "RESUMEN":
                self._handle_resumen(sender_phone, text)
            elif verb in ("LISTAR", "LISTA"):
                self._handle_listar(sender_phone, text)
            elif verb == "CERRAR":
                self._handle_cerrar(sender_phone, text)
            elif verb == "AVISAR":
                self._handle_avisar(sender_phone, text)
            elif verb == "AUTORIZAR":
                self._handle_autorizar(sender_phone, text)
            elif verb == "PAGAR":
                self._handle_pagar(sender_phone, text)
            elif verb == "CORREGIR":
                self._handle_corregir(sender_phone, text)
            elif verb == "APT":
                self._handle_apt_cmd(sender_phone, text)
            elif verb == "LOTE":
                self._handle_lote(sender_phone, text)
            elif verb == "DEBUG":
                self._handle_debug(sender_phone, text)
            elif verb in ("AYUDA", "HELP"):
                self._handle_ayuda(sender_phone, rol)
        except Exception as e:
            self.log.exception("error procesando comando %s de %s", verb, sender_phone)
            self.reply(sender_phone, f"❌ Error procesando comando: {e}")
        return True

    # ── NUEVO ─────────────────────────────────────────────────────────────────

    def _parse_nuevo(self, text: str) -> dict:
        lines = text.strip().splitlines()
        fields: dict[str, str] = {}
        for line in lines[1:]:
            line = line.strip()
            if not line or ":" not in line:
                continue
            k, v = line.split(":", 1)
            fields[k.strip().lower()] = v.strip()
        return fields

    def _handle_nuevo(self, sender_phone: str, text: str) -> None:
        fields = self._parse_nuevo(text)

        tipo_raw  = (fields.get("tipo") or fields.get("tipo_plano") or "").lower()
        numero    = (fields.get("expediente") or fields.get("numero")
                     or fields.get("numero_expediente"))
        topografo = fields.get("topografo") or fields.get("topógrafo")
        telefono  = (fields.get("telefono") or fields.get("teléfono")
                     or fields.get("cliente_telefono"))

        faltantes = []
        if not tipo_raw:  faltantes.append("tipo")
        if not numero:    faltantes.append("expediente")
        if not topografo: faltantes.append("topografo")
        if not telefono:  faltantes.append("telefono")
        if faltantes:
            self.reply(
                sender_phone,
                f"❌ Faltan campos: {', '.join(faltantes)}\n\n"
                "Envíe AYUDA para ver el formato.",
            )
            return

        tipo = _TIPO_ALIASES.get(tipo_raw)
        if not tipo:
            self.reply(
                sender_phone,
                f"❌ Tipo de plano inválido: {tipo_raw!r}\n"
                "Opciones: segregacion, rectificacion, informacion_posesoria, "
                "reunion_de_fincas, fincas_completas",
            )
            return

        try:
            telefono_norm = normalizar_telefono_cr(telefono)
        except ValueError as e:
            self.reply(sender_phone, f"❌ Teléfono inválido: {e}")
            return

        metadata = self._extraer_metadata(fields)
        metadata["operador_telefono"] = sender_phone  # para notificaciones al operador

        try:
            eid = self.db.crear_expediente(
                numero_expediente=numero,
                tipo_plano=tipo,
                nombre_topografo=topografo,
                telefono_cliente=telefono_norm,
                cedula_topografo=fields.get("cedula") or fields.get("cédula"),
                nombre_cliente=fields.get("cliente") or fields.get("nombre_cliente"),
                municipalidad=fields.get("municipalidad", "San Ramón"),
                metadata=metadata,
                actor=f"whatsapp:{sender_phone}",
            )
        except DatabaseError as e:
            self.reply(sender_phone, f"❌ {e}")
            return

        try:
            for fase in FASES:
                self.drive.folder_for(eid, fase)
        except Exception:
            self.log.exception("error creando carpetas para %s (continuando)", numero)

        # Identificador legible: nombre del proyecto si está, fallback a número
        exp_recien = {"numero_expediente": numero, "metadata_json": json.dumps(metadata)}
        ident = display_proyecto(exp_recien)
        self.reply(
            sender_phone,
            f"✅ Expediente creado\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"Proyecto: *{ident}*\n"
            f"ID: `{eid}`\n"
            f"Tipo: {tipo}\n"
            f"Topógrafo: {topografo}\n"
            f"Cliente: {telefono_norm}\n"
            f"Estado: {Estado.RECIBIDO.value}\n\n"
            f"📁 Coloque los archivos del plano en:\n"
            f"`data/files/{numero}/01_Campo/`\n\n"
            "El sistema iniciará el flujo automáticamente cuando "
            "detecte los archivos.",
        )

    # ── NUEVO CONTRATO ────────────────────────────────────────────────────────

    def _generar_numero_expediente(self) -> str:
        """Genera un número de expediente automático: EXP-{AÑO}-{SEQ:04d}."""
        anio = datetime.now(timezone.utc).year
        prefijo = f"EXP-{anio}-"
        # Buscar existentes con ese prefijo (sin límite relevante)
        existentes = self.db.buscar_expedientes(prefijo, max_resultados=9999)
        # Filtrar sólo los que coinciden exactamente con el prefijo
        del_anio = [
            e for e in existentes
            if e.get("numero_expediente", "").startswith(prefijo)
        ]
        seq = len(del_anio) + 1
        numero = f"{prefijo}{seq:04d}"
        # Garantizar unicidad en caso de colisión
        while self.db.buscar_por_numero(numero):
            seq += 1
            numero = f"{prefijo}{seq:04d}"
        return numero

    def _handle_nuevo_contrato(
        self,
        sender_phone: str,
        text: str,
        rol_info: Optional[tuple[str, str]],
    ) -> None:
        """NUEVO CONTRATO — crea expediente desde formato de contrato.

        Formato esperado:
            NUEVO CONTRATO
            Tipo: segregacion
            Cliente: Luis Alonso Rojas Herrera
            Cedula: 2-0000-0000
            Proyecto: Finca Las Palmas
            Finca: 123456          (opcional)
            Protocolo: 789         (opcional)
            Planos: 1              (opcional)
            Municipalidad: ...     (opcional, default San Ramón)
        """
        fields = self._parse_nuevo(text)

        tipo_raw    = (fields.get("tipo") or fields.get("tipo_plano") or "").lower()
        nombre_cli  = fields.get("cliente") or fields.get("nombre_cliente")
        cedula_cli  = fields.get("cedula") or fields.get("cédula")
        proyecto    = fields.get("proyecto") or fields.get("nombre_proyecto")

        faltantes = []
        if not tipo_raw:   faltantes.append("tipo")
        if not nombre_cli: faltantes.append("cliente")
        if faltantes:
            self.reply(
                sender_phone,
                f"❌ Faltan campos: {', '.join(faltantes)}\n\n"
                "Formato:\n"
                "```\nNUEVO CONTRATO\n"
                "Tipo: segregacion\n"
                "Cliente: Nombre Apellido\n"
                "Cedula: 2-0000-0000\n"
                "Proyecto: Nombre Propiedad\n"
                "Finca: 123456\n"
                "Protocolo: 789\n"
                "Planos: 1\n"
                "Provincia: Alajuela\n"
                "Canton: San Ramon\n"
                "Distrito: Santiago\n"
                "Dueño: Juan Perez\n"
                "Plano: 01\n```\n\n"
                "_Provincia/Canton/Distrito/Dueño/Plano son opcionales.\n"
                "Si se incluyen, la carpeta seguirá la estructura:\n"
                "ACTIVOS\\Provincia\\Canton\\Distrito\\Dueño\\Proyecto\\Año\\01\\_",
            )
            return

        tipo = _TIPO_ALIASES.get(tipo_raw)
        if not tipo:
            self.reply(
                sender_phone,
                f"❌ Tipo de plano inválido: {tipo_raw!r}\n"
                "Opciones: segregacion, rectificacion, informacion_posesoria, "
                "reunion_de_fincas, fincas_completas",
            )
            return

        # Topógrafo = nombre del usuario que envía el comando
        nombre_topografo = (rol_info[1] if rol_info else None) or "Desconocido"

        # Número de expediente autogenerado
        numero = self._generar_numero_expediente()

        # Metadata extra del contrato
        metadata = self._extraer_metadata(fields)
        if fields.get("finca"):
            metadata["finca"] = fields["finca"]
        if fields.get("protocolo"):
            metadata["protocolo"] = fields["protocolo"]
        if fields.get("proyecto") or fields.get("nombre_proyecto"):
            metadata["proyecto"] = proyecto
        planos_raw = fields.get("planos")
        if planos_raw:
            try:
                metadata["cantidad_planos"] = int(planos_raw)
            except ValueError:
                pass

        municipalidad = fields.get("municipalidad", "San Ramón")

        try:
            eid = self.db.crear_expediente(
                numero_expediente=numero,
                tipo_plano=tipo,
                nombre_topografo=nombre_topografo,
                telefono_cliente=sender_phone,   # teléfono del operador como contacto
                cedula_topografo=cedula_cli,      # cédula del cliente/propietario
                nombre_cliente=nombre_cli,
                municipalidad=municipalidad,
                metadata=metadata,
                actor=f"whatsapp:{sender_phone}",
            )
        except DatabaseError as e:
            self.reply(sender_phone, f"❌ {e}")
            return

        # Crear carpeta de trabajo en data/files/<PROV>/<CANT>/<DIST>/<NOMBRE>/
        # (workspace local del bot — independiente de MEGA)
        data_files_path = ""
        if metadata.get("provincia") and metadata.get("nombre_proyecto"):
            try:
                from pathlib import Path as _P
                import re as _re, unicodedata as _ud, json as _json
                def _norm(t: str) -> str:
                    t = _ud.normalize("NFD", t)
                    t = "".join(c for c in t if _ud.category(c) != "Mn")
                    t = t.upper().strip()
                    t = _re.sub(r"\s+", "_", t)
                    return _re.sub(r"[^A-Z0-9_\-]", "", t)
                base = (
                    _P("data/files")
                    / _norm(metadata["provincia"])
                    / _norm(metadata.get("canton", ""))
                    / _norm(metadata.get("distrito", ""))
                    / _norm(metadata["nombre_proyecto"])
                )
                for sub in ["01_Campo", "02_Oficina", "03_Catastrado"]:
                    (base / sub).mkdir(parents=True, exist_ok=True)
                data_files_path = str(base.absolute()).replace("\\", "/")
                # Guardar path en metadata
                exp_db = self.db.obtener_expediente(eid)
                meta_db = _json.loads(exp_db.get("metadata_json") or "{}")
                meta_db["path_carpeta"] = data_files_path
                self.db.actualizar_metadata(
                    eid, {"path_carpeta": data_files_path},
                    actor=f"whatsapp:{sender_phone}",
                )
            except Exception as e:
                self.log.exception("error creando carpeta data/files para %s", numero)

        # Crear estructura de carpetas Mega (v2 si hay provincia)
        mega_path_str = ""
        if self.folder_manager:
            try:
                raiz = self.folder_manager.crear_estructura_expediente(eid)
                mega_path_str = f"\n📁 Carpeta Mega:\n`{raiz}`"
            except Exception as e:
                self.log.exception("error creando carpetas Mega para %s", numero)
                mega_path_str = f"\n⚠ Carpetas Mega no creadas: {e}"
        else:
            # Fallback a drive (Semana 2)
            try:
                from src.agents.drive_agent import FASES
                for fase in FASES:
                    self.drive.folder_for(eid, fase)
            except Exception:
                self.log.exception("error creando carpetas Drive para %s (continuando)", numero)

        # Mostrar info de ubicación si está disponible
        ubicacion_str = ""
        if metadata.get("provincia"):
            provincia = metadata.get("provincia", "")
            canton    = metadata.get("canton", "")
            distrito  = metadata.get("distrito", "")
            num_plano = metadata.get("numero_plano", "01")
            ubicacion_str = (
                f"\nProvincia: {provincia}"
                f"\nCantón: {canton}"
                f"\nDistrito: {distrito}"
                f"\nPlano N°: {num_plano}"
            )

        # Identificador legible: nombre del proyecto si está, fallback a número
        exp_recien = {"numero_expediente": numero, "metadata_json": json.dumps(metadata)}
        ident = display_proyecto(exp_recien)
        self.reply(
            sender_phone,
            f"✅ Expediente creado (contrato)\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"Proyecto: *{ident}*\n"
            f"ID: `{eid}`\n"
            f"Tipo: {tipo}\n"
            f"Cliente: {nombre_cli}\n"
            f"Cédula: {cedula_cli or '(sin cédula)'}\n"
            f"Municipalidad: {municipalidad}\n"
            f"Topógrafo: {nombre_topografo}"
            f"{ubicacion_str}"
            f"{mega_path_str}\n\n"
            f"📂 Coloque los archivos del plano en la carpeta indicada:\n"
            f"  plano.pdf  entero.pdf  shape.zip\n"
            f"  visado.pdf  carta_agua.pdf  (cuando apliquen)",
        )
        self.log.info(
            "NUEVO CONTRATO — expediente: %s | tipo: %s | cliente: %s | de: %s",
            numero, tipo, nombre_cli, sender_phone,
        )

    def _extraer_metadata(self, fields: dict) -> dict:
        meta: dict = {}
        for k in ("area", "area_m2", "área", "área_m2"):
            if k in fields:
                try:
                    meta["area_m2"] = float(
                        fields[k].replace(",", ".").replace(" ", "")
                    )
                except ValueError:
                    pass
                break
        for k in ("carta_agua", "carta_agua_requerida"):
            if k in fields:
                v = fields[k].strip().lower()
                if v in _BOOL_TRUE:
                    meta["carta_agua_requerida"] = True
                elif v in _BOOL_FALSE:
                    meta["carta_agua_requerida"] = False
                break
        for k in ("zona", "zona_regulador", "zona regulador"):
            if k in fields:
                v_zona = fields[k].strip().lower()
                zona_id = _ZONA_REG_ALIASES.get(v_zona)
                if zona_id is None:
                    zona_id = _ZONA_REG_ALIASES.get(v_zona.replace(" ", "_"))
                if zona_id:
                    meta["zona_regulador"] = zona_id
                break
        for k in ("ubicacion", "ubicación", "direccion", "dirección"):
            if k in fields:
                meta["ubicacion"] = fields[k]
                break
        if "notas" in fields:
            meta["notas"] = fields["notas"]

        # ── Ubicación geográfica (estructura v2) ─────────────────────────────
        for k in ("provincia",):
            if k in fields and fields[k].strip():
                meta["provincia"] = fields[k].strip()
                break
        for k in ("canton", "cantón"):
            if k in fields and fields[k].strip():
                meta["canton"] = fields[k].strip()
                break
        for k in ("distrito",):
            if k in fields and fields[k].strip():
                meta["distrito"] = fields[k].strip()
                break
        for k in ("dueno", "dueño", "propietario", "nombre_dueno", "dueño_proyecto"):
            if k in fields and fields[k].strip():
                meta["nombre_dueno"] = fields[k].strip()
                break
        for k in ("anio", "año", "year"):
            if k in fields and fields[k].strip():
                try:
                    meta["anio"] = int(fields[k].strip())
                except ValueError:
                    pass
                break
        for k in ("plano", "numero_plano", "num_plano"):
            if k in fields and fields[k].strip():
                meta["numero_plano"] = str(fields[k].strip()).zfill(2)
                break
        # Nombre del proyecto (carpeta) — nombre completo del topógrafo,
        # NO la abreviatura del cajetín. Ej: "Rolando Granja", no "ROGRANJ".
        for k in ("proyecto", "nombre_proyecto", "nombre", "proy"):
            if k in fields and fields[k].strip():
                meta["nombre_proyecto"] = fields[k].strip()
                # Mantener también `proyecto` como alias por compat
                meta.setdefault("proyecto", fields[k].strip())
                break

        return meta

    # ── ESTADO ────────────────────────────────────────────────────────────────

    def _handle_estado(self, sender_phone: str, text: str) -> None:
        arg = self._extract_arg(text)
        if not arg:
            self.reply(sender_phone,
                       "❌ Uso: ESTADO <numero_expediente>\n"
                       "   o:  ESTADO APT <numero_expediente>")
            return

        # Sub-comando: ESTADO APT <numero>
        if arg.upper().startswith("APT"):
            numero = arg[3:].strip()
            self._handle_estado_apt(sender_phone, numero)
            return

        # Consulta de expediente en BD
        exp = self.db.buscar_por_numero(arg)
        if not exp:
            self.reply(sender_phone, f"❌ Expediente {arg!r} no encontrado.")
            return

        historial   = self.db.historial_estados(exp["id"])[-5:]
        cambios     = "\n".join(
            f"  • {h['timestamp'][:16]} → {h['estado_nuevo']}"
            for h in reversed(historial)
        ) or "  (sin cambios)"

        pendientes  = self.db.acciones_pendientes(expediente_id=exp["id"])
        pend_str    = ""
        if pendientes:
            pend_str = "\n\n⏳ Esperando confirmación:\n" + "\n".join(
                f"  • {p['tipo_accion']}" for p in pendientes
            )

        halt  = exp["estado_actual"] in Estado.halts()
        emoji = "🔴" if halt else "📋"

        ident = display_proyecto(exp)
        self.reply(
            sender_phone,
            f"{emoji} *{ident}*\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"Estado: *{exp['estado_actual']}*"
            f"{' [HALT — requiere APROBAR]' if halt else ''}\n"
            f"Tipo: {exp['tipo_plano']}\n"
            f"Topógrafo: {exp['nombre_topografo']}\n"
            f"Cliente: {exp.get('nombre_cliente') or '(sin nombre)'}\n"
            f"Teléfono: {exp['telefono_cliente']}\n"
            f"Actualizado: {exp['fecha_actualizacion'][:16]}\n\n"
            f"Últimos cambios:\n{cambios}"
            f"{pend_str}",
        )

    def _handle_listar(self, sender_phone: str, text: str) -> None:
        """LISTAR — registro de todos los planos del topógrafo.

        Sintaxis:
          LISTAR
          LISTAR <provincia>
          LISTAR <provincia> <canton>
          LISTAR estado <estado>
          LISTAR buscar <texto>

        Ej:
          LISTAR
          LISTAR ALAJUELA
          LISTAR ALAJUELA SAN_RAMON
          LISTAR estado enviado_cfia
          LISTAR buscar Rolando
        """
        import json as _json

        # Parsear filtros del texto
        partes = text.strip().split(maxsplit=4)[1:]  # quitar "LISTAR"
        filtro_provincia = ""
        filtro_canton = ""
        filtro_estado = ""
        filtro_busqueda = ""
        if partes:
            primera = partes[0].lower()
            if primera == "estado" and len(partes) >= 2:
                filtro_estado = partes[1].lower()
            elif primera == "buscar" and len(partes) >= 2:
                filtro_busqueda = " ".join(partes[1:]).upper()
            else:
                filtro_provincia = partes[0].upper()
                if len(partes) >= 2:
                    filtro_canton = partes[1].upper()

        # Cargar todos los expedientes activos
        try:
            rows = self.db.listar_expedientes(solo_activos=True)
        except AttributeError:
            # fallback: query directo
            with self.db.connect() as conn:
                rows = list(conn.execute(
                    "SELECT * FROM expedientes WHERE cancelado = 0 "
                    "ORDER BY numero_expediente"
                ).fetchall())

        items = []
        for r in rows:
            meta = _json.loads(r["metadata_json"] or "{}") if r["metadata_json"] else {}
            entry = {
                "numero":      r["numero_expediente"],
                "tipo":        r["tipo_plano"],
                "estado":      r["estado_actual"],
                "provincia":   meta.get("provincia") or "",
                "canton":      meta.get("canton") or "",
                "distrito":    meta.get("distrito") or "",
                "nombre":      meta.get("nombre_proyecto") or "",
                "apt_tramite": meta.get("apt_tramite") or "",
                "apt_estado":  meta.get("apt_estado") or "",
            }
            # Aplicar filtros
            if filtro_provincia and entry["provincia"].upper() != filtro_provincia:
                continue
            if filtro_canton and entry["canton"].upper() != filtro_canton:
                continue
            if filtro_estado and entry["estado"] != filtro_estado and entry["apt_estado"] != filtro_estado:
                continue
            if filtro_busqueda:
                hay = (entry["nombre"] or "").upper() + " " + entry["numero"].upper()
                if filtro_busqueda not in hay:
                    continue
            items.append(entry)

        if not items:
            self.reply(sender_phone, "📋 Sin resultados con esos filtros.")
            return

        # Construir respuesta — agrupar por ubicación si hay varios
        lineas = [f"📋 *{len(items)} expediente(s):*\n"]
        for e in items[:20]:   # máx 20 para no inundar WhatsApp
            ubic = "/".join(filter(None, [e["provincia"], e["canton"], e["distrito"]]))
            apt_info = f"  APT {e['apt_tramite']}" if e["apt_tramite"] else ""
            apt_estado = f" [{e['apt_estado']}]" if e["apt_estado"] else ""
            lineas.append(
                f"• *{e['numero']}* — {e['tipo']}\n"
                f"  📍 {ubic or '-'}  📁 {e['nombre'] or '-'}\n"
                f"  Estado: {e['estado']}{apt_info}{apt_estado}"
            )
        if len(items) > 20:
            lineas.append(f"\n_(mostrando primeros 20 de {len(items)} — use filtros para refinar)_")
        lineas.append(
            "\n_Filtros: LISTAR <PROV>, LISTAR <PROV> <CANT>,_"
            "\n_         LISTAR estado <est>, LISTAR buscar <texto>_"
        )
        self.reply(sender_phone, "\n".join(lineas))

    def _handle_estado_apt(self, sender_phone: str, numero: str) -> None:
        """ESTADO APT <numero> — consulta estado en portal APT (cfia.or.cr)."""
        if not numero:
            self.reply(sender_phone, "❌ Uso: ESTADO APT <numero_expediente>")
            return
        if self.apt is None:
            self.reply(
                sender_phone,
                "⚠️ Agente APT no disponible.\n"
                "Verifique que el token BCR está conectado y el bot reiniciado.",
            )
            return
        exp = self.db.buscar_por_numero(numero)
        if not exp:
            self.reply(sender_phone, f"❌ Expediente {numero!r} no encontrado en BD.")
            return

        meta = json.loads(exp.get("metadata_json") or "{}")
        tramite = meta.get("apt_tramite") or "(sin trámite)"
        archivos_r1 = meta.get("apt_r1_archivos") or []
        archivos_r2 = meta.get("apt_r2_archivos") or []

        try:
            estado_raw = self.apt.consultar_estado(exp["id"])
        except Exception as e:
            self.reply(sender_phone, f"❌ Error consultando portal APT: {e}")
            return

        if estado_raw is None:
            if tramite == "(sin trámite)":
                self.reply(
                    sender_phone,
                    f"🌐 *{numero}*\n"
                    f"━━━━━━━━━━━━━━━━━\n"
                    f"Sin trámite APT registrado.\n"
                    f"Estado interno: *{exp['estado_actual']}*",
                )
            else:
                self.reply(
                    sender_phone,
                    f"🌐 *{numero}*\n"
                    f"━━━━━━━━━━━━━━━━━\n"
                    f"Trámite #{tramite} no encontrado en portal APT.\n"
                    f"Puede que aún no haya sido procesado.",
                )
            return

        # Normalizar estado
        estado_norm = self.apt._normalizar_estado(estado_raw)
        _EMOJI = {
            "respondido": "📩",
            "inscrito":   "✅",
            "pendiente":  "⏳",
            "edicion":    "✏️",
        }
        _DESC = {
            "respondido": "APT emitió minuta/correcciones — revisar carpeta RESPUESTA",
            "inscrito":   "Plano inscrito en Registro Nacional",
            "pendiente":  "En revisión (Calificación RN)",
            "edicion":    "Aún en edición — sin presentar",
        }
        emoji = _EMOJI.get(estado_norm, "🔍")
        desc  = _DESC.get(estado_norm, estado_raw)

        # Líneas de archivos subidos
        arch_lines = ""
        if archivos_r1:
            arch_lines += f"\nArchivos R1: {', '.join(archivos_r1)}"
        if archivos_r2:
            arch_lines += f"\nArchivos R2: {', '.join(archivos_r2)}"

        self.reply(
            sender_phone,
            f"{emoji} *{numero}* — Portal APT\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"Trámite: #{tramite}\n"
            f"Estado portal: *{estado_raw}*\n"
            f"Estado interno: *{exp['estado_actual']}*"
            f"{arch_lines}\n\n"
            f"ℹ️ {desc}",
        )

    # ── APROBAR ───────────────────────────────────────────────────────────────

    def _handle_aprobar(self, sender_phone: str, text: str) -> None:
        numero = self._extract_arg(text)
        if not numero:
            self.reply(sender_phone, "❌ Uso: APROBAR <numero_expediente>")
            return
        exp = self.db.buscar_por_numero(numero)
        if not exp:
            self.reply(sender_phone, f"❌ Expediente {numero!r} no encontrado.")
            return

        estado_actual = exp["estado_actual"]
        nuevo = _HALT_TRANSITIONS.get(estado_actual)
        if not nuevo:
            self.reply(
                sender_phone,
                f"❌ Expediente *{numero}* en estado *{estado_actual}*\n"
                "no requiere aprobación manual.\n\n"
                "APROBAR sólo aplica a estados halt:\n"
                f"  - {', '.join(sorted(_HALT_TRANSITIONS))}",
            )
            return

        # ── limpieza especial al aprobar correcciones/traslapes APT ──────────
        # Si venimos de APT_CORRECCIONES o APT_TRASLAPES, el expediente va a
        # re-subir archivos a APT.  Hay que:
        #   1. Limpiar apt_r1_archivos_subidos para forzar re-upload.
        #   2. Invalidar firma_digital_r1 anterior (no confundir al workflow).
        #   3. Incrementar apt_correcciones_count para personalizar mensajes.
        if estado_actual in (
            Estado.APT_CORRECCIONES.value,
            Estado.APT_TRASLAPES.value,
        ):
            import json as _json
            meta_actual = _json.loads(exp.get("metadata_json") or "{}")
            conteo = meta_actual.get("apt_correcciones_count", 0) + 1
            self.db.actualizar_metadata(
                exp["id"],
                {
                    "apt_r1_archivos_subidos": False,
                    "apt_correcciones_count":  conteo,
                },
                actor=f"whatsapp:{sender_phone}",
            )
            # Invalidar confirmaciones anteriores de FD y subida para que
            # el workflow las solicite de nuevo con los archivos corregidos.
            self.db.invalidar_confirmacion(
                exp["id"], "firma_digital_r1",
                actor=f"whatsapp:{sender_phone}",
            )
            self.db.invalidar_confirmacion(
                exp["id"], "subir_apt",
                actor=f"whatsapp:{sender_phone}",
            )

        self.db.cambiar_estado(
            exp["id"], nuevo,
            actor=f"whatsapp:{sender_phone}",
            detalles=f"APROBAR manual desde {estado_actual}",
        )

        msg = (
            f"✅ Expediente *{numero}* avanzado\n"
            f"  De: {estado_actual}\n"
            f"  A:  {nuevo}"
        )
        # Recordatorio específico cuando hay re-subida a APT
        if estado_actual in (
            Estado.APT_CORRECCIONES.value,
            Estado.APT_TRASLAPES.value,
        ):
            msg += (
                "\n\n⚠️ *Antes de la próxima subida a APT*, asegúrese de "
                "colocar el plano *anverso firmado y corregido* en la "
                "carpeta 03_APT_R1\\SUBIR. Sin el anverso actualizado, "
                "APT rechazará con los mismos errores."
            )
        self.reply(sender_phone, msg)

    # ── RECHAZAR ──────────────────────────────────────────────────────────────

    def _handle_rechazar(self, sender_phone: str, text: str) -> None:
        numero = self._extract_arg(text)
        if not numero:
            self.reply(sender_phone, "❌ Uso: RECHAZAR <numero_expediente>")
            return
        exp = self.db.buscar_por_numero(numero)
        if not exp:
            self.reply(sender_phone, f"❌ Expediente {numero!r} no encontrado.")
            return

        if exp["estado_actual"] in Estado.terminales():
            self.reply(
                sender_phone,
                f"❌ Expediente *{numero}* ya está en estado terminal: "
                f"{exp['estado_actual']}",
            )
            return

        anterior = exp["estado_actual"]
        self.db.cambiar_estado(
            exp["id"], Estado.CANCELADO.value,
            actor=f"whatsapp:{sender_phone}",
            detalles="RECHAZAR manual",
        )
        self.reply(
            sender_phone,
            f"❌ Expediente *{numero}* cancelado\n"
            f"  Estado anterior: {anterior}",
        )

    # ── BUSCAR ────────────────────────────────────────────────────────────────

    def _handle_buscar(self, sender_phone: str, text: str) -> None:
        texto = self._extract_arg(text)
        if not texto:
            self.reply(sender_phone,
                       "❌ Uso: BUSCAR <texto>\n"
                       "Busca por número, topógrafo o nombre de cliente.")
            return

        resultados = self.db.buscar_expedientes(texto, max_resultados=10)
        if not resultados:
            self.reply(
                sender_phone,
                f"🔍 Sin resultados para *{texto}*\n"
                "Pruebe con otra palabra clave.",
            )
            return

        halts = Estado.halts()
        terminales = Estado.terminales()
        lines = [
            f"🔍 Resultados para *{texto}* ({len(resultados)}):",
            "━━━━━━━━━━━━━━━━━",
        ]
        for e in resultados:
            estado = e["estado_actual"]
            if estado in terminales:
                emoji = "⬛"
            elif estado in halts:
                emoji = "🔴"
            else:
                emoji = "🟢"
            ident = display_proyecto(e)
            lines.append(
                f"{emoji} *{ident}* ({e['tipo_plano']})\n"
                f"   Topógrafo: {e['nombre_topografo']}\n"
                f"   Estado: {estado}"
            )

        body = "\n".join(lines)
        if len(body) > 3500:
            body = body[:3400] + "\n…(más resultados, refine la búsqueda)"
        self.reply(sender_phone, body)

    # ── SUBIR ─────────────────────────────────────────────────────────────────

    def _handle_subir(self, sender_phone: str, text: str) -> None:
        """SUBIR <numero> — confirma que los archivos de campo están listos.

        Notifica al sistema para que en el próximo ciclo (60s) valide
        los archivos del expediente. No cambia estado directamente —
        el validador de formato hace esa transición.
        """
        numero = self._extract_arg(text)
        if not numero:
            self.reply(sender_phone,
                       "❌ Uso: SUBIR <numero_expediente>\n"
                       "Confirma que los archivos están en la carpeta 01_Campo.")
            return

        exp = self.db.buscar_por_numero(numero)
        if not exp:
            self.reply(sender_phone, f"❌ Expediente {numero!r} no encontrado.")
            return

        ident = display_proyecto(exp)
        if exp["estado_actual"] not in {Estado.RECIBIDO.value, Estado.FORMATO_INVALIDO.value}:
            self.reply(
                sender_phone,
                f"⚠️ Expediente *{ident}* en estado *{exp['estado_actual']}*\n"
                "SUBIR solo aplica cuando el expediente espera archivos.\n"
                "Use ESTADO para ver el estado actual.",
            )
            return

        self.log.info("SUBIR confirmado — expediente: %s | operador: %s",
                      numero, sender_phone)
        self.reply(
            sender_phone,
            f"📂 Archivos confirmados para *{ident}*\n"
            "El sistema validará formato y tamaño en el próximo ciclo (≤60s).\n\n"
            f"Carpeta esperada:\n`data/files/{numero}/01_Campo/`\n\n"
            "✅ PDF: B&N, < 600KB\n"
            "✅ ZIP: .shp + .dbf + .shx, < 1.5MB",
        )

    # ── RECIBIR ───────────────────────────────────────────────────────────────

    def _handle_recibir(self, sender_phone: str, text: str) -> None:
        """RECIBIR <numero> — marca que el cliente recibió el plano inscrito."""
        numero = self._extract_arg(text)
        if not numero:
            self.reply(sender_phone,
                       "❌ Uso: RECIBIR <numero_expediente>\n"
                       "Marca que el cliente recibió físicamente el plano inscrito.")
            return

        exp = self.db.buscar_por_numero(numero)
        if not exp:
            self.reply(sender_phone, f"❌ Expediente {numero!r} no encontrado.")
            return

        ident = display_proyecto(exp)
        estado = exp["estado_actual"]
        # Si está en INSCRITO_DESCARGADO → avanzar a ENTREGADO
        if estado == Estado.INSCRITO_DESCARGADO.value:
            self.db.cambiar_estado(
                exp["id"], Estado.ENTREGADO.value,
                actor=f"whatsapp:{sender_phone}",
                detalles="RECIBIR — cliente firmó de recibido",
            )
            self.reply(
                sender_phone,
                f"✅ Expediente *{ident}* marcado como *ENTREGADO*\n"
                "El plano fue recibido por el cliente.\n"
                "⚠️ Recuerde: plazo de 1 año para inscribir en Registro de la Propiedad.",
            )
        elif estado == Estado.ENTREGADO.value:
            self.reply(
                sender_phone,
                f"ℹ️ Expediente *{ident}* ya está marcado como ENTREGADO.",
            )
        else:
            # Registrar en audit log aunque no cambie estado
            self.log.info("RECIBIR registrado — expediente: %s | estado: %s | op: %s",
                          numero, estado, sender_phone)
            self.reply(
                sender_phone,
                f"📝 Recepción registrada para *{ident}*\n"
                f"Estado actual: *{estado}*\n"
                "El cambio a ENTREGADO ocurrirá cuando el plano esté inscrito y descargado.",
            )

    # ── RESUMEN ───────────────────────────────────────────────────────────────

    def _handle_debug(self, sender_phone: str, text: str) -> None:
        """DEBUG <expediente> — dump del estado interno del bot.

        Muestra:
          - Progreso APT (qué secciones bP* están verdes)
          - Apt trámite + estado
          - Discrepancias registradas
          - Anomalías reportadas
          - Snapshots disponibles
          - Reglas/ignoras activas del operador
        """
        import json
        numero = self._extract_arg(text)
        if not numero:
            self.reply(sender_phone,
                       "❌ Uso: DEBUG <numero_expediente>")
            return
        exp = self.db.buscar_por_numero(numero.upper())
        if not exp:
            self.reply(sender_phone, f"❌ Expediente {numero!r} no encontrado.")
            return

        meta = {}
        try:
            meta = json.loads(exp.get("metadata_json") or "{}")
        except Exception:
            pass

        progreso = meta.get("apt_progreso") or {}
        discrepancias = meta.get("apt_discrepancias_rnp") or []
        anomalias = meta.get("apt_anomalias") or []
        secciones = progreso.get("secciones") or {}
        archivos = progreso.get("archivos_subidos") or []

        # Snapshots disponibles
        snapshots = []
        try:
            from src.utils.anomaly_snapshot import listar_snapshots_de_expediente
            snapshots = listar_snapshots_de_expediente(exp["numero_expediente"])
        except Exception:
            pass

        ident = display_proyecto(exp)
        lineas = [
            f"🔍 *DEBUG — {ident}*",
            f"━━━━━━━━━━━━━━━━━",
            f"Estado workflow: *{exp['estado_actual']}*",
            f"Tipo plano:      {exp['tipo_plano']}",
            f"Trámite APT:     {meta.get('apt_tramite', '(sin contrato)')}",
            f"Estado APT:      {meta.get('apt_estado', '-')}",
            f"Envío CFIA:      {meta.get('apt_envio_fecha', '-')[:19]}",
            "",
        ]

        # Progreso secciones plano
        if progreso:
            lineas.append("📋 *Progreso bP1-bP7*")
            todas = ["bP1", "bP2", "bP3", "bP4", "bP5", "bP6", "bP7"]
            for s in todas:
                check = "✅" if secciones.get(s) else "⏳"
                lineas.append(f"  {check} {s}")
            if archivos:
                lineas.append(f"  Archivos: {', '.join(archivos)}")
            lineas.append("")

        # Discrepancias
        if discrepancias:
            lineas.append(f"⚠️ *Discrepancias ({len(discrepancias)})*")
            for d in discrepancias[-5:]:  # últimas 5
                tipo = d.get("tipo", "?")
                ctx = d.get("contexto", "?")
                lineas.append(f"  • [{tipo}] {ctx}")
            lineas.append("")

        # Anomalías
        if anomalias:
            lineas.append(f"🚨 *Anomalías ({len(anomalias)})*")
            for a in anomalias[-3:]:  # últimas 3
                ts = (a.get("ts") or "")[:16]
                desc = (a.get("descripcion", "") or "")[:60]
                lineas.append(f"  [{ts}] {desc}")
            lineas.append("")

        # Snapshots
        if snapshots:
            lineas.append(f"📸 *Snapshots ({len(snapshots)})*")
            for s in snapshots[:3]:
                lineas.append(f"  {s.name}")
            lineas.append("")

        # Reglas/ignoras del operador
        try:
            reglas = self.db.listar_memoria_operador(tipo="regla", activa=True)
            ignoras = self.db.listar_memoria_operador(tipo="ignora", activa=True)
            if reglas or ignoras:
                lineas.append(f"📌 *Memoria operador*")
                lineas.append(f"  Reglas activas: {len(reglas)}")
                lineas.append(f"  Ignoras activos: {len(ignoras)}")
        except Exception:
            pass

        self.reply(sender_phone, "\n".join(lineas))

    def _handle_resumen(self, sender_phone: str, text: str) -> None:
        today = datetime.now(timezone.utc).date().isoformat()
        expedientes = self.db.listar_expedientes(completados=False)
        terminales  = Estado.terminales()
        halts       = Estado.halts()

        activos_hoy = [
            e for e in expedientes
            if e["estado_actual"] not in terminales
            and (
                e["fecha_actualizacion"][:10] == today
                or e["fecha_creacion"][:10] == today
            )
        ]

        if not activos_hoy:
            self.reply(
                sender_phone,
                f"📊 *Resumen* ({today})\nSin expedientes activos hoy.",
            )
            return

        lines = [
            f"📊 *Resumen* ({today})",
            f"Total activos hoy: {len(activos_hoy)}",
            "━━━━━━━━━━━━━━━━━",
        ]
        for e in activos_hoy:
            estado = e["estado_actual"]
            emoji  = "🔴" if estado in halts else "🟢"
            suffix = " [HALT]" if estado in halts else ""
            lines.append(
                f"{emoji} *{e['numero_expediente']}* ({e['tipo_plano']})\n"
                f"   → {estado}{suffix}"
            )

        body = "\n".join(lines)
        if len(body) > 3500:
            body = body[:3400] + "\n\n…(truncado, use ESTADO <numero> para detalles)"
        self.reply(sender_phone, body)

    # ── CERRAR ────────────────────────────────────────────────────────────────

    def _handle_cerrar(self, sender_phone: str, text: str) -> None:
        """CERRAR APT — cierra la sesión Playwright del portal APT."""
        arg = (self._extract_arg(text) or "").upper()
        if arg != "APT":
            self.reply(
                sender_phone,
                "❌ Uso: CERRAR APT\n"
                "Cierra la sesión del portal APT y libera el token BCR.",
            )
            return

        if self.apt is None:
            self.reply(
                sender_phone,
                "⚠️ Agente APT no inicializado — no hay sesión que cerrar.",
            )
            return

        try:
            self.apt.cerrar_sesion()
            self.reply(
                sender_phone,
                "✅ Sesión APT cerrada.\n"
                "El token BCR puede ser extraído con seguridad.",
            )
        except NotImplementedError:
            self.reply(
                sender_phone,
                "⚠️ Cierre de sesión APT aún no implementado.\n"
                "Detenga el bot antes de extraer el token BCR.",
            )
        except Exception as e:
            self.reply(sender_phone, f"❌ Error cerrando sesión APT: {e}")

    # ── AVISAR ────────────────────────────────────────────────────────────────

    def _handle_avisar(self, sender_phone: str, text: str) -> None:
        """AVISAR <numero> <mensaje> — envía un mensaje al cliente del expediente."""
        arg = self._extract_arg(text)
        if not arg:
            self.reply(sender_phone,
                       "❌ Uso: AVISAR <numero_expediente> <mensaje>\n"
                       "Ejemplo: AVISAR 12345-2026 Su plano ya está listo.")
            return

        parts = arg.split(maxsplit=1)
        if len(parts) < 2:
            self.reply(sender_phone,
                       "❌ Falta el mensaje.\n"
                       "Uso: AVISAR <numero> <mensaje>")
            return

        numero, mensaje = parts[0], parts[1].strip()
        exp = self.db.buscar_por_numero(numero)
        if not exp:
            self.reply(sender_phone, f"❌ Expediente {numero!r} no encontrado.")
            return

        cliente_phone = exp["telefono_cliente"]
        prefijo       = f"📋 *Expediente {numero}*\n"
        self.reply(
            cliente_phone,
            prefijo + mensaje,
        )
        self.reply(
            sender_phone,
            f"✅ Mensaje enviado al cliente del expediente *{numero}*\n"
            f"Teléfono: {cliente_phone}\n"
            f"Mensaje: {mensaje[:100]}{'…' if len(mensaje) > 100 else ''}",
        )
        self.log.info(
            "AVISAR — expediente: %s | de: %s | a: %s | msg: %s",
            numero, sender_phone, cliente_phone, mensaje[:80],
        )

    # ── AUTORIZAR ─────────────────────────────────────────────────────────────

    def _handle_autorizar(self, sender_phone: str, text: str) -> None:
        """AUTORIZAR DRIVE — inicia el flujo OAuth de Google Drive en el servidor.

        Abre el navegador en la máquina que corre el bot. El operador debe
        estar en el servidor (o con acceso remoto) cuando ejecuta este comando.
        """
        arg = (self._extract_arg(text) or "").strip().upper()
        if arg != "DRIVE":
            self.reply(
                sender_phone,
                "❌ Uso: AUTORIZAR DRIVE\n"
                "Ejemplo: AUTORIZAR DRIVE",
            )
            return

        from src.core.exceptions import CredentialNotFoundError

        try:
            self.drive.credentials.get_google_oauth()
        except CredentialNotFoundError:
            self.reply(
                sender_phone,
                "❌ No hay client_secret de Google OAuth configurado.\n"
                "Configure primero con:\n"
                "  python -m src.core.credential_manager\n"
                "  (opción: Google OAuth)\n\n"
                "Necesita el archivo `client_secret.json` del proyecto GCP.",
            )
            return

        self.reply(
            sender_phone,
            "🔐 Iniciando autorización Google Drive...\n"
            "Se abrirá el navegador en el servidor.\n"
            "Recibirá confirmación cuando esté listo.",
        )

        def _callback(result, error):
            if error:
                self.reply(
                    sender_phone,
                    f"❌ Error autorizando Google Drive:\n{error}",
                )
                self.log.exception("error en flujo OAuth Drive")
            else:
                self.reply(
                    sender_phone,
                    "✅ *Google Drive autorizado correctamente.*\n"
                    "Los archivos procesados se sincronizarán automáticamente.",
                )
                self.log.info("Drive autorizado por operador %s", sender_phone)

        self.drive.authorize_async(_callback)

    # ── PAGAR ─────────────────────────────────────────────────────────────────

    def _handle_pagar(self, sender_phone: str, text: str) -> None:
        """PAGAR — registra el número de entero BCR y avanza el expediente.

        El entero bancario se paga en el BCR por el topógrafo (persona física).
        El bot solo registra el número que le envía el operador y avanza el flujo.

        Uso:
          PAGAR <expediente> <numero_entero>

        Ejemplos:
          PAGAR SEG-2026-001 20261234
          PAGAR SEG-2026-001 2026-1234-5
        """
        from src.models.estado import Estado

        arg = self._extract_arg(text)
        if not arg:
            self.reply(
                sender_phone,
                "❌ Uso: *PAGAR <expediente> <numero_entero>*\n\n"
                "Ejemplo: `PAGAR SEG-2026-001 20261234`\n\n"
                "El número de entero aparece en el comprobante del BCR.",
            )
            return

        partes = arg.split(maxsplit=1)
        numero        = partes[0]
        numero_entero = partes[1].strip() if len(partes) > 1 else None

        exp = self.db.buscar_por_numero(numero)
        if not exp:
            self.reply(sender_phone, f"❌ Expediente {numero!r} no encontrado.")
            return

        estado_actual = exp["estado_actual"]
        if estado_actual not in {Estado.FORMATO_VALIDADO.value, Estado.RECIBIDO.value}:
            self.reply(
                sender_phone,
                f"⚠️ El expediente *{numero}* está en estado *{estado_actual}*.\n"
                f"PAGAR solo aplica cuando el plano está en formato_validado.",
            )
            return

        if not numero_entero:
            self.reply(
                sender_phone,
                f"❌ Falta el número de entero BCR.\n\n"
                f"Uso: *PAGAR {numero} <numero_entero>*\n"
                f"Ejemplo: `PAGAR {numero} 20261234`",
            )
            return

        actor = f"whatsapp:{sender_phone}"

        # Guardar número de entero en metadata y marcar como confirmado
        self.db.actualizar_metadata(
            exp["id"],
            {"numero_entero": numero_entero, "entero_confirmado": True},
            actor=actor,
        )

        # Resolver la acción pendiente "registrar_entero" si existe
        try:
            pendientes = self.db.acciones_pendientes(expediente_id=exp["id"])
            for ap in pendientes:
                if ap.get("tipo_accion") == "registrar_entero":
                    self.db.resolver_accion(
                        ap["id"],
                        "confirmada",
                        whatsapp_response=f"PAGAR {numero} {numero_entero}",
                        actor=actor,
                    )
                    break
        except Exception:
            self.log.exception("no se pudo resolver accion registrar_entero para %s", numero)

        # Avanzar a ENTEROS_PAGADOS
        try:
            self.db.cambiar_estado(
                exp["id"],
                Estado.ENTEROS_PAGADOS.value,
                actor=actor,
                detalles=f"entero BCR registrado: {numero_entero}",
            )
        except Exception as exc:
            self.reply(sender_phone, f"❌ Error avanzando estado: {exc}")
            return

        self.reply(
            sender_phone,
            f"✅ *Entero registrado — {numero}*\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"Número de entero: *{numero_entero}*\n"
            f"Estado: *{Estado.ENTEROS_PAGADOS.value}*\n\n"
            f"El flujo continúa automáticamente con la subida a APT.",
        )
        self.log.info(
            "PAGAR — expediente: %s | entero: %s | por: %s",
            numero, numero_entero, sender_phone,
        )

    # ── APT ───────────────────────────────────────────────────────────────────

    def _handle_apt_cmd(self, sender_phone: str, text: str) -> None:
        """Subcomandos APT.

        APT SESION              — login con Firma Digital BCR
        APT ABRIR               — abrir portal en navegador visible
        APT ABRIR <url>         — abrir URL específica del portal
        APT CREAR <expediente>  — crear nuevo contrato en APT (llena los datos)
        APT TRAMITE <exp> <num> — registrar número de trámite manualmente
        """
        raw_arg = self._extract_arg(text) or ""
        subcmd = raw_arg.upper()
        self.log.info(
            "APT cmd — text=%r | raw_arg=%r | subcmd=%r", text, raw_arg, subcmd
        )
        if subcmd == "SESION":
            pass  # handled below
        elif subcmd == "SESION OK":
            # Operador confirma manualmente que ya inició sesión APT con Firma Digital
            if self.apt is None:
                self.reply(sender_phone, "❌ APTAgent no disponible.")
                return
            self.apt.confirmar_sesion_desde_whatsapp()
            self.reply(
                sender_phone,
                "🔄 Señal recibida — verificando sesión APT...\n"
                "Recibirá confirmación en unos segundos.",
            )
            return
        elif subcmd.startswith("TRAMITE"):
            # APT TRAMITE <numero_expediente> <numero_tramite>
            partes = subcmd.split()
            if len(partes) < 3:
                self.reply(sender_phone, "❌ Uso: APT TRAMITE <expediente> <numero_tramite>")
                return
            self._handle_apt_tramite(sender_phone, partes[1], partes[2])
            return
        elif subcmd.startswith("ABRIR"):
            # APT ABRIR [url]
            partes = text.split(maxsplit=2)
            url = partes[2] if len(partes) >= 3 else ""
            def notif(msg: str) -> None:
                self.reply(sender_phone, msg)
            import threading as _threading2
            def _run_abrir() -> None:
                try:
                    self.apt.abrir_portal(url=url, notificar_fn=notif)
                except Exception as exc:
                    self.log.exception("APT ABRIR error: %s", exc)
                    self.reply(sender_phone, f"❌ Error abriendo portal APT: {exc}")
            t2 = _threading2.Thread(target=_run_abrir, name="apt-abrir", daemon=True)
            t2.start()
            return
        elif subcmd.startswith("REGLA "):
            # APT REGLA <texto libre> — guarda nota que el bot mostrará en pre-flight
            descripcion = text.split(maxsplit=2)[2] if len(text.split(maxsplit=2)) >= 3 else ""
            if not descripcion.strip():
                self.reply(sender_phone, "❌ Uso: APT REGLA <texto descriptivo>")
                return
            try:
                mid = self.db.agregar_memoria_operador(
                    tipo="regla",
                    patron=descripcion.strip(),  # patron = texto completo
                    descripcion=descripcion.strip(),
                    operador=sender_phone,
                    actor=f"whatsapp:{sender_phone}",
                )
                self.reply(
                    sender_phone,
                    f"✅ Regla #{mid} registrada.\n"
                    f"Se mostrará en cada reporte pre-flight.\n\n"
                    f"Para verla: *APT REGLAS*\n"
                    f"Para desactivarla: *APT OLVIDA {mid}*",
                )
            except Exception as exc:
                self.reply(sender_phone, f"❌ Error registrando regla: {exc}")
            return
        elif subcmd.startswith("IGNORA "):
            # APT IGNORA <patron> — silencia discrepancias que contengan el patrón
            patron = text.split(maxsplit=2)[2] if len(text.split(maxsplit=2)) >= 3 else ""
            if not patron.strip():
                self.reply(
                    sender_phone,
                    "❌ Uso: APT IGNORA <patron>\n"
                    "Ejemplos:\n"
                    "  *APT IGNORA 2-0440-0388* — silencia esa cédula\n"
                    "  *APT IGNORA tipo:protocolo_diferente_al_activo* — silencia tipo entero",
                )
                return
            try:
                mid = self.db.agregar_memoria_operador(
                    tipo="ignora",
                    patron=patron.strip(),
                    operador=sender_phone,
                    actor=f"whatsapp:{sender_phone}",
                )
                self.reply(
                    sender_phone,
                    f"✅ Ignora #{mid} registrada — patrón: '{patron.strip()}'\n"
                    f"Las próximas discrepancias matcheantes NO se notificarán.\n"
                    f"(siguen registrándose en metadata para auditoría)\n\n"
                    f"Para verla: *APT IGNORAS*\n"
                    f"Para reactivar notificaciones: *APT OLVIDA {mid}*",
                )
            except Exception as exc:
                self.reply(sender_phone, f"❌ Error registrando ignora: {exc}")
            return
        elif subcmd == "REGLAS":
            reglas = self.db.listar_memoria_operador(tipo="regla", activa=True)
            if not reglas:
                self.reply(sender_phone, "(sin reglas activas)")
                return
            lineas = ["📋 *Reglas activas del operador*\n"]
            for r in reglas:
                lineas.append(f"  #{r['id']}: {r.get('descripcion') or r['patron']}")
            self.reply(sender_phone, "\n".join(lineas))
            return
        elif subcmd == "IGNORAS":
            ignoras = self.db.listar_memoria_operador(tipo="ignora", activa=True)
            if not ignoras:
                self.reply(sender_phone, "(sin patrones de ignora activos)")
                return
            lineas = ["🔇 *Patrones de ignora activos*\n"]
            for ig in ignoras:
                lineas.append(f"  #{ig['id']}: {ig['patron']}")
            self.reply(sender_phone, "\n".join(lineas))
            return
        elif subcmd.startswith("OLVIDA"):
            # APT OLVIDA <id> — desactiva regla o ignora
            partes = subcmd.split()
            if len(partes) < 2:
                self.reply(sender_phone, "❌ Uso: APT OLVIDA <id>")
                return
            try:
                mid = int(partes[1])
            except ValueError:
                self.reply(sender_phone, "❌ <id> debe ser un número entero")
                return
            ok = self.db.desactivar_memoria_operador(mid, actor=f"whatsapp:{sender_phone}")
            if ok:
                self.reply(sender_phone, f"✅ Memoria #{mid} desactivada.")
            else:
                self.reply(sender_phone, f"❌ Memoria #{mid} no encontrada.")
            return
        elif subcmd.startswith("CREAR"):
            # APT CREAR <numero_expediente>
            partes = subcmd.split()
            if len(partes) < 2:
                self.reply(sender_phone, "❌ Uso: APT CREAR <numero_expediente>")
                return
            numero = partes[1]
            exp = self.db.buscar_por_numero(numero.upper())
            if not exp:
                self.reply(sender_phone, f"❌ Expediente {numero} no encontrado.")
                return
            def notif_crear(msg: str) -> None:
                self.reply(sender_phone, msg)
            import threading as _threading3
            def _run_crear() -> None:
                try:
                    tramite = self.apt.crear_contrato(exp["id"], notificar_fn=notif_crear)
                    # Guardar en metadata automáticamente
                    self.db.actualizar_metadata(
                        exp["id"],
                        {"apt_tramite": tramite},
                        actor=f"apt.crear_contrato:{sender_phone}",
                    )
                    self.reply(
                        sender_phone,
                        f"✅ *{display_proyecto(exp)}* — Contrato APT creado.\n"
                        f"Trámite: *{tramite}*\n"
                        "Listo para subir archivos.",
                    )
                except Exception as exc:
                    self.log.exception("APT CREAR error: %s", exc)
                    self.reply(sender_phone, f"❌ Error creando contrato APT: {exc}")
            t3 = _threading3.Thread(target=_run_crear, name="apt-crear", daemon=True)
            t3.start()
            return
        else:
            self.reply(
                sender_phone,
                "❌ Subcomando no reconocido.\n"
                "Comandos APT disponibles:\n"
                "  *APT SESION* — autenticarse con Firma Digital BCR\n"
                "  *APT ABRIR* — abrir portal en navegador visible\n"
                "  *APT CREAR <expediente>* — crear contrato nuevo en APT\n"
                "  *APT TRAMITE <expediente> <numero>* — registrar número de trámite\n"
                "  *APT REGLA <texto>* — guardar nota operativa (sale en reportes)\n"
                "  *APT IGNORA <patrón>* — silenciar discrepancias matcheantes\n"
                "  *APT REGLAS* — listar reglas activas\n"
                "  *APT IGNORAS* — listar patrones de ignora activos\n"
                "  *APT OLVIDA <id>* — desactivar regla/ignora",
            )
            return

        if self.apt is None:
            self.reply(sender_phone, "❌ APTAgent no disponible.")
            return

        self.reply(
            sender_phone,
            "🖥️ Iniciando navegador portal APT...\n"
            "Ingrese con su *Firma Digital BCR* cuando se abra el navegador.\n"
            "Tiene 10 minutos. El bot avisará cuando detecte el login.",
        )

        def notif(msg: str) -> None:
            self.reply(sender_phone, msg)

        import threading as _threading  # noqa: PLC0415

        def _run() -> None:
            try:
                self.apt.iniciar_sesion_manual(notificar_fn=notif, timeout_min=10)
            except Exception as exc:
                self.log.exception("APT SESION error: %s", exc)
                self.reply(sender_phone, f"❌ Error en sesión APT: {exc}")

        t = _threading.Thread(target=_run, name="apt-sesion-manual", daemon=True)
        t.start()

    def _handle_apt_tramite(
        self, sender_phone: str, numero_exp: str, numero_tramite: str
    ) -> None:
        """APT TRAMITE <expediente> <numero> — registra el número de trámite APT."""
        exp = self.db.buscar_por_numero(numero_exp.upper())
        if not exp:
            self.reply(sender_phone, f"❌ Expediente {numero_exp} no encontrado.")
            return
        self.db.actualizar_metadata(
            exp["id"],
            {
                "apt_tramite": numero_tramite,
                "apt_tramite_solicitado": False,
            },
            actor=sender_phone,
        )
        self.reply(
            sender_phone,
            f"✅ *{display_proyecto(exp)}* — Trámite APT registrado: *{numero_tramite}*\n"
            "El bot subirá los archivos automáticamente en el próximo ciclo.",
        )
        self.log.info(
            "APT TRAMITE registrado — exp: %s | tramite: %s | por: %s",
            exp["numero_expediente"], numero_tramite, sender_phone,
        )

    # ── CORREGIR ──────────────────────────────────────────────────────────────

    def _handle_corregir(self, sender_phone: str, text: str) -> None:
        """CORREGIR — aplica una corrección de texto al DWG del expediente.

        Útil cuando el APT detectó un error en un valor sanitizado (ej. número
        de finca) que la IA no pudo extraer, o para ajustar manualmente cualquier
        texto en el plano.

        Uso:
          CORREGIR <expediente> <campo> <valor_incorrecto> <valor_correcto>

        Ejemplos:
          CORREGIR SEG-2026-001 finca 1-12345-678 1-12346-000
          CORREGIR SEG-2026-001 area 1200.50 1250.75
          CORREGIR SEG-2026-001 canton "San Ramon" "Palmares"

        Notas:
          - El campo es solo descriptivo (no filtra qué entidad cambiar).
          - Se aplica a TODAS las entidades TEXT/MTEXT que contengan
            el valor_incorrecto en el DWG del expediente.
          - El archivo corregido se guarda como <nombre>_corr.dxf.
        """
        from src.utils.dwg_corrector import CorreccionTexto, aplicar_correcciones

        arg = self._extract_arg(text)
        if not arg:
            self.reply(
                sender_phone,
                "❌ Uso: *CORREGIR <expediente> <campo> <valor_incorrecto> <valor_correcto>*\n\n"
                "Ejemplo: `CORREGIR SEG-2026-001 finca 1-12345-678 1-12346-000`\n"
                "Ejemplo: `CORREGIR SEG-2026-001 area 1200.50 1250.75`",
            )
            return

        # Parsear: "EXP-001 campo viejo nuevo" — campo puede contener espacios si se usa comillas
        # Para simplicidad: primer token = expediente, segundo = campo, resto dividido en 2 mitades
        partes = arg.split()
        if len(partes) < 4:
            self.reply(
                sender_phone,
                "❌ Se necesitan 4 argumentos: expediente, campo, valor_incorrecto, valor_correcto.\n\n"
                "Uso: *CORREGIR <expediente> <campo> <valor_incorrecto> <valor_correcto>*",
            )
            return

        numero        = partes[0]
        campo         = partes[1]
        valor_actual  = partes[2]
        valor_correcto = partes[3]

        # Si hay más tokens, unirlos al valor_correcto (permite espacios sin comillas)
        if len(partes) > 4:
            valor_correcto = " ".join(partes[3:])

        exp = self.db.buscar_por_numero(numero)
        if not exp:
            self.reply(sender_phone, f"❌ Expediente {numero!r} no encontrado.")
            return

        # Buscar el DWG del expediente
        try:
            archivos = self.db.archivos_de(exp["id"])
        except Exception:
            archivos = []

        from pathlib import Path as _Path
        archivo_dwg = next(
            (
                a for a in archivos
                if a.get("tipo_archivo") in ("dwg", "anverso")
                   and _Path(a.get("ruta_local", "")).suffix.lower() in (".dwg", ".dxf")
            ),
            None,
        )
        if not archivo_dwg:
            self.reply(
                sender_phone,
                f"⚠️ No hay archivo DWG/DXF registrado para *{numero}*.\n"
                f"El topógrafo debe subir el DWG antes de aplicar correcciones.",
            )
            return

        dwg_path = _Path(archivo_dwg["ruta_local"])
        if not dwg_path.is_file():
            self.reply(
                sender_phone,
                f"⚠️ El archivo DWG no está disponible localmente: {dwg_path.name}",
            )
            return

        corr = CorreccionTexto(
            campo=campo,
            valor_actual=valor_actual,
            valor_correcto=valor_correcto,
        )
        resultado = aplicar_correcciones(dwg_path, [corr])

        actor = f"whatsapp:{sender_phone}"

        if resultado.error:
            self.reply(sender_phone, f"❌ Error al procesar el DWG: {resultado.error}")
            return

        if resultado.modificado:
            # Registrar el DXF corregido en BD
            try:
                import hashlib as _hashlib
                _sha = (
                    _hashlib.sha256(resultado.archivo_corregido.read_bytes()).hexdigest()
                    if resultado.archivo_corregido.is_file()
                    else "0" * 64
                )
                self.db.registrar_archivo(
                    expediente_id=exp["id"],
                    nombre_original=resultado.archivo_corregido.name,
                    tipo_archivo="dwg_corregido",
                    ruta_local=str(resultado.archivo_corregido),
                    fase="correcciones_apt",
                    sha256=_sha,
                    actor=actor,
                )
            except Exception:
                self.log.exception(
                    "no se pudo registrar DXF corregido para %s", numero
                )

            # Actualizar metadata
            try:
                meta = json.loads(exp.get("metadata_json") or "{}")
                aplicadas_prev = meta.get("correcciones_dwg_aplicadas", [])
                aplicadas_prev.append({
                    "campo": campo,
                    "de": valor_actual,
                    "a": valor_correcto,
                    "por": sender_phone,
                    "ts": datetime.now(timezone.utc).isoformat(),
                })
                self.db.actualizar_metadata(
                    exp["id"],
                    {"correcciones_dwg_aplicadas": aplicadas_prev},
                    actor=actor,
                )
            except Exception:
                self.log.exception("no se pudo actualizar metadata para %s", numero)

            self.reply(
                sender_phone,
                f"✅ *Corrección aplicada — {numero}*\n"
                f"━━━━━━━━━━━━━━━━━\n"
                f"Campo: *{campo}*\n"
                f"Cambio: «{valor_actual}» → «{valor_correcto}»\n"
                f"Archivo: *{resultado.archivo_corregido.name}*\n\n"
                f"Revise el DXF corregido. Cuando esté conforme:\n"
                f"*APROBAR {numero}*",
            )
        else:
            self.reply(
                sender_phone,
                f"⚠️ *No se encontró «{valor_actual}»* en ninguna entidad "
                f"TEXT/MTEXT del DWG de *{numero}*.\n\n"
                f"Verifique que el valor está escrito exactamente igual (mayúsculas, "
                f"espacios, unidades) en el archivo {dwg_path.name}.",
            )

        self.log.info(
            "CORREGIR — exp: %s | campo: %s | «%s»→«%s» | modificado: %s | por: %s",
            numero, campo, valor_actual, valor_correcto, resultado.modificado, sender_phone,
        )

    # ── AYUDA ─────────────────────────────────────────────────────────────────

    def _handle_ayuda(self, sender_phone: str, rol: str = "admin") -> None:
        # Comandos disponibles para el rol actual
        permisos  = _ROL_PERMISOS.get(rol, set())
        disponibles = []
        if "NUEVO" in permisos:
            disponibles.append(
                "*NUEVO* — crear expediente:\n"
                "```\nNUEVO PLANO\n"
                "tipo: segregacion\n"
                "expediente: 12345-2026\n"
                "topografo: Nombre Apellido\n"
                "telefono: +50688887777\n"
                "cliente: Nombre Cliente\n"
                "area: 450\n"
                "carta_agua: si\n```\n\n"
                "*NUEVO CONTRATO* — crear desde contrato (número auto):\n"
                "```\nNUEVO CONTRATO\n"
                "Tipo: segregacion\n"
                "Cliente: Nombre Propietario\n"
                "Cedula: 2-0000-0000\n"
                "Proyecto: Nombre Finca\n"
                "Finca: 123456\n"
                "Protocolo: 789\n"
                "Planos: 1\n```"
            )
        if "ESTADO" in permisos:
            disponibles.append(
                "*ESTADO* `<numero>` — estado del expediente\n"
                "*ESTADO APT* `<numero>` — consultar portal APT"
            )
        if "APROBAR" in permisos:
            disponibles.append("*APROBAR* `<numero>` — avanzar expediente bloqueado")
        if "RECHAZAR" in permisos:
            disponibles.append("*RECHAZAR* `<numero>` — cancelar expediente")
        if "BUSCAR" in permisos:
            disponibles.append("*BUSCAR* `<texto>` — buscar por número/topógrafo/cliente")
        if "SUBIR" in permisos:
            disponibles.append("*SUBIR* `<numero>` — confirmar archivos listos")
        if "RECIBIR" in permisos:
            disponibles.append("*RECIBIR* `<numero>` — marcar plano entregado al cliente")
        if "RESUMEN" in permisos:
            disponibles.append("*RESUMEN* — expedientes activos del día")
        if "AVISAR" in permisos:
            disponibles.append("*AVISAR* `<numero>` `<mensaje>` — mensaje al cliente")
        if "CERRAR" in permisos:
            disponibles.append("*CERRAR APT* — cerrar sesión APT / liberar token BCR")
        if "AUTORIZAR" in permisos:
            disponibles.append("*AUTORIZAR DRIVE* — autorizar Google Drive (abre navegador en el servidor)")
        if "PAGAR" in permisos:
            disponibles.append(
                "*PAGAR* — registrar número de entero BCR:\n"
                "  `PAGAR SEG-2026-001 20261234`\n\n"
                "_El entero se paga en el BCR (persona física). "
                "El número aparece en el comprobante BCR._"
            )
        if "CORREGIR" in permisos:
            disponibles.append(
                "*CORREGIR* — corregir texto en el DWG del expediente:\n"
                "  `CORREGIR SEG-2026-001 finca 1-12345-678 1-12346-000`\n"
                "  `CORREGIR SEG-2026-001 area 1200.50 1250.75`\n\n"
                "_Aplica cuando APT detecta un error de texto (finca, área, cantón, etc.)._\n"
                "_El DXF corregido queda como {nombre}\\_corr.dxf en la carpeta del expediente._"
            )
        # DEBUG está siempre disponible para todos los roles (sólo lectura)
        disponibles.append(
            "*DEBUG* `<numero>` — dump del estado interno del bot:\n"
            "  progreso bP1-bP7, trámite, discrepancias, anomalías,\n"
            "  snapshots disponibles, reglas/ignoras del operador."
        )

        tipos = "segregacion · rectificacion · informacion_posesoria · reunion_de_fincas · fincas_completas"
        rol_emoji = {"admin": "👑", "topografo": "📐", "asistente": "📋"}.get(rol, "👤")

        self.reply(
            sender_phone,
            f"🤖 *catastro-bot — comandos*\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"Rol: {rol_emoji} *{rol}*\n\n"
            + "\n\n".join(disponibles)
            + f"\n\n📐 Tipos de plano:\n{tipos}",
        )

    # ── LOTE ──────────────────────────────────────────────────────────────────

    def _handle_lote(self, sender_phone: str, text: str) -> None:
        """Procesa comandos LOTE — orquestador serial multi-expediente.

        Subcomandos:
          LOTE APT-CREAR <exp1> <exp2> ...   → crea lote + código 2FA
          LOTE APT-PLANO <exp1> <exp2> ...   → idem para bP1-bP7
          LOTE ESTADO <lote_id>              → progreso
          LOTE LISTAR                        → últimos 10 lotes
          LOTE CANCELAR <lote_id>            → cancela items pendientes
          LOTE CONFIRMAR <lote_id> <codigo>  → autoriza vía 2FA

        Las acciones bloqueadas (apt-guardar / enviar-cfia) NO se pueden
        ejecutar en lote — se devuelve error explicando por qué.
        """
        from src.core.lote_manager import (
            LoteManager, LoteValidationError, LoteNotFoundError,
            LoteStateError, ACCIONES_PERMITIDAS, ACCIONES_BLOQUEADAS,
        )

        partes = text.strip().split()
        if len(partes) < 2:
            self.reply(sender_phone,
                       "Uso:\n"
                       "  LOTE APT-CREAR <exp1> <exp2> ...\n"
                       "  LOTE APT-PLANO <exp1> <exp2> ...\n"
                       "  LOTE ESTADO <lote_id>\n"
                       "  LOTE LISTAR\n"
                       "  LOTE CANCELAR <lote_id>\n"
                       "  LOTE CONFIRMAR <lote_id> <codigo>")
            return

        sub = partes[1].upper()
        # Reuso el TwoFactorAuth del bot si está disponible — si no, uno fresh
        auth = getattr(self, "_two_factor", None)
        lm = LoteManager(db=self.db, two_factor_auth=auth)

        # ── LISTAR ─────────────────────────────────────────────────────
        if sub == "LISTAR":
            lotes = lm.listar_lotes(limit=10)
            if not lotes:
                self.reply(sender_phone, "(sin lotes recientes)")
                return
            lineas = ["📋 *Últimos lotes:*"]
            for l in lotes:
                lineas.append(
                    f"• `{l['id']}` — {l['accion']} — {l['estado']}"
                )
            self.reply(sender_phone, "\n".join(lineas))
            return

        # ── ESTADO ─────────────────────────────────────────────────────
        if sub == "ESTADO":
            if len(partes) < 3:
                self.reply(sender_phone, "Uso: LOTE ESTADO <lote_id>")
                return
            lote_id = partes[2]
            try:
                lote = lm.estado_lote(lote_id)
            except LoteNotFoundError:
                self.reply(sender_phone, f"❌ Lote {lote_id} no existe.")
                return
            items = lm.listar_items(lote_id)
            prog = lm.progreso(lote_id)
            lineas = [
                f"📋 *Lote {lote_id}*",
                f"Acción: {lote['accion']}",
                f"Estado: *{lote['estado']}*",
                f"Items: {prog['total']} "
                f"(✓{prog['ok']} ✗{prog['fallos']} "
                f"·{prog['pendientes']} —{prog['cancelados']})",
                "",
            ]
            for it in items:
                marca = {"ok": "✓", "fallo": "✗", "pendiente": "·",
                         "ejecutando": "▶", "cancelado": "—"}.get(
                    it["estado"], "?")
                num = it.get("numero_expediente") or it["expediente_id"]
                err = f" — {it['error']}" if it.get("error") else ""
                lineas.append(f"  {marca} {num}{err}")
            self.reply(sender_phone, "\n".join(lineas))
            return

        # ── CANCELAR ───────────────────────────────────────────────────
        if sub == "CANCELAR":
            if len(partes) < 3:
                self.reply(sender_phone, "Uso: LOTE CANCELAR <lote_id>")
                return
            lote_id = partes[2]
            try:
                res = lm.cancelar_lote(lote_id=lote_id, actor=sender_phone)
            except LoteNotFoundError:
                self.reply(sender_phone, f"❌ Lote {lote_id} no existe.")
                return
            except LoteStateError as exc:
                self.reply(sender_phone, f"❌ {exc}")
                return
            self.reply(
                sender_phone,
                f"✅ Lote cancelado. "
                f"{len(res['cancelados'])} item(s) marcados como cancelados.",
            )
            return

        # ── CONFIRMAR ──────────────────────────────────────────────────
        if sub == "CONFIRMAR":
            if len(partes) < 4:
                self.reply(sender_phone,
                           "Uso: LOTE CONFIRMAR <lote_id> <codigo>")
                return
            lote_id, codigo = partes[2], partes[3]
            try:
                ok = lm.confirmar_lote(lote_id=lote_id,
                                       codigo=codigo, actor=sender_phone)
            except LoteNotFoundError:
                self.reply(sender_phone, f"❌ Lote {lote_id} no existe.")
                return
            except LoteStateError as exc:
                self.reply(sender_phone, f"❌ {exc}")
                return
            if ok:
                self.reply(
                    sender_phone,
                    f"✅ Lote {lote_id} confirmado.\n"
                    f"El bot lo procesará en el próximo tick.",
                )
            else:
                self.reply(
                    sender_phone,
                    "❌ Código inválido o expirado. "
                    "Genera un lote nuevo si es necesario.",
                )
            return

        # ── APT-CREAR / APT-PLANO (sub debe ser acción válida) ─────────
        accion = sub.lower()
        if accion in ACCIONES_BLOQUEADAS:
            self.reply(
                sender_phone,
                f"❌ Acción *{accion}* bloqueada en lote:\n"
                f"{ACCIONES_BLOQUEADAS[accion]}\n\n"
                f"Usa el comando individual con su propio 2FA.",
            )
            return
        if accion not in ACCIONES_PERMITIDAS:
            self.reply(
                sender_phone,
                f"❌ Subcomando LOTE {sub} desconocido.\n"
                f"Permitidos: {' '.join(sorted(ACCIONES_PERMITIDAS)).upper()}\n"
                f"Y: ESTADO, LISTAR, CANCELAR, CONFIRMAR",
            )
            return

        # partes[2:] = lista de expedientes
        expedientes = partes[2:]
        if len(expedientes) < 2:
            self.reply(
                sender_phone,
                "❌ Un lote requiere al menos 2 expedientes.\n"
                "Para uno solo usa el comando individual.",
            )
            return

        try:
            lote = lm.crear_lote(
                accion=accion,
                expedientes_ids=expedientes,
                creado_por=sender_phone,
            )
        except LoteValidationError as exc:
            lineas = [f"❌ Validación falló: {exc}"]
            if exc.detalles_por_exp:
                lineas.append("")
                for exp, det in exc.detalles_por_exp.items():
                    marca = "✓" if det["ok"] else "✗"
                    razon = f" — {det.get('razon', '')}" if not det["ok"] else ""
                    lineas.append(f"  {marca} {exp}{razon}")
            self.reply(sender_phone, "\n".join(lineas))
            return

        lineas = [
            f"📋 *Lote creado:* `{lote.id}`",
            f"Acción: *{lote.accion}*",
            f"Items: {len(lote.items)}",
            "",
        ]
        for it in lote.items:
            num = it.get("numero_expediente") or it["expediente_id"]
            lineas.append(f"  [{it['orden']}] {num}")
        if lote.codigo_2fa:
            lineas.append("")
            lineas.append(f"🔐 *Código 2FA:* `{lote.codigo_2fa}`")
            lineas.append("(válido 5 minutos)")
            lineas.append("")
            lineas.append(f"Para autorizar responde:")
            lineas.append(f"`LOTE CONFIRMAR {lote.id} {lote.codigo_2fa}`")
        self.reply(sender_phone, "\n".join(lineas))

    # ── helpers ───────────────────────────────────────────────────────────────

    def _extract_arg(self, text: str) -> Optional[str]:
        parts = text.strip().split(maxsplit=1)
        if len(parts) < 2:
            return None
        return parts[1].strip()
