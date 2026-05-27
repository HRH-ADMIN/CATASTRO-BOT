"""Snippet JavaScript reutilizable para CSRF en páginas del dashboard.

Las páginas HTML del bot (`dashboard_web.py`, `dashboard_control_html.py`,
etc.) deben incluir este snippet al INICIO de su primer `<script>` para
que `window.fetch` parche el header `X-CSRF-Token` automáticamente en
todos los POST/PUT/DELETE/PATCH.

USO:
    from src.web.csrf_js import CSRF_FETCH_WRAPPER_JS
    html = f"<script>{CSRF_FETCH_WRAPPER_JS}\\n... rest of JS ...</script>"

El snippet es puro JS — no contiene llaves `{}` problemáticas para
f-strings (todas las llaves están dentro de strings o son sintaxis JS
escapadas con doble llave).

Plan: PLAN_MEJORAS Sprint 4 / S-04.
"""

CSRF_FETCH_WRAPPER_JS = """
// ─── CSRF protection (Sprint 4 / S-04) ─────────────────────────────
// Patrón double-submit cookie. Server seteó la cookie 'catastro_csrf'
// en el GET inicial. JS la lee y la agrega al header X-CSRF-Token en
// POST/PUT/DELETE/PATCH. Server compara cookie == header.
(function () {
    function getCsrfToken() {
        var name = "catastro_csrf";
        var parts = ("; " + document.cookie).split("; " + name + "=");
        if (parts.length === 2) return parts.pop().split(";").shift();
        return "";
    }

    var origFetch = window.fetch;
    var UNSAFE = ["POST", "PUT", "DELETE", "PATCH"];

    window.fetch = function (resource, init) {
        init = init || {};
        var method = (init.method || "GET").toUpperCase();
        if (UNSAFE.indexOf(method) !== -1) {
            var token = getCsrfToken();
            if (token) {
                init.headers = init.headers || {};
                if (init.headers instanceof Headers) {
                    init.headers.set("X-CSRF-Token", token);
                } else {
                    init.headers["X-CSRF-Token"] = token;
                }
            }
        }
        return origFetch.call(window, resource, init);
    };
})();
"""
