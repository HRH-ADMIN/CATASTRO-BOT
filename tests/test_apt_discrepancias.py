"""Tests del módulo apt_discrepancia_handler — notificación al topógrafo
+ admins cuando hay errores de registro detectados durante el llenado APT.
"""
from __future__ import annotations
import json
from unittest.mock import MagicMock

import pytest

pytest.importorskip("win32cred", reason="solo Windows")

from src.agents.apt_discrepancia_handler import (  # noqa: E402
    TIPO_RNP_TSE_MISMATCH,
    TIPO_REGISTRO_INCOMPLETO,
    TIPO_PROTOCOLO_NO_ACTIVO,
    crear_discrepancia_rnp_tse,
    crear_discrepancia_registro_incompleto,
    procesar_discrepancias,
    resolver_telefono_topografo,
)
from src.agents.apt_agent import validar_formato_cedula  # noqa: E402


class TestValidarFormatoCedula:
    def test_fisica_valida(self):
        ok, _ = validar_formato_cedula("2-0310-0121", "1")
        assert ok is True

    def test_fisica_sin_guiones_invalida(self):
        ok, motivo = validar_formato_cedula("203100121", "1")
        assert ok is False
        assert "FÍSICA" in motivo

    def test_juridica_valida(self):
        ok, _ = validar_formato_cedula("3-101-123456", "2")
        assert ok is True

    def test_juridica_incompleta_invalida(self):
        """Caso real RDF-2026-003 — registro muestra '3-101-' sin dígitos."""
        ok, motivo = validar_formato_cedula("3-101-", "2")
        assert ok is False
        assert "JURÍDICA incompleta" in motivo
        assert "3-101-" in motivo

    def test_juridica_dígitos_de_más_invalida(self):
        ok, _ = validar_formato_cedula("3-101-1234567", "2")
        assert ok is False

    def test_vacia(self):
        ok, motivo = validar_formato_cedula("", "1")
        assert ok is False
        assert "vacía" in motivo


class TestCrearDiscrepancias:
    def test_rnp_tse_mismatch_tiene_tipo_correcto(self):
        d = crear_discrepancia_rnp_tse(
            contexto="titular#1", cedula="2-0440-0388",
            tse_nombre="AMALIA QUESADA", registro_nombre="GRACE ALVAREZ",
        )
        assert d["tipo"] == TIPO_RNP_TSE_MISMATCH
        assert d["cedula"] == "2-0440-0388"
        assert "AMALIA" in d["descripcion"]
        assert "GRACE" in d["descripcion"]
        assert "ts" in d

    def test_registro_incompleto_tiene_tipo_correcto(self):
        d = crear_discrepancia_registro_incompleto(
            contexto="propietario", campo="cedula", valor="3-101-",
        )
        assert d["tipo"] == TIPO_REGISTRO_INCOMPLETO
        assert d["valor"] == "3-101-"
        assert d["campo"] == "cedula"
        assert "ts" in d


