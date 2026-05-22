"""Tests para handlers de BaseWorkflow añadidos en Semana 5.

Cubre:
  - _h_enteros_pagados (2 sub-pasos: subir_apt + firma_digital_r1)
  - _h_presentado_apt_r1 (poll consultar_estado_r1)
  - _h_apt_r1_respondio (descarga minuta → APT_R1_ANALIZADO)
  - _h_listo_apt_r2 (2 sub-pasos: subir_apt_r2 + firma_digital_r2)
  - _h_presentado_apt_r2 (poll consultar_estado_r2 → INSCRITO)

No se usa Playwright real — APTAgent está completamente mockeado.
Los handlers muni y minuta también están mockeados pues no intervienen
en estos pasos.
"""
from __future__ import annotations

import contextlib
import json
import re
import secrets
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("win32cred", reason="pywin32 requerido (solo Windows)")

from src.core.database import Database  # noqa: E402
from src.core.exceptions import CredentialNotFoundError  # noqa: E402
from src.models.estado import Estado  # noqa: E402
from src.models.plano import TipoPlano  # noqa: E402
from src.workflows.segregacion import SegregacionWorkflow  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# TestDatabase (sin SQLCipher — idéntica al conftest global)
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


class FakeCredentialManager:
    def __init__(self):
        self._db_key = secrets.token_bytes(32)
        self._operators: set[str] = set()

    def get_or_create_db_key(self) -> bytes:
        return self._db_key

    def get_db_key(self) -> bytes:
        return self._db_key

    def get_operators(self) -> set[str]:
        return set(self._operators)

    def is_operator(self, phone: str) -> bool:
        return False

    def get_apt(self):
        return ("user", "pass")


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path):
    cm = FakeCredentialManager()
    database = TestDatabase(path=tmp_path / "test.db", credentials=cm)
    database.initialize_schema()
    return database


@pytest.fixture
def mock_agents():
    """Agentes completamente mockeados — cero IO real."""
    apt = MagicMock()
    drive = MagicMock()
    whatsapp = MagicMock()
    muni = MagicMock()
    minuta = MagicMock()
    return {
        "apt": apt,
        "drive": drive,
        "whatsapp": whatsapp,
        "muni": muni,
        "minuta": minuta,
    }


@pytest.fixture
def wf(db, mock_agents):
    return SegregacionWorkflow(db, mock_agents)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _crear_exp(db, estado: Estado, *, meta: dict | None = None) -> str:
    """Crea un expediente tipo segregacion y lo lleva al estado indicado."""
    eid = db.crear_expediente(
        numero_expediente="EXP-TEST-0001",
        tipo_plano=TipoPlano.SEGREGACION.value,
        nombre_topografo="Topo Test",
        telefono_cliente="50688880001",
        nombre_cliente="Cliente Test",
        cedula_topografo="1-0001-0001",
        metadata=meta or {},
        actor="test",
    )
    if estado != Estado.RECIBIDO:
        db.cambiar_estado(eid, estado.value, actor="test", detalles="setup test")
    return eid


def _confirmar(db, eid: str, tipo_accion: str) -> None:
    """Crea una acción pendiente y la resuelve como confirmada."""
    aid = db.crear_accion_pendiente(
        expediente_id=eid,
        tipo_accion=tipo_accion,
        descripcion=f"confirmacion de {tipo_accion} para test",
        actor="test",
    )
    db.resolver_accion(aid, "confirmada", whatsapp_response="SI", actor="test")


def _fake_archivo(db, eid: str, *, fase: str = "campo",
                  tipo: str = "anverso", ruta: str = "/fake/anverso.pdf") -> None:
    """Registra un archivo falso en la BD."""
    db.registrar_archivo(
        expediente_id=eid,
        fase=fase,
        tipo_archivo=tipo,
        nombre_original="anverso.pdf",
        sha256="a" * 64,
        ruta_local=ruta,
        tamano_bytes=100_000,
        actor="test",
    )


def _estado(db, eid: str) -> str:
    return db.obtener_expediente(eid)["estado_actual"]


def _meta(db, eid: str) -> dict:
    return json.loads(db.obtener_expediente(eid).get("metadata_json") or "{}")


# ─────────────────────────────────────────────────────────────────────────────
# _h_enteros_pagados
# ─────────────────────────────────────────────────────────────────────────────

