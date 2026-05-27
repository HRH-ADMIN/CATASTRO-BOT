"""Renderer del panel de control U-03 (/config/control).

Página standalone que consume /api/control/* y permite al operador:
  - Ver estado de cada módulo (apt, muni, whatsapp, scheduler, ...).
  - Arrancar/parar/resetear con modal de confirmación + razón opcional.
  - Polling de transición en curso con feedback visual.
  - Kill switch global con confirmación textual "APAGAR TODO".

Eventos SSE (/api/events/stream) se consumen también para mantener la
UI live sin polling activo extra.

Plan: PLAN_MEJORAS Sprint 1 / U-03 paso 2.3.
"""
from __future__ import annotations

# El HTML/CSS/JS es 100% inline para mantener consistencia con el resto
# del dashboard (sin build step, sin estáticos servidos aparte).
# Más adelante, si crece, migrar a templates Jinja + static assets.

_CONTROL_CSS = r"""
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
.container { max-width: 1000px; margin: 24px auto; padding: 0 24px; }

.module-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 14px;
    margin-bottom: 26px;
}
.module-card {
    background: #1e293b; border: 1px solid #334155; border-radius: 10px;
    padding: 16px 18px;
    border-left: 4px solid #6b7280;
    transition: border-color 0.3s;
}
.module-card.state-STOPPED  { border-left-color: #6b7280; }
.module-card.state-STARTING { border-left-color: #f59e0b; }
.module-card.state-RUNNING  { border-left-color: #22c55e; }
.module-card.state-STOPPING { border-left-color: #f59e0b; }
.module-card.state-ERROR    { border-left-color: #ef4444; }

.module-card .header { display: flex; justify-content: space-between; align-items: center; }
.module-card .name {
    font-weight: 600; font-size: 16px; color: #f1f5f9;
    text-transform: capitalize;
}
.module-card .badge {
    display: inline-flex; align-items: center; gap: 6px;
    font-size: 11px; font-weight: 700; letter-spacing: 0.05em;
    padding: 3px 9px; border-radius: 12px;
    background: #0f172a;
}
.module-card .badge::before {
    content: ""; width: 7px; height: 7px; border-radius: 50%;
    background: #6b7280;
}
.module-card .badge.STOPPED  { color: #94a3b8; }
.module-card .badge.STARTING { color: #fbbf24; }
.module-card .badge.STARTING::before { background: #f59e0b; animation: pulse 1s ease-in-out infinite; }
.module-card .badge.RUNNING  { color: #34d399; }
.module-card .badge.RUNNING::before { background: #22c55e; animation: pulse 2s ease-in-out infinite; }
.module-card .badge.STOPPING { color: #fbbf24; }
.module-card .badge.STOPPING::before { background: #f59e0b; animation: pulse 1s ease-in-out infinite; }
.module-card .badge.ERROR    { color: #f87171; }
.module-card .badge.ERROR::before { background: #ef4444; }
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }

.module-card .details {
    margin-top: 12px; font-size: 12px; color: #94a3b8;
    display: grid; grid-template-columns: max-content auto; gap: 3px 10px;
}
.module-card .details .k { color: #64748b; }
.module-card .details .v { color: #cbd5e1; word-break: break-word; }
.module-card .details .v.err { color: #f87171; }
.module-card .actions {
    margin-top: 14px; display: flex; gap: 8px; flex-wrap: wrap;
}
.module-card button {
    flex: 1; padding: 8px 12px; border: none; border-radius: 6px;
    font-weight: 600; font-size: 12px; cursor: pointer;
    transition: background-color 0.2s, opacity 0.2s;
}
.module-card button:disabled { opacity: 0.4; cursor: not-allowed; }
.btn-start  { background: #22c55e; color: white; }
.btn-start:hover:not(:disabled)  { background: #16a34a; }
.btn-stop   { background: #ef4444; color: white; }
.btn-stop:hover:not(:disabled)   { background: #dc2626; }
.btn-reset  { background: #f59e0b; color: white; }
.btn-reset:hover:not(:disabled)  { background: #d97706; }

.danger-zone {
    margin-top: 32px; padding: 20px;
    background: rgba(239, 68, 68, 0.08);
    border: 1px solid rgba(239, 68, 68, 0.3);
    border-radius: 10px;
}
.danger-zone h2 { margin: 0 0 8px 0; color: #f87171; font-size: 18px; }
.danger-zone p { color: #cbd5e1; font-size: 13px; margin: 0 0 14px 0; }
.danger-zone .emergency-form {
    display: grid; grid-template-columns: 1fr auto; gap: 10px;
    align-items: stretch;
}
.danger-zone .emergency-form input {
    background: #0f172a; border: 1px solid #ef4444; color: #f1f5f9;
    padding: 10px 14px; border-radius: 6px; font-family: monospace;
    font-size: 13px;
}
.danger-zone .emergency-form input::placeholder { color: #64748b; }
.danger-zone .emergency-form button {
    padding: 10px 18px; border: none; border-radius: 6px;
    background: #ef4444; color: white; font-weight: 700; cursor: pointer;
}
.danger-zone .emergency-form button:hover:not(:disabled) { background: #dc2626; }
.danger-zone .emergency-form button:disabled { opacity: 0.4; cursor: not-allowed; }

/* Modal de confirmación reusable */
.modal-bg {
    position: fixed; inset: 0; background: rgba(0,0,0,0.6);
    display: flex; align-items: center; justify-content: center;
    z-index: 1000;
}
.modal {
    background: #1e293b; padding: 24px; border-radius: 12px;
    width: 90%; max-width: 480px;
    border: 1px solid #334155;
}
.modal h3 { margin: 0 0 12px 0; color: #f1f5f9; }
.modal p  { color: #cbd5e1; margin: 0 0 14px 0; font-size: 14px; }
.modal input {
    width: 100%; background: #0f172a; border: 1px solid #475569;
    color: #e2e8f0; padding: 10px 12px; border-radius: 6px;
    font-size: 13px;
}
.modal .actions { display: flex; gap: 10px; margin-top: 18px; justify-content: flex-end; }
.modal button { padding: 9px 16px; border: none; border-radius: 6px; cursor: pointer; font-weight: 600; }
.modal .cancel  { background: #475569; color: white; }
.modal .confirm { background: #3b82f6; color: white; }
.modal .confirm.danger { background: #ef4444; }

/* Toast */
.toasts {
    position: fixed; bottom: 24px; right: 24px;
    display: flex; flex-direction: column; gap: 10px; z-index: 2000;
}
.toast {
    padding: 12px 16px; border-radius: 6px; background: #1e293b;
    border-left: 4px solid #3b82f6; min-width: 260px;
    box-shadow: 0 4px 12px rgba(0,0,0,0.4);
    font-size: 13px; color: #e2e8f0;
    animation: slide-in 0.25s ease-out;
}
.toast.ok    { border-left-color: #22c55e; }
.toast.err   { border-left-color: #ef4444; }
.toast.warn  { border-left-color: #f59e0b; }
@keyframes slide-in {
    from { transform: translateX(100%); opacity: 0; }
    to   { transform: translateX(0); opacity: 1; }
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
#live-status.warn::before { background: #f59e0b; }
#live-status.err::before { background: #ef4444; }
#live-status.ok    { color: #34d399; }
#live-status.warn  { color: #fbbf24; }
#live-status.err   { color: #f87171; }
"""


