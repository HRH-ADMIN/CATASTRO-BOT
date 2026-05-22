"""Tests del sistema de roles WhatsApp (Semana 2).

Cobertura:
  - Crear usuarios con los 3 roles (admin, topógrafo, asistente)
  - Número no registrado rechazado con mensaje de seguridad
  - Asistente bloqueado en comandos de aprobación irreversibles
  - Topógrafo bloqueado en CERRAR APT (solo admin)
  - Admin puede ejecutar cualquier comando
  - BUSCAR encuentra expedientes
  - SUBIR responde con instrucciones
  - RECIBIR avanza a ENTREGADO si en INSCRITO_DESCARGADO
  - AVISAR envía mensaje al cliente del expediente
  - AYUDA muestra comandos según rol
  - cambiar_rol y desactivar_usuario
  - Fallback: credential-operator sin registro en BD → tratado como admin
"""
from __future__ import annotations

import pytest

from tests.conftest import CLIENT_PHONE, OPERATOR_PHONE, FakeCredentialManager


# ── Teléfonos de prueba ───────────────────────────────────────────────────────
ADMIN_PHONE     = "50688387310"   # número admin inicial del proyecto
TOPOGRAFO_PHONE = "50688000001"
ASISTENTE_PHONE = "50688000002"
UNKNOWN_PHONE   = "50699999999"


# ── Fixtures adicionales ──────────────────────────────────────────────────────

@pytest.fixture
def db_con_usuarios(db):
    """BD con los 3 tipos de usuario pre-cargados."""
    db.crear_usuario(nombre="Alonso Admin",   telefono=ADMIN_PHONE,     rol="admin")
    db.crear_usuario(nombre="Juan Topógrafo", telefono=TOPOGRAFO_PHONE, rol="topografo",
                     carne_cfia="T-1234")
    db.crear_usuario(nombre="Ana Asistente",  telefono=ASISTENTE_PHONE, rol="asistente")
    return db


@pytest.fixture
def router_roles(db_con_usuarios, fake_creds, drive_agent, reply_fn):
    """Router con sistema de roles en BD (sin operadores en credentials)."""
    from src.agents.whatsapp_commands import WhatsAppCommandRouter
    return WhatsAppCommandRouter(
        db=db_con_usuarios,
        credentials=fake_creds,       # sin operadores — todo va por BD
        drive_agent=drive_agent,
        reply_fn=reply_fn,
    )


@pytest.fixture
def exp_inscrito(db_con_usuarios):
    """Expediente en INSCRITO_DESCARGADO listo para ser marcado ENTREGADO."""
    eid = db_con_usuarios.crear_expediente(
        numero_expediente="RECIBIR-2026",
        tipo_plano="segregacion",
        nombre_topografo="Juan Topógrafo",
        telefono_cliente=f"+{CLIENT_PHONE}",
    )
    db_con_usuarios.cambiar_estado(eid, "inscrito_descargado", actor="test")
    return eid


# ── Crear usuarios ────────────────────────────────────────────────────────────

def test_crear_usuario_admin(db):
    uid = db.crear_usuario(nombre="Alonso", telefono=ADMIN_PHONE, rol="admin")
    assert uid > 0
    u = db.obtener_usuario_por_telefono(ADMIN_PHONE)
    assert u is not None
    assert u["rol"] == "admin"
    assert u["nombre"] == "Alonso"
    assert u["activo"] == 1


def test_crear_usuario_topografo_con_carne(db):
    db.crear_usuario(nombre="Juan", telefono=TOPOGRAFO_PHONE,
                     rol="topografo", carne_cfia="T-9999")
    u = db.obtener_usuario_por_telefono(TOPOGRAFO_PHONE)
    assert u["rol"] == "topografo"
    assert u["carne_cfia"] == "T-9999"


def test_crear_usuario_asistente(db):
    db.crear_usuario(nombre="Ana", telefono=ASISTENTE_PHONE, rol="asistente")
    u = db.obtener_usuario_por_telefono(ASISTENTE_PHONE)
    assert u["rol"] == "asistente"