class TestProcesarDiscrepancias:
    def _mock_db(self, exp_meta: dict | None = None):
        db = MagicMock()
        meta_json = json.dumps(exp_meta or {})
        db.obtener_expediente.return_value = {
            "id": "abc-123",
            "numero_expediente": "RDF-2026-003",
            "metadata_json": meta_json,
            "nombre_topografo": "ROJAS HERRERA LUIS ALONSO",
            "cedula_topografo": "0205300432",
        }
        return db

    def test_lista_vacia_no_hace_nada(self):
        db = self._mock_db()
        res = procesar_discrepancias(
            db=db, expediente_id="abc", numero_expediente="X",
            discrepancias=[],
        )
        assert res["cantidad"] == 0
        assert res["notificado_topografo"] is False
        db.actualizar_metadata.assert_not_called()

    def test_persiste_en_metadata(self):
        db = self._mock_db()
        d = crear_discrepancia_rnp_tse(
            contexto="propietario", cedula="2-0440-0388",
            tse_nombre="AMALIA", registro_nombre="GRACE",
        )
        res = procesar_discrepancias(
            db=db, expediente_id="abc", numero_expediente="RDF-2026-003",
            discrepancias=[d],
        )
        assert res["cantidad"] == 1
        db.actualizar_metadata.assert_called_once()
        args = db.actualizar_metadata.call_args
        assert "apt_discrepancias_rnp" in args[0][1]
        assert len(args[0][1]["apt_discrepancias_rnp"]) == 1

    def test_acumula_con_discrepancias_previas(self):
        previa = {"tipo": TIPO_RNP_TSE_MISMATCH, "cedula": "1-0000-0001",
                  "tse_nombre": "X", "registro_nombre": "Y"}
        db = self._mock_db({"apt_discrepancias_rnp": [previa]})
        nueva = crear_discrepancia_registro_incompleto(
            contexto="propietario", campo="cedula", valor="3-101-",
        )
        procesar_discrepancias(
            db=db, expediente_id="abc", numero_expediente="X",
            discrepancias=[nueva],
        )
        args = db.actualizar_metadata.call_args
        guardadas = args[0][1]["apt_discrepancias_rnp"]
        assert len(guardadas) == 2  # 1 previa + 1 nueva

    def test_notifica_topografo_y_admins(self):
        db = self._mock_db()
        send = MagicMock()
        d = crear_discrepancia_registro_incompleto(
            contexto="propietario", campo="cedula", valor="3-101-",
        )
        res = procesar_discrepancias(
            db=db, expediente_id="abc", numero_expediente="RDF-2026-003",
            discrepancias=[d],
            whatsapp_send_fn=send,
            topografo_phone="+50688887310",
            admins_phones=["+50611112222", "+50633334444"],
        )
        assert res["notificado_topografo"] is True
        assert res["notificados_admin"] == 2
        assert send.call_count == 3  # 1 topo + 2 admin

    def test_no_duplica_si_topografo_es_admin(self):
        db = self._mock_db()
        send = MagicMock()
        d = crear_discrepancia_registro_incompleto(
            contexto="propietario", campo="cedula", valor="3-101-",
        )
        res = procesar_discrepancias(
            db=db, expediente_id="abc", numero_expediente="X",
            discrepancias=[d],
            whatsapp_send_fn=send,
            topografo_phone="+50688887310",
            admins_phones=["+50688887310", "+50611112222"],
        )
        assert res["notificado_topografo"] is True
        # topógrafo no se duplica aunque esté también en admins
        assert res["notificados_admin"] == 1
        assert send.call_count == 2

    def test_mensaje_agrupa_por_tipo(self):
        """El mensaje debe tener secciones separadas: RNP/TSE y registro incompleto."""
        db = self._mock_db()
        send = MagicMock()
        ds = [
            crear_discrepancia_rnp_tse(
                contexto="titular#1", cedula="2-0440-0388",
                tse_nombre="AMALIA QUESADA", registro_nombre="GRACE ALVAREZ",
            ),
            crear_discrepancia_registro_incompleto(
                contexto="propietario", campo="cedula", valor="3-101-",
            ),
        ]
        res = procesar_discrepancias(
            db=db, expediente_id="abc", numero_expediente="RDF-2026-003",
            discrepancias=ds, whatsapp_send_fn=send,
            topografo_phone="+50688887310",
        )
        m = res["mensaje"]
        assert "Cédulas con nombre distinto en TSE" in m
        assert "Datos incompletos en el registro" in m
        assert "AMALIA" in m and "GRACE" in m
        assert "3-101-" in m

    def test_falla_silencioso_si_send_fn_lanza(self):
        db = self._mock_db()
        send = MagicMock(side_effect=Exception("WhatsApp caído"))
        d = crear_discrepancia_registro_incompleto(
            contexto="propietario", campo="cedula", valor="3-101-",
        )
        # No debe propagar excepción
        res = procesar_discrepancias(
            db=db, expediente_id="abc", numero_expediente="X",
            discrepancias=[d], whatsapp_send_fn=send,
            topografo_phone="+50688887310", admins_phones=["+50611112222"],
        )
        # Pero registró que no se notificó
        assert res["notificado_topografo"] is False
        assert res["notificados_admin"] == 0


class TestResolverTelefonoTopografo:
    def test_match_por_cedula(self):
        db = MagicMock()
        db.listar_usuarios.return_value = [
            {"nombre": "OTRO USUARIO", "cedula": "9-9999-9999", "telefono": "+50699999999"},
            {"nombre": "ROJAS HERRERA LUIS ALONSO", "cedula": "0205300432",
             "telefono": "+50688887310"},
        ]
        exp = {"nombre_topografo": "ROJAS HERRERA LUIS ALONSO",
               "cedula_topografo": "0205300432"}
        assert resolver_telefono_topografo(db, exp) == "+50688887310"

    def test_match_por_nombre_case_insensitive(self):
        db = MagicMock()
        db.listar_usuarios.return_value = [
            {"nombre": "rojas herrera luis alonso", "cedula": "",
             "telefono": "+50688887310"},
        ]
        exp = {"nombre_topografo": "ROJAS HERRERA LUIS ALONSO", "cedula_topografo": ""}
        assert resolver_telefono_topografo(db, exp) == "+50688887310"

    def test_no_match_devuelve_none(self):
        db = MagicMock()
        db.listar_usuarios.return_value = [
            {"nombre": "OTRO", "cedula": "9-9999-9999", "telefono": "+50699999999"},
        ]
        exp = {"nombre_topografo": "INEXISTENTE", "cedula_topografo": ""}
        assert resolver_telefono_topografo(db, exp) is None

    def test_db_falla_devuelve_none(self):
        db = MagicMock()
        db.listar_usuarios.side_effect = Exception("DB down")
        exp = {"nombre_topografo": "X", "cedula_topografo": "Y"}
        assert resolver_telefono_topografo(db, exp) is None

    def test_expediente_vacio(self):
        assert resolver_telefono_topografo(MagicMock(), None) is None
        assert resolver_telefono_topografo(MagicMock(), {}) is None


