"""Genera un PDF con diagramas visuales del flujo del bot (estilo n8n).

USO:
    python tools/generar_esquema_pdf.py
    → genera data/esquema-catastro-bot.pdf
"""
from __future__ import annotations
import io
import os
import sys
from datetime import datetime
from pathlib import Path

os.chdir(r"C:\catastro-bot")
sys.path.insert(0, os.getcwd())
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")

from reportlab.lib.pagesizes import landscape, A3, A4
from reportlab.lib.colors import HexColor, white, black, transparent
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

OUTPUT_PATH = Path("data/esquema-catastro-bot.pdf")
PAGESIZE = landscape(A3)   # 420 x 297 mm — más espacio para diagramas

# ── Paleta de colores (estilo n8n/dashboards modernos) ─────────────────

BG_PAGE      = HexColor("#fafafa")
NODE_FILL    = HexColor("#ffffff")
NODE_BORDER  = HexColor("#e5e7eb")
NODE_SHADOW  = HexColor("#0000000d")  # rgba con alpha bajo
TEXT_DARK    = HexColor("#1f2937")
TEXT_LIGHT   = HexColor("#6b7280")
LINE_DASH    = HexColor("#94a3b8")
LINE_SOLID   = HexColor("#4b5563")

# Categorías
COL_TRIGGER  = HexColor("#fb923c")  # naranja
COL_ACTION   = HexColor("#3b82f6")  # azul
COL_AI       = HexColor("#8b5cf6")  # morado
COL_DATA     = HexColor("#10b981")  # verde
COL_DECISION = HexColor("#eab308")  # amarillo
COL_OK       = HexColor("#22c55e")  # verde
COL_WARN     = HexColor("#f59e0b")  # ámbar
COL_ERR      = HexColor("#ef4444")  # rojo
COL_TIMER    = HexColor("#06b6d4")  # cian


# ── Primitivas de dibujo ────────────────────────────────────────────────

def draw_node(c, x, y, w=80, h=46, *, icon="●", title="Nodo", subtitle="",
              color=COL_ACTION):
    """Dibuja un nodo estilo n8n: rectángulo redondeado con icono + título.

    (x, y) es la esquina inferior-izquierda.
    Color = acento (borde del icono).
    """
    # Sombra suave (usando alpha)
    c.saveState()
    c.setFillColor(HexColor("#00000010"))
    c.roundRect(x + 1, y - 1, w, h, 6, stroke=0, fill=1)
    c.restoreState()

    # Cuerpo blanco con borde
    c.setFillColor(NODE_FILL)
    c.setStrokeColor(NODE_BORDER)
    c.setLineWidth(0.8)
    c.roundRect(x, y, w, h, 6, stroke=1, fill=1)

    # Círculo de icono (color de categoría)
    ic_size = 22
    ic_x = x + 8
    ic_y = y + (h - ic_size) / 2
    c.setFillColor(color)
    c.setStrokeColor(color)
    c.circle(ic_x + ic_size/2, ic_y + ic_size/2, ic_size/2, fill=1, stroke=0)
    # Icono (texto blanco centrado)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 11)
    c.drawCentredString(ic_x + ic_size/2, ic_y + ic_size/2 - 4, icon)

    # Título (negra, bold)
    c.setFillColor(TEXT_DARK)
    c.setFont("Helvetica-Bold", 8)
    title_x = ic_x + ic_size + 6
    title_y = y + h/2 + (3 if subtitle else -2)
    c.drawString(title_x, title_y, title[:18])

    # Subtítulo (gris, regular)
    if subtitle:
        c.setFillColor(TEXT_LIGHT)
        c.setFont("Helvetica", 6.5)
        c.drawString(title_x, title_y - 9, subtitle[:24])


def draw_label(c, x, y, text, color=TEXT_LIGHT, size=7):
    """Etiqueta debajo de un nodo (texto pequeño)."""
    c.setFillColor(color)
    c.setFont("Helvetica", size)
    c.drawCentredString(x, y, text)


