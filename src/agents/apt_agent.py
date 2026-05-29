"""Agente Playwright para el portal APT-CFIA (Catastro Nacional, Costa Rica).

Portal inspeccionado: https://apt.cfia.or.cr/APT2/
SSO: https://sso.cfia.or.cr/sso/?IdSystem=1

Selectores verificados contra el portal real (2026-05-04).

== Flujo de presentacion ==
1. login()  -- usuario/contrasena CFIA en SSO
2. subir_archivos_plano()  -- sube PDFs y ZIP a la seccion Archivos del plano
3. FIRMA DIGITAL (accion manual) -- el topografo firma con su token/tarjeta
4. consultar_estado()  -- lee el estado de la tabla de consulta
5. descargar_archivos_respuesta()  -- descarga minuta/correcciones cuando hay defecto

IMPORTANTE: el paso de Firma Digital (Enviar #BtnEnviar) requiere presencia
fisica del token USB / tarjeta inteligente. El bot NO puede completar ese paso.
El bot prepara todo lo anterior y notifica al operador por WhatsApp cuando
los archivos estan listos para firmar.

Cualquier accion irreversible (presentar plano) verifica ANTES en BD que
existe una `accion_pendiente` con estado `confirmada` del tipo correspondiente
dentro de la ventana de TTL. Si no existe lanza `ConfirmationError`.
"""
from __future__ import annotations

import contextlib
import json
import threading
import time
from pathlib import Path
from typing import Optional

# HOTFIX 2026-05-22: helpers para reusar pestañas y evitar spam de about:blank.
# Ver src/utils/browser_session.py.
from src.utils.browser_session import (
    DEFAULT_APT_PATTERNS,
    cleanup_blank_tabs,
    get_or_create_apt_page,
    is_page_alive,
)

# Lock global que impide dos contextos Playwright usando el mismo perfil Chrome
# simultáneamente (Chromium mata al segundo proceso si el perfil está bloqueado).
_PROFILE_LOCK = threading.Lock()

from config.settings import (
    APT_CDP_ENDPOINT,
    APT_CONTRATO_URL,
    APT_HOME_URL,
    APT_LOGIN_URL,
    APT_PROFILE_PATH,
    APT_SESSION_PATH,
    APT_TRAMITES_URL,
    PLAYWRIGHT_HEADLESS,
    PLAYWRIGHT_SLOW_MO_MS,
)
from src.agents.base_agent import BaseAgent
from src.core.exceptions import AgentError, ConfirmationError
from src.utils.logger import get_logger


class APTSesionRequeridaError(AgentError):
    """El portal APT requiere autenticación manual con Firma Digital BCR."""


class RNPMismatchError(AgentError):
    """El nombre devuelto por el RNP no coincide con el esperado.

    Se lanza cuando el operador especifica un `nombre_esperado` para un
    propietario / titular y la respuesta automática del Registro Nacional
    a partir de la cédula trae a otra persona. Esto bloquea cualquier
    guardado para evitar enviar datos errados al CFIA.
    """


class APTAnomalyError(AgentError):
    """Algo inesperado pasó durante el llenado del formulario APT.

    Se usa como CIRCUIT BREAKER: cuando el bot detecta una situación que
    no entiende o que difiere de los flujos validados, lanza esta excepción
    para detener el ciclo y obligar al operador a intervenir.

    El runner top-level captura la excepción, dispara:
      - Notificación emergente en el escritorio del operador
      - Mensaje de WhatsApp al topógrafo (+ admins)
      - Log con stack trace
      - Sale con código de error sin tocar más datos

    Attributes:
        descripcion: una línea legible para el operador (qué pasó).
        contexto:    en qué sección/paso falló (ej: "bC5 PROYECTO").
        detalle:     información adicional para debug (URL, dump del modal, etc.)
    """

    def __init__(self, descripcion: str, *, contexto: str = "", detalle: str = ""):
        self.descripcion = descripcion
        self.contexto    = contexto
        self.detalle     = detalle
        super().__init__(f"[{contexto}] {descripcion}" if contexto else descripcion)


def _normalizar_nombre(s: str) -> str:
    """Normaliza un nombre para comparación robusta:
    mayúsculas, sin acentos/diacríticos, espacios colapsados, sin signos.
    """
    import unicodedata, re
    if not s:
        return ""
    # quitar diacríticos
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.upper()
    # quitar puntuación común
    s = re.sub(r"[.,'`´\"\\-]", " ", s)
    # colapsar espacios
    s = re.sub(r"\s+", " ", s).strip()
    return s


def validar_formato_cedula(cedula: str, tipo: str) -> tuple[bool, str]:
    """Valida que una cédula tenga el formato esperado según el tipo.

    Tipos APT:
      1 = FÍSICA       → X-XXXX-XXXX (1 + 4 + 4 = 9 dígitos)
      2 = JURÍDICA     → 3-XXX-XXXXXX (1 + 3 + 6 = 10 dígitos)
      3 = MENOR NACIONAL → mismo formato físico
      4 = CÉD. RESIDENCIA → 12-15 dígitos
      5 = CARNÉ PENSIONADO
      6 = PASAPORTE → alfanumérico, validación laxa
      7 = CARNÉ REFUGIADO

    Returns:
      (valida, motivo). Si valida=False, motivo describe el problema en
      lenguaje útil para notificar al topógrafo.
    """
    import re
    if not cedula:
        return False, "cédula vacía"
    raw = str(cedula).strip()
    digits = "".join(ch for ch in raw if ch.isdigit())
    tipo = str(tipo)

    if tipo == "1":  # FÍSICA
        if not re.match(r"^\d-\d{4}-\d{4}$", raw):
            return False, (
                f"Cédula FÍSICA mal formateada: '{raw}' "
                f"(esperado X-XXXX-XXXX, encontrados {len(digits)} dígitos)"
            )
        return True, ""

    if tipo == "2":  # JURÍDICA
        if not re.match(r"^3-\d{3}-\d{6}$", raw):
            return False, (
                f"Cédula JURÍDICA incompleta o mal formateada: '{raw}' "
                f"(esperado 3-XXX-XXXXXX, encontrados {len(digits)} dígitos)"
            )
        return True, ""

    # Tipos 3-7: validación laxa — al menos algo plausible
    if len(digits) < 5:
        return False, f"Identificación tipo {tipo} demasiado corta: '{raw}'"
    return True, ""


# ---------------------------------------------------------------------------
# Selectores verificados contra apt.cfia.or.cr/APT2  (2026-05-04)
# ---------------------------------------------------------------------------

# Fallbacks por campo crítico — usados por los helpers cuando el selector
# principal por #ID no matchea. Mantiene el bot funcionando si APT renombra
# un ID pero conserva el `name` del input (que es el binding al modelo MVC).
# Estos `name` están verificados contra el HTML real (mayo 2026).
FALLBACKS_CAMPOS_APT: dict[str, list[str]] = {
    # bC1 Propietario
    "#dllcedulasPropietario":   ["[name='Propietario.TipoIdentificacion']"],
    "#txtcedulaPropietario":    ["[name='Propietario.Cedula']"],
    "#txtnombrePropietario":    ["[name='Propietario.Nombre']"],
    "#txtApellido1Propietario": ["[name='Propietario.Apellido1']"],
    "#txtApellido2Propietario": ["[name='Propietario.Apellido2']"],
    "#txtcorreo":               ["[name='Propietario.Correo']"],
    # bC2 Contratante
    "#checkContratante":        ["[name='checkContratante']"],
    "#txtcedulaContratante":    ["[name='Contratante.Cedula']"],
    "#txtnombrecontratante":    ["[name='Contratante.Nombre']"],
    "#txtcorreocontratante":    ["[name='Contratante.Correo']"],
    # bC3 Profesional
    "#txtcorreoprofesional":    ["[name='Profesional.Correo']"],
    "#ChkNotificaProfesional":  ["[name='General.NotificaProfesional']"],
    # bC4 Protocolo
    "#dllprotocolo":            ["[name='Profesional.Protocolo']",
                                 "[name='Protocolo.Numero']"],
    "#txtfolio":                ["[name='Profesional.Folio']",
                                 "[name='Protocolo.Folio']"],
    "#ddlTipoProyectoModal":    ["[name='General.TipoProyectoModal']"],
    # bC5 Proyecto
    "#ddlTipoProyecto":         ["[name='General.TipoProyecto']"],
    "#dllProvincia":            ["[name='General.ProvinciaUbicacion']"],
    "#ddlCanton":               ["[name='General.CantonUbicacion']"],
    "#ddlDistrito":             ["[name='General.DistritoUbicacion']"],
    "#txtAreadetalle":          ["[name='General.AreaDetalle']"],
    "#dllTipoMoneda":           ["[name='General.TipoMoneda']"],
    # bC7 General
    "#txtareapredio":           ["[name='General.AreaPredio']"],
    "#txtareareal":             ["[name='General.AreaReal']"],
    "#txtValorAproximadoHonorarios": ["[name='General.ValorAproximadoHonorarios']"],
    "#txtLetras":               ["[name='General.HonorariosLetras']"],
    "#txtObservaciones":        ["[name='General.Observaciones']"],
    "#txtNorte":                ["[name='General.Norte']"],
    "#txtEste":                 ["[name='General.Este']"],
    # bC8 Firmas
    "#ddlFirmaProvincia":       ["[name='General.ProvinciaFirma']"],
    "#ddlFirmaCanton":          ["[name='General.CantonFirma']"],
    "#ddlFirmaDistrito":        ["[name='General.DistritoFirma']"],
    "#txtFechaFirma":           ["[name='General.FechaFirma']"],
    "#ChkNotificaCliente":      ["[name='General.NotificaCliente']"],
    # bP1 Plano Generales (nota: nombres exactos verificados)
    "#ddlTipoPlano":             ["[name='DatosPlano.Generales.TipoPlano']"],
    "#txtDescripcion":           ["[name='DatosPlano.Generales.Descripcion']"],
    "#ddlTipoZona":              ["[name='DatosPlano.Generales.TipoZona']"],
    "#ddlTipoUbicacion":         ["[name='DatosPlano.Generales.TipoUbicacion']"],
    "#ddlTipoUso":               ["[name='DatosPlano.Generales.TipoUso']"],
    "#ddlTamanno":               ["[name='DatosPlano.Generales.Tamanno']"],
    "#ddlTipoCoordenada":        ["[name='DatosPlano.Generales.TipoCoordenada']"],
    "#txtNorteP":                ["[name='DatosPlano.Generales.Norte']"],
    "#txtEsteP":                 ["[name='DatosPlano.Generales.Este']"],
    "#txtVertices":              ["[name='DatosPlano.Generales.Vertices']"],
    "#chkDelEstado":             ["[name='DatosPlano.Generales.DelEstado']"],
    # bP2 Fincas
    "#ddlProvinciaFinca":        ["[name='DatosPlano.Finca.Provincia']"],
    "#txtNumFinca":              ["[name='DatosPlano.Finca.Numero']"],
    "#txtDerecho":               ["[name='DatosPlano.Finca.Derecho']"],
    # bP4 Titulares
    "#ddlTipoIdentificacion":    ["[name='DatosPlano.Titular.TipoIdentificacion']"],
    "#txtIdentificacion":        ["[name='DatosPlano.Titular.Identificacion']"],
    "#ddlTitularidad":           ["[name='DatosPlano.Titular.Titularidad']"],
    "#txtNombreTitular":         ["[name='DatosPlano.Titular.Nombre']"],
    "#txtApellido1Titular":      ["[name='DatosPlano.Titular.Apellido1']"],
    "#txtApellido2Titular":      ["[name='DatosPlano.Titular.Apellido2']"],
    # bP5 Planos a modificar
    "#ddlProvinciaPlanoModificar": ["[name='DatosPlano.PlanoModificar.Provincia']"],
    "#txtNumPlanoModificar":     ["[name='DatosPlano.PlanoModificar.Numero']"],
    "#txtAnnoModificar":         ["[name='DatosPlano.PlanoModificar.Anno']"],
    # bP6 Enteros
    "#txtNumEntero":             ["[name='DatosPlano.Entero.Numero']"],
    "#FechaPago":                ["[name='DatosPlano.Entero.FechaPago']"],
    "#txtTotalCFIA":             ["[name='DatosPlano.Entero.TotalCFIA']"],
    "#txtTotalRegistro":         ["[name='DatosPlano.Entero.TotalRegistro']"],
    "#txtMontoPagado":           ["[name='DatosPlano.Entero.MontoPagado']"],
    "#txtMontoCIT_NTRIP":        ["[name='DatosPlano.Entero.MontoCITNTRIP']"],
    # bP7 Archivos
    "#ddlTipoArchivo":           ["[name='TipoArchivo']"],
    # Botones críticos
    "#BtnGuardar":               ["button[onclick*='Guardar']",
                                  "input[type='submit'][value='Guardar']"],
    "#BtnEnviar":                ["button[onclick*='Enviar']"],
}

# Login SSO
SEL_USER_INPUT      = 'input[placeholder="Usuario"]'
SEL_PASS_INPUT      = 'input[type="password"]'
SEL_LOGIN_BTN       = 'button[type="submit"]'       # texto "Ingreso"
SEL_USER_MENU       = "#navbarDropdownMenuLink"      # presente solo post-login

# Consulta de planos  (APT2/Plano/Consulta)
SEL_BUSCAR_CONTRATO = "#txtNumContrato"
SEL_TABLA_FILAS     = "table tbody tr"
# Indices de columna (0-based)
COL_TRAMITE = 1
COL_DETALLE = 2
COL_TOMO    = 4
COL_ASIENTO = 5
COL_ESTADO  = 7
COL_FECHA   = 8
COL_PROCESO = 9

# Valores de estado en la tabla
ESTADO_EN_EDICION   = "En Edición"
ESTADO_DEFECTUOSO   = "Público y Defectuoso"
ESTADO_CALIFICACION = "Calificación RN"
ESTADO_INSCRITO     = "Público e Inscrito"

# Nuevo Contrato (APT2/Contrato/Nuevo)
SEL_TAB_CONTRATO    = "#contrato-tab"
SEL_TAB_PLANO       = "#plano-tab"

# Botones acordeon del contrato  (bC*)
SEL_BTN_PROPIETARIO  = "#bC1"
SEL_BTN_CONTRATANTE  = "#bC2"
SEL_BTN_PROFESIONAL  = "#bC3"
SEL_BTN_PROTOCOLO    = "#bC4"
SEL_BTN_PROYECTO     = "#bC5"
SEL_BTN_CONTROVERSIAS = "#bC6"
SEL_BTN_ARCHIVOS_CTR = "#bC7"
SEL_BTN_FIRMAS       = "#bC8"

# Botones acordeon del plano (bP*)
SEL_BTN_GENERALES    = "#bP1"
SEL_BTN_FINCAS       = "#bP2"
SEL_BTN_SITUACION    = "#bP3"
SEL_BTN_TITULARES    = "#bP4"
SEL_BTN_PLANOS_MOD   = "#bP5"
SEL_BTN_ENTEROS      = "#bP6"
SEL_BTN_ARCHIVOS_PLA = "#bP7"

# Seccion Archivos del plano
SEL_TIPO_ARCHIVO     = "#ddlTipoArchivo"
SEL_FILE_INPUT       = "#file"
SEL_TIPO_ADJUNTO     = "#ddlTipoArchivoAdjunto"
SEL_FILE_ADJUNTO     = "#fileAdjunto"
SEL_BTN_CARGAR_ADJ   = "#btnCargarArchivoAdjunto"

# Valores del select #ddlTipoArchivo
TIPO_ANVERSO   = "1"    # ANVERSO (.pdf)
TIPO_VISADO    = "2"    # VISADO (.pdf)
TIPO_ENTERO    = "10"   # ENTERO (.pdf)
TIPO_DERROTERO = "16"   # DERROTERO (.zip)
TIPO_ADJUNTO   = "17"   # ADJUNTO (.pdf)  -- en ddlTipoArchivoAdjunto

# Guardar / Enviar
SEL_BTN_GUARDAR = "#BtnGuardar"
# BtnEnviar requiere Firma Digital -- no se ejecuta de forma automatica
SEL_BTN_ENVIAR  = "#BtnEnviar"


