"""Genera un PDF con la documentación completa de cómo funciona catastro-bot.

Incluye:
  - Vista general del sistema multi-agente
  - Cada agente con responsabilidad, triggers y capacidades
  - Workflows (state machine)
  - Sistema de detección (discrepancias + anomalías)
  - 20 capas de defensa
  - Schedulers
  - Lifecycle completo de un plano
  - Interfaces del operador
  - Seguridad
  - Métricas

USO:
  python tools/generar_pdf_arquitectura.py
  → genera ./arquitectura_catastro_bot.pdf
"""
from __future__ import annotations
import io
import sys
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak,
    KeepTogether,
)


def _esc(s: str) -> str:
    """Escapa caracteres HTML para reportlab."""
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;"))


# ─── Estilos ───────────────────────────────────────────────────────────

def _build_styles():
    s = getSampleStyleSheet()
    return {
        "titulo":  ParagraphStyle(
            "Titulo", parent=s["Heading1"],
            fontSize=24, textColor=colors.HexColor("#0D47A1"),
            spaceAfter=18, alignment=1,
        ),
        "subtitulo": ParagraphStyle(
            "Subtitulo", parent=s["Heading2"],
            fontSize=12, textColor=colors.HexColor("#1976D2"),
            alignment=1, spaceAfter=24,
        ),
        "h1": ParagraphStyle(
            "H1", parent=s["Heading1"],
            fontSize=18, textColor=colors.HexColor("#0D47A1"),
            spaceBefore=20, spaceAfter=10, keepWithNext=True,
        ),
        "h2": ParagraphStyle(
            "H2", parent=s["Heading2"],
            fontSize=14, textColor=colors.HexColor("#1565C0"),
            spaceBefore=14, spaceAfter=6, keepWithNext=True,
        ),
        "h3": ParagraphStyle(
            "H3", parent=s["Heading3"],
            fontSize=11, fontName="Helvetica-Bold",
            spaceBefore=8, spaceAfter=3, keepWithNext=True,
        ),
        "body": ParagraphStyle(
            "Body", parent=s["BodyText"],
            fontSize=10, leading=14, spaceAfter=5, alignment=0,
        ),
        "bullet": ParagraphStyle(
            "Bullet", parent=s["BodyText"],
            fontSize=10, leading=13, leftIndent=14,
            bulletIndent=4, spaceAfter=2,
        ),
        "code": ParagraphStyle(
            "Code", parent=s["BodyText"],
            fontSize=8.5, leading=10, fontName="Courier",
            backColor=colors.HexColor("#F5F5F5"),
            borderPadding=4, leftIndent=8, rightIndent=8,
            spaceBefore=4, spaceAfter=8,
        ),
        "small": ParagraphStyle(
            "Small", parent=s["BodyText"],
            fontSize=8.5, leading=11, textColor=colors.HexColor("#555"),
        ),
        "callout": ParagraphStyle(
            "Callout", parent=s["BodyText"],
            fontSize=10, leading=13,
            borderColor=colors.HexColor("#1976D2"),
            borderWidth=1, borderPadding=8,
            backColor=colors.HexColor("#E3F2FD"),
            spaceBefore=6, spaceAfter=10,
        ),
    }


# ─── Datos ──────────────────────────────────────────────────────────────