def draw_arrow(c, x1, y1, x2, y2, *, dashed=False, label=""):
    """Flecha de un nodo a otro con punta."""
    c.saveState()
    if dashed:
        c.setDash(3, 3)
        c.setStrokeColor(LINE_DASH)
    else:
        c.setStrokeColor(LINE_SOLID)
    c.setLineWidth(0.9)
    c.line(x1, y1, x2, y2)
    # Punta de flecha
    import math
    angle = math.atan2(y2 - y1, x2 - x1)
    head = 5
    c.line(x2, y2, x2 - head*math.cos(angle - math.pi/6),
                    y2 - head*math.sin(angle - math.pi/6))
    c.line(x2, y2, x2 - head*math.cos(angle + math.pi/6),
                    y2 - head*math.sin(angle + math.pi/6))
    c.restoreState()

    if label:
        c.setFillColor(TEXT_LIGHT)
        c.setFont("Helvetica", 6.5)
        mx = (x1 + x2) / 2
        my = (y1 + y2) / 2 + 4
        c.drawCentredString(mx, my, label)


def draw_diamond(c, cx, cy, w=70, h=44, *, label="¿Decisión?", color=COL_DECISION):
    """Rombo de decisión."""
    half_w = w / 2
    half_h = h / 2
    path = c.beginPath()
    path.moveTo(cx, cy + half_h)
    path.lineTo(cx + half_w, cy)
    path.lineTo(cx, cy - half_h)
    path.lineTo(cx - half_w, cy)
    path.close()
    c.setFillColor(color)
    c.setStrokeColor(NODE_BORDER)
    c.setLineWidth(0.8)
    c.drawPath(path, stroke=1, fill=1)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 7.5)
    c.drawCentredString(cx, cy - 3, label)


def draw_section_title(c, page_w, y, title, subtitle=""):
    """Título de sección arriba de la página."""
    c.setFillColor(TEXT_DARK)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(20, y, title)
    if subtitle:
        c.setFillColor(TEXT_LIGHT)
        c.setFont("Helvetica", 9.5)
        c.drawString(20, y - 14, subtitle)


def draw_footer(c, page_w, page_h, page_num, page_total):
    """Footer con número de página."""
    c.setFillColor(TEXT_LIGHT)
    c.setFont("Helvetica", 7)
    c.drawString(20, 14, "Catastro Bot · Esquema lineal de funcionamiento")
    c.drawRightString(
        page_w - 20, 14,
        f"Página {page_num}/{page_total} · {datetime.now().strftime('%Y-%m-%d')}",
    )


def fill_background(c, w, h):
    """Fondo gris suave estilo n8n."""
    c.setFillColor(BG_PAGE)
    c.rect(0, 0, w, h, stroke=0, fill=1)
    # Pequeñas grids de puntos (estilo n8n)
    c.saveState()
    c.setFillColor(HexColor("#e2e8f0"))
    for x in range(0, int(w), 18):
        for y in range(0, int(h), 18):
            c.circle(x, y, 0.5, stroke=0, fill=1)
    c.restoreState()


# ── Página 1: Flujo principal ───────────────────────────────────────────