class TestHEnterosPagados:

    def test_sin_confirmacion_solicita_y_queda(self, db, wf, mock_agents):
        """Sin acción previa → solicita confirmación 'subir_apt' y se queda."""
        eid = _crear_exp(db, Estado.ENTEROS_PAGADOS, meta={"apt_tramite": "9999001"})
        _fake_archivo(db, eid)

        wf.avanzar(eid)

        assert _estado(db, eid) == Estado.ENTEROS_PAGADOS.value
        mock_agents["whatsapp"].solicitar_confirmacion.assert_called_once()
        args = mock_agents["whatsapp"].solicitar_confirmacion.call_args
        assert args.kwargs.get("tipo_accion") == "subir_apt"

    def test_subir_apt_confirmado_llama_presentar_r1(self, db, wf, mock_agents, tmp_path):
        """'subir_apt' confirmada → llama presentar_r1 con el archivo."""
        eid = _crear_exp(db, Estado.ENTEROS_PAGADOS, meta={"apt_tramite": "9999001"})
        anverso = tmp_path / "anverso.pdf"
        anverso.write_bytes(b"fake pdf")
        _fake_archivo(db, eid, ruta=str(anverso))
        _confirmar(db, eid, "subir_apt")

        mock_agents["apt"].presentar_r1.return_value = {
            "tramite": "9999001",
            "archivos_subidos": ["anverso.pdf"],
            "errores": [],
            "listo_para_fd": True,
        }

        wf.avanzar(eid)

        mock_agents["apt"].presentar_r1.assert_called_once()
        # metadata guardada
        meta = _meta(db, eid)
        assert meta["apt_r1_archivos_subidos"] is True
        assert meta["apt_tramite"] == "9999001"
        # aún en ENTEROS_PAGADOS (esperando FD)
        assert _estado(db, eid) == Estado.ENTEROS_PAGADOS.value

    def test_subir_apt_confirmado_apt_error_queda(self, db, wf, mock_agents, tmp_path):
        """presentar_r1() falla → queda en ENTEROS_PAGADOS, notifica topógrafo."""
        eid = _crear_exp(db, Estado.ENTEROS_PAGADOS, meta={"apt_tramite": "9999001"})
        anverso = tmp_path / "anverso.pdf"
        anverso.write_bytes(b"fake pdf")
        _fake_archivo(db, eid, ruta=str(anverso))
        _confirmar(db, eid, "subir_apt")

        mock_agents["apt"].presentar_r1.side_effect = Exception("portal down")

        wf.avanzar(eid)

        assert _estado(db, eid) == Estado.ENTEROS_PAGADOS.value
        # Hay 2 notificaciones: confirmación de pago (nueva Semana 9) + error APT
        calls = mock_agents["whatsapp"].notificar_estado.call_args_list
        assert len(calls) >= 1
        ultimo_msg = calls[-1][0][1]
        assert "Error" in ultimo_msg

    def test_subir_apt_confirmado_listo_para_fd_false_queda(self, db, wf, mock_agents, tmp_path):
        """listo_para_fd=False (errores al subir) → queda en ENTEROS_PAGADOS."""
        eid = _crear_exp(db, Estado.ENTEROS_PAGADOS)
        anverso = tmp_path / "anverso.pdf"
        anverso.write_bytes(b"fake pdf")
        _fake_archivo(db, eid, ruta=str(anverso))
        _confirmar(db, eid, "subir_apt")

        mock_agents["apt"].presentar_r1.return_value = {
            "tramite": "",
            "archivos_subidos": [],
            "errores": ["archivo demasiado grande"],
            "listo_para_fd": False,
        }

        wf.avanzar(eid)

        assert _estado(db, eid) == Estado.ENTEROS_PAGADOS.value
        # Hay 2 notificaciones: confirmación de pago (Semana 9) + error de subida
        calls = mock_agents["whatsapp"].notificar_estado.call_args_list
        assert len(calls) >= 1

    def test_archivos_subidos_sin_fd_solicita_firma(self, db, wf, mock_agents):
        """apt_r1_archivos_subidos=True pero sin firma_digital_r1 → solicita firma."""
        eid = _crear_exp(db, Estado.ENTEROS_PAGADOS,
                         meta={"apt_r1_archivos_subidos": True, "apt_tramite": "9999001"})

        wf.avanzar(eid)

        assert _estado(db, eid) == Estado.ENTEROS_PAGADOS.value
        mock_agents["apt"].presentar_r1.assert_not_called()
        mock_agents["whatsapp"].solicitar_confirmacion.assert_called_once()
        args = mock_agents["whatsapp"].solicitar_confirmacion.call_args
        assert args.kwargs.get("tipo_accion") == "firma_digital_r1"

    def test_archivos_subidos_fd_confirmado_avanza(self, db, wf, mock_agents):
        """apt_r1_archivos_subidos=True + firma_digital_r1 confirmada → PRESENTADO_APT_R1."""
        eid = _crear_exp(db, Estado.ENTEROS_PAGADOS,
                         meta={"apt_r1_archivos_subidos": True, "apt_tramite": "9999001"})
        _confirmar(db, eid, "firma_digital_r1")

        wf.avanzar(eid)

        assert _estado(db, eid) == Estado.PRESENTADO_APT_R1.value
        mock_agents["apt"].presentar_r1.assert_not_called()
        mock_agents["whatsapp"].notificar_estado.assert_called_once()

    def test_sin_archivos_lanza_workflow_error(self, db, wf, mock_agents):
        """Sin archivos en BD → WorkflowError (no queda bloqueado silenciosamente)."""
        from src.core.exceptions import WorkflowError

        eid = _crear_exp(db, Estado.ENTEROS_PAGADOS, meta={"apt_tramite": "9999001"})
        _confirmar(db, eid, "subir_apt")

        with pytest.raises(WorkflowError, match="no hay archivos"):
            wf.avanzar(eid)

    def test_resubmision_solicita_confirmacion_con_recordatorio_anverso(
        self, db, wf, mock_agents
    ):
        """Con apt_correcciones_count > 0, la solicitud de subir_apt menciona
        el anverso corregido en el mensaje al topógrafo."""
        eid = _crear_exp(
            db, Estado.ENTEROS_PAGADOS,
            meta={"apt_correcciones_count": 1, "apt_tramite": "9999001"},
        )
        _fake_archivo(db, eid)

        wf.avanzar(eid)

        assert _estado(db, eid) == Estado.ENTEROS_PAGADOS.value
        mock_agents["whatsapp"].solicitar_confirmacion.assert_called_once()
        args = mock_agents["whatsapp"].solicitar_confirmacion.call_args
        descripcion = args.kwargs.get("descripcion", "")
        assert "anverso" in descripcion.lower(), \
            "debe mencionar anverso en solicitud de re-subida"

    def test_resubmision_fd_descripcion_menciona_resubmision(
        self, db, wf, mock_agents
    ):
        """Con apt_correcciones_count > 0, la solicitud de firma_digital_r1
        aclara que es una resubmisión."""
        eid = _crear_exp(
            db, Estado.ENTEROS_PAGADOS,
            meta={
                "apt_r1_archivos_subidos": True,
                "apt_tramite": "9999002",
                "apt_correcciones_count": 2,
            },
        )

        wf.avanzar(eid)

        assert _estado(db, eid) == Estado.ENTEROS_PAGADOS.value
        mock_agents["whatsapp"].solicitar_confirmacion.assert_called_once()
        args = mock_agents["whatsapp"].solicitar_confirmacion.call_args
        descripcion = args.kwargs.get("descripcion", "")
        assert "resubmisión" in descripcion.lower() or "resubmision" in descripcion.lower()


