"""Panel HTML side-by-side de revisión pre-envío (N-02 sub-paso C).

Renderiza la página /expediente/<exp_id>/revisar-envio:
  - Selecciona la revisión más reciente del expediente.
  - Muestra PDF anverso (iframe) a la izquierda.
  - Screenshot del portal CFIA a la derecha.
  - Checklist de campos críticos con check verde / rojo según diff.
  - Botones Aprobar / Rechazar con confirmación.

Plan: PLAN_MEJORAS Sprint 5 / N-02 sub-paso C.
"""
from __future__ import annotations

import html as _html
import json
from pathlib import Path


_CSS = r"""
* { box-sizing: border-box; }
body {
    font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
    margin: 0; background: #0f172a; color: #e2e8f0;
    height: 100vh; display: flex; flex-direction: column;
}
header {
    background: linear-gradient(135deg, #1e293b 0%, #334155 100%);
    padding: 16px 28px;
    border-bottom: 3px solid #3b82f6;
    flex-shrink: 0;
}
header h1 { margin: 0 0 3px 0; font-size: 18px; color: #f1f5f9; }
header .meta { font-size: 12px; color: #94a3b8; }
header .meta a { color: #60a5fa; text-decoration: none; }
header .meta .badge {
    display: inline-block; font-size: 11px; padding: 2px 8px;
    border-radius: 10px; margin-left: 8px;
    background: rgba(245, 158, 11, 0.15); color: #fbbf24;
    font-weight: 700;
}
header .meta .badge.ok { background: rgba(34, 197, 94, 0.15); color: #34d399; }
header .meta .badge.err { background: rgba(239, 68, 68, 0.15); color: #f87171; }

.main {
    flex: 1; display: flex; min-height: 0;  /* min-height:0 para que flex children scrolleen */
}
.pane {
    flex: 1; min-width: 0;
    display: flex; flex-direction: column;
    border-right: 1px solid #334155;
}
.pane:last-child { border-right: none; }
.pane > h2 {
    margin: 0; padding: 10px 18px;
    background: #1e293b; border-bottom: 1px solid #334155;
    color: #cbd5e1; font-size: 13px;
    text-transform: uppercase; letter-spacing: 0.04em;
}
.pane .content { flex: 1; overflow: auto; background: #1e293b; }
.pane iframe, .pane img {
    width: 100%; height: 100%; border: 0;
    background: #0f172a;
}
.pane img {
    object-fit: contain; max-height: 100%;
    display: block;
}
.pane .empty {
    padding: 40px; text-align: center; color: #64748b;
    font-style: italic;
}

.diff-pane {
    flex: 0 0 360px;
    display: flex; flex-direction: column;
    background: #1e293b; border-left: 1px solid #334155;
}
.diff-pane h2 {
    margin: 0; padding: 10px 18px;
    background: #0f172a; border-bottom: 1px solid #334155;
    color: #cbd5e1; font-size: 13px;
    text-transform: uppercase; letter-spacing: 0.04em;
}
.diff-list {
    flex: 1; overflow: auto; padding: 10px 16px;
}
.diff-item {
    padding: 9px 12px; margin-bottom: 6px;
    border-radius: 6px; background: #0f172a;
    border-left: 3px solid #6b7280;
    font-size: 12px;
}
.diff-item.ok { border-left-color: #22c55e; }
.diff-item.err { border-left-color: #ef4444; }
.diff-item .campo {
    font-weight: 600; color: #f1f5f9;
    display: flex; justify-content: space-between; align-items: center;
}
.diff-item .campo .check {
    color: #22c55e; font-size: 14px;
}
.diff-item .campo .x {
    color: #ef4444; font-size: 14px;
}
.diff-item .values {
    margin-top: 6px; display: grid;
    grid-template-columns: max-content 1fr; gap: 2px 8px;
    font-family: "Consolas", monospace; font-size: 11px;
}
.diff-item .values .k { color: #64748b; }
.diff-item .values .v { color: #cbd5e1; word-break: break-all; }
.diff-item .values .v.bad { color: #f87171; }

.actions {
    flex-shrink: 0;
    padding: 14px 18px;
    background: #0f172a; border-top: 1px solid #334155;
    display: grid; grid-template-columns: 1fr 1fr; gap: 10px;
}
.actions button {
    padding: 11px 14px; border: none; border-radius: 6px;
    font-weight: 700; font-size: 13px; cursor: pointer;
}
.actions .approve { background: #22c55e; color: white; }
.actions .approve:hover { background: #16a34a; }
.actions .reject  { background: #ef4444; color: white; }
.actions .reject:hover  { background: #dc2626; }
.actions button:disabled { opacity: 0.5; cursor: not-allowed; }

.modal-bg {
    position: fixed; inset: 0; background: rgba(0,0,0,0.7);
    display: flex; align-items: center; justify-content: center;
    z-index: 1000;
}
.modal {
    background: #1e293b; padding: 22px; border-radius: 10px;
    width: 90%; max-width: 460px; border: 1px solid #334155;
}
.modal h3 { margin: 0 0 10px 0; color: #f1f5f9; }
.modal p { color: #cbd5e1; font-size: 13px; margin: 0 0 12px 0; }
.modal input, .modal textarea {
    width: 100%; padding: 9px 12px; border-radius: 5px;
    background: #0f172a; border: 1px solid #475569; color: #e2e8f0;
    font-size: 13px; font-family: inherit;
}
.modal textarea { min-height: 70px; resize: vertical; }
.modal .modal-actions {
    display: flex; gap: 10px; justify-content: flex-end;
    margin-top: 14px;
}
.modal button {
    padding: 8px 16px; border: none; border-radius: 5px;
    cursor: pointer; font-weight: 600; font-size: 13px;
}
.modal .cancel { background: #475569; color: white; }
.modal .ok { background: #3b82f6; color: white; }
.modal .ok.danger { background: #ef4444; }

#flash {
    position: fixed; bottom: 22px; right: 22px;
    padding: 13px 18px; border-radius: 6px;
    background: #1e293b; color: #e2e8f0; font-size: 13px;
    border-left: 4px solid #3b82f6; max-width: 320px;
    box-shadow: 0 4px 14px rgba(0,0,0,0.5);
    opacity: 0; transition: opacity 0.2s; z-index: 2000;
}
#flash.show { opacity: 1; }
#flash.ok { border-left-color: #22c55e; }
#flash.err { border-left-color: #ef4444; }
"""


