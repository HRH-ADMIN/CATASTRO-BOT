"""Agente para Municipalidad de San Ramón — Formulario APT-PUBLICO.

Flujo real:
  - Presentación inicial: Google Form (no hay portal web dedicado).
    El bot construye una URL pre-rellenada y se la envía al topógrafo por
    WhatsApp. El topógrafo solo debe subir los archivos y hacer clic Enviar.
  - Respuestas (aviso impuestos, visado): email desde mgamboa@sanramondigital.net
    revisado por IMAP en el buzón del bot.
  - Correcciones / Resellos: email directo a mgamboa@sanramondigital.net (SMTP).

Formulario APT-PUBLICO:
  https://docs.google.com/forms/d/e/
  1FAIpQLSejhSph15X_RPtT39SjiLUYnCqjg_FB1Gq67A8mjgA-1XVXOQ/viewform

Entry IDs (obtenidos inspeccionando FB_PUBLIC_LOAD_DATA_):
  Tomo               413935745   (text)
  Asiento            112406539   (text)
  Proyecto-APT       1184863793  (text — número de contrato APT)
  Fecha-Minuta       1421348955  (date — split _year/_month/_day)
  Área               2015524080  (text)
  Nº Finca           2090596021  (text)
  Distrito           1965655160  (dropdown)
  Proceso trámite    335206885   (dropdown)
  Tipo Acceso        990422047   (dropdown)
  Vértices           44837579    (text)
  Profesional        299608996   (text)
  Carné              1470141826  (text)
  DOCUMENTOS         702941744   (file — Drive picker, manual)
  Archivo Shape      125723641   (file — Drive picker, manual)

La credencial CRED_MUNI_SAN_RAMON almacena:
  username = email del bot (p.ej. "tramites@topo.cr")
  password = App Password IMAP/SMTP (Gmail u otro proveedor)
"""
from __future__ import annotations

import email as email_lib
import imaplib
import json
import re
import smtplib
from datetime import datetime, timezone
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

from config.settings import (
    MUNI_IMAP_HOST,
    MUNI_IMAP_PORT,
    MUNI_SAN_RAMON_EMAIL,
    MUNI_SMTP_HOST,
    MUNI_SMTP_PORT,
)
from src.agents.base_agent import BaseAgent
from src.core.exceptions import AgentError, ConfirmationError
from src.utils.logger import get_logger

# ── Constantes del formulario ─────────────────────────────────────────────────
# DEPRECATED 2026-05-14: estas constantes son fallback. La fuente de verdad
# está en config/munis.yaml. Para agregar otra muni, NO modificar este archivo
# — agregar bloque YAML en config/munis.yaml.
_FORM_ID = "1FAIpQLSejhSph15X_RPtT39SjiLUYnCqjg_FB1Gq67A8mjgA-1XVXOQ"
_FORM_BASE = f"https://docs.google.com/forms/d/e/{_FORM_ID}/viewform"


def _cargar_config_muni(slug: str = "san_ramon") -> dict:
    """Carga config de la muni desde YAML con fallback a constantes."""
    try:
        from src.utils.muni_config import cargar_muni
        cfg = cargar_muni(slug)
        if cfg:
            return cfg
    except Exception:
        pass
    return {}

_ENTRY = {
    "tomo":          "entry.413935745",
    "asiento":       "entry.112406539",
    "proyecto_apt":  "entry.1184863793",
    "fecha_year":    "entry.1421348955_year",
    "fecha_month":   "entry.1421348955_month",
    "fecha_day":     "entry.1421348955_day",
    "area":          "entry.2015524080",
    "finca":         "entry.2090596021",
    "distrito":      "entry.1965655160",
    "proceso":       "entry.335206885",
    "tipo_acceso":   "entry.990422047",
    "vertices":      "entry.44837579",
    "profesional":   "entry.299608996",
    "carne":         "entry.1470141826",
}