AGENTES = [
    {
        "nombre": "WhatsAppAgent",
        "modulo": "src/agents/whatsapp_agent.py",
        "rol": "Envío/recepción de mensajes vía Green API",
        "actua_cuando": [
            "Llega webhook de Green API con mensaje entrante",
            "Otro agente llama enviar_mensaje(telefono, texto)",
        ],
        "defensas": [
            "Rate limiter (5 msgs/seg con burst) — evita throttling",
            "Validador de teléfonos CR (formato +506XXXXXXXX)",
            "Filtro de roles (admin/topografo/asistente) en cada comando",
        ],
    },
    {
        "nombre": "FileManager",
        "modulo": "src/agents/file_manager.py",
        "rol": "Detección y validación de archivos en carpetas de expedientes",
        "actua_cuando": [
            "Topógrafo deja un PDF/PNG/ZIP en data/files/.../01_Campo/",
            "Watchdog dispara el evento de filesystem",
        ],
        "defensas": [
            "Valida formato (PDF B&N, tamaño < 600KB, ZIP con .shp+.dbf+.shx)",
            "Extrae número de entero del comprobante BCR (regex 660XXXXXXX)",
            "Sugiere comando PAGAR al operador con datos auto-detectados",
            "Notifica al operador con identificador legible (nombre proyecto)",
        ],
    },
    {
        "nombre": "APTAgent",
        "modulo": "src/agents/apt_agent.py",
        "rol": "Automatización del portal CFIA APT 3.0 (Playwright sobre Chrome CDP)",
        "actua_cuando": [
            "Operador envía 'APT CREAR <exp>' → llena contrato bC1-bC8",
            "Operador envía 'APT PLANO <exp>' → llena bP1-bP7 + sube archivos",
            "Scheduler apt-sync-estados (cada 30 min) → consulta estado APT",
            "CFIA marca 'Defectuoso' → bot baja minuta y dispara R2",
        ],
        "defensas": [
            "Auto-firma SSO sin re-PIN si cert BCR sigue activo",
            "Selector resilience: si #id no matchea, prueba [name='X']",
            "Validación TSE vs registro con override (PIÑEIRO vs PI?EIRO)",
            "Detección discrepancias: protocolo viejo, cédula incompleta, TSE mismatch",
            "Validación estricta de modales post-acción",
            "Circuit breaker: APTAnomalyError detiene el ciclo y notifica",
            "Resume desde apt_progreso si crashea a mitad",
        ],
    },
    {
        "nombre": "MunicipalityAgent",
        "modulo": "src/agents/municipality_agent.py",
        "rol": "Visado municipal (San Ramón) vía SMTP/IMAP",
        "actua_cuando": [
            "CFIA acepta un plano de segregación/reunión → inicia muni-flow",
            "IMAP poll detecta correo nuevo de mgamboa@sanramondigital.net",
            "Operador confirma pago de impuestos municipales",
        ],
        "defensas": [
            "Parser de respuestas Muni (APROBADO/RECHAZADO/MOROSIDAD)",
            "Sub-flujo de morosidad: solicita pago al cliente, reenvía comprobante",
            "Reintentos automáticos si SMTP falla",
        ],
    },
    {
        "nombre": "RnpAgent",
        "modulo": "src/agents/rnp_agent.py",
        "rol": "Pago de enteros en rnpdigital.com (Playwright)",
        "actua_cuando": [
            "Estado entero_pendiente — actualmente NO se usa (pago manual BCR)",
            "Reserved para cuando se quiera automatizar el pago",
        ],
        "defensas": [
            "Confirmación WhatsApp previa antes de cualquier pago",
            "Validación de monto contra cálculo esperado",
        ],
    },
    {
        "nombre": "MinutaAgent",
        "modulo": "src/agents/minuta_agent.py",
        "rol": "Análisis de minutas e imagen-minutas de CFIA con Claude",
        "actua_cuando": [
            "CFIA marca plano como Defectuoso",
            "Bot baja la minuta del portal APT",
        ],
        "defensas": [
            "Modelo: claude-opus-4.7 con prompt caching",
            "Devuelve correcciones estructuradas: texto + imagen + tipo_error",
            "Sugerencias prácticas para el topógrafo",
        ],
    },
    {
        "nombre": "DriveAgent",
        "modulo": "src/agents/drive_agent.py",
        "rol": "Upload de archivos a Google Drive (stub actualmente)",
        "actua_cuando": [
            "PENDIENTE: cuando se implemente, al detectar archivos nuevos",
        ],
        "defensas": [
            "OAuth flow vía comando AUTORIZAR DRIVE (en código, falta wiring)",
        ],
    },
]


WORKFLOWS_ESTADOS = [
    ("RECIBIDO",                "Expediente recién creado, esperando archivos"),
    ("PAGO_CLIENTE_PENDIENTE",  "Cliente debe confirmar pago"),
    ("PAGO_CLIENTE_CONFIRMADO", "Pago confirmado, listo para validar archivos"),
    ("FORMATO_VALIDADO",        "Archivos validan formato (B&N, tamaño, shp)"),
    ("FORMATO_INVALIDO",        "HALT — archivos no cumplen requisitos"),
    ("ENTEROS_PAGADOS",         "Entero BCR registrado"),
    ("PRESENTADO_APT_R1",       "Contrato + plano enviados a CFIA"),
    ("APT_R1_RESPONDIO",        "CFIA respondió (revisar resultado)"),
    ("APT_CORRECCIONES",        "HALT — CFIA pide correcciones de texto"),
    ("APT_TRASLAPES",           "HALT — CFIA reporta traslape, requiere apelación"),
    ("APROBADO_R1",             "CFIA aprobó sin observaciones"),
    ("CARTA_AGUA_REQUERIDA",    "Solo segregación/reunión: pedir carta de agua"),
    ("CARTA_AGUA_OK",           "Carta de agua recibida"),
    ("LISTO_PAQUETE_MUNI",      "Preparar paquete para Muni"),
    ("FORMULARIO_MUNI_ENVIADO", "Google Form enviado a Muni"),
    ("AVISO_MUNI_RECIBIDO",     "Respuesta de Muni recibida"),
    ("PAGO_MUNI_CLIENTE_CONFIRMADO", "Cliente pagó impuestos muni"),
    ("CORREO_MUNI_ENVIADO",     "Comprobante enviado a Muni"),
    ("VISADO_APROBADO",         "Muni aprobó visado"),
    ("VISADO_RECHAZADO",        "HALT — Muni rechazó"),
    ("LISTO_APT_R2",            "Listo para presentar R2 a CFIA"),
    ("PRESENTADO_APT_R2",       "R2 en revisión"),
    ("INSCRITO",                "CFIA inscribió el plano"),
    ("INSCRITO_DESCARGADO",     "Plano firmado descargado por el bot"),
    ("ENTREGADO",               "Plano entregado físicamente al cliente"),
    ("CANCELADO",               "Terminal — operador rechazó"),
    ("ERROR",                   "Terminal — error no recuperable"),
]


DISCREPANCIAS = [
    ("rnp_tse_mismatch",
     "TSE devuelve nombre distinto al del registro RNP",
     "Para bC1 propietario: override + notifica. Para bP4 titular: INSERT→UPDATE."),
    ("registro_dato_incompleto",
     "Cédula mal formateada o cedula_registro_original declarada",
     "Registra discrepancia + notifica al topógrafo. Sigue procesando."),
    ("protocolo_diferente_al_activo",
     "El protocolo del cajetín NO es el último del dropdown CFIA",
     "Sugiere: continuación de contrato viejo (honorarios=0 + observaciones)."),
]


