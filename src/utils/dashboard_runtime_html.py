"""Panel HTML /config/runtime — procesos vivos del SO (U-02 paso B).

Consume:
  - GET /api/runtime/processes (con/sin include_dead)
  - GET /api/runtime/logs/<process_name>?tail=N
  - SSE: process_died / process_hanging para refresh automático

Plan: PLAN_MEJORAS Sprint 1 / U-02 paso B.
"""
from __future__ import annotations

_RUNTIME_CSS = r"""
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

table {
    width: 100%; border-collapse: collapse; margin-top: 16px;
    background: #1e293b; border: 1px solid #334155; border-radius: 8px;
    overflow: hidden;
}
th, td {
    padding: 10px 14px; text-align: left;
    border-bottom: 1px solid #334155;
}
th {
    background: #0f172a; color: #94a3b8;
    font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em;
}
td { font-size: 13px; }
td.actions { display: flex; gap: 6px; }
td.actions button {
    padding: 5px 10px; border: none; border-radius: 4px;
    font-size: 11px; font-weight: 600; cursor: pointer;
}
.btn-view-log { background: #3b82f6; color: white; }
.btn-view-log:hover { background: #2563eb; }

.status-badge {
    display: inline-flex; align-items: center; gap: 6px;
    font-size: 11px; font-weight: 700; padding: 3px 9px;
    border-radius: 12px; background: #0f172a;
}
.status-badge::before {
    content: ""; width: 7px; height: 7px; border-radius: 50%;
    background: #6b7280;
}
.status-badge.alive   { color: #34d399; }
.status-badge.alive::before { background: #22c55e; animation: pulse 2s ease-in-out infinite; }
.status-badge.hanging { color: #fbbf24; }
.status-badge.hanging::before { background: #f59e0b; }
.status-badge.dead    { color: #f87171; }
.status-badge.dead::before { background: #ef4444; }
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }

.toggle-row {
    margin: 16px 0; padding: 12px 16px; background: #1e293b;
    border-radius: 8px; border: 1px solid #334155;
    display: flex; align-items: center; gap: 16px;
}
.toggle-row label { font-size: 13px; cursor: pointer; }

/* Modal log viewer */
.log-modal-bg {
    position: fixed; inset: 0; background: rgba(0,0,0,0.75);
    display: flex; align-items: center; justify-content: center;
    z-index: 1000;
}
.log-modal {
    background: #0a0f1a; border: 1px solid #334155; border-radius: 10px;
    width: 90%; max-width: 1100px; max-height: 85vh;
    display: flex; flex-direction: column;
}
.log-modal .head {
    padding: 14px 20px; border-bottom: 1px solid #334155;
    display: flex; align-items: center; justify-content: space-between;
}
.log-modal .head h3 { margin: 0; color: #f1f5f9; font-size: 16px; }
.log-modal .head .close {
    background: #475569; color: white; border: none;
    padding: 6px 12px; border-radius: 4px; cursor: pointer;
}
.log-modal .head .auto-refresh {
    font-size: 12px; color: #94a3b8;
}
.log-modal .head label { cursor: pointer; }
.log-modal pre {
    flex: 1; overflow: auto; padding: 14px 18px; margin: 0;
    font-family: "Consolas", "Cascadia Code", monospace;
    font-size: 12px; line-height: 1.45; color: #e2e8f0;
    background: #0a0f1a; white-space: pre-wrap; word-break: break-all;
}
.log-modal .footer {
    padding: 10px 20px; border-top: 1px solid #334155;
    font-size: 11px; color: #64748b;
}

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
"""


def render_runtime_panel_html() -> str:
    from src.web.csrf_js import CSRF_FETCH_WRAPPER_JS as _csrf_js  # noqa: F841
    js = _RUNTIME_JS
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Catastro Bot — Procesos del sistema</title>
    <style>{_RUNTIME_CSS}</style>
</head>
<body>
    <header>
        <h1>⚙️ Procesos del sistema</h1>
        <div class="meta">
            <a href="/">← dashboard</a> ·
            <a href="/config/control">⚙️ panel de control</a> ·
            <a href="/config">credenciales</a> ·
            <span id="live-status"><span id="live-status-text">conectando…</span></span>
        </div>
    </header>

    <div class="container">
        <div class="toggle-row">
            <label>
                <input type="checkbox" id="include-dead" />
                Mostrar procesos terminados recientes (últimos 50)
            </label>
        </div>

        <table id="processes-table">
            <thead>
                <tr>
                    <th>Proceso</th>
                    <th>PID</th>
                    <th>Estado</th>
                    <th>Iniciado</th>
                    <th>Último heartbeat</th>
                    <th>CPU%</th>
                    <th>RAM</th>
                    <th>Acciones</th>
                </tr>
            </thead>
            <tbody id="processes-tbody">
                <tr><td colspan="8" style="text-align:center;color:#94a3b8;padding:20px">cargando…</td></tr>
            </tbody>
        </table>
    </div>

    <script>{_csrf_js}
{js}</script>
</body>
</html>
"""


_RUNTIME_JS = r"""
'use strict';

function $(s) { return document.querySelector(s); }

function fmtIso(s) {
    if (!s) return '—';
    return s.slice(0, 19).replace('T', ' ');
}
function fmtMem(mb) {
    if (mb == null) return '—';
    if (mb > 1024) return (mb / 1024).toFixed(1) + ' GB';
    return mb.toFixed(0) + ' MB';
}
function fmtCpu(p) {
    if (p == null) return '—';
    return p.toFixed(1) + '%';
}

