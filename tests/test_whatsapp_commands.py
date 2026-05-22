"""Tests del flujo NUEVO → ESTADO → APROBAR → RECHAZAR → RESUMEN sin
tocar Green API ni Windows Credential Manager reales.

Cobertura:
  - NUEVO: creación, autorización, campos faltantes, tipo inválido,
    duplicado, carpetas creadas
  - ESTADO: existente y no encontrado
  - APROBAR: estado halt (varias transiciones), estado no-halt
  - RECHAZAR: activo y terminal
  - RESUMEN: vacío y con expedientes
  - AYUDA
  - Mensaje no-comando (delegación al flujo SI/NO)
  - Dispatch desde WhatsAppAgent._procesar_notificacion
"""
from __future__ import annotations

from tests.conftest import CLIENT_PHONE, OPERATOR_PHONE


# ============================================================
# NUEVO
# ============================================================

NUEVO_VALIDO = """NUEVO PLANO
tipo: segregacion
expediente: 12345-2026
topografo: Juan Pérez
telefono: +506 8888-1111
cliente: Ana Solís
area: 450
carta_agua: si"""


def test_nuevo_crea_expediente_y_carpetas(router, db, replies, operator_phone, files_root):
    handled = router.handle(sender_phone=operator_phone, text=NUEVO_VALIDO)

    assert handled
    exp = db.buscar_por_numero("12345-2026")
    assert exp is not None
    assert exp["tipo_plano"] == "segregacion"
    assert exp["nombre_topografo"] == "Juan Pérez"
    assert exp["nombre_cliente"] == "Ana Solís"
    assert exp["estado_actual"] == "recibido"
    # Teléfono normalizado a +506XXXXXXXX
    assert exp["telefono_cliente"] == "+50688881111"

    # Metadata: area_m2 + carta_agua_requerida=True
    import json
    meta = json.loads(exp["metadata_json"])
    assert meta["area_m2"] == 450.0
    assert meta["carta_agua_requerida"] is True

    # Las 6 carpetas existen en disco
    base = files_root / "12345-2026"
    assert (base / "01_Campo").is_dir()
    assert (base / "02_Pago").is_dir()
    assert (base / "03_APT_Ronda1").is_dir()
    assert (base / "04_Municipalidad").is_dir()
    assert (base / "05_APT_Ronda2").is_dir()
    assert (base / "06_Inscrito").is_dir()

    # Reply confirmando creación
    assert len(replies) == 1
    phone, msg = replies[0]
    assert phone == operator_phone
    assert "12345-2026" in msg
    assert "✅" in msg
    assert "01_Campo" in msg


def test_nuevo_no_autorizado(router, db, replies):
    handled = router.handle(sender_phone="50699999999", text=NUEVO_VALIDO)

    assert handled  # consume el mensaje (con rechazo)
    assert db.buscar_por_numero("12345-2026") is None
    assert len(replies) == 1
    assert "no autorizado" in replies[0][1].lower()


def test_nuevo_campos_faltantes(router, replies, operator_phone):
    text = "NUEVO PLANO\ntipo: segregacion\nexpediente: X-2026"
    handled = router.handle(sender_phone=operator_phone, text=text)

    assert handled
    msg = replies[0][1].lower()
    assert "faltan campos" in msg
    assert "topografo" in msg
    assert "telefono" in msg


def test_nuevo_tipo_invalido(router, replies, operator_phone):
    text = ("NUEVO PLANO\n"
            "tipo: foo\n"
            "expediente: X-2026\n"
            "topografo: X\n"
            "telefono: +50688881111")
    handled = router.handle(sender_phone=operator_phone, text=text)

    assert handled
    assert "tipo de plano inválido" in replies[0][1].lower()


def test_nuevo_acepta_alias_de_tipo(router, db, replies, operator_phone):
    """Acepta 'rectificación' (con tilde) como alias de rectificacion."""
    text = ("NUEVO PLANO\n"
            "tipo: rectificación\n"
            "expediente: REC-2026\n"
            "topografo: X\n"
            "telefono: +50688881111")
    handled = router.handle(sender_phone=operator_phone, text=text)

    assert handled
    exp = db.buscar_por_numero("REC-2026")
    assert exp is not None
    assert exp["tipo_plano"] == "rectificacion"


def test_nuevo_duplicado(router, db, replies, operator_phone):
    router.handle(sender_phone=operator_phone, text=NUEVO_VALIDO)
    replies.clear()

    handled = router.handle(sender_phone=operator_phone, text=NUEVO_VALIDO)

    assert handled
    assert "❌" in replies[0][1]


def test_nuevo_telefono_invalido(router, replies, operator_phone):
    text = ("NUEVO PLANO\n"
            "tipo: segregacion\n"
            "expediente: T-2026\n"
            "topografo: X\n"
            "telefono: 123")
    handled = router.handle(sender_phone=operator_phone, text=text)

    assert handled
    assert "teléfono" in replies[0][1].lower() or "telefono" in replies[0][1].lower()