def pagina_flujo_principal(c, w, h):
    fill_background(c, w, h)
    draw_section_title(c, w,
        h - 30,
        "Flujo de un plano — del cliente al CFIA",
        "Cada nodo es una etapa del bot. Las flechas muestran el orden secuencial.",
    )

    # Layout: 7 pasos horizontales en una fila + decisión + ramas
    NODE_W, NODE_H = 110, 50
    y_main = h - 130
    x0 = 30
    sep = NODE_W + 20

    pasos = [
        ("⚡", "Webhook",       "WhatsApp / CLI",       COL_TRIGGER),
        ("➊", "Crear",         "expediente + carpetas", COL_ACTION),
        ("⬆", "Subir",          "archivos 01_Campo",    COL_ACTION),
        ("🤖", "Extraer",       "Vision + pypdf",       COL_AI),
        ("✓", "Pre-flight",    "validar seed",         COL_DATA),
        ("⚙", "APT-flujo",     "bC + bP + archivos",   COL_ACTION),
        ("👁", "Revisar",       "operador visual",      COL_DECISION),
        ("🚀", "Enviar CFIA",   "click + sin FD",       COL_OK),
    ]

    pos = []
    for i, (icon, title, sub, color) in enumerate(pasos):
        x = x0 + i * sep
        draw_node(c, x, y_main, NODE_W, NODE_H,
                  icon=icon, title=title, subtitle=sub, color=color)
        pos.append((x, y_main))

    # Flechas entre nodos
    for i in range(len(pos) - 1):
        x1 = pos[i][0] + NODE_W
        x2 = pos[i+1][0]
        y = y_main + NODE_H/2
        draw_arrow(c, x1 + 2, y, x2 - 2, y)

    # ─── Bajada: Espera R1 ───
    espera_x = (pos[-1][0] + NODE_W/2)
    espera_y = y_main - 70
    draw_arrow(c, espera_x, y_main, espera_x, espera_y + NODE_H + 2)
    draw_node(c, espera_x - NODE_W/2, espera_y, NODE_W, NODE_H,
              icon="⏳", title="Esperar R1", subtitle="5-7 días · scheduler",
              color=COL_TIMER)

    # ─── Decisión ───
    dec_y = espera_y - 65
    draw_arrow(c, espera_x, espera_y, espera_x, dec_y + 22)
    draw_diamond(c, espera_x, dec_y, w=130, h=40, label="Respuesta CFIA")

    # ─── 3 ramas ───
    rama_y = dec_y - 80
    rama_dx = 200
    ramas = [
        (espera_x - rama_dx,  "✅", "Inscrito",    "directo (raro)",       COL_OK),
        (espera_x,            "📨", "Respondido",  "minuta + muni",        COL_AI),
        (espera_x + rama_dx,  "⚠",  "Defectuoso",  "corregir + R2",        COL_ERR),
    ]
    for rx, icon, title, sub, color in ramas:
        # Flecha desde diamond
        draw_arrow(c, espera_x, dec_y - 22, rx, rama_y + NODE_H + 2,
                    label="")
        draw_node(c, rx - NODE_W/2, rama_y, NODE_W, NODE_H,
                  icon=icon, title=title, subtitle=sub, color=color)

    # ─── Final inscrito ───
    fin_x = espera_x
    fin_y = rama_y - 70
    draw_node(c, fin_x - NODE_W/2, fin_y, NODE_W, NODE_H,
              icon="🎉", title="INSCRITO",
              subtitle="plano completo", color=COL_OK)
    # Flechas desde los 3 a inscrito
    for rx, *_ in ramas:
        draw_arrow(c, rx, rama_y, fin_x, fin_y + NODE_H + 2)

    # ─── Texto explicativo abajo ───
    info_y = fin_y - 40
    c.setFillColor(TEXT_LIGHT)
    c.setFont("Helvetica", 9)
    c.drawString(30, info_y,
        "💡 Si el plano va a Municipalidad (segregación/reunión), el flujo intermedio entre 'Respondido' y 'Enviar R2' "
        "es: armar paquete PDF → enviar Google Form → polling Gmail → recibir visado → presentar R2 con visado.")

    draw_footer(c, w, h, 1, 4)


# ── Página 2: Flujo Muni y R2 ───────────────────────────────────────────