def test_crear_usuario_rol_invalido(db):
    from src.core.exceptions import DatabaseError
    with pytest.raises(DatabaseError, match="rol inválido"):
        db.crear_usuario(nombre="X", telefono="50688111111", rol="superadmin")


def test_crear_usuario_telefono_duplicado(db):
    from src.core.exceptions import DatabaseError
    db.crear_usuario(nombre="Uno", telefono=ADMIN_PHONE, rol="admin")
    with pytest.raises(DatabaseError, match="ya registrado"):
        db.crear_usuario(nombre="Dos", telefono=ADMIN_PHONE, rol="admin")


def test_obtener_usuario_normaliza_formato(db):
    """Debe encontrar el usuario con distintos formatos del mismo número."""
    db.crear_usuario(nombre="Test", telefono="50688387310", rol="admin")
    # Con +
    assert db.obtener_usuario_por_telefono("+50688387310") is not None
    # Solo 8 dígitos
    assert db.obtener_usuario_por_telefono("88387310") is not None
    # Exacto
    assert db.obtener_usuario_por_telefono("50688387310") is not None


def test_cambiar_rol(db):
    db.crear_usuario(nombre="X", telefono=TOPOGRAFO_PHONE, rol="asistente")
    db.cambiar_rol(TOPOGRAFO_PHONE, "topografo")
    u = db.obtener_usuario_por_telefono(TOPOGRAFO_PHONE)
    assert u["rol"] == "topografo"


def test_desactivar_usuario(db):
    db.crear_usuario(nombre="X", telefono=ASISTENTE_PHONE, rol="asistente")
    db.desactivar_usuario(ASISTENTE_PHONE)
    u = db.obtener_usuario_por_telefono(ASISTENTE_PHONE)
    assert u is None  # obtener_usuario_por_telefono solo devuelve activos


def test_listar_usuarios_filtro_rol(db_con_usuarios):
    admins = db_con_usuarios.listar_usuarios(rol="admin")
    assert len(admins) == 1
    assert admins[0]["telefono"] == ADMIN_PHONE

    tops = db_con_usuarios.listar_usuarios(rol="topografo")
    assert len(tops) == 1


# ── Autorización y permisos ───────────────────────────────────────────────────

def test_numero_no_registrado_rechazado(router_roles, replies):
    handled = router_roles.handle(sender_phone=UNKNOWN_PHONE, text="AYUDA")
    assert handled
    assert "no autorizado" in replies[0][1].lower()


def test_numero_no_registrado_consume_mensaje(router_roles, replies):
    """Incluso si el número no registrado envía NUEVO, el mensaje es consumido."""
    handled = router_roles.handle(
        sender_phone=UNKNOWN_PHONE,
        text="NUEVO PLANO\ntipo: segregacion\nexpediente: X-1\ntopografo: T\ntelefono: +50688881111",
    )
    assert handled
    assert router_roles.db.buscar_por_numero("X-1") is None


def test_asistente_no_puede_aprobar(router_roles, replies, db_con_usuarios):
    eid = db_con_usuarios.crear_expediente(
        numero_expediente="HALT-ASIST",
        tipo_plano="segregacion",
        nombre_topografo="X",
        telefono_cliente="+50688881111",
    )
    db_con_usuarios.cambiar_estado(eid, "carta_agua_requerida", actor="test")
    replies.clear()

    handled = router_roles.handle(
        sender_phone=ASISTENTE_PHONE, text="APROBAR HALT-ASIST"
    )

    assert handled
    msg = replies[0][1]
    assert "sin permisos" in msg.lower()
    assert "asistente" in msg.lower()
    # Estado no cambió
    exp = db_con_usuarios.obtener_expediente(eid)
    assert exp["estado_actual"] == "carta_agua_requerida"


def test_asistente_no_puede_rechazar(router_roles, replies, db_con_usuarios):
    db_con_usuarios.crear_expediente(
        numero_expediente="REJ-ASIST",
        tipo_plano="segregacion",
        nombre_topografo="X",
        telefono_cliente="+50688881111",
    )
    replies.clear()

    router_roles.handle(sender_phone=ASISTENTE_PHONE, text="RECHAZAR REJ-ASIST")

    assert "sin permisos" in replies[0][1].lower()