# Opciones válidas para dropdowns (tal cual aparecen en el formulario)
DISTRITOS = [
    "01 San Ramón", "02 Santiago", "03 San Juan", "04 Piedades Norte",
    "05 Piedades Sur", "06 San Rafael", "07 San Isidro", "08 Ángeles",
    "09 Alfaro", "10 Volio", "11 Concepción", "12 Zapotal",
    "13 Peñas Blancas", "14 San Lorenzo",
]
PROCESOS = {
    # Solo segregacion y reunion_de_fincas van a municipalidad (Ley 6545)
    "segregacion":          "Segregación",
    "reunion_de_fincas":    "Segregar y Reunir",
    # Entradas de fallback para tipos que no requieren visado municipal:
    "fincas_completas":         "Localizar Derecho",
    "rectificacion":            "Validación de Vía Pública",
    "informacion_posesoria":    "Localizar Derecho",
}
TIPOS_ACCESO = [
    "Ruta Cantonal",
    "Ruta Nacional  Nº 01", "Ruta Nacional  Nº 135", "Ruta Nacional Nº 156",
    "Ruta Nacional Nº 169", "Ruta Nacional Nº 702", "Ruta Nacional  Nº 703",
    "Ruta Nacional Nº 704", "Ruta Nacional Nº 705", "Ruta Nacional Nº 713",
    "Ruta Nacional N°725", "Ruta Nacional Nº 742",
    "Ruta Nacional Naranjo - Florencia",
    "Servidumbre de Paso", "Acceso excepcional uso residencial",
    "Servidumbre Pecuaria", "Servidumbre Forestal",
    "Servidumbre Mixta", "Servidumbre Agrícola",
]

# Email de contacto catastral de la Muni (resello/correcciones)
# CORREGIDO 2026-05-13 — antes estaba mgamboa@sanramondigital.net (incorrecto).
# El email real (confirmado en acuse oficial del Google Form) es:
_MUNI_CATASTRAL = "mgamboa@sanramon.go.cr"

_RE_IMPUESTO = re.compile(
    r"(impuesto|tributo|timbre|₡\s*[\d,.]+|monto\s+a\s+pagar)",
    re.IGNORECASE,
)
_RE_MONTO = re.compile(r"₡\s*([\d,.']+)", re.IGNORECASE)
_RE_VISADO_OK = re.compile(
    r"(visado\s+aprobado|plano\s+visado|se\s+aprueba|visto\s+bueno)",
    re.IGNORECASE,
)
_RE_VISADO_NO = re.compile(
    r"(visado\s+rechazado|se\s+rechaza|no\s+procede)",
    re.IGNORECASE,
)


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _formatear_nombre_topografo_muni(nombre_cfia: str) -> str:
    """Convierte 'ROJAS HERRERA LUIS ALONSO' → 'luis alonso rojas herrera'.

    Formato muni (aprendido 2026-05-13): minúsculas, nombres primero.
    Asume convención CFIA: APELLIDO1 APELLIDO2 NOMBRE1 NOMBRE2.
    """
    if not nombre_cfia:
        return ""
    partes = nombre_cfia.strip().split()
    if len(partes) >= 4:
        # CFIA: AP1 AP2 NOM1 NOM2 → muni: nom1 nom2 ap1 ap2
        ap1, ap2, *nombres = partes
        return " ".join(nombres + [ap1, ap2]).lower()
    if len(partes) == 3:
        ap1, ap2, nom = partes
        return f"{nom} {ap1} {ap2}".lower()
    return nombre_cfia.lower()


def _formatear_carne_topografo_muni(raw: str) -> str:
    """Convierte 'IT 10676' / 'I.T. 10676' / '10676' → 'it-10676'.

    Formato muni (aprendido 2026-05-13): minúsculas con guion.
    """
    if not raw:
        return ""
    # Extraer solo dígitos
    digits = re.sub(r"[^\d]", "", str(raw))
    if not digits:
        return str(raw).lower().strip()
    return f"it-{digits}"