def pagina_muni_r2(c, w, h):
    fill_background(c, w, h)
    draw_section_title(c, w, h - 30,
        "Flujo Muni + R2 (después de R1 aprobado)",
        "Para tipos segregación y reunión de fincas — paso intermedio entre R1 y R2 del CFIA.",
    )

    NODE_W, NODE_H = 115, 48
    y0 = h - 130

    # Fila 1: post R1
    fila1 = [
        ("✅", "Aprobado R1",     "CFIA respondió OK",   COL_OK),
        ("💧", "Carta de agua",  "según área del lote",  COL_DECISION),
    ]
    for i, (icon, title, sub, color) in enumerate(fila1):
        draw_node(c, 30 + i*(NODE_W+25), y0, NODE_W, NODE_H,
                  icon=icon, title=title, subtitle=sub, color=color)
    draw_arrow(c, 30 + NODE_W + 2, y0 + NODE_H/2,
                30 + NODE_W + 25, y0 + NODE_H/2)

    # Carta agua: 3 tramos (diamond + 3 ramas)
    diamante_x = 30 + (NODE_W+25) + NODE_W/2
    diamante_y = y0 - 80
    draw_arrow(c, diamante_x, y0, diamante_x, diamante_y + 22)
    draw_diamond(c, diamante_x, diamante_y, w=200, h=40,
                  label="Área del lote → tramo")

    # 3 ramas según área
    tramos = [
        (diamante_x - 180, "💧", "OBLIGATORIA",  "< 1000 m²",          COL_TIMER),
        (diamante_x,       "💧", "OPCIONAL",     "1000-5000 m²",       COL_DECISION),
        (diamante_x + 180, "📝", "Solo nota",    "> 5000 m²",          COL_ACTION),
    ]
    tramo_y = diamante_y - 75
    for tx, icon, title, sub, color in tramos:
        draw_arrow(c, diamante_x, diamante_y - 22, tx, tramo_y + NODE_H + 2)
        draw_node(c, tx - NODE_W/2, tramo_y, NODE_W, NODE_H,
                  icon=icon, title=title, subtitle=sub, color=color)

    # Paquete + envío muni
    paquete_y = tramo_y - 70
    paquete_x = diamante_x
    draw_node(c, paquete_x - NODE_W/2, paquete_y, NODE_W, NODE_H,
              icon="📦", title="Paquete PDF",
              subtitle="combinar todos", color=COL_ACTION)
    for tx, *_ in tramos:
        draw_arrow(c, tx, tramo_y, paquete_x, paquete_y + NODE_H + 2)

    form_y = paquete_y - 75
    form_x = paquete_x
    draw_node(c, form_x - NODE_W/2, form_y, NODE_W, NODE_H,
              icon="🌐", title="Google Form",
              subtitle="URL pre-llenada", color=COL_AI)
    draw_arrow(c, paquete_x, paquete_y, form_x, form_y + NODE_H + 2)

    # Polling Gmail (paralelo)
    gmail_x = form_x + 200
    gmail_y = form_y
    draw_node(c, gmail_x - NODE_W/2, gmail_y, NODE_W, NODE_H,
              icon="📧", title="Polling Gmail",
              subtitle="cada 15min", color=COL_TIMER)
    draw_arrow(c, form_x + NODE_W/2 - 5, form_y + NODE_H/2,
                gmail_x - NODE_W/2, gmail_y + NODE_H/2, dashed=True,
                label="acuse / aprobado / morosidad")

    # Decisión muni
    dec_muni_y = gmail_y - 65
    draw_arrow(c, gmail_x, gmail_y, gmail_x, dec_muni_y + 22)
    draw_diamond(c, gmail_x, dec_muni_y, w=140, h=40, label="Respuesta Muni")

    # 3 ramas muni
    muni_y = dec_muni_y - 75
    muni_options = [
        (gmail_x - 180, "✅", "Aprobado",     "visado emitido",   COL_OK),
        (gmail_x,       "💰", "Morosidad",    "cliente paga",     COL_WARN),
        (gmail_x + 180, "❌", "Rechazado",    "corregir + reenv", COL_ERR),
    ]
    for mx, icon, title, sub, color in muni_options:
        draw_arrow(c, gmail_x, dec_muni_y - 22, mx, muni_y + NODE_H + 2)
        draw_node(c, mx - NODE_W/2, muni_y, NODE_W, NODE_H,
                  icon=icon, title=title, subtitle=sub, color=color)

    # R2 final
    r2_y = muni_y - 75
    r2_x = gmail_x - 180
    draw_node(c, r2_x - NODE_W/2, r2_y, NODE_W, NODE_H,
              icon="🚀", title="APT-R2",
              subtitle="anverso + visado", color=COL_ACTION)
    draw_arrow(c, r2_x, muni_y, r2_x, r2_y + NODE_H + 2)

    # Inscrito
    insc_y = r2_y - 65
    insc_x = r2_x
    draw_node(c, insc_x - NODE_W/2, insc_y, NODE_W, NODE_H,
              icon="🎉", title="INSCRITO",
              subtitle="trámite cerrado", color=COL_OK)
    draw_arrow(c, r2_x, r2_y, insc_x, insc_y + NODE_H + 2)

    draw_footer(c, w, h, 2, 4)


