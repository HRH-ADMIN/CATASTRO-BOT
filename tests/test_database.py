"""Tests para Database — cobertura de todos los métodos públicos.

Usa TestDatabase (sqlite3 sin cifrar) y FakeCredentialManager del conftest.
Cada test opera en una BD temporal aislada (tmp_path).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("win32cred", reason="pywin32 requerido (solo Windows)")

from tests.conftest import FakeCredentialManager, TestDatabase  # noqa: E402
from src.core.exceptions import DatabaseError  # noqa: E402
from src.models.estado import Estado  # noqa: E402
from src.models.plano import TipoPlano  # noqa: E402


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path: Path):
    creds = FakeCredentialManager()
    database = TestDatabase(path=tmp_path / "test.db", credentials=creds)
    database.initialize_schema()
    return database


def _crear_exp(db, *, numero="EXP-001", tipo="segregacion",
               telefono="50688880001", topografo="Juan Mora",
               cliente="Ana Arias", municipalidad="San Ramón",
               metadata=None) -> str:
    return db.crear_expediente(
        numero_expediente=numero,
        tipo_plano=tipo,
        nombre_topografo=topografo,
        telefono_cliente=telefono,
        cedula_topografo="1-0001-0001",
        nombre_cliente=cliente,
        municipalidad=municipalidad,
        metadata=metadata or {},
        actor="test",
    )


# ─────────────────────────────────────────────────────────────────────────────
# crear_expediente / obtener_expediente
# ─────────────────────────────────────────────────────────────────────────────

class TestCrearObtenerExpediente:

    def test_crear_retorna_uuid(self, db):
        eid = _crear_exp(db)
        assert len(eid) == 36  # UUID v4

    def test_obtener_por_id(self, db):
        eid = _crear_exp(db, numero="EXP-111")
        exp = db.obtener_expediente(eid)
        assert exp is not None
        assert exp["numero_expediente"] == "EXP-111"

    def test_obtener_inexistente_devuelve_none(self, db):
        assert db.obtener_expediente("no-existe") is None

    def test_estado_inicial_es_recibido(self, db):
        eid = _crear_exp(db)
        exp = db.obtener_expediente(eid)
        assert exp["estado_actual"] == Estado.RECIBIDO.value

    def test_tipo_plano_invalido_lanza(self, db):
        with pytest.raises(DatabaseError, match="tipo_plano"):
            db.crear_expediente(
                numero_expediente="EXP-X",
                tipo_plano="tipo_inventado",
                nombre_topografo="T",
                telefono_cliente="50688880001",
                actor="test",
            )

    def test_numero_duplicado_lanza(self, db):
        _crear_exp(db, numero="EXP-DUP")
        with pytest.raises(Exception):
            _crear_exp(db, numero="EXP-DUP")

    def test_metadata_guardada(self, db):
        eid = _crear_exp(db, metadata={"finca": "123456", "area_m2": 500.0})
        exp = db.obtener_expediente(eid)
        meta = json.loads(exp["metadata_json"])
        assert meta["finca"] == "123456"
        assert meta["area_m2"] == 500.0

    def test_campos_opcionales_ninguno(self, db):
        eid = db.crear_expediente(
            numero_expediente="EXP-MIN",
            tipo_plano="informacion_posesoria",
            nombre_topografo="T",
            telefono_cliente="50699990001",
            actor="test",
        )
        exp = db.obtener_expediente(eid)
        assert exp is not None
        assert exp["cedula_topografo"] is None
        assert exp["nombre_cliente"] is None


# ─────────────────────────────────────────────────────────────────────────────
# buscar_por_numero
# ─────────────────────────────────────────────────────────────────────────────

class TestBuscarPorNumero:

    def test_encontrado(self, db):
        _crear_exp(db, numero="EXP-FIND")
        exp = db.buscar_por_numero("EXP-FIND")
        assert exp is not None
        assert exp["numero_expediente"] == "EXP-FIND"

    def test_no_encontrado_devuelve_none(self, db):
        assert db.buscar_por_numero("NO-EXISTE") is None


# ─────────────────────────────────────────────────────────────────────────────
# cambiar_estado / historial_estados
# ─────────────────────────────────────────────────────────────────────────────

class TestCambiarEstado:

    def test_cambia_estado(self, db):
        eid = _crear_exp(db)
        db.cambiar_estado(eid, Estado.FORMATO_VALIDADO.value, actor="test")
        exp = db.obtener_expediente(eid)
        assert exp["estado_actual"] == Estado.FORMATO_VALIDADO.value

    def test_estado_invalido_lanza(self, db):
        eid = _crear_exp(db)
        with pytest.raises(DatabaseError, match="estado"):
            db.cambiar_estado(eid, "estado_inventado", actor="test")

    def test_idempotente_mismo_estado(self, db):
        eid = _crear_exp(db)
        db.cambiar_estado(eid, Estado.FORMATO_VALIDADO.value, actor="test")
        db.cambiar_estado(eid, Estado.FORMATO_VALIDADO.value, actor="test")  # no falla
        exp = db.obtener_expediente(eid)
        assert exp["estado_actual"] == Estado.FORMATO_VALIDADO.value

    def test_expediente_inexistente_lanza(self, db):
        with pytest.raises(DatabaseError):
            db.cambiar_estado("no-existe", Estado.FORMATO_VALIDADO.value, actor="t")

    def test_entregado_marca_completado(self, db):
        eid = _crear_exp(db)
        db.cambiar_estado(eid, Estado.ENTREGADO.value, actor="test")
        exp = db.obtener_expediente(eid)
        assert exp["completado"] == 1

    def test_cancelado_marca_cancelado(self, db):
        eid = _crear_exp(db)
        db.cambiar_estado(eid, Estado.CANCELADO.value, actor="test")
        exp = db.obtener_expediente(eid)
        assert exp["cancelado"] == 1

    def test_historial_registra_transiciones(self, db):
        eid = _crear_exp(db)
        db.cambiar_estado(eid, Estado.PAGO_CLIENTE_PENDIENTE.value, actor="test")
        db.cambiar_estado(eid, Estado.PAGO_CLIENTE_CONFIRMADO.value, actor="test")
        hist = db.historial_estados(eid)
        # Incluye la entrada inicial de creación
        assert len(hist) >= 3
        estados_nuevos = [h["estado_nuevo"] for h in hist]
        assert Estado.PAGO_CLIENTE_PENDIENTE.value in estados_nuevos
        assert Estado.PAGO_CLIENTE_CONFIRMADO.value in estados_nuevos

    def test_historial_orden_ascendente(self, db):
        eid = _crear_exp(db)
        db.cambiar_estado(eid, Estado.PAGO_CLIENTE_PENDIENTE.value, actor="test")
        db.cambiar_estado(eid, Estado.PAGO_CLIENTE_CONFIRMADO.value, actor="test")
        hist = db.historial_estados(eid)
        ids = [h["id"] for h in hist]
        assert ids == sorted(ids)


# ─────────────────────────────────────────────────────────────────────────────
# listar_expedientes
# ─────────────────────────────────────────────────────────────────────────────

class TestListarExpedientes:

    def test_lista_todos(self, db):
        _crear_exp(db, numero="E1")
        _crear_exp(db, numero="E2")
        exps = db.listar_expedientes()
        assert len(exps) >= 2

    def test_filtro_estado(self, db):
        eid = _crear_exp(db, numero="E-EST")
        db.cambiar_estado(eid, Estado.FORMATO_VALIDADO.value, actor="t")
        # Hay un expediente en FORMATO_VALIDADO
        encontrados = db.listar_expedientes(estado=Estado.FORMATO_VALIDADO.value)
        ids = [e["id"] for e in encontrados]
        assert eid in ids

    def test_filtro_tipo_plano(self, db):
        _crear_exp(db, numero="E-SEG", tipo="segregacion")
        _crear_exp(db, numero="E-SIT", tipo="informacion_posesoria")
        segr = db.listar_expedientes(tipo_plano="segregacion")
        tipos = {e["tipo_plano"] for e in segr}
        assert tipos == {"segregacion"}

    def test_filtro_no_completados(self, db):
        eid = _crear_exp(db, numero="E-COMP")
        db.cambiar_estado(eid, Estado.ENTREGADO.value, actor="t")
        no_comp = db.listar_expedientes(completados=False)
        ids = [e["id"] for e in no_comp]
        assert eid not in ids


# ─────────────────────────────────────────────────────────────────────────────
# actualizar_metadata / get_mega_path / set_mega_path
# ─────────────────────────────────────────────────────────────────────────────

class TestMetadata:

    def test_actualizar_metadata_merge(self, db):
        eid = _crear_exp(db, metadata={"a": 1})
        db.actualizar_metadata(eid, {"b": 2}, actor="test")
        exp = db.obtener_expediente(eid)
        meta = json.loads(exp["metadata_json"])
        assert meta["a"] == 1  # preservado
        assert meta["b"] == 2  # nuevo

    def test_actualizar_metadata_sobrescribe_campo(self, db):
        eid = _crear_exp(db, metadata={"campo": "viejo"})
        db.actualizar_metadata(eid, {"campo": "nuevo"}, actor="test")
        meta = json.loads(db.obtener_expediente(eid)["metadata_json"])
        assert meta["campo"] == "nuevo"

    def test_set_get_mega_path(self, db):
        eid = _crear_exp(db)
        assert db.get_mega_path(eid) is None
        db.set_mega_path(eid, "A:/mega/catastro-bot/ACTIVOS/MORA_JUAN/EXP-001", actor="t")
        stored = db.get_mega_path(eid)
        assert stored is not None
        assert "EXP-001" in stored

    def test_set_mega_path_inexistente_lanza(self, db):
        with pytest.raises(DatabaseError):
            db.set_mega_path("no-existe", "A:/mega/test", actor="t")

    def test_get_mega_path_inexistente_devuelve_none(self, db):
        assert db.get_mega_path("no-existe") is None


# ─────────────────────────────────────────────────────────────────────────────
# buscar_por_mega_path
# ─────────────────────────────────────────────────────────────────────────────

class TestBuscarPorMegaPath:

    def test_match_exacto(self, db):
        eid = _crear_exp(db)
        ruta = "A:/mega/catastro-bot/ACTIVOS/TEST/EXP-001"
        db.set_mega_path(eid, ruta, actor="test")
        resultado = db.buscar_por_mega_path(ruta)
        assert resultado is not None
        assert resultado["id"] == eid

    def test_sub_ruta_exacta_no_almacenada_devuelve_none(self, db):
        """buscar_por_mega_path busca el path almacenado dentro del input.
        Si el input es una sub-ruta, el SQL LIKE no la encuentra — el watchdog
        resuelve esto iterando sobre los padres (ver _expediente_desde_path)."""
        eid = _crear_exp(db)
        ruta_raiz = "A:/mega/catastro-bot/ACTIVOS/TEST/EXP-001"
        db.set_mega_path(eid, ruta_raiz, actor="test")
        # Sub-ruta que NO está almacenada → None esperado (el watchdog itera padres)
        sub = ruta_raiz + "/03_APT_R1/SUBIR"
        resultado = db.buscar_por_mega_path(sub)
        assert resultado is None  # comportamiento esperado: sin match SQL

    def test_ruta_inexistente_devuelve_none(self, db):
        assert db.buscar_por_mega_path("A:/mega/no-existe") is None

    def test_expediente_cancelado_no_aparece(self, db):
        eid = _crear_exp(db)
        ruta = "A:/mega/catastro-bot/ACTIVOS/TEST/EXP-CANCEL"
        db.set_mega_path(eid, ruta, actor="test")
        db.cambiar_estado(eid, Estado.CANCELADO.value, actor="test")
        resultado = db.buscar_por_mega_path(ruta)
        assert resultado is None


# ─────────────────────────────────────────────────────────────────────────────
# buscar_expedientes
# ─────────────────────────────────────────────────────────────────────────────

class TestBuscarExpedientes:

    def test_buscar_por_numero(self, db):
        _crear_exp(db, numero="EXP-BUSCAR-001")
        resultados = db.buscar_expedientes("BUSCAR-001")
        assert any(e["numero_expediente"] == "EXP-BUSCAR-001" for e in resultados)

    def test_buscar_por_topografo(self, db):
        _crear_exp(db, numero="E-T", topografo="Herrera Vargas")
        resultados = db.buscar_expedientes("Herrera")
        assert any("Herrera" in e["nombre_topografo"] for e in resultados)

    def test_buscar_por_cliente(self, db):
        _crear_exp(db, numero="E-C", cliente="Campos Solano")
        resultados = db.buscar_expedientes("Campos")
        assert any("Campos" in (e.get("nombre_cliente") or "") for e in resultados)

    def test_sin_resultados_devuelve_lista_vacia(self, db):
        resultados = db.buscar_expedientes("xyzzy_nada_123")
        assert resultados == []

    def test_cancelados_excluidos(self, db):
        eid = _crear_exp(db, numero="E-CANC")
        db.cambiar_estado(eid, Estado.CANCELADO.value, actor="test")
        resultados = db.buscar_expedientes("E-CANC")
        assert all(e["id"] != eid for e in resultados)

    def test_max_resultados_respetado(self, db):
        for i in range(10):
            _crear_exp(db, numero=f"EXP-LOTE-{i:02d}")
        resultados = db.buscar_expedientes("EXP-LOTE", max_resultados=3)
        assert len(resultados) <= 3


# ─────────────────────────────────────────────────────────────────────────────
# registrar_archivo / archivos_de
# ─────────────────────────────────────────────────────────────────────────────

class TestArchivos:

    def _registrar(self, db, eid, *, fase="campo", tipo="anverso",
                   nombre="plano.pdf", sha="abc123", ruta=None):
        return db.registrar_archivo(
            expediente_id=eid,
            fase=fase,
            tipo_archivo=tipo,
            nombre_original=nombre,
            sha256=sha,
            ruta_local=ruta,
            tamano_bytes=1024,
            actor="test",
        )

    def test_registrar_retorna_uuid(self, db):
        eid = _crear_exp(db)
        fid = self._registrar(db, eid)
        assert len(fid) == 36

    def test_archivos_de_expediente(self, db):
        eid = _crear_exp(db)
        self._registrar(db, eid, nombre="a.pdf", sha="sha1")
        self._registrar(db, eid, nombre="b.pdf", sha="sha2")
        archivos = db.archivos_de(eid)
        assert len(archivos) == 2

    def test_archivos_de_filtro_fase(self, db):
        eid = _crear_exp(db)
        self._registrar(db, eid, fase="campo",     nombre="campo.pdf", sha="s1")
        self._registrar(db, eid, fase="apt_ronda1", nombre="apt.pdf",   sha="s2")
        campo = db.archivos_de(eid, fase="campo")
        assert len(campo) == 1
        assert campo[0]["nombre_original"] == "campo.pdf"

    def test_archivos_de_expediente_sin_archivos(self, db):
        eid = _crear_exp(db)
        assert db.archivos_de(eid) == []

    def test_archivos_orden_ascendente_fecha(self, db):
        eid = _crear_exp(db)
        self._registrar(db, eid, nombre="primero.pdf", sha="s1")
        self._registrar(db, eid, nombre="segundo.pdf", sha="s2")
        archivos = db.archivos_de(eid)
        assert archivos[0]["nombre_original"] == "primero.pdf"


# ─────────────────────────────────────────────────────────────────────────────
# crear_accion_pendiente / resolver_accion / acciones_pendientes
# ─────────────────────────────────────────────────────────────────────────────

class TestAccionesPendientes:

    def _crear_accion(self, db, eid, tipo="pago_cliente",
                      expira_en=None, msg_id=None):
        return db.crear_accion_pendiente(
            expediente_id=eid,
            tipo_accion=tipo,
            descripcion=f"Confirmar {tipo}",
            whatsapp_message_id=msg_id,
            expira_en=expira_en,
            actor="test",
        )

    def test_crear_retorna_uuid(self, db):
        eid = _crear_exp(db)
        aid = self._crear_accion(db, eid)
        assert len(aid) == 36

    def test_estado_inicial_pendiente(self, db):
        eid = _crear_exp(db)
        aid = self._crear_accion(db, eid)
        acciones = db.acciones_pendientes(expediente_id=eid)
        assert len(acciones) == 1
        assert acciones[0]["id"] == aid
        assert acciones[0]["estado"] == "pendiente"

    def test_acciones_pendientes_global(self, db):
        e1 = _crear_exp(db, numero="E1")
        e2 = _crear_exp(db, numero="E2")
        self._crear_accion(db, e1, tipo="t1")
        self._crear_accion(db, e2, tipo="t2")
        todas = db.acciones_pendientes()
        ids = {a["id"] for a in todas}
        assert len(ids) >= 2

    def test_resolver_confirmada(self, db):
        eid = _crear_exp(db)
        aid = self._crear_accion(db, eid)
        db.resolver_accion(aid, "confirmada", whatsapp_response="SI", actor="t")
        acciones = db.acciones_pendientes(expediente_id=eid)
        assert len(acciones) == 0  # ya no está pendiente

    def test_resolver_rechazada(self, db):
        eid = _crear_exp(db)
        aid = self._crear_accion(db, eid)
        db.resolver_accion(aid, "rechazada", actor="t")
        assert db.acciones_pendientes(expediente_id=eid) == []

    def test_resolver_expirada(self, db):
        eid = _crear_exp(db)
        aid = self._crear_accion(db, eid)
        db.resolver_accion(aid, "expirada", actor="t")
        assert db.acciones_pendientes(expediente_id=eid) == []

    def test_resolver_estado_invalido_lanza(self, db):
        eid = _crear_exp(db)
        aid = self._crear_accion(db, eid)
        with pytest.raises(DatabaseError, match="estado"):
            db.resolver_accion(aid, "pendiente", actor="t")

    def test_resolver_no_existente_lanza(self, db):
        with pytest.raises(DatabaseError):
            db.resolver_accion("no-existe", "confirmada", actor="t")

    def test_resolver_ya_resuelta_lanza(self, db):
        eid = _crear_exp(db)
        aid = self._crear_accion(db, eid)
        db.resolver_accion(aid, "confirmada", actor="t")
        with pytest.raises(DatabaseError, match="ya resuelta"):
            db.resolver_accion(aid, "rechazada", actor="t")

    def test_whatsapp_message_id_guardado(self, db):
        eid = _crear_exp(db)
        self._crear_accion(db, eid, msg_id="MSG-999")
        accion = db.acciones_pendientes(expediente_id=eid)[0]
        assert accion["whatsapp_message_id"] == "MSG-999"


# ─────────────────────────────────────────────────────────────────────────────
# ultima_accion / accion_confirmada
# ─────────────────────────────────────────────────────────────────────────────

class TestUltimaAccionConfirmada:

    def test_ultima_accion_devuelve_la_mas_reciente(self, db):
        eid = _crear_exp(db)
        a1 = db.crear_accion_pendiente(
            expediente_id=eid, tipo_accion="tipo_a",
            descripcion="d1", actor="t",
        )
        a2 = db.crear_accion_pendiente(
            expediente_id=eid, tipo_accion="tipo_a",
            descripcion="d2", actor="t",
        )
        db.resolver_accion(a1, "expirada", actor="t")
        ult = db.ultima_accion(expediente_id=eid, tipo_accion="tipo_a")
        assert ult is not None
        assert ult["id"] == a2

    def test_ultima_accion_none_si_no_existe(self, db):
        eid = _crear_exp(db)
        assert db.ultima_accion(expediente_id=eid, tipo_accion="inexistente") is None

    def test_accion_confirmada_dentro_de_ventana(self, db):
        eid = _crear_exp(db)
        aid = db.crear_accion_pendiente(
            expediente_id=eid, tipo_accion="pago",
            descripcion="d", actor="t",
        )
        db.resolver_accion(aid, "confirmada", actor="t")
        resultado = db.accion_confirmada(
            expediente_id=eid, tipo_accion="pago",
            dentro_de_segundos=3600,
        )
        assert resultado is not None
        assert resultado["estado"] == "confirmada"

    def test_accion_pendiente_no_cuenta_como_confirmada(self, db):
        eid = _crear_exp(db)
        db.crear_accion_pendiente(
            expediente_id=eid, tipo_accion="pago",
            descripcion="d", actor="t",
        )
        resultado = db.accion_confirmada(
            expediente_id=eid, tipo_accion="pago",
            dentro_de_segundos=3600,
        )
        assert resultado is None


# ─────────────────────────────────────────────────────────────────────────────
# invalidar_confirmacion
# ─────────────────────────────────────────────────────────────────────────────

class TestInvalidarConfirmacion:
    """Cubre db.invalidar_confirmacion() — limpieza al reiniciar flujo APT."""

    def test_confirmada_pasa_a_expirada(self, db):
        eid = _crear_exp(db)
        aid = db.crear_accion_pendiente(
            expediente_id=eid, tipo_accion="firma_digital_r1",
            descripcion="d", actor="t",
        )
        db.resolver_accion(aid, "confirmada", actor="t")
        # Antes de invalidar, accion_confirmada la encuentra
        assert db.accion_confirmada(
            expediente_id=eid, tipo_accion="firma_digital_r1",
            dentro_de_segundos=3600,
        ) is not None

        n = db.invalidar_confirmacion(eid, "firma_digital_r1", actor="test")
        assert n == 1

        # Después de invalidar, ya no se encuentra como confirmada
        assert db.accion_confirmada(
            expediente_id=eid, tipo_accion="firma_digital_r1",
            dentro_de_segundos=3600,
        ) is None

    def test_retorna_cero_si_nada_confirmado(self, db):
        eid = _crear_exp(db)
        n = db.invalidar_confirmacion(eid, "tipo_inexistente", actor="t")
        assert n == 0

    def test_solo_afecta_tipo_indicado(self, db):
        eid = _crear_exp(db)
        aid_a = db.crear_accion_pendiente(
            expediente_id=eid, tipo_accion="firma_digital_r1",
            descripcion="d", actor="t",
        )
        aid_b = db.crear_accion_pendiente(
            expediente_id=eid, tipo_accion="subir_apt",
            descripcion="d", actor="t",
        )
        db.resolver_accion(aid_a, "confirmada", actor="t")
        db.resolver_accion(aid_b, "confirmada", actor="t")

        db.invalidar_confirmacion(eid, "firma_digital_r1", actor="t")

        # firma_digital_r1 ya no disponible
        assert db.accion_confirmada(
            expediente_id=eid, tipo_accion="firma_digital_r1",
            dentro_de_segundos=3600,
        ) is None
        # subir_apt sigue confirmada
        assert db.accion_confirmada(
            expediente_id=eid, tipo_accion="subir_apt",
            dentro_de_segundos=3600,
        ) is not None

    def test_no_toca_acciones_pendientes(self, db):
        """Solo invalida confirmadas, no pendientes."""
        eid = _crear_exp(db)
        db.crear_accion_pendiente(
            expediente_id=eid, tipo_accion="firma_digital_r1",
            descripcion="d", actor="t",
        )
        n = db.invalidar_confirmacion(eid, "firma_digital_r1", actor="t")
        assert n == 0  # nada confirmado → nada invalidado


# ─────────────────────────────────────────────────────────────────────────────
# buscar_acciones
# ─────────────────────────────────────────────────────────────────────────────

class TestBuscarAcciones:

    def test_buscar_por_tipo_y_estado(self, db):
        eid = _crear_exp(db)
        aid = db.crear_accion_pendiente(
            expediente_id=eid, tipo_accion="recordatorio_vencimiento_plano",
            descripcion="Aviso vencimiento", actor="t",
        )
        db.resolver_accion(aid, "expirada", actor="t")
        resultados = db.buscar_acciones(
            tipo_accion="recordatorio_vencimiento_plano",
            estado="expirada",
        )
        assert len(resultados) >= 1
        assert all(r["tipo_accion"] == "recordatorio_vencimiento_plano"
                   for r in resultados)

    def test_sin_resultados_devuelve_lista_vacia(self, db):
        resultados = db.buscar_acciones(
            tipo_accion="tipo_inexistente", estado="confirmada"
        )
        assert resultados == []


# ─────────────────────────────────────────────────────────────────────────────
# Usuarios
# ─────────────────────────────────────────────────────────────────────────────

class TestUsuarios:

    def test_crear_usuario_devuelve_id(self, db):
        uid = db.crear_usuario(
            nombre="Luis Mora", telefono="88880001",
            rol="topografo", actor="test",
        )
        assert isinstance(uid, int)
        assert uid > 0

    def test_obtener_usuario_por_telefono(self, db):
        db.crear_usuario(nombre="Ana", telefono="88880002", rol="asistente", actor="t")
        user = db.obtener_usuario_por_telefono("88880002")
        assert user is not None
        assert user["nombre"] == "Ana"
        assert user["rol"] == "asistente"

    def test_obtener_usuario_con_prefijo_506(self, db):
        db.crear_usuario(nombre="Juan", telefono="88880003", rol="admin", actor="t")
        # El número se guarda como "50688880003" — buscar con ese formato también funciona
        user = db.obtener_usuario_por_telefono("50688880003")
        assert user is not None
        assert user["nombre"] == "Juan"

    def test_obtener_usuario_inexistente_devuelve_none(self, db):
        assert db.obtener_usuario_por_telefono("00000000") is None

    def test_rol_invalido_lanza(self, db):
        with pytest.raises(DatabaseError, match="rol"):
            db.crear_usuario(
                nombre="X", telefono="88880099", rol="jefe", actor="t"
            )

    def test_telefono_duplicado_lanza(self, db):
        db.crear_usuario(nombre="A", telefono="88880004", rol="admin", actor="t")
        with pytest.raises(DatabaseError):
            db.crear_usuario(nombre="B", telefono="88880004", rol="topografo", actor="t")

    def test_listar_usuarios_todos(self, db):
        db.crear_usuario(nombre="U1", telefono="88880011", rol="admin", actor="t")
        db.crear_usuario(nombre="U2", telefono="88880012", rol="asistente", actor="t")
        users = db.listar_usuarios()
        assert len(users) >= 2

    def test_listar_usuarios_por_rol(self, db):
        db.crear_usuario(nombre="Top1", telefono="88880021", rol="topografo", actor="t")
        db.crear_usuario(nombre="Adm1", telefono="88880022", rol="admin", actor="t")
        tops = db.listar_usuarios(rol="topografo")
        assert all(u["rol"] == "topografo" for u in tops)

    def test_listar_usuarios_activos(self, db):
        db.crear_usuario(nombre="Activo", telefono="88880031", rol="admin", actor="t")
        db.crear_usuario(nombre="Inactivo", telefono="88880032", rol="admin", actor="t")
        db.desactivar_usuario("88880032", actor="test")
        activos = db.listar_usuarios(activo=True)
        nombres = [u["nombre"] for u in activos]
        assert "Inactivo" not in nombres
        assert "Activo" in nombres

    def test_cambiar_rol(self, db):
        db.crear_usuario(nombre="CambioRol", telefono="88880041", rol="asistente", actor="t")
        db.cambiar_rol("88880041", "topografo", actor="test")
        user = db.obtener_usuario_por_telefono("88880041")
        assert user["rol"] == "topografo"

    def test_cambiar_rol_invalido_lanza(self, db):
        db.crear_usuario(nombre="X", telefono="88880042", rol="admin", actor="t")
        with pytest.raises(DatabaseError, match="rol"):
            db.cambiar_rol("88880042", "jefe", actor="t")

    def test_cambiar_rol_inexistente_lanza(self, db):
        with pytest.raises(DatabaseError):
            db.cambiar_rol("00000000", "admin", actor="t")

    def test_desactivar_usuario(self, db):
        db.crear_usuario(nombre="Des", telefono="88880051", rol="admin", actor="t")
        db.desactivar_usuario("88880051", actor="test")
        # obtener_usuario solo devuelve activos
        assert db.obtener_usuario_por_telefono("88880051") is None

    def test_desactivar_usuario_inexistente_lanza(self, db):
        with pytest.raises(DatabaseError):
            db.desactivar_usuario("00000000", actor="t")


# ─────────────────────────────────────────────────────────────────────────────
# Audit log
# ─────────────────────────────────────────────────────────────────────────────

class TestAuditLog:

    def test_verify_audit_chain_limpia(self, db):
        # initialize_schema ya escribió una entrada de audit
        eid = _crear_exp(db)
        db.cambiar_estado(eid, Estado.FORMATO_VALIDADO.value, actor="t")
        n = db.verify_audit_chain()
        assert n >= 3  # schema.initialize + expediente.crear + cambiar_estado

    def test_audit_log_por_expediente(self, db):
        eid = _crear_exp(db, numero="EXP-AUDIT")
        db.cambiar_estado(eid, Estado.PAGO_CLIENTE_PENDIENTE.value, actor="t")
        entradas = db.audit_log(expediente_id=eid)
        acciones = [e["accion"] for e in entradas]
        assert "expediente.crear" in acciones
        assert "expediente.cambiar_estado" in acciones

    def test_audit_log_global_incluye_schema(self, db):
        entradas = db.audit_log()
        acciones = [e["accion"] for e in entradas]
        assert "schema.initialize" in acciones

    def test_audit_log_limit(self, db):
        # Crear varias entradas
        for i in range(5):
            _crear_exp(db, numero=f"EXP-LIM-{i}")
        entradas = db.audit_log(limit=3)
        assert len(entradas) <= 3

    def test_audit_triggers_impiden_modificacion(self, db):
        """El trigger debe impedir UPDATE sobre audit_log."""
        from src.core.exceptions import DatabaseError as DBE
        eid = _crear_exp(db)
        # Intentar UPDATE directo en audit_log debe fallar
        import sqlite3 as _sqlite3
        with db.connect() as conn:
            with pytest.raises(_sqlite3.IntegrityError, match="immutable"):
                conn.execute("UPDATE audit_log SET actor = 'hacker' WHERE id = 1")

    def test_audit_triggers_impiden_eliminacion(self, db):
        import sqlite3 as _sqlite3
        _crear_exp(db)
        with db.connect() as conn:
            with pytest.raises(_sqlite3.IntegrityError, match="immutable"):
                conn.execute("DELETE FROM audit_log WHERE id = 1")