# ─────────────────────────────────────────────────────────────────────────────
# _h_presentado_apt_r1
# ─────────────────────────────────────────────────────────────────────────────

class TestHPresentadoAptR1:

    def test_estado_pendiente_queda(self, db, wf, mock_agents):
        """consultar_estado_r1 devuelve 'pendiente' → queda en PRESENTADO_APT_R1."""
        eid = _crear_exp(db, Estado.PRESENTADO_APT_R1)
        mock_agents["apt"].consultar_estado_r1.return_value = "pendiente"

        wf.avanzar(eid)

        assert _estado(db, eid) == Estado.PRESENTADO_APT_R1.value

    def test_estado_none_queda(self, db, wf, mock_agents):
        """consultar_estado_r1 devuelve None → queda en PRESENTADO_APT_R1."""
        eid = _crear_exp(db, Estado.PRESENTADO_APT_R1)
        mock_agents["apt"].consultar_estado_r1.return_value = None

        wf.avanzar(eid)

        assert _estado(db, eid) == Estado.PRESENTADO_APT_R1.value

    def test_estado_respondido_avanza(self, db, wf, mock_agents):
        """consultar_estado_r1 devuelve 'respondido' → APT_R1_RESPONDIO."""
        eid = _crear_exp(db, Estado.PRESENTADO_APT_R1)
        mock_agents["apt"].consultar_estado_r1.return_value = "respondido"

        wf.avanzar(eid)

        assert _estado(db, eid) == Estado.APT_R1_RESPONDIO.value

    def test_estado_edicion_queda(self, db, wf, mock_agents):
        """'edicion' no es respondido → queda en PRESENTADO_APT_R1."""
        eid = _crear_exp(db, Estado.PRESENTADO_APT_R1)
        mock_agents["apt"].consultar_estado_r1.return_value = "edicion"

        wf.avanzar(eid)

        assert _estado(db, eid) == Estado.PRESENTADO_APT_R1.value