def test_nuevo_zona_regulador_alias_residencial(router, db, replies, operator_phone):
    """'zona: residencial' se traduce a SR_RESIDENCIAL en metadata."""
    import json
    text = ("NUEVO PLANO\n"
            "tipo: segregacion\n"
            "expediente: ZONA-001\n"
            "topografo: X\n"
            "telefono: +50688881111\n"
            "zona: residencial")
    router.handle(sender_phone=operator_phone, text=text)
    exp = db.buscar_por_numero("ZONA-001")
    assert exp is not None
    meta = json.loads(exp["metadata_json"])
    assert meta.get("zona_regulador") == "SR_RESIDENCIAL"


def test_nuevo_zona_regulador_alias_amortiguamiento(router, db, replies, operator_phone):
    """'zona: amortiguamiento ciudad' se traduce a SR_AMORTIGUAMIENTO_CIUDAD."""
    import json
    text = ("NUEVO PLANO\n"
            "tipo: segregacion\n"
            "expediente: ZONA-002\n"
            "topografo: X\n"
            "telefono: +50688881111\n"
            "zona: amortiguamiento ciudad")
    router.handle(sender_phone=operator_phone, text=text)
    exp = db.buscar_por_numero("ZONA-002")
    assert exp is not None
    meta = json.loads(exp["metadata_json"])
    assert meta.get("zona_regulador") == "SR_AMORTIGUAMIENTO_CIUDAD"


def test_nuevo_zona_regulador_id_directo(router, db, replies, operator_phone):
    """Permite pasar el ID SR_AGROPECUARIO directamente (uppercase)."""
    import json
    text = ("NUEVO PLANO\n"
            "tipo: segregacion\n"
            "expediente: ZONA-003\n"
            "topografo: X\n"
            "telefono: +50688881111\n"
            "zona_regulador: SR_AGROPECUARIO")
    router.handle(sender_phone=operator_phone, text=text)
    exp = db.buscar_por_numero("ZONA-003")
    assert exp is not None
    meta = json.loads(exp["metadata_json"])
    assert meta.get("zona_regulador") == "SR_AGROPECUARIO"


def test_nuevo_zona_regulador_invalida_ignorada(router, db, replies, operator_phone):
    """Zona inválida/desconocida no se guarda en metadata (no crashea)."""
    import json
    text = ("NUEVO PLANO\n"
            "tipo: segregacion\n"
            "expediente: ZONA-004\n"
            "topografo: X\n"
            "telefono: +50688881111\n"
            "zona: zona_inexistente_xyz")
    router.handle(sender_phone=operator_phone, text=text)
    exp = db.buscar_por_numero("ZONA-004")
    assert exp is not None
    meta = json.loads(exp["metadata_json"])
    assert "zona_regulador" not in meta


# ============================================================
# ESTADO
# ============================================================


def test_estado_de_expediente_existente(router, db, replies, operator_phone):
    eid = db.crear_expediente(
        numero_expediente="55555-2026",
        tipo_plano="segregacion",
        nombre_topografo="X",
        telefono_cliente="+50688881111",
    )
    db.cambiar_estado(eid, "enteros_pagados", actor="test")
    replies.clear()

    handled = router.handle(sender_phone=operator_phone, text="ESTADO 55555-2026")

    assert handled
    msg = replies[0][1]
    assert "55555-2026" in msg
    assert "enteros_pagados" in msg
    assert "segregacion" in msg


def test_estado_marca_halt(router, db, replies, operator_phone):
    eid = db.crear_expediente(
        numero_expediente="HALT-2026",
        tipo_plano="segregacion",
        nombre_topografo="X",
        telefono_cliente="+50688881111",
    )
    db.cambiar_estado(eid, "carta_agua_requerida", actor="test")
    replies.clear()

    router.handle(sender_phone=operator_phone, text="ESTADO HALT-2026")

    assert "HALT" in replies[0][1]


def test_estado_no_existe(router, replies, operator_phone):
    handled = router.handle(sender_phone=operator_phone, text="ESTADO NO-EXISTE")

    assert handled
    assert "no encontrado" in replies[0][1].lower()


def test_estado_sin_argumento(router, replies, operator_phone):
    handled = router.handle(sender_phone=operator_phone, text="ESTADO")

    assert handled
    assert "uso" in replies[0][1].lower()


# ============================================================
# APROBAR
# ============================================================