_JS = r"""
'use strict';

var EXP_ID = window._expedienteId;
var REV = null;  // se rellena al cargar

function $(s) { return document.querySelector(s); }

function flash(msg, klass) {
    var el = $('#flash');
    el.textContent = msg;
    el.className = 'show ' + (klass || '');
    setTimeout(function () { el.classList.remove('show'); }, 4000);
}

function fmtVal(v) {
    if (v === null || v === undefined) return '(vacío)';
    if (typeof v === 'string') return v;
    return JSON.stringify(v);
}

function loadRevision() {
    fetch('/api/revisiones/pendientes')
        .then(function (r) { return r.json(); })
        .then(function (data) {
            var pendiente = (data.revisiones || []).find(function (r) {
                return r.expediente_id === EXP_ID;
            });
            if (!pendiente) {
                $('#diff-list').innerHTML =
                    '<div style="padding:40px;text-align:center;color:#64748b">' +
                    'No hay revisión pendiente para este expediente.<br><br>' +
                    '<a href="/" style="color:#60a5fa">&larr; Volver al dashboard</a>' +
                    '</div>';
                $('#pane-pdf .content').innerHTML = '<div class="empty">Sin revisión.</div>';
                $('#pane-screenshot .content').innerHTML = '<div class="empty">Sin revisión.</div>';
                $('.actions').style.display = 'none';
                return;
            }
            REV = pendiente;
            renderRevision(pendiente);
        });
}

function renderRevision(rev) {
    // Header info
    var badge = '<span class="badge">' + rev.n_discrepancias + ' discrepancias</span>';
    if (rev.n_discrepancias === 0) {
        badge = '<span class="badge ok">sin discrepancias &check;</span>';
    } else if (rev.n_discrepancias > 3) {
        badge = '<span class="badge err">' + rev.n_discrepancias + ' discrepancias</span>';
    }
    $('#header-badge').innerHTML = badge;
    $('#header-revid').textContent = rev.id.slice(0, 8) + '...';
    $('#header-creado').textContent = rev.creado_at.slice(0, 19).replace('T', ' ');

    // PDF
    var pdfPane = $('#pane-pdf .content');
    if (rev.pdf_anverso_path) {
        pdfPane.innerHTML = '<iframe src="/api/revisiones/' + rev.id + '/pdf"></iframe>';
    } else {
        pdfPane.innerHTML = '<div class="empty">(Sin PDF anverso registrado)</div>';
    }

    // Screenshot
    var ssPane = $('#pane-screenshot .content');
    if (rev.screenshot_path) {
        ssPane.innerHTML = '<img src="/api/revisiones/' + rev.id + '/screenshot.png" alt="Portal CFIA">';
    } else {
        ssPane.innerHTML = '<div class="empty">(Sin screenshot capturado)</div>';
    }

    // Diff list
    renderDiff(rev);
}

function renderDiff(rev) {
    var seed = rev.seed || {};
    var portal = rev.snapshot_dom || {};
    var diffs = rev.diff || [];

    // Indexar diffs por campo
    var byCampo = {};
    diffs.forEach(function (d) { byCampo[d.campo] = d; });

    // Lista TODOS los campos del seed con check/x según si hay diff
    var allCampos = Object.keys(seed).sort();
    if (!allCampos.length) {
        $('#diff-list').innerHTML =
            '<div class="empty" style="padding:30px;text-align:center;color:#64748b">' +
            'Sin datos del seed.</div>';
        return;
    }

    var html = '';
    allCampos.forEach(function (campo) {
        var diff = byCampo[campo];
        var ok = !diff;
        var iconHtml = ok ? '<span class="check">&check;</span>'
                          : '<span class="x">&times;</span>';
        html += '<div class="diff-item ' + (ok ? 'ok' : 'err') + '">' +
                  '<div class="campo"><span>' + campo + '</span>' + iconHtml + '</div>' +
                  '<div class="values">' +
                    '<span class="k">seed:</span>' +
                    '<span class="v">' + escapeHtml(fmtVal(seed[campo])) + '</span>' +
                    '<span class="k">portal:</span>' +
                    '<span class="v' + (ok ? '' : ' bad') + '">' +
                    escapeHtml(fmtVal(portal[campo])) + '</span>' +
                  '</div>' +
                '</div>';
    });
    $('#diff-list').innerHTML = html;
}

function escapeHtml(s) {
    var div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
}

// ─── Modal ────────────────────────────────────────────────────────
function modal(opts) {
    return new Promise(function (resolve) {
        var bg = document.createElement('div');
        bg.className = 'modal-bg';
        var input = opts.textarea
            ? document.createElement('textarea')
            : document.createElement('input');
        input.type = 'text';
        if (opts.placeholder) input.placeholder = opts.placeholder;
        if (opts.value) input.value = opts.value;
        var ok = document.createElement('button');
        ok.textContent = opts.okText || 'Confirmar';
        ok.className = 'ok ' + (opts.danger ? 'danger' : '');
        ok.addEventListener('click', function () { bg.remove(); resolve(input.value); });
        var cancel = document.createElement('button');
        cancel.textContent = 'Cancelar';
        cancel.className = 'cancel';
        cancel.addEventListener('click', function () { bg.remove(); resolve(null); });
        bg.addEventListener('click', function (e) {
            if (e.target === bg) { bg.remove(); resolve(null); }
        });
        var inner = document.createElement('div');
        inner.className = 'modal';
        var h = document.createElement('h3'); h.textContent = opts.title; inner.appendChild(h);
        if (opts.body) {
            var p = document.createElement('p'); p.innerHTML = opts.body; inner.appendChild(p);
        }
        inner.appendChild(input);
        var acts = document.createElement('div'); acts.className = 'modal-actions';
        acts.appendChild(cancel); acts.appendChild(ok);
        inner.appendChild(acts);
        bg.appendChild(inner);
        document.body.appendChild(bg);
        setTimeout(function () { input.focus(); }, 50);
    });
}

// ─── Aprobar ──────────────────────────────────────────────────────
function aprobar() {
    if (!REV) return;
    var msg = '¿Aprobar y proceder a enviar al CFIA?';
    if (REV.n_discrepancias > 0) {
        msg = '⚠️ <strong>' + REV.n_discrepancias + ' discrepancias detectadas.</strong><br>' +
              '¿Aprobar y enviar de todas formas?';
    }
    modal({
        title: '¿Aprobar y enviar?',
        body: msg,
        placeholder: 'Nota opcional (queda en audit log)',
        okText: 'Aprobar y enviar',
    }).then(function (resp) {
        if (resp === null) return;
        fetch('/api/revisiones/' + REV.id + '/aprobar', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Actor': 'web_dashboard' },
            body: JSON.stringify({ nota: resp || '' }),
        }).then(function (r) { return r.json(); }).then(function (data) {
            if (data.error) { flash('Error: ' + data.error, 'err'); return; }
            flash('✓ Aprobado. El bot va a hacer click en Enviar.', 'ok');
            setTimeout(function () { window.location.href = '/'; }, 1500);
        });
    });
}
window.aprobar = aprobar;

// ─── Rechazar ─────────────────────────────────────────────────────
function rechazar() {
    if (!REV) return;
    modal({
        title: '¿Rechazar revisión?',
        body: 'El bot NO va a enviar. Razón obligatoria:',
        textarea: true,
        placeholder: 'Por qué se rechaza (ej: falta verificar carta de agua)',
        okText: 'Rechazar',
        danger: true,
    }).then(function (resp) {
        if (resp === null) return;
        if (!resp.trim()) { flash('La razón es obligatoria.', 'err'); return; }
        fetch('/api/revisiones/' + REV.id + '/rechazar', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Actor': 'web_dashboard' },
            body: JSON.stringify({ razon: resp.trim() }),
        }).then(function (r) { return r.json(); }).then(function (data) {
            if (data.error) { flash('Error: ' + data.error, 'err'); return; }
            flash('✗ Rechazado.', 'err');
            setTimeout(function () { window.location.href = '/'; }, 1500);
        });
    });
}
window.rechazar = rechazar;

loadRevision();
"""