# ─────────────────────────────────────────────────────────────────────────────
# _h_apt_r1_respondio
# ─────────────────────────────────────────────────────────────────────────────

class TestHAptR1Respondio:

    def test_descarga_archivos_y_avanza(self, db, wf, mock_agents, tmp_path):
        """descargar_archivos_r1 devuelve paths → guarda en drive, avanza."""
        eid = _crear_exp(db, Estado.APT_R1_RESPONDIO)

        carpeta = tmp_path / "apt_r1"
        carpeta.mkdir()
        minuta = carpeta / "minuta.pdf"
        minuta.write_bytes(b"minuta")
        correcciones = carpeta / "correcciones.pdf"
        correcciones.write_bytes(b"correcciones")

        mock_agents["drive"].folder_for.return_value = carpeta
        mock_agents["apt"].descargar_archivos_r1.return_value = [minuta, correcciones]
        mock_agents["drive"].guardar_archivo.return_value = {
            "id": "f1", "sha256": "a" * 64, "ruta_local": str(minuta),
            "drive_file_id": None,
        }

        wf.avanzar(eid)

        mock_agents["apt"].descargar_archivos_r1.assert_called_once_with(
            eid, carpeta
        )
        assert mock_agents["drive"].guardar_archivo.call_count == 2
        assert _estado(db, eid) == Estado.APT_R1_ANALIZADO.value

    def test_sin_archivos_igual_avanza(self, db, wf, mock_agents, tmp_path):
        """descargar_archivos_r1 devuelve [] → avanza igual (0 archivos)."""
        eid = _crear_exp(db, Estado.APT_R1_RESPONDIO)

        carpeta = tmp_path / "apt_r1"
        carpeta.mkdir()
        mock_agents["drive"].folder_for.return_value = carpeta
        mock_agents["apt"].descargar_archivos_r1.return_value = []

        wf.avanzar(eid)

        mock_agents["drive"].guardar_archivo.assert_not_called()
        assert _estado(db, eid) == Estado.APT_R1_ANALIZADO.value

    def test_tipo_minuta_inferido(self, db, wf, mock_agents, tmp_path):
        """_inferir_tipo clasifica archivos por nombre."""
        eid = _crear_exp(db, Estado.APT_R1_RESPONDIO)

        carpeta = tmp_path / "apt_r1"
        carpeta.mkdir()
        minuta = carpeta / "respuesta_minuta.pdf"
        minuta.write_bytes(b"m")
        img_minuta = carpeta / "imagenminuta_1234.pdf"
        img_minuta.write_bytes(b"i")

        mock_agents["drive"].folder_for.return_value = carpeta
        mock_agents["apt"].descargar_archivos_r1.return_value = [minuta, img_minuta]
        mock_agents["drive"].guardar_archivo.return_value = {
            "id": "f1", "sha256": "a" * 64, "ruta_local": str(minuta),
            "drive_file_id": None,
        }

        wf.avanzar(eid)

        calls = mock_agents["drive"].guardar_archivo.call_args_list
        tipos = [c.kwargs["tipo_archivo"] for c in calls]
        assert "minuta" in tipos
        assert "imagen_minuta" in tipos


# ─────────────────────────────────────────────────────────────────────────────
# _h_listo_apt_r2
# ─────────────────────────────────────────────────────────────────────────────