class TestProtocoloDistintoAlActivo:
    """Cuando el seed declara un protocolo distinto al último del dropdown
    (que es el activo del año en curso), el bot debe registrar discrepancia
    para avisar al operador.
    """

    def _make_agent_with_protocolo_check(self, tmp_path):
        import pytest as _pt
        _pt.importorskip("playwright", reason="playwright requerido")
        from tests.conftest import FakeCredentialManager, TestDatabase
        from src.agents.apt_agent import APTAgent
        creds = FakeCredentialManager()
        db = TestDatabase(path=tmp_path / "t.db", credentials=creds)
        db.initialize_schema()
        return APTAgent(db, creds)

    def _mock_page(self, protocolo_activo_dropdown: str | None = "24162"):
        """Mock page con evaluate() que devuelve el protocolo activo del
        dropdown (último option) cuando se le pregunta — y True para todo
        lo demás. Si protocolo_activo_dropdown=None, simula no dropdown
        (la lectura falla y cae al fallback de settings).
        """
        page = MagicMock()
        locator = MagicMock()
        locator.count.return_value = 1
        locator.first = locator
        locator.input_value.return_value = ""
        locator.is_disabled.return_value = False
        locator.is_checked.return_value = False
        locator.evaluate.return_value = True
        page.locator.return_value = locator
        def _eval(js, *args, **kw):
            if "dllprotocolo" in js and "options" in js:
                return protocolo_activo_dropdown or ""
            return True
        page.evaluate.side_effect = _eval
        return page

    def test_protocolo_igual_al_activo_no_registra(self, tmp_path):
        agent = self._make_agent_with_protocolo_check(tmp_path)
        from unittest.mock import patch
        with patch("src.agents.apt_agent.time.sleep"):
            agent._llenar_seccion_protocolo(
                self._mock_page(protocolo_activo_dropdown="24162"),
                {"numero": "24162", "folio": "100", "tipo_proyecto_modal": "27"},
            )
        assert not any(d.get("tipo") == TIPO_PROTOCOLO_NO_ACTIVO
                       for d in agent.discrepancias_rnp)

    def test_protocolo_distinto_al_activo_registra(self, tmp_path):
        agent = self._make_agent_with_protocolo_check(tmp_path)
        from unittest.mock import patch
        with patch("src.agents.apt_agent.time.sleep"):
            agent._llenar_seccion_protocolo(
                self._mock_page(protocolo_activo_dropdown="24162"),
                {"numero": "19245", "folio": "082", "tipo_proyecto_modal": "27"},
            )
        protos = [d for d in agent.discrepancias_rnp
                  if d.get("tipo") == TIPO_PROTOCOLO_NO_ACTIVO]
        assert len(protos) == 1
        assert protos[0]["valor"] == "19245"
        assert protos[0]["valor_esperado"] == "24162"
        assert "contrato anterior" in protos[0]["descripcion"]
        assert "último del dropdown" in protos[0]["descripcion"]

    def test_per_user_protocolo_prevalece_sobre_dropdown(self, tmp_path):
        """Multi-user: si pasamos protocolo_activo_topografo, gana sobre dropdown."""
        agent = self._make_agent_with_protocolo_check(tmp_path)
        from unittest.mock import patch
        # Dropdown dice "24162", pero el topógrafo dice "20000" → 20000 wins
        with patch("src.agents.apt_agent.time.sleep"):
            agent._llenar_seccion_protocolo(
                self._mock_page(protocolo_activo_dropdown="24162"),
                {"numero": "19245", "folio": "082", "tipo_proyecto_modal": "27"},
                protocolo_activo_topografo="20000",
            )
        protos = [d for d in agent.discrepancias_rnp
                  if d.get("tipo") == TIPO_PROTOCOLO_NO_ACTIVO]
        assert len(protos) == 1
        # Esperaba el per-user "20000", no el dropdown "24162"
        assert protos[0]["valor_esperado"] == "20000"

    def test_fallback_a_settings_cuando_dropdown_no_lee(self, tmp_path, monkeypatch):
        """Si la lectura del dropdown falla (devuelve ''), cae al setting."""
        monkeypatch.setattr("config.settings.PROTOCOLO_ACTIVO_TOPOGRAFO", "24162")
        agent = self._make_agent_with_protocolo_check(tmp_path)
        from unittest.mock import patch
        with patch("src.agents.apt_agent.time.sleep"):
            agent._llenar_seccion_protocolo(
                self._mock_page(protocolo_activo_dropdown=None),  # simula fallo
                {"numero": "19245", "folio": "082", "tipo_proyecto_modal": "27"},
            )
        protos = [d for d in agent.discrepancias_rnp
                  if d.get("tipo") == TIPO_PROTOCOLO_NO_ACTIVO]
        assert len(protos) == 1
        assert protos[0]["valor_esperado"] == "24162"

    def test_ningun_protocolo_activo_no_chequea(self, tmp_path, monkeypatch):
        """Dropdown vacío + setting vacío → no se hace la detección."""
        monkeypatch.setattr("config.settings.PROTOCOLO_ACTIVO_TOPOGRAFO", "")
        agent = self._make_agent_with_protocolo_check(tmp_path)
        from unittest.mock import patch
        with patch("src.agents.apt_agent.time.sleep"):
            agent._llenar_seccion_protocolo(
                self._mock_page(protocolo_activo_dropdown=None),
                {"numero": "19245", "folio": "082", "tipo_proyecto_modal": "27"},
            )
        assert agent.discrepancias_rnp == []


