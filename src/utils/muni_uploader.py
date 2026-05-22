"""Subida automatizada al formulario muni (Google Forms) — flujo completo.

REGLAS APLICADAS (aprendidas con TILMAN 2026-05-13):

  R-M1: Login persistente
    - Chrome del bot perfil dedicado: data/temp/chrome_profile_apt
    - Loguear Gmail UNA VEZ con cuenta del topógrafo
    - Sesión persiste entre reinicios

  R-M2: URL pre-llenada con emailAddress
    - Google Forms con captura email requiere login
    - Usar emailAddress=<correo> en query string
    - Si abre SIN sesión, redirect login pierde prefills → asegurar
      sesión antes de abrir link

  R-M3: Formato de campos
    - Finca:       "<prov> <num>-<derecho>" (ej "2 629270-000")
    - Varias:      "<f1>/<f2>/..."
    - Carné:       "it-NNNNN" minúsculas + guion
    - Nombre:      "nombres apellidos" minúsculas
    - Vértices:    LISTA "1-2-3-4" (NO cantidad)
    - Tipo Acceso: calle pública (Cantonal/Nacional XX) — usar visor SR
    - Cada plano: vértices DISTINTOS — NO copiar de hermanos

  R-M4: Pickers de archivos (Google Drive embebido)
    - Cada campo file abre iframe picker (URL contiene 'picker')
    - Click "Añadir archivo" via page.mouse.click(coords) — NO evaluate
      (Google bloquea evaluate-click)
    - PDF: set_input_files al input[type=file] del picker
    - ZIP: usar page.expect_file_chooser() porque accept del input
      restringe a PDF/image (FileChooser bypasea accept)
    - Múltiples pickers: usar coords REALES del botón "Examinar"
      (rect_iframe + rect_boton) para click certero

  R-M5: ENVIAR requiere click humano
    - Google bloquea TODOS los clicks programáticos al botón Enviar
      (evaluate, mouse.click, keyboard Enter, dispatchEvent)
    - El bot prepara TODO, el operador da el click final
    - Esto es OPERATIVAMENTE CORRECTO — acciones irreversibles las
      confirma un humano
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("catastro.muni_uploader")


def cerrar_pickers_residuales(page, *, verbose: bool = False) -> dict:
    """Cierra/destruye TODOS los pickers de Google Drive residuales.

    Estrategia en cascada (cada paso si el anterior no terminó):
      1. Escape repetido (cierra modal visible)
      2. Click botón "Cerrar el selector Insertar un archivo" en cada iframe
      3. Remove() de los iframe elements directamente (destrucción forzada)
      4. Verificación final con conteo

    Returns:
        {
            "ok": bool,
            "pickers_iniciales": int,
            "pickers_finales": int,
            "estrategias_usadas": list[str],
        }
    """
    estrategias: list[str] = []

    def _contar_pickers() -> int:
        return len([f for f in page.frames
                    if 'picker' in f.url and 'about:blank' not in f.url])

    n_inicial = _contar_pickers()
    if n_inicial == 0:
        return {"ok": True, "pickers_iniciales": 0, "pickers_finales": 0,
                "estrategias_usadas": []}
    if verbose:
        log.info("cerrar_pickers: %d pickers activos", n_inicial)

    # Estrategia 1: Escape (cierra modal visible)
    for _ in range(8):
        try:
            page.keyboard.press("Escape")
        except Exception:
            break
        time.sleep(0.2)
    estrategias.append("escape")
    time.sleep(1)
    if _contar_pickers() == 0:
        return {"ok": True, "pickers_iniciales": n_inicial,
                "pickers_finales": 0, "estrategias_usadas": estrategias}

    # Estrategia 2: click botón cerrar dentro de cada iframe picker
    for pf in [f for f in page.frames
               if 'picker' in f.url and 'about:blank' not in f.url]:
        try:
            pf.evaluate(
                "() => { document.querySelectorAll('button').forEach(b => { "
                "  const a = b.getAttribute('aria-label') || ''; "
                "  if (/Cerrar el selector|Cerrar/i.test(a) && b.offsetParent) b.click(); "
                "}); }"
            )
        except Exception:
            pass
    estrategias.append("close_btn_iframe")
    time.sleep(1.5)
    if _contar_pickers() == 0:
        return {"ok": True, "pickers_iniciales": n_inicial,
                "pickers_finales": 0, "estrategias_usadas": estrategias}

    # Estrategia 3: destrucción forzada del DOM del iframe
    try:
        n_removidos = page.evaluate(
            "() => { let n = 0; "
            "  document.querySelectorAll('iframe').forEach(f => { "
            "    if ((f.src || '').includes('picker')) { f.remove(); n++; } "
            "  }); "
            "  // Quitar también backdrops de Drive picker"
            "  document.querySelectorAll('.picker-backdrop, [class*=\"picker-dialog\"]')"
            "    .forEach(el => el.remove()); "
            "  return n; "
            "}"
        )
        estrategias.append(f"iframe_remove({n_removidos})")
    except Exception as exc:
        if verbose:
            log.warning("iframe remove falló: %s", exc)
    time.sleep(1)

    n_final = _contar_pickers()
    return {
        "ok":                n_final == 0,
        "pickers_iniciales": n_inicial,
        "pickers_finales":   n_final,
        "estrategias_usadas": estrategias,
    }


def coords_boton_picker(page, titulo_listitem: str) -> Optional[dict]:
    """Obtiene coordenadas pantalla del botón 'Añadir archivo' del listitem.

    Args:
        titulo_listitem: 'DOCUMENTOS', 'archivo shape', etc. (case-insensitive)

    Returns: {"x": float, "y": float} o None si no se encuentra.
    """
    return page.evaluate(
        """(titulo) => {
            const items = document.querySelectorAll('[role=listitem]');
            for (const item of items) {
                const heading = item.querySelector('[role=heading]');
                const t = heading ? (heading.innerText || '').toLowerCase() : '';
                if (t.includes(titulo.toLowerCase())) {
                    item.scrollIntoView({block: 'center'});
                    const btn = item.querySelector('div[role=button]');
                    if (btn) {
                        const r = btn.getBoundingClientRect();
                        return {x: r.x + r.width/2, y: r.y + r.height/2};
                    }
                }
            }
            return null;
        }""",
        titulo_listitem,
    )


def coords_examinar_picker(page) -> Optional[dict]:
    """Obtiene coords REALES del botón 'Examinar' dentro del iframe picker.

    Combina rect del iframe + rect del botón → coords absolutas pantalla.
    Necesario cuando hay múltiples pickers superpuestos para que el click
    aterrice en el correcto.
    """
    return page.evaluate(
        """() => {
            for (const f of document.querySelectorAll('iframe')) {
                try {
                    const doc = f.contentDocument;
                    if (!doc) continue;
                    const btns = doc.querySelectorAll('button');
                    for (const b of btns) {
                        if ((b.innerText || '').trim().toLowerCase() === 'examinar' && b.offsetParent) {
                            const rF = f.getBoundingClientRect();
                            const rB = b.getBoundingClientRect();
                            return {x: rF.x + rB.x + rB.width/2, y: rF.y + rB.y + rB.height/2};
                        }
                    }
                } catch (e) {}
            }
            return null;
        }"""
    )


def subir_archivo_a_picker(
    page,
    *,
    titulo_listitem: str,
    ruta_archivo: str | Path,
    es_zip: bool = False,
    timeout_picker_s: float = 10,
) -> dict:
    """Sube un archivo al campo Google Forms del listitem indicado.

    Args:
        titulo_listitem: ej 'DOCUMENTOS' o 'archivo shape'.
        ruta_archivo: ruta absoluta del archivo.
        es_zip: True si es ZIP (necesita FileChooser para bypass accept).

    Returns:
        {"ok": bool, "archivo": str, "metodo": "set_input_files"|"file_chooser"|None,
         "error": str|None}
    """
    ruta = str(ruta_archivo)
    if not Path(ruta).exists():
        return {"ok": False, "archivo": ruta, "error": "archivo no existe"}

    # 1. Click "Añadir archivo" por coords (evaluate-click es bloqueado)
    coords = coords_boton_picker(page, titulo_listitem)
    if not coords:
        return {"ok": False, "archivo": ruta,
                "error": f"no encontré botón Añadir archivo en listitem {titulo_listitem!r}"}
    time.sleep(1)
    page.mouse.move(coords["x"], coords["y"])
    time.sleep(0.3)
    page.mouse.click(coords["x"], coords["y"])
    log.info("click Añadir archivo %s en (%s, %s)",
             titulo_listitem, coords["x"], coords["y"])

    # 2. Esperar picker
    picker = None
    for _ in range(int(timeout_picker_s)):
        time.sleep(1)
        pickers = [f for f in page.frames
                   if 'picker' in f.url and 'about:blank' not in f.url]
        if pickers:
            picker = pickers[-1]
            break
    if not picker:
        return {"ok": False, "archivo": ruta, "error": "picker no apareció"}

    # 3. Asegurar tab "Subir" activo
    picker.evaluate(
        "() => { document.querySelectorAll('[role=tab]').forEach(t => { "
        "  if ((t.innerText || '').trim() === 'Subir' && t.offsetParent) t.click(); "
        "}); }"
    )
    time.sleep(2)

    # 4. Subir según tipo
    if es_zip:
        # ZIP: FileChooser bypasea accept del input
        try:
            with page.expect_file_chooser(timeout=20000) as fc_info:
                coords_ex = coords_examinar_picker(page)
                if coords_ex:
                    page.mouse.click(coords_ex["x"], coords_ex["y"])
                else:
                    picker.evaluate(
                        "() => { document.querySelectorAll('button').forEach(b => { "
                        "  if ((b.innerText || '').trim().toLowerCase() === 'examinar' && b.offsetParent) b.click(); "
                        "}); }"
                    )
            fc_info.value.set_files(ruta)
            metodo = "file_chooser"
        except Exception as exc:
            return {"ok": False, "archivo": ruta,
                    "error": f"file_chooser timeout: {exc}"}
    else:
        # PDF: set_input_files directo
        try:
            picker.locator("input[type=file]").set_input_files(ruta, timeout=15000)
            metodo = "set_input_files"
        except Exception as exc:
            return {"ok": False, "archivo": ruta,
                    "error": f"set_input_files: {exc}"}

    # 5. Esperar procesamiento (PDF/ZIP grandes tardan más)
    time.sleep(10)

    # 6. Verificar visualmente
    verif = page.evaluate(
        """(titulo) => {
            const items = document.querySelectorAll('[role=listitem]');
            for (const item of items) {
                const heading = item.querySelector('[role=heading]');
                const t = heading ? (heading.innerText || '').toLowerCase() : '';
                if (t.includes(titulo.toLowerCase())) {
                    const txt = item.innerText || '';
                    const m = txt.match(/[\\w\\d\\.\\-\\s]+\\.(pdf|zip)/i);
                    return m ? m[0].trim() : null;
                }
            }
            return null;
        }""",
        titulo_listitem,
    )
    ok = verif is not None
    return {
        "ok":       ok,
        "archivo":  ruta,
        "metodo":   metodo,
        "verif":    verif,
        "error":    None if ok else "archivo no visible en listitem tras subida",
    }


def doble_chequeo_form_muni(page, esperados: dict) -> dict:
    """Verifica que los campos del form muni tengan los valores esperados.

    Args:
        esperados: dict {"campo_value": "valor_esperado"} — los values se
            buscan en cualquier input[type=text|email] visible.
            Ej: {"tomo": "2025", "asiento": "81701", "finca": "2 629270-000"}

    Returns:
        {"ok": bool, "encontrados": [...], "faltantes": [...]}
    """
    valores_form = page.evaluate(
        """() => {
            const out = [];
            document.querySelectorAll('input[type=text], input[type=email]').forEach(el => {
                if (el.offsetParent && el.value) out.push(el.value);
            });
            return out;
        }"""
    )
    valores_set = {str(v) for v in valores_form}
    faltantes = []
    encontrados = []
    for k, v in esperados.items():
        if str(v) in valores_set or any(str(v) in fv for fv in valores_set):
            encontrados.append({"campo": k, "valor": str(v)})
        else:
            faltantes.append({"campo": k, "valor_esperado": str(v)})
    return {
        "ok":          len(faltantes) == 0,
        "encontrados": encontrados,
        "faltantes":   faltantes,
        "valores_form_actuales": list(valores_set),
    }


__all__ = [
    "cerrar_pickers_residuales",
    "coords_boton_picker",
    "coords_examinar_picker",
    "subir_archivo_a_picker",
    "doble_chequeo_form_muni",
]
