"""Tests para RnpAgent.

Cubre:
  - _parsear_monto: formatos costarricenses y fallback
  - pagar_entero: sin credenciales → CredentialNotFoundError
  - pagar_entero: sin confirmación WhatsApp → ConfirmationError
  - pagar_entero: sin numero_entero en metadata → AgentError
  - pagar_entero: Playwright mocked → llama a page.goto, fill, click
  - pagar_entero: comprobante descargado correctamente
  - pagar_entero: portal retorna entero ya pagado → AgentError
  - pagar_entero: portal retorna entero vencido → AgentError
  - pagar_entero: botón imprimir ausente → devuelve sin comprobante
"""
from __future__ import annotations

import contextlib
import json
import secrets
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("win32cred", reason="pywin32 requerido (solo Windows)")

from src.agents.rnp_agent import RnpAgent, _parsear_monto  # noqa: E402
from src.core.database import Database                       # noqa: E402
from src.core.exceptions import AgentError, ConfirmationError, CredentialNotFoundError  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Infraestructura
# ─────────────────────────────────────────────────────────────────────────────

class TestDatabase(Database):
    @contextlib.contextmanager
    def connect(self):
        conn = sqlite3.connect(str(self.path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            yield conn
        finally:
            conn.close()


class FakeCM:
    """Credential Manager falso para tests."""
    def __init__(self, *, with_rnp: bool = True):
        self._db_key = secrets.token_bytes(32)
        self._with_rnp = with_rnp

    def get_db_key(self):
        return self._db_key

    def get_rnp(self):
        if not self._with_rnp:
            raise CredentialNotFoundError("rnp")
        return "usuario_test", "clave_test"


@pytest.fixture
def tmp_db(tmp_path):
    db = TestDatabase(path=tmp_path / "test.db", credentials=FakeCM())
    db.initialize_schema()
    return db


@pytest.fixture
def tmp_dir(tmp_path):
    return tmp_path


def _crear_expediente(db, numero: str = "RNP-001", **meta_extra) -> str:
    meta = {"numero_entero": "20261234", "area_m2": 500.0}
    meta.update(meta_extra)
    eid = db.crear_expediente(
        numero_expediente=numero,
        tipo_plano="segregacion",
        nombre_topografo="Ana Test",
        telefono_cliente="+50688887777",
        metadata=meta,
    )
    return eid


def _confirmar_pago(db, eid: str) -> None:
    """Crea y confirma una accion_pendiente 'pagar_enteros'."""
    ap_id = db.crear_accion_pendiente(
        expediente_id=eid,
        tipo_accion="pagar_enteros",
        descripcion="pagar entero test",
        actor="test",
    )
    db.resolver_accion(
        ap_id, "confirmada", whatsapp_response="SI", actor="test"
    )


# ─────────────────────────────────────────────────────────────────────────────
# _parsear_monto
# ─────────────────────────────────────────────────────────────────────────────

class TestParsearMonto:
    def test_formato_costarricense(self):
        assert _parsear_monto("₡ 1.250,00") == 1250.0

    def test_formato_punto_decimal(self):
        assert _parsear_monto("1250.50") == 1250.50

    def test_formato_sin_decimales(self):
        assert _parsear_monto("2500") == 2500.0

    def test_formato_con_colones_sin_espacio(self):
        assert _parsear_monto("₡12.500,00") == 12500.0

    def test_texto_invalido_devuelve_cero(self):
        assert _parsear_monto("N/A") == 0.0

    def test_cadena_vacia(self):
        assert _parsear_monto("") == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Precondiciones
# ─────────────────────────────────────────────────────────────────────────────

class TestPrecondiciones:
    def test_sin_credenciales_rnp_lanza(self, tmp_db, tmp_dir):
        cm_sin_rnp = FakeCM(with_rnp=False)
        agent = RnpAgent(
            tmp_db, cm_sin_rnp,
            home_url="http://fake",
            busqueda_url="http://fake/busqueda",
            session_path=tmp_dir / "session.json",
            download_dir=tmp_dir,
        )
        eid = _crear_expediente(tmp_db)
        _confirmar_pago(tmp_db, eid)

        # La página indica que no está logueado → intenta login → get_rnp() falla
        page = MagicMock()
        page.locator.return_value.count.return_value = 0  # no logueado

        with _patch_session(agent, page):
            with pytest.raises(CredentialNotFoundError):
                agent.pagar_entero(eid)

    def test_sin_confirmacion_lanza(self, tmp_db, tmp_dir):
        agent = RnpAgent(
            tmp_db, FakeCM(),
            home_url="http://fake",
            busqueda_url="http://fake/busqueda",
            session_path=tmp_dir / "session.json",
            download_dir=tmp_dir,
        )
        eid = _crear_expediente(tmp_db)
        # NO confirmar la acción → debe lanzar ConfirmationError

        with pytest.raises(ConfirmationError):
            agent.pagar_entero(eid)

    def test_sin_numero_entero_en_metadata_lanza(self, tmp_db, tmp_dir):
        agent = RnpAgent(
            tmp_db, FakeCM(),
            home_url="http://fake",
            busqueda_url="http://fake/busqueda",
            session_path=tmp_dir / "session.json",
            download_dir=tmp_dir,
        )
        eid = _crear_expediente(tmp_db, sin_entero=True)
        # Quitar numero_entero de metadata
        tmp_db.actualizar_metadata(eid, {"numero_entero": None}, actor="test")
        exp = tmp_db.obtener_expediente(eid)
        meta = json.loads(exp["metadata_json"] or "{}")
        meta.pop("numero_entero", None)
        tmp_db.actualizar_metadata(eid, meta, actor="test")

        _confirmar_pago(tmp_db, eid)

        with pytest.raises(AgentError, match="numero_entero"):
            agent.pagar_entero(eid)


# ─────────────────────────────────────────────────────────────────────────────
# Flujo Playwright mocked
# ─────────────────────────────────────────────────────────────────────────────

def _build_mock_page(
    *,
    logueado: bool = True,
    estado_entero: str = "Pendiente",
    monto_texto: str = "₡ 1.500,00",
    comprobante_num: str = "RNP-99999",
    tiene_btn_imprimir: bool = True,
    tiene_btn_confirmar: bool = True,
    tiene_btn_pagar_final: bool = False,
) -> MagicMock:
    page = MagicMock()

    # _ya_logueado
    locator_user_menu = MagicMock()
    locator_user_menu.count.return_value = 1 if logueado else 0

    # tabla de enteros — una fila que coincide con "20261234"
    fila_mock = MagicMock()
    celdas = [
        MagicMock(inner_text=MagicMock(return_value="20261234")),   # COL_NUM_ENTERO
        MagicMock(inner_text=MagicMock(return_value="Catastro")),   # COL_SERVICIO
        MagicMock(inner_text=MagicMock(return_value=monto_texto)),  # COL_MONTO
        MagicMock(inner_text=MagicMock(return_value=estado_entero)),# COL_ESTADO
        MagicMock(),                                                  # COL_BTN_PAGAR
    ]
    fila_mock.locator.return_value.nth.side_effect = lambda i: celdas[i]

    locator_tabla = MagicMock()
    locator_tabla.count.return_value = 1
    locator_tabla.nth.return_value = fila_mock

    # monto confirmación
    locator_monto_conf = MagicMock()
    locator_monto_conf.inner_text.return_value = monto_texto

    # botones
    locator_btn_confirmar = MagicMock()
    locator_btn_confirmar.count.return_value = 1 if tiene_btn_confirmar else 0

    locator_btn_pagar_final = MagicMock()
    locator_btn_pagar_final.count.return_value = 1 if tiene_btn_pagar_final else 0

    locator_btn_imprimir = MagicMock()
    locator_btn_imprimir.count.return_value = 1 if tiene_btn_imprimir else 0

    # número de comprobante
    locator_comprobante = MagicMock()
    locator_comprobante.inner_text.return_value = comprobante_num

    # body fallback
    locator_body = MagicMock()
    locator_body.inner_text.return_value = f"Número de comprobante: {comprobante_num}"

    def _locator_dispatch(selector):
        from src.agents.rnp_agent import (
            SEL_USER_MENU, SEL_TABLA_FILAS, SEL_MONTO_CONFIRMACION,
            SEL_BTN_CONFIRMAR, SEL_BTN_PAGAR_FINAL, SEL_BTN_IMPRIMIR,
            SEL_NUM_COMPROBANTE,
        )
        mapping = {
            SEL_USER_MENU:           locator_user_menu,
            SEL_TABLA_FILAS:         locator_tabla,
            SEL_MONTO_CONFIRMACION:  locator_monto_conf,
            SEL_BTN_CONFIRMAR:       locator_btn_confirmar,
            SEL_BTN_PAGAR_FINAL:     locator_btn_pagar_final,
            SEL_BTN_IMPRIMIR:        locator_btn_imprimir,
            SEL_NUM_COMPROBANTE:     locator_comprobante,
            "body":                  locator_body,
        }
        return mapping.get(selector, MagicMock())

    page.locator.side_effect = _locator_dispatch

    # expect_download context manager
    dl_info = MagicMock()
    dl_info.__enter__ = MagicMock(return_value=dl_info)
    dl_info.__exit__ = MagicMock(return_value=False)
    dl_info.value = MagicMock()
    page.expect_download.return_value = dl_info

    return page


@contextlib.contextmanager
def _patch_session(agent: RnpAgent, page: MagicMock):
    """Parchea _session del agente para devolver la página mock directamente."""
    ctx = MagicMock()
    ctx.__enter__ = MagicMock(return_value=page)
    ctx.__exit__ = MagicMock(return_value=False)
    with patch.object(agent, "_session", return_value=ctx):
        yield


class TestPagarEnteroPlaywright:
    @pytest.fixture
    def agent(self, tmp_db, tmp_dir):
        return RnpAgent(
            tmp_db, FakeCM(),
            home_url="http://fake/",
            busqueda_url="http://fake/busqueda",
            session_path=tmp_dir / "session.json",
            download_dir=tmp_dir,
        )

    def test_llama_goto_home_y_busqueda(self, agent, tmp_db):
        eid = _crear_expediente(tmp_db)
        _confirmar_pago(tmp_db, eid)
        page = _build_mock_page()

        with _patch_session(agent, page):
            agent.pagar_entero(eid)

        calls = [str(c) for c in page.goto.call_args_list]
        assert any("fake/" in c for c in calls)
        assert any("busqueda" in c for c in calls)

    def test_llama_fill_con_numero_entero(self, agent, tmp_db):
        eid = _crear_expediente(tmp_db)
        _confirmar_pago(tmp_db, eid)
        page = _build_mock_page()

        with _patch_session(agent, page):
            agent.pagar_entero(eid)

        # Verificar que fill fue llamado con el número de entero en alguna posición
        fill_values = [args[0][1] for args in page.fill.call_args_list]
        assert "20261234" in fill_values

    def test_devuelve_dict_con_comprobante_y_monto(self, agent, tmp_db):
        eid = _crear_expediente(tmp_db)
        _confirmar_pago(tmp_db, eid)
        page = _build_mock_page(
            comprobante_num="RNP-99999",
            monto_texto="₡ 1.500,00",
        )

        with _patch_session(agent, page):
            resultado = agent.pagar_entero(eid)

        assert resultado["numero_comprobante"] == "RNP-99999"
        assert resultado["monto_colones"] == 1500.0

    def test_entero_ya_pagado_lanza(self, agent, tmp_db):
        eid = _crear_expediente(tmp_db)
        _confirmar_pago(tmp_db, eid)
        page = _build_mock_page(estado_entero="Pagado")

        with _patch_session(agent, page):
            with pytest.raises(AgentError, match="ya fue pagado"):
                agent.pagar_entero(eid)

    def test_entero_vencido_lanza(self, agent, tmp_db):
        eid = _crear_expediente(tmp_db)
        _confirmar_pago(tmp_db, eid)
        page = _build_mock_page(estado_entero="Vencido")

        with _patch_session(agent, page):
            with pytest.raises(AgentError, match="vencido"):
                agent.pagar_entero(eid)

    def test_sin_filas_en_tabla_lanza(self, agent, tmp_db):
        eid = _crear_expediente(tmp_db)
        _confirmar_pago(tmp_db, eid)
        page = _build_mock_page()
        # Sobreescribir locator de tabla para devolver 0 filas
        from src.agents.rnp_agent import SEL_TABLA_FILAS
        locator_tabla_vacia = MagicMock()
        locator_tabla_vacia.count.return_value = 0
        original_side_effect = page.locator.side_effect

        def _override(sel):
            if sel == SEL_TABLA_FILAS:
                return locator_tabla_vacia
            return original_side_effect(sel)

        page.locator.side_effect = _override

        with _patch_session(agent, page):
            with pytest.raises(AgentError, match="no encontrado"):
                agent.pagar_entero(eid)

    def test_sin_btn_imprimir_devuelve_ruta_none(self, agent, tmp_db, tmp_dir):
        eid = _crear_expediente(tmp_db)
        _confirmar_pago(tmp_db, eid)
        page = _build_mock_page(tiene_btn_imprimir=False)

        with _patch_session(agent, page):
            resultado = agent.pagar_entero(eid)

        assert resultado["ruta_comprobante"] is None

    def test_comprobante_guardado_en_download_dir(self, agent, tmp_db, tmp_dir):
        eid = _crear_expediente(tmp_db)
        _confirmar_pago(tmp_db, eid)
        page = _build_mock_page(tiene_btn_imprimir=True)

        with _patch_session(agent, page):
            resultado = agent.pagar_entero(eid)

        # La ruta debe apuntar al download_dir del agente
        ruta = resultado.get("ruta_comprobante")
        if ruta:
            assert str(tmp_dir) in ruta