def test_aprobar_carta_agua(router, db, replies, operator_phone):
    eid = db.crear_expediente(
        numero_expediente="CA-2026",
        tipo_plano="segregacion",
        nombre_topografo="X",
        telefono_cliente="+50688881111",
    )
    db.cambiar_estado(eid, "carta_agua_requerida", actor="test")
    replies.clear()

    handled = router.handle(sender_phone=operator_phone, text="APROBAR CA-2026")

    assert handled
    exp = db.obtener_expediente(eid)
    assert exp["estado_actual"] == "carta_agua_ok"
    assert "✅" in replies[0][1]


def test_aprobar_apt_correcciones_repite_apt(router, db, replies, operator_phone):
    """APROBAR sobre APT_CORRECCIONES devuelve a ENTEROS_PAGADOS para
    re-presentar el plano corregido."""
    eid = db.crear_expediente(
        numero_expediente="CORR-2026",
        tipo_plano="segregacion",
        nombre_topografo="X",
        telefono_cliente="+50688881111",
    )
    db.cambiar_estado(eid, "apt_correcciones", actor="test")
    replies.clear()

    handled = router.handle(sender_phone=operator_phone, text="APROBAR CORR-2026")

    assert handled
    exp = db.obtener_expediente(eid)
    assert exp["estado_actual"] == "enteros_pagados"


def test_aprobar_correcciones_limpia_flags_apt(router, db, replies, operator_phone):
    """APROBAR desde APT_CORRECCIONES limpia apt_r1_archivos_subidos e
    incrementa apt_correcciones_count para que el workflow re-suba los
    archivos corregidos en lugar de reutilizar la subida anterior."""
    import json
    eid = db.crear_expediente(
        numero_expediente="FLAGS-2026",
        tipo_plano="segregacion",
        nombre_topografo="X",
        telefono_cliente="+50688881111",
    )
    # Simular que ya se había subido en la ronda anterior
    db.actualizar_metadata(
        eid,
        {"apt_r1_archivos_subidos": True, "apt_correcciones_count": 0},
        actor="test",
    )
    db.cambiar_estado(eid, "apt_correcciones", actor="test")
    replies.clear()

    router.handle(sender_phone=operator_phone, text="APROBAR FLAGS-2026")

    exp = db.obtener_expediente(eid)
    meta = json.loads(exp["metadata_json"])
    assert meta.get("apt_r1_archivos_subidos") is False, \
        "debe limpiar flag para forzar re-upload"
    assert meta.get("apt_correcciones_count") == 1, \
        "debe incrementar contador de correcciones"


def test_aprobar_correcciones_invalida_firma_digital(router, db, replies, operator_phone):
    """APROBAR desde APT_CORRECCIONES invalida la confirmación de firma digital
    anterior para que el workflow solicite una nueva firma con los archivos
    corregidos, evitando que pase a PRESENTADO_APT_R1 sin nueva FD."""
    eid = db.crear_expediente(
        numero_expediente="FD-2026",
        tipo_plano="segregacion",
        nombre_topografo="X",
        telefono_cliente="+50688881111",
    )
    # Crear y confirmar firma_digital_r1 (simula primera ronda completada)
    aid = db.crear_accion_pendiente(
        expediente_id=eid, tipo_accion="firma_digital_r1",
        descripcion="Confirmar FD", actor="test",
    )
    db.resolver_accion(aid, "confirmada", actor="test")
    # Verificar que está confirmada antes del APROBAR
    assert db.accion_confirmada(
        expediente_id=eid, tipo_accion="firma_digital_r1",
        dentro_de_segundos=3600,
    ) is not None

    db.cambiar_estado(eid, "apt_correcciones", actor="test")
    replies.clear()

    router.handle(sender_phone=operator_phone, text="APROBAR FD-2026")

    # Ahora NO debe aparecer como confirmada (fue invalidada)
    assert db.accion_confirmada(
        expediente_id=eid, tipo_accion="firma_digital_r1",
        dentro_de_segundos=3600,
    ) is None, "firma_digital_r1 anterior debe quedar invalidada"


def test_aprobar_correcciones_incluye_recordatorio_anverso(
    router, db, replies, operator_phone
):
    """La respuesta del APROBAR desde correcciones debe incluir el aviso
    sobre el anverso firmado y corregido."""
    eid = db.crear_expediente(
        numero_expediente="AV-2026",
        tipo_plano="segregacion",
        nombre_topografo="X",
        telefono_cliente="+50688881111",
    )
    db.cambiar_estado(eid, "apt_correcciones", actor="test")
    replies.clear()

    router.handle(sender_phone=operator_phone, text="APROBAR AV-2026")

    msg = replies[0][1]
    assert "anverso" in msg.lower(), "debe mencionar el anverso"
    assert "corregido" in msg.lower() or "correg" in msg.lower()