class TestAceptarAvisoProtocoloInactivo:
    """Cuando APT muestra 'Atención: El protocolo seleccionado no está activo',
    el bot debe aceptar automáticamente (es esperado en continuaciones).
    """

    def _make_agent_and_page(self, tmp_path, modal_data):
        from tests.conftest import FakeCredentialManager, TestDatabase
        from src.agents.apt_agent import APTAgent
        creds = FakeCredentialManager()
        db = TestDatabase(path=tmp_path / "t.db", credentials=creds)
        db.initialize_schema()
        agent = APTAgent(db, creds)
        page = MagicMock()
        def _eval(js, *args, **kw):
            if "swal2-popup" in js and "title" in js:
                return modal_data
            return True
        page.evaluate.side_effect = _eval
        return agent, page

    def test_dismiss_aviso_protocolo_inactivo(self, tmp_path):
        agent, page = self._make_agent_and_page(tmp_path, {
            "title": "Atención",
            "html": "El protocolo seleccionado no está activo",
        })
        from unittest.mock import patch
        with patch("src.agents.apt_agent.time.sleep"):
            ok = agent._aceptar_modal_swal_si_es_aviso_protocolo(page)
        assert ok is True

    def test_no_dismiss_otros_modales(self, tmp_path):
        """Modales que NO son el aviso esperado se dejan intactos."""
        agent, page = self._make_agent_and_page(tmp_path, {
            "title": "Error",
            "html": "Algo salió mal con la cédula",
        })
        from unittest.mock import patch
        with patch("src.agents.apt_agent.time.sleep"):
            ok = agent._aceptar_modal_swal_si_es_aviso_protocolo(page)
        assert ok is False

    def test_sin_modal_no_hace_nada(self, tmp_path):
        agent, page = self._make_agent_and_page(tmp_path, None)
        from unittest.mock import patch
        with patch("src.agents.apt_agent.time.sleep"):
            ok = agent._aceptar_modal_swal_si_es_aviso_protocolo(page)
        assert ok is False


class TestMensajeProtocolo:
    def test_mensaje_incluye_proximo_paso(self):
        from src.agents.apt_discrepancia_handler import _formatear_mensaje
        ds = [{
            "tipo": TIPO_PROTOCOLO_NO_ACTIVO,
            "contexto": "protocolo",
            "campo": "protocolo.numero",
            "valor": "19245",
            "valor_esperado": "24162",
            "descripcion": "Protocolo declarado (19245) NO coincide con activo (24162).",
        }]
        msg = _formatear_mensaje("ROVUELT (RDF-2026-003)", ds)
        assert "Protocolo diferente al activo" in msg
        assert "19245" in msg
        assert "24162" in msg
        assert "honorarios=0" in msg or "continuación" in msg.lower()