class TestHListoAptR2:

    def test_sin_confirmacion_solicita_y_queda(self, db, wf, mock_agents):
        eid = _crear_exp(db, Estado.LISTO_APT_R2)
        _fake_archivo(db, eid, fase="apt_ronda1")

        wf.avanzar(eid)

        assert _estado(db, eid) == Estado.LISTO_APT_R2.value
        mock_agents["whatsapp"].solicitar_confirmacion.assert_called_once()
        args = mock_agents["whatsapp"].solicitar_confirmacion.call_args
        assert args.kwargs.get("tipo_accion") == "subir_apt_r2"

    def test_subir_apt_r2_confirmado_llama_presentar_r2(self, db, wf, mock_agents, tmp_path):
        eid = _crear_exp(db, Estado.LISTO_APT_R2)
        anverso = tmp_path / "anverso.pdf"
        anverso.write_bytes(b"fake pdf")
        _fake_archivo(db, eid, fase="apt_ronda1", ruta=str(anverso))
        _confirmar(db, eid, "subir_apt_r2")

        mock_agents["apt"].presentar_r2.return_value = {
            "tramite": "9999002",
            "archivos_subidos": ["anverso.pdf"],
            "errores": [],
            "listo_para_fd": True,
        }

        wf.avanzar(eid)

        mock_agents["apt"].presentar_r2.assert_called_once()
        meta = _meta(db, eid)
        assert meta["apt_r2_archivos_subidos"] is True
        assert _estado(db, eid) == Estado.LISTO_APT_R2.value  # esperando FD

    def test_archivos_subidos_fd_confirmado_avanza(self, db, wf, mock_agents):
        eid = _crear_exp(db, Estado.LISTO_APT_R2,
                         meta={"apt_r2_archivos_subidos": True, "apt_tramite": "9999002"})
        _confirmar(db, eid, "firma_digital_r2")

        wf.avanzar(eid)

        assert _estado(db, eid) == Estado.PRESENTADO_APT_R2.value
        mock_agents["apt"].presentar_r2.assert_not_called()

    def test_presentar_r2_error_queda(self, db, wf, mock_agents, tmp_path):
        eid = _crear_exp(db, Estado.LISTO_APT_R2)
        anverso = tmp_path / "anverso.pdf"
        anverso.write_bytes(b"x")
        _fake_archivo(db, eid, fase="apt_ronda1", ruta=str(anverso))
        _confirmar(db, eid, "subir_apt_r2")

        mock_agents["apt"].presentar_r2.side_effect = Exception("portal caído")

        wf.avanzar(eid)

        assert _estado(db, eid) == Estado.LISTO_APT_R2.value
        mock_agents["whatsapp"].notificar_estado.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# _h_presentado_apt_r2
# ─────────────────────────────────────────────────────────────────────────────

class TestHPresentadoAptR2:

    def test_estado_pendiente_queda(self, db, wf, mock_agents):
        eid = _crear_exp(db, Estado.PRESENTADO_APT_R2)
        mock_agents["apt"].consultar_estado_r2.return_value = "pendiente"

        wf.avanzar(eid)

        assert _estado(db, eid) == Estado.PRESENTADO_APT_R2.value

    def test_estado_inscrito_avanza(self, db, wf, mock_agents):
        eid = _crear_exp(db, Estado.PRESENTADO_APT_R2)
        mock_agents["apt"].consultar_estado_r2.return_value = "inscrito"

        wf.avanzar(eid)

        assert _estado(db, eid) == Estado.INSCRITO.value

    def test_estado_none_queda(self, db, wf, mock_agents):
        eid = _crear_exp(db, Estado.PRESENTADO_APT_R2)
        mock_agents["apt"].consultar_estado_r2.return_value = None

        wf.avanzar(eid)

        assert _estado(db, eid) == Estado.PRESENTADO_APT_R2.value


# ─────────────────────────────────────────────────────────────────────────────
# Integration: avanzar() respeta halts y terminales
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# _h_listo_paquete_muni (2 sub-pasos)
# ─────────────────────────────────────────────────────────────────────────────