# ── Página 3: Scheduler ─────────────────────────────────────────────────

def pagina_scheduler(c, w, h):
    fill_background(c, w, h)
    draw_section_title(c, w, h - 30,
        "Scheduler — 8 jobs corriendo en paralelo",
        "El bot corre 24/7 desde Startup de Windows. Cada job se ejecuta a su frecuencia.",
    )

    NODE_W, NODE_H = 130, 52

    # Nodo central
    central_x = w/2 - NODE_W/2
    central_y = h - 140
    draw_node(c, central_x, central_y, NODE_W, NODE_H,
              icon="🤖", title="src.main",
              subtitle="scheduler 24/7", color=COL_AI)

    # 8 jobs alrededor
    jobs = [
        ("⏱",  "tick",                 "cada 60 segundos",     "workflows + transitions",  COL_ACTION),
        ("🔄", "apt-sync-estados",     "cada 30 minutos",      "consulta APT vía CDP",     COL_TIMER),
        ("📧", "muni-sync-emails",     "cada 15 minutos",      "Gmail IMAP",               COL_TIMER),
        ("⚠",  "stale-alert",          "cada 6 horas",          "expedientes parados",      COL_WARN),
        ("🔔", "correcciones-renotif", "cada 24 horas",         "recordatorios",            COL_WARN),
        ("💾", "db-backup",            "diario 02:00",         "BD + config → ZIP",        COL_DATA),
        ("🔒", "audit-verify",         "diario 03:00",         "cadena hash inmutable",    COL_DATA),
        ("📊", "weekly-report",        "lunes 07:00",          "email reporte semanal",    COL_OK),
    ]

    import math
    rad = 220   # radio del círculo
    n = len(jobs)
    cx = central_x + NODE_W/2
    cy = central_y + NODE_H/2
    for i, (icon, title, freq, what, color) in enumerate(jobs):
        # Distribuir en 2 filas (4 arriba, 4 abajo)
        if i < 4:
            # arriba
            spread_w = 4 * (NODE_W + 20)
            x = (w - spread_w)/2 + i * (NODE_W + 20)
            y = central_y - 110
        else:
            # abajo
            spread_w = 4 * (NODE_W + 20)
            x = (w - spread_w)/2 + (i-4) * (NODE_W + 20)
            y = central_y - 200

        draw_node(c, x, y, NODE_W, NODE_H,
                  icon=icon, title=title, subtitle=freq, color=color)
        # Etiqueta debajo
        draw_label(c, x + NODE_W/2, y - 10, what, color=TEXT_LIGHT, size=6.5)
        # Línea desde el central
        # Desde central-down al nodo-top
        if i < 4:
            sx = cx
            sy = central_y
            tx = x + NODE_W/2
            ty = y + NODE_H
        else:
            sx = cx
            sy = central_y
            tx = x + NODE_W/2
            ty = y + NODE_H
        draw_arrow(c, sx, sy, tx, ty, dashed=True)

    draw_footer(c, w, h, 3, 4)


