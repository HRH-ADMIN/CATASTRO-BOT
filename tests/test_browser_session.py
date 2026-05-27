"""Tests del helper browser_session (HOTFIX 2026-05-22).

Sin Playwright real — mockea Browser/Context/Page con duck typing.
Verifica:
  - is_page_alive: detecta page.is_closed() y excepciones.
  - get_or_create_apt_page: reusa pestaña viva con URL APT/SSO,
    abre nueva solo si no hay. Acepta Browser y BrowserContext.
  - cleanup_blank_tabs: cierra about:blank huérfanas, respeta max_blank,
    no toca pestañas con URL relevante.
  - safe_page_op: ejecuta fn, devuelve default si pestaña cerrada,
    detecta TargetClosedError-like.
  - assert_page_alive: lanza PageClosedByUser si está muerta.
"""
from __future__ import annotations
from unittest.mock import MagicMock

import pytest

from src.utils.browser_session import (
    DEFAULT_APT_PATTERNS,
    PageClosedByUser,
    assert_page_alive,
    cleanup_blank_tabs,
    get_or_create_apt_page,
    is_page_alive,
    safe_page_op,
)


def _mk_page(url: str, *, closed: bool = False):
    """Page mock con duck typing de Playwright."""
    p = MagicMock()
    p.is_closed.return_value = closed
    # PropertyMock-style: page.url retorna el string
    type(p).url = property(lambda self: url)  # type: ignore
    return p


def _mk_ctx(pages: list):
    ctx = MagicMock()
    ctx.pages = pages
    ctx.new_page.return_value = _mk_page("about:blank")
    return ctx


def _mk_browser(contexts: list):
    b = MagicMock()
    b.contexts = contexts
    return b


# ─── is_page_alive ────────────────────────────────────────────────────

class TestIsPageAlive:
    def test_page_abierta_es_viva(self):
        assert is_page_alive(_mk_page("https://apt.cfia.or.cr/")) is True

    def test_page_cerrada_no_es_viva(self):
        assert is_page_alive(_mk_page("https://apt.cfia.or.cr/", closed=True)) is False

    def test_None_no_es_viva(self):
        assert is_page_alive(None) is False

    def test_excepcion_en_is_closed_no_es_viva(self):
        p = MagicMock()
        p.is_closed.side_effect = Exception("TargetClosed")
        type(p).url = property(lambda self: (_ for _ in ()).throw(Exception("dead")))  # type: ignore
        assert is_page_alive(p) is False


# ─── get_or_create_apt_page ───────────────────────────────────────────

class TestGetOrCreateAptPage:
    def test_reusa_pestaña_apt_existente(self):
        apt_page = _mk_page("https://apt.cfia.or.cr/APT2/Home")
        other = _mk_page("https://www.google.com")
        ctx = _mk_ctx([other, apt_page])
        browser = _mk_browser([ctx])

        result = get_or_create_apt_page(browser)
        assert result is apt_page
        # No debe haber llamado new_page (reutilización)
        ctx.new_page.assert_not_called()

    def test_reusa_pestaña_sso_durante_login(self):
        """El bug REAL: durante login la pestaña está en sso.cfia.or.cr,
        no en apt.cfia.or.cr. Antes el código no la detectaba."""
        sso_page = _mk_page("https://sso.cfia.or.cr/sso/?IdSystem=1")
        ctx = _mk_ctx([sso_page])
        browser = _mk_browser([ctx])

        result = get_or_create_apt_page(browser)
        assert result is sso_page
        ctx.new_page.assert_not_called()

    def test_ignora_pestañas_cerradas(self):
        dead = _mk_page("https://apt.cfia.or.cr/", closed=True)
        alive = _mk_page("https://apt.cfia.or.cr/APT2/")
        ctx = _mk_ctx([dead, alive])
        browser = _mk_browser([ctx])

        result = get_or_create_apt_page(browser)
        assert result is alive  # no la cerrada

    def test_abre_nueva_si_no_hay_candidata(self):
        ctx = _mk_ctx([_mk_page("https://www.google.com")])
        browser = _mk_browser([ctx])

        result = get_or_create_apt_page(browser)
        ctx.new_page.assert_called_once()

    def test_acepta_context_directo(self):
        """Duck typing: get_or_create_apt_page debe aceptar BrowserContext."""
        apt_page = _mk_page("https://apt.cfia.or.cr/")
        ctx = _mk_ctx([apt_page])
        # Nota: ctx NO tiene .contexts, solo .pages — debe funcionar igual
        del ctx.contexts  # asegurar que no se confunde

        result = get_or_create_apt_page(ctx)
        assert result is apt_page

    def test_browser_sin_contextos_lanza(self):
        b = _mk_browser([])
        with pytest.raises(RuntimeError):
            get_or_create_apt_page(b)

    def test_fallback_create_false_devuelve_None(self):
        ctx = _mk_ctx([_mk_page("https://www.google.com")])
        result = get_or_create_apt_page(_mk_browser([ctx]), fallback_create=False)
        assert result is None

    def test_url_patterns_custom(self):
        custom = _mk_page("https://muni.sanramon.go.cr/forms")
        ctx = _mk_ctx([custom])
        result = get_or_create_apt_page(
            _mk_browser([ctx]),
            url_patterns=("sanramon.go.cr",),
        )
        assert result is custom


