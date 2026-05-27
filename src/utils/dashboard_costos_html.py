"""Panel HTML /config/costos — costos Anthropic (Sprint 4 / O-08 sub-paso C).

Consume:
  - GET /api/costs/summary
  - GET /api/costs/recent
  - POST /api/costs/budget

Plan: PLAN_MEJORAS Sprint 4 / O-08.
"""
from __future__ import annotations

_COSTOS_CSS = r"""
* { box-sizing: border-box; }
body {
    font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
    margin: 0; background: #0f172a; color: #e2e8f0;
}
header {
    background: linear-gradient(135deg, #1e293b 0%, #334155 100%);
    padding: 22px 32px;
    border-bottom: 3px solid #3b82f6;
}
header h1 { margin: 0 0 4px 0; font-size: 22px; color: #f1f5f9; }
header .meta { font-size: 13px; color: #94a3b8; }
header .meta a { color: #60a5fa; text-decoration: none; }
.container { max-width: 1100px; margin: 24px auto; padding: 0 24px; }

.budget-card {
    background: #1e293b; border: 1px solid #334155; border-radius: 10px;
    padding: 18px 22px; margin-bottom: 24px;
}
.budget-card h2 { margin: 0 0 8px 0; color: #f1f5f9; font-size: 17px; }
.budget-card .spend-row {
    display: flex; justify-content: space-between; align-items: baseline;
    margin-bottom: 8px;
}
.budget-card .spend-row .now {
    font-size: 28px; font-weight: 700; color: #60a5fa;
}
.budget-card .spend-row .of {
    font-size: 13px; color: #94a3b8;
}
.budget-card .bar {
    height: 14px; background: #0f172a; border-radius: 7px; overflow: hidden;
    margin-bottom: 8px;
}
.budget-card .bar > .fill {
    height: 100%; background: linear-gradient(90deg, #22c55e, #3b82f6);
    transition: width 0.4s ease, background 0.4s ease;
}
.budget-card.warn .bar > .fill { background: linear-gradient(90deg, #f59e0b, #ef4444); }
.budget-card.over .bar > .fill { background: #ef4444; }
.budget-card .ratio { font-size: 12px; color: #94a3b8; }
.budget-card .edit-row {
    display: flex; gap: 10px; margin-top: 14px; align-items: center;
    flex-wrap: wrap;
}
.budget-card .edit-row label { font-size: 12px; color: #cbd5e1; }
.budget-card .edit-row input {
    background: #0f172a; border: 1px solid #475569; color: #e2e8f0;
    padding: 6px 10px; border-radius: 4px; width: 90px;
    font-family: monospace; font-size: 13px;
}
.budget-card .edit-row button {
    background: #3b82f6; color: white; border: none;
    padding: 7px 14px; border-radius: 5px; font-weight: 600;
    cursor: pointer; font-size: 12px;
}
.budget-card .edit-row button:hover { background: #2563eb; }

.section { margin-top: 26px; }
.section h2 { margin: 0 0 12px 0; color: #f1f5f9; font-size: 17px; }

table {
    width: 100%; border-collapse: collapse;
    background: #1e293b; border: 1px solid #334155; border-radius: 8px;
    overflow: hidden;
}
th, td { padding: 9px 12px; text-align: left; border-bottom: 1px solid #334155; }
th { background: #0f172a; color: #94a3b8; font-size: 11px;
     text-transform: uppercase; letter-spacing: 0.05em; }
td { font-size: 12px; }
td.num, th.num { text-align: right; font-family: monospace; }
td.usd { color: #34d399; font-weight: 600; }

.daily-bars {
    display: flex; align-items: flex-end; gap: 3px;
    height: 100px; margin-bottom: 10px;
    padding: 8px; background: #1e293b; border-radius: 8px;
    border: 1px solid #334155;
}
.daily-bars .bar {
    flex: 1; min-width: 6px; background: #3b82f6; border-radius: 3px 3px 0 0;
    position: relative; min-height: 2px;
    transition: background 0.2s;
}
.daily-bars .bar:hover { background: #60a5fa; }
.daily-bars .bar .tooltip {
    position: absolute; bottom: 100%; left: 50%;
    transform: translateX(-50%); background: #0f172a;
    padding: 4px 8px; border-radius: 4px;
    font-size: 11px; white-space: nowrap;
    opacity: 0; pointer-events: none; transition: opacity 0.15s;
    border: 1px solid #475569;
}
.daily-bars .bar:hover .tooltip { opacity: 1; }

#live-status {
    display: inline-flex; align-items: center; gap: 6px;
    font-size: 12px; padding: 3px 10px; border-radius: 12px;
    background: #1e293b; color: #94a3b8;
}
#live-status::before {
    content: ""; width: 8px; height: 8px; border-radius: 50%;
    background: #6b7280;
}
#live-status.ok::before { background: #10b981; animation: pulse 2s ease-in-out infinite; }
#live-status.err::before { background: #ef4444; }
#live-status.ok  { color: #34d399; }
#live-status.err { color: #f87171; }
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }

.flash-msg {
    padding: 10px 14px; margin-bottom: 14px; border-radius: 6px;
    background: rgba(34, 197, 94, 0.15); color: #34d399;
    border-left: 3px solid #22c55e;
}
"""