def test_asistente_no_puede_cerrar_apt(router_roles, replies):
    router_roles.handle(sender_phone=ASISTENTE_PHONE, text="CERRAR APT")
    assert "sin permisos" in replies[0][1].lower()


def test_topografo_no_puede_cerrar_apt(router_roles, replies):
    """CERRAR APT es solo para admin."""
    router_roles.handle(sender_phone=TOPOGRAFO_PHONE, text="CERRAR APT")
    assert "sin permisos" in replies[0][1].lower()


def test_admin_puede_aprobar(router_roles, replies, db_con_usuarios):
    eid = db_con_usuarios.crear_expediente(
        numero_expediente="HALT-ADMIN",
        tipo_plano="segregacion",
        nombre_topografo="X",
        telefono_cliente="+50688881111",
    )
    db_con_usuarios.cambiar_estado(eid, "carta_agua_requerida", actor="test")
    replies.clear()

    router_roles.handle(sender_phone=ADMIN_PHONE, text="APROBAR HALT-ADMIN")

    exp = db_con_usuarios.obtener_expediente(eid)
    assert exp["estado_actual"] == "carta_agua_ok"
    assert "✅" in replies[0][1]


def test_topografo_puede_aprobar(router_roles, replies, db_con_usuarios):
    eid = db_con_usuarios.crear_expediente(
        numero_expediente="HALT-TOP",
        tipo_plano="segregacion",
        nombre_topografo="Juan Topógrafo",
        telefono_cliente="+50688881111",
    )
    db_con_usuarios.cambiar_estado(eid, "formato_invalido", actor="test")
    replies.clear()

    router_roles.handle(sender_phone=TOPOGRAFO_PHONE, text="APROBAR HALT-TOP")

    exp = db_con_usuarios.obtener_expediente(eid)
    assert exp["estado_actual"] == "recibido"


# ── BUSCAR ────────────────────────────────────────────────────────────────────

def test_buscar_por_numero(router_roles, replies, db_con_usuarios):
    db_con_usuarios.crear_expediente(
        numero_expediente="BUSCAR-001",
        tipo_plano="segregacion",
        nombre_topografo="Juan Topógrafo",
        telefono_cliente="+50688881111",
    )
    replies.clear()

    router_roles.handle(sender_phone=ADMIN_PHONE, text="BUSCAR BUSCAR-001")

    msg = replies[0][1]
    assert "BUSCAR-001" in msg


def test_buscar_por_topografo(router_roles, replies, db_con_usuarios):
    db_con_usuarios.crear_expediente(
        numero_expediente="TOP-SEARCH-001",
        tipo_plano="reunion_de_fincas",
        nombre_topografo="María Quesada",
        telefono_cliente="+50688881111",
    )
    db_con_usuarios.crear_expediente(
        numero_expediente="TOP-SEARCH-002",
        tipo_plano="informacion_posesoria",
        nombre_topografo="María Quesada",
        telefono_cliente="+50688882222",
    )
    replies.clear()

    router_roles.handle(sender_phone=ADMIN_PHONE, text="BUSCAR María")

    msg = replies[0][1]
    assert "TOP-SEARCH-001" in msg
    assert "TOP-SEARCH-002" in msg


def test_buscar_sin_resultados(router_roles, replies):
    router_roles.handle(sender_phone=ADMIN_PHONE, text="BUSCAR XYZ_INEXISTENTE_99")
    assert "sin resultados" in replies[0][1].lower()


def test_buscar_sin_texto(router_roles, replies):
    router_roles.handle(sender_phone=ADMIN_PHONE, text="BUSCAR")
    assert "uso:" in replies[0][1].lower()


def test_asistente_puede_buscar(router_roles, replies, db_con_usuarios):
    db_con_usuarios.crear_expediente(
        numero_expediente="ASIST-BUSCA",
        tipo_plano="segregacion",
        nombre_topografo="X",
        telefono_cliente="+50688881111",
    )
    replies.clear()

    router_roles.handle(sender_phone=ASISTENTE_PHONE, text="BUSCAR ASIST-BUSCA")

    assert "ASIST-BUSCA" in replies[0][1]