function loadProcesses() {
    var includeDead = $('#include-dead').checked;
    var url = '/api/runtime/processes' + (includeDead ? '?include_dead=1' : '');
    fetch(url).then(function (r) { return r.json(); }).then(function (data) {
        renderTable(data.processes || []);
    }).catch(function (e) {
        $('#processes-tbody').innerHTML =
            '<tr><td colspan="8" style="color:#f87171">Error: ' + e.message + '</td></tr>';
    });
}

function renderTable(procs) {
    var tbody = $('#processes-tbody');
    if (!procs.length) {
        tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;color:#94a3b8;padding:20px">Sin procesos.</td></tr>';
        return;
    }
    tbody.innerHTML = '';
    procs.forEach(function (p) {
        var tr = document.createElement('tr');
        tr.innerHTML =
            '<td><strong>' + p.process_name + '</strong></td>' +
            '<td>' + p.pid + '</td>' +
            '<td><span class="status-badge ' + p.status + '">' + p.status + '</span></td>' +
            '<td>' + fmtIso(p.started_at) + '</td>' +
            '<td>' + fmtIso(p.last_heartbeat_at) + '</td>' +
            '<td>' + fmtCpu(p.cpu_percent) + '</td>' +
            '<td>' + fmtMem(p.memory_mb) + '</td>' +
            '<td class="actions"></td>';
        var actions = tr.querySelector('.actions');
        if (p.log_file_path) {
            var b = document.createElement('button');
            b.className = 'btn-view-log';
            b.textContent = '📄 Ver log';
            b.addEventListener('click', function () { openLogModal(p.process_name); });
            actions.appendChild(b);
        }
        tbody.appendChild(tr);
    });
}

// ─── Modal del log ──────────────────────────────────────────────────
var logRefreshInterval = null;
var currentLogProcess = null;

function openLogModal(processName) {
    currentLogProcess = processName;
    closeLogModal(); // por si había uno abierto

    var bg = document.createElement('div');
    bg.className = 'log-modal-bg';
    bg.id = 'log-modal-bg';
    bg.addEventListener('click', function (e) { if (e.target === bg) closeLogModal(); });

    var modal = document.createElement('div');
    modal.className = 'log-modal';
    modal.innerHTML =
        '<div class="head">' +
            '<h3>📄 Log: ' + processName + '</h3>' +
            '<div>' +
                '<label class="auto-refresh">' +
                    '<input type="checkbox" id="auto-refresh-log" checked /> Auto-refresh (2s)' +
                '</label>' +
                ' &nbsp; ' +
                '<button class="close" onclick="closeLogModal()">Cerrar</button>' +
            '</div>' +
        '</div>' +
        '<pre id="log-content">cargando…</pre>' +
        '<div class="footer" id="log-footer">—</div>';
    bg.appendChild(modal);
    document.body.appendChild(bg);

    refreshLog();
    document.getElementById('auto-refresh-log').addEventListener('change', function (e) {
        if (e.target.checked) startLogAutoRefresh();
        else stopLogAutoRefresh();
    });
    startLogAutoRefresh();
}

function closeLogModal() {
    stopLogAutoRefresh();
    currentLogProcess = null;
    var bg = document.getElementById('log-modal-bg');
    if (bg) bg.remove();
}
window.closeLogModal = closeLogModal;

function startLogAutoRefresh() {
    stopLogAutoRefresh();
    logRefreshInterval = setInterval(refreshLog, 2000);
}
function stopLogAutoRefresh() {
    if (logRefreshInterval) { clearInterval(logRefreshInterval); logRefreshInterval = null; }
}

function refreshLog() {
    if (!currentLogProcess) return;
    fetch('/api/runtime/logs/' + encodeURIComponent(currentLogProcess) + '?tail=200')
        .then(function (r) { return r.json(); })
        .then(function (data) {
            if (!currentLogProcess) return; // cerraron mientras
            var pre = document.getElementById('log-content');
            if (data.error) {
                pre.textContent = '⚠️ ' + data.error;
                return;
            }
            pre.textContent = (data.lines || []).join('\n') || '(log vacío)';
            // Scroll automático al final
            pre.scrollTop = pre.scrollHeight;
            var footer = document.getElementById('log-footer');
            footer.textContent = data.log_file_path + ' · mostrando últimas ' +
                (data.lines ? data.lines.length : 0) + ' de ' + data.total_lines + ' líneas';
        })
        .catch(function (e) {
            var pre = document.getElementById('log-content');
            if (pre) pre.textContent = 'Error: ' + e.message;
        });
}

// ─── Toggle include_dead ────────────────────────────────────────────
$('#include-dead').addEventListener('change', loadProcesses);

// ─── SSE para refresh automático cuando un proceso cambia ───────────
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
    es.onopen = function () { setStatus('ok', 'live ✓'); };
    es.onmessage = function (e) {
        try {
            var d = JSON.parse(e.data);
            if (d.type === 'process_died' || d.type === 'process_hanging') {
                loadProcesses();
            }
        } catch (err) { /* ignore */ }
    };
    es.onerror = function () { setStatus('err', 'reconectando…'); };
})();

// Polling de respaldo cada 5s (por si el monitor pass no produce evento)
setInterval(loadProcesses, 5000);
loadProcesses();
"""
