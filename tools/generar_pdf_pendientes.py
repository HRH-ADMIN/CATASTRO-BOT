"""Genera un PDF con los items pendientes de probar en el bot.

Items ordenados por:
  1. Crítico para cerrar el ciclo (R2/R3, visado muni, inscripción)
  2. Validación de features ya implementadas pero no probadas en vivo
  3. Edge cases que aparecerán con más planos

USO:
  python tools/generar_pdf_pendientes.py
  → genera ./pendientes_de_probar.pdf
"""
from __future__ import annotations
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
)


# ─── Datos ────────────────────────────────────────────────────────────

ITEMS_PENDIENTES = [
    # ── CRÍTICOS — cerrar el ciclo end-to-end ──
    {
        "id": 1,
        "titulo": "R2/R3 — ciclo de correcciones CFIA",
        "categoria": "CRÍTICO — cerrar ciclo",
        "depende_de": "Esperar respuesta CFIA (~7 días) de cualquiera de los 6 trámites enviados (1257831, 1257881, 1258052, 1258126).",
        "descripcion": (
            "Cuando CFIA marca un plano como 'Defectuoso' o 'Calificación con observaciones', "
            "el bot debe: (a) detectar el cambio via scheduler apt-sync-estados, (b) descargar la minuta "
            "y la imagen-minuta del portal APT, (c) presentar las correcciones al operador via WhatsApp, "
            "(d) aplicar correcciones automáticas al DWG (si son de texto), (e) re-empacar y enviar R2, "
            "(f) repetir si CFIA devuelve R3."
        ),
        "como_probar": [
            "Esperar respuesta CFIA real de algún trámite enviado",
            "Verificar que apt-sync-estados detecta el cambio de estado",
            "Verificar que el bot baja la minuta y la presenta correctamente",
            "Probar correcciones automáticas vs correcciones que requieren intervención",
            "Subir R2 y validar que CFIA lo recibe correctamente",
        ],
        "componentes": "src/agents/apt_agent.py · src/agents/minuta_agent.py · src/utils/dwg_corrector.py · scheduler tasks.py",
        "test_unitario": "✅ Existen tests con mocks. Falta validación end-to-end.",
    },
    {
        "id": 2,
        "titulo": "Visado municipal (San Ramón)",
        "categoria": "CRÍTICO — cerrar ciclo",
        "depende_de": "Esperar respuesta de Muni San Ramón al plano FELIPE_TIOS (RDF-2026-004, segregación enviada al CFIA el 11-05-2026).",
        "descripcion": (
            "Para segregaciones y reuniones de fincas, después de CFIA viene la municipalidad. "
            "Flujo esperado: (a) topógrafo envía el Google Form, (b) Muni responde por correo a "
            "mgamboa@sanramondigital.net con: APROBADO, RECHAZADO, o aviso de MOROSIDAD, (c) bot parsea "
            "el correo via IMAP, (d) actualiza estado, (e) si morosidad: solicita pago al cliente, recibe "
            "comprobante, lo reenvía a la Muni, (f) si aprobado: pasa a paso final."
        ),
        "como_probar": [
            "Enviar el Google Form de FELIPE_TIOS cuando CFIA acepte (~7 días)",
            "Esperar respuesta de Muni (puede ser horas o días)",
            "Verificar que el IMAP poll detecta el correo nuevo",
            "Validar el parser para cada caso (aprobado/rechazado/morosidad)",
            "Si morosidad: probar el sub-flujo de pago + reenvío",
        ],
        "componentes": "src/agents/municipality_agent.py · IMAP poll del scheduler",
        "test_unitario": "✅ Tests con mocks. Falta validación end-to-end con correo real.",
    },
    {
        "id": 3,
        "titulo": "Inscripción final + descarga plano firmado",
        "categoria": "CRÍTICO — cerrar ciclo",
        "depende_de": "CFIA marca el plano como 'Público e Inscrito' (típicamente 1-3 semanas después de la primera presentación).",
        "descripcion": (
            "Cuando el plano queda Inscrito, el bot debe: (a) descargar el PDF firmado digitalmente "
            "del portal APT, (b) guardarlo en data/files/.../03_Catastrado/, (c) notificar al "
            "topógrafo que el plano está listo para entrega, (d) avisar al cliente cuando lo reciba "
            "físicamente, (e) marcar estado ENTREGADO."
        ),
        "como_probar": [
            "Esperar a que primer plano se marque Inscrito",
            "Verificar que apt-sync-estados detecta el cambio",
            "Verificar descarga automática del PDF inscrito",
            "Validar flujo de notificación al cliente",
            "Probar comando RECIBIR cuando el cliente confirma",
        ],
        "componentes": "src/agents/apt_agent.py · src/workflows/base_workflow.py (paso INSCRITO)",
        "test_unitario": "✅ Tests existen. Falta validación con plano real inscrito.",
    },

    # ── ALTA prioridad — validación live de features ──
    {
        "id": 4,
        "titulo": "Auto-extracción con Claude Vision",
        "categoria": "ALTA — validar live",
        "depende_de": "Configurar ANTHROPIC_API_KEY en Windows Credential Manager.",
        "descripcion": (
            "Probar el extractor completo end-to-end contra los 4 expedientes reales subidos "
            "(ROGRANJ, OMAR_2026, ROVUELT, FELIPE_TIOS). El bot debe leer plano.pdf + registro.png + "
            "entero.pdf, armar datos_apt con reglas de oficina aplicadas, y producir el mismo seed "
            "(o uno mejor) que se hizo manualmente."
        ),
        "como_probar": [
            "Configurar: python -c \"from src.core.credential_manager import CredentialManager; CredentialManager().set_secret('anthropic-api', 'sk-ant-...')\"",
            "Correr: python tools/extraer_datos_apt.py RDF-2026-002",
            "Comparar el JSON resultante con el seed_datos_apt_omar.py manual",
            "Repetir para los 4 expedientes (002, 003, 004 + uno nuevo)",
            "Validar advertencias y campos con baja confianza",
        ],
        "componentes": "src/utils/plano_vision_extractor.py · src/utils/seed_builder.py · tools/extraer_datos_apt.py",
        "test_unitario": "✅ 33 tests con mocks de Anthropic API. Falta live test.",
    },
    {
        "id": 5,
        "titulo": "OCR fallback (pypdf+regex) sin Vision",
        "categoria": "ALTA — validar live",
        "depende_de": "Configurar entorno sin ANTHROPIC_API_KEY para forzar el fallback.",
        "descripcion": (
            "Cuando no hay API key o la API falla, el bot debe caer a pypdf+regex y extraer lo que "
            "pueda del cajetín. Validar qué campos recupera bien (área, protocolo, entero, identificador) "
            "y cuáles no (coordenadas, nombre propietario)."
        ),
        "como_probar": [
            "Desconfigurar ANTHROPIC_API_KEY: $env:ANTHROPIC_API_KEY=''",
            "Correr: python tools/extraer_datos_apt.py RDF-2026-002",
            "Verificar que el extractor cae a pypdf",
            "Listar campos recuperados vs faltantes",
            "Validar que las advertencias señalan campos no extraídos",
        ],
        "componentes": "src/utils/plano_vision_extractor.py (función extract_cajetin_con_fallback)",
        "test_unitario": "✅ 4 tests con mocks. Falta live test sin API key.",
    },
    {
        "id": 6,
        "titulo": "Healthcheck server + watchdog Chrome",
        "categoria": "ALTA — validar live",
        "depende_de": "Bot corriendo en modo servicio Windows.",
        "descripcion": (
            "El healthcheck server escucha en localhost:9223/health y devuelve JSON con el estado del "
            "sistema. El watchdog detecta si Chrome del bot crashea y lo relanza automáticamente."
        ),
        "como_probar": [
            "Instalar el servicio: python -m src.service.windows_service install",
            "Arrancarlo: python -m src.service.windows_service start",
            "Verificar healthcheck: curl http://localhost:9223/health",
            "Matar Chrome del bot manualmente: taskkill /F /IM chrome.exe",
            "Verificar que el watchdog lo relanza dentro de 10 segundos",
            "Conectar un monitor externo (ej. UptimeRobot) al endpoint",
        ],
        "componentes": "src/utils/healthcheck.py · src/service/windows_service.py",
        "test_unitario": "✅ 14 tests con mocks. Falta validación en modo servicio real.",
    },
    {
        "id": 7,
        "titulo": "Backup BD automático + restore",
        "categoria": "ALTA — validar live",
        "depende_de": "Dejar el scheduler corriendo durante 24h+.",
        "descripcion": (
            "El scheduler hace backup a las 2:00 UTC diariamente (job 'db-backup' usando VACUUM INTO). "
            "Validar que crea el archivo, que la rotación funciona, y que el archivo es restaurable."
        ),
        "como_probar": [
            "Verificar que data/backups/ existe y contiene catastro-YYYY-MM-DD.db",
            "Forzar backup manual: python tools/backup_db.py",
            "Listar: python tools/backup_db.py --solo-listar",
            "Probar restore: cp data/backups/catastro-...db data/test-restore.db; sqlite3 data/test-restore.db '.tables'",
            "Verificar que la rotación mantiene 30 backups",
        ],
        "componentes": "src/scheduler/tasks.py (job db-backup) · src/utils/backup_db.py · tools/backup_db.py",
        "test_unitario": "✅ 13 tests del módulo backup_db. Falta validación en modo servicio.",
    },
    {
        "id": 8,
        "titulo": "Dashboard + alertas proactivas",
        "categoria": "ALTA — validar live",
        "depende_de": "Acumular 20+ planos para tener datos significativos.",
        "descripcion": (
            "El dashboard agrega métricas (estados, tipos, topógrafos, tiempos, discrepancias frecuentes, "
            "anomalías recientes). Las alertas proactivas detectan patrones (ej. 3+ anomalías en mismo "
            "contexto → posible cambio en APT)."
        ),
        "como_probar": [
            "Correr con BD actual: python tools/dashboard.py",
            "Verificar que las métricas son consistentes con listar_planos",
            "Provocar anomalías deliberadas para activar alertas proactivas",
            "Probar formato JSON: python tools/dashboard.py --json | jq .",
            "Probar CSV import en Excel: python tools/dashboard.py --csv > dashboard.csv",
        ],
        "componentes": "src/utils/metricas.py · tools/dashboard.py",
        "test_unitario": "✅ 16 tests. Falta validación con BD producción larga.",
    },

    # ── MEDIA — features secundarias ──
    {
        "id": 9,
        "titulo": "Validación derrotero ZIP",
        "categoria": "MEDIA — validar live",
        "depende_de": "Cualquier plano nuevo con Derrotero.zip.",
        "descripcion": (
            "Cuando se sube un Derrotero.zip, el validador compara área y coordenadas del shapefile "
            "con lo declarado en el cajetín. Si difieren >5% → error bloqueante. Falta validar el "
            "comportamiento con shapefiles malformados o de otra proyección."
        ),
        "como_probar": [
            "Subir un plano nuevo con Derrotero.zip válido — validar que pasa OK",
            "Probar con un ZIP sin .prj — validar advertencia",
            "Probar con coords en otra proyección (CR05 en vez de CRTM05)",
            "Probar con áreas que difieren >5% — validar error bloqueante",
        ],
        "componentes": "src/utils/derrotero_validator.py · hook en seed_builder.py",
        "test_unitario": "✅ 13 tests + live test contra FELIPE_TIOS exitoso.",
    },
    {
        "id": 10,
        "titulo": "Detección auto tipo BD (segregación/rectificación/etc.)",
        "categoria": "MEDIA — validar live",
        "depende_de": "Próximo plano nuevo, sin declarar tipo manualmente.",
        "descripcion": (
            "El detector lee el texto del cajetín y del registro para decidir si es segregación, "
            "rectificación, reunión, info posesoria o finca completa. Validar contra casos reales."
        ),
        "como_probar": [
            "Para próximo plano, NO declarar tipo al crear expediente",
            "Correr el extractor Vision",
            "Verificar que detectar_tipo_plano() devuelve el tipo correcto",
            "Probar casos ambiguos (mezcla de palabras clave)",
        ],
        "componentes": "src/utils/tipo_plano_detector.py",
        "test_unitario": "✅ 19 tests. Falta integración con flujo de creación de expediente.",
    },
    {
        "id": 11,
        "titulo": "Memoria operador (APT REGLA / APT IGNORA)",
        "categoria": "MEDIA — validar live",
        "depende_de": "WhatsApp activo y comandos enviados por operador.",
        "descripcion": (
            "Operador puede agregar reglas operativas y silenciar discrepancias repetitivas via "
            "WhatsApp. Validar persistencia y que las ignoras silencian las notificaciones."
        ),
        "como_probar": [
            "Enviar: APT REGLA Para planos en RIO CUARTO incluir mapa adjunto",
            "Verificar respuesta del bot con ID #X",
            "Listar: APT REGLAS",
            "Provocar una discrepancia con cédula 2-0440-0388 (Grace/Amalia)",
            "Enviar: APT IGNORA 2-0440-0388",
            "Provocar la misma discrepancia → verificar que NO notifica",
            "Verificar que sigue en metadata.apt_discrepancias_rnp (auditoría)",
            "Desactivar: APT OLVIDA <id>",
        ],
        "componentes": "src/agents/whatsapp_commands.py · src/utils/memoria_operador.py · tabla apt_memoria_operador",
        "test_unitario": "✅ 18 tests. Falta validación con WhatsApp real.",
    },
    {
        "id": 12,
        "titulo": "Comando WhatsApp DEBUG <expediente>",
        "categoria": "MEDIA — validar live",
        "depende_de": "WhatsApp activo.",
        "descripcion": (
            "Operador pide DEBUG <numero> y recibe dump del estado interno del bot: progreso bP1-bP7, "
            "trámite, discrepancias, anomalías, snapshots disponibles, reglas activas."
        ),
        "como_probar": [
            "Enviar: DEBUG RDF-2026-004",
            "Verificar respuesta con todos los campos esperados",
            "Probar con expediente sin contrato APT",
            "Probar con expediente enviado al CFIA",
            "Probar con expediente que tenga discrepancias/anomalías",
        ],
        "componentes": "src/agents/whatsapp_commands.py (_handle_debug)",
        "test_unitario": "✅ Tests indirectos. Falta validación end-to-end con WhatsApp real.",
    },
    {
        "id": 13,
        "titulo": "Email digest semanal",
        "categoria": "MEDIA — validar live",
        "depende_de": "Lunes a las 7am UTC con scheduler corriendo.",
        "descripcion": (
            "El digest envía resumen del dashboard a los admins con correo configurado en "
            "usuarios.correo_apt. Validar que llega correctamente formateado."
        ),
        "como_probar": [
            "Configurar email de admin: UPDATE usuarios SET correo_apt='...' WHERE rol='admin'",
            "Forzar envío inmediato: python tools/catastro_bot.py enviar-digest",
            "Verificar que el correo llega y se ve bien (texto + HTML)",
            "Esperar al lunes 7am y validar que el scheduler lo dispara automáticamente",
        ],
        "componentes": "src/utils/email_digest.py · scheduler tasks.py (próximo)",
        "test_unitario": "✅ 13 tests. Falta validación con SMTP real.",
    },
    {
        "id": 14,
        "titulo": "2FA para acciones sensibles",
        "categoria": "MEDIA — validar live",
        "depende_de": "Integración con whatsapp_commands para acciones ENVIAR CFIA / RECHAZAR / borrado.",
        "descripcion": (
            "El módulo TwoFactorAuth genera códigos 6-digit con TTL 5 min para autorizar acciones "
            "críticas. Falta integrarlo en los handlers de WhatsApp y luego validarlo en vivo."
        ),
        "como_probar": [
            "Integrar en _handle_rechazar / _handle_apt_enviar_cfia (TODO)",
            "Operador envía: ENVIAR CFIA RDF-2026-005",
            "Bot responde con código de 6 dígitos",
            "Operador envía: CONFIRMAR <codigo>",
            "Verificar que el bot ejecuta la acción",
            "Probar timeout: esperar 5+ min sin confirmar → código expira",
            "Probar código incorrecto 5 veces → invalidación por max_intentos",
        ],
        "componentes": "src/utils/two_factor_auth.py · src/agents/whatsapp_commands.py (integración pendiente)",
        "test_unitario": "✅ 16 tests del módulo. Falta integración + validación live.",
    },
    {
        "id": 15,
        "titulo": "Rate limiting WhatsApp (Green API)",
        "categoria": "MEDIA — validar live",
        "depende_de": "Bot enviando muchos mensajes simultáneamente (ej. discrepancia masiva).",
        "descripcion": (
            "El RateLimiter previene throttling de Green API. Falta integrarlo en WhatsAppAgent."
        ),
        "como_probar": [
            "Integrar GREEN_API_LIMITER en WhatsAppAgent.enviar_mensaje (TODO)",
            "Provocar burst de 20 notificaciones simultáneas",
            "Verificar que se envían a ritmo controlado (~5/seg) sin throttling",
            "Revisar stats del limiter: total_acquired, total_waited_sec",
        ],
        "componentes": "src/utils/rate_limiter.py · src/agents/whatsapp_agent.py (integración pendiente)",
        "test_unitario": "✅ 11 tests del módulo. Falta integración + carga real.",
    },

    # ── BAJA — features experimentales ──
    {
        "id": 16,
        "titulo": "Selector resilience (fallback por NAME)",
        "categoria": "BAJA — validar live",
        "depende_de": "Que APT cambie un selector / haga deploy nuevo.",
        "descripcion": (
            "Si APT renombra un ID (#dllprotocolo → #ddlProtocolo), el bot debe seguir funcionando "
            "vía el fallback `[name='Profesional.Protocolo']`. Difícil de probar hasta que pase."
        ),
        "como_probar": [
            "Simular: monkey-patch un ID en una página de prueba → cambiarlo a otro",
            "Verificar que el bot usa el fallback automáticamente",
            "Revisar logs: 'usando fallback X' debería aparecer",
            "En producción: la primera vez que APT cambie algo, ver si el bot lo absorbe",
        ],
        "componentes": "src/agents/apt_agent.py (FALLBACKS_CAMPOS_APT + _localizar_resilient)",
        "test_unitario": "✅ 11 tests. Falta evento real (cuando APT cambie).",
    },
    {
        "id": 17,
        "titulo": "Snapshot post-mortem en anomalías",
        "categoria": "BAJA — validar live",
        "depende_de": "Que ocurra una anomalía real durante el llenado.",
        "descripcion": (
            "Cuando APTAnomalyError dispara, el bot guarda HTML+PNG+modal+meta en data/anomaly_snapshots/. "
            "Falta validar que los snapshots son útiles para debugging post-mortem."
        ),
        "como_probar": [
            "Provocar una anomalía deliberada (modal de error)",
            "Inspeccionar data/anomaly_snapshots/<exp>/<ts>/",
            "Validar que page.html abre en navegador y muestra el estado real",
            "Validar que screenshot.png muestra lo visible al momento de fallar",
            "Validar que modal_actual.json captura el swal de error",
        ],
        "componentes": "src/utils/anomaly_snapshot.py · src/agents/apt_anomaly_handler.py",
        "test_unitario": "✅ 13 tests con mocks. Falta validación con anomalía real.",
    },
    {
        "id": 18,
        "titulo": "State machine + resume desde crash",
        "categoria": "BAJA — validar live",
        "depende_de": "Crash del bot a mitad del llenado del plano (improbable pero posible).",
        "descripcion": (
            "Si el bot muere mientras llena el plano, al reiniciar debe leer apt_progreso + verificar "
            "estado live en APT, y retomar desde la sección siguiente sin reintentar las verdes."
        ),
        "como_probar": [
            "Llenar bP1+bP2+bP3 → verificar que apt_progreso se persiste",
            "Matar el proceso a mitad de bP4: taskkill /F /PID <pid>",
            "Re-correr el runner",
            "Verificar que muestra 'SKIP bP1', 'SKIP bP2', 'SKIP bP3' y empieza desde bP4",
        ],
        "componentes": "src/utils/apt_progreso.py · tools/run_apt_plano_auto.py",
        "test_unitario": "✅ 19 tests. Falta simular crash real mid-llenado.",
    },
    {
        "id": 19,
        "titulo": "Shadow mode con auto_if_clean",
        "categoria": "BAJA — validar live",
        "depende_de": "Validación de 10+ planos exitosos antes de subir trust.",
        "descripcion": (
            "Cambiar BOT_SAVE_MODE_CONTRATO de 'manual' a 'auto_if_clean' una vez que tengamos "
            "confianza alta. El bot guardará automáticamente cuando no haya discrepancias."
        ),
        "como_probar": [
            "Después de 10 planos exitosos en modo manual, cambiar config:",
            "$env:BOT_SAVE_MODE_CONTRATO='auto_if_clean'",
            "Correr un plano limpio → debe guardar automáticamente",
            "Correr un plano con discrepancia → debe pausar",
            "Volver a manual si algo sale mal: unset env",
        ],
        "componentes": "src/utils/shadow_mode.py · config/settings.py · tools/run_apt_crear_auto.py",
        "test_unitario": "✅ 14 tests del shadow_mode. Falta uso en producción.",
    },
    {
        "id": 20,
        "titulo": "Logs estructurados JSON",
        "categoria": "BAJA — validar live",
        "depende_de": "Necesidad de ingest a herramientas externas (ELK, jq).",
        "descripcion": (
            "Cambiar formato de logs a JSON line para facilitar parsing con jq/log-aggregators."
        ),
        "como_probar": [
            "Setear: $env:CATASTRO_LOG_FORMAT='json'",
            "Reiniciar el bot",
            "Correr cualquier acción",
            "Verificar logs en logs/catastro-bot.log → cada línea es JSON válido",
            "Probar: Get-Content logs/catastro-bot.log | jq '.level=\"ERROR\"'",
        ],
        "componentes": "src/utils/logger.py (JsonFormatter)",
        "test_unitario": "✅ 10 tests. Falta uso real con herramientas externas.",
    },
    {
        "id": 21,
        "titulo": "CLI unificado catastro-bot",
        "categoria": "BAJA — validar live",
        "depende_de": "Uso diario por el operador.",
        "descripcion": (
            "Un solo entry point reemplaza los ~25 tools/*.py. Falta que el operador lo adopte como "
            "hábito y reporte qué subcomandos faltan o tienen UX mejorable."
        ),
        "como_probar": [
            "Reemplazar todos los aliases / scripts batch que usen tools/*.py por catastro-bot <sub>",
            "Documentar en CLAUDE.md o README la sintaxis nueva",
            "Probar autocompletado bash/PowerShell (opcional)",
            "Recoger feedback del operador después de 1 semana de uso",
        ],
        "componentes": "tools/catastro_bot.py",
        "test_unitario": "✅ 4 tests del router. Falta validación de UX.",
    },
]