CAPAS = [
    ("01", "Watchdog filesystem",        "Detecta archivos nuevos en 01_Campo/"),
    ("02", "FileManager validador",      "Formato, tamaño, contenido"),
    ("03", "Vision auto-extract",        "Claude Sonnet 4.5 lee PDFs y PNGs"),
    ("04", "OCR fallback (pypdf)",       "Sin API key, regex sobre texto extraído"),
    ("05", "Seed builder + advertencias","Aplica reglas de oficina; marca dudosos"),
    ("06", "Pre-flight (sin red)",       "Polígono, área, cédula, honorarios"),
    ("07", "Auto-firma SSO",             "Cert BCR activo → no re-PIN"),
    ("08", "Resume saved+live",          "Skip secciones ya verdes"),
    ("09", "Multi-topógrafo",            "Per-user protocolo y correo CFIA"),
    ("10", "Shadow mode",                "manual / auto_if_clean / auto"),
    ("11", "Memoria operador",           "APT IGNORA silencia falsos positivos"),
    ("12", "Selector resilience",        "#id → [name='X'] si APT cambia"),
    ("13", "Strict modal validation",    "APTAnomalyError si modal raro"),
    ("14", "Circuit breaker",            "Snapshot + MessageBox + WhatsApp"),
    ("15", "Healthcheck + watchdog",     "HTTP :9223 + relanza Chrome"),
    ("16", "Backup BD diario",           "VACUUM INTO con rotación 30 días"),
    ("17", "Rate limiting Green API",    "5 msgs/seg con burst"),
    ("18", "2FA acciones sensibles",     "Códigos 6-digit con TTL 5 min"),
    ("19", "Dashboard + alertas",        "Métricas + detección de patrones"),
    ("20", "Logs JSON estructurados",    "Opt-in via CATASTRO_LOG_FORMAT=json"),
]


SCHEDULERS = [
    ("tick",                "60 segundos",     "Heartbeat del bot"),
    ("audit-verify",        "1 hora",          "Verifica integridad del audit log"),
    ("db-backup",           "02:00 UTC diario","VACUUM INTO + rotación 30 días"),
    ("stale-alert",         "6 horas",         "Alerta si expediente sin actividad >48h"),
    ("weekly-report",       "Lunes 7am UTC",   "Reporte semanal por WhatsApp"),
    ("correcciones-renotif","24 horas",        "Re-notifica correcciones pendientes"),
    ("apt-sync-estados",    "30 minutos",      "Consulta APT, dispara R2 si Defectuoso"),
]


LIFECYCLE = [
    ("1", "Crear expediente",
     "Operador via WhatsApp 'NUEVO PLANO ...' o CLI catastro-bot crear",
     "Bot crea expediente en BD + estructura de carpetas data/files/PROV/CANT/DIST/PROYECTO/",
     "RECIBIDO"),
    ("2", "Topógrafo sube archivos",
     "Coloca plano.pdf, planof.pdf, entero.pdf, Derrotero.zip, registro.png en 01_Campo/",
     "Watchdog → FileManager valida formato + extrae número entero",
     "ENTEROS_PAGADOS"),
    ("3", "Crear contrato APT",
     "Operador: 'APT CREAR <exp>' o catastro-bot apt-crear",
     "Vision extrae datos → seed → preflight → bot llena bC1-bC8 → PAUSA pre-guardar (modo manual)",
     "(pendiente GUARDAR)"),
    ("4", "Guardar contrato",
     "Operador revisa en Chrome → catastro-bot apt-guardar",
     "APT crea trámite. Bot persiste apt_tramite + dispara discrepancias notificaciones",
     "PRESENTADO_APT_R1"),
    ("5", "Llenar plano (bP1-bP7)",
     "Operador: 'APT PLANO <exp>' o catastro-bot apt-plano",
     "Bot llena 7 secciones; resume si crashea; circuit breaker si anomalía",
     "(7 secciones verdes)"),
    ("6", "Enviar al CFIA",
     "Operador click ENVIAR (manual por seguridad) o catastro-bot envia-cfia",
     "APT firma con BCR. Estado pasa a Calificación RN en portal",
     "PRESENTADO_APT_R1"),
    ("7", "CFIA califica (5-7 días)",
     "Scheduler apt-sync-estados consulta cada 30 min",
     "Si Defectuoso: baja minuta → MinutaAgent → operador → R2. Si Calificación RN: aprueba auto",
     "APT_R1_RESPONDIO"),
    ("8", "Visado municipal (solo segregación/reunión)",
     "Topógrafo envía Google Form. Bot espera correo IMAP de Muni",
     "Aprobado/Rechazado/Morosidad. Si morosidad: sub-flujo de pago",
     "VISADO_APROBADO"),
    ("9", "R2 al CFIA",
     "Bot empaca correcciones + visado y envía R2",
     "CFIA revisa de nuevo. Resultado típico: Inscrito",
     "PRESENTADO_APT_R2"),
    ("10","Inscripción + descarga",
     "CFIA marca Inscrito",
     "Bot baja PDF firmado a 03_Catastrado/, notifica al topógrafo",
     "INSCRITO_DESCARGADO"),
    ("11","Entrega al cliente",
     "Topógrafo entrega físicamente. WhatsApp: 'RECIBIR <exp>'",
     "Bot marca terminal. Recordatorio: 1 año para inscribir en RP",
     "ENTREGADO"),
]


