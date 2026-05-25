"""Dashboard web local — http://localhost:9224

Servidor HTTP simple que renderiza una tabla bonita con el estado de
TODOS los expedientes. Auto-refresh cada 30s. Sin dependencias externas
(stdlib `http.server` + HTML/CSS inline).

USO:
  python -m src.utils.dashboard_web                 # arranca en 9224
  python -m src.utils.dashboard_web --port 9300     # otro puerto

Diseño:
  - Header con título + última actualización
  - Tabla principal: expediente · etapa (color) · trámite · cliente · días · próximo paso
  - Resumen por etapa al final (cards con conteo)
  - Auto-refresh cada 30s (meta http-equiv)
  - Diseño limpio, sin JS — solo HTML+CSS+stdlib

Para el flujo de oficina: abrí http://localhost:9224 en cualquier
pestaña del browser y dejala abierta todo el día. Cambia sola.
"""
from __future__ import annotations
import argparse
import html
import json
import logging
import os
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional

log = logging.getLogger("catastro.dashboard_web")

DEFAULT_PORT = 9224
DB_PATH = Path("data/catastro.db")


# ── Mapeo estado → (etapa human, color hex, próximo paso) ──────────────

ETAPAS = {
    # ── Etapa 1: Preparación (antes de llegar a APT) ────────────────────
    "recibido":              ("📥 1. Expediente creado",                "#94a3b8",
                              "Subir archivos a 01_Campo (plano, registro, entero)"),
    "en_extraccion":         ("🔍 1. Extrayendo datos de los PDFs",     "#06b6d4",
                              "Vision/pypdf leyendo cajetín, registro, entero"),
    "listo_para_apt":        ("✅ 2. Datos listos para APT",            "#10b981",
                              "Ejecutar: catastro-bot apt-flujo"),

    # ── Etapa 3: Llenado del trámite en el portal CFIA ──────────────────
    "en_llenado_contrato":   ("📝 3a. Llenando contrato en APT",        "#f59e0b",
                              "Continuar: catastro-bot apt-plano"),
    "en_llenado_plano":      ("📐 3b. Llenando plano en APT",           "#f59e0b",
                              "Operador revisa + apt-enviar"),

    # ── Etapa 4: En revisión del Catastro Nacional (R1) ─────────────────
    "presentado_apt_r1":     ("🚀 4. En revisión R1 — CFIA (5-7 días)", "#3b82f6",
                              "Esperar respuesta del Catastro Nacional"),
    "enviado_cfia":          ("🚀 4. Enviado al CFIA",                  "#3b82f6",
                              "Esperar respuesta R1"),
    "en_calificacion":       ("⏳ 4. En calificación del RNP",          "#3b82f6",
                              "Calificador del RNP revisando el plano"),

    # ── Etapa 5: Resultado R1 ───────────────────────────────────────────
    "respondido_r1":         ("📨 5. CFIA respondió R1 (con minuta)",   "#8b5cf6",
                              "Revisar minuta y avanzar a muni"),
    "aprobado_r1":           ("✅ 5. R1 aprobado por el CFIA",          "#10b981",
                              "Continuar con muni o presentar R2"),
    "defectuoso":            ("⚠️ 5. R1 defectuoso — corregir",         "#ef4444",
                              "Revisar errores y presentar correcciones"),

    # ── Etapa 6: Documentos pendientes del topógrafo/cliente ────────────
    "carta_agua_requerida":  ("💧 6. Falta carta de agua del AyA",      "#06b6d4",
                              "Tramitar carta de agua en AyA"),
    "carta_agua_pendiente":  ("💧 6. Carta de agua presentada al AyA",  "#06b6d4",
                              "Esperar respuesta del AyA"),
    "documento_pendiente":   ("📄 6. Documento pendiente",              "#f59e0b",
                              "Operador completar documento faltante"),

    # ── Etapa 7: Corrección del plano (post-defectuoso) ─────────────────
    "en_correccion":         ("🔧 7. Corrigiendo plano (DWG/datos)",    "#f97316",
                              "Aplicar correcciones y volver a presentar"),

    # ── Etapa 8: Flujo Municipalidad (entre R1 y R2) ────────────────────
    "enviado_muni":          ("🏛️ 8. Enviado a la municipalidad",       "#06b6d4",
                              "Esperar visado municipal"),
    "muni_morosidad":        ("💰 8. Muni: cliente con morosidad",      "#f59e0b",
                              "Avisar al cliente que pague impuestos muni"),
    "muni_aprobado":         ("✅ 8. Muni visó — listo para R2",        "#10b981",
                              "Ejecutar: catastro-bot apt-r2"),
    "muni_rechazado":        ("❌ 8. Muni rechazó el visado",           "#ef4444",
                              "Revisar observaciones y corregir"),

    # ── Etapa 9: Segunda ronda APT (R2 con visado muni) ─────────────────
    "presentado_apt_r2":     ("🚀 9. En revisión R2 — CFIA",            "#3b82f6",
                              "Esperar inscripción del Catastro"),

    # ── Etapa 10: Plano inscrito (terminado) ────────────────────────────
    "inscrito":              ("🎉 10. INSCRITO en el CFIA",             "#22c55e",
                              "✅ Plano completo — trámite finalizado"),
    "cerrado":               ("🔒 Trámite cerrado",                     "#64748b",
                              "Archivo del expediente"),
}


def _etapa_info(estado: str) -> tuple[str, str, str]:
    return ETAPAS.get(estado, (f"❓ {estado}", "#6b7280", "-"))


def _parse_ts(ts: Optional[str]) -> Optional[datetime]:
    """Parser robusto de timestamps SQLite/ISO. Acepta:
      - "2026-05-15 20:40:09"      (SQLite datetime())
      - "2026-05-15T20:40:09"      (ISO sin tz)
      - "2026-05-15T20:40:09.123+00:00"  (ISO con tz)
      - "2026-05-15T20:40:09Z"
      - "2026-05-15"               (solo fecha)
    Devuelve datetime en UTC, o None si no parsea.
    """
    if not ts:
        return None
    try:
        # Normalizar: SQLite datetime() usa espacio en vez de T
        norm = ts.replace(" ", "T", 1) if " " in ts and "T" not in ts else ts
        norm = norm.replace("Z", "+00:00")
        # Si no tiene tz, asumir UTC
        if "+" not in norm and "T" in norm:
            dt = datetime.fromisoformat(norm).replace(tzinfo=timezone.utc)
        elif "T" not in norm:
            dt = datetime.fromisoformat(norm + "T00:00:00").replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(norm)
        return dt
    except Exception:
        return None