class TestHListoPaqueteMuni:

    def test_sin_confirmacion_solicita_enviar_formulario_muni(self, db, wf, mock_agents):
        """Sin confirmación previa → solicita 'enviar_formulario_muni' y se queda."""
        eid = _crear_exp(db, Estado.LISTO_PAQUETE_MUNI)

        wf.avanzar(eid)

        assert _estado(db, eid) == Estado.LISTO_PAQUETE_MUNI.value
        mock_agents["whatsapp"].solicitar_confirmacion.assert_called_once()
        args = mock_agents["whatsapp"].solicitar_confirmacion.call_args
        assert args.kwargs.get("tipo_accion") == "enviar_formulario_muni"

    def test_enviar_formulario_confirmado_llama_muni_y_notifica(
        self, db, wf, mock_agents
    ):
        """'enviar_formulario_muni' confirmada → llama muni.enviar_formulario,
        envía instrucciones al topógrafo y guarda muni_url_enviada."""
        eid = _crear_exp(db, Estado.LISTO_PAQUETE_MUNI)
        _confirmar(db, eid, "enviar_formulario_muni")

        mock_agents["muni"].enviar_formulario.return_value = "https://forms.google.com/test"
        mock_agents["muni"].generar_instrucciones_formulario.return_value = (
            "📋 Formulario listo\nURL: https://forms.google.com/test\nResponda SI"
        )

        wf.avanzar(eid)

        mock_agents["muni"].enviar_formulario.assert_called_once_with(eid)
        mock_agents["muni"].generar_instrucciones_formulario.assert_called_once_with(eid)
        mock_agents["whatsapp"].notificar_estado.assert_called_once()
        # queda en LISTO_PAQUETE_MUNI esperando sub-paso B
        assert _estado(db, eid) == Estado.LISTO_PAQUETE_MUNI.value
        assert _meta(db, eid).get("muni_url_enviada") is True

    def test_url_enviada_sin_completado_solicita_segundo_confirmacion(
        self, db, wf, mock_agents
    ):
        """Con muni_url_enviada=True pero sin 'formulario_muni_completado'
        → solicita segunda confirmación."""
        eid = _crear_exp(
            db, Estado.LISTO_PAQUETE_MUNI,
            meta={"muni_url_enviada": True},
        )

        wf.avanzar(eid)

        assert _estado(db, eid) == Estado.LISTO_PAQUETE_MUNI.value
        mock_agents["muni"].enviar_formulario.assert_not_called()
        mock_agents["whatsapp"].solicitar_confirmacion.assert_called_once()
        args = mock_agents["whatsapp"].solicitar_confirmacion.call_args
        assert args.kwargs.get("tipo_accion") == "formulario_muni_completado"

    def test_completado_confirmado_avanza_a_formulario_enviado(
        self, db, wf, mock_agents
    ):
        """Con muni_url_enviada=True y 'formulario_muni_completado' confirmada
        → avanza a FORMULARIO_MUNI_ENVIADO."""
        eid = _crear_exp(
            db, Estado.LISTO_PAQUETE_MUNI,
            meta={"muni_url_enviada": True},
        )
        _confirmar(db, eid, "formulario_muni_completado")

        wf.avanzar(eid)

        assert _estado(db, eid) == Estado.FORMULARIO_MUNI_ENVIADO.value
        mock_agents["muni"].enviar_formulario.assert_not_called()

    def test_error_en_muni_notifica_y_queda(self, db, wf, mock_agents):
        """muni.enviar_formulario() falla → notifica topógrafo, queda en LISTO_PAQUETE_MUNI."""
        eid = _crear_exp(db, Estado.LISTO_PAQUETE_MUNI)
        _confirmar(db, eid, "enviar_formulario_muni")

        mock_agents["muni"].enviar_formulario.side_effect = Exception("API error")

        wf.avanzar(eid)

        assert _estado(db, eid) == Estado.LISTO_PAQUETE_MUNI.value
        mock_agents["whatsapp"].notificar_estado.assert_called_once()
        notif_msg = mock_agents["whatsapp"].notificar_estado.call_args[0][1]
        assert "Error" in notif_msg


class TestAvanzarGuardas:

    def test_halt_no_avanza(self, db, wf, mock_agents):
        """Un estado halt no llama a ningún agente."""
        eid = _crear_exp(db, Estado.APT_CORRECCIONES)
        wf.avanzar(eid)
        mock_agents["apt"].consultar_estado_r1.assert_not_called()

    def test_terminal_no_avanza(self, db, wf, mock_agents):
        """Un estado terminal (ENTREGADO) no llama a ningún agente."""
        eid = _crear_exp(db, Estado.ENTREGADO)
        wf.avanzar(eid)
        mock_agents["apt"].consultar_estado_r1.assert_not_called()

    def test_tipo_incorrecto_lanza(self, db, mock_agents):
        """Workflow de un tipo no puede avanzar un expediente de otro tipo."""
        from src.core.exceptions import WorkflowError
        from src.workflows.rectificacion import RectificacionWorkflow

        wf_rect = RectificacionWorkflow(db, mock_agents)
        # el expediente es de tipo segregacion
        eid = _crear_exp(db, Estado.PRESENTADO_APT_R1)

        with pytest.raises(WorkflowError):
            wf_rect.avanzar(eid)

    def test_expediente_inexistente_lanza(self, db, wf, mock_agents):
        from src.core.exceptions import WorkflowError

        with pytest.raises(WorkflowError):
            wf.avanzar("uuid-que-no-existe")

    def test_cancelado_no_avanza(self, db, wf, mock_agents):
        """Expedientes cancelados son ignorados por el orchestrator,
        pero avanzar() puede llamarse — termina en CANCELADO sin cambio."""
        eid = _crear_exp(db, Estado.CANCELADO)
        wf.avanzar(eid)
        # CANCELADO es terminal → no hay handler
        assert _estado(db, eid) == Estado.CANCELADO.value


# ─────────────────────────────────────────────────────────────────────────────
# _h_formato_validado — pago de enteros
# ─────────────────────────────────────────────────────────────────────────────