COMANDOS_WHATSAPP = [
    ("NUEVO PLANO ...",      "Crear expediente (multilinea con campos)"),
    ("ESTADO <exp>",         "Estado actual + últimos cambios"),
    ("ESTADO APT <exp>",     "Consultar estado en portal CFIA"),
    ("APROBAR <exp>",        "Desbloquear expediente en estado halt"),
    ("RECHAZAR <exp>",       "Cancelar expediente"),
    ("BUSCAR <texto>",       "Buscar por número, topógrafo, cliente, cajetín"),
    ("LISTAR [filtros]",     "Listar planos con filtros opcionales"),
    ("SUBIR <exp>",          "Confirmar que archivos están listos"),
    ("RECIBIR <exp>",        "Marcar plano entregado al cliente"),
    ("RESUMEN",              "Expedientes activos del día"),
    ("DEBUG <exp>",          "Dump estado interno del bot"),
    ("PAGAR <exp> <entero>", "Registrar pago manual BCR"),
    ("CORREGIR <exp> ...",   "Aplicar corrección de texto al DWG"),
    ("APT SESION",           "Login con Firma Digital BCR"),
    ("APT CREAR <exp>",      "Crear contrato APT en portal"),
    ("APT TRAMITE <exp> <#>","Registrar número de trámite manual"),
    ("APT REGLA <texto>",    "Guardar nota operativa (sale en pre-flight)"),
    ("APT IGNORA <patrón>",  "Silenciar discrepancias matcheantes"),
    ("APT REGLAS / IGNORAS", "Listar memoria del operador"),
    ("APT OLVIDA <id>",      "Desactivar regla/ignora"),
    ("AVISAR <exp> <msg>",   "Mensaje al cliente del expediente"),
    ("AUTORIZAR DRIVE",      "OAuth Google Drive"),
    ("CERRAR APT",           "Liberar sesión Playwright"),
    ("AYUDA / HELP",         "Listado de comandos por rol"),
]


CLI_COMANDOS = [
    ("catastro-bot crear ...",          "Crear expediente + carpetas"),
    ("catastro-bot extraer <exp>",      "Vision auto-extract de los 3 archivos"),
    ("catastro-bot listar [<exp>]",     "Lista o detalle de expedientes"),
    ("catastro-bot dashboard",          "Métricas operativas (texto/JSON/CSV)"),
    ("catastro-bot backup",             "Backup manual de BD + rotación"),
    ("catastro-bot apt-crear <exp>",    "Llenar contrato (pausa antes guardar)"),
    ("catastro-bot apt-guardar <exp>",  "Click GUARDAR + capturar trámite"),
    ("catastro-bot apt-plano <exp>",    "Llenar bP1-bP7 con resume"),
    ("catastro-bot health",             "Estado JSON del sistema"),
    ("catastro-bot enviar-digest",      "Forzar digest semanal por email"),
]


# ─── Builder ────────────────────────────────────────────────────────────