def render_costos_panel_html() -> str:
    from src.web.csrf_js import CSRF_FETCH_WRAPPER_JS as _csrf_js  # noqa: F841
    js = _COSTOS_JS
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Catastro Bot - Costos de IA</title>
    <style>{_COSTOS_CSS}</style>
</head>
<body>
    <header>
        <h1>$$$  Costos de IA (Anthropic)</h1>
        <div class="meta">
            <a href="/">&larr; dashboard</a> &middot;
            <a href="/config/control">control</a> &middot;
            <a href="/config/runtime">procesos</a> &middot;
            <span id="live-status"><span id="live-status-text">conectando...</span></span>
        </div>
    </header>

    <div class="container">
        <div id="flash"></div>

        <div id="budget-card" class="budget-card">
            <h2>Consumo del mes</h2>
            <div class="spend-row">
                <div><span class="now" id="spend-now">$0.00</span>
                     <span class="of">de <span id="spend-budget">$0.00</span> USD</span></div>
                <div class="ratio" id="spend-ratio">0%</div>
            </div>
            <div class="bar"><div class="fill" id="spend-fill" style="width:0%"></div></div>
            <div class="edit-row">
                <label>Presupuesto mensual (USD):</label>
                <input id="budget-input" type="number" step="1" min="0" value="50" />
                <label>Alerta a (%):</label>
                <input id="threshold-input" type="number" step="5" min="10" max="150" value="80" />
                <button onclick="updateBudget()">Guardar</button>
            </div>
        </div>

        <div class="section">
            <h2>Consumo por día (últimos 31 días)</h2>
            <div id="daily-bars" class="daily-bars"></div>
        </div>

        <div class="section">
            <h2>Resumen mensual por modelo</h2>
            <table>
                <thead>
                    <tr>
                        <th>Mes</th><th>Modelo</th>
                        <th class="num">Llamadas</th>
                        <th class="num">Input</th>
                        <th class="num">Output</th>
                        <th class="num">Cache R/W</th>
                        <th class="num">Total USD</th>
                    </tr>
                </thead>
                <tbody id="monthly-tbody"><tr><td colspan="7" style="color:#94a3b8">cargando...</td></tr></tbody>
            </table>
        </div>

        <div class="section">
            <h2>Top expedientes por costo</h2>
            <table>
                <thead>
                    <tr>
                        <th>Expediente</th>
                        <th class="num">Llamadas</th>
                        <th class="num">Tokens</th>
                        <th class="num">Total USD</th>
                        <th>Ultima llamada</th>
                    </tr>
                </thead>
                <tbody id="exp-tbody"><tr><td colspan="5" style="color:#94a3b8">cargando...</td></tr></tbody>
            </table>
        </div>

        <div class="section">
            <h2>Ultimas 50 llamadas</h2>
            <table>
                <thead>
                    <tr>
                        <th>Cuando</th><th>Tipo</th><th>Modelo</th><th>Exp</th>
                        <th class="num">In</th><th class="num">Out</th>
                        <th class="num">USD</th><th class="num">ms</th>
                    </tr>
                </thead>
                <tbody id="recent-tbody"><tr><td colspan="8" style="color:#94a3b8">cargando...</td></tr></tbody>
            </table>
        </div>
    </div>

    <script>{_csrf_js}
{js}</script>
</body>
</html>
"""


_COSTOS_JS = r"""
'use strict';