class TestHFormatoValidado:
    """El bot notifica al topógrafo para que pague el entero en BCR.

    El operador después confirma con el comando PAGAR <exp> <numero_entero>,
    que avanza directamente a ENTEROS_PAGADOS sin pasar por el workflow.
    El workflow solo gestiona la notificación inicial y el wait.
    """

    def test_sin_accion_previa_notifica_topografo_y_queda(self, db, wf, mock_agents):
        """Sin entero.pdf → notifica una vez al topógrafo y queda en FORMATO_VALIDADO."""
        eid = _crear_exp(db, Estado.FORMATO_VALIDADO)
        wf.avanzar(eid)
        assert _estado(db, eid) == Estado.FORMATO_VALIDADO.value
        # Debe haber notificado al topógrafo sobre el archivo faltante
        mock_agents["whatsapp"].notificar_estado.assert_called_once()
        msg = mock_agents["whatsapp"].notificar_estado.call_args[0][1]
        assert "entero" in msg.lower()
        # Debe haberse marcado la notificación como enviada en metadata
        meta = _meta(db, eid)
        assert meta.get("entero_notif_enviada") is True

    def test_accion_pendiente_existente_no_re_envia(self, db, wf, mock_agents):
        """Con entero_notif_enviada=True en metadata → no re-envía notificación."""
        eid = _crear_exp(
            db, Estado.FORMATO_VALIDADO,
            meta={"entero_notif_enviada": True},
        )
        wf.avanzar(eid)
        assert _estado(db, eid) == Estado.FORMATO_VALIDADO.value
        # No re-enviar notificación (ya fue enviada)
        mock_agents["whatsapp"].notificar_estado.assert_not_called()

    def test_con_numero_entero_confirmado_avanza(self, db, wf, mock_agents):
        """Con numero_entero + entero_confirmado en metadata → avanza a ENTEROS_PAGADOS."""
        eid = _crear_exp(
            db, Estado.FORMATO_VALIDADO,
            meta={"numero_entero": "20261234", "entero_confirmado": True},
        )
        wf.avanzar(eid)
        assert _estado(db, eid) == Estado.ENTEROS_PAGADOS.value
        # No notifica (ya fue procesado por comando PAGAR)
        mock_agents["whatsapp"].solicitar_confirmacion.assert_not_called()

    def test_sin_numero_entero_aunque_confirmado_false_queda(self, db, wf, mock_agents):
        """Sin numero_entero en metadata → crea accion y notifica."""
        eid = _crear_exp(
            db, Estado.FORMATO_VALIDADO,
            meta={"entero_confirmado": False},
        )
        wf.avanzar(eid)
        assert _estado(db, eid) == Estado.FORMATO_VALIDADO.value
        mock_agents["whatsapp"].notificar_estado.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# _requiere_carta_agua — criterio por área
# ─────────────────────────────────────────────────────────────────────────────