def _limpiar_monto(texto: str) -> Optional[float]:
    m = _RE_MONTO.search(texto)
    if not m:
        return None
    try:
        return float(re.sub(r"[,.'']", "", m.group(1)))
    except ValueError:
        return None


class MunicipalityAgent(BaseAgent):
    name = "muni-san-ramon"

    def __init__(
        self,
        db,
        credentials,
        *,
        muni_email: str = MUNI_SAN_RAMON_EMAIL,
        muni_catastral: str = _MUNI_CATASTRAL,
        smtp_host: str = MUNI_SMTP_HOST,
        smtp_port: int = MUNI_SMTP_PORT,
        imap_host: str = MUNI_IMAP_HOST,
        imap_port: int = MUNI_IMAP_PORT,
    ):
        super().__init__(db, credentials)
        self._muni_email = muni_email
        self._muni_catastral = muni_catastral
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port
        self._imap_host = imap_host
        self._imap_port = imap_port
        self._log = get_logger("muni_agent")

    # ── credenciales ─────────────────────────────────────────────────────────

    def _bot_email(self) -> tuple[str, str]:
        return self.credentials.get_muni_san_ramon()

    # ── guard ─────────────────────────────────────────────────────────────────

    def _exigir_confirmacion(self, expediente_id: str, tipo_accion: str) -> dict:
        confirmada = self.db.accion_confirmada(
            expediente_id=expediente_id, tipo_accion=tipo_accion
        )
        if not confirmada:
            raise ConfirmationError(
                f"acción {tipo_accion!r} requiere confirmación WhatsApp "
                f"para el expediente {expediente_id!r}"
            )
        return confirmada

    # ── Pre-filled URL ────────────────────────────────────────────────────────

    def construir_url_formulario(self, expediente_id: str) -> str:
        """Construye la URL pre-rellenada del Google Form con los datos del expediente.

        Lee config muni desde config/munis.yaml (slug por defecto 'san_ramon').
        Si el YAML no existe, usa constantes hardcoded como fallback.

        El topógrafo solo debe subir los archivos (DOCUMENTOS + Shape) y
        hacer clic en Enviar.
        """
        exp = self.db.obtener_expediente(expediente_id)
        if not exp:
            raise AgentError(f"expediente {expediente_id!r} no existe")
        meta = json.loads(exp.get("metadata_json") or "{}")

        # Resolver muni slug desde metadata.canton, con fallback a san_ramon
        muni_slug = meta.get("muni_slug")
        if not muni_slug:
            try:
                from src.utils.muni_config import muni_para_canton
                muni_slug = muni_para_canton(meta.get("canton", "")) or "san_ramon"
            except Exception:
                muni_slug = "san_ramon"
        cfg_muni = _cargar_config_muni(muni_slug)
        # Form base: prefiere YAML
        form_id = (cfg_muni.get("google_form") or {}).get("form_id", _FORM_ID)
        form_base = f"https://docs.google.com/forms/d/e/{form_id}/viewform"

        # Campos de texto y dropdown
        params: dict[str, str] = {"usp": "pp_url"}

        # Tomo y Asiento vienen de APT (guardados en metadata tras la primera ronda)
        tomo    = meta.get("apt_tomo", "")
        asiento = meta.get("apt_asiento", "")
        params[_ENTRY["tomo"]]       = str(tomo)
        params[_ENTRY["asiento"]]    = str(asiento)
        params[_ENTRY["proyecto_apt"]] = str(meta.get("apt_tramite", ""))

        # Fecha de minuta (formato YYYY-MM-DD en metadata, split para el form)
        fecha_raw = meta.get("apt_fecha_minuta", "")
        if fecha_raw and len(fecha_raw) >= 10:
            try:
                dt = datetime.fromisoformat(fecha_raw[:10])
                params[_ENTRY["fecha_year"]]  = str(dt.year)
                params[_ENTRY["fecha_month"]] = str(dt.month)
                params[_ENTRY["fecha_day"]]   = str(dt.day)
            except ValueError:
                pass

        # Datos técnicos del expediente
        params[_ENTRY["area"]]    = str(meta.get("area_m2", ""))

        # Formato de finca (aprendido 2026-05-13, TILMAN):
        #   Una finca:  "2 629270-000"   (provincia + espacio + número + "-" + derecho)
        #   Varias:     "2 333333-000/2 444444-001/..."  (separadas por "/")
        # Prefiere metadata.fincas_muni si existe (lista de dicts con provincia/numero/derecho).
        # Fallback: usa finca_numero suelto con provincia=2 y derecho=000.
        fincas_muni = meta.get("fincas_muni") or []
        if not fincas_muni:
            # Construir desde datos_apt.plano.fincas si existe
            datos_apt = meta.get("datos_apt") or {}
            fincas_plano = (datos_apt.get("plano") or {}).get("fincas") or []
            for f in fincas_plano:
                fincas_muni.append({
                    "provincia": f.get("provincia", "2"),
                    "numero":    f.get("numero", ""),
                    "derecho":   f.get("derecho", "000"),
                })
        if not fincas_muni and meta.get("finca_numero"):
            # Último fallback — solo el número simple
            fincas_muni = [{
                "provincia": meta.get("provincia_codigo", "2"),
                "numero":    str(meta["finca_numero"]),
                "derecho":   meta.get("finca_derecho", "000"),
            }]
        if fincas_muni:
            params[_ENTRY["finca"]] = "/".join(
                f"{f.get('provincia', '2')} {f.get('numero', '')}-{f.get('derecho', '000')}"
                for f in fincas_muni if f.get("numero")
            )
        else:
            params[_ENTRY["finca"]] = ""

        params[_ENTRY["vertices"]] = str(meta.get("vertices", ""))

        # Datos del profesional
        # Aprendido 2026-05-13 (TILMAN):
        #   - Nombre profesional: minúsculas con nombres ANTES de apellidos.
        #     Ej: "luis alonso rojas herrera" (no "ROJAS HERRERA LUIS ALONSO" CFIA).
        #   - Carné: formato "it-NNNNN" con minúsculas y guion.
        #     Ej: "it-10676" (no "IT 10676" del cajetín).
        #   - Correo: usar parámetro Google emailAddress= (form captura email).
        nombre_topo = (
            meta.get("nombre_topografo_muni")
            or _formatear_nombre_topografo_muni(exp.get("nombre_topografo", ""))
        )
        carne_topo = (
            meta.get("carne_topografo_muni")
            or _formatear_carne_topografo_muni(meta.get("carne_topografo")
                                               or exp.get("cedula_topografo", ""))
        )
        params[_ENTRY["profesional"]] = nombre_topo
        params[_ENTRY["carne"]]       = carne_topo

        # Dropdowns
        distrito = meta.get("distrito", "01 San Ramón")
        if not any(d.startswith(distrito[:2]) for d in DISTRITOS):
            distrito = "01 San Ramón"
        params[_ENTRY["distrito"]] = distrito

        tipo_plano = exp.get("tipo_plano", "")
        proceso = PROCESOS.get(tipo_plano, "Segregación")
        params[_ENTRY["proceso"]] = proceso

        tipo_acceso = meta.get("tipo_acceso", "Ruta Cantonal")
        if tipo_acceso not in TIPOS_ACCESO:
            tipo_acceso = "Ruta Cantonal"
        params[_ENTRY["tipo_acceso"]] = tipo_acceso

        # Correo del topógrafo — Google Forms pre-llena con emailAddress=
        # cuando el form captura email. Aprendido 2026-05-13 con TILMAN.
        correo_topo = (
            meta.get("correo_topografo_muni")
            or meta.get("correo_topografo")
            or ""
        )
        if correo_topo:
            params["emailAddress"] = correo_topo

        url = f"{form_base}?{urlencode(params, encoding='utf-8')}"
        self._log.info("URL formulario muni construida para exp %s (muni=%s)",
                       expediente_id, muni_slug)
        return url

    def generar_instrucciones_formulario(self, expediente_id: str) -> str:
        """Genera el mensaje WhatsApp con la URL pre-rellenada e instrucciones."""
        exp = self.db.obtener_expediente(expediente_id)
        if not exp:
            raise AgentError(f"expediente {expediente_id!r} no existe")
        meta = json.loads(exp.get("metadata_json") or "{}")
        numero = exp["numero_expediente"]

        url = self.construir_url_formulario(expediente_id)

        # Listar archivos disponibles
        archivos = []
        for fase in ("apt_ronda1", "campo"):
            for a in self.db.archivos_de(expediente_id, fase=fase):
                archivos.append(f"  • {a['nombre_original']} ({a['fase']})")

        archivos_str = "\n".join(archivos) if archivos else "  (no hay archivos registrados aún)"

        return (
            f"📋 *Formulario Municipalidad San Ramón listo*\n"
            f"Expediente: *{numero}*\n\n"
            f"Todos los campos están pre-rellenados. Solo falta:\n"
            f"  1️⃣ Subir DOCUMENTOS como *un solo PDF* con:\n"
            f"     - Plano rechazado por APT (sello CFIA)\n"
            f"     - Plano corregido\n"
            f"     - Minuta de rechazo del Catastro\n"
            f"     - Croquis del proceso\n"
            f"     - Nota de agua (si aplica)\n"
            f"  2️⃣ Subir Archivo Shape como *ZIP* (si aplica)\n"
            f"  3️⃣ Hacer clic en *Enviar*\n\n"
            f"Archivos disponibles en sistema:\n{archivos_str}\n\n"
            f"🔗 URL del formulario:\n{url}\n\n"
            f"Responda *SI* cuando haya enviado el formulario."
        )

    # ── ensamblado PDF municipal ──────────────────────────────────────────────

    def ensamblar_pdf_muni(
        self,
        *,
        imagenminuta: Path,
        minuta: Optional[Path] = None,
        plano: Path,
        carta_agua: Optional[Path] = None,
        croquis: Optional[Path] = None,
        numero_minuta: str = "",
        dest_dir: Optional[Path] = None,
    ) -> Path:
        """Ensambla el PDF único que se envía a la Municipalidad.

        Orden de páginas (Reglamento Catastro / práctica San Ramón):
          1. imagenminuta  — imagen/resumen de la minuta APT
          2. minuta        — minuta completa de observaciones (si hay)
          3. plano         — anverso corregido (amberso.pdf o plano.pdf)
          4. carta_agua    — carta AyA (si aplica)
          5. croquis       — croquis de ubicación (si hay)

        Nombre del archivo:  ``{numero_minuta}.pdf``
        Si ``numero_minuta`` está vacío → ``documento_muni.pdf``

        El archivo se guarda en ``dest_dir`` (por defecto junto a ``plano``).

        Prioriza pypdf → fitz → shutil (solo plano).
        """
        nombre_archivo = f"{numero_minuta}.pdf" if numero_minuta else "documento_muni.pdf"
        if dest_dir is None:
            dest_dir = plano.parent
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        destino = dest_dir / nombre_archivo

        # Construir lista de PDFs a combinar (solo los que existen)
        pdfs: list[Path] = []
        for p, etiqueta in (
            (imagenminuta, "imagenminuta"),
            (minuta,       "minuta"),
            (plano,        "plano"),
            (carta_agua,   "carta_agua"),
            (croquis,      "croquis"),
        ):
            if p is None:
                continue
            p = Path(p)
            if not p.is_file():
                self._log.warning("PDF muni: archivo no encontrado (%s): %s", etiqueta, p)
                continue
            pdfs.append(p)

        if not pdfs:
            raise AgentError("ensamblar_pdf_muni: no hay archivos PDF para combinar")

        # ── Intentar con pypdf ────────────────────────────────────────────────
        try:
            from pypdf import PdfWriter   # type: ignore[import]
            writer = PdfWriter()
            for p in pdfs:
                writer.append(str(p))
            with open(destino, "wb") as fh:
                writer.write(fh)
            self._log.info(
                "PDF muni ensamblado con pypdf (%d archivos): %s",
                len(pdfs), destino.name,
            )
            return destino
        except ImportError:
            pass

        # ── Intentar con fitz (PyMuPDF) ───────────────────────────────────────
        try:
            import fitz   # type: ignore[import]
            doc = fitz.open()
            for p in pdfs:
                src = fitz.open(str(p))
                doc.insert_pdf(src)
                src.close()
            doc.save(str(destino), garbage=4, deflate=True)
            doc.close()
            self._log.info(
                "PDF muni ensamblado con fitz (%d archivos): %s",
                len(pdfs), destino.name,
            )
            return destino
        except ImportError:
            pass

        # ── Fallback: copiar solo el plano ────────────────────────────────────
        import shutil
        shutil.copy2(plano, destino)
        self._log.warning(
            "pypdf ni fitz disponibles — PDF muni es solo plano.pdf: %s",
            destino.name,
        )
        return destino

    # ── paso 12: enviar formulario ────────────────────────────────────────────

    def enviar_formulario(self, expediente_id: str) -> str:
        """Construye la URL pre-rellenada del formulario APT-PUBLICO.

        No hace submit automático (requiere subir archivos manualmente).
        Devuelve la URL como referencia para que el workflow la envíe al operador.
        """
        self._exigir_confirmacion(expediente_id, "enviar_formulario_muni")

        url = self.construir_url_formulario(expediente_id)

        # Guardar URL en metadata
        self.db.actualizar_metadata(
            expediente_id,
            {"muni_formulario_url": url, "muni_url_generada": _now_utc()},
            actor="muni_agent",
        )
        return url

    # ── paso 13: consultar aviso impuestos ────────────────────────────────────

    def consultar_aviso_impuestos(
        self, expediente_id: str
    ) -> Optional[dict]:
        """Revisa el buzón IMAP buscando aviso de impuestos de la Muni.

        Devuelve `{"monto": float, "detalle": str}` o None.
        """
        exp = self.db.obtener_expediente(expediente_id)
        if not exp:
            raise AgentError(f"expediente {expediente_id!r} no existe")

        bodies = self._buscar_imap(exp["numero_expediente"])
        for body in bodies:
            if _RE_IMPUESTO.search(body):
                monto = _limpiar_monto(body) or 0.0
                self._log.info(
                    "aviso impuestos para %s — monto: %s",
                    exp["numero_expediente"], monto
                )
                return {"monto": monto, "detalle": body[:400].strip()}
        return None

    # ── paso 14b: enviar correo pago ──────────────────────────────────────────

    def enviar_correo_pago(self, expediente_id: str) -> str:
        """Envía comprobante de pago a mgamboa@sanramondigital.net por SMTP.

        Formato de asunto según instrucciones del formulario:
        "Trámite RESELLO APT 2016 - #####"
        """
        self._exigir_confirmacion(expediente_id, "enviar_correo_muni")

        exp = self.db.obtener_expediente(expediente_id)
        if not exp:
            raise AgentError(f"expediente {expediente_id!r} no existe")

        meta = json.loads(exp.get("metadata_json") or "{}")
        tramite = meta.get("apt_tramite", exp["numero_expediente"])
        numero  = exp["numero_expediente"]

        adjuntos: list[Path] = []
        for a in self.db.archivos_de(expediente_id, fase="municipalidad"):
            if a.get("tipo_archivo") in ("comprobante_pago", "recibo"):
                p = Path(a["ruta_local"])
                if p.is_file():
                    adjuntos.append(p)

        asunto = f"Trámite RESELLO APT 2016 - {tramite}"
        cuerpo = (
            f"Estimado señor Gamboa,\n\n"
            f"Adjunto el comprobante de pago de impuestos municipales "
            f"correspondiente al trámite APT #{tramite} (expediente {numero}).\n\n"
            f"Quedo a la espera del visado.\n\n"
            f"Atentamente,\n{exp.get('nombre_topografo', 'Topógrafo')}\n"
        )

        self._enviar_smtp(
            to=self._muni_catastral,
            subject=asunto,
            body=cuerpo,
            adjuntos=adjuntos,
        )

        self.db.actualizar_metadata(
            expediente_id,
            {"muni_pago_enviado": True, "muni_pago_fecha": _now_utc()},
            actor="muni_agent",
        )
        self._log.info("correo pago enviado para tramite %s", tramite)
        return numero

    # ── paso 15: consultar visado ─────────────────────────────────────────────

    def consultar_visado(self, expediente_id: str) -> Optional[str]:
        """Revisa IMAP buscando visado aprobado o rechazado.

        Devuelve "aprobado", "rechazado" o None.
        """
        exp = self.db.obtener_expediente(expediente_id)
        if not exp:
            raise AgentError(f"expediente {expediente_id!r} no existe")

        bodies = self._buscar_imap(exp["numero_expediente"])
        for body in bodies:
            if _RE_VISADO_OK.search(body):
                self._log.info("visado aprobado para %s", exp["numero_expediente"])
                return "aprobado"
            if _RE_VISADO_NO.search(body):
                self._log.info("visado rechazado para %s", exp["numero_expediente"])
                return "rechazado"
        return None

    # ── SMTP ──────────────────────────────────────────────────────────────────

    def _enviar_smtp(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        adjuntos: list[Path] | None = None,
    ) -> None:
        from_addr, password = self._bot_email()
        msg = MIMEMultipart()
        msg["From"] = from_addr
        msg["To"] = to
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))

        for path in (adjuntos or []):
            path = Path(path)
            if not path.is_file():
                self._log.warning("adjunto no encontrado: %s", path)
                continue
            with open(path, "rb") as f:
                part = MIMEApplication(f.read(), Name=path.name)
            part["Content-Disposition"] = f'attachment; filename="{path.name}"'
            msg.attach(part)

        with smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(from_addr, password)
            smtp.sendmail(from_addr, [to], msg.as_string())

        self._log.info("correo enviado a %s — %s", to, subject)

    # ── IMAP ──────────────────────────────────────────────────────────────────

    def _buscar_imap(self, numero_expediente: str) -> list[str]:
        """Devuelve bodies de correos recibidos que contienen el número."""
        from_addr, password = self._bot_email()
        bodies: list[str] = []
        try:
            with imaplib.IMAP4_SSL(self._imap_host, self._imap_port) as imap:
                imap.login(from_addr, password)
                imap.select("INBOX")
                _, ids = imap.search(
                    None,
                    f'(FROM "{self._muni_catastral}" SUBJECT "{numero_expediente}")',
                )
                for mid in (ids[0].split() if ids[0] else []):
                    _, data = imap.fetch(mid, "(RFC822)")
                    for part in data:
                        if isinstance(part, tuple):
                            msg = email_lib.message_from_bytes(part[1])
                            txt = _extraer_texto(msg)
                            if txt:
                                bodies.append(txt)
        except (imaplib.IMAP4.error, OSError) as exc:
            self._log.warning("IMAP error buscando %s: %s", numero_expediente, exc)
        return bodies

    # ── BaseAgent ─────────────────────────────────────────────────────────────

    def run(self, expediente_id: str) -> None:
        raise NotImplementedError(
            "MunicipalityAgent es invocado por el workflow paso a paso."
        )


# ── helper ────────────────────────────────────────────────────────────────────

def _extraer_texto(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if (part.get_content_type() == "text/plain"
                    and "attachment" not in str(part.get("Content-Disposition", ""))):
                try:
                    return part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="replace"
                    )
                except Exception:
                    return ""
    else:
        try:
            return msg.get_payload(decode=True).decode(
                msg.get_content_charset() or "utf-8", errors="replace"
            )
        except Exception:
            return ""
    return ""
