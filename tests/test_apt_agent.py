"""Tests para APTAgent — portal APT-CFIA.

Todos los tests usan mocks de Playwright: ningun test abre un navegador real.
La logica probada incluye: normalizacion de estados, manejo de BD,
verificacion de confirmacion, construccion de argumentos de upload,
y manejo de errores.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

pytest.importorskip(
    "win32cred",
    reason="pywin32 requerido (este proyecto solo corre en Windows)",
)
pytest.importorskip(
    "playwright",
    reason="playwright requerido para APTAgent",
)

from tests.conftest import FakeCredentialManager, TestDatabase  # noqa: E402
from src.agents.apt_agent import (                               # noqa: E402
    APTAgent,
    ESTADO_DEFECTUOSO,
    ESTADO_EN_EDICION,
    ESTADO_CALIFICACION,
    ESTADO_INSCRITO,
    TIPO_ANVERSO,
    TIPO_ENTERO,
    TIPO_DERROTERO,
    TIPO_VISADO,
)
from src.core.exceptions import AgentError, ConfirmationError    # noqa: E402


# ---------------------------------------------------------------------------
# Helpers y fixtures
# ---------------------------------------------------------------------------

def _make_db(tmp_path: Path) -> TestDatabase:
    creds = FakeCredentialManager()
    db = TestDatabase(path=tmp_path / "test.db", credentials=creds)
    db.initialize_schema()
    return db


def _crear_expediente(db: TestDatabase, *, tramite: str | None = "1256709") -> str:
    """Crea un expediente de prueba y devuelve su ID."""
    meta: dict = {}
    if tramite:
        meta["apt_tramite"] = tramite
    try:
        db.crear_usuario(telefono="50600000001", nombre="Test Op",
                         rol="admin", actor="test")
    except Exception:
        pass  # ya existe
    eid = db.crear_expediente(
        numero_expediente="EXP-TEST-0001",
        tipo_plano="segregacion",
        nombre_cliente="Cliente Test",
        cedula_topografo="1-1111-2222",
        nombre_topografo="Topo Test",
        telefono_cliente="50600000099",
        municipalidad="San Ramon",
        metadata=meta,
        actor="50600000001",
    )
    return eid


def _make_agent(tmp_path: Path, db=None) -> APTAgent:
    if db is None:
        db = _make_db(tmp_path)
    creds = FakeCredentialManager()
    return APTAgent(
        db=db,
        credentials=creds,
        headless=True,
        slow_mo_ms=0,
        session_path=tmp_path / "state.json",
    )


# ---------------------------------------------------------------------------
# Clase 1: _normalizar_estado (funcion pura, sin mocks)
# ---------------------------------------------------------------------------

class TestNormalizarEstado:
    def test_defectuoso(self):
        assert APTAgent._normalizar_estado(ESTADO_DEFECTUOSO) == "respondido"

    def test_inscrito(self):
        assert APTAgent._normalizar_estado(ESTADO_INSCRITO) == "inscrito"

    def test_calificacion(self):
        assert APTAgent._normalizar_estado(ESTADO_CALIFICACION) == "pendiente"

    def test_en_edicion(self):
        assert APTAgent._normalizar_estado(ESTADO_EN_EDICION) == "edicion"

    def test_none(self):
        assert APTAgent._normalizar_estado(None) is None

    def test_desconocido(self):
        result = APTAgent._normalizar_estado("Otro Estado")
        assert "otro" in result.lower()

    def test_consultar_estado_r1_delegado(self, tmp_path):
        """consultar_estado_r1 delega a consultar_estado y normaliza."""
        db = _make_db(tmp_path)
        eid = _crear_expediente(db, tramite="9999")
        agent = _make_agent(tmp_path, db=db)

        with patch.object(agent, "consultar_estado", return_value=ESTADO_DEFECTUOSO):
            result = agent.consultar_estado_r1(eid)
        assert result == "respondido"

    def test_consultar_estado_r2_delegado(self, tmp_path):
        """consultar_estado_r2 delega a consultar_estado_r1."""
        db = _make_db(tmp_path)
        eid = _crear_expediente(db, tramite="9999")
        agent = _make_agent(tmp_path, db=db)

        with patch.object(agent, "consultar_estado", return_value=ESTADO_INSCRITO):
            result = agent.consultar_estado_r2(eid)
        assert result == "inscrito"


# ---------------------------------------------------------------------------
# Clase 2: ya_logueado (logica de deteccion)
# ---------------------------------------------------------------------------

class TestYaLogueado:
    def _fake_page(self, count: int, texto: str = "Usuario Admin") -> MagicMock:
        """Crea un page mock realista: count controla si el elemento existe,
        texto es lo que devuelve inner_text() (debe ser > 1 char para True)."""
        page = MagicMock()
        page.url = "https://apt.cfia.or.cr/APT2/Home"
        locator = MagicMock()
        locator.count.return_value = count
        locator.first.is_visible.return_value = (count > 0)
        locator.first.inner_text.return_value = texto
        page.locator.return_value = locator
        return page

    def test_true_cuando_menu_existe(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = self._fake_page(1, texto="Luis Rojas")   # texto > 1 char
        assert agent._ya_logueado(page) is True

    def test_false_cuando_menu_ausente(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = self._fake_page(0)
        assert agent._ya_logueado(page) is False

    def test_false_cuando_sso_url(self, tmp_path):
        """Si el URL es del SSO, _ya_logueado retorna False sin importar el DOM."""
        agent = _make_agent(tmp_path)
        page = self._fake_page(1, texto="Usuario Admin")
        page.url = "https://sso.cfia.or.cr/sso/?IdSystem=1"
        assert agent._ya_logueado(page) is False

    def test_false_cuando_excepcion(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = MagicMock()
        page.url = "https://apt.cfia.or.cr/APT2/Home"
        page.locator.side_effect = Exception("timeout")
        assert agent._ya_logueado(page) is False


# ---------------------------------------------------------------------------
# Clase 3: consultar_estado
# ---------------------------------------------------------------------------

class TestConsultarEstado:
    def _mock_session(self, agent, filas: list[dict]):
        """Parchea _session para devolver una pagina con filas mock."""
        mock_page = MagicMock()
        mock_page.locator.return_value.count.return_value = 1   # logueado

        # _buscar_en_consulta -> _parsear_tabla devuelve filas
        with patch.object(agent, "_buscar_en_consulta", return_value=filas):
            yield mock_page

    def test_devuelve_estado_correcto(self, tmp_path):
        db = _make_db(tmp_path)
        eid = _crear_expediente(db, tramite="1256709")
        agent = _make_agent(tmp_path, db=db)

        fila = {"tramite": "1256709", "estado": ESTADO_DEFECTUOSO,
                "detalle": "test", "tomo": "", "asiento": "",
                "fecha": "", "proceso": "", "_row": MagicMock()}

        mock_page = MagicMock()
        mock_page.locator.return_value.count.return_value = 1

        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=mock_page)
        ctx.__exit__ = MagicMock(return_value=False)

        with patch.object(agent, "_session", return_value=ctx), \
             patch.object(agent, "_asegurar_login"), \
             patch.object(agent, "_buscar_en_consulta", return_value=[fila]):
            result = agent.consultar_estado(eid)

        assert result == ESTADO_DEFECTUOSO

    def test_none_si_no_hay_tramite(self, tmp_path):
        db = _make_db(tmp_path)
        eid = _crear_expediente(db, tramite=None)
        agent = _make_agent(tmp_path, db=db)

        result = agent.consultar_estado(eid)
        assert result is None

    def test_error_si_expediente_no_existe(self, tmp_path):
        db = _make_db(tmp_path)
        agent = _make_agent(tmp_path, db=db)
        with pytest.raises(AgentError):
            agent.consultar_estado("id-inexistente")

    def test_none_si_tramite_no_encontrado_en_portal(self, tmp_path):
        db = _make_db(tmp_path)
        eid = _crear_expediente(db, tramite="9999")
        agent = _make_agent(tmp_path, db=db)

        mock_page = MagicMock()
        mock_page.locator.return_value.count.return_value = 1
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=mock_page)
        ctx.__exit__ = MagicMock(return_value=False)

        with patch.object(agent, "_session", return_value=ctx), \
             patch.object(agent, "_asegurar_login"), \
             patch.object(agent, "_buscar_en_consulta", return_value=[]):
            result = agent.consultar_estado(eid)

        assert result is None


# ---------------------------------------------------------------------------
# Clase 4: confirmacion requerida
# ---------------------------------------------------------------------------

class TestConfirmacionRequerida:
    def test_subir_sin_confirmacion_lanza(self, tmp_path):
        db = _make_db(tmp_path)
        eid = _crear_expediente(db, tramite="1256709")
        agent = _make_agent(tmp_path, db=db)

        dummy_pdf = tmp_path / "plano.pdf"
        dummy_pdf.write_bytes(b"fake pdf")

        with pytest.raises(ConfirmationError):
            agent.subir_archivos_plano(eid, anverso=dummy_pdf)

    def test_presentar_r1_sin_confirmacion_lanza(self, tmp_path):
        db = _make_db(tmp_path)
        eid = _crear_expediente(db, tramite="1256709")
        agent = _make_agent(tmp_path, db=db)

        with pytest.raises(ConfirmationError):
            agent.presentar_r1(eid, Path("anverso.pdf"), Path("entero.pdf"))

    def test_presentar_r2_sin_confirmacion_lanza(self, tmp_path):
        db = _make_db(tmp_path)
        eid = _crear_expediente(db, tramite="1256709")
        agent = _make_agent(tmp_path, db=db)

        with pytest.raises(ConfirmationError):
            agent.presentar_r2(eid, Path("a.pdf"), Path("e.pdf"))


# ---------------------------------------------------------------------------
# Clase 5: subir_archivos_plano (con mock de session y confirmacion)
# ---------------------------------------------------------------------------

class TestSubirArchivosPlano:
    def _crear_confirmacion(self, db, eid):
        """Inserta una confirmacion fake en la BD."""
        db.registrar_accion_pendiente(
            expediente_id=eid,
            tipo_accion="subir_apt",
            confirmada=True,
            actor="test",
        )

    def test_error_si_no_hay_tramite(self, tmp_path):
        db = _make_db(tmp_path)
        eid = _crear_expediente(db, tramite=None)
        agent = _make_agent(tmp_path, db=db)

        # Mockear la confirmacion para que pase
        with patch.object(agent, "_exigir_confirmacion", return_value={}):
            with pytest.raises(AgentError, match="apt_tramite"):
                agent.subir_archivos_plano(eid, anverso=tmp_path / "x.pdf")

    def test_error_si_sin_archivos(self, tmp_path):
        db = _make_db(tmp_path)
        eid = _crear_expediente(db, tramite="1256709")
        agent = _make_agent(tmp_path, db=db)

        with patch.object(agent, "_exigir_confirmacion", return_value={}):
            with pytest.raises(AgentError, match="ningun archivo"):
                agent.subir_archivos_plano(eid)

    def test_error_si_archivo_no_existe(self, tmp_path):
        db = _make_db(tmp_path)
        eid = _crear_expediente(db, tramite="1256709")
        agent = _make_agent(tmp_path, db=db)

        with patch.object(agent, "_exigir_confirmacion", return_value={}):
            with pytest.raises(AgentError, match="no existe"):
                agent.subir_archivos_plano(
                    eid, anverso=tmp_path / "no_existe.pdf"
                )

    def test_sube_un_archivo_ok(self, tmp_path):
        db = _make_db(tmp_path)
        eid = _crear_expediente(db, tramite="1256709")
        agent = _make_agent(tmp_path, db=db)

        pdf = tmp_path / "anverso.pdf"
        pdf.write_bytes(b"%PDF fake")

        fila = {"tramite": "1256709", "estado": ESTADO_EN_EDICION,
                "detalle": "test", "_row": MagicMock()}

        mock_page = MagicMock()
        mock_page.locator.return_value.count.return_value = 1

        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=mock_page)
        ctx.__exit__ = MagicMock(return_value=False)

        with patch.object(agent, "_exigir_confirmacion", return_value={}), \
             patch.object(agent, "_session", return_value=ctx), \
             patch.object(agent, "_asegurar_login"), \
             patch.object(agent, "_buscar_en_consulta", return_value=[fila]), \
             patch.object(agent, "_abrir_tramite"), \
             patch.object(agent, "_ir_a_tab_planos"), \
             patch.object(agent, "_seleccionar_plano"), \
             patch.object(agent, "_expandir_archivos"), \
             patch.object(agent, "_subir_un_archivo") as mock_subir:

            result = agent.subir_archivos_plano(eid, anverso=pdf)

        assert result["tramite"] == "1256709"
        assert len(result["archivos_subidos"]) == 1
        assert len(result["errores"]) == 0
        mock_subir.assert_called_once_with(mock_page, TIPO_ANVERSO, pdf)

    def test_sube_multiples_archivos(self, tmp_path):
        db = _make_db(tmp_path)
        eid = _crear_expediente(db, tramite="1256709")
        agent = _make_agent(tmp_path, db=db)

        pdf_a = tmp_path / "anverso.pdf"
        pdf_e = tmp_path / "entero.pdf"
        zip_d = tmp_path / "derrotero.zip"
        for f in (pdf_a, pdf_e, zip_d):
            f.write_bytes(b"fake")

        fila = {"tramite": "1256709", "estado": ESTADO_EN_EDICION,
                "detalle": "test", "_row": MagicMock()}

        mock_page = MagicMock()
        mock_page.locator.return_value.count.return_value = 1
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=mock_page)
        ctx.__exit__ = MagicMock(return_value=False)

        with patch.object(agent, "_exigir_confirmacion", return_value={}), \
             patch.object(agent, "_session", return_value=ctx), \
             patch.object(agent, "_asegurar_login"), \
             patch.object(agent, "_buscar_en_consulta", return_value=[fila]), \
             patch.object(agent, "_abrir_tramite"), \
             patch.object(agent, "_ir_a_tab_planos"), \
             patch.object(agent, "_seleccionar_plano"), \
             patch.object(agent, "_expandir_archivos"), \
             patch.object(agent, "_subir_un_archivo") as mock_subir:

            result = agent.subir_archivos_plano(
                eid, anverso=pdf_a, entero=pdf_e, derrotero=zip_d
            )

        assert len(result["archivos_subidos"]) == 3
        assert len(result["errores"]) == 0
        assert mock_subir.call_count == 3

    def test_tramite_no_encontrado_lanza(self, tmp_path):
        db = _make_db(tmp_path)
        eid = _crear_expediente(db, tramite="9999")
        agent = _make_agent(tmp_path, db=db)

        pdf = tmp_path / "anverso.pdf"
        pdf.write_bytes(b"fake")

        mock_page = MagicMock()
        mock_page.locator.return_value.count.return_value = 1
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=mock_page)
        ctx.__exit__ = MagicMock(return_value=False)

        with patch.object(agent, "_exigir_confirmacion", return_value={}), \
             patch.object(agent, "_session", return_value=ctx), \
             patch.object(agent, "_asegurar_login"), \
             patch.object(agent, "_buscar_en_consulta", return_value=[]):

            with pytest.raises(AgentError, match="no encontrado"):
                agent.subir_archivos_plano(eid, anverso=pdf)

    def test_error_en_un_archivo_se_reporta(self, tmp_path):
        """Errores de upload individual se acumulan en 'errores', no abortan."""
        db = _make_db(tmp_path)
        eid = _crear_expediente(db, tramite="1256709")
        agent = _make_agent(tmp_path, db=db)

        pdf_a = tmp_path / "anverso.pdf"
        pdf_e = tmp_path / "entero.pdf"
        pdf_a.write_bytes(b"fake")
        pdf_e.write_bytes(b"fake")

        fila = {"tramite": "1256709", "estado": ESTADO_EN_EDICION,
                "detalle": "test", "_row": MagicMock()}

        def _subir_side_effect(page, tipo, path):
            if tipo == TIPO_ENTERO:
                raise Exception("timeout APT")

        mock_page = MagicMock()
        mock_page.locator.return_value.count.return_value = 1
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=mock_page)
        ctx.__exit__ = MagicMock(return_value=False)

        with patch.object(agent, "_exigir_confirmacion", return_value={}), \
             patch.object(agent, "_session", return_value=ctx), \
             patch.object(agent, "_asegurar_login"), \
             patch.object(agent, "_buscar_en_consulta", return_value=[fila]), \
             patch.object(agent, "_abrir_tramite"), \
             patch.object(agent, "_ir_a_tab_planos"), \
             patch.object(agent, "_seleccionar_plano"), \
             patch.object(agent, "_expandir_archivos"), \
             patch.object(agent, "_subir_un_archivo",
                          side_effect=_subir_side_effect):

            result = agent.subir_archivos_plano(eid, anverso=pdf_a, entero=pdf_e)

        assert len(result["archivos_subidos"]) == 1
        assert len(result["errores"]) == 1
        assert "entero" in result["errores"][0].lower()


# ---------------------------------------------------------------------------
# Clase 6: presentar_r1 / presentar_r2
# ---------------------------------------------------------------------------

class TestPresentarRondas:
    def test_r1_llama_subir_y_devuelve_listo(self, tmp_path):
        db = _make_db(tmp_path)
        eid = _crear_expediente(db, tramite="1256709")
        agent = _make_agent(tmp_path, db=db)

        pdf_a = tmp_path / "anverso.pdf"
        pdf_e = tmp_path / "entero.pdf"
        pdf_a.write_bytes(b"fake")
        pdf_e.write_bytes(b"fake")

        subir_ret = {"tramite": "1256709", "archivos_subidos": ["a.pdf", "e.pdf"], "errores": []}

        with patch.object(agent, "_exigir_confirmacion", return_value={}), \
             patch.object(agent, "subir_archivos_plano", return_value=subir_ret) as mock_subir:

            result = agent.presentar_r1(eid, pdf_a, pdf_e)

        assert result["listo_para_fd"] is True
        mock_subir.assert_called_once_with(eid, anverso=pdf_a, entero=pdf_e, derrotero=None)

    def test_r1_listo_es_false_si_hay_errores(self, tmp_path):
        db = _make_db(tmp_path)
        eid = _crear_expediente(db, tramite="1256709")
        agent = _make_agent(tmp_path, db=db)

        subir_ret = {"tramite": "1256709", "archivos_subidos": [], "errores": ["fallo.pdf: timeout"]}

        with patch.object(agent, "_exigir_confirmacion", return_value={}), \
             patch.object(agent, "subir_archivos_plano", return_value=subir_ret):

            result = agent.presentar_r1(eid, Path("a.pdf"), Path("e.pdf"))

        assert result["listo_para_fd"] is False

    def test_r2_incluye_visado(self, tmp_path):
        db = _make_db(tmp_path)
        eid = _crear_expediente(db, tramite="1256709")
        agent = _make_agent(tmp_path, db=db)

        pdf_a = tmp_path / "anverso.pdf"
        pdf_e = tmp_path / "entero.pdf"
        pdf_v = tmp_path / "visado.pdf"
        for f in (pdf_a, pdf_e, pdf_v):
            f.write_bytes(b"fake")

        subir_ret = {"tramite": "1256709", "archivos_subidos": ["a", "e", "v"], "errores": []}

        with patch.object(agent, "_exigir_confirmacion", return_value={}), \
             patch.object(agent, "subir_archivos_plano", return_value=subir_ret) as mock_subir:

            result = agent.presentar_r2(eid, pdf_a, pdf_e, archivo_visado=pdf_v)

        assert result["listo_para_fd"] is True
        mock_subir.assert_called_once_with(
            eid, anverso=pdf_a, entero=pdf_e, visado=pdf_v, derrotero=None
        )


# ---------------------------------------------------------------------------
# Clase 7: descargar_archivos_r1
# ---------------------------------------------------------------------------

class TestDescargarArchivos:
    def test_vacio_si_no_hay_defectuosos(self, tmp_path):
        db = _make_db(tmp_path)
        eid = _crear_expediente(db, tramite="1256709")
        agent = _make_agent(tmp_path, db=db)

        fila_ok = {"tramite": "1256709", "estado": ESTADO_EN_EDICION,
                   "detalle": "test", "_row": MagicMock()}

        mock_page = MagicMock()
        mock_page.locator.return_value.count.return_value = 1
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=mock_page)
        ctx.__exit__ = MagicMock(return_value=False)

        with patch.object(agent, "_session", return_value=ctx), \
             patch.object(agent, "_asegurar_login"), \
             patch.object(agent, "_buscar_en_consulta", return_value=[fila_ok]):

            result = agent.descargar_archivos_r1(eid, tmp_path / "dest")

        assert result == []

    def test_error_si_sin_tramite(self, tmp_path):
        db = _make_db(tmp_path)
        eid = _crear_expediente(db, tramite=None)
        agent = _make_agent(tmp_path, db=db)

        with pytest.raises(AgentError, match="apt_tramite"):
            agent.descargar_archivos_r1(eid, tmp_path / "dest")

    def test_llama_descargar_por_cada_defectuosa(self, tmp_path):
        db = _make_db(tmp_path)
        eid = _crear_expediente(db, tramite="1256709")
        agent = _make_agent(tmp_path, db=db)

        fila_def = {"tramite": "1256709", "estado": ESTADO_DEFECTUOSO,
                    "detalle": "plano1", "_row": MagicMock()}

        mock_page = MagicMock()
        mock_page.locator.return_value.count.return_value = 1
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=mock_page)
        ctx.__exit__ = MagicMock(return_value=False)

        descargado = tmp_path / "minuta.pdf"
        descargado.write_bytes(b"fake")

        with patch.object(agent, "_session", return_value=ctx), \
             patch.object(agent, "_asegurar_login"), \
             patch.object(agent, "_buscar_en_consulta", return_value=[fila_def]), \
             patch.object(agent, "_descargar_documentos_fila",
                          return_value=[descargado]) as mock_dl:

            result = agent.descargar_archivos_r1(eid, tmp_path / "dest")

        assert len(result) == 1
        assert result[0] == descargado
        mock_dl.assert_called_once()


# ---------------------------------------------------------------------------
# Clase 8: get_tramites_activos
# ---------------------------------------------------------------------------

class TestGetTramitesActivos:
    def test_devuelve_lista_de_dicts(self, tmp_path):
        agent = _make_agent(tmp_path)

        filas = [
            {"tramite": "1001", "estado": ESTADO_EN_EDICION,
             "detalle": "plano1", "tomo": "", "asiento": "",
             "fecha": "01/01/2026", "proceso": "Digital", "_row": MagicMock()},
            {"tramite": "1002", "estado": ESTADO_DEFECTUOSO,
             "detalle": "plano2", "tomo": "2026", "asiento": "99",
             "fecha": "02/01/2026", "proceso": "Digital", "_row": MagicMock()},
        ]

        mock_page = MagicMock()
        mock_page.locator.return_value.count.return_value = 1
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=mock_page)
        ctx.__exit__ = MagicMock(return_value=False)

        with patch.object(agent, "_session", return_value=ctx), \
             patch.object(agent, "_asegurar_login"), \
             patch.object(agent, "_parsear_tabla", return_value=filas):

            result = agent.get_tramites_activos()

        assert len(result) == 2
        assert result[0]["tramite"] == "1001"
        assert result[1]["estado"] == ESTADO_DEFECTUOSO


# ---------------------------------------------------------------------------
# Clase 9: parsear_tabla (parseo de filas HTML mockeadas)
# ---------------------------------------------------------------------------

class TestParsearTabla:
    def _make_celda(self, texto: str) -> MagicMock:
        c = MagicMock()
        c.inner_text.return_value = texto
        return c

    def test_parsea_fila_completa(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = MagicMock()

        # 10 columnas: Acciones, Contrato, Detalle, Area, Tomo, Asiento, Tipo, Estado, Fecha, Proceso
        celdas = [
            self._make_celda(""),           # acciones
            self._make_celda("1256709"),    # contrato
            self._make_celda("daniel bureal"),  # detalle
            self._make_celda("20966.88"),   # area
            self._make_celda(""),           # tomo
            self._make_celda(""),           # asiento
            self._make_celda(""),           # tipo
            self._make_celda(ESTADO_EN_EDICION),  # estado
            self._make_celda("04/05/2026"), # fecha
            self._make_celda("Digital"),    # proceso
        ]
        fila_mock = MagicMock()
        fila_mock.query_selector_all.return_value = celdas
        page.query_selector_all.return_value = [fila_mock]

        result = agent._parsear_tabla(page)

        assert len(result) == 1
        assert result[0]["tramite"] == "1256709"
        assert result[0]["detalle"] == "daniel bureal"
        assert result[0]["estado"] == ESTADO_EN_EDICION
        assert result[0]["fecha"] == "04/05/2026"

    def test_ignora_fila_con_pocas_celdas(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = MagicMock()

        fila_mock = MagicMock()
        fila_mock.query_selector_all.return_value = [self._make_celda("x")] * 3
        page.query_selector_all.return_value = [fila_mock]

        result = agent._parsear_tabla(page)
        assert result == []

    def test_tabla_vacia(self, tmp_path):
        agent = _make_agent(tmp_path)
        page = MagicMock()
        page.query_selector_all.return_value = []
        assert agent._parsear_tabla(page) == []


# ---------------------------------------------------------------------------
# Clase 10: APT SESION OK — evento manual y verificación de sesión
# ---------------------------------------------------------------------------

class TestSesionManualOK:
    """Tests para el nuevo flujo APT SESION OK:
    - confirmar_sesion_desde_whatsapp() activa el evento
    - iniciar_sesion_manual() responde al evento verificando _confirmar_sesion_apt()
    - El comando WhatsApp 'APT SESION OK' llama confirmar_sesion_desde_whatsapp()
    """

    def test_evento_inicia_limpio(self, tmp_path):
        """El evento _sesion_event está limpio al crear el agente."""
        agent = _make_agent(tmp_path)
        assert not agent._sesion_event.is_set()

    def test_confirmar_sesion_desde_whatsapp_activa_evento(self, tmp_path):
        """confirmar_sesion_desde_whatsapp() pone el evento."""
        agent = _make_agent(tmp_path)
        agent.confirmar_sesion_desde_whatsapp()
        assert agent._sesion_event.is_set()

    def test_confirmar_sesion_idempotente(self, tmp_path):
        """Llamar dos veces no rompe nada."""
        agent = _make_agent(tmp_path)
        agent.confirmar_sesion_desde_whatsapp()
        agent.confirmar_sesion_desde_whatsapp()
        assert agent._sesion_event.is_set()

    def test_iniciar_sesion_manual_detecta_evento_con_sesion_activa(self, tmp_path):
        """Si llega APT SESION OK y _confirmar_sesion_apt devuelve True → éxito."""
        import threading as _t
        agent = _make_agent(tmp_path)
        notifs: list[str] = []

        # Simular: sesión expirada al inicio → lleva a SSO → usuario loguea → OK
        call_count = [0]
        def _fake_confirmar(page) -> bool:
            call_count[0] += 1
            # Primera llamada (check inicial): expirada
            # Segunda llamada (tras APT SESION OK): activa
            return call_count[0] >= 2

        mock_ctx = MagicMock()
        mock_page = MagicMock()
        mock_page.url = "https://sso.cfia.or.cr/sso/?IdSystem=1"
        mock_ctx.pages = [mock_page]
        mock_ctx.new_page.return_value = mock_page
        mock_ctx.__enter__ = MagicMock(return_value=mock_ctx)
        mock_ctx.__exit__ = MagicMock(return_value=False)

        # No hay segundo contexto: la verificación reutiliza el page existente.

        def _fire_event():
            import time as _time
            _time.sleep(0.5)
            agent.confirmar_sesion_desde_whatsapp()

        with patch.object(agent, "_confirmar_sesion_apt", side_effect=_fake_confirmar), \
             patch.object(agent, "_limpiar_lock_perfil"), \
             patch.object(agent, "_cdp_disponible", return_value=False), \
             patch("playwright.sync_api.sync_playwright") as mock_sp, \
             patch("src.agents.apt_agent._PROFILE_LOCK"):

            mock_sp.return_value.__enter__.return_value.chromium \
                .launch_persistent_context.return_value = mock_ctx

            # Disparar evento en background
            t = _t.Thread(target=_fire_event, daemon=True)
            t.start()

            agent.iniciar_sesion_manual(
                notificar_fn=notifs.append,
                timeout_min=1,
            )
            t.join(timeout=5)

        # Debe haber enviado la notificación de sesión guardada
        assert any("Sesión APT guardada" in n or "Sesión APT activa" in n
                   or "guardada" in n.lower() or "activa" in n.lower()
                   for n in notifs), f"Notifs recibidas: {notifs}"

    def test_iniciar_sesion_manual_reintenta_si_sesion_no_confirmada(self, tmp_path):
        """APT SESION OK recibido pero sesión no confirmada → avisa y sigue esperando."""
        import threading as _t
        agent = _make_agent(tmp_path)
        notifs: list[str] = []

        # _confirmar_sesion_apt siempre devuelve False
        mock_page = MagicMock()
        mock_page.url = "https://sso.cfia.or.cr/sso/?IdSystem=1"
        mock_ctx = MagicMock()
        mock_ctx.pages = [mock_page]
        mock_ctx.new_page.return_value = mock_page

        def _fire_event_then_stop():
            import time as _time
            _time.sleep(0.3)
            agent.confirmar_sesion_desde_whatsapp()

        with patch.object(agent, "_confirmar_sesion_apt", return_value=False), \
             patch.object(agent, "_limpiar_lock_perfil"), \
             patch.object(agent, "_cdp_disponible", return_value=False), \
             patch("playwright.sync_api.sync_playwright") as mock_sp, \
             patch("src.agents.apt_agent._PROFILE_LOCK"), \
             pytest.raises(Exception):  # timeout esperado

            mock_sp.return_value.__enter__.return_value.chromium \
                .launch_persistent_context.return_value = mock_ctx

            t = _t.Thread(target=_fire_event_then_stop, daemon=True)
            t.start()
            agent.iniciar_sesion_manual(
                notificar_fn=notifs.append,
                # 0.1 s de deadline: la señal llega a los 0.3 s (el wait(3) la
                # atrapa), se envía "no detectada", y en la sig. comprobación
                # while el deadline ya pasó → AgentError esperado.
                timeout_min=0.1 / 60,
            )

        # Debe haber enviado el aviso de que la sesión no se detectó
        assert any("no detectada" in n.lower() or "sesión apt" in n.lower()
                   for n in notifs), f"Notifs: {notifs}"

    def test_confirmar_sesion_apt_retorna_false_cuando_redirige_a_sso(self, tmp_path):
        """_confirmar_sesion_apt devuelve False si APT redirige al SSO."""
        agent = _make_agent(tmp_path)
        page = MagicMock()
        page.url = "https://sso.cfia.or.cr/sso/?IdSystem=1"
        page.goto = MagicMock()

        assert agent._confirmar_sesion_apt(page) is False

    def test_confirmar_sesion_apt_retorna_true_cuando_url_es_apt(self, tmp_path):
        """_confirmar_sesion_apt devuelve True si la URL final es de APT."""
        agent = _make_agent(tmp_path)
        page = MagicMock()
        page.url = "https://apt.cfia.or.cr/APT2/Home"
        page.goto = MagicMock()

        assert agent._confirmar_sesion_apt(page) is True

    def test_confirmar_sesion_apt_retorna_false_ante_excepcion(self, tmp_path):
        """_confirmar_sesion_apt devuelve False si goto lanza excepción."""
        agent = _make_agent(tmp_path)
        page = MagicMock()
        page.goto.side_effect = Exception("Timeout 60000ms exceeded")

        assert agent._confirmar_sesion_apt(page) is False


# ---------------------------------------------------------------------------
# Clase 11: APT SESION OK via WhatsApp router
# ---------------------------------------------------------------------------

class TestWhatsAppSesionOK:
    """Verifica que el router WhatsApp maneja 'APT SESION OK' correctamente."""

    def _make_router(self, tmp_path, apt_agent=None):
        from src.agents.whatsapp_commands import WhatsAppCommandRouter
        from tests.conftest import FakeCredentialManager, TestDatabase

        creds = FakeCredentialManager()
        db = TestDatabase(path=tmp_path / "test.db", credentials=creds)
        db.initialize_schema()
        try:
            db.crear_usuario(
                telefono="50600000001", nombre="Admin", rol="admin", actor="test"
            )
        except Exception:
            pass

        replies: list[tuple[str, str]] = []
        drive = MagicMock()

        router = WhatsAppCommandRouter(
            db=db,
            credentials=creds,
            drive_agent=drive,
            reply_fn=lambda phone, msg: replies.append((phone, msg)),
            apt_agent=apt_agent,
        )
        return router, replies

    def test_apt_sesion_ok_llama_confirmar(self, tmp_path):
        """'APT SESION OK' llama apt_agent.confirmar_sesion_desde_whatsapp()."""
        agent = _make_agent(tmp_path)
        router, replies = self._make_router(tmp_path, apt_agent=agent)

        router.handle(sender_phone="50600000001", text="APT SESION OK")

        # Evento debe estar activado
        assert agent._sesion_event.is_set()
        # Debe haber enviado un mensaje de confirmación
        assert len(replies) == 1
        assert "verificando" in replies[0][1].lower() or "señal" in replies[0][1].lower()

    def test_apt_sesion_ok_sin_agent_da_error(self, tmp_path):
        """'APT SESION OK' sin apt_agent disponible responde con error."""
        router, replies = self._make_router(tmp_path, apt_agent=None)
        router.handle(sender_phone="50600000001", text="APT SESION OK")
        assert len(replies) == 1
        assert "❌" in replies[0][1]

    def test_apt_sesion_ok_no_confunde_con_apt_sesion(self, tmp_path):
        """'APT SESION' y 'APT SESION OK' son comandos distintos."""
        agent = _make_agent(tmp_path)
        router, replies = self._make_router(tmp_path, apt_agent=agent)

        # 'APT SESION' abre el navegador (lo mockeamos)
        with patch.object(agent, "iniciar_sesion_manual"):
            router.handle(sender_phone="50600000001", text="APT SESION")

        # El evento NO debe estar activado (fue APT SESION, no APT SESION OK)
        assert not agent._sesion_event.is_set()
