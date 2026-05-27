"""Bitácora diaria del bot (Sprint 5 / N-09).

Recopila los eventos del día desde las tablas existentes:
  - estados_historial    → cambios de estado_actual
  - api_costs            → llamadas Anthropic + USD gastado
  - audit_log            → errores y eventos críticos
  - revisiones_pre_envio → pre-envíos aprobados/rechazados (N-02)
  - runtime_processes    → procesos muertos/colgados (U-02)
  - external_services_health → cambios de salud (N-03)

Output:
  - `recopilar(fecha) → dict` con todos los buckets.
  - `formatear_markdown(dict) → str` para guardar en docs/bitacoras/.
  - `formatear_texto(dict) → str` plano para enviar por WhatsApp / email.
  - `formatear_html(dict) → str` para email con tablas.

Diseñado para ser puro (sin side-effects) — el job scheduler invoca + persiste.

Plan: PLAN_MEJORAS Sprint 5 / N-09.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("catastro.daily_log")


def _open(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _fecha_default() -> str:
    """Fecha 'hoy' en hora Costa Rica (UTC-6). Si son las 23:30 CR son
    las 05:30 UTC, así que el job que corre 19:00 CR (01:00 UTC del día
    siguiente) tiene que mirar el día CR de ayer-UTC, no de hoy-UTC."""
    # Convertir UTC → CR (offset -6h fijo, sin DST en CR).
    cr_now = datetime.now(timezone.utc) - timedelta(hours=6)
    return cr_now.strftime("%Y-%m-%d")


def recopilar(db_path: Path, fecha: Optional[str] = None) -> dict:
    """Recopila eventos del día. Fecha en formato YYYY-MM-DD (default: hoy CR)."""
    fecha = fecha or _fecha_default()
    inicio = f"{fecha}T00:00:00"
    fin = f"{fecha}T23:59:59"

    with _open(db_path) as conn:
        # ── Cambios de estado ──────────────────────────────────────
        estados = list(conn.execute(
            """SELECT h.id, h.expediente_id, h.estado_anterior, h.estado_nuevo,
                      h.timestamp, h.actor, h.detalles,
                      e.numero_expediente
                 FROM estados_historial h
            LEFT JOIN expedientes e ON e.id = h.expediente_id
                WHERE h.timestamp >= ? AND h.timestamp <= ?
             ORDER BY h.timestamp ASC""",
            (inicio, fin),
        ))

        # ── Costos Anthropic ───────────────────────────────────────
        costos = list(conn.execute(
            """SELECT model, tipo, expediente_id, input_tokens, output_tokens,
                      cost_usd, ts, duration_ms
                 FROM api_costs
                WHERE ts >= ? AND ts <= ?
             ORDER BY ts ASC""",
            (inicio, fin),
        ))
        costos_resumen = list(conn.execute(
            """SELECT model, tipo, COUNT(*) AS n,
                      SUM(input_tokens) AS in_tok,
                      SUM(output_tokens) AS out_tok,
                      ROUND(SUM(cost_usd), 4) AS total_usd
                 FROM api_costs
                WHERE ts >= ? AND ts <= ?
             GROUP BY model, tipo
             ORDER BY total_usd DESC""",
            (inicio, fin),
        ))
        total_usd = sum(float(r["total_usd"] or 0) for r in costos_resumen)

        # ── Errores del audit_log ──────────────────────────────────
        errores = list(conn.execute(
            """SELECT timestamp, actor, expediente_id, accion, detalles_json
                 FROM audit_log
                WHERE timestamp >= ? AND timestamp <= ?
                  AND (accion LIKE '%error%'
                       OR accion LIKE '%fail%'
                       OR accion LIKE '%down%'
                       OR accion LIKE '%hanging%'
                       OR accion LIKE 'whatsapp.fallback%'
                       OR accion LIKE 'greenapi.%')
             ORDER BY timestamp ASC""",
            (inicio, fin),
        ))

        # ── Revisiones pre-envío (N-02) ────────────────────────────
        try:
            revisiones = list(conn.execute(
                """SELECT id, expediente_id, estado, creado_at, resuelto_at,
                          resuelto_por, razon_rechazo, n_discrepancias
                     FROM revisiones_pre_envio
                    WHERE creado_at >= ? AND creado_at <= ?
                 ORDER BY creado_at ASC""",
                (inicio, fin),
            ))
        except sqlite3.OperationalError:
            # Tabla puede no existir si la BD productiva no se ha
            # re-inicializado tras aplicar el schema de N-02. Es tolerante.
            revisiones = []

        # ── Procesos muertos/colgados (U-02) ───────────────────────
        try:
            proc_eventos = list(conn.execute(
                """SELECT process_name, pid, status, started_at, stopped_at,
                          stop_reason
                     FROM runtime_processes
                    WHERE (stopped_at >= ? AND stopped_at <= ?)
                       OR (status = 'hanging' AND last_heartbeat_at >= ?)
                 ORDER BY COALESCE(stopped_at, last_heartbeat_at) ASC""",
                (inicio, fin, inicio),
            ))
        except sqlite3.OperationalError:
            proc_eventos = []  # tabla todavía no existe en algunos branches

        # ── Cambios de salud externa (N-03) ────────────────────────
        try:
            ext_eventos = list(conn.execute(
                """SELECT service_name, status, last_down_at, last_up_at,
                          last_error_code, last_error_message
                     FROM external_services_health
                    WHERE (last_down_at >= ? AND last_down_at <= ?)
                       OR (last_up_at >= ? AND last_up_at <= ?)
                 ORDER BY service_name""",
                (inicio, fin, inicio, fin),
            ))
        except sqlite3.OperationalError:
            ext_eventos = []

    # Cuentas resumen
    n_estados = len(estados)
    cambios_a = {}
    for e in estados:
        nuevo = e["estado_nuevo"]
        cambios_a[nuevo] = cambios_a.get(nuevo, 0) + 1

    enviados_cfia = sum(1 for e in estados
                        if e["estado_nuevo"] in ("presentado_apt_r1", "enviado_cfia"))
    inscritos = sum(1 for e in estados if e["estado_nuevo"] in ("inscrito",))
    defectuosos = sum(1 for e in estados if e["estado_nuevo"] in ("defectuoso",))

    return {
        "fecha": fecha,
        "generado_at": datetime.now(timezone.utc).isoformat(),
        "resumen": {
            "n_eventos_estado": n_estados,
            "n_enviados_cfia": enviados_cfia,
            "n_inscritos": inscritos,
            "n_defectuosos": defectuosos,
            "cambios_a": cambios_a,
            "n_llamadas_anthropic": len(costos),
            "total_usd_anthropic": round(total_usd, 4),
            "n_errores": len(errores),
            "n_revisiones": len(revisiones),
            "n_procesos_problemas": len(proc_eventos),
            "n_servicios_externos_eventos": len(ext_eventos),
        },
        "estados": [dict(r) for r in estados],
        "costos": [dict(r) for r in costos],
        "costos_resumen": [dict(r) for r in costos_resumen],
        "errores": [dict(r) for r in errores],
        "revisiones": [dict(r) for r in revisiones],
        "procesos": [dict(r) for r in proc_eventos],
        "servicios_externos": [dict(r) for r in ext_eventos],
    }


# ─── Formatters ──────────────────────────────────────────────────────

def formatear_markdown(data: dict) -> str:
    """Genera bitácora en markdown para guardar en docs/bitacoras/."""
    fecha = data["fecha"]
    r = data["resumen"]
    lines = [
        f"# Bitácora del bot — {fecha}",
        "",
        f"Generado: {data['generado_at']}",
        "",
        "## Resumen del día",
        "",
        f"- **Eventos de cambio de estado:** {r['n_eventos_estado']}",
        f"- **Planos enviados al CFIA:** {r['n_enviados_cfia']}",
        f"- **Planos inscritos:** {r['n_inscritos']}",
        f"- **Planos defectuosos (R1 rechazado):** {r['n_defectuosos']}",
        f"- **Llamadas Anthropic:** {r['n_llamadas_anthropic']}",
        f"- **Costo Anthropic del día:** ${r['total_usd_anthropic']:.4f} USD",
        f"- **Errores registrados:** {r['n_errores']}",
        f"- **Revisiones pre-envío:** {r['n_revisiones']}",
        f"- **Procesos con problemas:** {r['n_procesos_problemas']}",
        f"- **Servicios externos con eventos:** {r['n_servicios_externos_eventos']}",
        "",
    ]

    # Cambios de estado detallados
    if data["estados"]:
        lines.append("## Cambios de estado")
        lines.append("")
        lines.append("| Hora | Expediente | De → A | Actor |")
        lines.append("|---|---|---|---|")
        for e in data["estados"]:
            hora = (e["timestamp"] or "")[11:19]
            num = e.get("numero_expediente") or (e["expediente_id"] or "")[:8]
            de = e["estado_anterior"] or "—"
            a = e["estado_nuevo"]
            actor = e["actor"] or "—"
            lines.append(f"| {hora} | {num} | `{de}` → `{a}` | {actor} |")
        lines.append("")

    # Costos
    if data["costos_resumen"]:
        lines.append("## Costos Anthropic")
        lines.append("")
        lines.append("| Modelo | Tipo | Llamadas | Input tok | Output tok | USD |")
        lines.append("|---|---|---:|---:|---:|---:|")
        for c in data["costos_resumen"]:
            modelo = (c["model"] or "?").replace("claude-", "").replace("-20", " 20")[:25]
            lines.append(
                f"| {modelo} | {c['tipo'] or '-'} | {c['n']} | "
                f"{c['in_tok'] or 0:,} | {c['out_tok'] or 0:,} | "
                f"${c['total_usd'] or 0:.4f} |"
            )
        lines.append("")
        lines.append(f"**Total del día:** ${r['total_usd_anthropic']:.4f} USD")
        lines.append("")

    # Revisiones
    if data["revisiones"]:
        lines.append("## Revisiones pre-envío (N-02)")
        lines.append("")
        lines.append("| Creada | Expediente | Estado | Discrepancias | Resuelto por |")
        lines.append("|---|---|---|---:|---|")
        for rev in data["revisiones"]:
            hora = (rev["creado_at"] or "")[11:19]
            exp = (rev["expediente_id"] or "")[:18]
            estado = rev["estado"]
            ndisc = rev["n_discrepancias"]
            resuelto = rev["resuelto_por"] or "—"
            lines.append(f"| {hora} | {exp} | {estado} | {ndisc} | {resuelto} |")
        lines.append("")

    # Errores
    if data["errores"]:
        lines.append("## Errores y eventos críticos")
        lines.append("")
        for err in data["errores"][:20]:  # max 20 para no inflar
            hora = (err["timestamp"] or "")[11:19]
            accion = err["accion"]
            exp = err["expediente_id"] or "—"
            detalles = err["detalles_json"] or ""
            if len(detalles) > 120:
                detalles = detalles[:120] + "…"
            lines.append(f"- **{hora}** `{accion}` (exp={exp}) — {detalles}")
        if len(data["errores"]) > 20:
            lines.append(f"- _…y {len(data['errores']) - 20} más._")
        lines.append("")

    # Procesos
    if data["procesos"]:
        lines.append("## Procesos del sistema (U-02)")
        lines.append("")
        for p in data["procesos"]:
            stop = (p.get("stopped_at") or "")[11:19] or "—"
            lines.append(
                f"- **{p['process_name']}** pid={p['pid']} → "
                f"`{p['status']}` ({p.get('stop_reason') or '—'}, {stop})"
            )
        lines.append("")

    # Servicios externos
    if data["servicios_externos"]:
        lines.append("## Servicios externos (N-03)")
        lines.append("")
        for s in data["servicios_externos"]:
            lines.append(
                f"- **{s['service_name']}**: status=`{s['status']}` "
                f"(error_code={s.get('last_error_code') or '—'})"
            )
        lines.append("")

    if not any(data[k] for k in ("estados", "costos", "errores", "revisiones",
                                  "procesos", "servicios_externos")):
        lines.append("_Día sin actividad registrada._")
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("Generado automáticamente por `catastro-bot` (N-09).")
    return "\n".join(lines)


def formatear_texto(data: dict) -> str:
    """Versión compacta plana para WhatsApp / preview email."""
    fecha = data["fecha"]
    r = data["resumen"]
    body = [
        f"📋 Bitácora {fecha}",
        "",
        f"• Cambios de estado: {r['n_eventos_estado']}",
    ]
    if r["n_enviados_cfia"]:
        body.append(f"• Enviados al CFIA: {r['n_enviados_cfia']}")
    if r["n_inscritos"]:
        body.append(f"• Inscritos: {r['n_inscritos']}")
    if r["n_defectuosos"]:
        body.append(f"• ⚠️ Defectuosos: {r['n_defectuosos']}")
    body.append(f"• Llamadas IA: {r['n_llamadas_anthropic']}  (${r['total_usd_anthropic']:.2f} USD)")
    if r["n_errores"]:
        body.append(f"• ⚠️ Errores: {r['n_errores']}")
    if r["n_revisiones"]:
        body.append(f"• Revisiones pre-envío: {r['n_revisiones']}")
    if r["n_procesos_problemas"]:
        body.append(f"• ⚠️ Procesos con problemas: {r['n_procesos_problemas']}")
    if r["n_servicios_externos_eventos"]:
        body.append(f"• Servicios externos: {r['n_servicios_externos_eventos']} eventos")
    body.append("")
    body.append("Ver detalle: http://localhost:9224/api/bitacora?fecha=" + fecha)
    return "\n".join(body)


def formatear_html(data: dict) -> str:
    """Markdown convertido a HTML simple. Sin libs externas — usa <pre>
    para el contenido + un wrapper bonito."""
    fecha = data["fecha"]
    md = formatear_markdown(data)
    # Convertir el .md a HTML básico: headings + tablas → render literal en <pre>
    # con CSS monospace. Más simple, suficiente para Gmail.
    import html as _h
    esc = _h.escape(md)
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8"><style>'
        'body{font-family:-apple-system,Segoe UI,sans-serif;background:#0f172a;color:#e2e8f0;padding:24px}'
        'h1,h2{color:#f1f5f9}'
        'pre{background:#1e293b;padding:18px;border-radius:8px;'
        'border:1px solid #334155;white-space:pre-wrap;font-size:13px;'
        'font-family:Consolas,Cascadia Code,monospace;line-height:1.5}'
        '</style></head><body>'
        f'<h1>Bitácora catastro-bot — {fecha}</h1>'
        f'<pre>{esc}</pre>'
        '</body></html>'
    )


# ─── Persistencia ────────────────────────────────────────────────────

def guardar_bitacora(root: Path, fecha: str, contenido_md: str) -> Path:
    """Guarda el .md en docs/bitacoras/YYYY-MM-DD.md. Devuelve el path."""
    out_dir = root / "docs" / "bitacoras"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{fecha}.md"
    out_path.write_text(contenido_md, encoding="utf-8")
    return out_path


def leer_bitacora(root: Path, fecha: str) -> Optional[str]:
    p = root / "docs" / "bitacoras" / f"{fecha}.md"
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8")


def listar_bitacoras(root: Path) -> list[str]:
    """Devuelve lista de fechas YYYY-MM-DD con bitácora guardada."""
    d = root / "docs" / "bitacoras"
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.md"))


__all__ = [
    "recopilar",
    "formatear_markdown",
    "formatear_texto",
    "formatear_html",
    "guardar_bitacora",
    "leer_bitacora",
    "listar_bitacoras",
]