def render_revision_panel_html(expediente_id: str) -> str:
    from src.web.csrf_js import CSRF_FETCH_WRAPPER_JS as _csrf_js  # noqa: F841
    exp_safe = _html.escape(expediente_id)
    js_with_exp = (
        f"window._expedienteId = {json.dumps(expediente_id)};\n" + _JS
    )
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Catastro Bot - Revisión pre-envío</title>
    <style>{_CSS}</style>
</head>
<body>
    <header>
        <h1>📋 Revisión visual pre-envío al CFIA</h1>
        <div class="meta">
            Expediente: <strong>{exp_safe}</strong> ·
            Revisión <span id="header-revid">cargando...</span> ·
            Creada: <span id="header-creado">-</span>
            <span id="header-badge"></span>
            ·
            <a href="/">&larr; dashboard</a>
        </div>
    </header>

    <div class="main">
        <div class="pane" id="pane-pdf">
            <h2>📄 PDF anverso del plano</h2>
            <div class="content"><div class="empty">cargando...</div></div>
        </div>

        <div class="pane" id="pane-screenshot">
            <h2>🖥️ Portal CFIA (snapshot)</h2>
            <div class="content"><div class="empty">cargando...</div></div>
        </div>

        <div class="diff-pane">
            <h2>🔎 Campos críticos</h2>
            <div id="diff-list" class="diff-list">cargando...</div>
            <div class="actions">
                <button class="reject" onclick="rechazar()">✗ Rechazar</button>
                <button class="approve" onclick="aprobar()">✓ Aprobar y enviar</button>
            </div>
        </div>
    </div>

    <div id="flash"></div>

    <script>{_csrf_js}
{js_with_exp}</script>
</body>
</html>
"""