def construir_pdf(output: Path) -> None:
    doc = SimpleDocTemplate(
        str(output), pagesize=letter,
        leftMargin=0.6 * inch, rightMargin=0.6 * inch,
        topMargin=0.6 * inch, bottomMargin=0.6 * inch,
    )
    st = _build_styles()
    story = []

    # ────────── Portada ──────────
    story.append(Spacer(1, 1.5 * inch))
    story.append(Paragraph("catastro-bot", st["titulo"]))
    story.append(Paragraph(
        "Documentación técnica — Cómo funciona, agentes, workflows y defensas",
        st["subtitulo"],
    ))
    story.append(Spacer(1, 0.5 * inch))

    stats = [
        ["Sistema multi-agente",      "7 agentes principales"],
        ["Workflows",                 f"{len(WORKFLOWS_ESTADOS)} estados con transiciones"],
        ["Capas de defensa",          f"{len(CAPAS)} layers de validación + recovery"],
        ["Jobs periódicos",           f"{len(SCHEDULERS)} schedulers"],
        ["Tests unitarios",           "1064 pasando"],
        ["Comandos WhatsApp",         f"{len(COMANDOS_WHATSAPP)} comandos · 3 roles"],
        ["Subcomandos CLI",           f"{len(CLI_COMANDOS)} en catastro-bot unificado"],
    ]
    stats_tabla = Table(stats, colWidths=[2.5*inch, 3.0*inch])
    stats_tabla.setStyle(TableStyle([
        ("FONTNAME",   (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE",   (0, 0), (-1, -1), 10),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#E3F2FD")),
        ("TEXTCOLOR",  (0, 0), (0, -1), colors.HexColor("#0D47A1")),
        ("FONTNAME",   (0, 0), (0, -1), "Helvetica-Bold"),
        ("GRID",       (0, 0), (-1, -1), 0.5, colors.HexColor("#BBDEFB")),
        ("LEFTPADDING",(0, 0), (-1, -1), 8),
        ("RIGHTPADDING",(0,0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING",(0,0),(-1, -1), 6),
    ]))
    story.append(stats_tabla)

    story.append(Spacer(1, 0.4 * inch))
    story.append(Paragraph(
        f"Generado: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        st["small"],
    ))
    story.append(PageBreak())

    # ────────── 1. Vista general ──────────
    story.append(Paragraph("1. Vista general", st["h1"]))
    story.append(Paragraph(
        "El bot es un sistema multi-agente donde cada componente tiene una "
        "responsabilidad clara. La coordinación se hace por eventos (archivos "
        "detectados, mensajes WhatsApp, cambios de estado APT), schedulers "
        "(APScheduler con 7 jobs periódicos) y estado compartido en BD "
        "(SQLite cifrada con SQLCipher).",
        st["body"],
    ))

    diagrama = """
                    ┌─────────────────────────────┐
                    │      OPERADOR (WhatsApp)    │
                    └──────────────┬──────────────┘
                                   │ Green API webhook
                    ┌──────────────▼──────────────┐
                    │  whatsapp_commands (router) │
                    │  17+ comandos · 3 roles     │
                    └──────────────┬──────────────┘
                                   │
        ┌──────────────────────────┼──────────────────────────┐
        │                          │                          │
        ▼                          ▼                          ▼
  ┌───────────┐             ┌──────────┐             ┌─────────────────┐
  │FileManager│             │ APTAgent │             │MunicipalityAgent│
  │ watchdog  │             │Playwright│             │   SMTP/IMAP     │
  └─────┬─────┘             └────┬─────┘             └────────┬────────┘
        │                        │                            │
        └──────────────┬─────────┴─────────────┬──────────────┘
                       │                       │
                ┌──────▼──────┐         ┌──────▼──────┐
                │  Database   │         │  Scheduler  │
                │ (SQLCipher) │         │  (7 jobs)   │
                └─────────────┘         └─────────────┘
    """
    story.append(Paragraph(f"<pre>{_esc(diagrama)}</pre>", st["code"]))

    # ────────── 2. Agentes ──────────
    story.append(Paragraph("2. Agentes principales", st["h1"]))
    for i, ag in enumerate(AGENTES, 1):
        story.append(Paragraph(
            f"2.{i}. {ag['nombre']}", st["h2"],
        ))
        story.append(Paragraph(
            f"<font face='Courier' size='9'>{_esc(ag['modulo'])}</font>",
            st["small"],
        ))
        story.append(Paragraph(
            f"<b>Rol:</b> {_esc(ag['rol'])}", st["body"],
        ))
        story.append(Paragraph("<b>Actúa cuando:</b>", st["body"]))
        for trigger in ag["actua_cuando"]:
            story.append(Paragraph(f"&bull; {_esc(trigger)}", st["bullet"]))
        story.append(Paragraph("<b>Defensas y características:</b>", st["body"]))
        for d in ag["defensas"]:
            story.append(Paragraph(f"&bull; {_esc(d)}", st["bullet"]))

    story.append(PageBreak())

    # ────────── 3. Workflows ──────────
    story.append(Paragraph("3. Workflows (state machine)", st["h1"]))
    story.append(Paragraph(
        "Cada expediente avanza por estados definidos en "
        "<font face='Courier' size='9'>src/models/estado.py</font>. "
        "Los workflows (<font face='Courier' size='9'>src/workflows/*.py</font>) "
        "deciden las transiciones según el tipo de plano y los eventos.",
        st["body"],
    ))

    story.append(Paragraph("Tabla de estados", st["h2"]))
    data = [["Estado", "Significado"]]
    for est, sig in WORKFLOWS_ESTADOS:
        data.append([est, sig])
    tabla = Table(data, colWidths=[2.2*inch, 5.0*inch])
    tabla.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0D47A1")),
        ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
        ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME",   (0, 1), (0, -1), "Courier-Bold"),
        ("FONTSIZE",   (0, 0), (-1, -1), 9),
        ("GRID",       (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING",(0, 0), (-1, -1), 6),
        ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#F5F5F5")),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),
         [colors.white, colors.HexColor("#F5F5F5")]),
    ]))
    story.append(tabla)

    story.append(Paragraph("Estados HALT (requieren APROBAR)", st["h2"]))
    halts = ["formato_invalido", "apt_correcciones", "apt_traslapes", "visado_rechazado"]
    for h in halts:
        story.append(Paragraph(
            f"&bull; <font face='Courier'>{_esc(h)}</font>", st["bullet"],
        ))

    story.append(Paragraph("Workflows por tipo de plano", st["h2"]))
    tipos_data = [
        ["Tipo de plano",       "Pasa por Muni?", "Workflow"],
        ["segregacion",         "SÍ",             "SegregacionWorkflow"],
        ["reunion_de_fincas",   "SÍ",             "ReunionDeFincasWorkflow"],
        ["finca_completa",      "SÍ",             "FincaCompletaWorkflow"],
        ["rectificacion",       "NO",             "RectificacionWorkflow"],
        ["informacion_posesoria","NO",            "InformacionPosesoriaWorkflow"],
    ]
    tipos_tabla = Table(tipos_data, colWidths=[2.0*inch, 1.5*inch, 3.0*inch])
    tipos_tabla.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0D47A1")),
        ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
        ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",   (0, 0), (-1, -1), 9),
        ("GRID",       (0, 0), (-1, -1), 0.5, colors.grey),
        ("ALIGN",      (1, 0), (1, -1), "CENTER"),
        ("LEFTPADDING",(0, 0), (-1, -1), 6),
    ]))
    story.append(tipos_tabla)
    story.append(PageBreak())

    # ────────── 4. Detección y notificaciones ──────────
    story.append(Paragraph("4. Sistema de detección y notificaciones", st["h1"]))

    story.append(Paragraph("4.1. Discrepancias (3 tipos)", st["h2"]))
    story.append(Paragraph(
        "Detectadas durante el llenado de APT. Se persisten en "
        "<font face='Courier' size='9'>metadata.apt_discrepancias_rnp[]</font> "
        "y se notifican al topógrafo + admins via WhatsApp (si el operador no "
        "las silenció previamente con APT IGNORA).",
        st["body"],
    ))
    disc_data = [["Tipo", "Cuándo se dispara", "Acción del bot"]]
    for t, c, a in DISCREPANCIAS:
        disc_data.append([t, c, a])
    disc_tabla = Table(disc_data, colWidths=[2.0*inch, 2.5*inch, 2.5*inch])
    disc_tabla.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F57C00")),
        ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
        ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME",   (0, 1), (0, -1), "Courier-Bold"),
        ("FONTSIZE",   (0, 0), (-1, -1), 8.5),
        ("GRID",       (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN",     (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING",(0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING",(0,0),(-1, -1), 4),
    ]))
    story.append(disc_tabla)

    story.append(Paragraph("4.2. Anomalías (Circuit breaker)", st["h2"]))
    story.append(Paragraph(
        "Cuando ocurre algo INESPERADO (modal de error, validación que no "
        "conocemos, selector que falló), el bot lanza "
        "<font face='Courier' size='9'>APTAnomalyError</font> y la cadena "
        "completa de circuit breaker se dispara:",
        st["body"],
    ))
    cadena = [
        "1. Snapshot automático (HTML + PNG + modal swal + metadata) en data/anomaly_snapshots/<exp>/<ts>/",
        "2. MessageBox modal CENTRADA en pantalla (Win32 user32.MessageBoxW + SystemModal)",
        "3. WhatsApp al topógrafo responsable (resuelto desde tabla usuarios)",
        "4. WhatsApp a todos los admins activos (sin duplicar topógrafo)",
        "5. Persiste en metadata.apt_anomalias[] para auditoría",
        "6. Log con stack trace completo",
        "7. El bot SALE (exit code 1) — NO sigue intentando",
    ]
    for paso in cadena:
        story.append(Paragraph(f"&bull; {_esc(paso)}", st["bullet"]))

    story.append(Paragraph("4.3. Pre-flight (sin red, antes de Chrome)", st["h2"]))
    story.append(Paragraph(
        "Validaciones que corren ANTES de abrir el navegador. Si fallan, ni "
        "siquiera se intenta el llenado:",
        st["body"],
    ))
    preflight = [
        "Cédula propietario formato (físico X-XXXX-XXXX, jurídico 3-XXX-XXXXXX)",
        "Polígono cerrado, sin auto-intersección, mínimo 3 vértices",
        "Centroide cae DENTRO del polígono",
        "Área cajetín coincide con área del derrotero ZIP (±1% warning, ±5% error)",
        "Honorarios=0 requiere exoneración=True Y observaciones llenas",
        "Tipo zona consistente con área (<2000m² urbano, ≥2000 rural)",
        "Provincia/cantón/distrito mapean a códigos APT (zero-padded)",
        "Plano a modificar tiene formato válido X-NNNNNNN-AAAA",
        "Número de entero parece de 9 dígitos",
    ]
    for chk in preflight:
        story.append(Paragraph(f"&bull; {_esc(chk)}", st["bullet"]))

    story.append(PageBreak())

    # ────────── 5. Capas de defensa ──────────
    story.append(Paragraph("5. 20 capas de defensa", st["h1"]))
    story.append(Paragraph(
        "El bot implementa <b>defense-in-depth</b>: si una capa falla, otra "
        "atrapa el error. El operador siempre recibe notificación de cualquier "
        "anomalía — el bot NUNCA toma decisiones silenciosas sobre datos críticos.",
        st["body"],
    ))
    capas_data = [["#", "Capa", "Función"]]
    for n, capa, func in CAPAS:
        capas_data.append([n, capa, func])
    capas_tabla = Table(capas_data, colWidths=[0.35*inch, 2.3*inch, 4.4*inch])
    capas_tabla.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1976D2")),
        ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
        ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME",   (1, 1), (1, -1), "Helvetica-Bold"),
        ("FONTSIZE",   (0, 0), (-1, -1), 9),
        ("GRID",       (0, 0), (-1, -1), 0.5, colors.grey),
        ("ALIGN",      (0, 0), (0, -1), "CENTER"),
        ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING",(0, 0), (-1, -1), 5),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),
         [colors.white, colors.HexColor("#F5F5F5")]),
    ]))
    story.append(capas_tabla)
    story.append(PageBreak())

    # ────────── 6. Schedulers ──────────
    story.append(Paragraph("6. Schedulers (jobs periódicos)", st["h1"]))
    story.append(Paragraph(
        "El bot corre 7 jobs en APScheduler. Estos son la fuente de cambios "
        "automáticos no iniciados por el operador.",
        st["body"],
    ))
    sched_data = [["Job", "Frecuencia", "Qué hace"]]
    for j, f, q in SCHEDULERS:
        sched_data.append([j, f, q])
    sched_tabla = Table(sched_data, colWidths=[1.9*inch, 1.6*inch, 3.7*inch])
    sched_tabla.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#388E3C")),
        ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
        ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME",   (0, 1), (0, -1), "Courier-Bold"),
        ("FONTSIZE",   (0, 0), (-1, -1), 9),
        ("GRID",       (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING",(0, 0), (-1, -1), 6),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),
         [colors.white, colors.HexColor("#F5F5F5")]),
    ]))
    story.append(sched_tabla)

    # ────────── 7. Lifecycle ──────────
    story.append(Paragraph("7. Lifecycle completo de un plano", st["h1"]))
    story.append(Paragraph(
        "Recorrido end-to-end desde que el topógrafo recibe un encargo hasta "
        "que entrega el plano inscrito al cliente:",
        st["body"],
    ))
    for n, paso, quien, que, estado in LIFECYCLE:
        story.append(Paragraph(f"Paso {n}: {_esc(paso)}", st["h3"]))
        story.append(Paragraph(f"<b>Quién/cuándo:</b> {_esc(quien)}", st["body"]))
        story.append(Paragraph(f"<b>Qué pasa:</b> {_esc(que)}", st["body"]))
        story.append(Paragraph(
            f"<b>Estado resultante:</b> <font face='Courier' size='9'>{_esc(estado)}</font>",
            st["body"],
        ))
        story.append(Spacer(1, 4))

    story.append(PageBreak())

    # ────────── 8. Comandos ──────────
    story.append(Paragraph("8. Interfaces del operador", st["h1"]))

    story.append(Paragraph("8.1. Comandos WhatsApp", st["h2"]))
    wa_data = [["Comando", "Descripción"]]
    for c, d in COMANDOS_WHATSAPP:
        wa_data.append([c, d])
    wa_tabla = Table(wa_data, colWidths=[2.6*inch, 4.6*inch])
    wa_tabla.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#7B1FA2")),
        ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
        ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME",   (0, 1), (0, -1), "Courier-Bold"),
        ("FONTSIZE",   (0, 0), (-1, -1), 9),
        ("GRID",       (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING",(0, 0), (-1, -1), 5),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),
         [colors.white, colors.HexColor("#F5F5F5")]),
    ]))
    story.append(wa_tabla)

    story.append(Paragraph("8.2. CLI unificado (catastro-bot)", st["h2"]))
    cli_data = [["Subcomando", "Descripción"]]
    for c, d in CLI_COMANDOS:
        cli_data.append([c, d])
    cli_tabla = Table(cli_data, colWidths=[2.6*inch, 4.6*inch])
    cli_tabla.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0D47A1")),
        ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
        ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME",   (0, 1), (0, -1), "Courier"),
        ("FONTSIZE",   (0, 0), (-1, -1), 9),
        ("GRID",       (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING",(0, 0), (-1, -1), 5),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),
         [colors.white, colors.HexColor("#F5F5F5")]),
    ]))
    story.append(cli_tabla)

    story.append(Paragraph("8.3. HTTP (monitoreo externo)", st["h2"]))
    story.append(Paragraph(
        "<b>GET</b> <font face='Courier' size='9'>"
        "http://localhost:9223/health</font>", st["body"],
    ))
    story.append(Paragraph(
        "&bull; 200 OK con JSON del estado si todo bien o hay anomalías recientes",
        st["bullet"],
    ))
    story.append(Paragraph(
        "&bull; 503 si componente crítico caído (Chrome CDP, BD, etc.)",
        st["bullet"],
    ))
    story.append(Paragraph(
        "Útil para conectar UptimeRobot, Pingdom, o un cron de Windows.",
        st["body"],
    ))

    story.append(PageBreak())

    # ────────── 9. Seguridad ──────────
    story.append(Paragraph("9. Seguridad por capas", st["h1"]))
    seg_data = [
        ["Capa",                "Mecanismo"],
        ["Datos en reposo",     "SQLite cifrada con SQLCipher AES-256-CBC + HMAC-SHA512"],
        ["Credenciales",        "Windows Credential Manager (no en disco)"],
        ["Audit log",           "Append-only con hash SHA-256 encadenado"],
        ["Confirmaciones cliente","TTL configurable (default 24h), expiración automática"],
        ["2FA acciones sensibles","Códigos 6-digit con TTL 5 min, max 5 intentos"],
        ["Anomalías",           "Snapshot post-mortem + circuit breaker"],
        ["WhatsApp",            "Validación de roles (admin/topografo/asistente)"],
        ["Pre-flight",          "Bloquea envíos a CFIA con datos inválidos"],
        ["Sesión APT",          "Vía Chrome CDP del usuario — token BCR no toca disco"],
        ["Webhook Green API",   "Validación HMAC (si está configurada)"],
    ]
    seg_tabla = Table(seg_data, colWidths=[2.0*inch, 5.2*inch])
    seg_tabla.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#C62828")),
        ("TEXTCOLOR",  (0, 0), (-1, 0), colors.white),
        ("FONTNAME",   (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",   (0, 0), (-1, -1), 9),
        ("GRID",       (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING",(0, 0), (-1, -1), 6),
        ("ROWBACKGROUNDS",(0,1),(-1,-1),
         [colors.white, colors.HexColor("#FFEBEE")]),
    ]))
    story.append(seg_tabla)

    # ────────── 10. Métricas ──────────
    story.append(Paragraph("10. Métricas y observabilidad", st["h1"]))
    story.append(Paragraph(
        "El bot recolecta métricas y las expone via "
        "<font face='Courier'>catastro-bot dashboard</font>:",
        st["body"],
    ))
    metricas = [
        "Total de expedientes activos",
        "Distribución por estado workflow",
        "Distribución por tipo de plano",
        "Distribución por topógrafo (multi-user)",
        "Distribución por estado APT (enviado_cfia, etc.)",
        "Tiempo promedio: creación → envío al CFIA",
        "Tasa de exoneración de honorarios",
        "Top discrepancias detectadas",
        "Últimas anomalías reportadas",
        "Alertas proactivas: ≥3 anomalías en mismo contexto → posible cambio en APT",
    ]
    for m in metricas:
        story.append(Paragraph(f"&bull; {_esc(m)}", st["bullet"]))

    story.append(Paragraph("Salidas disponibles", st["h2"]))
    salidas = [
        ("texto",     "Tabla coloreada en consola para revisión rápida"),
        ("--json",    "JSON estructurado para automatización / scripts"),
        ("--csv",     "Export importable en Excel para reportes manuales"),
        ("--alertas-solo", "Solo alertas proactivas (cron-friendly)"),
    ]
    for cmd, desc in salidas:
        story.append(Paragraph(
            f"&bull; <font face='Courier'>{_esc(cmd)}</font> — {_esc(desc)}",
            st["bullet"],
        ))

    # ────────── 11. Inteligencia ──────────
    story.append(Paragraph("11. Inteligencia del bot", st["h1"]))

    story.append(Paragraph("11.1. Vision (Claude Sonnet 4.5)", st["h2"]))
    story.append(Paragraph(
        "Extrae datos estructurados de los 3 documentos típicos:",
        st["body"],
    ))
    vision = [
        "Cajetín del plano: descripción, áreas, vértices, protocolo, número entero",
        "Información de registro: propietario, cédula, finca, naturaleza, plano previo",
        "Entero BCR: número, fecha, montos por timbre, descuentos",
        "Fallback automático a pypdf+regex si no hay ANTHROPIC_API_KEY",
    ]
    for v in vision:
        story.append(Paragraph(f"&bull; {_esc(v)}", st["bullet"]))

    story.append(Paragraph("11.2. Reglas de oficina codificadas", st["h2"]))
    reglas = [
        "Tipo zona: área <2000m² → URBANO, ≥2000 → RURAL",
        "Tipo uso: naturaleza 'café' → CULTIVOS_VARIOS (código 23 APT)",
        "Naturaleza siempre Equidad (2) por política de oficina",
        "Composición siempre unipersonal",
        "Tipo proyecto APT siempre 27 (Plano Simple)",
        "Tipo coordenada siempre 3 (CRTM05) — regla legal CR",
        "Honorarios: Decreto 17481-MOPT + ajuste fijo de ₡5000 por plano",
        "Protocolo activo: último del dropdown CFIA (per-topógrafo)",
        "Correo profesional: viene del cert BCR (read-only en APT)",
        "Cantón/distrito: zero-padded a 2 dígitos para APT",
        "Parcela urbana: letra A-E → códigos 5-10 (mapper)",
    ]
    for r in reglas:
        story.append(Paragraph(f"&bull; {_esc(r)}", st["bullet"]))

    story.append(Paragraph("11.3. Detección automática", st["h2"]))
    deteccion = [
        "Tipo BD desde texto del cajetín: 'ES PARTE DE' → segregación",
        "Discrepancias TSE/registro: override automático en propietario bC1",
        "Polígono inválido o área inconsistente con el shapefile",
        "Cédulas malformadas o truncadas en el registro",
        "Modales APT esperados vs inesperados",
        "Protocolo no-activo (continuación de contrato viejo)",
        "Diferencia >5% entre área cajetín y área del derrotero",
        "Patrones de anomalía recurrente (3+ en mismo contexto)",
    ]
    for d in deteccion:
        story.append(Paragraph(f"&bull; {_esc(d)}", st["bullet"]))

    # ────────── 12. Filosofía ──────────
    story.append(Paragraph("12. Filosofía del bot", st["h1"]))
    callout = (
        "<b>Fail loud, fail safe.</b> El bot NUNCA toma decisiones silenciosas "
        "sobre datos críticos. Si detecta algo que no entiende: detiene el ciclo, "
        "captura snapshot, notifica al operador por 3 canales (escritorio + "
        "WhatsApp + log), y espera intervención humana. Mejor que el operador "
        "intervenga 30 segundos antes que descubrir 3 días después que CFIA "
        "rechazó el plano por un dato inválido."
    )
    story.append(Paragraph(callout, st["callout"]))

    principios = [
        "Cada acción crítica se puede revertir o requiere confirmación explícita",
        "El estado vive en BD — el bot puede crashear y retomar exactamente donde quedó",
        "Toda decisión queda auditada en el log inmutable (hash encadenado)",
        "Los datos del cliente nunca salen del entorno local (no hay telemetría)",
        "El operador siempre puede ver el estado interno (DEBUG <exp>)",
        "Las reglas de oficina están codificadas explícitamente, no asumidas",
        "Nuevas reglas se aprenden iterando — cada plano valida lo aprendido",
    ]
    for p in principios:
        story.append(Paragraph(f"&bull; {_esc(p)}", st["bullet"]))

    # ────────── Cierre ──────────
    story.append(Spacer(1, 0.3 * inch))
    story.append(Paragraph(
        "<i>Este documento se genera automáticamente desde "
        "tools/generar_pdf_arquitectura.py — refleja el estado actual del bot. "
        "Para ver pendientes de probar, ver pendientes_de_probar.pdf.</i>",
        st["small"],
    ))

    doc.build(story)


def main() -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                   errors="replace")
    output = Path("arquitectura_catastro_bot.pdf")
    construir_pdf(output)
    print(f"✅ PDF generado: {output.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