# ─── cleanup_blank_tabs ──────────────────────────────────────────────

class TestCleanupBlankTabs:
    def test_cierra_blanks_y_respeta_apt(self):
        blank1 = _mk_page("about:blank")
        blank2 = _mk_page("about:blank")
        blank3 = _mk_page("chrome://newtab/")
        apt = _mk_page("https://apt.cfia.or.cr/")
        ctx = _mk_ctx([blank1, blank2, blank3, apt])

        n = cleanup_blank_tabs(_mk_browser([ctx]), max_blank=1)
        assert n == 2  # 3 blanks - max 1 = 2 cerrados
        # APT no se toca
        apt.close.assert_not_called()
        # Al menos 1 blank quedó vivo
        cerrados = blank1.close.call_count + blank2.close.call_count + blank3.close.call_count
        assert cerrados == 2

    def test_max_blank_cero_cierra_todos(self):
        b1 = _mk_page("about:blank")
        b2 = _mk_page("about:blank")
        ctx = _mk_ctx([b1, b2])
        n = cleanup_blank_tabs(_mk_browser([ctx]), max_blank=0)
        assert n == 2

    def test_keep_url_pattern_protege(self):
        especial = _mk_page("about:blank?important=1")
        otra = _mk_page("about:blank")
        ctx = _mk_ctx([especial, otra])
        n = cleanup_blank_tabs(
            _mk_browser([ctx]),
            keep_url_pattern="important",
            max_blank=0,
        )
        # especial NO se cierra (matchea keep_url_pattern), otra sí
        especial.close.assert_not_called()
        assert n == 1


# ─── safe_page_op ─────────────────────────────────────────────────────

class TestSafePageOp:
    def test_ejecuta_y_devuelve_resultado(self):
        page = _mk_page("https://apt.cfia.or.cr/")
        result = safe_page_op(page, lambda p: 42)
        assert result == 42

    def test_pagina_cerrada_devuelve_default(self):
        page = _mk_page("https://apt.cfia.or.cr/", closed=True)
        result = safe_page_op(page, lambda p: 42, default="X")
        assert result == "X"

    def test_target_closed_error_devuelve_default(self):
        page = _mk_page("https://apt.cfia.or.cr/")

        def boom(p):
            raise Exception("Target page, context or browser has been closed")

        result = safe_page_op(page, boom, default=None)
        assert result is None  # no propaga

    def test_otra_excepcion_se_traga_y_devuelve_default(self):
        page = _mk_page("https://apt.cfia.or.cr/")

        def boom(p):
            raise ValueError("algun error random")

        result = safe_page_op(page, boom, default="fallback")
        assert result == "fallback"


# ─── assert_page_alive ────────────────────────────────────────────────

class TestAssertPageAlive:
    def test_no_lanza_si_viva(self):
        assert_page_alive(_mk_page("https://apt.cfia.or.cr/"))

    def test_lanza_PageClosedByUser_si_cerrada(self):
        with pytest.raises(PageClosedByUser) as exc_info:
            assert_page_alive(_mk_page("https://apt.cfia.or.cr/", closed=True))
        assert "cerrada manualmente" in str(exc_info.value)

    def test_lanza_PageClosedByUser_si_None(self):
        with pytest.raises(PageClosedByUser):
            assert_page_alive(None)