def test_aprobar_no_halt_rechaza(router, db, replies, operator_phone):
    eid = db.crear_expediente(
        numero_expediente="NH-2026",
        tipo_plano="segregacion",
        nombre_topografo="X",
        telefono_cliente="+50688881111",
    )
    # Estado RECIBIDO no es halt
    handled = router.handle(sender_phone=operator_phone, text="APROBAR NH-2026")

    assert handled
    exp = db.obtener_expediente(eid)
    assert exp["estado_actual"] == "recibido"  # sin cambio
    assert "no requiere aprobación" in replies[0][1].lower()


def test_aprobar_no_existe(router, replies, operator_phone):
    handled = router.handle(sender_phone=operator_phone, text="APROBAR ZZZ-2026")

    assert handled
    assert "no encontrado" in replies[0][1].lower()


# ============================================================
# RECHAZAR
# ============================================================


def test_rechazar_cancela(router, db, replies, operator_phone):
    eid = db.crear_expediente(
        numero_expediente="REC-2026",
        tipo_plano="segregacion",
        nombre_topografo="X",
        telefono_cliente="+50688881111",
    )
    handled = router.handle(sender_phone=operator_phone, text="RECHAZAR REC-2026")

    assert handled
    exp = db.obtener_expediente(eid)
    assert exp["estado_actual"] == "cancelado"
    assert exp["cancelado"] == 1


def test_rechazar_terminal_falla(router, db, replies, operator_phone):
    eid = db.crear_expediente(
        numero_expediente="TER-2026",
        tipo_plano="segregacion",
        nombre_topografo="X",
        telefono_cliente="+50688881111",
    )
    db.cambiar_estado(eid, "entregado", actor="test")
    replies.clear()

    handled = router.handle(sender_phone=operator_phone, text="RECHAZAR TER-2026")

    assert handled
    assert "terminal" in replies[0][1].lower()
    exp = db.obtener_expediente(eid)
    assert exp["estado_actual"] == "entregado"  # sin cambio


# ============================================================
# RESUMEN
# ============================================================


def test_resumen_vacio(router, replies, operator_phone):
    handled = router.handle(sender_phone=operator_phone, text="RESUMEN")

    assert handled
    msg = replies[0][1]
    assert "Sin expedientes activos" in msg or "📊" in msg


def test_resumen_lista_activos_del_dia(router, db, replies, operator_phone):
    db.crear_expediente(
        numero_expediente="A-2026",
        tipo_plano="segregacion",
        nombre_topografo="X",
        telefono_cliente="+50688881111",
    )
    eid_b = db.crear_expediente(
        numero_expediente="B-2026",
        tipo_plano="rectificacion",
        nombre_topografo="Y",
        telefono_cliente="+50688881111",
    )
    db.cambiar_estado(eid_b, "apt_correcciones", actor="test")  # halt
    replies.clear()

    router.handle(sender_phone=operator_phone, text="RESUMEN")

    msg = replies[0][1]
    assert "A-2026" in msg
    assert "B-2026" in msg
    assert "[HALT]" in msg  # B-2026 en halt
    assert "🟢" in msg or "🔴" in msg


def test_resumen_excluye_terminales(router, db, replies, operator_phone):
    eid = db.crear_expediente(
        numero_expediente="ENT-2026",
        tipo_plano="segregacion",
        nombre_topografo="X",
        telefono_cliente="+50688881111",
    )
    db.cambiar_estado(eid, "entregado", actor="test")
    replies.clear()

    router.handle(sender_phone=operator_phone, text="RESUMEN")

    msg = replies[0][1]
    assert "ENT-2026" not in msg


# ============================================================
# AYUDA
# ============================================================


def test_ayuda(router, replies, operator_phone):
    handled = router.handle(sender_phone=operator_phone, text="AYUDA")

    assert handled
    msg = replies[0][1]
    assert "ESTADO" in msg
    assert "APROBAR" in msg
    assert "RECHAZAR" in msg
    assert "NUEVO" in msg


def test_ayuda_alias_help(router, replies, operator_phone):
    handled = router.handle(sender_phone=operator_phone, text="HELP")

    assert handled
    assert "comandos" in replies[0][1].lower()


# ============================================================
# delegación / no-comando
# ============================================================


def test_mensaje_libre_no_es_comando(router, replies, operator_phone):
    handled = router.handle(sender_phone=operator_phone, text="hola buenos días")

    assert handled is False
    assert replies == []


def test_si_no_es_comando(router, replies, operator_phone):
    """SI/NO no son comandos — deben caer al flujo de confirmaciones."""
    handled = router.handle(sender_phone=operator_phone, text="SI")

    assert handled is False


# ============================================================
# integración: WhatsAppAgent._procesar_notificacion → router
# ============================================================