# ─── PDF builder ──────────────────────────────────────────────────────

def _color_categoria(cat: str):
    if "CRÍTICO" in cat:
        return colors.HexColor("#D32F2F")  # rojo
    if "ALTA" in cat:
        return colors.HexColor("#F57C00")  # naranja
    if "MEDIA" in cat:
        return colors.HexColor("#1976D2")  # azul
    return colors.HexColor("#757575")      # gris


def construir_pdf(output: Path) -> None:
    doc = SimpleDocTemplate(
        str(output),
        pagesize=letter,
        leftMargin=0.6 * inch, rightMargin=0.6 * inch,
        topMargin=0.6 * inch, bottomMargin=0.6 * inch,
    )

    estilos = getSampleStyleSheet()
    h1 = ParagraphStyle(
        "H1", parent=estilos["Heading1"],
        fontSize=18, textColor=colors.HexColor("#0D47A1"),
        spaceAfter=12,
    )
    h2 = ParagraphStyle(
        "H2", parent=estilos["Heading2"],
        fontSize=13, spaceAfter=6, spaceBefore=12,
    )
    h3 = ParagraphStyle(
        "H3", parent=estilos["Heading3"],
        fontSize=11, spaceAfter=4, fontName="Helvetica-Bold",
    )
    normal = ParagraphStyle(
        "Body", parent=estilos["BodyText"],
        fontSize=10, leading=13, spaceAfter=4,
    )
    bullet = ParagraphStyle(
        "Bullet", parent=normal, leftIndent=16, bulletIndent=4,
        spaceAfter=2,
    )
    badge = ParagraphStyle(
        "Badge", parent=normal,
        textColor=colors.white, fontSize=9, fontName="Helvetica-Bold",
        alignment=1,
    )

    story = []

    # Portada
    story.append(Paragraph("catastro-bot", h1))
    story.append(Paragraph(
        "Pendientes de probar — orden de prioridad", h2,
    ))
    story.append(Paragraph(
        f"Generado: {datetime.now().strftime('%Y-%m-%d %H:%M')}", normal,
    ))
    story.append(Paragraph(
        f"<b>Total items:</b> {len(ITEMS_PENDIENTES)} &nbsp;&nbsp; "
        "<b>Tests unitarios:</b> 1064 pasando &nbsp;&nbsp; "
        "<b>Planos enviados al CFIA:</b> 6", normal,
    ))
    story.append(Spacer(1, 8))

    # Resumen por categoría
    por_cat: dict[str, int] = {}
    for it in ITEMS_PENDIENTES:
        cat = it["categoria"].split(" — ")[0]
        por_cat[cat] = por_cat.get(cat, 0) + 1
    data = [["Categoría", "Cantidad", "Significa"]]
    significados = {
        "CRÍTICO": "Eventos externos: esperar CFIA/Muni para validar",
        "ALTA": "Features ya implementadas; falta uso/validación live",
        "MEDIA": "Validación con datos reales en producción",
        "BAJA": "Experimental o requiere evento específico (raro)",
    }
    for cat in ("CRÍTICO", "ALTA", "MEDIA", "BAJA"):
        if cat in por_cat:
            data.append([cat, str(por_cat[cat]), significados[cat]])
    tabla = Table(data, colWidths=[1.2*inch, 0.8*inch, 4.5*inch])
    tabla.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0D47A1")),
        ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
        ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",   (0, 0), (-1, -1), 9),
        ("GRID",       (0, 0), (-1, -1), 0.5, colors.grey),
        ("ALIGN",      (1, 0), (1, -1), "CENTER"),
        ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(tabla)

    story.append(Spacer(1, 14))
    story.append(Paragraph(
        "<b>Cómo usar este documento:</b> los items están ordenados por urgencia. "
        "Items 1-3 son críticos para cerrar el ciclo end-to-end (esperan eventos externos). "
        "Items 4-8 son validación live de features ya construidas. 9-21 son secundarios.",
        normal,
    ))
    story.append(PageBreak())

    # Items detallados
    for it in ITEMS_PENDIENTES:
        # Header con badge de categoría
        cat_color = _color_categoria(it["categoria"])
        header_data = [[
            Paragraph(f"<font color='white'><b>#{it['id']}</b></font>", badge),
            Paragraph(f"<b>{it['titulo']}</b>", h3),
            Paragraph(
                f"<font color='white'><b>{it['categoria']}</b></font>",
                badge,
            ),
        ]]
        header_tabla = Table(header_data,
                             colWidths=[0.5*inch, 5.0*inch, 2.0*inch])
        header_tabla.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, 0), cat_color),
            ("BACKGROUND", (2, 0), (2, 0), cat_color),
            ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING",  (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING",   (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING",(0, 0), (-1, -1), 4),
        ]))
        story.append(header_tabla)
        story.append(Spacer(1, 4))

        # Cuerpo (escapar HTML en textos libres)
        def _esc(s: str) -> str:
            return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        story.append(Paragraph(
            f"<b>Depende de:</b> {_esc(it['depende_de'])}", normal,
        ))
        story.append(Paragraph(
            f"<b>Descripción:</b> {_esc(it['descripcion'])}", normal,
        ))
        story.append(Paragraph("<b>Cómo probar:</b>", normal))
        for paso in it["como_probar"]:
            story.append(Paragraph(f"&bull; {_esc(paso)}", bullet))
        story.append(Paragraph(
            f"<b>Componentes:</b> <font face='Courier' size='9'>"
            f"{_esc(it['componentes'])}</font>", normal,
        ))
        story.append(Paragraph(
            f"<b>Estado tests:</b> {_esc(it['test_unitario'])}", normal,
        ))
        story.append(Spacer(1, 10))

    # Footer
    story.append(PageBreak())
    story.append(Paragraph("Resumen de capas de defensa del bot", h2))
    capas = [
        "1. Vision auto-extract → llena seed desde PDFs/PNGs",
        "2. Seed builder + advertencias → marca datos dudosos",
        "3. Pre-flight (sin red) → bloquea errores estructurales antes de Chrome",
        "4. Auto-firma SSO si cert BCR activo → sesión APT sin re-PIN",
        "5. Resume desde progreso saved+live → skip secciones ya verdes",
        "6. Multi-topógrafo → per-user protocolo y correo",
        "7. Shadow mode → controla qué se auto-guarda",
        "8. Memoria operador → silencia falsos positivos persistentemente",
        "9. Selector resilience → sobrevive cambios de ID en APT",
        "10. Strict modal validation → APTAnomalyError si APT rechaza",
        "11. Circuit breaker → MessageBox + WhatsApp + snapshot",
        "12. Healthcheck + watchdog → detecta Chrome caído y relanza",
        "13. Backup BD diario → protección contra corrupción/pérdida",
        "14. Rate limiting → previene throttling Green API",
        "15. 2FA acciones sensibles → confirma envío/rechazo con código",
        "16. Dashboard + alertas proactivas → visibilidad operativa",
        "17. Email digest semanal → resumen sin abrir WhatsApp",
        "18. DEBUG WhatsApp → self-service del operador",
        "19. CLI unificado → un solo entry point para todo",
        "20. Logs JSON → ingest a herramientas externas",
    ]
    for capa in capas:
        capa_esc = capa.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        story.append(Paragraph(capa_esc, bullet))

    doc.build(story)


def main() -> int:
    import io as _io
    import sys as _sys
    # UTF-8 stdout para que los emojis no rompan
    _sys.stdout = _io.TextIOWrapper(_sys.stdout.buffer, encoding="utf-8",
                                     errors="replace")
    output = Path("pendientes_de_probar.pdf")
    construir_pdf(output)
    print(f"✅ PDF generado: {output.resolve()}")
    print(f"   {len(ITEMS_PENDIENTES)} items ordenados por prioridad")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