def render_control_panel_html() -> str:
    """Renderiza la página /config/control. Todo client-side data via /api/control/*."""
    from src.web.csrf_js import CSRF_FETCH_WRAPPER_JS as _csrf_js  # noqa: F841

    # JS se construye con %s al final para no chocar con f-string.
    js = _CONTROL_JS

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Catastro Bot — Panel de control</title>
    <style>{_CONTROL_CSS}</style>
</head>
<body>
    <header>
        <h1>⚙️ Panel de control del bot</h1>
        <div class="meta">
            <a href="/">← volver al dashboard</a> ·
            <a href="/config">credenciales</a> ·
            <span id="live-status"><span id="live-status-text">conectando…</span></span>
        </div>
    </header>

    <div class="container">
        <div id="module-grid" class="module-grid">
            <!-- cards se inyectan por JS al cargar -->
        </div>

        <div class="danger-zone">
            <h2>⚠️ Zona de emergencia</h2>
            <p>
                <strong>Kill switch global.</strong> Detiene TODOS los módulos
                inmediatamente y aplica un cooldown de 5 minutos durante el
                cual no se permiten reinicios programáticos. Requiere escribir
                <code>APAGAR TODO</code> exactamente.
            </p>
            <div class="emergency-form">
                <input id="emergency-input" type="text"
                       placeholder='Escribí "APAGAR TODO" para confirmar'
                       autocomplete="off" />
                <button id="emergency-btn" disabled>🛑 Apagar todo</button>
            </div>
        </div>
    </div>

    <div id="toasts" class="toasts"></div>

    <script>{_csrf_js}
{js}</script>
</body>
</html>
"""


# JavaScript del panel — vanilla, sin dependencias.
_CONTROL_JS = r"""
'use strict';