# ── Página 4: Interfaces de monitoreo ────────────────────────────────

def pagina_interfaces(c, w, h):
    fill_background(c, w, h)
    draw_section_title(c, w, h - 30,
        "Interfaces — cómo el operador usa el bot",
        "3 formas de consumir el bot. Todas leen/escriben en la misma BD SQLite local.",
    )

    NODE_W, NODE_H = 130, 56

    # Operador (arriba centro)
    op_x = w/2 - NODE_W/2
    op_y = h - 130
    draw_node(c, op_x, op_y, NODE_W, NODE_H,
              icon="👤", title="Operador",
              subtitle="topógrafo · admin", color=COL_TRIGGER)

    # 3 interfaces (intermedias)
    int_y = op_y - 100
    interfaces = [
        (op_x - 220, "📐", "Dashboard Web",     "localhost:9224 · auto-refresh 30s",  COL_AI),
        (op_x,       "⌨",  "CLI catastro-bot", "subcomandos: 16+",                   COL_ACTION),
        (op_x + 220, "📱", "WhatsApp",          "Green API · alerts + confirm",       COL_OK),
    ]
    for ix, icon, title, sub, color in interfaces:
        draw_node(c, ix, int_y, NODE_W, NODE_H,
                  icon=icon, title=title, subtitle=sub, color=color)
        draw_arrow(c, op_x + NODE_W/2, op_y, ix + NODE_W/2, int_y + NODE_H + 2)

    # BD central (abajo)
    bd_w, bd_h = 380, 100
    bd_x = w/2 - bd_w/2
    bd_y = int_y - 150
    # Body
    c.setFillColor(NODE_FILL)
    c.setStrokeColor(NODE_BORDER)
    c.setLineWidth(1.0)
    c.roundRect(bd_x, bd_y, bd_w, bd_h, 8, stroke=1, fill=1)
    # Header
    c.setFillColor(COL_DATA)
    c.roundRect(bd_x, bd_y + bd_h - 24, bd_w, 24, 6, stroke=0, fill=1)
    c.setFillColor(white)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(bd_x + 12, bd_y + bd_h - 16, "📊 BD SQLite — data/catastro.db")

    # Tablas
    tablas = [
        "expedientes",
        "estados_historial",
        "archivos",
        "acciones_pendientes",
        "apt_memoria_operador (89+ reglas)",
        "audit_log (hash chain)",
        "usuarios",
        "lotes",
    ]
    c.setFillColor(TEXT_DARK)
    c.setFont("Helvetica", 8)
    col_w = bd_w / 2
    for i, t in enumerate(tablas):
        col = i % 2
        row = i // 2
        c.drawString(bd_x + 14 + col * col_w, bd_y + bd_h - 38 - row * 12, f"• {t}")

    # Flechas desde interfaces a BD
    for ix, *_ in interfaces:
        draw_arrow(c, ix + NODE_W/2, int_y, w/2, bd_y + bd_h, dashed=True)

    # Servicios externos
    ext_y = bd_y - 110
    externos = [
        (op_x - 280, "☁",  "Anthropic API",     "Vision + minuta",            COL_AI),
        (op_x - 80,  "🌐", "APT CFIA",          "vía Chrome CDP + Playwright", COL_ACTION),
        (op_x + 120, "🏛", "Muni San Ramón",   "Google Form + Gmail IMAP",   COL_WARN),
        (op_x + 320, "💬", "WhatsApp",          "Green API",                  COL_OK),
    ]
    for ex, icon, title, sub, color in externos:
        draw_node(c, ex, ext_y, NODE_W, NODE_H,
                  icon=icon, title=title, subtitle=sub, color=color)
        # Flecha desde BD a externo
        draw_arrow(c, w/2, bd_y, ex + NODE_W/2, ext_y + NODE_H + 2, dashed=True)

    # Label entre BD y externos
    c.setFillColor(TEXT_LIGHT)
    c.setFont("Helvetica-Oblique", 8)
    c.drawCentredString(w/2, bd_y - 18, "Integraciones externas (consume vía agents)")

    draw_footer(c, w, h, 4, 4)