# ── SUBIR ─────────────────────────────────────────────────────────────────────

def test_subir_expediente_recibido(router_roles, replies, db_con_usuarios):
    db_con_usuarios.crear_expediente(
        numero_expediente="SUBIR-001",
        tipo_plano="segregacion",
        nombre_topografo="Juan Topógrafo",
        telefono_cliente="+50688881111",
    )
    replies.clear()

    router_roles.handle(sender_phone=TOPOGRAFO_PHONE, text="SUBIR SUBIR-001")

    msg = replies[0][1]
    assert "SUBIR-001" in msg
    assert "01_Campo" in msg or "01_campo" in msg.lower()


def test_subir_estado_incorrecto(router_roles, replies, db_con_usuarios):
    eid = db_con_usuarios.crear_expediente(
        numero_expediente="SUBIR-WRONG",
        tipo_plano="segregacion",
        nombre_topografo="X",
        telefono_cliente="+50688881111",
    )
    db_con_usuarios.cambiar_estado(eid, "presentado_apt_r1", actor="test")
    replies.clear()

    router_roles.handle(sender_phone=ADMIN_PHONE, text="SUBIR SUBIR-WRONG")

    assert "⚠️" in replies[0][1]


def test_asistente_no_puede_subir(router_roles, replies):
    router_roles.handle(sender_phone=ASISTENTE_PHONE, text="SUBIR 123-2026")
    assert "sin permisos" in replies[0][1].lower()


# ── RECIBIR ───────────────────────────────────────────────────────────────────

def test_recibir_inscrito_descargado_avanza_entregado(
    router_roles, replies, db_con_usuarios, exp_inscrito
):
    router_roles.handle(
        sender_phone=TOPOGRAFO_PHONE, text="RECIBIR RECIBIR-2026"
    )

    exp = db_con_usuarios.obtener_expediente(exp_inscrito)
    assert exp["estado_actual"] == "entregado"
    assert exp["completado"] == 1
    msg = replies[0][1]
    assert "ENTREGADO" in msg
    assert "Registro de la Propiedad" in msg


def test_recibir_ya_entregado(router_roles, replies, db_con_usuarios, exp_inscrito):
    db_con_usuarios.cambiar_estado(exp_inscrito, "entregado", actor="test")
    replies.clear()

    router_roles.handle(sender_phone=ADMIN_PHONE, text="RECIBIR RECIBIR-2026")

    assert "ya está marcado" in replies[0][1].lower()


def test_recibir_estado_intermedio(router_roles, replies, db_con_usuarios):
    db_con_usuarios.crear_expediente(
        numero_expediente="RECIBIR-MID",
        tipo_plano="segregacion",
        nombre_topografo="X",
        telefono_cliente="+50688881111",
    )
    # En RECIBIDO (no inscrito_descargado) → registrar pero no cambiar estado
    router_roles.handle(sender_phone=ADMIN_PHONE, text="RECIBIR RECIBIR-MID")

    assert "registrada" in replies[0][1].lower() or "recepción" in replies[0][1].lower()


def test_asistente_puede_recibir(router_roles, replies, db_con_usuarios, exp_inscrito):
    """Asistente puede marcar RECIBIR (verifica entrega)."""
    router_roles.handle(
        sender_phone=ASISTENTE_PHONE, text="RECIBIR RECIBIR-2026"
    )
    exp = db_con_usuarios.obtener_expediente(exp_inscrito)
    assert exp["estado_actual"] == "entregado"


# ── AVISAR ────────────────────────────────────────────────────────────────────

def test_avisar_envia_al_cliente(router_roles, replies, db_con_usuarios):
    db_con_usuarios.crear_expediente(
        numero_expediente="AVISAR-001",
        tipo_plano="segregacion",
        nombre_topografo="X",
        telefono_cliente=f"+{CLIENT_PHONE}",
    )
    replies.clear()

    router_roles.handle(
        sender_phone=ADMIN_PHONE,
        text="AVISAR AVISAR-001 Su plano está listo para retirar.",
    )

    # Debe haber 2 replies: uno al cliente y uno de confirmación al operador
    assert len(replies) == 2
    cliente_reply = next(
        (r for r in replies if r[0] == f"+{CLIENT_PHONE}" or r[0] == CLIENT_PHONE),
        None,
    )
    assert cliente_reply is not None
    assert "Su plano está listo" in cliente_reply[1]

    # Confirmación al operador
    confirm_reply = next((r for r in replies if r[0] == ADMIN_PHONE), None)
    assert confirm_reply is not None
    assert "AVISAR-001" in confirm_reply[1]