def test_agente_dispatches_comando_al_router(db, fake_creds, drive_agent, operator_phone):
    """Verifica el dispatch end-to-end por _procesar_notificacion sin
    llamadas a Green API real."""
    from src.agents.whatsapp_agent import WhatsAppAgent
    from src.agents.whatsapp_commands import WhatsAppCommandRouter

    agent = WhatsAppAgent(db, fake_creds)
    sent: list[tuple[str, str]] = []
    # Reemplazar enviar_mensaje para evitar HTTP a Green API
    agent.enviar_mensaje = lambda phone, msg: (
        sent.append((phone, msg)) or "fake-msg-id"
    )

    router = WhatsAppCommandRouter(
        db=db,
        credentials=fake_creds,
        drive_agent=drive_agent,
        reply_fn=agent.enviar_mensaje,
    )
    agent.set_command_router(router)

    notif = {
        "receiptId": 1,
        "body": {
            "typeWebhook": "incomingMessageReceived",
            "senderData": {"chatId": f"{operator_phone}@c.us"},
            "messageData": {
                "typeMessage": "textMessage",
                "textMessageData": {"textMessage": "AYUDA"},
            },
        },
    }

    handled = agent._procesar_notificacion(notif)

    assert handled is True
    assert len(sent) == 1
    phone, msg = sent[0]
    assert phone == operator_phone
    assert "comandos" in msg.lower()


def test_agente_dispatches_si_no_a_accion_pendiente(db, fake_creds, drive_agent):
    """Un SI de un cliente debe resolver una accion_pendiente (sin pasar
    por command router)."""
    from src.agents.whatsapp_agent import WhatsAppAgent
    from src.agents.whatsapp_commands import WhatsAppCommandRouter

    eid = db.crear_expediente(
        numero_expediente="SI-2026",
        tipo_plano="segregacion",
        nombre_topografo="X",
        telefono_cliente=f"+{CLIENT_PHONE}",
    )
    accion_id = db.crear_accion_pendiente(
        expediente_id=eid,
        tipo_accion="pago_cliente",
        descripcion="¿Confirma pago?",
    )

    agent = WhatsAppAgent(db, fake_creds)
    agent.enviar_mensaje = lambda phone, msg: "fake-msg-id"
    router = WhatsAppCommandRouter(
        db=db, credentials=fake_creds,
        drive_agent=drive_agent, reply_fn=agent.enviar_mensaje,
    )
    agent.set_command_router(router)

    notif = {
        "receiptId": 1,
        "body": {
            "typeWebhook": "incomingMessageReceived",
            "senderData": {"chatId": f"{CLIENT_PHONE}@c.us"},
            "messageData": {
                "typeMessage": "textMessage",
                "textMessageData": {"textMessage": "SI"},
            },
        },
    }

    handled = agent._procesar_notificacion(notif)

    assert handled is True
    accion = db.ultima_accion(expediente_id=eid, tipo_accion="pago_cliente")
    assert accion["estado"] == "confirmada"
    assert accion["whatsapp_response"] == "SI"


# ============================================================
# PAGAR
# ============================================================

def test_pagar_sin_argumento_rechaza(router, replies, operator_phone):
    router.handle(sender_phone=operator_phone, text="PAGAR")
    assert replies and "Uso:" in replies[-1][1]


def test_pagar_expediente_inexistente(router, replies, operator_phone):
    router.handle(sender_phone=operator_phone, text="PAGAR EXP-NOPE")
    assert replies and "no encontrado" in replies[-1][1].lower()


def test_pagar_avanza_estado_a_enteros_pagados(router, db, replies, operator_phone):
    """PAGAR <num> <numero_entero> avanza a ENTEROS_PAGADOS y guarda el número."""
    import json
    from src.models.estado import Estado
    eid = db.crear_expediente(
        numero_expediente="PAG-2026",
        tipo_plano="segregacion",
        nombre_topografo="T",
        telefono_cliente="50688880001",
        actor="test",
    )
    db.cambiar_estado(eid, Estado.FORMATO_VALIDADO.value, actor="test")

    router.handle(sender_phone=operator_phone, text="PAGAR PAG-2026 20261234")

    exp = db.obtener_expediente(eid)
    assert exp["estado_actual"] == Estado.ENTEROS_PAGADOS.value
    meta = json.loads(exp["metadata_json"] or "{}")
    assert meta.get("numero_entero") == "20261234"
    assert meta.get("entero_confirmado") is True
    assert any("registrado" in r[1].lower() or "entero" in r[1].lower()
               for r in replies)


def test_pagar_guarda_numero_entero_en_metadata(router, db, replies, operator_phone):
    """PAGAR <num> <num_entero> guarda numero_entero y entero_confirmado en metadata."""
    import json
    from src.models.estado import Estado
    eid = db.crear_expediente(
        numero_expediente="PAG-COMP-2026",
        tipo_plano="segregacion",
        nombre_topografo="T",
        telefono_cliente="50688880001",
        actor="test",
    )
    db.cambiar_estado(eid, Estado.FORMATO_VALIDADO.value, actor="test")

    router.handle(
        sender_phone=operator_phone,
        text="PAGAR PAG-COMP-2026 20261234",
    )

    exp = db.obtener_expediente(eid)
    meta = json.loads(exp["metadata_json"] or "{}")
    assert meta.get("numero_entero") == "20261234"
    assert meta.get("entero_confirmado") is True


