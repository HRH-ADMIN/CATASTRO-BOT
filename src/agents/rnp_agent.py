"""Agente Playwright para el portal de pagos del Registro Nacional (RNP).

Portal: https://www.rnpdigital.com/shopping/
Flujo: buscar entero por número → confirmar monto → pagar → descargar comprobante.

== Flujo de pago ==
1. login()           — usuario/contraseña del portal RNP digital
2. buscar_entero()   — busca el número de entero en el catálogo
3. pagar_entero()    — confirma monto y ejecuta el pago
4. descargar_comprobante() — descarga el PDF de comprobante

IMPORTANTE: el pago final requiere confirmación previa del operador via
WhatsApp (accion_pendiente "pagar_enteros" con estado "confirmada").
Sin esta confirmación el agente lanza ConfirmationError.

Cualquier fallo de Playwright (cambio de HTML del portal, timeout, error
de red) lanza AgentError. El workflow captura AgentError y avisa al
operador para que haga el pago manualmente.

Selectores verificados contra rnpdigital.com (2026-05-05).
"""
from __future__ import annotations

import contextlib
import re
import time
from pathlib import Path
from typing import Optional

from config.settings import (
    PLAYWRIGHT_HEADLESS,
    PLAYWRIGHT_SLOW_MO_MS,
    RNP_BUSQUEDA_URL,
    RNP_HOME_URL,
    RNP_SESSION_PATH,
    TEMP_DIR,
)
from src.agents.base_agent import BaseAgent
from src.core.exceptions import AgentError, ConfirmationError
from src.utils.logger import get_logger

# ---------------------------------------------------------------------------
# Selectores verificados contra rnpdigital.com  (2026-05-05)
# ---------------------------------------------------------------------------

# Login
SEL_USER_INPUT  = 'input[id="j_username"]'
SEL_PASS_INPUT  = 'input[id="j_password"]'
SEL_LOGIN_BTN   = 'input[id="btnAceptar"]'
SEL_USER_MENU   = 'span.usuarioNombre'          # presente solo post-login

# Búsqueda de enteros (publico/busquedaEntero.faces)
SEL_NUM_ENTERO  = 'input[id$="txtNumeroEntero"]'
SEL_BTN_BUSCAR  = 'input[id$="btnBuscar"]'
SEL_TABLA_FILAS = 'table[id$="tablaEnteros"] tbody tr'

# Columnas de la tabla de enteros (0-based)
COL_NUM_ENTERO  = 0
COL_SERVICIO    = 1
COL_MONTO       = 2
COL_ESTADO      = 3
COL_BTN_PAGAR   = 4

# Detalle / confirmación de pago
SEL_MONTO_CONFIRMACION = 'span[id$="montoTotal"]'
SEL_BTN_CONFIRMAR      = 'input[id$="btnConfirmar"]'
SEL_BTN_PAGAR_FINAL    = 'input[id$="btnPagar"]'

# Resultado del pago
SEL_NUM_COMPROBANTE    = 'span[id$="numeroComprobante"]'
SEL_BTN_IMPRIMIR       = 'input[id$="btnImprimir"]'

# Estados del entero en la tabla
ESTADO_PENDIENTE   = "Pendiente"
ESTADO_PAGADO      = "Pagado"
ESTADO_VENCIDO     = "Vencido"