def test_avisar_sin_mensaje(router_roles, replies):
    router_roles.handle(sender_phone=ADMIN_PHONE, text="AVISAR 12345-2026")
    assert "falta" in replies[0][1].lower()


def test_avisar_expediente_no_existe(router_roles, replies):
    router_roles.handle(sender_phone=ADMIN_PHONE, text="AVISAR NOEXISTE-2026 hola")
    assert "no encontrado" in replies[0][1].lower()


def test_asistente_no_puede_avisar(router_roles, replies):
    router_roles.handle(
        sender_phone=ASISTENTE_PHONE, text="AVISAR 123-2026 mensaje"
    )
    assert "sin permisos" in replies[0][1].lower()


# ── AYUDA por rol ─────────────────────────────────────────────────────────────

def test_ayuda_admin_muestra_todos_los_comandos(router_roles, replies):
    router_roles.handle(sender_phone=ADMIN_PHONE, text="AYUDA")

    msg = replies[0][1]
    assert "ESTADO" in msg
    assert "APROBAR" in msg
    assert "RECHAZAR" in msg
    assert "BUSCAR" in msg
    assert "SUBIR" in msg
    assert "RECIBIR" in msg
    assert "AVISAR" in msg
    assert "CERRAR APT" in msg
    assert "admin" in msg.lower()


def test_ayuda_asistente_no_muestra_aprobar(router_roles, replies):
    router_roles.handle(sender_phone=ASISTENTE_PHONE, text="AYUDA")

    msg = replies[0][1]
    assert "APROBAR" not in msg
    assert "RECHAZAR" not in msg
    assert "CERRAR APT" not in msg
    assert "asistente" in msg.lower()
    # Sí debe tener los comandos permitidos
    assert "BUSCAR" in msg
    assert "ESTADO" in msg


def test_ayuda_topografo_tiene_aprobar_pero_no_cerrar(router_roles, replies):
    router_roles.handle(sender_phone=TOPOGRAFO_PHONE, text="AYUDA")

    msg = replies[0][1]
    assert "APROBAR" in msg
    assert "CERRAR APT" not in msg
    assert "topografo" in msg.lower()


# ── Fallback: credentials → admin ────────────────────────────────────────────

def test_fallback_credential_operator_es_admin(db, fake_creds, drive_agent, reply_fn):
    """Un número en credentials (Semana 1) sin registro en BD se trata como admin."""
    fake_creds.add_operator(OPERATOR_PHONE)
    from src.agents.whatsapp_commands import WhatsAppCommandRouter
    router = WhatsAppCommandRouter(
        db=db, credentials=fake_creds, drive_agent=drive_agent, reply_fn=reply_fn
    )
    replies_list: list = []
    router.reply = lambda phone, msg: replies_list.append((phone, msg))

    # AYUDA debe funcionar y mostrar admin
    router.handle(sender_phone=OPERATOR_PHONE, text="AYUDA")
    assert len(replies_list) == 1
    assert "comandos" in replies_list[0][1].lower()


def test_fallback_no_number_en_ninguno_rechaza(db, fake_creds, drive_agent, reply_fn):
    """Si no está en BD NI en credentials, rechaza."""
    from src.agents.whatsapp_commands import WhatsAppCommandRouter
    replies_list: list = []
    router = WhatsAppCommandRouter(
        db=db, credentials=fake_creds, drive_agent=drive_agent,
        reply_fn=lambda p, m: replies_list.append((p, m))
    )
    router.handle(sender_phone=UNKNOWN_PHONE, text="RESUMEN")
    assert "no autorizado" in replies_list[0][1].lower()