function $(s) { return document.querySelector(s); }
function fmtUsd(n) { return '$' + (n || 0).toFixed(4); }
function fmtInt(n) { return (n || 0).toLocaleString('en-US'); }
function fmtIso(s) { return s ? s.slice(0, 19).replace('T', ' ') : '-'; }
function fmtModel(m) {
    if (!m) return '-';
    return m.replace('claude-', '').replace(/-\d{8}$/, '');
}

function flash(msg, klass) {
    var el = $('#flash');
    el.innerHTML = '<div class="flash-msg">' + msg + '</div>';
    setTimeout(function () { el.innerHTML = ''; }, 4000);
}

function loadSummary() {
    fetch('/api/costs/summary').then(function (r) { return r.json(); }).then(function (data) {
        var spend = data.current_month_spend_usd || 0;
        var budget = (data.budget && data.budget.monthly_usd) || 50;
        var threshold = (data.budget && data.budget.alert_threshold) || 0.80;
        var ratio = budget > 0 ? spend / budget : 0;

        $('#spend-now').textContent = fmtUsd(spend);
        $('#spend-budget').textContent = '$' + budget.toFixed(2);
        $('#spend-ratio').textContent = (ratio * 100).toFixed(1) + '%';
        $('#spend-fill').style.width = Math.min(100, ratio * 100) + '%';

        var card = $('#budget-card');
        card.classList.remove('warn', 'over');
        if (ratio >= 1.0) card.classList.add('over');
        else if (ratio >= threshold) card.classList.add('warn');

        $('#budget-input').value = budget.toFixed(0);
        $('#threshold-input').value = (threshold * 100).toFixed(0);

        // Daily bars
        renderDailyBars(data.daily || []);

        // Monthly table
        var mTbody = $('#monthly-tbody');
        if (!data.monthly || !data.monthly.length) {
            mTbody.innerHTML = '<tr><td colspan="7" style="color:#94a3b8">Sin datos.</td></tr>';
        } else {
            mTbody.innerHTML = data.monthly.map(function (r) {
                var cache = (r.total_cache_read || 0) + '/' + (r.total_cache_write || 0);
                return '<tr>' +
                    '<td>' + r.mes + '</td>' +
                    '<td>' + fmtModel(r.model) + '</td>' +
                    '<td class="num">' + fmtInt(r.llamadas) + '</td>' +
                    '<td class="num">' + fmtInt(r.total_input) + '</td>' +
                    '<td class="num">' + fmtInt(r.total_output) + '</td>' +
                    '<td class="num">' + cache + '</td>' +
                    '<td class="num usd">' + fmtUsd(r.total_usd) + '</td>' +
                    '</tr>';
            }).join('');
        }

        // Expediente table
        var eTbody = $('#exp-tbody');
        if (!data.by_expediente || !data.by_expediente.length) {
            eTbody.innerHTML = '<tr><td colspan="5" style="color:#94a3b8">Sin datos.</td></tr>';
        } else {
            eTbody.innerHTML = data.by_expediente.map(function (r) {
                return '<tr>' +
                    '<td>' + (r.expediente_id || '-').slice(0, 18) + '</td>' +
                    '<td class="num">' + fmtInt(r.llamadas) + '</td>' +
                    '<td class="num">' + fmtInt(r.total_tokens) + '</td>' +
                    '<td class="num usd">' + fmtUsd(r.total_usd) + '</td>' +
                    '<td>' + fmtIso(r.ultima_llamada) + '</td>' +
                    '</tr>';
            }).join('');
        }
    }).catch(function (e) { $('#flash').textContent = 'Error: ' + e.message; });
}