def _dias_desde(ts: Optional[str]) -> int:
    """Días desde un timestamp. Devuelve -1 si no parsea."""
    dt = _parse_ts(ts)
    if dt is None:
        return -1
    return int((datetime.now(timezone.utc) - dt).total_seconds() // 86400)


def _dias_en_estado_actual(expediente_id: str, estado_actual: str,
                           fecha_creacion: str) -> int:
    """Días desde que el expediente ENTRÓ a su estado actual.

    Si nunca cambió de estado (estado inicial), usa fecha_creacion.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        # Buscar el último cambio AL estado actual
        r = conn.execute(
            "SELECT timestamp FROM estados_historial "
            "WHERE expediente_id=? AND estado_nuevo=? "
            "ORDER BY timestamp DESC LIMIT 1",
            (expediente_id, estado_actual),
        ).fetchone()
        conn.close()
        if r and r["timestamp"]:
            return _dias_desde(r["timestamp"])
    except Exception:
        pass
    # Fallback: desde fecha_creacion
    return _dias_desde(fecha_creacion)


def _leer_expedientes() -> list[dict]:
    if not DB_PATH.exists():
        return []
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, numero_expediente, tipo_plano, estado_actual, fecha_creacion, "
        "       fecha_actualizacion, nombre_cliente, telefono_cliente, metadata_json "
        "FROM expedientes ORDER BY fecha_actualizacion DESC, fecha_creacion DESC"
    ).fetchall()
    conn.close()
    out = []
    for r in rows:
        meta = json.loads(r["metadata_json"] or "{}")
        prop = meta.get("datos_apt", {}).get("propietario", {})
        cliente_nombre = prop.get("nombre", "") or meta.get("nombre_proyecto", "")
        if prop.get("apellido1"):
            cliente_nombre = f"{prop['nombre']} {prop['apellido1']}".strip()

        # Días en estado actual: prioridad
        #  1. metadata.apt_fecha_presentacion (cuándo se envió a CFIA — más preciso)
        #  2. último cambio AL estado_actual en estados_historial
        #  3. fecha_creacion
        estado = r["estado_actual"] or "?"
        if estado in ("presentado_apt_r1", "enviado_cfia") and meta.get("apt_fecha_presentacion"):
            dias = _dias_desde(meta["apt_fecha_presentacion"])
        else:
            dias = _dias_en_estado_actual(r["id"], estado, r["fecha_creacion"])

        out.append({
            "numero":     r["numero_expediente"],
            "tipo":       r["tipo_plano"],
            "estado":     estado,
            "tramite":    meta.get("apt_tramite", ""),
            "cliente":    cliente_nombre or "—",
            "proyecto":   meta.get("nombre_proyecto", ""),
            "ubicacion":  f"{meta.get('provincia','')}/{meta.get('canton','')}/{meta.get('distrito','')}".strip("/"),
            "dias":       dias,
            "creado":     r["fecha_creacion"],
            "actualizado": r["fecha_actualizacion"],
            "apt_fecha_presentacion": meta.get("apt_fecha_presentacion"),
        })
    return out


# ── HTML rendering ─────────────────────────────────────────────────────

_CSS = """
* { box-sizing: border-box; }
body {
    font-family: -apple-system, "Segoe UI", Roboto, Oxygen, Ubuntu, sans-serif;
    margin: 0;
    background: #0f172a;
    color: #e2e8f0;
    line-height: 1.5;
}
header {
    background: linear-gradient(135deg, #1e293b 0%, #334155 100%);
    padding: 24px 32px;
    border-bottom: 3px solid #3b82f6;
}
header h1 {
    margin: 0 0 4px 0;
    font-size: 24px;
    color: #f1f5f9;
}
header .meta {
    font-size: 13px;
    color: #94a3b8;
}
header .meta strong { color: #60a5fa; }
.container {
    max-width: 1600px;
    margin: 24px auto;
    padding: 0 24px;
}
.cards {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 12px;
    margin-bottom: 24px;
}
.card {
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 8px;
    padding: 14px 16px;
    display: flex;
    align-items: center;
    gap: 12px;
}
.card .count {
    font-size: 28px;
    font-weight: 700;
    color: #f1f5f9;
}
.card .label {
    font-size: 12px;
    color: #94a3b8;
    line-height: 1.3;
}
table {
    width: 100%;
    border-collapse: collapse;
    background: #1e293b;
    border-radius: 8px;
    overflow: hidden;
    box-shadow: 0 4px 12px rgba(0,0,0,0.3);
}
th {
    background: #334155;
    color: #cbd5e1;
    text-align: left;
    padding: 12px 14px;
    font-size: 12px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    border-bottom: 2px solid #475569;
}
td {
    padding: 12px 14px;
    border-bottom: 1px solid #334155;
    font-size: 14px;
}
tbody tr:hover { background: #283548; }
tbody tr:last-child td { border-bottom: none; }
.expediente {
    font-family: "Consolas", "Monaco", monospace;
    font-weight: 600;
    color: #f1f5f9;
}
.etapa {
    display: inline-block;
    padding: 4px 10px;
    border-radius: 16px;
    font-size: 12px;
    font-weight: 600;
    color: #0f172a;
}
.tramite {
    font-family: "Consolas", monospace;
    color: #93c5fd;
}
.proyecto {
    font-family: "Consolas", monospace;
    color: #fbbf24;
    font-weight: 600;
}
.cliente { color: #e2e8f0; }
.dias {
    font-weight: 600;
    text-align: center;
}
.dias.viejo { color: #f97316; }
.dias.muy-viejo { color: #ef4444; }
.proximo { color: #94a3b8; font-size: 13px; }
footer {
    text-align: center;
    padding: 20px;
    color: #64748b;
    font-size: 12px;
}
footer a { color: #60a5fa; text-decoration: none; }
.empty {
    text-align: center;
    padding: 60px;
    color: #64748b;
    font-size: 16px;
}
/* Panel estado del bot */
.bot-status {
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 10px;
    padding: 16px 20px;
    margin-bottom: 24px;
}
.bot-status h2 {
    margin: 0 0 12px 0;
    font-size: 16px;
    color: #cbd5e1;
    display: flex;
    align-items: center;
    gap: 10px;
}
.bot-status .pulse {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: #22c55e;
    box-shadow: 0 0 0 0 rgba(34, 197, 94, 0.7);
    animation: pulse 2s infinite;
}
.bot-status .pulse.down { background: #ef4444; animation: none; }
@keyframes pulse {
    0% { box-shadow: 0 0 0 0 rgba(34, 197, 94, 0.6); }
    70% { box-shadow: 0 0 0 12px rgba(34, 197, 94, 0); }
    100% { box-shadow: 0 0 0 0 rgba(34, 197, 94, 0); }
}
.bot-services {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 10px;
}
.svc {
    background: #283548;
    padding: 10px 14px;
    border-radius: 6px;
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 13px;
}
.svc .led {
    width: 8px; height: 8px; border-radius: 50%;
    flex-shrink: 0;
}
.svc.ok .led    { background: #22c55e; }
.svc.warn .led  { background: #f59e0b; }
.svc.down .led  { background: #ef4444; }
.svc .name      { flex: 1; color: #cbd5e1; }
.svc .detail    { color: #64748b; font-family: monospace; font-size: 11px; }
.bot-actions {
    margin-top: 12px;
    padding-top: 12px;
    border-top: 1px solid #334155;
    font-size: 12px;
    color: #94a3b8;
}
.bot-actions code {
    background: #0f172a;
    padding: 2px 8px;
    border-radius: 4px;
    color: #93c5fd;
    font-size: 11px;
}

@media (max-width: 768px) {
    .container { padding: 0 12px; }
    th, td { padding: 8px; font-size: 12px; }
    .ubicacion { display: none; }
}
"""


# ── Página /config — gestión de credenciales ────────────────────────────

# Definición declarativa de las credenciales que se pueden editar via web
_CONFIG_CREDS = [
    {
        "key":   "anthropic-api",
        "label": "Anthropic API Key",
        "desc":  "Key para Vision (sk-ant-...)",
        "fields": [{"name": "val1", "label": "API Key", "type": "password",
                    "placeholder": "sk-ant-..."}],
    },
    {
        "key":   "muni-san-ramon",
        "label": "Gmail muni (IMAP)",
        "desc":  "Email + App Password para leer respuestas muni",
        "fields": [
            {"name": "val1", "label": "Email", "type": "email",
             "placeholder": "topografiahrh@gmail.com"},
            {"name": "val2", "label": "App Password (16 chars)",
             "type": "password", "placeholder": "abcd efgh ijkl mnop"},
        ],
    },
    {
        "key":   "green-api",
        "label": "Green API (WhatsApp)",
        "desc":  "Instance ID + Token para WhatsApp",
        "fields": [
            {"name": "val1", "label": "Instance ID", "type": "text",
             "placeholder": "7107606637"},
            {"name": "val2", "label": "Token", "type": "password",
             "placeholder": "abc123..."},
        ],
    },
    {
        "key":   "apt-cfia",
        "label": "APT CFIA",
        "desc":  "Usuario + password del topógrafo en APT",
        "fields": [
            {"name": "val1", "label": "Usuario", "type": "text",
             "placeholder": "topografo@dominio"},
            {"name": "val2", "label": "Password", "type": "password",
             "placeholder": "********"},
        ],
    },
    {
        "key":   "email-bot",
        "label": "Email bot (SMTP)",
        "desc":  "Cuenta Hotmail/Office365 para enviar reportes",
        "fields": [
            {"name": "val1", "label": "Email", "type": "email",
             "placeholder": "bot@hotmail.com"},
            {"name": "val2", "label": "App Password", "type": "password",
             "placeholder": "********"},
        ],
    },
]


def _obtener_config_estado() -> dict:
    """Estado de las credenciales (sin valores, solo OK/falta)."""
    from src.core.credential_manager import CredentialManager
    cm = CredentialManager()
    out = {}
    for c in _CONFIG_CREDS:
        key = c["key"]
        try:
            if key == "anthropic-api":
                k = cm.get_anthropic_key()
                out[key] = {"ok": bool(k and k.startswith("sk-ant-")),
                             "detalle": f"len={len(k or '')}"}
            elif key == "muni-san-ramon":
                user, pw = cm.get_muni_san_ramon()
                out[key] = {"ok": ("@" in user and len(pw) >= 10),
                             "detalle": f"{user[:25]}, pw_len={len(pw or '')}"}
            elif key == "green-api":
                data = cm.get_green_api()
                iid = str(data.get("instance_id", ""))
                tok = str(data.get("token", ""))
                out[key] = {"ok": bool(iid and tok),
                             "detalle": f"instance={iid[:8]}..."}
            elif key == "apt-cfia":
                u = cm.get_secret("apt-cfia-user")
                p = cm.get_secret("apt-cfia-pass")
                out[key] = {"ok": bool(u and p),
                             "detalle": f"user={u[:25] if u else '-'}"}
            elif key == "email-bot":
                u = cm.get_secret("email-bot-user")
                p = cm.get_secret("email-bot-pass")
                out[key] = {"ok": bool(u and p),
                             "detalle": f"user={u[:25] if u else '-'}"}
        except Exception as exc:
            out[key] = {"ok": False, "detalle": str(exc)[:50]}
    return out


def _set_credencial(key: str, val1: str, val2: str = "") -> str:
    """Guarda una credencial. Devuelve mensaje resultado."""
    from src.core.credential_manager import CredentialManager
    cm = CredentialManager()
    try:
        if key == "anthropic-api":
            if not val1.startswith("sk-ant-"):
                return "❌ Key debe empezar con sk-ant-"
            cm.set_secret("anthropic-api", val1)
            return f"✅ Anthropic API key guardada (len={len(val1)})"
        elif key == "muni-san-ramon":
            if "@" not in val1:
                return "❌ Email inválido (debe contener @)"
            cm.set_muni_san_ramon(val1, val2.replace(" ", ""))
            return f"✅ Gmail muni guardado: {val1}"
        elif key == "green-api":
            cm.set_secret("green-api-instance", val1)
            cm.set_secret("green-api-token", val2)
            return f"✅ Green API guardado: instance {val1[:8]}..."
        elif key == "apt-cfia":
            cm.set_secret("apt-cfia-user", val1)
            cm.set_secret("apt-cfia-pass", val2)
            return f"✅ APT CFIA guardado: {val1[:25]}"
        elif key == "email-bot":
            cm.set_secret("email-bot-user", val1)
            cm.set_secret("email-bot-pass", val2)
            return f"✅ Email bot guardado: {val1[:25]}"
        return f"❌ Credencial desconocida: {key}"
    except Exception as exc:
        return f"❌ Error: {str(exc)[:120]}"


def _render_config_html(msg: str = "") -> str:
    """Renderiza la página /config con formularios para cada credencial."""
    from urllib.parse import urlparse, parse_qs
    estado = _obtener_config_estado()
    bot = _obtener_estado_bot()

    # Panel "Control del bot"
    def _btn(action, service, label, color, icon=""):
        return f"""
        <form method="POST" action="/control" style="display:inline-block;margin:4px">
            <input type="hidden" name="accion" value="{action}">
            <input type="hidden" name="servicio" value="{service}">
            <button type="submit" class="ctl-btn ctl-{color}">
                {icon} {html.escape(label)}
            </button>
        </form>
        """

    def _svc_row(nombre, key_estado, descripcion):
        running = (
            (key_estado == "chrome" and bot["chrome"]["alive"])
            or (key_estado != "chrome" and bot.get(key_estado, {}).get("running"))
        )
        led_class = "ok" if running else "down"
        status = "Corriendo" if running else "Detenido"
        btn = _btn("apagar", key_estado, "Apagar", "red", "⏹") if running else _btn("encender", key_estado, "Encender", "green", "▶")
        return f"""
        <div class="ctl-row">
            <div class="ctl-info">
                <span class="ctl-led {led_class}"></span>
                <strong>{html.escape(nombre)}</strong>
                <small>{html.escape(descripcion)}</small>
            </div>
            <div class="ctl-actions">
                <span class="ctl-status {led_class}">{status}</span>
                {btn}
            </div>
        </div>
        """

    control_html = f"""
    <div class="cfg-card" style="border-left:4px solid #3b82f6">
        <div class="cfg-header">
            <h3>🎛️ Control del bot</h3>
            <p>Apagar/encender servicios sin tener que abrir terminal.</p>
        </div>
        {_svc_row("Chrome del bot (CDP 9222)", "chrome", "Browser para portal APT — perfil dedicado")}
        {_svc_row("Watchdog Chrome", "watchdog", "Relanza Chrome cada 60s si cae")}
        {_svc_row("Scheduler (src.main)", "scheduler", "Jobs automáticos: APT, muni, backup, etc.")}
        <hr style="margin:14px 0;border-color:#334155">
        <div style="text-align:center">
            <strong style="color:#cbd5e1;font-size:13px">Acciones masivas:</strong><br>
            {_btn("encender", "todo", "Encender todo el bot", "green", "▶▶")}
            {_btn("apagar", "todo", "Apagar todo el bot", "red", "⏹⏹")}
            {_btn("reiniciar", "todo", "Reiniciar todo", "amber", "🔄")}
        </div>
        <div style="margin-top:12px;font-size:11px;color:#64748b;text-align:center">
            ℹ️ El dashboard web (este) sigue activo después de "Apagar todo". Si querés cerrarlo también,
            cerrá la ventana cmd "Dashboard" manualmente.
        </div>
    </div>
    """

    forms_html = ""
    for cred in _CONFIG_CREDS:
        key = cred["key"]
        est = estado.get(key, {"ok": False, "detalle": "?"})
        icon = "✅" if est["ok"] else "❌"
        color_class = "ok" if est["ok"] else "falta"
        fields_html = ""
        for f in cred["fields"]:
            fields_html += f"""
            <div class="cfg-field">
                <label>{html.escape(f['label'])}</label>
                <input type="{f['type']}" name="{f['name']}"
                       placeholder="{html.escape(f.get('placeholder', ''))}"
                       autocomplete="off" />
            </div>
            """
        forms_html += f"""
        <div class="cfg-card {color_class}">
            <div class="cfg-header">
                <h3>{icon} {html.escape(cred['label'])}</h3>
                <p>{html.escape(cred['desc'])}</p>
                <small>Estado actual: <code>{html.escape(est['detalle'][:60])}</code></small>
            </div>
            <form method="POST" action="/config/set" class="cfg-form">
                <input type="hidden" name="key" value="{key}" />
                {fields_html}
                <button type="submit">Actualizar {html.escape(cred['label'])}</button>
            </form>
        </div>
        """

    # Mensaje flash si viene de un redirect
    msg_html = ""
    if msg:
        msg_html = f'<div class="flash-msg">{html.escape(msg)}</div>'

    css_extra = """
    .config-container { max-width: 900px; margin: 0 auto; }
    .ctl-row { display:flex; justify-content:space-between; align-items:center;
               padding: 12px 14px; background:#0f172a; border-radius:8px;
               margin-bottom:8px; }
    .ctl-info { display:flex; align-items:center; gap:10px; flex:1; }
    .ctl-info strong { color:#f1f5f9; font-size:14px; }
    .ctl-info small { color:#94a3b8; font-size:11px; margin-left:auto; padding-right:12px; }
    .ctl-led { width:10px; height:10px; border-radius:50%; flex-shrink:0; }
    .ctl-led.ok { background:#22c55e; box-shadow:0 0 6px rgba(34,197,94,0.6); }
    .ctl-led.down { background:#ef4444; }
    .ctl-status { font-size:12px; font-weight:600; padding:3px 10px; border-radius:12px; }
    .ctl-status.ok { background:rgba(34,197,94,0.15); color:#22c55e; }
    .ctl-status.down { background:rgba(239,68,68,0.15); color:#ef4444; }
    .ctl-actions { display:flex; align-items:center; gap:10px; }
    .ctl-btn { padding:6px 12px; border:none; border-radius:5px; font-weight:600;
               font-size:12px; cursor:pointer; }
    .ctl-green { background:#22c55e; color:white; }
    .ctl-green:hover { background:#16a34a; }
    .ctl-red { background:#ef4444; color:white; }
    .ctl-red:hover { background:#dc2626; }
    .ctl-amber { background:#f59e0b; color:white; }
    .ctl-amber:hover { background:#d97706; }
    .cfg-card {
        background: #1e293b; border: 1px solid #334155; border-radius: 10px;
        padding: 20px; margin-bottom: 20px;
    }
    .cfg-card.ok    { border-left: 4px solid #22c55e; }
    .cfg-card.falta { border-left: 4px solid #ef4444; }
    .cfg-header h3  { margin: 0 0 4px 0; color: #f1f5f9; font-size: 18px; }
    .cfg-header p   { margin: 0 0 8px 0; color: #94a3b8; font-size: 13px; }
    .cfg-header small { color: #64748b; font-size: 11px; }
    .cfg-header code { background: #0f172a; padding: 2px 6px; border-radius: 3px;
                       color: #93c5fd; font-size: 11px; }
    .cfg-form { margin-top: 14px; padding-top: 14px; border-top: 1px solid #334155;
                display: flex; flex-direction: column; gap: 10px; }
    .cfg-field { display: flex; flex-direction: column; gap: 4px; }
    .cfg-field label { font-size: 12px; color: #cbd5e1; }
    .cfg-field input {
        background: #0f172a; border: 1px solid #475569; color: #e2e8f0;
        padding: 8px 12px; border-radius: 4px; font-family: monospace;
        font-size: 13px;
    }
    .cfg-field input:focus { border-color: #3b82f6; outline: none; }
    .cfg-form button {
        background: #3b82f6; color: white; border: none; padding: 10px 16px;
        border-radius: 4px; font-weight: 600; cursor: pointer; align-self: flex-start;
        margin-top: 6px;
    }
    .cfg-form button:hover { background: #2563eb; }
    .flash-msg {
        padding: 14px 20px; border-radius: 8px; margin-bottom: 20px;
        background: #1e293b; border-left: 4px solid #60a5fa; color: #e2e8f0;
    }
    .nav-back {
        display: inline-block; margin: 16px 0; color: #60a5fa;
        text-decoration: none; font-size: 13px;
    }
    .nav-back:hover { text-decoration: underline; }
    """

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="utf-8">
    <title>Catastro Bot — Configuración</title>
    <style>{_CSS}{css_extra}</style>
</head>
<body>
    <header>
        <h1>⚙️ Configuración de credenciales</h1>
        <div class="meta">
            Actualiza las credenciales del bot. Los valores se guardan en Windows Credential Manager (cifrado).
            <br>
            🔒 Esta página solo escucha en 127.0.0.1 — no es accesible desde otra computadora.
        </div>
    </header>
    <div class="container config-container">
        <a href="/" class="nav-back">← Volver al dashboard</a>
        {msg_html}
        <p style="color:#cbd5e1; font-size:13px; line-height:1.5;">
            <strong>Importante:</strong> los valores que pongas acá NO se muestran después.
            Si querés ver el valor actual, mirá el "Estado actual" debajo de cada título
            (solo muestra metadata, no la credencial completa).
            <br><br>
            <strong>Anthropic API key:</strong> generala/rotala en
            <a href="https://console.anthropic.com/settings/keys" target="_blank"
               style="color:#60a5fa">console.anthropic.com</a>.
            <br>
            <strong>Gmail App Password:</strong> requiere 2FA activado.
            Generala en
            <a href="https://myaccount.google.com/apppasswords" target="_blank"
               style="color:#60a5fa">myaccount.google.com/apppasswords</a>.
        </p>
        {control_html}
        {forms_html}
    </div>
    <footer>
        Catastro Bot &middot; <a href="/" style="color:#60a5fa">Dashboard</a>
        &middot; <a href="/api/config" style="color:#60a5fa">API JSON</a>
    </footer>
</body>
</html>
"""


# ── Control del bot (start/stop servicios) ─────────────────────────────

def _matar_procesos(patrones: list[str]) -> dict:
    """Mata procesos python.exe / chrome.exe que matchean cualquiera de los
    patrones (substring en CommandLine). Devuelve {ok, matados, errores}."""
    import subprocess
    matados = []
    errores = []
    for patron in patrones:
        # Identificar process tipo (chrome o python) por el patrón
        proc_name = "chrome.exe" if "chrome" in patron else "python.exe"
        ps_cmd = (
            f"Get-WmiObject Win32_Process | "
            f"Where-Object {{ "
            f"  $_.Name -eq '{proc_name}' -and "
            f"  $_.CommandLine -like '*{patron}*' "
            f"}} | ForEach-Object {{ "
            f"  $_.ProcessId; "
            f"  Stop-Process -Id $_.ProcessId -Force "
            f"}}"
        )
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                capture_output=True, text=True, timeout=10,
            )
            pids = [int(x) for x in r.stdout.split() if x.strip().isdigit()]
            matados.extend(pids)
        except Exception as exc:
            errores.append(f"{patron}: {exc}")
    return {"ok": not errores, "matados": matados, "errores": errores}


def _lanzar_proceso(modulo_o_script: str, *args, detached: bool = True) -> dict:
    """Lanza un proceso python en background. Devuelve {ok, pid}."""
    import subprocess
    try:
        if modulo_o_script.startswith("-m "):
            cmd = [sys.executable, "-m", modulo_o_script[3:]] + list(args)
        else:
            cmd = [sys.executable, modulo_o_script] + list(args)
        flags = 0
        if detached and sys.platform == "win32":
            flags = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "DETACHED_PROCESS", 0)
            )
        proc = subprocess.Popen(
            cmd,
            creationflags=flags,
            close_fds=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=str(ROOT),
        )
        return {"ok": True, "pid": proc.pid}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200]}


def _ejecutar_control(accion: str, servicio: str) -> str:
    """Ejecuta acción sobre un servicio. Devuelve mensaje resultado."""
    import time as _time
    if accion == "encender":
        if servicio == "scheduler":
            r = _lanzar_proceso("-m src.main")
            return f"✅ Scheduler arrancado (PID {r['pid']})" if r["ok"] else f"❌ {r.get('error')}"
        elif servicio == "chrome":
            r = _lanzar_proceso("tools/start_chrome_bot.py")
            return f"✅ Chrome bot arrancando (PID {r['pid']})" if r["ok"] else f"❌ {r.get('error')}"
        elif servicio == "watchdog":
            r = _lanzar_proceso("-m src.utils.healthcheck", "--interval", "60")
            return f"✅ Watchdog arrancado (PID {r['pid']})" if r["ok"] else f"❌ {r.get('error')}"
        elif servicio == "todo":
            # Lanzar Chrome → esperar → watchdog → scheduler
            r1 = _lanzar_proceso("tools/start_chrome_bot.py")
            _time.sleep(2)
            r2 = _lanzar_proceso("-m src.utils.healthcheck", "--interval", "60")
            r3 = _lanzar_proceso("-m src.main")
            ok = all(r.get("ok") for r in (r1, r2, r3))
            return f"{'✅' if ok else '⚠️'} Chrome={r1.get('pid','?')} · Watchdog={r2.get('pid','?')} · Scheduler={r3.get('pid','?')}"
    elif accion == "apagar":
        if servicio == "scheduler":
            r = _matar_procesos(["src.main"])
            return f"✅ Scheduler apagado ({len(r['matados'])} procesos)" if r["ok"] else f"❌ {r['errores']}"
        elif servicio == "chrome":
            r = _matar_procesos(["chrome_profile_apt", "remote-debugging-port=9222"])
            return f"✅ Chrome bot apagado ({len(r['matados'])} procesos)" if r["ok"] else f"❌ {r['errores']}"
        elif servicio == "watchdog":
            r = _matar_procesos(["src.utils.healthcheck"])
            return f"✅ Watchdog apagado ({len(r['matados'])} procesos)" if r["ok"] else f"❌ {r['errores']}"
        elif servicio == "todo":
            # Apagar TODO excepto el dashboard (este proceso)
            r = _matar_procesos([
                "src.main", "src.utils.healthcheck",
                "chrome_profile_apt", "remote-debugging-port=9222",
            ])
            return f"✅ Bot apagado ({len(r['matados'])} procesos matados). Dashboard sigue activo."
    elif accion == "reiniciar":
        # Apagar + esperar + encender (sin tocar dashboard)
        r_kill = _matar_procesos([
            "src.main", "src.utils.healthcheck",
            "chrome_profile_apt", "remote-debugging-port=9222",
        ])
        _time.sleep(3)
        r1 = _lanzar_proceso("tools/start_chrome_bot.py")
        _time.sleep(2)
        r2 = _lanzar_proceso("-m src.utils.healthcheck", "--interval", "60")
        r3 = _lanzar_proceso("-m src.main")
        return f"🔄 Reinicio completo: matados={len(r_kill['matados'])} · Chrome={r1.get('pid','?')} · Watchdog={r2.get('pid','?')} · Scheduler={r3.get('pid','?')}"
    return f"❌ Acción desconocida: {accion}/{servicio}"


def _obtener_estado_bot() -> dict:
    """Inspecciona qué servicios del bot están corriendo.

    Devuelve dict con:
      chrome:    {alive, browser, error?}
      dashboard: {alive, port}  (siempre alive — somos nosotros)
      watchdog:  {running}  (proceso python -m src.utils.healthcheck)
      scheduler: {running}  (proceso python -m src.main)
      anthropic: {configured} — hay key en Cred Manager
      bd:        {ok, size_kb, expedientes_count}
      logs_dir:  {exists, latest_log_age_sec}
    """
    import subprocess
    import urllib.request

    out: dict = {}

    # Chrome del bot
    try:
        with urllib.request.urlopen(
            "http://localhost:9222/json/version", timeout=2.0,
        ) as r:
            data = json.loads(r.read().decode("utf-8"))
            out["chrome"] = {"alive": True, "browser": data.get("Browser", "?")}
    except Exception as exc:
        out["chrome"] = {"alive": False, "error": str(exc)[:80]}

    # Dashboard (nosotros) — siempre alive
    out["dashboard"] = {"alive": True, "port": DEFAULT_PORT}

    # Watchdog + Scheduler — buscar procesos python por CommandLine (PowerShell)
    def _python_corriendo(patron: str) -> bool:
        try:
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"$null -ne (Get-WmiObject Win32_Process | "
                 f"Where-Object {{$_.Name -eq 'python.exe' -and "
                 f"$_.CommandLine -like '*{patron}*'}} | Select-Object -First 1)"],
                capture_output=True, text=True, timeout=5,
            )
            return r.stdout.strip().lower() == "true"
        except Exception:
            return False

    out["watchdog"] = {"running": _python_corriendo("healthcheck")}
    out["scheduler"] = {"running": _python_corriendo("src.main")}

    # Anthropic key
    try:
        from src.core.credential_manager import CredentialManager
        k = CredentialManager().get_anthropic_key()
        out["anthropic"] = {"configured": bool(k and k.startswith("sk-ant-"))}
    except Exception:
        out["anthropic"] = {"configured": False}

    # BD
    try:
        size_kb = DB_PATH.stat().st_size / 1024 if DB_PATH.exists() else 0
        conn = sqlite3.connect(DB_PATH)
        n = conn.execute("SELECT COUNT(*) FROM expedientes").fetchone()[0]
        conn.close()
        out["bd"] = {"ok": True, "size_kb": round(size_kb, 1), "expedientes": n}
    except Exception as exc:
        out["bd"] = {"ok": False, "error": str(exc)[:80]}

    # Logs
    log_file = Path("logs/catastro-bot.log")
    if log_file.exists():
        age = (datetime.now().timestamp() - log_file.stat().st_mtime)
        out["logs"] = {"exists": True, "age_sec": int(age), "size_kb": round(log_file.stat().st_size / 1024, 1)}
    else:
        out["logs"] = {"exists": False}

    return out


def _render_html(refresh_sec: int = 30) -> str:
    exps = _leer_expedientes()
    bot = _obtener_estado_bot()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Contar por etapa
    contador: dict[str, int] = {}
    for e in exps:
        etapa, _, _ = _etapa_info(e["estado"])
        contador[etapa] = contador.get(etapa, 0) + 1

    # Filas
    rows_html = ""
    for e in exps:
        etapa, color, proximo = _etapa_info(e["estado"])
        dias = e["dias"]
        dias_str = f"{dias}d" if dias >= 0 else "—"
        dias_class = ""
        if dias > 7:    dias_class = "viejo"
        if dias > 14:   dias_class = "muy-viejo"
        tramite = e["tramite"] or "—"
        proyecto = html.escape(e.get("proyecto") or "—")[:20]
        cliente = html.escape(e["cliente"] or "—")[:35]
        rows_html += f"""
        <tr>
            <td class="expediente">{html.escape(e["numero"])}</td>
            <td><span class="etapa" style="background:{color}">{html.escape(etapa)}</span></td>
            <td class="tramite">{html.escape(str(tramite))}</td>
            <td class="proyecto">{proyecto}</td>
            <td class="cliente">{cliente}</td>
            <td class="dias {dias_class}">{dias_str}</td>
            <td class="proximo">{html.escape(proximo)}</td>
        </tr>
        """

    if not exps:
        rows_html = '<tr><td colspan="7" class="empty">No hay expedientes registrados todavía.</td></tr>'

    # Cards
    cards_html = ""
    for etapa, n in sorted(contador.items(), key=lambda x: -x[1]):
        # color del card según etapa
        color = "#6b7280"
        for k, (lbl, c, _) in ETAPAS.items():
            if lbl == etapa:
                color = c
                break
        cards_html += f"""
        <div class="card" style="border-left: 4px solid {color}">
            <div class="count">{n}</div>
            <div class="label">{html.escape(etapa)}</div>
        </div>
        """

    # Panel estado del bot
    chrome_ok = bot["chrome"]["alive"]
    bd_ok = bot["bd"].get("ok", False)
    overall_ok = chrome_ok and bd_ok
    pulse_class = "pulse" if overall_ok else "pulse down"
    estado_general = "🟢 Bot operativo" if overall_ok else "🔴 Bot con problemas"

    def _svc(name: str, ok: bool, detail: str = "") -> str:
        cls = "ok" if ok else "down"
        icon = "✅" if ok else "❌"
        return f"""
        <div class="svc {cls}">
            <span class="led"></span>
            <span class="name">{icon} {html.escape(name)}</span>
            <span class="detail">{html.escape(detail)}</span>
        </div>"""

    bot_html = f"""
    <div class="bot-status">
        <h2><span class="{pulse_class}"></span> {estado_general}</h2>
        <div class="bot-services">
            {_svc("Chrome del bot (CDP 9222)", chrome_ok,
                  bot["chrome"].get("browser", "")[:30] if chrome_ok else "caído")}
            {_svc("Watchdog Chrome", bot["watchdog"]["running"],
                  "activo" if bot["watchdog"]["running"] else "no corriendo")}
            {_svc("Scheduler (src.main)", bot["scheduler"]["running"],
                  "polling APT cada 30min" if bot["scheduler"]["running"] else "manual")}
            {_svc("Dashboard web", True, f"puerto {bot['dashboard']['port']}")}
            {_svc("Anthropic API key", bot["anthropic"]["configured"],
                  "configurada" if bot["anthropic"]["configured"] else "FALTA")}
            {_svc("Base de datos", bd_ok,
                  f"{bot['bd'].get('expedientes',0)} expedientes · {bot['bd'].get('size_kb',0)}KB"
                  if bd_ok else bot['bd'].get('error',''))}
            {_svc("Logs", bot['logs']['exists'],
                  f"hace {bot['logs'].get('age_sec',0)//60} min · {bot['logs'].get('size_kb',0)}KB"
                  if bot['logs']['exists'] else "no creado aún")}
        </div>
        <div class="bot-actions">
            ⚙️ <a href="/config" style="color:#60a5fa;font-weight:600">Configurar credenciales</a> &middot;
            🔒 Backup local: <code>catastro-bot backup</code> &middot;
            <br>
            ⌨️ Comandos útiles:
            <code>catastro-bot resumen</code> ·
            <code>catastro-bot health</code> ·
            <code>catastro-bot apt-flujo &lt;EXP&gt;</code> ·
            <code>catastro-bot apt-enviar &lt;EXP&gt;</code> ·
            <code>catastro-bot debug estado &lt;EXP&gt;</code>
            <br>
            🔄 Si Chrome del bot está caído: corré <code>python tools/start_chrome_bot.py</code>
            <br>
            📊 API JSON del estado: <a href="/api/bot-status" style="color:#60a5fa">/api/bot-status</a>
        </div>
    </div>
    """

    # NOTA U-04 paso 6 (2026-05-22):
    # Eliminado <meta http-equiv="refresh" content="N"> — el browser ya no
    # hace full reload cada 30s. En su lugar, cliente JS abre EventSource
    # contra /api/events/stream y recarga la fila/página afectada.
    # Fallback: si SSE no está disponible (endpoint legacy stdlib server
    # SIN flask), el `refresh_sec` se respeta como antes via `noscript`.
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <noscript>
        <!-- Fallback: si JS está deshabilitado, refresh full-page legacy -->
        <meta http-equiv="refresh" content="{refresh_sec}">
    </noscript>
    <title>Catastro Bot — Dashboard</title>
    <style>{_CSS}</style>
    <style>
    /* Banner externo (N-03) — visible solo si algún servicio externo está down */
    #ext-services-banner {{
        display: none; padding: 10px 20px; margin: 0;
        background: rgba(245, 158, 11, 0.12);
        border-bottom: 1px solid rgba(245, 158, 11, 0.4);
        color: #fbbf24; font-size: 13px;
        text-align: center;
    }}
    #ext-services-banner.show {{ display: block; }}
    #ext-services-banner strong {{ color: #f1f5f9; }}
    #ext-services-banner a {{
        color: #60a5fa; text-decoration: none; margin-left: 12px;
    }}
    /* Indicador de sincronización live (U-04 paso 6) */
    #live-status {{
        display: inline-flex; align-items: center; gap: 6px;
        font-size: 12px; padding: 3px 10px; border-radius: 12px;
        background: #1e293b; color: #94a3b8;
        transition: background-color 0.3s, color 0.3s;
    }}
    #live-status::before {{
        content: ""; width: 8px; height: 8px; border-radius: 50%;
        background: #6b7280;
    }}
    #live-status.ok::before {{ background: #10b981; animation: pulse 2s ease-in-out infinite; }}
    #live-status.warn::before {{ background: #f59e0b; }}
    #live-status.err::before {{ background: #ef4444; }}
    #live-status.ok    {{ color: #34d399; }}
    #live-status.warn  {{ color: #fbbf24; }}
    #live-status.err   {{ color: #f87171; }}
    @keyframes pulse {{
        0%, 100% {{ opacity: 1; }}
        50% {{ opacity: 0.4; }}
    }}
    /* Flash visual cuando una fila se actualiza por SSE */
    tr.row-flash {{
        animation: row-flash-anim 1.2s ease-out;
    }}
    @keyframes row-flash-anim {{
        0%  {{ background: rgba(96, 165, 250, 0.35); }}
        100% {{ background: transparent; }}
    }}
    </style>
</head>
<body>
    <div id="ext-services-banner">
        <!-- Inyectado por JS si algún servicio externo está down -->
    </div>
    <header>
        <h1>📐 Catastro Bot — Dashboard</h1>
        <div class="meta">
            Última actualización (server): <strong>{now}</strong> &nbsp;|&nbsp;
            Total expedientes: <strong>{len(exps)}</strong> &nbsp;|&nbsp;
            <span id="live-status" title="Estado de la conexión live (SSE)">
                <span id="live-status-text">conectando…</span>
            </span>
            &nbsp;|&nbsp;
            <a href="/config/control" style="color:#60a5fa">⚙️ Control</a>
            &nbsp;|&nbsp;
            <a href="/config/runtime" style="color:#60a5fa">🖥️ Procesos</a>
            &nbsp;|&nbsp;
            <a href="/config/costos" style="color:#60a5fa">💲 Costos</a>
            &nbsp;|&nbsp;
            <span id="revisiones-link" style="display:none">
                <a href="#" style="color:#fbbf24" id="revisiones-link-a"></a>
                &nbsp;|&nbsp;
            </span>
            <a href="/" style="color:#60a5fa" id="reload-now">refrescar ahora</a>
        </div>
    </header>
    <div class="container">
        {bot_html}
        <div class="cards">{cards_html}</div>
        <table>
            <thead>
                <tr>
                    <th>Expediente</th>
                    <th>Etapa actual</th>
                    <th>Trámite APT</th>
                    <th>Proyecto</th>
                    <th>Cliente / Propietario</th>
                    <th style="text-align:center">Días</th>
                    <th>Próximo paso</th>
                </tr>
            </thead>
            <tbody id="expedientes-tbody">{rows_html}</tbody>
        </table>
    </div>
    <footer>
        Catastro Bot &middot; Datos locales en C:\\catastro-bot\\data\\catastro.db &middot;
        <a href="/api/expedientes">/api/expedientes</a> (JSON) &middot;
        <a href="/api/events/stream">/api/events/stream</a> (SSE)
    </footer>

    <script>
    // ─── Banner de servicios externos (N-03) ───────────────────────────
    (function () {{
        var bannerEl = document.getElementById("ext-services-banner");
        if (!bannerEl) return;

        var FRIENDLY_NAMES = {{
            "green_api": "WhatsApp (Green API)",
            "anthropic": "IA Vision (Anthropic)",
            "drive": "Backup Drive",
            "rnp": "RNP digital"
        }};
        var ACTIONS = {{
            "green_api": '<a href="https://console.green-api.com/" target="_blank">Re-autorizar instancia</a>'
        }};

        function loadStatus() {{
            fetch("/api/external-services")
                .then(function (r) {{ return r.json(); }})
                .then(function (data) {{
                    var down = (data.services || []).filter(function (s) {{
                        return s.status === "down" || s.status === "degraded";
                    }});
                    if (!down.length) {{
                        bannerEl.classList.remove("show");
                        bannerEl.innerHTML = "";
                        return;
                    }}
                    var parts = down.map(function (s) {{
                        var name = FRIENDLY_NAMES[s.service_name] || s.service_name;
                        var info = "<strong>" + name + "</strong> caído";
                        if (s.last_error_code) info += " (" + s.last_error_code + ")";
                        if (ACTIONS[s.service_name]) info += " · " + ACTIONS[s.service_name];
                        return info;
                    }});
                    bannerEl.innerHTML = "⚠️ Servicios externos: " + parts.join(" &nbsp;·&nbsp; ") +
                        ' · El bot está usando fallbacks (ver <a href="/config/runtime">/config/runtime</a>)';
                    bannerEl.classList.add("show");
                }})
                .catch(function () {{ /* ignore */ }});
        }}

        loadStatus();
        setInterval(loadStatus, 30000);  // refresh polling cada 30s
        window._extServicesReload = loadStatus;  // expose para SSE
    }})();

    // ─── Link de revisiones pendientes (N-02) ──────────────────────────
    (function () {{
        var linkEl = document.getElementById("revisiones-link");
        var aEl = document.getElementById("revisiones-link-a");
        if (!linkEl || !aEl) return;

        function loadPendientes() {{
            fetch("/api/revisiones/pendientes")
                .then(function (r) {{ return r.json(); }})
                .then(function (data) {{
                    var revs = data.revisiones || [];
                    if (!revs.length) {{
                        linkEl.style.display = "none";
                        return;
                    }}
                    // Mostrar la más vieja (FIFO) en el header
                    var primera = revs[0];
                    var url = "/expediente/" + encodeURIComponent(primera.expediente_id) + "/revisar-envio";
                    var n = revs.length;
                    aEl.href = url;
                    aEl.textContent = "📋 " + n + " revisión" + (n > 1 ? "es" : "") + " pendiente" + (n > 1 ? "s" : "");
                    linkEl.style.display = "inline";
                }})
                .catch(function () {{ /* ignore */ }});
        }}

        loadPendientes();
        setInterval(loadPendientes, 30000);
        window._revisionesReload = loadPendientes;
    }})();

    // ─── SSE client (U-04 paso 6) ──────────────────────────────────────
    // Conecta a /api/events/stream y reacciona a:
    //   - 'hello' / 'heartbeat'    → mantener UI en 'ok'.
    //   - 'expediente_updated'     → flash fila + full reload (simple y
    //                                robusto; mejorable a row-patch en
    //                                el futuro si se nota lag).
    //   - 'control_state_changed'  → reload (panel del bot puede cambiar).
    // Si la conexión se pierde, el browser reintenta automáticamente via
    // retry directive enviado por el server. Si pasa >2 min sin eventos,
    // mostramos warning visual.
    (function () {{
        var statusEl = document.getElementById("live-status");
        var statusText = document.getElementById("live-status-text");
        var lastEventTs = Date.now();
        var reloadDebounce = null;

        function setStatus(klass, text) {{
            if (!statusEl) return;
            statusEl.classList.remove("ok", "warn", "err");
            statusEl.classList.add(klass);
            if (statusText) statusText.textContent = text;
        }}

        function scheduleReload() {{
            // Debounce: si llegan muchos eventos seguidos (ej. apt-sync que
            // toca varios expedientes), un solo reload los cubre todos.
            if (reloadDebounce) clearTimeout(reloadDebounce);
            reloadDebounce = setTimeout(function () {{ window.location.reload(); }}, 350);
        }}

        function flashRow(numero) {{
            // Buscar la fila por número de expediente. El renderer
            // pone el numero en la primera celda como texto.
            var rows = document.querySelectorAll("#expedientes-tbody tr");
            for (var i = 0; i < rows.length; i++) {{
                var first = rows[i].querySelector("td");
                if (first && first.textContent.indexOf(numero) !== -1) {{
                    rows[i].classList.remove("row-flash");
                    // forzar reflow para reiniciar la animación
                    void rows[i].offsetWidth;
                    rows[i].classList.add("row-flash");
                    break;
                }}
            }}
        }}

        if (!("EventSource" in window)) {{
            setStatus("warn", "sin SSE — refrescá manualmente");
            return;
        }}
        var es = new EventSource("/api/events/stream");

        es.onopen = function () {{
            setStatus("ok", "live ✓");
            lastEventTs = Date.now();
        }};

        es.onmessage = function (e) {{
            lastEventTs = Date.now();
            setStatus("ok", "live ✓");
            try {{
                var data = JSON.parse(e.data);
                if (data.type === "expediente_updated") {{
                    if (data.numero_expediente) flashRow(data.numero_expediente);
                    scheduleReload();
                }} else if (data.type === "control_state_changed") {{
                    scheduleReload();
                }} else if (data.type === "external_service_changed") {{
                    // N-03: refresca el banner sin recargar la página
                    if (window._extServicesReload) window._extServicesReload();
                }} else if (data.type === "revision_pendiente" || data.type === "revision_resuelta") {{
                    // N-02: refresca el link de revisiones pendientes
                    if (window._revisionesReload) window._revisionesReload();
                }}
                // 'hello' y 'heartbeat' solo refrescan lastEventTs (ya hecho arriba).
            }} catch (err) {{
                console.warn("SSE: payload no parseable", e.data);
            }}
        }};

        es.onerror = function () {{
            setStatus("err", "reconectando…");
            // EventSource reconecta solo (con el retry hint del server).
        }};

        // Watchdog visual: si pasa >2 min sin eventos (ni heartbeat),
        // marcar conexión como degradada. El server manda heartbeat
        // cada 25s así que en condiciones normales esto NUNCA dispara.
        setInterval(function () {{
            var silencio_ms = Date.now() - lastEventTs;
            if (silencio_ms > 120000) {{
                setStatus("warn", "sin actividad >2 min");
            }} else if (silencio_ms > 60000) {{
                setStatus("warn", "sin eventos recientes");
            }}
        }}, 10000);
    }})();
    </script>
</body>
</html>
"""


# ── HTTP Server ────────────────────────────────────────────────────────

class _DashboardHandler(BaseHTTPRequestHandler):
    _refresh_sec: int = 30

    def do_GET(self):
        if self.path == "/api/expedientes":
            self._send_json(_leer_expedientes())
            return
        if self.path == "/api/bot-status":
            self._send_json(_obtener_estado_bot())
            return
        if self.path in ("/", "/index.html", "/dashboard"):
            self._send_html(_render_html(self._refresh_sec))
            return
        # /config con posible ?msg=...
        if self.path.startswith("/config"):
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            msg = qs.get("msg", [""])[0]
            self._send_html(_render_config_html(msg=msg))
            return
        if self.path == "/api/config":
            self._send_json(_obtener_config_estado())
            return
        self.send_error(404)

    def do_POST(self):
        """Endpoints POST: /config/set y /control."""
        from urllib.parse import parse_qs, quote
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length).decode("utf-8")
            params = parse_qs(body)

            if self.path == "/config/set":
                key = (params.get("key", [""])[0]).strip()
                val1 = (params.get("val1", [""])[0]).strip()
                val2 = (params.get("val2", [""])[0]).strip()
                resultado = _set_credencial(key, val1, val2)
            elif self.path == "/control":
                accion = (params.get("accion", [""])[0]).strip()
                servicio = (params.get("servicio", [""])[0]).strip()
                resultado = _ejecutar_control(accion, servicio)
            else:
                self.send_error(404)
                return

            # Redirect back to /config con mensaje
            self.send_response(303)
            self.send_header("Location", f"/config?msg={quote(resultado)}")
            self.end_headers()
        except Exception as exc:
            self.send_error(500, f"Error: {exc}")

    def _send_html(self, content: str) -> None:
        body = content.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, data) -> None:
        body = json.dumps(data, ensure_ascii=False, indent=2, default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        log.debug("HTTP %s", format % args)


def start_dashboard_server(port: int = DEFAULT_PORT,
                           refresh_sec: int = 30) -> Optional[HTTPServer]:
    """Arranca el server en thread daemon. Devuelve el server.

    NO bloquea — daemon thread muere con el proceso principal.
    """
    handler_cls = type(
        "_DashHandler", (_DashboardHandler,),
        {"_refresh_sec": refresh_sec},
    )
    try:
        server = HTTPServer(("127.0.0.1", port), handler_cls)
    except OSError as exc:
        log.warning("no se pudo levantar dashboard en :%d — %s", port, exc)
        return None

    def _run():
        try:
            server.serve_forever()
        except Exception:
            log.exception("dashboard server crashed")

    thread = threading.Thread(target=_run, name="dashboard-web", daemon=True)
    thread.start()
    log.info("dashboard escuchando en http://127.0.0.1:%d", port)
    return server


def main() -> int:
    # Forzar UTF-8 en stdout para emojis en consola Windows
    import io as _io
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = _io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                       errors="replace")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"Puerto del dashboard (default {DEFAULT_PORT})")
    parser.add_argument("--refresh", type=int, default=30,
                        help="Auto-refresh en segundos (default 30)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    server = start_dashboard_server(port=args.port, refresh_sec=args.refresh)
    if not server:
        print(f"[ERROR] No se pudo arrancar el dashboard en :{args.port}")
        return 1

    print(f"📐 Dashboard activo: http://localhost:{args.port}")
    print(f"   Auto-refresh cada {args.refresh}s")
    print(f"   API JSON:        http://localhost:{args.port}/api/expedientes")
    print(f"   Detener: Ctrl+C")
    try:
        while True:
            import time as _t
            _t.sleep(1)
    except KeyboardInterrupt:
        print("\n[OK] Dashboard detenido")
        server.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