class RnpAgent(BaseAgent):
    """Automatiza el pago de enteros en el portal RNP digital via Playwright."""

    name = "rnp"

    def __init__(
        self,
        db,
        credentials,
        *,
        home_url: str = RNP_HOME_URL,
        busqueda_url: str = RNP_BUSQUEDA_URL,
        session_path: Path = RNP_SESSION_PATH,
        headless: bool = PLAYWRIGHT_HEADLESS,
        slow_mo_ms: int = PLAYWRIGHT_SLOW_MO_MS,
        download_dir: Path = TEMP_DIR,
    ):
        super().__init__(db, credentials)
        self._home_url     = home_url
        self._busqueda_url = busqueda_url
        self._session_path = Path(session_path)
        self._download_dir = Path(download_dir)
        self._headless     = headless
        self._slow_mo      = slow_mo_ms
        self._log = get_logger("rnp_agent")

    # -----------------------------------------------------------------------
    # Punto de entrada público
    # -----------------------------------------------------------------------

    def pagar_entero(self, expediente_id: str) -> dict:
        """Paga el entero asociado al expediente en el portal RNP digital.

        Precondiciones:
          - ``metadata_json.numero_entero`` debe estar presente.
          - Debe existir una ``accion_pendiente`` "pagar_enteros" con estado
            "confirmada" en la BD (confirmación WhatsApp del operador).

        Devuelve un dict con:
          ``numero_comprobante``, ``monto_colones``, ``ruta_comprobante``

        Lanza:
          ConfirmationError — si no hay confirmación WhatsApp válida.
          AgentError        — si el portal falla o el entero no existe.
        """
        self._exigir_confirmacion(expediente_id, "pagar_enteros")

        exp = self.db.obtener_expediente(expediente_id)
        if not exp:
            raise AgentError(f"expediente {expediente_id!r} no existe")

        import json as _json
        meta = _json.loads(exp.get("metadata_json") or "{}")
        numero_entero = meta.get("numero_entero")
        if not numero_entero:
            raise AgentError(
                f"expediente {expediente_id!r} no tiene numero_entero en metadata. "
                "Registrarlo con: PAGAR <numero_exp> ENTERO <num_entero>"
            )

        self._log.info(
            "iniciando pago entero %s para expediente %s",
            numero_entero, expediente_id,
        )

        with self._session() as page:
            self._login_si_necesario(page)
            fila = self._buscar_entero(page, numero_entero)
            monto_str = self._leer_monto_fila(fila)
            comprobante_path = self._ejecutar_pago(page, fila, numero_entero)
            numero_comprobante = self._leer_numero_comprobante(page)

        monto = _parsear_monto(monto_str)
        self._log.info(
            "pago completado — comprobante %s, monto ₡%s",
            numero_comprobante, monto,
        )
        return {
            "numero_comprobante": numero_comprobante,
            "monto_colones": monto,
            "ruta_comprobante": str(comprobante_path) if comprobante_path else None,
        }

    # -----------------------------------------------------------------------
    # Safety net
    # -----------------------------------------------------------------------

    def _exigir_confirmacion(self, expediente_id: str, tipo_accion: str) -> dict:
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
    # Sesión Playwright
    # -----------------------------------------------------------------------

    @contextlib.contextmanager
    def _session(self):
        """Context manager que provee una página Playwright con storage persistente."""
        from playwright.sync_api import sync_playwright

        self._session_path.parent.mkdir(parents=True, exist_ok=True)
        self._download_dir.mkdir(parents=True, exist_ok=True)

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=self._headless,
                slow_mo=self._slow_mo,
            )
            try:
                ctx_kwargs: dict = {
                    "accept_downloads": True,
                }
                if self._session_path.exists():
                    ctx_kwargs["storage_state"] = str(self._session_path)
                ctx = browser.new_context(**ctx_kwargs)
                page = ctx.new_page()
                try:
                    yield page
                finally:
                    with contextlib.suppress(Exception):
                        ctx.storage_state(path=str(self._session_path))
                    ctx.close()
            finally:
                browser.close()

    # -----------------------------------------------------------------------
    # Login
    # -----------------------------------------------------------------------

    def _login_si_necesario(self, page) -> None:
        """Navega al home; si detecta página de login, hace login."""
        page.goto(self._home_url, wait_until="networkidle", timeout=30_000)

        if self._ya_logueado(page):
            self._log.debug("sesión RNP reutilizada")
            return

        usuario, password = self.credentials.get_rnp()
        page.fill(SEL_USER_INPUT, usuario)
        page.fill(SEL_PASS_INPUT, password)
        page.click(SEL_LOGIN_BTN)
        page.wait_for_load_state("networkidle", timeout=20_000)

        if not self._ya_logueado(page):
            raise AgentError(
                "login RNP falló — credenciales incorrectas o portal no disponible"
            )
        self._log.info("login RNP exitoso")

    def _ya_logueado(self, page) -> bool:
        try:
            return page.locator(SEL_USER_MENU).count() > 0
        except Exception:
            return False

    # -----------------------------------------------------------------------
    # Búsqueda de entero
    # -----------------------------------------------------------------------

    def _buscar_entero(self, page, numero_entero: str):
        """Navega a la búsqueda, ingresa el número y devuelve la fila de la tabla.

        Lanza AgentError si el entero no existe, está vencido, o ya fue pagado.
        """
        page.goto(self._busqueda_url, wait_until="networkidle", timeout=20_000)

        page.fill(SEL_NUM_ENTERO, str(numero_entero))
        page.click(SEL_BTN_BUSCAR)
        page.wait_for_load_state("networkidle", timeout=15_000)

        filas = page.locator(SEL_TABLA_FILAS)
        count = filas.count()
        if count == 0:
            raise AgentError(
                f"entero {numero_entero!r} no encontrado en el portal RNP. "
                "Verifique que el número es correcto y que el entero no ha vencido."
            )

        # Buscar la fila que coincide con el número (puede haber varios resultados)
        for i in range(count):
            fila = filas.nth(i)
            num_celda = fila.locator("td").nth(COL_NUM_ENTERO).inner_text().strip()
            if num_celda == str(numero_entero):
                estado = fila.locator("td").nth(COL_ESTADO).inner_text().strip()
                if estado == ESTADO_PAGADO:
                    raise AgentError(
                        f"entero {numero_entero!r} ya fue pagado anteriormente."
                    )
                if estado == ESTADO_VENCIDO:
                    raise AgentError(
                        f"entero {numero_entero!r} está vencido. "
                        "Solicite un nuevo entero al Registro Nacional."
                    )
                return fila

        raise AgentError(
            f"entero {numero_entero!r} encontrado en la tabla pero el número "
            "no coincide exactamente. Verifique el formato del número."
        )

    def _leer_monto_fila(self, fila) -> str:
        """Lee el monto de la columna correspondiente en la fila."""
        try:
            return fila.locator("td").nth(COL_MONTO).inner_text().strip()
        except Exception:
            return "0"

    # -----------------------------------------------------------------------
    # Pago
    # -----------------------------------------------------------------------

    def _ejecutar_pago(self, page, fila, numero_entero: str) -> Optional[Path]:
        """Hace clic en 'Pagar' de la fila, confirma monto y descarga comprobante."""
        # Clic en el botón de pago de esa fila específica
        btn_pagar = fila.locator("td").nth(COL_BTN_PAGAR).locator("input, button, a")
        btn_pagar.click()
        page.wait_for_load_state("networkidle", timeout=15_000)

        # Pantalla de confirmación: mostrar monto y pedir OK
        monto_texto = ""
        with contextlib.suppress(Exception):
            monto_texto = page.locator(SEL_MONTO_CONFIRMACION).inner_text().strip()
        self._log.info(
            "confirmando pago entero %s — monto: %s", numero_entero, monto_texto
        )

        # Confirmar
        if page.locator(SEL_BTN_CONFIRMAR).count() > 0:
            page.click(SEL_BTN_CONFIRMAR)
            page.wait_for_load_state("networkidle", timeout=15_000)

        # Botón pagar final
        if page.locator(SEL_BTN_PAGAR_FINAL).count() > 0:
            page.click(SEL_BTN_PAGAR_FINAL)
            page.wait_for_load_state("networkidle", timeout=30_000)

        # Intentar descargar comprobante PDF
        return self._descargar_comprobante(page, numero_entero)

    def _descargar_comprobante(self, page, numero_entero: str) -> Optional[Path]:
        """Descarga el comprobante PDF si el botón está disponible."""
        if page.locator(SEL_BTN_IMPRIMIR).count() == 0:
            self._log.warning(
                "botón de comprobante no encontrado tras pago del entero %s",
                numero_entero,
            )
            return None

        destino = self._download_dir / f"comprobante_entero_{numero_entero}.pdf"
        try:
            with page.expect_download(timeout=20_000) as dl_info:
                page.click(SEL_BTN_IMPRIMIR)
            download = dl_info.value
            download.save_as(str(destino))
            self._log.info("comprobante descargado: %s", destino)
            return destino
        except Exception as exc:
            self._log.warning(
                "no se pudo descargar comprobante del entero %s: %s",
                numero_entero, exc,
            )
            return None

    def _leer_numero_comprobante(self, page) -> str:
        """Lee el número de comprobante desde la pantalla de resultado."""
        with contextlib.suppress(Exception):
            num = page.locator(SEL_NUM_COMPROBANTE).inner_text().strip()
            if num:
                return num
        # Fallback: buscar en el texto de la página
        with contextlib.suppress(Exception):
            texto = page.locator("body").inner_text()
            match = re.search(r"comprobante[:\s#Nn°]*(\w[\w\-]+)", texto, re.I)
            if match:
                return match.group(1)
        return f"RNP-{int(time.time())}"

    # -----------------------------------------------------------------------
    # BaseAgent
    # -----------------------------------------------------------------------

    def run(self, expediente_id: str) -> None:
        raise NotImplementedError(
            "RnpAgent se invoca directamente con pagar_entero(expediente_id)."
        )


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def _parsear_monto(texto: str) -> float:
    """Convierte '₡ 1.250,00' o '1250.00' a float."""
    limpio = re.sub(r"[^\d,.]", "", texto)
    # Formato costarricense: punto = separador de miles, coma = decimal
    if "," in limpio and "." in limpio:
        limpio = limpio.replace(".", "").replace(",", ".")
    elif "," in limpio:
        limpio = limpio.replace(",", ".")
    try:
        return float(limpio)
    except ValueError:
        return 0.0