def test_pagar_estado_incorrecto_muestra_advertencia(router, db, replies, operator_phone):
    """PAGAR en estado no válido devuelve advertencia."""
    from src.models.estado import Estado
    eid = db.crear_expediente(
        numero_expediente="PAG-ESTADO-2026",
        tipo_plano="segregacion",
        nombre_topografo="T",
        telefono_cliente="50688880001",
        actor="test",
    )
    # Poner en ENTEROS_PAGADOS — PAGAR ya no aplica
    db.cambiar_estado(eid, Estado.ENTEROS_PAGADOS.value, actor="test")

    router.handle(sender_phone=operator_phone, text="PAGAR PAG-ESTADO-2026 20261234")

    assert replies
    assert "⚠" in replies[-1][1] or "estado" in replies[-1][1].lower()


def test_pagar_resuelve_accion_pendiente_registrar_entero(router, db, replies, operator_phone):
    """PAGAR resuelve la accion_pendiente 'registrar_entero' si existe."""
    from src.models.estado import Estado
    eid = db.crear_expediente(
        numero_expediente="PAG-AP-2026",
        tipo_plano="segregacion",
        nombre_topografo="T",
        telefono_cliente="50688880001",
        actor="test",
    )
    db.cambiar_estado(eid, Estado.FORMATO_VALIDADO.value, actor="test")
    db.crear_accion_pendiente(
        expediente_id=eid,
        tipo_accion="registrar_entero",
        descripcion="Registrar número de entero BCR",
        actor="test",
    )

    router.handle(sender_phone=operator_phone, text="PAGAR PAG-AP-2026 20261234")

    accion = db.ultima_accion(expediente_id=eid, tipo_accion="registrar_entero")
    assert accion["estado"] == "confirmada"


def test_pagar_sin_numero_entero_pide_numero(router, db, replies, operator_phone):
    """PAGAR <num> sin número de entero → mensaje solicitando el número BCR."""
    from src.models.estado import Estado
    eid = db.crear_expediente(
        numero_expediente="PAG-NOENT-2026",
        tipo_plano="segregacion",
        nombre_topografo="T",
        telefono_cliente="50688880001",
        actor="test",
    )
    db.cambiar_estado(eid, Estado.FORMATO_VALIDADO.value, actor="test")

    router.handle(sender_phone=operator_phone, text="PAGAR PAG-NOENT-2026")

    assert replies
    last = replies[-1][1]
    # Debe pedir el número de entero
    assert "entero" in last.lower() or "falta" in last.lower() or "número" in last.lower()


def test_pagar_numero_entero_acepta_formato_con_guiones(router, db, replies, operator_phone):
    """PAGAR <num> <num_entero_con_guiones> acepta formatos de entero con guiones."""
    import json
    from src.models.estado import Estado
    eid = db.crear_expediente(
        numero_expediente="PAG-ENT-2026",
        tipo_plano="segregacion",
        nombre_topografo="T",
        telefono_cliente="50688880001",
        actor="test",
    )
    db.cambiar_estado(eid, Estado.FORMATO_VALIDADO.value, actor="test")

    router.handle(
        sender_phone=operator_phone,
        text="PAGAR PAG-ENT-2026 2026-1234-5",
    )

    exp = db.obtener_expediente(eid)
    meta = json.loads(exp["metadata_json"] or "{}")
    assert meta.get("numero_entero") == "2026-1234-5"
    assert exp["estado_actual"] == Estado.ENTEROS_PAGADOS.value


def test_pagar_no_autorizado_rechaza(router, replies):
    """Número sin autorización recibe rechazo."""
    router.handle(sender_phone="50699990001", text="PAGAR EXP-NOPE")
    assert replies and "autorizado" in replies[-1][1].lower()


# ============================================================
# AUTORIZAR DRIVE
# ============================================================

def test_autorizar_sin_subcomando_rechaza(router, replies, operator_phone):
    router.handle(sender_phone=operator_phone, text="AUTORIZAR")
    assert replies and "Uso:" in replies[-1][1]


def test_autorizar_subcomando_incorrecto_rechaza(router, replies, operator_phone):
    router.handle(sender_phone=operator_phone, text="AUTORIZAR MEGA")
    assert replies and "Uso:" in replies[-1][1]