// ─── Estado en memoria ───────────────────────────────────────────────
var ModuleCards = {}; // module_name → DOM element
var PendingTransitions = {}; // transition_id → {module, started_at}

// ─── DOM helpers ─────────────────────────────────────────────────────
function $(sel) { return document.querySelector(sel); }
function el(tag, attrs, ...kids) {
    var e = document.createElement(tag);
    if (attrs) {
        for (var k in attrs) {
            if (k === 'class') e.className = attrs[k];
            else if (k === 'html') e.innerHTML = attrs[k];
            else if (k.indexOf('on') === 0) e.addEventListener(k.slice(2), attrs[k]);
            else e.setAttribute(k, attrs[k]);
        }
    }
    kids.forEach(function (k) {
        if (k == null) return;
        e.appendChild(typeof k === 'string' ? document.createTextNode(k) : k);
    });
    return e;
}

// ─── Toasts ──────────────────────────────────────────────────────────
function toast(msg, level) {
    var t = el('div', { class: 'toast ' + (level || '') }, msg);
    $('#toasts').appendChild(t);
    setTimeout(function () { t.style.opacity = '0'; t.style.transition = 'opacity 0.3s'; }, 4000);
    setTimeout(function () { t.remove(); }, 4500);
}

// ─── Modal ───────────────────────────────────────────────────────────
function modal(opts) {
    return new Promise(function (resolve) {
        var bg = el('div', { class: 'modal-bg' });
        var input = el('input', { type: 'text', placeholder: opts.placeholder || 'Razón (opcional)' });
        var confirmBtn = el('button', { class: 'confirm ' + (opts.danger ? 'danger' : '') }, opts.confirmText || 'Confirmar');
        var cancelBtn = el('button', { class: 'cancel' }, 'Cancelar');
        confirmBtn.addEventListener('click', function () {
            bg.remove();
            resolve(input.value || null);
        });
        cancelBtn.addEventListener('click', function () { bg.remove(); resolve(undefined); });
        bg.addEventListener('click', function (e) { if (e.target === bg) { bg.remove(); resolve(undefined); } });
        var box = el('div', { class: 'modal' },
            el('h3', null, opts.title),
            el('p', { html: opts.body }),
            input,
            el('div', { class: 'actions' }, cancelBtn, confirmBtn)
        );
        bg.appendChild(box);
        document.body.appendChild(bg);
        setTimeout(function () { input.focus(); }, 50);
    });
}

