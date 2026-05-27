"""Helpers para reutilizar pestañas Playwright + manejar cierres manuales.

Resuelve dos bugs operativos del bot reportados 2026-05-22:

  1. **Spam de pestañas about:blank** — cada llamada a `ctx.new_page()`
     abre pestaña nueva. Si el operador encadena `apt-sync` + `apt-enviar`
     + `apt-flujo`, terminan ~5 pestañas APT distintas. Watchdog o
     retry-loops pueden incluso provocar spam infinito.

  2. **Bucle de errores al cerrar manualmente** — si el operador cierra
     la pestaña desde la UI, las siguientes operaciones lanzan
     `TargetClosedError` y se replican por todos los retries del bot.

API pública:

  is_page_alive(page) -> bool
      Devuelve False si la pestaña fue cerrada manualmente, sin lanzar.

  get_or_create_apt_page(browser, url_patterns=None) -> Page
      Busca pestaña existente que matchee alguno de los patrones.
      Si ninguna está viva, abre una nueva. SIN side effects sobre el
      browser si encuentra candidata.

  cleanup_blank_tabs(browser, keep_url_pattern=None, max_blank=1) -> int
      Cierra pestañas `about:blank` huérfanas (deja máximo `max_blank`).
      Devuelve cantidad cerrada.

  safe_page_op(page, fn, *, default=None, log_prefix=None)
      Ejecuta fn(page); captura TargetClosedError + Error genérico;
      devuelve `default` con log limpio en lugar de propagar.

  PageClosedByUser
      Excepción específica que el caller puede capturar para detenerse
      limpiamente (vs crash en retry-loop).

Plan: HOTFIX/browser-session-leak (Sprint 4 / operativo crítico).
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Iterable, Optional

log = logging.getLogger("catastro.browser_session")


# Patrones default para detectar la pestaña del portal CFIA. Match laxo
# para tolerar redirects al SSO durante el login.
DEFAULT_APT_PATTERNS = (
    "apt.cfia.or.cr",
    "sso.cfia.or.cr",
)


class PageClosedByUser(Exception):
    """Lanzada cuando se detecta que la pestaña fue cerrada manualmente.

    Diseñada para que el caller la capture y aborte limpiamente en vez de
    entrar en bucle de TargetClosedError.
    """


def is_page_alive(page: Any) -> bool:
    """¿La pestaña sigue abierta y operable?

    Trata cualquier excepción al consultar como "no viva" — Playwright
    levanta distintas en versiones distintas (TargetClosedError, Error
    "page.url: ...").
    """
    if page is None:
        return False
    try:
        # is_closed() es el chequeo barato y oficial de Playwright.
        if hasattr(page, "is_closed"):
            return not page.is_closed()
        # Fallback: consultar url. Si lanza, está muerta.
        _ = page.url
        return True
    except Exception:
        return False


def _url_safe(page: Any) -> str:
    """Devuelve page.url o '' si la página está cerrada — sin lanzar."""
    try:
        return page.url or ""
    except Exception:
        return ""


def get_or_create_apt_page(
    browser: Any,
    *,
    url_patterns: Optional[Iterable[str]] = None,
    fallback_create: bool = True,
) -> Any:
    """Reusa una pestaña existente del portal CFIA o crea UNA nueva.

    Recorre TODOS los contextos del browser CDP buscando una pestaña que:
      1. Esté viva (is_page_alive == True).
      2. Su URL matchee alguno de los `url_patterns`.

    Si encuentra varias, devuelve la primera (orden interno de Playwright).
    Si no encuentra ninguna y `fallback_create=True`, abre UNA nueva en
    el primer contexto.

    Args:
        browser: Browser de Playwright (post connect_over_cdp).
        url_patterns: lista de substrings a buscar en page.url.
            Default: DEFAULT_APT_PATTERNS (apt.cfia + sso.cfia).
        fallback_create: si False, devuelve None en vez de crear nueva.

    Returns:
        Page. Lanza si no hay contextos y fallback_create=True.
    """
    patterns = list(url_patterns) if url_patterns else list(DEFAULT_APT_PATTERNS)

    # Duck typing: detectar por el tipo de .pages (BrowserContext) vs
    # .contexts (Browser). Usamos `isinstance(..., list)` para evitar
    # que MagicMock crudo pase como BrowserContext o Browser igual de bien.
    pages_attr = getattr(browser, "pages", None)
    contexts_attr = getattr(browser, "contexts", None)
    if isinstance(pages_attr, list):
        # Es un BrowserContext directo (tiene .pages como lista de pages)
        contextos = [browser]
    elif isinstance(contexts_attr, list):
        contextos = list(contexts_attr)
    else:
        # MagicMock o objeto raro — intentar iterar contexts si existe
        try:
            contextos = list(contexts_attr) if contexts_attr is not None else []
        except (TypeError, ValueError):
            contextos = []

    if not contextos:
        if fallback_create:
            raise RuntimeError(
                "browser sin contextos — Chrome no tiene ninguna ventana abierta"
            )
        return None

    # Buscar candidata viva en cualquier contexto
    for ctx in contextos:
        for pg in list(getattr(ctx, "pages", []) or []):
            if not is_page_alive(pg):
                continue
            url = _url_safe(pg)
            if any(p in url for p in patterns):
                log.info("browser_session: reusando pestaña existente (url=%s)", url)
                return pg

    if not fallback_create:
        return None

    # No hay candidata viva → crear UNA nueva en el primer contexto
    target_ctx = contextos[0]
    log.info("browser_session: no se encontró pestaña APT viva — abriendo nueva")
    return target_ctx.new_page()


def cleanup_blank_tabs(
    browser: Any,
    *,
    keep_url_pattern: Optional[str] = None,
    max_blank: int = 1,
) -> int:
    """Cierra pestañas `about:blank` y duplicadas vacías huérfanas.

    Útil para limpiar después de un proceso del bot que dejó tabs en blanco.

    Mantiene como máximo `max_blank` pestañas about:blank por contexto
    (default 1, por si Chrome necesita una activa).

    Args:
        browser: Browser CDP.
        keep_url_pattern: si la pestaña matchea esto, NUNCA se cierra
            aunque sea about:blank.
        max_blank: máximo de about:blank que se permite por contexto.

    Returns:
        Cantidad total de pestañas cerradas.
    """
    cerradas = 0
    # Idem detección: BrowserContext si .pages es list, sino Browser.
    pages_attr = getattr(browser, "pages", None)
    contexts_attr = getattr(browser, "contexts", None)
    if isinstance(pages_attr, list):
        contextos = [browser]
    elif isinstance(contexts_attr, list):
        contextos = list(contexts_attr)
    else:
        return 0
    for ctx in contextos:
        blanks = []
        for pg in list(getattr(ctx, "pages", []) or []):
            if not is_page_alive(pg):
                continue
            url = _url_safe(pg)
            es_blank = (url in ("about:blank", "chrome://newtab/", "")
                        or url.startswith("chrome://"))
            if not es_blank:
                continue
            if keep_url_pattern and keep_url_pattern in url:
                continue
            blanks.append(pg)

        # Dejar max_blank vivas, cerrar el resto
        for pg in blanks[max_blank:]:
            try:
                pg.close()
                cerradas += 1
                log.info("browser_session: cerrada pestaña en blanco")
            except Exception as exc:
                log.debug("no se pudo cerrar blank: %s", exc)
    if cerradas:
        log.info("browser_session: cleanup_blank_tabs cerró %d pestaña(s)", cerradas)
    return cerradas


def safe_page_op(
    page: Any,
    fn: Callable[[Any], Any],
    *,
    default: Any = None,
    log_prefix: str = "page_op",
) -> Any:
    """Ejecuta `fn(page)` con manejo defensivo de page-closed.

    Si la pestaña está cerrada (antes o durante la ejecución), retorna
    `default` y loggea — NO propaga. Otras excepciones se loggean al
    nivel WARNING (no ERROR para no spamear) y también retornan default.

    Diseñado para callers que están en loops largos (scheduler poll,
    watchdog) y no deben fallar catastróficamente si el operador cierra
    una pestaña manualmente.

    Uso:
        resultado = safe_page_op(page, lambda p: p.locator("#X").count(),
                                 default=0, log_prefix="apt-sync")
    """
    if not is_page_alive(page):
        log.info("%s: skip — pestaña ya cerrada", log_prefix)
        return default
    try:
        return fn(page)
    except Exception as exc:
        # Detectar nombres de excepción de Playwright relacionados a
        # cierre. La clase exacta varía por versión:
        #   TargetClosedError, Error con msg "Target page, context or browser
        #   has been closed", Page.is_closed().
        nombre = type(exc).__name__
        msg = str(exc)
        es_cerrada = (
            "Target" in msg and "closed" in msg
            or "TargetClosedError" in nombre
            or "PageClosedError" in nombre
            or not is_page_alive(page)
        )
        if es_cerrada:
            log.info("%s: pestaña cerrada durante operación — skip", log_prefix)
        else:
            log.warning("%s: error inesperado (%s): %s",
                        log_prefix, nombre, msg[:200])
        return default


def assert_page_alive(page: Any) -> None:
    """Como `is_page_alive` pero lanza `PageClosedByUser` si está muerta.

    Útil al principio de funciones largas: si el operador cerró la
    pestaña, abortar inmediatamente con excepción específica que el
    caller puede capturar en lugar de un TargetClosedError genérico
    a mitad de operación.
    """
    if not is_page_alive(page):
        raise PageClosedByUser(
            "La pestaña fue cerrada manualmente. "
            "Cerrá el script o invocá APT SESION para abrir una nueva."
        )


__all__ = [
    "DEFAULT_APT_PATTERNS",
    "PageClosedByUser",
    "is_page_alive",
    "get_or_create_apt_page",
    "cleanup_blank_tabs",
    "safe_page_op",
    "assert_page_alive",
]