def test_autorizar_drive_sin_client_secret_informa(router, replies, operator_phone, fake_creds):
    """Sin CRED_GOOGLE_OAUTH configurado → mensaje de error explicativo."""
    # fake_creds no tiene google-oauth por defecto
    router.handle(sender_phone=operator_phone, text="AUTORIZAR DRIVE")
    assert replies
    last = replies[-1][1]
    assert "client_secret" in last.lower() or "oauth" in last.lower()


def test_autorizar_drive_con_client_secret_envia_inicio(
    router, replies, operator_phone, fake_creds
):
    """Con CRED_GOOGLE_OAUTH configurado → envía mensaje de inicio y lanza async."""
    from unittest.mock import patch
    fake_creds.set_secret("google-oauth", '{"installed": {"client_id": "fake"}}')

    with patch.object(router.drive, "authorize_async") as mock_auth:
        router.handle(sender_phone=operator_phone, text="AUTORIZAR DRIVE")

    assert replies
    assert "Drive" in replies[-1][1] or "browser" in replies[-1][1].lower() or "autorización" in replies[-1][1].lower()
    mock_auth.assert_called_once()


# ============================================================
# CORREGIR
# ============================================================

def test_corregir_sin_argumento_rechaza(router, replies, operator_phone):
    router.handle(sender_phone=operator_phone, text="CORREGIR")
    assert replies
    last = replies[-1][1]
    assert "Uso:" in last or "❌" in last


def test_corregir_insuficientes_argumentos_rechaza(router, replies, operator_phone):
    """CORREGIR <num> <campo> — sin viejo y nuevo → error."""
    router.handle(sender_phone=operator_phone, text="CORREGIR SEG-2026-001 area")
    assert replies
    last = replies[-1][1]
    assert "❌" in last or "argumento" in last.lower() or "Uso:" in last


def test_corregir_expediente_inexistente(router, replies, operator_phone):
    router.handle(sender_phone=operator_phone, text="CORREGIR EXP-NOPE area 1200 1300")
    assert replies and "no encontrado" in replies[-1][1].lower()


def test_corregir_sin_dwg_registrado(router, db, replies, operator_phone):
    """CORREGIR cuando no hay DWG en BD → aviso sin crash."""
    eid = db.crear_expediente(
        numero_expediente="CORR-NODWG-2026",
        tipo_plano="segregacion",
        nombre_topografo="T",
        telefono_cliente="50688880001",
        actor="test",
    )
    router.handle(
        sender_phone=operator_phone,
        text="CORREGIR CORR-NODWG-2026 area 1200.50 1250.75",
    )
    assert replies
    last = replies[-1][1]
    assert "⚠" in last or "no hay" in last.lower() or "dwg" in last.lower()


def test_corregir_texto_encontrado_aplica_correccion(
    router, db, replies, operator_phone, tmp_path
):
    """CORREGIR con DWG registrado + texto encontrado → modifica y confirma."""
    from pathlib import Path
    from unittest.mock import patch, MagicMock
    from src.utils.dwg_corrector import ResultadoCorreccion

    # Crear expediente con un DWG fake registrado
    eid = db.crear_expediente(
        numero_expediente="CORR-OK-2026",
        tipo_plano="segregacion",
        nombre_topografo="T",
        telefono_cliente="50688880001",
        actor="test",
    )
    # Crear archivo DXF "real" en disco (contenido fake)
    dwg_file = tmp_path / "plano.dxf"
    dwg_file.write_bytes(b"DXF fake")
    db.registrar_archivo(
        expediente_id=eid,
        nombre_original="plano.dxf",
        tipo_archivo="anverso",
        ruta_local=str(dwg_file),
        fase="campo",
        sha256="a" * 64,
        actor="test",
    )

    corr_file = tmp_path / "plano_corr.dxf"
    resultado_mock = ResultadoCorreccion(
        modificado=True,
        archivo_corregido=corr_file,
        aplicadas=[("1200.50", "1250.75")],
    )
    with patch(
        "src.utils.dwg_corrector.aplicar_correcciones",
        return_value=resultado_mock,
    ):
        router.handle(
            sender_phone=operator_phone,
            text="CORREGIR CORR-OK-2026 area 1200.50 1250.75",
        )

    assert replies
    last = replies[-1][1]
    assert "✅" in last
    assert "1200.50" in last or "1250.75" in last