// ─── API client ──────────────────────────────────────────────────────
function api(method, path, body) {
    var opts = { method: method, headers: { 'X-Actor': 'web_dashboard' } };
    if (body !== undefined) {
        opts.headers['Content-Type'] = 'application/json';
        opts.body = JSON.stringify(body);
    }
    return fetch(path, opts).then(function (r) {
        return r.json().then(function (data) {
            return { ok: r.ok, status: r.status, body: data };
        });
    });
}

// ─── Render de un module-card ────────────────────────────────────────
function buildCard(m) {
    var state = m.state;
    var card = el('div', { class: 'module-card state-' + state, 'data-module': m.module_name });
    var header = el('div', { class: 'header' },
        el('span', { class: 'name' }, m.module_name),
        el('span', { class: 'badge ' + state }, state)
    );
    var details = el('div', { class: 'details' });
    var rows = [
        ['Última transición', m.last_transition_at ? m.last_transition_at.slice(0, 19) : '—'],
        ['Por', m.last_transition_actor || '—'],
        ['Razón', m.last_transition_reason || '—'],
    ];
    if (m.error_message) rows.push(['Error', m.error_message, true]);
    if (m.cooldown_until) {
        var until = new Date(m.cooldown_until);
        var msLeft = until - new Date();
        if (msLeft > 0) {
            rows.push(['Cooldown hasta', until.toLocaleTimeString()]);
        }
    }
    rows.forEach(function (r) {
        details.appendChild(el('span', { class: 'k' }, r[0]));
        details.appendChild(el('span', { class: 'v' + (r[2] ? ' err' : '') }, r[1]));
    });

    var actions = el('div', { class: 'actions' });
    // Botones según estado actual:
    if (state === 'STOPPED') {
        actions.appendChild(el('button', {
            class: 'btn-start',
            onclick: function () { confirmAndCall('start', m.module_name, 'Iniciar ' + m.module_name); }
        }, '▶ Iniciar'));
    } else if (state === 'RUNNING') {
        actions.appendChild(el('button', {
            class: 'btn-stop',
            onclick: function () { confirmAndCall('stop', m.module_name, 'Detener ' + m.module_name); }
        }, '■ Detener'));
    } else if (state === 'STARTING' || state === 'STOPPING') {
        var btn = el('button', { disabled: 'disabled' }, state === 'STARTING' ? 'Iniciando…' : 'Deteniendo…');
        actions.appendChild(btn);
    } else if (state === 'ERROR') {
        actions.appendChild(el('button', {
            class: 'btn-reset',
            onclick: function () { confirmAndCall('reset', m.module_name, 'Reset de error en ' + m.module_name); }
        }, '⟲ Reset'));
    }

    card.appendChild(header);
    card.appendChild(details);
    card.appendChild(actions);
    return card;
}

function renderAll(modules) {
    var grid = $('#module-grid');
    grid.innerHTML = '';
    ModuleCards = {};
    modules.forEach(function (m) {
        var card = buildCard(m);
        ModuleCards[m.module_name] = card;
        grid.appendChild(card);
    });
}

function refreshModule(name) {
    api('GET', '/api/control/status').then(function (resp) {
        if (!resp.ok) return;
        var m = (resp.body.modules || []).find(function (x) { return x.module_name === name; });
        if (m && ModuleCards[name]) {
            var fresh = buildCard(m);
            ModuleCards[name].replaceWith(fresh);
            ModuleCards[name] = fresh;
        }
    });
}

// ─── Confirm + call ──────────────────────────────────────────────────
function confirmAndCall(action, moduleName, title) {
    modal({
        title: title,
        body: 'Razón (opcional, queda en el audit log):',
        confirmText: action === 'stop' ? 'Detener' : (action === 'start' ? 'Iniciar' : 'Reset'),
        danger: action === 'stop',
    }).then(function (reason) {
        if (reason === undefined) return; // cancelado
        var body = reason ? { reason: reason } : {};
        var path = '/api/control/' + action + '/' + moduleName;
        api('POST', path, body).then(function (resp) {
            if (!resp.ok) {
                toast('❌ ' + (resp.body.error || ('HTTP ' + resp.status)), 'err');
                return;
            }
            toast('Transición iniciada: ' + moduleName + ' → ' + resp.body.state, 'ok');
            var tid = resp.body.last_transition_id;
            if (tid) {
                PendingTransitions[tid] = { module: moduleName, started: Date.now() };
                pollTransition(tid);
            }
            refreshModule(moduleName);
        }).catch(function (e) {
            toast('❌ Error de red: ' + e.message, 'err');
        });
    });
}