class TestRequiereCartaAgua:

    def _exp_dict(self, tipo: str, area_m2=None, explicito=None, **kwargs) -> dict:
        # extra kwargs van directo a metadata (ej zona_regulador)
        return {
            "tipo_plano": tipo,
            "metadata_json": json.dumps(
                self._build_meta(area_m2, explicito, **kwargs)
            ),
        }

    @staticmethod
    def _build_meta(area_m2=None, explicito=None, **kwargs):
        meta: dict = {**kwargs}
        if area_m2 is not None:
            meta["area_m2"] = area_m2
        if explicito is not None:
            meta["carta_agua_requerida"] = explicito
        return meta

    def test_segregacion_sin_area_requiere(self, wf):
        # Regla actualizada 2026-05-15: sin área → OPCIONAL (operador decide).
        # _requiere_carta_agua() devuelve True solo para OBLIGATORIA.
        # Por lo tanto sin área → False (es opcional, no obligatoria).
        exp = self._exp_dict("segregacion")
        assert wf._requiere_carta_agua(exp) is False

    def test_segregacion_area_pequeña_requiere(self, wf):
        """Área < 1000 m² → OBLIGATORIA siempre."""
        exp = self._exp_dict("segregacion", area_m2=500.0)
        assert wf._requiere_carta_agua(exp) is True

    def test_segregacion_area_exacta_umbral_no_requiere(self, wf):
        """Área == 2000 m² → tramo OPCIONAL (no obligatoria)."""
        exp = self._exp_dict("segregacion", area_m2=2000.0)
        assert wf._requiere_carta_agua(exp) is False

    def test_segregacion_area_grande_no_requiere(self, wf):
        """Área > 5000 m² → NO APLICA."""
        exp = self._exp_dict("segregacion", area_m2=5001.0)
        assert wf._requiere_carta_agua(exp) is False

    def test_reunion_de_fincas_no_requiere(self, wf):
        """reunion_de_fincas nunca requiere carta de agua (no es fraccionamiento)."""
        exp = self._exp_dict("reunion_de_fincas", area_m2=800.0)
        assert wf._requiere_carta_agua(exp) is False

    def test_rectificacion_no_requiere_nunca(self, wf):
        """Tipos distintos de segregacion → no requieren carta de agua."""
        exp = self._exp_dict("rectificacion", area_m2=100.0)
        assert wf._requiere_carta_agua(exp) is False

    def test_informacion_posesoria_no_requiere_nunca(self, wf):
        exp = self._exp_dict("informacion_posesoria")
        assert wf._requiere_carta_agua(exp) is False

    def test_fincas_completas_no_requiere(self, wf):
        exp = self._exp_dict("fincas_completas", area_m2=500.0)
        assert wf._requiere_carta_agua(exp) is False

    def test_explicito_false_anula_tipo(self, wf):
        """carta_agua_requerida: False anula el tipo (segregacion pequeña)."""
        exp = self._exp_dict("segregacion", area_m2=100.0, explicito=False)
        assert wf._requiere_carta_agua(exp) is False

    def test_explicito_true_anula_tipo(self, wf):
        """carta_agua_requerida: True fuerza requerida aunque sea rectificacion."""
        exp = self._exp_dict("rectificacion", explicito=True)
        assert wf._requiere_carta_agua(exp) is True

    def test_area_invalida_como_str_sin_area_requiere_por_precaucion(self, wf):
        """Área con texto inválido → tramo OPCIONAL (no obligatoria)."""
        meta = {"area_m2": "no es un número"}
        exp = {
            "tipo_plano": "segregacion",
            "metadata_json": json.dumps(meta),
        }
        # Regla actualizada: área no parseable → opcional, no obligatoria
        assert wf._requiere_carta_agua(exp) is False

    # ── Nueva regla operativa (3 tramos puros — sin zonas del Plan Reg) ────
    # La lógica de zona_regulador ya no se usa para decidir obligatoria/no.
    # Lo que manda es el ÁREA del lote: <1000 obligatoria, 1000-5000 opcional,
    # >5000 no aplica.
    # Estos tests verifican que la zona NO interfiere con la decisión por área.

    def test_zona_residencial_500m2_es_obligatoria(self, wf):
        """500 m² → obligatoria (independiente de zona)."""
        exp = self._exp_dict("segregacion", area_m2=500.0,
                              zona_regulador="SR_RESIDENCIAL")
        assert wf._requiere_carta_agua(exp) is True

    def test_zona_residencial_2500m2_es_opcional(self, wf):
        """2500 m² → tramo opcional (no obligatoria)."""
        exp = self._exp_dict("segregacion", area_m2=2500.0,
                              zona_regulador="SR_RESIDENCIAL")
        assert wf._requiere_carta_agua(exp) is False

    def test_zona_amortiguamiento_3000m2_es_opcional(self, wf):
        """3000 m² → opcional (no obligatoria)."""
        exp = self._exp_dict("segregacion", area_m2=3000.0,
                              zona_regulador="SR_AMORTIGUAMIENTO_CIUDAD")
        assert wf._requiere_carta_agua(exp) is False

    def test_zona_proteccion_recursos_500m2_es_obligatoria(self, wf):
        """500 m² → obligatoria (la zona ya no anula la regla por área)."""
        exp = self._exp_dict("segregacion", area_m2=500.0,
                              zona_regulador="SR_PROTECCION_RECURSOS")
        # Para preservar el caso "zona sin AyA", el operador debe usar
        # metadata.carta_agua_requerida=false explícitamente
        assert wf._requiere_carta_agua(exp) is True

    def test_zona_proteccion_recursos_sin_area_es_opcional(self, wf):
        """Sin área → opcional (no obligatoria)."""
        exp = self._exp_dict("segregacion")
        exp["metadata_json"] = json.dumps({"zona_regulador": "SR_PROTECCION_RECURSOS"})
        assert wf._requiere_carta_agua(exp) is False

    def test_zona_agropecuario_8000m2_no_aplica(self, wf):
        """8000 m² > 5000 → no aplica (carta no requerida)."""
        exp = self._exp_dict("segregacion", area_m2=8000.0,
                              zona_regulador="SR_AGROPECUARIO")
        assert wf._requiere_carta_agua(exp) is False

    def test_zona_agropecuario_12000m2_no_aplica(self, wf):
        """12000 m² > 5000 → no aplica."""
        exp = self._exp_dict("segregacion", area_m2=12000.0,
                              zona_regulador="SR_AGROPECUARIO")
        assert wf._requiere_carta_agua(exp) is False

    def test_zona_desconocida_1500m2_es_opcional(self, wf):
        """1500 m² (independiente de zona) → opcional."""
        exp = self._exp_dict("segregacion", area_m2=1500.0,
                              zona_regulador="ZONA_INEXISTENTE")
        assert wf._requiere_carta_agua(exp) is False