def test_corregir_texto_no_encontrado_avisa(
    router, db, replies, operator_phone, tmp_path
):
    """CORREGIR con DWG registrado pero texto no encontrado → aviso al operador."""
    from pathlib import Path
    from unittest.mock import patch
    from src.utils.dwg_corrector import ResultadoCorreccion

    eid = db.crear_expediente(
        numero_expediente="CORR-NOFOUND-2026",
        tipo_plano="segregacion",
        nombre_topografo="T",
        telefono_cliente="50688880001",
        actor="test",
    )
    dwg_file = tmp_path / "plano2.dxf"
    dwg_file.write_bytes(b"DXF fake")
    db.registrar_archivo(
        expediente_id=eid,
        nombre_original="plano2.dxf",
        tipo_archivo="anverso",
        ruta_local=str(dwg_file),
        fase="campo",
        sha256="b" * 64,
        actor="test",
    )

    resultado_mock = ResultadoCorreccion(
        modificado=False,
        no_encontradas=[("1200.50", "1250.75")],
    )
    with patch(
        "src.utils.dwg_corrector.aplicar_correcciones",
        return_value=resultado_mock,
    ):
        router.handle(
            sender_phone=operator_phone,
            text="CORREGIR CORR-NOFOUND-2026 area 1200.50 1250.75",
        )

    assert replies
    last = replies[-1][1]
    assert "⚠" in last or "no se encontró" in last.lower() or "1200.50" in last


def test_corregir_actualiza_metadata_historial(
    router, db, replies, operator_phone, tmp_path
):
    """CORREGIR exitoso guarda historial en metadata_json."""
    import json
    from pathlib import Path
    from unittest.mock import patch
    from src.utils.dwg_corrector import ResultadoCorreccion

    eid = db.crear_expediente(
        numero_expediente="CORR-META-2026",
        tipo_plano="segregacion",
        nombre_topografo="T",
        telefono_cliente="50688880001",
        actor="test",
    )
    dwg_file = tmp_path / "plano3.dxf"
    dwg_file.write_bytes(b"DXF fake")
    db.registrar_archivo(
        expediente_id=eid,
        nombre_original="plano3.dxf",
        tipo_archivo="anverso",
        ruta_local=str(dwg_file),
        fase="campo",
        sha256="c" * 64,
        actor="test",
    )

    corr_file = tmp_path / "plano3_corr.dxf"
    resultado_mock = ResultadoCorreccion(
        modificado=True,
        archivo_corregido=corr_file,
        aplicadas=[("viejo", "nuevo")],
    )
    with patch(
        "src.utils.dwg_corrector.aplicar_correcciones",
        return_value=resultado_mock,
    ):
        router.handle(
            sender_phone=operator_phone,
            text="CORREGIR CORR-META-2026 finca viejo nuevo",
        )

    exp = db.obtener_expediente(eid)
    meta = json.loads(exp.get("metadata_json") or "{}")
    historial = meta.get("correcciones_dwg_aplicadas", [])
    assert len(historial) == 1
    assert historial[0]["campo"] == "finca"
    assert historial[0]["de"] == "viejo"
    assert historial[0]["a"] == "nuevo"


def test_corregir_no_autorizado_rechaza(router, replies):
    """Número sin autorización recibe rechazo."""
    router.handle(sender_phone="50699990002", text="CORREGIR EXP-001 area 1 2")
    assert replies and "autorizado" in replies[-1][1].lower()



# ============================================================
# DEBUG <expediente>
# ============================================================


def test_debug_expediente_existente(router, db, replies, operator_phone):
    """DEBUG muestra estado interno del bot para un expediente."""
    import json
    eid = db.crear_expediente(
        numero_expediente="DBG-2026-001",
        tipo_plano="segregacion",
        nombre_topografo="Luis Test",
        telefono_cliente="50611112222",
        actor="test",
    )
    # Agregar progreso simulado
    db.actualizar_metadata(eid, {
        "apt_tramite": "1258126",
        "apt_estado": "enviado_cfia",
        "apt_progreso": {
            "secciones": {"bP1": True, "bP2": True, "bP6": True},
            "archivos_subidos": ["anverso", "entero"],
        },
        "apt_discrepancias_rnp": [
            {"tipo": "rnp_tse_mismatch", "contexto": "propietario"},
        ],
        "apt_anomalias": [
            {"ts": "2026-05-11T10:00:00", "descripcion": "test anomalía"},
        ],
    }, actor="test")

    handled = router.handle(sender_phone=operator_phone, text="DEBUG DBG-2026-001")
    assert handled
    body = replies[-1][1]
    # Verifica que el mensaje contiene los datos clave
    assert "DBG-2026-001" in body
    assert "1258126" in body
    assert "enviado_cfia" in body
    assert "bP1" in body
    assert "anverso" in body
    assert "rnp_tse_mismatch" in body
    assert "test anomalía" in body or "anom" in body.lower()


def test_debug_expediente_no_encontrado(router, replies, operator_phone):
    """DEBUG con expediente inexistente devuelve error claro."""
    router.handle(sender_phone=operator_phone, text="DEBUG INEXISTENTE-2026")
    assert replies and "no encontrado" in replies[-1][1].lower()


def test_debug_sin_argumento(router, replies, operator_phone):
    """DEBUG sin número devuelve uso."""
    router.handle(sender_phone=operator_phone, text="DEBUG")
    assert replies and "Uso" in replies[-1][1]