class APTAgent(BaseAgent):
    """Automatiza interacciones con el portal APT de CFIA via Playwright."""

    name = "apt"

    def __init__(
        self,
        db,
        credentials,
        *,
        login_url: str = APT_LOGIN_URL,
        tramites_url: str = APT_TRAMITES_URL,
        session_path: Path = APT_SESSION_PATH,        # legado, no se usa
        profile_path: Path = APT_PROFILE_PATH,        # perfil Chrome persistente
        headless: bool = PLAYWRIGHT_HEADLESS,
        slow_mo_ms: int = PLAYWRIGHT_SLOW_MO_MS,
        cdp_endpoint: str | None = APT_CDP_ENDPOINT,  # Chrome del usuario via CDP
    ):
        super().__init__(db, credentials)
        self._login_url    = login_url
        self._tramites_url = tramites_url
        self._session_path = Path(session_path)   # legado
        self._profile_path = Path(profile_path)   # perfil Chrome persistente
        self._headless     = headless
        self._slow_mo      = slow_mo_ms
        self._cdp_endpoint = cdp_endpoint   # CDP modo preferido
        self._log = get_logger("apt_agent")
        # Evento manual: el operador envía "APT SESION OK" cuando ya inició sesión
        # en el portal (útil cuando Firma Digital abre una ventana externa de Chrome)
        self._sesion_event = threading.Event()
        # Discrepancias RNP/TSE detectadas durante el llenado del contrato/plano.
        # Cada elemento es dict {ts, contexto, cedula, tse_nombre, registro_nombre}.
        # El caller las puede leer post-llenado y enviárselas al operador via
        # WhatsApp + persistirlas en metadata del expediente.
        self.discrepancias_rnp: list[dict] = []

    def reset_discrepancias_rnp(self) -> None:
        """Limpia la lista de discrepancias antes de un nuevo llenado."""
        self.discrepancias_rnp = []

    def _cdp_disponible(self) -> bool:
        """True si el Chrome del usuario está corriendo con --remote-debugging-port.

        Hace una petición HTTP a /json/version del endpoint CDP. Si responde,
        Chrome está accesible y podemos conectar via connect_over_cdp.
        """
        if not self._cdp_endpoint:
            return False
        try:
            import urllib.request
            url = f"{self._cdp_endpoint.rstrip('/')}/json/version"
            with urllib.request.urlopen(url, timeout=2) as r:
                return r.status == 200
        except Exception:
            return False

    def confirmar_sesion_desde_whatsapp(self) -> None:
        """El operador señala via WhatsApp ('APT SESION OK') que ya está logueado."""
        self._sesion_event.set()

    # -----------------------------------------------------------------------
    # Safety net
    # -----------------------------------------------------------------------

    def _exigir_confirmacion(self, expediente_id: str, tipo_accion: str) -> dict:
        """Verifica que exista confirmacion WhatsApp reciente. Lanza si no."""
        confirmada = self.db.accion_confirmada(
            expediente_id=expediente_id, tipo_accion=tipo_accion
        )
        if not confirmada:
            raise ConfirmationError(
                f"accion {tipo_accion!r} requiere confirmacion WhatsApp "
                f"reciente para el expediente {expediente_id!r}"
            )
        return confirmada

    # -----------------------------------------------------------------------
    # Playwright session — perfil Chrome persistente
    # -----------------------------------------------------------------------

    def _limpiar_lock_perfil(self) -> None:
        """Elimina archivos lock del perfil Chrome que quedan si el proceso fue forzado.

        Chromium crea Default/LOCK y SingletonLock al iniciar. Si el proceso muere
        abruptamente (force-kill), estos archivos quedan en disco e impiden que una
        nueva instancia abra el mismo perfil (exitCode=21, TargetClosedError).
        """
        for nombre in ("Default/LOCK", "SingletonLock", "SingletonCookie", "SingletonSocket"):
            lock_path = self._profile_path / nombre
            if lock_path.exists():
                with contextlib.suppress(Exception):
                    lock_path.unlink()
                    self._log.info("lock residual eliminado: %s", lock_path)

    @contextlib.contextmanager
    def _session(self):
        """Context manager que provee una pagina Playwright para operar APT.

        MODO PREFERIDO (CDP): si el Chrome del bot está corriendo en el
        puerto 9222, conectamos via DevTools Protocol y reusamos la
        ventana del usuario. Cero spam de pestañas — el bot trabaja
        adentro de la sesión que el operador ya tiene abierta.

        FALLBACK (launch_persistent_context): si NO hay CDP disponible
        (Chrome del bot caído), lanzamos un Chrome dedicado con el
        perfil persistente. Menos limpio porque cada llamada abre/cierra
        una instancia Chrome, pero la única forma de operar sin CDP.

        HOTFIX 2026-05-27: Antes este `_session` SIEMPRE usaba el
        fallback (launch_persistent_context) sin chequear CDP, lo que
        causaba spam visible de Chrome abriéndose y cerrándose en cada
        job apt-sync (cada 30 min, sobre cada expediente activo).
        Ahora SOLO usa el fallback si CDP no responde.
        """
        from playwright.sync_api import sync_playwright  # importacion diferida

        # ── MODO PREFERIDO: CDP ─────────────────────────────────────
        if self._cdp_disponible():
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(self._cdp_endpoint)
                try:
                    if not browser.contexts:
                        raise APTSesionRequeridaError(
                            "Chrome conectado sin contextos. "
                            "Ejecute APT SESION primero."
                        )
                    # Limpiar blanks acumulados antes de buscar/crear
                    with contextlib.suppress(Exception):
                        cleanup_blank_tabs(browser, max_blank=1)
                    page = get_or_create_apt_page(browser)
                    yield page
                    # Importante: NO cerramos `browser` ni `page` aca —
                    # el browser pertenece al Chrome del usuario y se
                    # debe mantener vivo. Cerrar la conexion CDP en el
                    # finally es suficiente.
                finally:
                    with contextlib.suppress(Exception):
                        browser.close()
            return

        # ── FALLBACK: launch_persistent_context ─────────────────────
        # Solo cuando CDP no responde. Cada llamada lanza un Chrome
        # nuevo — costoso, pero la única forma sin CDP.
        self._log.info(
            "_session: CDP no disponible, fallback a launch_persistent_context"
        )
        self._profile_path.mkdir(parents=True, exist_ok=True)
        self._limpiar_lock_perfil()
        with _PROFILE_LOCK:
            with sync_playwright() as p:
                ctx = p.chromium.launch_persistent_context(
                    str(self._profile_path),
                    channel="chrome",
                    headless=self._headless,
                    slow_mo=self._slow_mo,
                )
                try:
                    with contextlib.suppress(Exception):
                        cleanup_blank_tabs(ctx, max_blank=0)
                    page = get_or_create_apt_page(ctx)
                    try:
                        yield page
                    finally:
                        with contextlib.suppress(Exception):
                            ctx.close()
                except Exception:
                    with contextlib.suppress(Exception):
                        ctx.close()
                    raise

    # -----------------------------------------------------------------------
    # Login helpers
    # -----------------------------------------------------------------------

    def login(self) -> None:
        """Abre el portal CFIA y hace login.

        Intenta primero usuario/contraseña. Si el portal requiere
        Firma Digital, lanza APTSesionRequeridaError.
        """
        usuario, password = self.credentials.get_apt()
        with self._session() as page:
            page.goto(self._login_url, wait_until="load", timeout=60000)
            if self._ya_logueado(page):
                self._log.info("sesion APT reutilizada")
                return
            # Intentar login con usuario/contraseña
            if page.locator(SEL_USER_INPUT).count() > 0:
                page.fill(SEL_USER_INPUT, usuario)
                page.fill(SEL_PASS_INPUT, password)
                page.click(SEL_LOGIN_BTN)
                page.wait_for_load_state("load", timeout=60000)
                if self._ya_logueado(page):
                    self._log.info("login APT exitoso (usuario/contraseña)")
                    return
            # Sin sesión y sin formulario usuario/contraseña → Firma Digital requerida
            self._log.warning("login APT requiere Firma Digital BCR")
            raise APTSesionRequeridaError(
                "El portal APT requiere autenticación con Firma Digital BCR. "
                "Ejecute el comando APT SESION para autenticarse manualmente."
            )

    def _iniciar_sesion_manual_cdp(self, notificar_fn, deadline: float) -> None:
        """Flujo CDP — conecta al Chrome del usuario via DevTools Protocol.

        Pre-requisito: el usuario inició Chrome con tools/start-chrome-bot.bat
        (o equivalentemente con --remote-debugging-port=9222).

        Flujo:
            1. Conectar via connect_over_cdp.
            2. Buscar tab APT activa entre las pestañas existentes.
            3. Si ya hay sesión APT → confirmar y retornar.
            4. Si no, abrir tab nueva en SSO y esperar (poll + APT SESION OK).

        El Chrome del usuario tiene las apps locales (Fortify, ID Protector,
        Monitor Agent, Gaudi) que Firma Digital BCR necesita.
        """
        from playwright.sync_api import sync_playwright  # noqa: PLC0415

        if notificar_fn:
            notificar_fn(
                "🔗 *Bot conectado a su Chrome.*\n"
                "Si ya tiene la pestaña APT abierta y con sesión activa, el bot la usará.\n"
                "Si no, voy a abrir una pestaña nueva — haga *Firma Digital BCR* ahí.\n"
                "Cuando el portal APT cargue, envíe: *APT SESION OK*"
            )

        # Nota: _sesion_event ya fue limpiado al inicio de iniciar_sesion_manual
        start_time = time.time()
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(self._cdp_endpoint)
            try:
                # Tomar el primer contexto del Chrome del usuario
                if not browser.contexts:
                    raise AgentError(
                        "Chrome conectado pero sin contextos. "
                        "Cierre Chrome y reabra con tools/start-chrome-bot.bat."
                    )
                # Recolectar TODAS las pestañas de TODOS los contextos. Cuando se
                # conecta via CDP, Chrome puede tener múltiples BrowserContexts y
                # cada Firma Digital puede abrir el portal APT en uno distinto.
                def _todas_pages():
                    out = []
                    for c in browser.contexts:
                        try:
                            out.extend(c.pages)
                        except Exception:
                            continue
                    return out

                # Tomar el primer contexto solo para crear pestañas nuevas
                ctx = browser.contexts[0]

                # 1. ¿Ya hay tab APT con sesión activa? (revisar TODOS los contextos)
                apt_page = None
                for pg in _todas_pages():
                    try:
                        if "apt.cfia.or.cr" in pg.url:
                            apt_page = pg
                            break
                    except Exception:
                        continue

                if apt_page is not None:
                    self._log.info("tab APT existente detectada via CDP — verificando sesión")
                    if self._confirmar_sesion_apt(apt_page):
                        self._log.info("sesion APT activa via CDP")
                        if notificar_fn:
                            notificar_fn(
                                "✅ *Sesión APT activa* en su Chrome. "
                                "Ahora envíe: *APT CREAR <expediente>*"
                            )
                        return

                # HOTFIX 2026-05-22: limpiar tabs blank huérfanas antes de
                # abrir el SSO (evita acumulación cada vez que se invoca
                # APT SESION repetidas veces).
                with contextlib.suppress(Exception):
                    cleanup_blank_tabs(browser, max_blank=0)

                # 2. No hay tab APT logueado — buscar pestaña SSO existente
                # antes de crear una. Antes esto SIEMPRE abría una nueva tab,
                # aún si ya había una pestaña sso.cfia.or.cr abierta de un
                # intento anterior.
                page = get_or_create_apt_page(
                    browser, url_patterns=("sso.cfia.or.cr", "apt.cfia.or.cr"),
                )
                self._log.info("CDP: usando tab SSO (url=%s)", page.url[:80] if is_page_alive(page) else "?")
                page.goto(self._login_url, wait_until="load", timeout=60000)
                if notificar_fn:
                    notificar_fn(
                        "🖥️ *Pestaña SSO abierta en su Chrome.*\n"
                        "Haga *Firma Digital BCR* ahí (Fortify/Gaudi/Monitor Agent).\n"
                        "Cuando vea el portal APT cargado, envíe: *APT SESION OK*"
                    )

                _ultimo_url_log = ""
                _iter = 0
                while time.time() < deadline:
                    # Señal manual del operador
                    if self._sesion_event.wait(timeout=3):
                        self._sesion_event.clear()
                        self._log.info("APT SESION OK recibido (CDP) — verificando")
                        # Buscar entre TODAS las tabs (todos los contextos)
                        confirmado = False
                        for pg in _todas_pages():
                            try:
                                if self._confirmar_sesion_apt(pg):
                                    confirmado = True
                                    break
                            except Exception:
                                continue
                        if confirmado:
                            self._log.info("sesion APT confirmada via CDP + APT SESION OK")
                            if notificar_fn:
                                notificar_fn(
                                    "✅ *Sesión APT confirmada* en su Chrome.\n"
                                    "Ahora envíe: *APT CREAR <expediente>*"
                                )
                            return
                        if notificar_fn:
                            notificar_fn(
                                "⚠️ Sesión APT *no detectada* aún.\n"
                                "Asegúrese que la pestaña apt.cfia.or.cr esté abierta y "
                                "con sesión activa, luego envíe *APT SESION OK* de nuevo."
                            )
                        continue

                    # Poll automático — revisa TODOS los contextos
                    _iter += 1
                    todas = _todas_pages()
                    for pg in todas:
                        try:
                            u = pg.url
                        except Exception:
                            continue
                        if u != _ultimo_url_log or _iter % 10 == 0:
                            self._log.info(
                                "APT SESION CDP poll [%ds] tabs=%d URL: %s",
                                int(time.time() - start_time),
                                len(todas), u,
                            )
                            _ultimo_url_log = u
                        if "apt.cfia.or.cr" in u:
                            self._log.info("sesion APT detectada via CDP poll: %s", u)
                            if notificar_fn:
                                notificar_fn(
                                    "✅ *Sesión APT detectada* en su Chrome.\n"
                                    "Ahora envíe: *APT CREAR <expediente>*"
                                )
                            return

                elapsed_min = int((time.time() - start_time) / 60)
                raise AgentError(
                    f"Timeout esperando login manual APT ({elapsed_min} min). "
                    "Inténtelo de nuevo con APT SESION."
                )
            finally:
                with contextlib.suppress(Exception):
                    browser.close()

    def iniciar_sesion_manual(
        self,
        notificar_fn=None,
        timeout_min: int = 10,
    ) -> None:
        """Abre un navegador VISIBLE para que el topógrafo haga login con Firma Digital.

        Bloquea hasta que el login es detectado o se agota el timeout.
        Guarda la sesión en APT_SESSION_PATH para reutilización futura.

        Args:
            notificar_fn: callable(str) para enviar mensajes WhatsApp al operador.
            timeout_min: minutos máximos de espera antes de abortar.
        """
        from playwright.sync_api import sync_playwright  # noqa: PLC0415

        self._session_path.parent.mkdir(parents=True, exist_ok=True)

        # Limpiar evento al inicio (evita estado heredado de invocaciones previas)
        self._sesion_event.clear()

        deadline = time.time() + timeout_min * 60
        self._log.info("iniciando sesion manual APT (timeout=%d min)", timeout_min)

        # ──────────────────────────────────────────────────────────────────────
        # MODO PREFERIDO: CDP — conectar al Chrome del usuario
        # ──────────────────────────────────────────────────────────────────────
        # Si el Chrome del usuario está corriendo con --remote-debugging-port=9222,
        # el bot se conecta ahí en vez de abrir un Chrome aparte. Ventajas:
        #   - Firma Digital BCR funciona porque es el Chrome real con todas las
        #     apps locales (Fortify, ID Protector, Monitor Agent, Gaudi).
        #   - Sin confusión de ventanas (el usuario ya está en su Chrome).
        #   - La sesión persiste en el perfil normal del usuario.
        if self._cdp_disponible():
            self._log.info("CDP detectado en %s — usando Chrome del usuario", self._cdp_endpoint)
            return self._iniciar_sesion_manual_cdp(notificar_fn, deadline)

        # ──────────────────────────────────────────────────────────────────────
        # FALLBACK: launch_persistent_context con perfil propio
        # ──────────────────────────────────────────────────────────────────────
        self._log.info("CDP no disponible — fallback a launch_persistent_context")
        if notificar_fn:
            notificar_fn(
                "🖥️ *Portal APT abierto en su pantalla.*\n"
                "Ingrese con su *Firma Digital BCR* (token USB o tarjeta).\n"
                f"El bot detectará el login automáticamente (espera máx. {timeout_min} min).\n\n"
                "💡 _Tip: para evitar problemas, ejecute primero "
                "tools/start-chrome-bot.bat antes de APT SESION._"
            )

        self._profile_path.mkdir(parents=True, exist_ok=True)
        self._limpiar_lock_perfil()
        with _PROFILE_LOCK:
            with sync_playwright() as p:
                ctx = p.chromium.launch_persistent_context(
                    str(self._profile_path),
                    channel="chrome",
                    headless=False,
                    slow_mo=50,
                )
                # HOTFIX 2026-05-22: limpiar blank tabs + reusar APT/SSO si existe.
                with contextlib.suppress(Exception):
                    cleanup_blank_tabs(ctx, max_blank=0)
                page = get_or_create_apt_page(ctx)
                try:
                    # ── Verificar si el perfil ya tiene sesión APT activa ─────────
                    # Navegar a APT Home: si NO hay redirect al SSO → sesión válida.
                    if self._confirmar_sesion_apt(page):
                        self._log.info("sesion APT del perfil persistente sigue valida")
                        if notificar_fn:
                            notificar_fn(
                                "✅ *Sesión APT activa.* El bot continuará automáticamente."
                            )
                        return

                    # Sesión expirada → llevar al usuario al login SSO
                    self._log.info(
                        "sesion APT expirada — abriendo SSO para login con Firma Digital"
                    )
                    page.goto(self._login_url, wait_until="load", timeout=60000)

                    # ── Esperar login manual (Firma Digital BCR) ──────────────────
                    # Firma Digital BCCR puede abrir el APT en una ventana EXTERNA
                    # de Chrome (fuera del contexto Playwright). Por eso usamos DOS
                    # mecanismos de detección:
                    #   A) Poll automático de todas las páginas del contexto.
                    #   B) Señal manual: operador envía "APT SESION OK" por WhatsApp
                    #      → self._sesion_event se activa → verificamos con
                    #        _confirmar_sesion_apt() en un nuevo contexto headless.
                    if notificar_fn:
                        notificar_fn(
                            "🖥️ *Portal APT abierto.*\n"
                            "Ingrese con su *Firma Digital BCR*.\n\n"
                            "Cuando el portal APT esté visible y con su sesión activa, "
                            "envíe por WhatsApp:\n"
                            "*APT SESION OK*\n\n"
                            f"_(El bot esperará hasta {timeout_min} minutos)_"
                        )
                    # Nota: _sesion_event ya fue limpiado al inicio de iniciar_sesion_manual
                    _ultimo_url_log = ""
                    _iter = 0
                    while time.time() < deadline:
                        # ── Opción A: señal manual del operador ──────────────
                        if self._sesion_event.wait(timeout=3):
                            self._sesion_event.clear()
                            self._log.info(
                                "APT SESION OK recibido — navegando a APT Home para verificar"
                            )
                            # Verificar en el contexto headful existente (sin abrir uno nuevo:
                            # el perfil Chromium solo admite un contexto persistente a la vez).
                            # _confirmar_sesion_apt navega a APT_HOME_URL y verifica la URL
                            # resultante; si APT acepta la sesión no redirige al SSO.
                            try:
                                if self._confirmar_sesion_apt(page):
                                    self._log.info(
                                        "sesion APT confirmada via APT SESION OK"
                                    )
                                    if notificar_fn:
                                        notificar_fn(
                                            "✅ *Sesión APT guardada en perfil.*\n"
                                            "La sesión persistirá para próximas operaciones.\n"
                                            "Ahora envíe: *APT CREAR <expediente>*"
                                        )
                                    return
                                else:
                                    self._log.warning(
                                        "APT SESION OK recibido pero sesión NO confirmada"
                                    )
                                    if notificar_fn:
                                        notificar_fn(
                                            "⚠️ Sesión APT *no detectada* todavía.\n"
                                            "Asegúrese de que el portal APT "
                                            "(`apt.cfia.or.cr`) esté abierto y con "
                                            "sesión activa, luego envíe *APT SESION OK* "
                                            "de nuevo."
                                        )
                            except Exception as exc:
                                self._log.warning(
                                    "error verificando sesión APT tras SESION OK: %s", exc
                                )
                            continue

                        # ── Opción B: poll automático de pestañas del contexto ─
                        _iter += 1
                        try:
                            todas = list(ctx.pages)
                        except Exception:
                            continue
                        for p_check in todas:
                            try:
                                u = p_check.url
                            except Exception:
                                continue
                            if u != _ultimo_url_log or _iter % 10 == 0:
                                self._log.info(
                                    "APT SESION poll [%ds] páginas=%d URL: %s",
                                    int(time.time() - (deadline - timeout_min * 60)),
                                    len(todas),
                                    u,
                                )
                                _ultimo_url_log = u
                            if "apt.cfia.or.cr" in u:
                                self._log.info(
                                    "sesion APT detectada via poll automático — URL: %s",
                                    u,
                                )
                                if notificar_fn:
                                    notificar_fn(
                                        "✅ *Sesión APT guardada en perfil.*\n"
                                        "La sesión persistirá para próximas operaciones.\n"
                                        "Ahora envíe: *APT CREAR <expediente>*"
                                    )
                                return

                    raise AgentError(
                        f"Timeout esperando login manual APT ({timeout_min} min). "
                        "Inténtelo de nuevo con APT SESION."
                    )
                finally:
                    with contextlib.suppress(Exception):
                        ctx.close()

    def abrir_portal(self, url: str = "", notificar_fn=None) -> None:
        """Abre el portal APT en un navegador VISIBLE con la sesión guardada.

        Permanece abierto hasta que el usuario cierre el navegador.
        Útil para inspeccionar el portal, crear contratos manualmente o
        verificar el estado de un trámite.

        Si no hay sesión guardada, abre el login para autenticación manual.
        """
        from playwright.sync_api import sync_playwright

        destino = url or APT_HOME_URL
        self._log.info("abriendo portal APT en navegador visible: %s", destino)

        if notificar_fn:
            notificar_fn(
                f"🖥️ *Portal APT abierto en su pantalla.*\n"
                f"Navegando a: `{destino}`\n"
                "Cierre el navegador cuando termine."
            )

        self._profile_path.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(
                str(self._profile_path),
                channel="chrome",
                headless=False,
                slow_mo=300,
            )
            # HOTFIX 2026-05-22: reusar pestaña abierta en lugar de nueva.
            with contextlib.suppress(Exception):
                cleanup_blank_tabs(ctx, max_blank=0)
            page = get_or_create_apt_page(ctx)
            try:
                page.goto(destino, wait_until="load", timeout=60000)
                # Mantener abierto hasta que el usuario cierre
                page.wait_for_event("close", timeout=0)  # 0 = sin timeout
            except Exception:
                pass  # usuario cerró el navegador
            finally:
                with contextlib.suppress(Exception):
                    ctx.close()

    def crear_contrato(
        self,
        expediente_id: str,
        *,
        notificar_fn=None,
    ) -> str:
        """Crea un nuevo contrato en APT para el expediente dado.

        Navega a APT2/Contrato/Nuevo, llena los datos del expediente
        (tipo de plano, cliente, topógrafo) y guarda.

        Devuelve el número de trámite (apt_tramite) asignado por APT.
        El trámite queda en estado 'En Edición' listo para subir archivos.

        NOTA: abre un navegador VISIBLE (headless=False) para que el
        operador pueda verificar cada paso.
        """
        from playwright.sync_api import sync_playwright

        exp = self.db.obtener_expediente(expediente_id)
        if not exp:
            raise AgentError(f"expediente {expediente_id!r} no existe en BD")

        tipo_plano  = exp.get("tipo_plano", "")
        cliente     = exp.get("nombre_cliente", "")
        topografo   = exp.get("nombre_topografo", "")
        numero      = exp.get("numero_expediente", "")

        self._log.info(
            "creando contrato APT para exp=%s tipo=%s", expediente_id, tipo_plano
        )
        if notificar_fn:
            notificar_fn(
                f"🖥️ Abriendo APT para crear contrato de *{numero}*.\n"
                "Observe el navegador — el bot llenará los datos automáticamente."
            )

        tramite_extraido: list[str] = []   # mutable para capturar desde el closure

        # ──────────────────────────────────────────────────────────────────────
        # MODO PREFERIDO: CDP — trabajar dentro del Chrome del usuario
        # ──────────────────────────────────────────────────────────────────────
        if self._cdp_disponible():
            self._log.info("crear_contrato via CDP (Chrome del usuario)")
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(self._cdp_endpoint)
                try:
                    if not browser.contexts:
                        raise AgentError(
                            "Chrome conectado sin contextos. Ejecute APT SESION primero."
                        )
                    # HOTFIX 2026-05-22: usar helper que reusa pestaña APT/SSO
                    # viva si existe y solo abre nueva si no hay candidata.
                    # También limpia blanks acumulados ANTES.
                    with contextlib.suppress(Exception):
                        cleanup_blank_tabs(browser, max_blank=1)
                    page = get_or_create_apt_page(browser)
                    # target_ctx ya no se usa más abajo (era para new_page)

                    self._crear_contrato_body(
                        page, exp, tipo_plano, topografo, numero,
                        tramite_extraido, notificar_fn,
                        wait_for_close=False,  # CDP: no esperamos que cierre
                    )
                finally:
                    with contextlib.suppress(Exception):
                        browser.close()
        else:
            # ──────────────────────────────────────────────────────────────────
            # FALLBACK: launch_persistent_context con perfil propio
            # ──────────────────────────────────────────────────────────────────
            self._profile_path.mkdir(parents=True, exist_ok=True)
            if _PROFILE_LOCK.locked():
                raise APTSesionRequeridaError(
                    "El navegador APT está ocupado (APT SESION en curso). "
                    "Espere a que cierre y vuelva a enviar APT CREAR."
                )
            self._limpiar_lock_perfil()
            with _PROFILE_LOCK:
                with sync_playwright() as p:
                    ctx = p.chromium.launch_persistent_context(
                        str(self._profile_path),
                        headless=False,
                        channel="chrome",
                        slow_mo=600,
                    )
                    page = ctx.new_page()
                    try:
                        self._crear_contrato_body(
                            page, exp, tipo_plano, topografo, numero,
                            tramite_extraido, notificar_fn,
                            wait_for_close=True,
                        )
                    finally:
                        with contextlib.suppress(Exception):
                            ctx.close()

        if not tramite_extraido:
            raise AgentError(
                f"No se pudo obtener número de trámite APT para exp={expediente_id}. "
                f"Registre manualmente con: APT TRAMITE {numero} <numero>"
            )
        return tramite_extraido[0]

    def _crear_contrato_body(
        self,
        page,
        exp: dict,
        tipo_plano: str,
        topografo: str,
        numero: str,
        tramite_extraido: list,
        notificar_fn,
        *,
        wait_for_close: bool,
    ) -> None:
        """Cuerpo de crear_contrato — recibe una page Playwright lista para usar.

        Lee `datos_apt` desde `exp.metadata` y llena el formulario completo
        (Propietario, Contratante, Protocolo, Proyecto, Controversias, General,
        Firmas). Profesional viene auto-llenado por Firma Digital BCR.

        Si `datos_apt` no existe, lanza error indicando ejecutar el CLI tool.
        """
        import json as _json

        # 0. Leer datos_apt del expediente (la columna real es metadata_json)
        meta_raw = exp.get("metadata_json") or exp.get("metadata") or "{}"
        if isinstance(meta_raw, str):
            try:
                meta = _json.loads(meta_raw)
            except Exception:
                meta = {}
        else:
            meta = meta_raw or {}
        datos_apt = meta.get("datos_apt") or {}
        if not datos_apt:
            raise AgentError(
                f"No hay datos_apt para {numero}. "
                f"Ejecute en el servidor:\n"
                f"  python tools/datos_apt.py {numero}\n"
                "y vuelva a enviar APT CREAR."
            )

        # 1. Verificar sesión APT navegando a APT_HOME
        if not self._confirmar_sesion_apt(page):
            raise APTSesionRequeridaError(
                "Sesión APT expirada. Ejecute APT SESION para autenticarse."
            )

        # 2. Navegar a Nuevo Contrato. Con wait_until="load" la página se
        # marca lista pero APT aún está populando el modal y dropdowns via
        # JS — sleep para dar tiempo a que rendere antes de interactuar.
        page.goto(APT_CONTRATO_URL, wait_until="load", timeout=60000)
        time.sleep(2.0)
        self._log.info("en página Nuevo Contrato — llenando con datos_apt")

        # 2b. Cerrar el modal "Tipo de Proyecto" que APT abre automáticamente
        # al cargar /Contrato/Nuevo. Si no se cierra, todo el form está bloqueado.
        self._cerrar_modal_tipo_proyecto(
            page,
            tipo_proyecto=datos_apt.get("protocolo", {}).get("tipo_proyecto_modal", "27"),
        )

        # 3. Llenar todas las secciones desde datos_apt
        max_planos = (datos_apt.get("general") or {}).get("max_planos", "1")
        self._llenar_seccion_propietario(page, datos_apt.get("propietario", {}))
        self._llenar_seccion_contratante(page, datos_apt)
        self._llenar_seccion_profesional(page, datos_apt.get("profesional", {}))
        self._llenar_seccion_protocolo(page, datos_apt.get("protocolo", {}))
        self._llenar_seccion_proyecto(page, datos_apt.get("proyecto", {}), max_planos=max_planos)
        self._llenar_seccion_controversias(page, datos_apt.get("controversias", {}))
        self._llenar_seccion_general(page, datos_apt.get("general", {}))
        self._llenar_seccion_firmas(page, datos_apt.get("firmas", {}))

        # 4. Guardar contrato
        time.sleep(0.5)
        if notificar_fn:
            notificar_fn("💾 Datos llenos — haciendo clic en GUARDAR...")
        self._fill_if_exists(page, SEL_BTN_GUARDAR, click=True)
        page.wait_for_load_state("load", timeout=60000)
        time.sleep(1.5)

        # 5. Extraer número de trámite
        tramite = self._extraer_tramite_de_pagina(page)
        if tramite:
            tramite_extraido.append(tramite)
            self._log.info("contrato creado — tramite: %s", tramite)
            if notificar_fn:
                notificar_fn(
                    f"✅ Contrato APT creado.\n"
                    f"Trámite: *{tramite}*\n"
                    "Ahora puede subir los archivos del plano."
                )
        else:
            self._log.warning(
                "contrato guardado pero no se pudo extraer numero de tramite — "
                "use: APT TRAMITE %s <numero>", numero,
            )
            if notificar_fn:
                notificar_fn(
                    "⚠️ Contrato guardado en APT pero no se pudo leer el número de trámite.\n"
                    "Verifique en su Chrome si hay errores en pantalla.\n"
                    "Si fue creado, lea el número y envíe:\n"
                    f"*APT TRAMITE {numero} <numero_tramite>*"
                )

        if wait_for_close:
            if notificar_fn:
                notificar_fn(
                    "🖥️ Navegador APT abierto — ciérrelo cuando haya revisado el contrato."
                )
            page.wait_for_event("close", timeout=0)
        else:
            if notificar_fn:
                notificar_fn(
                    "🖥️ Pestaña APT lista en su Chrome — revise el contrato cuando guste."
                )

    # ─────────────────────────────────────────────────────────────────────
    # Helpers para llenar cada sección del formulario APT/Contrato/Nuevo
    # Selectores verificados contra el DOM real (mayo 2026)
    # ─────────────────────────────────────────────────────────────────────

    def _cerrar_modal_tipo_proyecto(self, page, *, tipo_proyecto: str = "27") -> bool:
        """Cierra el modal 'Tipo de Proyecto' que APT abre automáticamente al
        entrar a /APT2/Contrato/Nuevo.

        El modal (id `CargarTipoContrato`) bloquea el formulario hasta que el
        usuario seleccione un tipo y confirme con el botón verde
        (onclick=`ContinuarRegistroContrato()`).

        Returns True si se cerró exitosamente; False si no había modal o falló.
        """
        try:
            modal = page.locator("#CargarTipoContrato")
            if modal.count() == 0:
                return False
            # Esperar que sea visible (puede tardar un instante en aparecer)
            try:
                modal.wait_for(state="visible", timeout=5000)
            except Exception:
                # Si no está visible, no hace falta hacer nada
                return False
            time.sleep(0.3)

            # Seleccionar Plano Simple (27) en el dropdown del modal.
            # Esperar primero a que las opciones se hayan cargado (con
            # `wait_until="load"` algunas veces el dropdown aún se está
            # populando cuando llegamos aquí).
            try:
                page.wait_for_function(
                    """() => {
                        const dd = document.querySelector('#ddlTipoProyectoModal');
                        return dd && dd.options.length >= 3;
                    }""",
                    timeout=5000,
                )
            except Exception:
                pass
            try:
                page.select_option("#ddlTipoProyectoModal", value=str(tipo_proyecto), timeout=5000)
                time.sleep(0.3)
            except Exception as exc:
                self._log.warning("modal: no se pudo seleccionar tipo_proyecto=%s: %s",
                                   tipo_proyecto, exc)

            # Click en el botón verde "Continuar" (versión simple que funcionaba)
            confirm_btn = page.locator("#CargarTipoContrato button.btn-success")
            if confirm_btn.count() == 0:
                confirm_btn = page.locator(
                    "#CargarTipoContrato button[onclick*='ContinuarRegistroContrato']"
                )
            if confirm_btn.count() == 0:
                self._log.warning("modal: no se encontró el botón verde de confirmar")
                return False
            confirm_btn.first.click()

            # Esperar a que el modal se oculte
            try:
                modal.wait_for(state="hidden", timeout=5000)
            except Exception:
                pass
            time.sleep(0.4)
            self._log.info(
                "modal 'Tipo de Proyecto' cerrado (tipo_proyecto=%s)", tipo_proyecto
            )
            return True
        except Exception as exc:
            self._log.warning("error cerrando modal Tipo de Proyecto: %s", exc)
            return False

    def _localizar_resilient(self, page, *selectores: str):
        """Intenta múltiples selectores en orden; devuelve el primero que matchee.

        Útil cuando APT cambia un ID y queremos sobrevivir el cambio: el helper
        prueba por `#id`, luego `[name='X']`, etc.

        Returns: (Locator, selector_que_funcionó) o (None, "") si todos fallaron.
        """
        for sel in selectores:
            if not sel:
                continue
            try:
                elem = page.locator(sel)
                if elem.count() > 0:
                    return elem, sel
            except Exception as exc:
                self._log.debug("selector %r falló: %s", sel, exc)
                continue
        return None, ""

    def _set_select(self, page, selector: str, value: str,
                    *, fallbacks: list[str] | None = None) -> bool:
        """Selecciona un valor en un <select>.

        APT usa jQuery Unobtrusive Validation (data-val-required, etc.). Para
        que la validación se actualice y limpie el mensaje "este campo es
        requerido", hay que llamar `$(el).valid()` explícitamente después
        del cambio.

        Si el `selector` principal no encuentra elemento, prueba:
          1. fallbacks explícitos pasados como kwarg
          2. FALLBACKS_CAMPOS_APT[selector] del catálogo de fallbacks conocidos
        Esto sobrevive a cambios de ID en APT mientras el `name` del input se
        mantenga (lo que hace ASP.NET MVC por defecto).
        """
        if not value:
            return False
        try:
            elem = page.locator(selector)
            sel_usado = selector
            if elem.count() == 0:
                # Combinar fallbacks explícitos + catálogo conocido
                fb = list(fallbacks or []) + FALLBACKS_CAMPOS_APT.get(selector, [])
                if fb:
                    elem, sel_usado = self._localizar_resilient(page, *fb)
                    if elem is None:
                        return False
                    self._log.warning(
                        "_set_select: '%s' no encontrado, usando fallback '%s'",
                        selector, sel_usado,
                    )
                else:
                    return False

            # Estrategia 1: JS directo + eventos + validación explícita
            try:
                ok = elem.first.evaluate(
                    """(el, val) => {
                        const opt = Array.from(el.options).find(o => o.value === String(val));
                        if (!opt) return false;
                        opt.selected = true;
                        el.value = String(val);
                        el.dispatchEvent(new Event('input',  {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        el.dispatchEvent(new Event('blur',   {bubbles: true}));
                        if (window.jQuery) {
                            const $el = jQuery(el);
                            $el.trigger('change').trigger('blur');
                            // Re-correr la validación de jQuery Unobtrusive y limpiar
                            // el mensaje "este campo es requerido"
                            try { if ($el.valid) $el.valid(); } catch(e) {}
                            try {
                                $el.removeClass('input-validation-error')
                                   .addClass('valid');
                                const span = jQuery('span[data-valmsg-for="' + el.name + '"]');
                                span.removeClass('field-validation-error')
                                    .addClass('field-validation-valid')
                                    .empty();
                            } catch(e) {}
                        }
                        return true;
                    }""",
                    str(value),
                )
                if ok:
                    return True
            except Exception:
                pass

            # Estrategia 2: Playwright select_option (fallback)
            page.select_option(selector, value=str(value), timeout=5000)
            return True
        except Exception as exc:
            self._log.warning("_set_select %s=%s: %s", selector, value, exc)
            return False

    def _set_input(self, page, selector: str, value: str,
                   *, fallbacks: list[str] | None = None) -> bool:
        """Llena un <input>/<textarea>.

        - Salta inputs disabled (algunos campos como #txtLetras los rellena APT)
        - Usa JS directo cuando el elemento no es interactuable por accordion
        - Si `selector` no matchea, prueba:
            1. fallbacks explícitos pasados como kwarg
            2. FALLBACKS_CAMPOS_APT[selector] del catálogo
          Esto sobrevive a cambios de ID en APT.
        """
        if value is None or value == "":
            return False
        try:
            elem = page.locator(selector)
            if elem.count() == 0:
                fb = list(fallbacks or []) + FALLBACKS_CAMPOS_APT.get(selector, [])
                if fb:
                    elem, sel_usado = self._localizar_resilient(page, *fb)
                    if elem is None:
                        return False
                    self._log.warning(
                        "_set_input: '%s' no encontrado, usando fallback '%s'",
                        selector, sel_usado,
                    )
                else:
                    return False
            # Skip si está disabled — APT lo rellena solo
            try:
                if elem.first.is_disabled():
                    self._log.info("_set_input %s skip — disabled", selector)
                    return False
            except Exception:
                pass

            # Estrategia 1: JS directo (rápido, funciona en accordions colapsados)
            # IMPORTANTE: incluimos keyup además de input/change/blur. APT
            # escucha keyup para disparar lookups async (ej. consulta RNP del
            # propietario). Sin keyup, el lookup no se activa.
            try:
                ok = elem.first.evaluate(
                    """(el, val) => {
                        if (el.disabled || el.readOnly) return false;
                        el.focus();
                        el.value = String(val);
                        el.dispatchEvent(new Event('input',  {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        // keyup: dispara lookups async tipo RNP/SUGEF de APT
                        el.dispatchEvent(new KeyboardEvent('keyup', {bubbles: true, key: 'End'}));
                        el.dispatchEvent(new Event('blur',   {bubbles: true}));
                        if (window.jQuery) {
                            const $el = jQuery(el);
                            $el.trigger('keyup').trigger('change').trigger('blur');
                            try { if ($el.valid) $el.valid(); } catch(e) {}
                            try {
                                $el.removeClass('input-validation-error')
                                   .addClass('valid');
                                const span = jQuery('span[data-valmsg-for="' + el.name + '"]');
                                span.removeClass('field-validation-error')
                                    .addClass('field-validation-valid')
                                    .empty();
                            } catch(e) {}
                        }
                        return true;
                    }""",
                    str(value),
                )
                if ok:
                    return True
            except Exception:
                pass

            # Fallback: Playwright fill con force
            elem.first.fill(str(value), timeout=5000, force=True)
            return True
        except Exception as exc:
            self._log.warning("_set_input %s=%r: %s", selector, value, exc)
            return False

    def _set_check(self, page, selector: str, checked: bool) -> bool:
        """Marca o desmarca un checkbox/radio.

        Estrategia híbrida (los radios/checks ocultos por accordions colapsados
        no responden a Locator.check() incluso con force=True):
          1. JS directo: setea .checked y dispara change (cubre todos los casos)
          2. Fallback a Playwright check/uncheck
        """
        try:
            elem = page.locator(selector)
            if elem.count() == 0:
                return False

            # Estrategia 1: JS directo
            #
            # Para checkboxes con handlers `onclick` (ej. #checkContratante que
            # auto-copia propietario→contratante), usamos el.click() — esto
            # dispara TODOS los handlers (onclick, onchange) como un clic real.
            # Para radios/checks sin handlers especiales, esto es equivalente
            # a setear .checked + change.
            try:
                ok = elem.first.evaluate(
                    """(el, want) => {
                        // Si ya está en el estado deseado, no hacer nada
                        if (el.checked === !!want) return true;
                        // .click() dispara onclick (APT lo usa para auto-rellenar
                        // contratante=propietario, etc.) además del cambio de estado.
                        el.click();
                        if (el.checked !== !!want) {
                            // Fallback si .click() no cambió el estado: setearlo
                            el.checked = !!want;
                            el.dispatchEvent(new Event('input',  {bubbles: true}));
                            el.dispatchEvent(new Event('change', {bubbles: true}));
                        }
                        if (window.jQuery) {
                            const $el = jQuery(el);
                            $el.trigger('change');
                            try { if ($el.valid) $el.valid(); } catch(e) {}
                            try {
                                $el.removeClass('input-validation-error');
                                const span = jQuery('span[data-valmsg-for="' + el.name + '"]');
                                span.removeClass('field-validation-error')
                                    .addClass('field-validation-valid')
                                    .empty();
                            } catch(e) {}
                        }
                        return true;
                    }""",
                    bool(checked),
                )
                if ok:
                    return True
            except Exception:
                pass

            # Fallback: Playwright check/uncheck con force
            try:
                if elem.first.is_checked() == checked:
                    return True
            except Exception:
                pass
            if checked:
                elem.first.check(force=True, timeout=5000)
            else:
                elem.first.uncheck(force=True, timeout=5000)
            return True
        except Exception as exc:
            self._log.warning("_set_check %s=%s: %s", selector, checked, exc)
            return False

    def _validar_y_corregir_rnp(
        self,
        page,
        *,
        sel_nombre: str,
        sel_ap1: str,
        sel_ap2: str,
        nombre_registro: str,
        ap1_registro: str,
        ap2_registro: str,
        cedula: str,
        contexto: str,
    ) -> dict:
        """Lee los campos auto-rellenados por la consulta RNP/TSE y los compara
        contra el nombre del registro de la finca (provisto por el operador).

        Política aplicada (decisión del operador):
          - La cédula es lo único legalmente vinculante para CFIA.
          - Cuando hay discrepancia entre TSE y registro, se CONSERVA la cédula
            y se SOBRESCRIBE el nombre con el del registro.
          - Cada discrepancia se registra en `self.discrepancias_rnp` para que
            el caller la informe al operador (vía WhatsApp + metadata).

        Casos:
          1. RNP no devolvió nada Y dict no trae nombre →
                no podemos llenar — RNPMismatchError (cédula inválida).
          2. RNP no devolvió nada Y dict trae nombre →
                llenar campos del dict (sin notificar — no hay con qué comparar).
          3. RNP devolvió algo Y dict no trae nombre →
                conservar TSE, log info.
          4. RNP devolvió algo Y dict trae nombre Y coinciden →
                conservar (no tocar campos).
          5. RNP devolvió algo Y dict trae nombre Y NO coinciden →
                SOBRESCRIBIR campos con valores del dict + registrar
                discrepancia + log warning. NO bloquea guardado.

        Devuelve dict {tse_nombre, registro_nombre, match, override_aplicado}.
        """
        leido = page.evaluate(
            """(sels) => ({
                nombre: (document.querySelector(sels.n)?.value || '').trim(),
                ap1:    (document.querySelector(sels.a1)?.value || '').trim(),
                ap2:    (document.querySelector(sels.a2)?.value || '').trim()
            })""",
            {"n": sel_nombre, "a1": sel_ap1, "a2": sel_ap2},
        )
        # Defensa: si el page mockeado o un timeout devolvió algo no-dict,
        # tratarlo como respuesta vacía.
        if not isinstance(leido, dict):
            leido = {"nombre": "", "ap1": "", "ap2": ""}
        tse_nombre = " ".join(
            p for p in [leido.get("nombre"), leido.get("ap1"), leido.get("ap2")] if p
        )
        registro_nombre = " ".join(
            p for p in [nombre_registro, ap1_registro, ap2_registro] if p
        )
        tse_norm  = _normalizar_nombre(tse_nombre)
        reg_norm  = _normalizar_nombre(registro_nombre)

        # Caso 1: RNP vacío y dict tampoco trae nombre
        if not tse_norm and not reg_norm:
            msg = (
                f"[{contexto}] RNP no devolvió datos para cédula {cedula} y "
                f"el registro tampoco trae nombre — abortando guardado."
            )
            self._log.error(msg)
            raise RNPMismatchError(msg)

        # Caso 2: RNP vacío pero el dict trae nombre del registro → llenar dict
        if not tse_norm and reg_norm:
            self._set_input(page, sel_nombre, nombre_registro)
            self._set_input(page, sel_ap1,    ap1_registro)
            self._set_input(page, sel_ap2,    ap2_registro)
            self._log.info(
                "[%s] RNP no respondió, se llenó del registro: %r",
                contexto, registro_nombre,
            )
            return {
                "tse_nombre": "", "registro_nombre": registro_nombre,
                "match": None, "override_aplicado": True,
            }

        # Caso 3: RNP devolvió algo pero dict no declaró nombre
        if not reg_norm:
            self._log.info("[%s] TSE: %r (sin nombre del registro para validar)", contexto, tse_nombre)
            return {
                "tse_nombre": tse_nombre, "registro_nombre": "",
                "match": None, "override_aplicado": False,
            }

        # Casos 4-5: comparar TSE vs registro
        # Tolerancia: aceptamos coincidencia si todas las palabras del registro
        # aparecen en el TSE (cubre orden distinto / segundo nombre faltante).
        palabras_reg = set(reg_norm.split())
        palabras_tse = set(tse_norm.split())
        coincide = (reg_norm == tse_norm) or palabras_reg.issubset(palabras_tse)

        if coincide:
            self._log.info(
                "[%s] TSE OK ≈ registro: %r (cédula %s)",
                contexto, registro_nombre, cedula,
            )
            return {
                "tse_nombre": tse_nombre, "registro_nombre": registro_nombre,
                "match": True, "override_aplicado": False,
            }

        # Caso 5: MISMATCH — registrar discrepancia para notificar al operador.
        #
        # NOTA TÉCNICA: APT enforce el binding cédula→TSE solo en el INSERT
        # inicial, NO en el UPDATE. Por eso el flow real es:
        #   1. INSERT inicial pasa con el nombre TSE (no podemos evitarlo).
        #   2. Caller hace click sobre la card guardada (SeleccionarTitular)
        #      → el form se llena con los datos del record.
        #   3. Caller sobrescribe nombre/apellidos en el form.
        #   4. Click GuardarTitular → ahora es UPDATE → APT respeta el form.
        #
        # Verificado empíricamente con RDF-2026-002 (cédula 2-0440-0388):
        # con UPDATE el nombre quedó como `GRACE ALVAREZ GONZALEZ` aunque
        # TSE devolvía AMALIA QUESADA RODRIGUEZ.
        #
        # `_llenar_seccion_plano_titulares` aplica el ciclo automáticamente
        # cuando recibe `match=False` aquí. Esta función solo registra la
        # discrepancia y deja el INSERT pasar con el nombre TSE (paso 1).
        self._log.warning(
            "[%s] DISCREPANCIA RNP/TSE — cédula %s — TSE='%s' registro='%s'. "
            "Tras el INSERT se hará UPDATE con el nombre del registro. Notificando al operador.",
            contexto, cedula, tse_nombre, registro_nombre,
        )

        from datetime import datetime
        from src.agents.apt_discrepancia_handler import TIPO_RNP_TSE_MISMATCH
        self.discrepancias_rnp.append({
            "tipo":            TIPO_RNP_TSE_MISMATCH,
            "ts":              datetime.now().isoformat(timespec="seconds"),
            "contexto":        contexto,
            "cedula":          cedula,
            "tse_nombre":      tse_nombre,
            "registro_nombre": registro_nombre,
            # Después del UPDATE post-insert el nombre guardado pasa a ser
            # el del registro. Esto se confirma desde el caller después de
            # que el UPDATE fue exitoso.
            "guardado_como":   registro_nombre,
        })
        return {
            "tse_nombre": tse_nombre, "registro_nombre": registro_nombre,
            "match": False, "override_aplicado": False,
        }

    def _llenar_seccion_propietario(self, page, datos: dict) -> None:
        """Sección Propietario — bC1.

        Importante:
          - FÍSICA (tipo=1): SOLO se llena tipo + cédula; APT consulta RNP
            automáticamente y autocompleta nombre + apellidos.
          - JURÍDICA (tipo=2): se llena tipo + cédula + nombre (razón social).
            APT no autocompleta para jurídicas.
        """
        if not datos:
            self._log.warning("datos_apt.propietario vacío")
            return
        self._fill_if_exists(page, "#bC1", click=True)
        time.sleep(0.3)

        tipo = str(datos.get("tipo_cedula", ""))
        self._set_select(page, "#dllcedulasPropietario", tipo)
        time.sleep(0.5)

        cedula = datos.get("cedula", "")
        # Pre-flight 1: si el operador declaró `cedula_registro_original` y
        # ésta difiere de `cedula`, significa que el registro tenía un dato
        # incompleto/erróneo que el operador corrigió manualmente. Se
        # registra discrepancia para notificar al topógrafo (decisión de
        # oficina: siempre notificar cuando hubo corrección al registro).
        cedula_orig = (datos.get("cedula_registro_original") or "").strip()
        if cedula_orig and cedula_orig != str(cedula).strip():
            from src.agents.apt_discrepancia_handler import (
                crear_discrepancia_registro_incompleto,
            )
            self.discrepancias_rnp.append(
                crear_discrepancia_registro_incompleto(
                    contexto="propietario",
                    campo="cedula",
                    valor=cedula_orig,
                    descripcion=(
                        f"Registro mostraba cédula '{cedula_orig}' (incompleta/errónea). "
                        f"Operador completó manualmente como '{cedula}'. "
                        f"Verificar con RNP que la cédula real coincida."
                    ),
                )
            )
            self._log.warning(
                "[propietario] cédula corregida: registro='%s' → operador='%s'",
                cedula_orig, cedula,
            )
        # Pre-flight 2: si la cédula a enviar a APT tiene formato inválido,
        # registramos otra discrepancia (caso en que se decida enviar la
        # cédula incompleta tal cual está en el registro).
        if cedula:
            ok_fmt, motivo = validar_formato_cedula(cedula, tipo)
            if not ok_fmt:
                from src.agents.apt_discrepancia_handler import (
                    crear_discrepancia_registro_incompleto,
                )
                self.discrepancias_rnp.append(
                    crear_discrepancia_registro_incompleto(
                        contexto="propietario",
                        campo="cedula",
                        valor=cedula,
                        descripcion=motivo,
                    )
                )
                self._log.warning("[propietario] cédula inválida según registro: %s", motivo)
        if cedula:
            # APT consulta RNP automáticamente cuando se setea la cédula via JS
            # + se disparan los eventos input/change/blur. El formato con
            # guiones (X-XXXX-XXXX) es el que APT acepta nativamente.
            self._set_input(page, "#txtcedulaPropietario", cedula)
            # Esperar respuesta async del RNP/TSE (Tribunal Supremo Electoral /
            # Registro Civil) — puede tardar 2-3s en producción.
            time.sleep(3.0)

        # JURÍDICA: APT no autocompleta — llenar nombre (razón social) del dict.
        if tipo == "2":
            self._set_input(page, "#txtnombrePropietario", datos.get("nombre", ""))

        # FÍSICA: validar TSE vs registro (nombre del cajetín). Si difieren,
        # SOBRESCRIBIR con el del registro y registrar discrepancia para
        # avisar al operador. Acepta `nombre_esperado` por compatibilidad
        # hacia atrás (en cuyo caso lo trata como "nombre del registro" pero
        # no puede dividirlo en apellidos).
        #
        # Caso especial común: TSE guarda algunos nombres con `?` en lugar
        # de ñ o tildes (RDF-2026-004 — cédula 2-0466-0095 trajo 'PI?EIRO'
        # en lugar de 'PIÑEIRO'). Como el dict del operador es la fuente
        # autoritativa, SOBRESCRIBIMOS los campos del propietario en bC1
        # antes de guardar el contrato. Para el contrato esto SÍ se respeta
        # (verificado RDF-2026-004) — distinto al INSERT de titulares bP4
        # donde APT enforce TSE.
        if tipo == "1" and cedula:
            n = datos.get("nombre", "")
            a1 = datos.get("apellido1", "")
            a2 = datos.get("apellido2", "")
            # Compat: si vino solo `nombre_esperado` (string completo), úsalo
            if not (n or a1 or a2) and datos.get("nombre_esperado"):
                n = datos["nombre_esperado"]
            res = self._validar_y_corregir_rnp(
                page,
                sel_nombre="#txtnombrePropietario",
                sel_ap1="#txtApellido1Propietario",
                sel_ap2="#txtApellido2Propietario",
                nombre_registro=n,
                ap1_registro=a1,
                ap2_registro=a2,
                cedula=cedula,
                contexto=f"propietario ced={cedula}",
            )
            # Override explícito post-validación: para el propietario del
            # contrato (bC1), si TSE difiere del registro Y el dict trae
            # nombre/apellidos, SOBRESCRIBIR los campos manualmente. APT
            # respeta esto al guardar (verificado RDF-2026-004 — caso
            # 'PI?EIRO' → 'PIÑEIRO').
            if res and res.get("match") is False and (n or a1 or a2):
                if n:
                    self._set_input(page, "#txtnombrePropietario", n)
                if a1:
                    self._set_input(page, "#txtApellido1Propietario", a1)
                if a2:
                    self._set_input(page, "#txtApellido2Propietario", a2)
                self._log.info(
                    "[propietario] sobrescrito post-TSE: %s %s %s",
                    n, a1, a2,
                )

        self._set_input(page, "#txtcorreo", datos.get("correo", ""))
        self._log.info("sección Propietario llenada (tipo=%s)", tipo)

    def _llenar_seccion_profesional(self, page, datos: dict) -> None:
        """Sección Profesional — bC3.

        Firma Digital BCR auto-llena cédula/nombre/carné/teléfono/correo.
        El correo es READONLY en APT — no se puede modificar desde el bot.
        Si el operador necesita otro correo, debe configurarlo en CFIA.

        Regla siempre aplicada:
          - Marcar "Enviar notificación al profesional"
        """
        self._fill_if_exists(page, "#bC3", click=True)
        time.sleep(0.3)
        # Marcar siempre la notificación al profesional
        self._set_check(page, "#ChkNotificaProfesional", True)

        correo = (datos or {}).get("correo", "")
        if correo:
            # APT marca el correo profesional como readonly (lo trae de la
            # Firma Digital BCR). Quitamos el atributo readonly via JS y
            # sobreescribimos al correo corporativo. La validación de envío
            # se hace en el server, que suele aceptar el cambio aunque la UI
            # diga readonly.
            try:
                elem = page.locator("#txtcorreoprofesional")
                if elem.count() == 0:
                    return
                ok = elem.first.evaluate(
                    """(el, val) => {
                        // Quitar readonly/disabled si los tiene
                        el.removeAttribute('readonly');
                        el.removeAttribute('disabled');
                        el.readOnly = false;
                        el.disabled = false;
                        el.focus();
                        el.value = String(val);
                        el.dispatchEvent(new Event('input',  {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        el.dispatchEvent(new Event('blur',   {bubbles: true}));
                        if (window.jQuery) {
                            const $el = jQuery(el);
                            $el.trigger('change').trigger('blur');
                            try { if ($el.valid) $el.valid(); } catch(e) {}
                        }
                        return el.value === String(val);
                    }""",
                    correo,
                )
                if ok:
                    self._log.info("correo profesional cambiado a %s", correo)
                else:
                    self._log.warning("correo profesional NO se pudo cambiar (APT lo bloqueó)")
            except Exception as exc:
                self._log.warning("error en correo profesional: %s", exc)

    def _llenar_seccion_contratante(self, page, datos_apt: dict) -> None:
        """Sección Contratante — bC2. Si contratante_es_propietario, marca el checkbox."""
        es_mismo = datos_apt.get("contratante_es_propietario", True)
        if es_mismo:
            self._set_check(page, "#checkContratante", True)
            self._log.info("contratante = propietario (checkbox marcado)")
            return
        # Sino, llenar datos del contratante
        self._set_check(page, "#checkContratante", False)
        contratante = datos_apt.get("contratante", {})
        self._fill_if_exists(page, "#bC2", click=True)
        time.sleep(0.3)
        self._set_select(page, "#dllcedulasContratante", contratante.get("tipo_cedula", ""))
        self._set_input(page, "#txtcedulaContratante", contratante.get("cedula", ""))
        self._set_input(page, "#txtnombrecontratante", contratante.get("nombre", ""))
        self._set_input(page, "#txtcorreocontratante", contratante.get("correo", ""))
        self._log.info("sección Contratante llenada")

    def _aceptar_modal_swal_si_es_aviso_protocolo(self, page) -> bool:
        """Dismiss el modal swal2 'El protocolo seleccionado no está activo'.

        Es ESPERADO en contratos que continúan trabajos viejos (el operador
        deliberadamente elige un protocolo no-activo). La discrepancia se
        registra por separado vía `_llenar_seccion_protocolo`, así que aquí
        sólo cerramos el modal para continuar el llenado.

        Devuelve True si había modal de protocolo y se cerró; False si no
        había modal o si era de otro tipo (no lo toca para no enmascarar
        anomalías reales).
        """
        try:
            info = page.evaluate(
                """() => {
                    const p = document.querySelector('.swal2-popup');
                    if (!p || p.offsetParent === null) return null;
                    return {
                        title: (document.querySelector('.swal2-title')?.innerText || '').trim(),
                        html:  (document.querySelector('.swal2-html-container')?.innerText || '').trim(),
                    };
                }"""
            )
            if not info:
                return False
            t = (info.get("title") or "").lower()
            h = (info.get("html") or "").lower()
            es_aviso_protocolo = (
                ("atención" in t or "atencion" in t)
                and "protocolo" in h
                and ("no está activo" in h or "no esta activo" in h)
            )
            if not es_aviso_protocolo:
                self._log.debug(
                    "modal post-protocolo no era aviso esperado: %r — %r",
                    info.get("title"), info.get("html"),
                )
                return False
            page.evaluate("document.querySelector('button.swal2-confirm')?.click();")
            time.sleep(1.0)
            self._log.info("modal 'protocolo no activo' aceptado automáticamente")
            return True
        except Exception as exc:
            self._log.warning("error aceptando modal protocolo: %s", exc)
            return False

    def _leer_protocolo_activo_del_dropdown(self, page) -> str:
        """Lee el protocolo activo (más reciente) del dropdown #dllprotocolo.

        Regla de oficina: el último protocolo del dropdown es siempre el
        activo (el del año en curso). El bot lo lee dinámicamente para
        evitar hardcoding y para que funcione automáticamente cada vez
        que CFIA agrega un nuevo protocolo al topógrafo.
        """
        try:
            return page.evaluate(
                r"""() => {
                    const sel = document.querySelector('#dllprotocolo');
                    if (!sel) return '';
                    const opts = [...sel.options].filter(o => o.value && o.value !== '0');
                    return opts.length ? String(opts[opts.length - 1].value) : '';
                }"""
            ) or ""
        except Exception:
            return ""

    def _llenar_seccion_protocolo(self, page, datos: dict,
                                  *, protocolo_activo_topografo: str = "") -> None:
        """Sección Protocolo — bC4.

        Si el número de protocolo difiere del último del dropdown (el más
        reciente, que es el activo del año en curso), se registra una
        discrepancia tipo `protocolo_diferente_al_activo` para avisar al
        operador: es probable que este plano sea continuación de un contrato
        viejo y los honorarios ya hayan sido cobrados en el contrato
        original. Decisión final siempre queda en manos del operador.

        Orden de preferencia para resolver el protocolo activo:
          1. `protocolo_activo_topografo` (per-user, de tabla usuarios) — mejor
             cuando hay multi-topógrafo en la oficina
          2. Lectura DOM (último option del dropdown) — se actualiza solo cuando
             CFIA emite uno nuevo y el dropdown se refresca
          3. `settings.PROTOCOLO_ACTIVO_TOPOGRAFO` — override de testing
        """
        if not datos:
            self._log.warning("datos_apt.protocolo vacío")
            return
        self._fill_if_exists(page, "#bC4", click=True)
        time.sleep(0.3)
        numero_prot = str(datos.get("numero", "")).strip()
        self._set_select(page, "#dllprotocolo", numero_prot)
        # APT muestra modal "Atención: El protocolo seleccionado no está activo"
        # cuando elegimos un protocolo viejo. Es ESPERADO y operativo lo
        # acepta (caso continuación de contratos viejos). Lo dismissamos
        # automáticamente — la discrepancia ya queda registrada por la
        # detección abajo.
        time.sleep(0.8)
        self._aceptar_modal_swal_si_es_aviso_protocolo(page)
        self._set_input(page, "#txtfolio", datos.get("folio", ""))
        # tipo_proyecto_modal — algunos forms usan este modal interno
        self._set_select(page, "#ddlTipoProyectoModal", datos.get("tipo_proyecto_modal", "27"))

        # ── Detección: protocolo distinto al activo del año ──────────
        # Preferencia: per-user > DOM > settings (cada uno mejor que el next).
        protocolo_activo = str(protocolo_activo_topografo or "").strip()
        if not protocolo_activo:
            protocolo_activo = self._leer_protocolo_activo_del_dropdown(page)
        if not protocolo_activo:
            from config.settings import PROTOCOLO_ACTIVO_TOPOGRAFO as _PA
            protocolo_activo = str(_PA or "").strip()
        if (
            protocolo_activo
            and numero_prot
            and numero_prot != protocolo_activo
        ):
            self.discrepancias_rnp.append({
                "tipo":         "protocolo_diferente_al_activo",
                "ts":           __import__("datetime").datetime.now().isoformat(timespec="seconds"),
                "contexto":     "protocolo",
                "campo":        "protocolo.numero",
                "valor":        numero_prot,
                "valor_esperado": protocolo_activo,
                "descripcion":  (
                    f"Protocolo declarado ({numero_prot}) NO coincide con el "
                    f"protocolo activo del topógrafo ({protocolo_activo} — "
                    f"último del dropdown). Posible continuación de contrato "
                    f"anterior — verificar si corresponde poner honorarios en 0 "
                    f"y referenciar el contrato viejo en observaciones."
                ),
            })
            self._log.warning(
                "[protocolo] %s ≠ activo %s — posible continuación de contrato anterior",
                numero_prot, protocolo_activo,
            )
        self._log.info("sección Protocolo llenada (numero=%s)", numero_prot)

    def _llenar_seccion_proyecto(self, page, datos: dict, *, max_planos: int = 1) -> None:
        """Sección Proyecto — bC5. Maneja dropdowns en cascada (provincia→cantón→distrito).

        El campo 'descripción' (#txtAreadetalle, label 'El contratante pacta con
        el profesional, los servicios de consultoría de:') se rellena con
        "X plano(s) a catastrar" usando max_planos del contrato.
        """
        if not datos:
            self._log.warning("datos_apt.proyecto vacío")
            return
        self._fill_if_exists(page, "#bC5", click=True)
        time.sleep(0.3)

        # Tipo de plano APT — siempre 27 (Plano Simple) según política del topógrafo
        self._set_select(page, "#ddlTipoProyecto", datos.get("tipo_plano_apt", "27"))

        # Ubicación cascada: provincia → esperar carga AJAX → cantón → esperar → distrito
        # APT carga cantones/distritos via AJAX; hay que esperar más que un sleep fijo.
        # APT exige zero-padding en cantón/distrito ("02", "07" no "2", "7") — auto-pad.
        def _pad(s: str) -> str:
            s = str(s or "").strip()
            return s.zfill(2) if s.isdigit() and len(s) == 1 else s
        provincia = datos.get("provincia", "")
        canton    = _pad(datos.get("canton", ""))
        distrito  = _pad(datos.get("distrito", ""))
        if provincia:
            self._set_select(page, "#dllProvincia", provincia)
            time.sleep(1.2)  # cargar cantones via AJAX
        if canton:
            self._set_select(page, "#ddlCanton", canton)
            time.sleep(1.2)  # cargar distritos via AJAX
        if distrito:
            self._set_select(page, "#ddlDistrito", distrito)
            time.sleep(0.4)

        # Descripción del proyecto — campo "El contratante pacta con el profesional,
        # los servicios de consultoría de:". Si no se proveyó descripcion explícita,
        # auto-generar como "X plano(s) a catastrar" usando max_planos del contrato.
        descripcion = datos.get("descripcion") or ""
        if not descripcion:
            n = int(max_planos) if str(max_planos).isdigit() else 1
            descripcion = f"{n} {'plano' if n == 1 else 'planos'} a catastrar"
        self._set_input(page, "#txtAreadetalle", descripcion)

        # Naturaleza: 1=Derecho, 2=Equidad, 3=Pericial.
        # Regla de oficina: SIEMPRE Equidad (2). Permite override si se pasa explícito.
        nat = str(datos.get("naturaleza", "2"))
        nat_sel = {"1": "#rbDerecho", "2": "#rbEquidad", "3": "#rbPericial"}.get(nat)
        if nat_sel:
            self._set_check(page, nat_sel, True)

        # Provincia/Cantón/Distrito de FIRMA del contrato.
        # Regla de oficina: SIEMPRE iguales a la ubicación del terreno
        # (a menos que se pase un override explícito en datos_apt).
        firma_prov = datos.get("firma_provincia") or provincia
        firma_cant = _pad(datos.get("firma_canton") or canton)
        firma_dist = _pad(datos.get("firma_distrito") or distrito)
        if firma_prov:
            self._set_select(page, "#ddlFirmaProvincia", firma_prov)
            time.sleep(1.2)
        if firma_cant:
            self._set_select(page, "#ddlFirmaCanton", firma_cant)
            time.sleep(1.2)
        if firma_dist:
            self._set_select(page, "#ddlFirmaDistrito", firma_dist)
            time.sleep(0.4)

        self._log.info("sección Proyecto llenada")

    def _llenar_seccion_controversias(self, page, datos: dict) -> None:
        """Sección Controversias — bC6."""
        # Solo radio Tribunal por ahora
        tribunal = datos.get("tribunal_check_2") if datos else None
        if tribunal:
            self._fill_if_exists(page, "#bC6", click=True)
            time.sleep(0.3)
            self._set_check(page, "#Controversion_Check_2", bool(tribunal))
            self._log.info("sección Controversias llenada")

    def _llenar_seccion_general(self, page, datos: dict) -> None:
        """Sección General — area, honorarios, entero, etc. Visible directo.

        Auto-completa `honorarios_letras` desde `honorarios` si no se proveyó,
        usando el conversor número→letras.
        """
        if not datos:
            self._log.warning("datos_apt.general vacío")
            return

        # Reglas globales de oficina (siempre se aplican):
        #  - Interés Social: SIEMPRE desmarcado
        #  - Notificación al cliente: SIEMPRE desmarcado
        self._set_check(page, "#checkInteresSocial", False)
        self._set_check(page, "#ChkNotificaCliente", False)

        self._set_input(page, "#txtareapredio", datos.get("area_predio", ""))
        self._set_input(page, "#txtareareal", datos.get("area_real", ""))
        self._set_select(page, "#dllTipoMoneda", datos.get("moneda", "1"))  # 1=COLON
        honorarios = datos.get("honorarios", "")
        self._set_input(page, "#txtValorAproximadoHonorarios", honorarios)

        # Honorarios en letras: si no se proveyó, computar desde el monto
        letras = datos.get("honorarios_letras", "")
        if not letras and honorarios:
            try:
                from src.utils.num_to_letras import numero_a_letras_colones  # noqa: PLC0415
                letras = numero_a_letras_colones(honorarios)
            except Exception:
                pass
        self._set_input(page, "#txtLetras", letras)
        if datos.get("exoneracion_honorarios"):
            self._set_check(page, "#CheckExonerarHonorarios", True)
        self._set_input(page, "#txtAdelanto", datos.get("adelanto", ""))
        self._set_input(page, "#txtpagosparciales", datos.get("pagos_parciales", ""))
        self._set_input(page, "#txtPlazoEntrega", datos.get("plazo_entrega", ""))
        self._set_input(page, "#txtmaximoplanos", datos.get("max_planos", "1"))
        self._set_input(page, "#txtObservaciones", datos.get("observaciones", ""))
        # Coordenadas
        self._set_input(page, "#txtNorte", datos.get("norte", ""))
        self._set_input(page, "#txtEste", datos.get("este", ""))
        # Composición — radio
        comp = datos.get("composicion", "unipersonal")
        if comp == "unipersonal":
            self._set_check(page, "#ChkComposicionUnipersonal", True)
        elif comp == "colegiado":
            self._set_check(page, "#ChkComposicionColegiado", True)
        # Datos Entero (BCR)
        entero = datos.get("entero") or {}
        self._set_input(page, "#txtNumEntero", entero.get("numero", ""))
        self._set_input(page, "#txtTotalCFIA", entero.get("monto", ""))
        self._set_input(page, "#FechaPago", entero.get("fecha", ""))
        # Archivo PDF entero — sólo si se proveyó la ruta y existe
        ruta_pdf = entero.get("archivo_pdf", "")
        if ruta_pdf and Path(ruta_pdf).exists():
            try:
                page.set_input_files("#fileEntero", ruta_pdf)
                self._log.info("entero PDF subido: %s", ruta_pdf)
            except Exception as exc:
                self._log.warning("no se pudo subir entero PDF: %s", exc)
        self._log.info("sección General llenada")

    def _llenar_seccion_plano_generales(self, page, datos: dict) -> None:
        """Sección 'Generales' del PLANO (post-creación del contrato — bP1).

        Datos esperados en `datos` (todos opcionales — vacío = skip):
          tipo_plano:       código APT (default "27" Plano Simple)
          descripcion:      nombre del plano (del cajetín — ej "ROGRANJ(1)")
          area_real:        área del cajetín (m²)
          area_registro:    suma de áreas según registro (m²)
          tipo_zona:        "2" RURAL / "3" URBANO (auto-regla por área)
          tipo_ubicacion:   solo URBANO (E, CH, etc.)
          tipo_uso:         código APT (mapeado de la naturaleza del registro)
          tamanno:          tamaño físico (auto-detectable del PDF)
          tipo_coordenada:  "3" CRTM05 siempre (regla legal CR)
          norte / este:     centroide AREAL del polígono (2 decimales)
          vertices:         cantidad de puntos del listado de coordenadas
          del_estado:       bool — siempre False salvo planos del Estado
        """
        if not datos:
            self._log.warning("datos_apt.plano vacío")
            return

        # Expandir bP1 (sección Generales del plano)
        self._fill_if_exists(page, "#bP1", click=True)
        time.sleep(0.3)

        # Tipo de plano — default 27 (Plano Simple)
        self._set_select(page, "#ddlTipoPlano", datos.get("tipo_plano", "27"))

        # Descripción (nombre del plano)
        self._set_input(page, "#txtDescripcion", datos.get("descripcion", ""))

        # Áreas — IMPORTANTE: usar selector por NAME en lugar de ID porque
        # `#txtAreaReal` (PLANO) choca case-insensitive con `#txtareareal`
        # (CONTRATO) en Chrome's CSS selector engine. El selector por name es
        # case-sensitive y nos da el elemento correcto.
        self._set_input(
            page,
            "input[name='DatosPlano.Generales.AreaReal']",
            datos.get("area_real", ""),
        )
        self._set_input(
            page,
            "input[name='DatosPlano.Generales.AreaRegistro']",
            datos.get("area_registro", ""),
        )

        # Tipo de zona — códigos APT verificados (mayo 2026):
        #   2 = RURAL    (área >= 2000 m²)
        #   3 = URBANO   (área < 2000 m²)
        # NO usar "1" — no es un valor válido en el dropdown.
        zona = str(datos.get("tipo_zona", "")).strip()
        # Normalizar valores históricos incorrectos:
        if zona == "1":
            self._log.warning("tipo_zona='1' es inválido — auto-corrigiendo a '3' URBANO")
            zona = "3"
        if not zona and datos.get("area_real"):
            try:
                area = float(str(datos["area_real"]).replace(",", ""))
                zona = "2" if area >= 2000 else "3"
            except Exception:
                zona = ""
        self._set_select(page, "#ddlTipoZona", zona)
        time.sleep(0.6)  # tipo_ubicacion carga opciones tras seleccionar zona

        # Tipo ubicación (solo URBANO) — códigos APT:
        #   5 = Parcela A    6 = Parcela B    7 = Parcela C
        #   8 = Parcela CH   9 = Parcela D   10 = Parcela E
        # Acepta letra ('A','B','C','CH','D','E') y la mapea al código.
        ubic_raw = str(datos.get("tipo_ubicacion", "")).strip().upper()
        _UBIC_LETRA_A_CODIGO = {
            "A": "5", "B": "6", "C": "7", "CH": "8", "D": "9", "E": "10",
        }
        ubic_codigo = _UBIC_LETRA_A_CODIGO.get(ubic_raw, ubic_raw)  # si ya es código pasa intacto
        if ubic_codigo:
            self._set_select(page, "#ddlTipoUbicacion", ubic_codigo)

        # Tipo uso (mapeado desde naturaleza del registro)
        self._set_select(page, "#ddlTipoUso", datos.get("tipo_uso", ""))

        # Tamaño físico del plano
        self._set_select(page, "#ddlTamanno", datos.get("tamanno", ""))

        # Tipo coordenada — regla legal CR siempre 3 (CRTM05)
        self._set_select(page, "#ddlTipoCoordenada", datos.get("tipo_coordenada", "3"))

        # Coordenadas centroide areal
        self._set_input(page, "#txtNorteP",  datos.get("norte", ""))
        self._set_input(page, "#txtEsteP",   datos.get("este", ""))

        # Cantidad de vértices
        self._set_input(page, "#txtVertices", datos.get("vertices", ""))

        # "Del Estado" — siempre desmarcado salvo override
        self._set_check(page, "#chkDelEstado", bool(datos.get("del_estado", False)))

        self._log.info("sección Generales del Plano llenada")

    # ── Helpers para llenar secciones del PLANO (post-creación contrato) ──
    #
    # El plano APT tiene 7 sub-secciones en acordeones bP1-bP7:
    #   bP1 Generales, bP2 Fincas, bP3 Situación Geográfica (default),
    #   bP4 Titulares (default), bP5 Planos a Modificar, bP6 Enteros,
    #   bP7 Archivos (subida ANVERSO/ENTERO/DERROTERO).
    # Selectores verificados contra el portal real (mayo 2026).

    def _aceptar_modal_swal(self, page, *, esperar_segundos: float = 1.5) -> bool:
        """Click en el botón 'Aceptar' de un modal SweetAlert2 si está abierto.

        Devuelve True si había modal y se cerró, False si no había modal.
        """
        try:
            ok = page.evaluate(
                """() => {
                    const b = document.querySelector('button.swal2-confirm');
                    if (b && b.offsetParent !== null) { b.click(); return true; }
                    return false;
                }"""
            )
            if ok:
                time.sleep(esperar_segundos)
            return bool(ok)
        except Exception as exc:
            self._log.warning("error cerrando modal swal: %s", exc)
            return False

    # Frases conocidas que indican que el modal swal2 NO es un éxito y debe
    # tratarse como anomalía (circuit breaker).
    _SWAL_PATRONES_ERROR = (
        "error", "atención", "atencion", "no se pudo", "no se ha podido",
        "fallo", "falló", "inválid", "invalid", "no es válid", "no es valid",
        "requerid", "completar los siguientes", "debe indicar",
    )
    _SWAL_PATRONES_OK = (
        "éxito", "exito", "exitoso", "guardado", "registrado", "enviado",
    )

    def _detectar_anomalia_modal(self, page) -> dict | None:
        """Lee el modal swal2 visible (si lo hay) y determina si es:
          - un caso conocido OK → devuelve None (no anomalía)
          - una confirmación esperada (¿Desea...?) → devuelve None
          - un error o mensaje inesperado → devuelve {title, html} para
            que el caller lo trate como anomalía.
        """
        try:
            info = page.evaluate(
                """() => {
                    const p = document.querySelector('.swal2-popup');
                    if (!p || p.offsetParent === null) return null;
                    return {
                        title: (document.querySelector('.swal2-title')?.innerText || '').trim(),
                        html:  (document.querySelector('.swal2-html-container')?.innerText || '').trim(),
                        icon:  (p.querySelector('.swal2-icon')?.className || '').trim(),
                    };
                }"""
            )
        except Exception as exc:
            self._log.debug("error leyendo modal: %s", exc)
            return None

        if not info:
            return None
        if not isinstance(info, dict):
            return None
        title = (info.get("title") or "").lower()
        body  = (info.get("html") or "").lower()
        icon  = (info.get("icon") or "").lower()
        text  = f"{title} {body}"

        # Caso OK conocido — no es anomalía
        if any(p in text for p in self._SWAL_PATRONES_OK):
            return None
        # Caso confirmación esperada (¿Desea...?) — no es anomalía
        if "¿" in title or "?" in title or "desea" in text:
            return None
        # Si el icono es 'error' / 'warning' o el texto matchea patrones
        if "error" in icon or "warning" in icon:
            return info
        if any(p in text for p in self._SWAL_PATRONES_ERROR):
            return info
        # Modal desconocido (no OK, no confirmación, no error claro) →
        # devolverlo igual para que el caller decida si es anomalía
        return info

    def _llenar_seccion_plano_fincas(self, page, fincas: list) -> None:
        """Registra cada finca en la sección bP2 del plano.

        `fincas` es una lista de dicts con:
          - provincia: código numérico (1-7)
          - numero: número de finca (sin guiones, sin letras de provincia)
          - derecho: número de derecho (default "000")
          - duplicado: letra A-Z (default "" = vacío)

        Para reunión de fincas se incluyen TODAS las que entran (completas o
        parciales). Cada una se guarda con click en GuardarFinca() y un modal
        swal2 confirma cada guardado.
        """
        if not fincas:
            self._log.warning("datos_apt.plano.fincas vacío")
            return
        # Expandir bP2
        self._fill_if_exists(page, "#bP2", click=True)
        time.sleep(0.5)

        for i, f in enumerate(fincas, start=1):
            # Setear provincia → APT muestra campos derecho/duplicado tras meter número
            self._set_select(page, "#ddlProvinciaFinca", str(f.get("provincia", "")))
            time.sleep(0.6)
            self._set_input(page, "#txtNumFinca", str(f.get("numero", "")))
            time.sleep(2.0)  # esperar consulta async de APT al RNP
            self._set_input(page, "#txtDerecho", str(f.get("derecho", "000")))
            duplicado = f.get("duplicado", "") or "0"
            if duplicado not in ("", "0"):
                self._set_select(page, "#ddlDuplicado", duplicado)
            # Click GuardarFinca
            try:
                page.evaluate(
                    """() => {
                        const b = Array.from(document.querySelectorAll('button'))
                            .find(x => /GuardarFinca/.test(x.getAttribute('onclick') || ''));
                        if (b) b.click();
                    }"""
                )
            except Exception as exc:
                self._log.warning("error click GuardarFinca: %s", exc)
            time.sleep(2.5)
            # Validación estricta: si APT devuelve modal de error
            # (finca no existe, derecho inválido, etc.) → APTAnomalyError
            anom = self._detectar_anomalia_modal(page)
            if anom and any(
                p in (anom.get("title", "") + " " + anom.get("html", "")).lower()
                for p in ("error", "atención", "no se pudo", "inválid", "no existe")
            ):
                raise APTAnomalyError(
                    f"APT rechazó finca {f.get('provincia')}-{f.get('numero')}: "
                    f"{anom.get('title','?')} — {anom.get('html','?')}",
                    contexto=f"bP2 finca#{i}",
                    detalle=str(anom),
                )
            self._aceptar_modal_swal(page)
            self._log.info("finca %d guardada: %s-%s", i, f.get("provincia"), f.get("numero"))

    def _llenar_seccion_plano_titulares(self, page, titulares: list) -> None:
        """Registra titulares ADICIONALES en la sección bP4 del plano.

        El propietario que ya está en el contrato (bC1) NO se repite aquí.
        Solo se agregan los demás propietarios cuando hay múltiples fincas
        con dueños distintos, o cuando una sola finca tiene varios derechos.

        Regla: SOLO se suben "derechos" (titularidad = PROPIETARIO).
        Los usufrutos NO se suben.

        `titulares` es una lista de dicts con:
          - tipo_cedula: "1"=FÍSICA, "2"=JURÍDICA, etc.
          - cedula:     X-XXXX-XXXX (FÍSICA) o 3-XXX-XXXXXX (JURÍDICA)
          - nombre:     opcional — solo para JURÍDICA o cuando RNP no responde
          - apellido1:  opcional — solo si RNP no autocompleta
          - apellido2:  opcional — solo si RNP no autocompleta
          - titularidad: default "5" (PROPIETARIO). NO usar usufructo.

        Selectores verificados contra el portal real (mayo 2026):
          #ddlTipoIdentificacion, #txtIdentificacion, #ddlTitularidad,
          #txtNombreTitular, #txtApellido1Titular, #txtApellido2Titular,
          button onclick="GuardarTitular();"
        """
        if not titulares:
            self._log.info("datos_apt.plano.titulares vacío (solo propietario del contrato)")
            return
        # Expandir bP4
        self._fill_if_exists(page, "#bP4", click=True)
        time.sleep(0.5)

        for i, t in enumerate(titulares, start=1):
            tipo = str(t.get("tipo_cedula", "1"))
            self._set_select(page, "#ddlTipoIdentificacion", tipo)
            time.sleep(0.4)

            cedula = t.get("cedula", "")
            # Pre-flight 1: cédula del registro corregida por el operador
            cedula_orig = (t.get("cedula_registro_original") or "").strip()
            if cedula_orig and cedula_orig != str(cedula).strip():
                from src.agents.apt_discrepancia_handler import (
                    crear_discrepancia_registro_incompleto,
                )
                self.discrepancias_rnp.append(
                    crear_discrepancia_registro_incompleto(
                        contexto=f"titular#{i}",
                        campo="cedula",
                        valor=cedula_orig,
                        descripcion=(
                            f"Registro mostraba cédula '{cedula_orig}' (incompleta/errónea). "
                            f"Operador completó manualmente como '{cedula}'. "
                            f"Verificar con RNP."
                        ),
                    )
                )
                self._log.warning(
                    "[titular#%d] cédula corregida: registro='%s' → operador='%s'",
                    i, cedula_orig, cedula,
                )
            # Pre-flight 2: cédula malformada del registro → discrepancia
            if cedula:
                ok_fmt, motivo = validar_formato_cedula(cedula, tipo)
                if not ok_fmt:
                    from src.agents.apt_discrepancia_handler import (
                        crear_discrepancia_registro_incompleto,
                    )
                    self.discrepancias_rnp.append(
                        crear_discrepancia_registro_incompleto(
                            contexto=f"titular#{i}",
                            campo="cedula",
                            valor=cedula,
                            descripcion=motivo,
                        )
                    )
                    self._log.warning("[titular#%d] cédula inválida: %s", i, motivo)

            if cedula:
                # APT consulta RNP automáticamente vía keyup (mismo patrón
                # que el propietario del contrato). _set_input dispara keyup.
                self._set_input(page, "#txtIdentificacion", cedula)
                time.sleep(3.0)  # esperar respuesta async RNP/TSE

            # Titularidad: SIEMPRE "5" (PROPIETARIO) — no usufructos
            titularidad = str(t.get("titularidad", "5"))
            self._set_select(page, "#ddlTitularidad", titularidad)
            time.sleep(0.3)

            requiere_edit_post_save = False
            if tipo == "2":
                # JURÍDICA: forzar todo del dict (RNP no autocompleta sociedades)
                self._set_input(page, "#txtNombreTitular",    t.get("nombre", ""))
                self._set_input(page, "#txtApellido1Titular", t.get("apellido1", ""))
                self._set_input(page, "#txtApellido2Titular", t.get("apellido2", ""))
            elif cedula:
                # FÍSICA: validar TSE vs registro. La cédula se conserva siempre.
                # Si TSE difiere del registro (caso real RDF-2026-002:
                # 2-0440-0388 → TSE='AMALIA QUESADA' vs registro='GRACE ALVAREZ'),
                # el bot:
                #   1. Deja el INSERT inicial pasar con el nombre TSE (APT
                #      enforce TSE en INSERT, no podemos evitarlo).
                #   2. Marca el titular para editarlo post-save: el ciclo
                #      UPDATE sí respeta los nombres del form.
                res = self._validar_y_corregir_rnp(
                    page,
                    sel_nombre="#txtNombreTitular",
                    sel_ap1="#txtApellido1Titular",
                    sel_ap2="#txtApellido2Titular",
                    nombre_registro=t.get("nombre", ""),
                    ap1_registro=t.get("apellido1", ""),
                    ap2_registro=t.get("apellido2", ""),
                    cedula=cedula,
                    contexto=f"titular#{i} ced={cedula}",
                )
                if res.get("match") is False:
                    requiere_edit_post_save = True

            # Click GuardarTitular() — INSERT
            try:
                page.evaluate(
                    """() => {
                        const b = Array.from(document.querySelectorAll('button'))
                            .find(x => /GuardarTitular/.test(x.getAttribute('onclick') || ''));
                        if (b) b.click();
                    }"""
                )
            except Exception as exc:
                self._log.warning("error click GuardarTitular: %s", exc)
            time.sleep(2.5)
            self._aceptar_modal_swal(page)

            # Edit post-save: para discrepancias TSE/registro, click sobre el
            # registro recién creado, sobrescribir nombres y guardar de nuevo
            # (UPDATE respeta el form). Verificado empíricamente.
            if requiere_edit_post_save:
                self._editar_titular_post_insert(
                    page,
                    cedula=cedula,
                    nombre=t.get("nombre", ""),
                    ap1=t.get("apellido1", ""),
                    ap2=t.get("apellido2", ""),
                    contexto=f"titular#{i}",
                )

            self._log.info("titular %d guardado: tipo=%s ced=%s", i, tipo, cedula)

    def _editar_titular_post_insert(
        self, page, *, cedula: str, nombre: str, ap1: str, ap2: str, contexto: str,
    ) -> bool:
        """Click sobre un titular ya guardado en bP4, sobrescribir nombres y
        guardar (UPDATE). El UPDATE respeta los valores del form, mientras
        que el INSERT inicial enforce el nombre TSE.

        Verificado en RDF-2026-002 (cédula 2-0440-0388): el flow funciona
        cuando se hace en dos pasos.
        """
        cedula_digits = "".join(c for c in str(cedula) if c.isdigit())
        if not cedula_digits:
            self._log.warning("[%s] no se pudo editar — cédula vacía", contexto)
            return False

        # Click en SeleccionarTitular del record cuya cédula matchea
        ok = page.evaluate(
            r"""(digits) => {
                const sels = [...document.querySelectorAll('[onclick^="SeleccionarTitular"]')];
                const target = sels.find(el =>
                    new RegExp('SeleccionarTitular\\([^)]*' + digits + '[^)]*\\)')
                        .test(el.getAttribute('onclick') || '')
                );
                if (!target) return false;
                target.click();
                return true;
            }""",
            cedula_digits,
        )
        if not ok:
            self._log.warning("[%s] no encontré card del titular cédula %s para editar", contexto, cedula)
            return False
        time.sleep(1.0)

        # Sobrescribir campos en el form (que ahora tiene los datos cargados)
        self._set_input(page, "#txtNombreTitular",    nombre)
        self._set_input(page, "#txtApellido1Titular", ap1)
        self._set_input(page, "#txtApellido2Titular", ap2)
        time.sleep(0.3)

        # Click GuardarTitular — esta vez es UPDATE
        try:
            page.evaluate(
                """() => {
                    const b = Array.from(document.querySelectorAll('button'))
                        .find(x => /GuardarTitular/.test(x.getAttribute('onclick') || ''));
                    if (b) b.click();
                }"""
            )
        except Exception as exc:
            self._log.warning("[%s] error click GuardarTitular (UPDATE): %s", contexto, exc)
            return False
        time.sleep(2.5)
        self._aceptar_modal_swal(page)
        self._log.info(
            "[%s] titular editado post-insert: cédula %s → %s %s %s",
            contexto, cedula, nombre, ap1, ap2,
        )
        return True

    def _llenar_seccion_plano_planos_modificar(self, page, planos: list) -> None:
        """Registra cada plano que el actual MODIFICA en la sección bP5.

        `planos` lista de dicts:
          - provincia: código numérico (letra del prefijo del plano: A→2, etc.)
          - numero: número del plano CON ceros a la izquierda (ej. "0061108")
          - anno: año (ej. "2024")
        """
        if not planos:
            return
        self._fill_if_exists(page, "#bP5", click=True)
        time.sleep(0.5)

        for i, p in enumerate(planos, start=1):
            self._set_select(page, "#ddlProvinciaPlanoModificar", str(p.get("provincia", "")))
            time.sleep(0.4)
            self._set_input(page, "#txtNumPlanoModificar", str(p.get("numero", "")))
            self._set_input(page, "#txtAnnoModificar", str(p.get("anno", "")))
            try:
                page.evaluate(
                    """() => {
                        const b = Array.from(document.querySelectorAll('button'))
                            .find(x => /GuardarPlanosModificar/.test(x.getAttribute('onclick') || ''));
                        if (b) b.click();
                    }"""
                )
            except Exception as exc:
                self._log.warning("error click GuardarPlanosModificar: %s", exc)
            time.sleep(2.5)
            # Validación estricta: APT rechaza si el plano a modificar no existe
            anom = self._detectar_anomalia_modal(page)
            if anom and any(
                pat in (anom.get("title", "") + " " + anom.get("html", "")).lower()
                for pat in ("error", "no se pudo", "inválid", "no existe")
            ):
                raise APTAnomalyError(
                    f"APT rechazó plano a modificar "
                    f"{p.get('provincia')}-{p.get('numero')}-{p.get('anno')}: "
                    f"{anom.get('title','?')} — {anom.get('html','?')}",
                    contexto=f"bP5 plano_modificar#{i}",
                    detalle=str(anom),
                )
            self._aceptar_modal_swal(page)
            self._log.info("plano a modificar %d guardado: %s-%s-%s",
                           i, p.get("provincia"), p.get("numero"), p.get("anno"))

    def _llenar_seccion_plano_enteros(self, page, datos: dict) -> None:
        """Sección bP6 Enteros — datos del comprobante BCR.

        Reglas (verificadas contra entero.pdf real):
          - txtNumEntero      = número del entero del cajetín del plano / BCR
          - FechaPago         = fecha de pago del comprobante BCR
          - txtTotalCFIA      = "Monto total" del timbre 038 R.R.P.CFIA
                                (NO el monto pagado con descuento)
          - txtTotalRegistro  = "Monto total" del timbre 001 REGISTRO NACIONAL
          - txtMontoPagado    = monto TASADO total (suma antes de descuentos)
          - txtMontoCIT_NTRIP = monto del timbre 055 CIT-NTRIP
        """
        if not datos:
            return
        self._fill_if_exists(page, "#bP6", click=True)
        time.sleep(0.5)
        self._set_input(page, "#txtNumEntero",      datos.get("numero", ""))
        self._set_input(page, "#txtTotalCFIA",      datos.get("total_cfia", ""))
        self._set_input(page, "#txtTotalRegistro",  datos.get("total_registro", ""))
        self._set_input(page, "#txtMontoPagado",    datos.get("monto_pagado", ""))
        self._set_input(page, "#txtMontoCIT_NTRIP", datos.get("cit_ntrip", ""))
        self._set_input(page, "#FechaPago",         datos.get("fecha", ""))
        try:
            page.evaluate(
                """() => {
                    const b = Array.from(document.querySelectorAll('button'))
                        .find(x => /GuardarEnteros/.test(x.getAttribute('onclick') || ''));
                    if (b) b.click();
                }"""
            )
        except Exception as exc:
            self._log.warning("error click GuardarEnteros: %s", exc)
        time.sleep(2.5)
        # Validación estricta: APT puede rechazar si los timbres no
        # cuadran con su tabla (montos esperados por tipo de plano).
        anom = self._detectar_anomalia_modal(page)
        if anom and any(
            pat in (anom.get("title", "") + " " + anom.get("html", "")).lower()
            for pat in ("error", "no se pudo", "inválid", "no coincide")
        ):
            raise APTAnomalyError(
                f"APT rechazó datos del entero {datos.get('numero')}: "
                f"{anom.get('title','?')} — {anom.get('html','?')}",
                contexto="bP6 enteros",
                detalle=str(anom),
            )
        self._aceptar_modal_swal(page)
        self._log.info("sección Enteros guardada (entero=%s)", datos.get("numero"))

    # Códigos del dropdown #ddlTipoArchivo (sección bP7)
    TIPO_ARCHIVO_ANVERSO   = "1"
    TIPO_ARCHIVO_VISADO    = "2"
    TIPO_ARCHIVO_ENTERO    = "10"
    TIPO_ARCHIVO_DERROTERO = "16"

    def _subir_archivo_plano(self, page, tipo_codigo: str, ruta_archivo: str | Path) -> bool:
        """Sube un archivo en la sección bP7 Archivos.

        Args:
            tipo_codigo: código del dropdown ddlTipoArchivo
                         ("1"=ANVERSO, "2"=VISADO, "10"=ENTERO, "16"=DERROTERO)
            ruta_archivo: ruta absoluta al archivo

        Returns True si subió OK (modal "Exitoso..."), False si falló.

        NOTA: minuta + imagenminuta NO se suben — son archivos internos del
        topógrafo que quedan solo en la carpeta del expediente.
        """
        ruta = str(ruta_archivo)
        if not Path(ruta).exists():
            self._log.warning("archivo no existe: %s", ruta)
            return False
        # Expandir bP7
        self._fill_if_exists(page, "#bP7", click=True)
        time.sleep(0.4)
        # Seleccionar tipo
        self._set_select(page, "#ddlTipoArchivo", tipo_codigo)
        time.sleep(0.5)
        # Subir archivo
        try:
            page.locator("#file").set_input_files(ruta)
            time.sleep(1.0)
        except Exception as exc:
            self._log.warning("error subiendo archivo %s: %s", ruta, exc)
            return False
        # Click CARGAR ARCHIVO (#btnCargarArchivo dispara ValidarArchivo())
        try:
            ok = page.evaluate(
                """() => {
                    const b = document.querySelector('#btnCargarArchivo');
                    if (b) { b.click(); return true; }
                    return false;
                }"""
            )
            if not ok:
                return False
        except Exception as exc:
            self._log.warning("error click cargar archivo: %s", exc)
            return False
        time.sleep(3.0)
        # Verificar éxito en el modal
        try:
            modal_text = page.evaluate(
                """() => {
                    const x = document.querySelector('.swal2-popup');
                    return x && x.offsetParent ? x.innerText : '';
                }"""
            )
            exito = "Exitoso" in (modal_text or "") or "guardó con éxito" in (modal_text or "")
        except Exception:
            exito = False
        self._aceptar_modal_swal(page)
        if exito:
            self._log.info("archivo subido tipo=%s: %s", tipo_codigo, Path(ruta).name)
        else:
            self._log.warning("subida puede haber fallado: %s", Path(ruta).name)
        return exito

    def _enviar_plano_cfia(self, page) -> bool:
        """Click en 'ENVIAR AL CFIA' y confirma el modal swal.

        Es el paso FINAL de la presentación R1 — después de esto, el plano
        queda en revisión por ~7 días y NO se puede modificar.

        Returns True si el envío fue exitoso, False si falló.
        """
        # Click en el botón
        try:
            ok = page.evaluate(
                """() => {
                    const b = document.querySelector('#BtnEnviarAgrimensura');
                    if (b) { b.click(); return true; }
                    return false;
                }"""
            )
            if not ok:
                self._log.warning("no se encontró #BtnEnviarAgrimensura")
                return False
        except Exception as exc:
            self._log.warning("error click ENVIAR AL CFIA: %s", exc)
            return False
        time.sleep(2.0)
        # Modal de confirmación: "¿Desea enviar el plano a revisión?"
        try:
            page.evaluate(
                """() => {
                    const b = document.querySelector('button.swal2-confirm');
                    if (b) b.click();
                }"""
            )
        except Exception:
            pass
        time.sleep(5.0)  # procesa el envío al CFIA (puede tardar)
        # Verificar éxito final
        try:
            modal_text = page.evaluate(
                """() => {
                    const x = document.querySelector('.swal2-popup');
                    return x && x.offsetParent ? x.innerText : '';
                }"""
            )
            exito = "enviado" in (modal_text or "").lower() or "éxito" in (modal_text or "").lower()
        except Exception:
            exito = False
        self._aceptar_modal_swal(page)
        if exito:
            self._log.info("plano enviado al CFIA exitosamente")
        return exito

    def _llenar_seccion_firmas(self, page, datos: dict) -> None:
        """Sección Firmas — bC8.

        Regla de oficina: la fecha de firma SIEMPRE es la del día de hoy
        (a menos que se pase un override explícito en datos.fecha).
        """
        from datetime import date as _date
        self._fill_if_exists(page, "#bC8", click=True)
        time.sleep(0.3)
        fecha = (datos or {}).get("fecha") or _date.today().isoformat()
        self._set_input(page, "#txtFechaFirma", fecha)
        self._log.info("sección Firmas: fecha = %s", fecha)

    def _fill_if_exists(self, page, selector: str, *, click: bool = False, value: str = "") -> bool:
        """Hace clic o rellena un selector si existe. Devuelve True si encontrado."""
        elem = page.locator(selector)
        if elem.count() == 0:
            return False
        try:
            if click:
                elem.first.click()
            elif value:
                elem.first.fill(value)
        except Exception as exc:
            self._log.warning("_fill_if_exists %s: %s", selector, exc)
        return True

    def _extraer_tramite_de_pagina(self, page) -> str:
        """Intenta extraer el número de trámite de la URL o del contenido de la página.

        El portal APT normalmente muestra el trámite en la URL o en un campo
        de la página después de guardar (ej. ?numContrato=12345 o en un <span>).
        """
        import re as _re

        # Estrategia 1: URL contiene el número de trámite
        url = page.url
        for patron in [r"[Nn]um[Cc]ontrato=(\d+)", r"contrato/(\d+)", r"tramite=(\d+)", r"id=(\d+)"]:
            m = _re.search(patron, url, _re.IGNORECASE)
            if m:
                return m.group(1)

        # Estrategia 2: campo de texto visible con número de contrato
        for sel in ["#txtNumContrato", "#numContrato", "input[id*='Contrato']", "input[id*='Tramite']"]:
            elem = page.locator(sel)
            if elem.count() > 0:
                val = elem.first.input_value()
                if val and _re.match(r"\d+", val.strip()):
                    return val.strip()

        # Estrategia 3: texto en la página que parece un número de trámite
        texto = page.inner_text("body")
        m = _re.search(r"[Tt]r[áa]mite[:\s#]*(\d{4,})", texto)
        if m:
            return m.group(1)
        m = _re.search(r"[Cc]ontrato[:\s#]*(\d{4,})", texto)
        if m:
            return m.group(1)

        return ""

    def _ya_logueado(self, page) -> bool:
        """True si la página actual muestra el portal APT con sesión de aplicación activa.

        IMPORTANTE: NO usar la URL como único indicador. El SSO redirige a
        apt.cfia.or.cr/APT2 incluso cuando la sesión de *aplicación* APT está
        expirada (el cookie SSO sigue vivo pero el servidor APT ya no lo reconoce).
        Se requieren elementos del portal autenticado.

        Estrategias (en orden):
        1. Si la URL es del SSO → definitivamente NO autenticados en APT.
        2. Menú dropdown de usuario visible (#navbarDropdownMenuLink con texto).
        3. Navbar APT con contenido sustancial (varios ítems = portal logueado).
        """
        try:
            url = page.url
            # Si seguimos/volvemos al SSO → no hay sesión APT de aplicación
            if "sso.cfia.or.cr" in url:
                return False
            # Menú de usuario: solo aparece post-login APT (contiene nombre del usuario)
            menu = page.locator(SEL_USER_MENU)
            if menu.count() > 0 and menu.first.is_visible():
                texto = menu.first.inner_text().strip()
                if texto and len(texto) > 1:
                    return True
            # Navbar con contenido real (la de APT logueado tiene varios ítems de menú)
            navbar = page.locator(".navbar-nav")
            if navbar.count() > 0:
                texto_nav = navbar.first.inner_text().strip()
                if len(texto_nav) > 20:   # sin login la navbar está vacía o es muy corta
                    return True
            return False
        except Exception:
            return False

    def _confirmar_sesion_apt(self, page) -> bool:
        """Navegación activa a APT_HOME para confirmar que la sesión APT es real.

        Prueba definitiva: si al navegar a APT_HOME el servidor NO nos redirige
        al SSO y la URL queda dentro de apt.cfia.or.cr/APT2, la sesión APT
        de aplicación está activa.

        Si APT redirigió al SSO (sesión APT vencida) pero la **Firma Digital
        BCR sigue conectada**, el bot completa el wizard de SSO
        automáticamente sin pedir PIN (lección operativa: la Firma sólo
        solicita el PIN en el primer ingreso al portal; mientras el
        certificado siga insertado / la app BCR siga abierta, los re-logins
        sólo requieren click "Realizar firma" → "Terminar").

        NO depende de selectores CSS específicos del Home; sólo verifica la
        URL final tras la navegación.

        Devuelve True si la sesión APT de aplicación está activa.
        """
        try:
            page.goto(APT_HOME_URL, wait_until="load", timeout=60_000)
            url = page.url
            self._log.debug("_confirmar_sesion_apt: url tras navegar a Home = %s", url)
            # Si APT redirigió al SSO → intentar auto-completar el wizard
            # de Firma Digital (sin PIN — sólo si el cert sigue activo).
            if "sso.cfia.or.cr" in url:
                self._log.info("APT redirigió a SSO — intentando auto-firma")
                if self._intentar_auto_firma_digital(page):
                    # Verificar nuevamente tras la auto-firma
                    page.goto(APT_HOME_URL, wait_until="load", timeout=60_000)
                    url = page.url
                    if "apt.cfia.or.cr" in url and "/APT2" in url:
                        self._log.info("auto-firma exitosa, sesión APT reactivada")
                        return True
                self._log.info("sesion APT expirada (redirect a SSO)")
                return False
            # Si seguimos en APT2 → sesión válida (APT aceptó la petición sin login)
            if "apt.cfia.or.cr" in url and "/APT2" in url:
                self._log.info("sesion APT activa (URL: %s)", url)
                return True
            # URL inesperada (raro) — no asumir sesión válida
            self._log.warning("_confirmar_sesion_apt: URL inesperada tras navegar a Home: %s", url)
            return False
        except Exception as exc:
            self._log.warning("_confirmar_sesion_apt excepcion: %s", exc)
            return False

    def _intentar_auto_firma_digital(self, page) -> bool:
        """Completa el wizard SSO de Firma Digital sin intervención del operador.

        Aprendizaje operativo (RDF-2026-004): cuando el certificado BCR sigue
        insertado y la app de Firma Digital sigue corriendo, el SSO de CFIA
        NO pide PIN en re-logins — sólo es necesario:

            1. Navegar a sso.cfia.or.cr/sso/DigitalSign.aspx?IdSystem=1
            2. Click "Realizar firma" (#sign) → APT crea el JWT firmado
            3. Click "Terminar" → JS FinishSign() → redirect a APT2/Home

        El PIN del certificado SÓLO se solicita en la PRIMERA entrada al
        portal del día / hasta que la app BCR se cierre. Esto significa que
        los flujos automáticos del bot que corren múltiples expedientes
        seguidos no necesitan interacción humana entre ellos.

        Devuelve True si el wizard se completó (la sesión APT debería
        estar reactivada al volver a APT_HOME_URL).
        """
        try:
            page.goto(
                "https://sso.cfia.or.cr/sso/DigitalSign.aspx?IdSystem=1",
                wait_until="load", timeout=60_000,
            )
            time.sleep(2.0)
            # Paso 3 — Click "Realizar firma" (#sign)
            page.evaluate("document.querySelector('#sign')?.click();")
            time.sleep(3.0)
            # Paso 4 — Click "Terminar" (FinishSign)
            page.evaluate("typeof FinishSign === 'function' && FinishSign();")
            time.sleep(5.0)
            url = page.url
            # Si redirigió a APT2 → éxito
            if "apt.cfia.or.cr" in url and "/APT2" in url:
                return True
            # A veces hay un paso intermedio; verificar de nuevo
            time.sleep(3.0)
            return "apt.cfia.or.cr" in page.url and "/APT2" in page.url
        except Exception as exc:
            self._log.warning("auto-firma digital falló: %s", exc)
            return False

    def _asegurar_login(self, page) -> None:
        """Verifica que la sesión APT esté activa. Lanza APTSesionRequeridaError si no.

        Usa _confirmar_sesion_apt() para navegar a APT_HOME y verificar que el
        servidor no redirige al SSO (la única prueba confiable de sesión activa).
        """
        if self._confirmar_sesion_apt(page):
            return
        # Intentar re-login con usuario/contraseña (solo si el portal lo acepta)
        usuario, password = self.credentials.get_apt()
        page.goto(self._login_url, wait_until="load", timeout=60000)
        if page.locator(SEL_USER_INPUT).count() > 0:
            page.fill(SEL_USER_INPUT, usuario)
            page.fill(SEL_PASS_INPUT, password)
            page.click(SEL_LOGIN_BTN)
            page.wait_for_load_state("load", timeout=60000)
            if self._confirmar_sesion_apt(page):
                self._log.info("re-login APT exitoso (usuario/contrasena)")
                return
        raise APTSesionRequeridaError(
            "Sesión APT expirada. Ejecute APT SESION para autenticarse con Firma Digital."
        )

    # -----------------------------------------------------------------------
    # Consulta de estado
    # -----------------------------------------------------------------------

    def consultar_estado(self, expediente_id: str) -> Optional[str]:
        """Devuelve el estado del tramite (backward compat).

        Wrapper sobre `consultar_apt_data` que solo retorna el campo `estado`
        para mantener compatibilidad con callers viejos (whatsapp_commands,
        consultar_estado_r1/r2).

        Retorna uno de:
            "En Edicion", "Publico y Defectuoso", "Calificacion RN",
            "Publico e Inscrito", o None si no se encontro el tramite.
        """
        data = self.consultar_apt_data(expediente_id)
        if data is None:
            return None
        return data.get("estado")

    def consultar_apt_data(self, expediente_id: str) -> Optional[dict]:
        """Devuelve el dict completo escaneado del portal APT para este expediente.

        Retorna dict con las claves:
            estado    — "En Edicion" | "Publico y Defectuoso" | "Calificacion RN" |
                       "Publico e Inscrito" | ...
            tomo      — string (puede estar vacio si APT no lo asignó aún)
            asiento   — string (puede estar vacio)
            fecha     — string con formato del portal (DD/MM/YYYY u otro)
            proceso   — string ("Pendiente", "Aprobado", etc — depende del portal)
            detalle   — string con la descripcion / nombre del tramite (Nivel 1)

        Retorna None si:
            - el expediente no tiene `apt_tramite` en metadata
            - el tramite no se encontró en la tabla Consulta del portal

        Operacion de solo-lectura, no requiere confirmacion WhatsApp.

        Plan: APT-FULL Fase A (2026-05-29).
        """
        exp = self.db.obtener_expediente(expediente_id)
        if not exp:
            raise AgentError(f"expediente {expediente_id!r} no existe en BD")
        tramite = json.loads(exp.get("metadata_json") or "{}").get("apt_tramite")
        if not tramite:
            self._log.info(
                "expediente %s sin numero de tramite APT — sin estado", expediente_id
            )
            return None

        with self._session() as page:
            self._asegurar_login(page)
            filas = self._buscar_en_consulta(page, tramite)
            if not filas:
                self._log.info("tramite %s no encontrado en Consulta", tramite)
                return None
            fila = filas[0]
            data = {
                "estado":  fila.get("estado", "") or "",
                "tomo":    fila.get("tomo", "") or "",
                "asiento": fila.get("asiento", "") or "",
                "fecha":   fila.get("fecha", "") or "",
                "proceso": fila.get("proceso", "") or "",
                "detalle": fila.get("detalle", "") or "",
            }
            self._log.info(
                "tramite %s data: estado=%s tomo=%s asiento=%s",
                tramite, data["estado"], data["tomo"] or "-", data["asiento"] or "-",
            )
            return data

    def get_tramites_activos(self) -> list[dict]:
        """Lista todos los planos visibles en la tabla de Consulta.

        Devuelve lista de dicts con claves:
            tramite, detalle, tomo, asiento, estado, fecha, proceso
        """
        with self._session() as page:
            self._asegurar_login(page)
            page.goto(self._tramites_url, wait_until="load", timeout=60000)
            return self._parsear_tabla(page)

    def _buscar_en_consulta(self, page, num_tramite: str) -> list[dict]:
        """Navega a Consulta, filtra por tramite y devuelve las filas."""
        page.goto(self._tramites_url, wait_until="load", timeout=60000)
        page.fill(SEL_BUSCAR_CONTRATO, str(num_tramite))
        page.keyboard.press("Enter")
        page.wait_for_load_state("load", timeout=60000)
        time.sleep(0.5)   # tabla puede actualizarse con JS
        return self._parsear_tabla(page)

    def _parsear_tabla(self, page) -> list[dict]:
        """Extrae todas las filas de la tabla de Consulta."""
        rows = page.query_selector_all(SEL_TABLA_FILAS)
        resultado: list[dict] = []
        for row in rows:
            celdas = row.query_selector_all("td")
            if len(celdas) <= COL_PROCESO:
                continue
            texto = [c.inner_text().strip() for c in celdas]
            resultado.append(
                {
                    "tramite": texto[COL_TRAMITE],
                    "detalle": texto[COL_DETALLE],
                    "tomo":    texto[COL_TOMO],
                    "asiento": texto[COL_ASIENTO],
                    "estado":  texto[COL_ESTADO],
                    "fecha":   texto[COL_FECHA],
                    "proceso": texto[COL_PROCESO],
                    "_row":    row,          # referencia para acciones
                }
            )
        return resultado

    # -----------------------------------------------------------------------
    # Navegacion a tramite especifico
    # -----------------------------------------------------------------------

    def _abrir_tramite(self, page, fila: dict) -> None:
        """Hace clic en el enlace de Acciones de una fila para abrir el tramite."""
        row = fila["_row"]
        action_link = row.query_selector("td:first-child a")
        if not action_link:
            raise AgentError(
                f"no se encontro enlace de accion para tramite {fila['tramite']!r}"
            )
        action_link.click()
        page.wait_for_load_state("load", timeout=60000)

    def _ir_a_tab_planos(self, page) -> None:
        """Cambia al tab de Planos dentro del contrato."""
        page.click(SEL_TAB_PLANO)
        page.wait_for_load_state("load", timeout=60000)
        time.sleep(0.3)

    def _seleccionar_plano(self, page, detalle: str) -> None:
        """Hace clic en el lapiz/edicion de la tarjeta del plano indicado."""
        # Las tarjetas de plano contienen el detalle como texto
        tarjeta = page.locator(
            f'div.card:has-text("{detalle}"), '
            f'div[class*="plano"]:has-text("{detalle}")'
        ).first
        if tarjeta.count() == 0:
            # Fallback: seleccionar la primera tarjeta
            tarjeta = page.locator("div.card").first
        # Boton lapiz (primer boton dentro de la tarjeta)
        btn_editar = tarjeta.locator("button, a").first
        btn_editar.click()
        page.wait_for_load_state("load", timeout=60000)
        time.sleep(0.3)

    def _expandir_archivos(self, page) -> None:
        """Expande la seccion Archivos del plano (#bP7)."""
        btn = page.locator(SEL_BTN_ARCHIVOS_PLA)
        if btn.count() == 0:
            raise AgentError("boton Archivos del plano (#bP7) no encontrado")
        # Solo expandir si no esta abierto
        seccion = page.locator("#C7")
        if "show" not in (seccion.get_attribute("class") or ""):
            btn.click()
            time.sleep(0.5)

    # -----------------------------------------------------------------------
    # Subida de archivos
    # -----------------------------------------------------------------------

    def subir_archivos_plano(
        self,
        expediente_id: str,
        *,
        anverso: Optional[Path] = None,
        entero: Optional[Path] = None,
        derrotero: Optional[Path] = None,
        visado: Optional[Path] = None,
    ) -> dict:
        """Sube uno o varios archivos al portal APT para el expediente dado.

        Parametros
        ----------
        expediente_id:
            ID del expediente en la BD local.
        anverso:
            PDF del plano catastral (B&N, < 600 KB).
        entero:
            PDF del entero fiscal.
        derrotero:
            ZIP con los archivos shapefile (.shp + .dbf + .shx).
        visado:
            PDF del visado municipal (solo en APT R2).

        Requiere confirmacion WhatsApp del tipo "subir_apt".

        Devuelve dict {"tramite": ..., "archivos_subidos": [...], "errores": [...]}.
        """
        self._exigir_confirmacion(expediente_id, "subir_apt")

        exp = self.db.obtener_expediente(expediente_id)
        if not exp:
            raise AgentError(f"expediente {expediente_id!r} no existe en BD")
        tramite = json.loads(exp.get("metadata_json") or "{}").get("apt_tramite")
        if not tramite:
            raise AgentError(
                f"expediente {expediente_id!r} no tiene apt_tramite en metadata. "
                "Agrega 'apt_tramite': '<numero>' en metadata_json."
            )

        archivos_a_subir: list[tuple[str, Path]] = []
        for tipo, path in (
            (TIPO_ANVERSO,   anverso),
            (TIPO_ENTERO,    entero),
            (TIPO_DERROTERO, derrotero),
            (TIPO_VISADO,    visado),
        ):
            if path is not None:
                path = Path(path)
                if not path.exists():
                    raise AgentError(f"archivo no existe: {path}")
                archivos_a_subir.append((tipo, path))

        if not archivos_a_subir:
            raise AgentError("no se especifico ningun archivo para subir")

        subidos: list[str] = []
        errores: list[str] = []

        with self._session() as page:
            self._asegurar_login(page)
            filas = self._buscar_en_consulta(page, tramite)
            if not filas:
                raise AgentError(
                    f"tramite {tramite!r} no encontrado en Consulta APT"
                )
            fila = filas[0]
            self._abrir_tramite(page, fila)
            self._ir_a_tab_planos(page)
            # Si hay mas de un plano en el contrato elegir el del expediente
            detalle = exp.get("nombre_cliente") or fila.get("detalle", "")
            self._seleccionar_plano(page, detalle)
            self._expandir_archivos(page)

            for tipo, path in archivos_a_subir:
                try:
                    self._subir_un_archivo(page, tipo, path)
                    subidos.append(path.name)
                    self._log.info(
                        "archivo subido: %s (tipo %s) tramite %s",
                        path.name, tipo, tramite,
                    )
                except Exception as exc:
                    msg = f"{path.name}: {exc}"
                    errores.append(msg)
                    self._log.error("error subiendo %s: %s", path.name, exc)

        return {"tramite": tramite, "archivos_subidos": subidos, "errores": errores}

    def _subir_un_archivo(self, page, tipo: str, path: Path) -> None:
        """Selecciona el tipo en el dropdown y sube el archivo indicado."""
        # Seleccionar tipo
        page.select_option(SEL_TIPO_ARCHIVO, tipo)
        time.sleep(0.3)
        # Asignar el archivo al input (dropzone)
        page.set_input_files(SEL_FILE_INPUT, str(path))
        # Esperar que el portal confirme la carga (puede haber progreso)
        page.wait_for_load_state("load", timeout=60000)
        time.sleep(1.0)
        # Guardar la seccion de archivos
        self._guardar_seccion_plano(page)

    def _guardar_seccion_plano(self, page) -> None:
        """Hace clic en el boton Guardar de la seccion activa del plano."""
        # Hay varios botones Guardar en la pagina; el de la seccion Archivos
        # es el ultimo visible dentro del acordeon abierto.
        btn = page.locator('#C7 button:has-text("Guardar"), #C7 .btn-primary').last
        if btn.count() > 0:
            btn.click()
            page.wait_for_load_state("load", timeout=60000)
            time.sleep(0.5)
        else:
            # Fallback: boton global de guardar del plano
            self._log.warning("boton Guardar en C7 no encontrado, usando BtnGuardar")

    # -----------------------------------------------------------------------
    # Presentacion
    # -----------------------------------------------------------------------

    def presentar_r1(
        self,
        expediente_id: str,
        archivo_anverso: Path,
        archivo_entero: Path,
        archivo_derrotero: Optional[Path] = None,
    ) -> dict:
        """Prepara el portal para presentacion R1: sube los archivos y notifica
        al operador que debe firmar con Firma Digital.

        NO hace clic en #BtnEnviar -- eso requiere Firma Digital fisica.
        Devuelve {"tramite": ..., "archivos_subidos": [...], "listo_para_fd": True/False}.
        """
        self._exigir_confirmacion(expediente_id, "subir_apt")
        resultado = self.subir_archivos_plano(
            expediente_id,
            anverso=archivo_anverso,
            entero=archivo_entero,
            derrotero=archivo_derrotero,
        )
        resultado["listo_para_fd"] = len(resultado["errores"]) == 0
        return resultado

    def presentar_r2(
        self,
        expediente_id: str,
        archivo_anverso: Path,
        archivo_entero: Optional[Path] = None,
        archivo_visado: Optional[Path] = None,
        archivo_derrotero: Optional[Path] = None,
    ) -> dict:
        """Prepara el portal para presentacion R2 (con visado si aplica).

        En R2 el entero original ya está adjunto desde R1; solo si la APT
        pidió uno nuevo (raro) se vuelve a subir. El visado municipal SÍ
        es el archivo crítico de R2.

        NO hace clic en #BtnEnviar -- eso requiere Firma Digital fisica.
        """
        self._exigir_confirmacion(expediente_id, "subir_apt_r2")
        resultado = self.subir_archivos_plano(
            expediente_id,
            anverso=archivo_anverso,
            entero=archivo_entero,
            visado=archivo_visado,
            derrotero=archivo_derrotero,
        )
        resultado["listo_para_fd"] = len(resultado["errores"]) == 0
        return resultado

    # -----------------------------------------------------------------------
    # Descarga de archivos de respuesta
    # -----------------------------------------------------------------------

    def descargar_archivos_r1(
        self, expediente_id: str, dest_dir: Path
    ) -> list[Path]:
        """Descarga minuta + correcciones cuando el estado es 'Publico y Defectuoso'.

        Devuelve lista de Paths descargados en dest_dir.
        Operacion de solo-lectura — no requiere confirmacion.
        """
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)

        exp = self.db.obtener_expediente(expediente_id)
        if not exp:
            raise AgentError(f"expediente {expediente_id!r} no existe en BD")
        tramite = json.loads(exp.get("metadata_json") or "{}").get("apt_tramite")
        if not tramite:
            raise AgentError(
                f"expediente {expediente_id!r} no tiene apt_tramite en metadata"
            )

        descargados: list[Path] = []
        with self._session() as page:
            self._asegurar_login(page)
            filas = self._buscar_en_consulta(page, tramite)
            defectuosas = [f for f in filas if f["estado"] == ESTADO_DEFECTUOSO]
            if not defectuosas:
                self._log.info(
                    "tramite %s no tiene planos en estado Defectuoso", tramite
                )
                return descargados

            # Por cada plano defectuoso intentar descargar sus documentos
            for fila in defectuosas:
                try:
                    paths = self._descargar_documentos_fila(
                        page, fila, dest_dir
                    )
                    descargados.extend(paths)
                except Exception as exc:
                    self._log.error(
                        "error descargando docs de fila %s: %s",
                        fila.get("detalle"), exc,
                    )

        return descargados

    def _descargar_documentos_fila(
        self, page, fila: dict, dest_dir: Path
    ) -> list[Path]:
        """Abre el tramite y descarga los links de documentos disponibles."""
        self._abrir_tramite(page, fila)
        # Buscar links de descarga (PDF links en la pagina del tramite)
        links_descarga = page.query_selector_all(
            'a[href*=".pdf"], a[href*="descargar"], a[download]'
        )
        descargados: list[Path] = []
        for link in links_descarga:
            href = link.get_attribute("href") or ""
            nombre = link.inner_text().strip() or Path(href).name or "descarga"
            nombre = nombre.replace("/", "_").replace("\\", "_")[:80]
            if not nombre.endswith(".pdf"):
                nombre += ".pdf"
            dest_path = dest_dir / nombre
            try:
                with page.expect_download() as dl_info:
                    link.click()
                dl = dl_info.value
                dl.save_as(str(dest_path))
                descargados.append(dest_path)
                self._log.info("descargado: %s", dest_path.name)
            except Exception as exc:
                self._log.warning("no se pudo descargar '%s': %s", nombre, exc)
        return descargados

    def descargar_inscrito(
        self, expediente_id: str, dest_dir: Path
    ) -> list[Path]:
        """Descarga el plano inscrito cuando el estado es 'Publico e Inscrito'."""
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)

        exp = self.db.obtener_expediente(expediente_id)
        if not exp:
            raise AgentError(f"expediente {expediente_id!r} no existe en BD")
        tramite = json.loads(exp.get("metadata_json") or "{}").get("apt_tramite")
        if not tramite:
            raise AgentError(
                f"expediente {expediente_id!r} no tiene apt_tramite en metadata"
            )

        descargados: list[Path] = []
        with self._session() as page:
            self._asegurar_login(page)
            filas = self._buscar_en_consulta(page, tramite)
            inscritos = [f for f in filas if f["estado"] == ESTADO_INSCRITO]
            if not inscritos:
                self._log.info(
                    "tramite %s no tiene planos inscritos todavia", tramite
                )
                return descargados

            for fila in inscritos:
                try:
                    paths = self._descargar_documentos_fila(
                        page, fila, dest_dir
                    )
                    descargados.extend(paths)
                except Exception as exc:
                    self._log.error(
                        "error descargando inscrito de fila %s: %s",
                        fila.get("detalle"), exc,
                    )

        return descargados

    # -----------------------------------------------------------------------
    # Consulta de estado por ronda
    # -----------------------------------------------------------------------

    def consultar_estado_r1(self, expediente_id: str) -> Optional[str]:
        """Devuelve el estado normalizado para APT R1.

        Retorna:
            "respondido"  -- APT emitio minuta o correcciones (Defectuoso)
            "inscrito"    -- plano inscrito directamente (Publico e Inscrito)
            "pendiente"   -- sigue en revision (Calificacion RN)
            "edicion"     -- aun en edicion (En Edicion)
            None          -- tramite no encontrado
        """
        estado_raw = self.consultar_estado(expediente_id)
        return self._normalizar_estado(estado_raw)

    def consultar_estado_r2(self, expediente_id: str) -> Optional[str]:
        """Como consultar_estado_r1 pero para la segunda ronda."""
        return self.consultar_estado_r1(expediente_id)

    @staticmethod
    def _normalizar_estado(estado_raw: Optional[str]) -> Optional[str]:
        """Convierte estado del portal a valor canonico interno."""
        if estado_raw is None:
            return None
        if estado_raw == ESTADO_DEFECTUOSO:
            return "respondido"
        if estado_raw == ESTADO_INSCRITO:
            return "inscrito"
        if estado_raw == ESTADO_CALIFICACION:
            return "pendiente"
        if estado_raw == ESTADO_EN_EDICION:
            return "edicion"
        return estado_raw.lower().replace(" ", "_")

    # -----------------------------------------------------------------------
    # BaseAgent
    # -----------------------------------------------------------------------

    def run(self, expediente_id: str) -> None:
        """Por compatibilidad con BaseAgent — delega a consultar_estado."""
        self.consultar_estado(expediente_id)