// ─── Polling de transición ──────────────────────────────────────────
function pollTransition(tid) {
    var info = PendingTransitions[tid];
    if (!info) return;
    var elapsed = Date.now() - info.started;
    if (elapsed > 60000) {
        toast('⚠️ Transición ' + info.module + ' lleva >60s — abandonando polling', 'warn');
        delete PendingTransitions[tid];
        return;
    }
    api('GET', '/api/control/transition/' + tid).then(function (resp) {
        if (!resp.ok || resp.body.is_final) {
            if (resp.ok) {
                toast('✓ ' + info.module + ' → ' + resp.body.state, resp.body.state === 'ERROR' ? 'err' : 'ok');
            }
            delete PendingTransitions[tid];
            refreshModule(info.module);
            return;
        }
        setTimeout(function () { pollTransition(tid); }, 500);
    });
}

// ─── Emergency stop ─────────────────────────────────────────────────
(function emergencyWiring() {
    var input = $('#emergency-input');
    var btn = $('#emergency-btn');
    input.addEventListener('input', function () {
        btn.disabled = (input.value !== 'APAGAR TODO');
    });
    btn.addEventListener('click', function () {
        modal({
            title: '🛑 ¿Apagar TODOS los módulos?',
            body: 'Esto detiene <strong>scheduler, apt, muni, whatsapp, drive_backup, rnp</strong> ' +
                  'inmediatamente y aplica un cooldown de 5 minutos. ' +
                  'Razón (opcional, queda en el audit log):',
            confirmText: 'Apagar todo',
            danger: true,
        }).then(function (reason) {
            if (reason === undefined) return;
            api('POST', '/api/control/emergency-stop', {
                confirmation: 'APAGAR TODO',
                reason: reason || 'emergency stop desde dashboard',
                cooldown_seconds: 300,
            }).then(function (resp) {
                if (!resp.ok) {
                    toast('❌ ' + (resp.body.error || 'HTTP ' + resp.status), 'err');
                    return;
                }
                toast('🛑 Emergency stop aplicado · cooldown ' + resp.body.cooldown_seconds + 's', 'warn');
                input.value = ''; btn.disabled = true;
                loadAll();
            });
        });
    });
})();

// ─── Inicialización ──────────────────────────────────────────────────
function loadAll() {
    api('GET', '/api/control/status').then(function (resp) {
        if (resp.ok) renderAll(resp.body.modules);
        else toast('❌ No se pudo cargar el estado: ' + resp.status, 'err');
    });
}

// ─── SSE para refresh automático ────────────────────────────────────
(function sseWiring() {
    var statusEl = $('#live-status');
    var statusText = $('#live-status-text');
    function setStatus(klass, text) {
        statusEl.classList.remove('ok', 'warn', 'err');
        statusEl.classList.add(klass);
        statusText.textContent = text;
    }
    if (!('EventSource' in window)) {
        setStatus('warn', 'sin SSE');
        return;
    }
    var es = new EventSource('/api/events/stream');
    es.onopen = function () { setStatus('ok', 'live ✓'); };
    es.onmessage = function (e) {
        try {
            var data = JSON.parse(e.data);
            if (data.type === 'module_state_changed') {
                refreshModule(data.module);
            } else if (data.type === 'emergency_stop') {
                loadAll();
            }
        } catch (err) { /* ignore */ }
    };
    es.onerror = function () { setStatus('err', 'reconectando…'); };
})();

loadAll();
"""