function loadRecent() {
    fetch('/api/costs/recent?limit=50').then(function (r) { return r.json(); }).then(function (data) {
        var tbody = $('#recent-tbody');
        if (!data.calls || !data.calls.length) {
            tbody.innerHTML = '<tr><td colspan="8" style="color:#94a3b8">Sin llamadas registradas.</td></tr>';
            return;
        }
        tbody.innerHTML = data.calls.map(function (r) {
            return '<tr>' +
                '<td>' + fmtIso(r.ts) + '</td>' +
                '<td>' + (r.tipo || '-') + '</td>' +
                '<td>' + fmtModel(r.model) + '</td>' +
                '<td>' + (r.expediente_id ? r.expediente_id.slice(0, 12) : '-') + '</td>' +
                '<td class="num">' + fmtInt(r.input_tokens) + '</td>' +
                '<td class="num">' + fmtInt(r.output_tokens) + '</td>' +
                '<td class="num usd">' + fmtUsd(r.cost_usd) + '</td>' +
                '<td class="num">' + (r.duration_ms || '-') + '</td>' +
                '</tr>';
        }).join('');
    });
}

function renderDailyBars(daily) {
    var el = $('#daily-bars');
    if (!daily.length) { el.innerHTML = '<div style="color:#94a3b8">Sin datos.</div>'; return; }
    daily.sort(function (a, b) { return a.dia.localeCompare(b.dia); });
    var max = Math.max.apply(null, daily.map(function (d) { return d.total_usd || 0; }));
    if (max === 0) max = 0.01;
    el.innerHTML = daily.map(function (d) {
        var h = Math.max(2, (d.total_usd / max) * 100);
        return '<div class="bar" style="height:' + h + '%">' +
               '<div class="tooltip">' + d.dia + ': ' + fmtUsd(d.total_usd) +
               ' (' + d.llamadas + ' llamadas)</div></div>';
    }).join('');
}

function updateBudget() {
    var monthly = parseFloat($('#budget-input').value || '50');
    var threshold = parseFloat($('#threshold-input').value || '80') / 100;
    fetch('/api/costs/budget', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Actor': 'web_dashboard' },
        body: JSON.stringify({ monthly_usd: monthly, alert_threshold: threshold }),
    }).then(function (r) { return r.json(); }).then(function (data) {
        if (data.error) flash('Error: ' + data.error);
        else { flash('Presupuesto actualizado: $' + monthly + ' / alerta a ' + Math.round(threshold * 100) + '%'); loadSummary(); }
    });
}
window.updateBudget = updateBudget;

// ─── SSE para refresh automático ───────────────────────────────────
(function () {
    var statusEl = $('#live-status');
    var statusText = $('#live-status-text');
    function setStatus(klass, text) {
        statusEl.classList.remove('ok', 'err');
        statusEl.classList.add(klass);
        statusText.textContent = text;
    }
    if (!('EventSource' in window)) { setStatus('err', 'sin SSE'); return; }
    var es = new EventSource('/api/events/stream');
    es.onopen = function () { setStatus('ok', 'live OK'); };
    es.onmessage = function (e) {
        try {
            var d = JSON.parse(e.data);
            if (d.type === 'api_cost_recorded') {
                loadSummary();
                loadRecent();
            }
        } catch (err) {}
    };
    es.onerror = function () { setStatus('err', 'reconectando...'); };
})();

loadSummary();
loadRecent();
setInterval(loadSummary, 30000);  // polling de respaldo
"""
