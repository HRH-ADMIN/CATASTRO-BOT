"""APRENDER cómo llenar un contrato APT por demostración.

Flujo:
  1. Toma snapshot del form en blanco.
  2. Usted llena el contrato manualmente en su Chrome.
  3. Antes de guardar, vuelve aquí y presiona Enter.
  4. Bot detecta TODOS los campos que cambiaron.
  5. Por cada campo le pregunta:
       - ¿Es siempre este valor para este tipo de plano?
       - ¿Depende de algo (cliente, ubicación, etc.)?
       - ¿Solo aplica a este caso?
  6. Guarda el conocimiento en data/apt_form_knowledge.json.
  7. La próxima vez, el bot pre-llena lo que aprendió y solo le pregunta lo que varía.

USO:
  .venv\\Scripts\\python tools\\aprender_contrato.py SEG-2026-001
"""
from __future__ import annotations
import io
import json
import os
import sys
from pathlib import Path
from datetime import datetime

os.environ.setdefault("CATASTRO_BOT_DEV_MODE", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from playwright.sync_api import sync_playwright


KNOWLEDGE_PATH = Path(__file__).resolve().parents[1] / "data" / "apt_form_knowledge.json"


def cargar_knowledge() -> dict:
    if KNOWLEDGE_PATH.exists():
        try:
            return json.loads(KNOWLEDGE_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def guardar_knowledge(data: dict) -> None:
    KNOWLEDGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    KNOWLEDGE_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def snapshot_form(page) -> dict:
    """Captura {field_id: {value, label, tag, type, options}} del form actual."""
    js = """
    () => {
        const out = {};
        document.querySelectorAll('input, select, textarea').forEach((el) => {
            if (!el.id && !el.name) return;
            const key = el.id || el.name;
            let label = '';
            if (el.id) {
                const lbl = document.querySelector(`label[for="${el.id}"]`);
                if (lbl) label = lbl.innerText.trim();
            }
            if (!label && el.placeholder) label = '(ph) ' + el.placeholder;
            if (!label && el.name) label = '(name) ' + el.name;
            // Para select: capturar value + texto de la opción
            let textValue = el.value;
            if (el.tagName === 'SELECT') {
                const opt = el.options[el.selectedIndex];
                if (opt) textValue = `${el.value} (${opt.text.trim()})`;
            } else if (el.type === 'checkbox' || el.type === 'radio') {
                textValue = el.checked ? 'CHECKED' : 'UNCHECKED';
            }
            out[key] = {
                tag: el.tagName.toLowerCase(),
                type: el.type || '',
                value: el.value || '',
                text_value: textValue,
                label: label.substring(0, 100),
            };
        });
        return out;
    }
    """
    return page.evaluate(js)


def diff_snapshots(before: dict, after: dict) -> list:
    """Devuelve lista de campos cuyo value cambió."""
    changes = []
    for key, after_field in after.items():
        before_field = before.get(key, {})
        before_val = before_field.get("value", "")
        after_val = after_field.get("value", "")
        if before_val != after_val:
            changes.append({
                "id": key,
                "before": before_val,
                "after": after_val,
                "text_after": after_field.get("text_value", after_val),
                "label": after_field.get("label", ""),
                "tag": after_field.get("tag", ""),
                "type": after_field.get("type", ""),
            })
    return changes


def prompt(msg: str) -> str:
    print(f"\n{msg}", end="", flush=True)
    try:
        return input().strip()
    except EOFError:
        return ""


def main() -> int:
    if len(sys.argv) < 2:
        print("USO: aprender_contrato.py <NUMERO_EXPEDIENTE>")
        return 1
    numero_exp = sys.argv[1].upper()

    # Cargar tipo de plano del expediente desde BD
    import sqlite3
    conn = sqlite3.connect("data/catastro.db")
    row = conn.execute(
        "SELECT tipo_plano, nombre_cliente, nombre_topografo "
        "FROM expedientes WHERE numero_expediente = ?",
        (numero_exp,),
    ).fetchone()
    if not row:
        print(f"[ERROR] Expediente {numero_exp} no existe.")
        return 1
    tipo_plano, cliente, topografo = row
    print(f"\nExpediente: {numero_exp}")
    print(f"  tipo_plano       = {tipo_plano}")
    print(f"  nombre_cliente   = {cliente}")
    print(f"  nombre_topografo = {topografo}")

    # Cargar knowledge previa si existe
    knowledge = cargar_knowledge()
    by_tipo = knowledge.setdefault("by_tipo_plano", {})
    rules_para_tipo = by_tipo.setdefault(tipo_plano, {})

    if rules_para_tipo:
        print(f"\n[INFO] Ya hay {len(rules_para_tipo)} reglas aprendidas para tipo_plano={tipo_plano!r}:")
        for fid, info in rules_para_tipo.items():
            print(f"  • {fid} = {info.get('value')!r}  ({info.get('note', '')})")

    # Conectar Chrome
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        try:
            apt_page = None
            for c in browser.contexts:
                for pg in c.pages:
                    if "apt.cfia.or.cr/APT2/Contrato" in pg.url:
                        apt_page = pg
                        break
                if apt_page:
                    break
            if not apt_page:
                print("[ERROR] No hay pestaña APT en /Contrato/. Abra Nuevo Contrato primero.")
                return 1

            apt_page.bring_to_front()
            print(f"\n[OK] Pestaña APT: {apt_page.url}")

            # Snapshot inicial
            print("\n[STEP 1] Tomando snapshot inicial del form...")
            snap_inicial = snapshot_form(apt_page)
            print(f"   {len(snap_inicial)} campos detectados.")

            # Si hay reglas aprendidas, ofrecer pre-llenar
            if rules_para_tipo:
                ans = prompt(f">>> Pre-llenar {len(rules_para_tipo)} campos con reglas aprendidas? [s/N]: ")
                if ans.lower() == "s":
                    aplicados = 0
                    for fid, info in rules_para_tipo.items():
                        val = info.get("value", "")
                        try:
                            sel = f"#{fid}"
                            elem = apt_page.locator(sel)
                            if elem.count() == 0:
                                continue
                            tag = info.get("tag", "")
                            if tag == "select":
                                apt_page.select_option(sel, value=val)
                            elif info.get("type") in ("checkbox", "radio"):
                                if val.upper() == "CHECKED":
                                    elem.first.check()
                            else:
                                elem.first.fill(val)
                            print(f"   ✓ {fid} ← {val!r}")
                            aplicados += 1
                        except Exception as e:
                            print(f"   ✗ {fid}: {e}")
                    print(f"\n[OK] {aplicados} campos pre-llenados.")

            print()
            print("=" * 60)
            print("  AHORA LLENE EL CONTRATO MANUALMENTE EN SU CHROME.")
            print("  Llene todos los campos que sepa.")
            print("  NO haga clic en Guardar todavía.")
            print("=" * 60)
            input("\nCuando termine de llenar, presione Enter aquí... ")

            # Snapshot final
            print("\n[STEP 2] Capturando snapshot final...")
            snap_final = snapshot_form(apt_page)

            cambios = diff_snapshots(snap_inicial, snap_final)
            print(f"   {len(cambios)} campos cambiaron.")

            if not cambios:
                print("\n[INFO] No detecté cambios. Si llenó algo, puede que el form esté en un iframe.")
                return 0

            # Aprender de cada cambio
            print("\n" + "=" * 60)
            print(f"  APRENDIENDO {len(cambios)} CAMPOS")
            print("=" * 60)

            nuevos = 0
            for i, c in enumerate(cambios, 1):
                fid = c["id"]
                ya_aprendido = fid in rules_para_tipo
                marca = "[YA EN KNOWLEDGE]" if ya_aprendido else ""
                print(f"\n── [{i}/{len(cambios)}] {marca} ──")
                print(f"  Campo:   #{fid}")
                print(f"  Etiqueta: {c['label']!r}")
                print(f"  Tag/type: {c['tag']}/{c['type']}")
                print(f"  Antes:   {c['before']!r}")
                print(f"  Después: {c['after']!r}    [{c['text_after']}]")
                print()
                print("  ¿Cómo es este valor?")
                print("    s = SIEMPRE este valor para este tipo de plano (regla fija)")
                print("    v = VARÍA — me explico el patrón")
                print("    u = ÚNICO de este caso (no aprender)")
                print("    n = saltar este campo")
                ans = prompt("  Respuesta [s/v/u/n]: ").lower()

                if ans == "s":
                    nota = prompt("  Nota opcional (¿por qué siempre es este valor?): ")
                    rules_para_tipo[fid] = {
                        "value": c["after"],
                        "text_value": c["text_after"],
                        "label": c["label"],
                        "tag": c["tag"],
                        "type": c["type"],
                        "note": nota,
                        "learned_at": datetime.now().isoformat(timespec="seconds"),
                        "rule": "fixed",
                    }
                    nuevos += 1
                    print(f"  ✓ Guardado como regla fija.")
                elif ans == "v":
                    explicacion = prompt("  ¿De qué depende? (ej: 'depende de la provincia del terreno'): ")
                    # Guardamos como variable para revisar luego
                    rules_para_tipo[fid] = {
                        "value": c["after"],
                        "text_value": c["text_after"],
                        "label": c["label"],
                        "tag": c["tag"],
                        "type": c["type"],
                        "note": explicacion,
                        "ejemplo_actual": c["after"],
                        "learned_at": datetime.now().isoformat(timespec="seconds"),
                        "rule": "variable",
                    }
                    nuevos += 1
                    print(f"  ✓ Guardado como variable. (Bot preguntará en cada caso futuro.)")
                elif ans == "u":
                    print(f"  · No se guarda.")
                else:
                    print(f"  · Saltado.")

            # Guardar knowledge
            guardar_knowledge(knowledge)
            print()
            print("=" * 60)
            print(f"  KNOWLEDGE actualizado:")
            print(f"  → {KNOWLEDGE_PATH}")
            print(f"  → {nuevos} reglas nuevas/actualizadas para tipo_plano={tipo_plano!r}")
            print(f"  → Total reglas para este tipo: {len(rules_para_tipo)}")
            print("=" * 60)

            # Ofrecer guardar el contrato
            ans = prompt("\n>>> Hago clic en GUARDAR ahora? [s/N]: ")
            if ans.lower() == "s":
                btn = apt_page.locator("#BtnGuardar")
                if btn.count() > 0:
                    btn.first.click()
                    apt_page.wait_for_load_state("networkidle")
                    print("[OK] Click en Guardar.")
                    import time as _t
                    _t.sleep(2)
                    # Intentar extraer trámite
                    import re
                    url = apt_page.url
                    print(f"\n  URL después de guardar: {url}")
                    for patron in [r"[Nn]um[Cc]ontrato=(\d+)", r"contrato/(\d+)", r"tramite=(\d+)", r"id=(\d+)"]:
                        m = re.search(patron, url, re.IGNORECASE)
                        if m:
                            print(f"  ✅ TRAMITE: {m.group(1)}")
                            break
                    else:
                        # Buscar en texto
                        try:
                            txt = apt_page.inner_text("body")
                            m = re.search(r"[Tt]r[áa]mite[:\s#]*(\d{4,})", txt)
                            if m:
                                print(f"  ✅ TRAMITE (de texto): {m.group(1)}")
                            else:
                                print("  ⚠️ No se pudo extraer trámite. Lea en pantalla.")
                        except Exception:
                            pass
                else:
                    print("[ERROR] No se encontró #BtnGuardar")

            print("\n[FIN]")
            return 0

        finally:
            with __import__("contextlib").suppress(Exception):
                browser.close()


if __name__ == "__main__":
    sys.exit(main())