# ── Generación ──────────────────────────────────────────────────────────

def generar_pdf() -> Path:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    w, h = PAGESIZE

    c = canvas.Canvas(str(OUTPUT_PATH), pagesize=PAGESIZE)
    c.setTitle("Catastro Bot - Esquema lineal")
    c.setAuthor("catastro-bot")

    # Portada
    fill_background(c, w, h)
    c.setFillColor(TEXT_DARK)
    c.setFont("Helvetica-Bold", 32)
    c.drawCentredString(w/2, h/2 + 60, "Catastro Bot")
    c.setFont("Helvetica", 18)
    c.setFillColor(TEXT_LIGHT)
    c.drawCentredString(w/2, h/2 + 30, "Esquema lineal de funcionamiento")
    c.setFont("Helvetica", 11)
    c.drawCentredString(
        w/2, h/2,
        f"Documentación operativa · Generado {datetime.now().strftime('%Y-%m-%d %H:%M')}",
    )
    # Box de descripción
    box_w, box_h = 500, 130
    box_x = w/2 - box_w/2
    box_y = h/2 - 130
    c.setFillColor(NODE_FILL)
    c.setStrokeColor(NODE_BORDER)
    c.roundRect(box_x, box_y, box_w, box_h, 10, stroke=1, fill=1)
    c.setFillColor(TEXT_DARK)
    c.setFont("Helvetica", 10)
    desc_lines = [
        "Sistema multi-agente para automatizar la presentación de planos catastrales",
        "en Costa Rica. Levantamiento topográfico → APT (CFIA) → Muni → inscripción RNP.",
        "",
        "Este documento muestra:",
        "  Página 2: Flujo del plano (crear → enviar al CFIA)",
        "  Página 3: Flujo Muni + R2 (post R1 aprobado)",
        "  Página 4: Scheduler — 8 jobs automáticos",
        "  Página 5: Interfaces de monitoreo + BD + integraciones",
    ]
    for i, line in enumerate(desc_lines):
        c.drawString(box_x + 20, box_y + box_h - 22 - i * 13, line)
    draw_footer(c, w, h, 1, 5)
    c.showPage()

    # Página 1: flujo principal
    pagina_flujo_principal(c, w, h)
    c.showPage()

    # Página 2: muni + R2
    pagina_muni_r2(c, w, h)
    c.showPage()

    # Página 3: scheduler
    pagina_scheduler(c, w, h)
    c.showPage()

    # Página 4: interfaces
    pagina_interfaces(c, w, h)
    c.showPage()

    c.save()
    return OUTPUT_PATH


def main() -> int:
    print("Generando PDF visual del flujo (estilo n8n)...")
    path = generar_pdf()
    size_kb = path.stat().st_size / 1024
    print()
    print("=" * 60)
    print(f"  PDF generado con 5 diagramas visuales")
    print("=" * 60)
    print(f"  Archivo:  {path.resolve()}")
    print(f"  Tamano:   {size_kb:.1f} KB")
    print(f"  Tamano:   A3 horizontal (mas espacio para diagramas)")
    print()
    print(f"  Paginas:")
    print(f"    1. Portada")
    print(f"    2. Flujo de un plano (7 pasos + decision R1)")
    print(f"    3. Flujo Muni + R2 (con polling Gmail)")
    print(f"    4. Scheduler — 8 jobs paralelos")
    print(f"    5. Interfaces + BD + integraciones externas")
    print()
    print(f"  Para abrir: start {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
