"""APT SYNC — consulta APT por estado y fecha real de cada trámite.

Lee del portal CFIA (vía CDP) el estado actual de cada trámite que tenemos
en BD y actualiza:
  - metadata.apt_estado     (estado real del portal)
  - metadata.apt_fecha_presentacion (cuando se envió a CFIA)
  - metadata.apt_fecha_ultima_consulta (cuándo polleamos)

Mapeo estado APT → estado bot:
  "Calificación RN"       → presentado_apt_r1 (sin cambio)
  "Defectuoso"            → defectuoso (notificar al operador)
  "Público e Inscrito"    → inscrito (notificar — terminado)
  "En Edición"            → en_llenado_plano (todavía editable)

USO:
  python tools/apt_sync.py                # todos los con trámite
  python tools/apt_sync.py SEG-2026-005   # uno solo
"""
from __future__ import annotations
import io
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

os.chdir(r"C:\catastro-bot")
os.environ.setdefault("CATASTRO_BOT_DEV_MODE", "1")
# DESACTIVAR simulaciones — esto es producción real
os.environ["CATASTRO_BOT_SIMULAR_APT"] = "0"
sys.path.insert(0, os.getcwd())
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")


def main() -> int:
    expediente_filtro = sys.argv[1].upper() if len(sys.argv) > 1 else None

    from src.agents.apt_agent import APTAgent
    from src.core.credential_manager import CredentialManager
    from src.core.database import Database

    creds = CredentialManager()
    db = Database(Path("data/catastro.db"), creds)
    agent = APTAgent(db, creds)

    if not agent._cdp_disponible():
        print("[ERROR] Chrome del bot no corriendo — lanzá tools/start_chrome_bot.py")
        return 1

    # Leer expedientes con apt_tramite
    conn = sqlite3.connect("data/catastro.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, numero_expediente, estado_actual, metadata_json "
        "FROM expedientes WHERE metadata_json LIKE '%apt_tramite%'"
    ).fetchall()
    conn.close()

    objetivos = []
    for r in rows:
        meta = json.loads(r["metadata_json"] or "{}")
        tramite = meta.get("apt_tramite", "")
        if not tramite or "TEST" in str(tramite) or "(compartido)" in str(tramite):
            continue
        if expediente_filtro and r["numero_expediente"] != expediente_filtro:
            continue
        objetivos.append({
            "id": r["id"],
            "numero": r["numero_expediente"],
            "estado_actual": r["estado_actual"],
            "tramite": str(tramite).strip(),
        })

    if not objetivos:
        print("(no hay expedientes con trámite APT real para consultar)")
        return 0

    print(f"Consultando APT por {len(objetivos)} trámite(s)...")
    print()
    actualizados = 0
    errores = 0

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(agent._cdp_endpoint)
        page = None
        for c in browser.contexts:
            for pg in c.pages:
                try:
                    if "apt.cfia.or.cr" in pg.url:
                        page = pg; break
                except Exception: continue
            if page: break
        if not page:
            print("[ERROR] No hay pestaña apt.cfia.or.cr abierta. Navegá ahí.")
            return 2

        # Verificar sesión activa
        try:
            if not agent._confirmar_sesion_apt(page):
                print("[ERROR] Sesión APT no activa")
                return 3
        except Exception as e:
            print(f"[WARN] No pude confirmar sesión: {e}")

        for exp in objetivos:
            print(f"  {exp['numero']:<16} trámite {exp['tramite']:<10} ", end="")
            try:
                estado_raw = agent.consultar_estado(exp["id"])
                estado_norm = agent._normalizar_estado(estado_raw)
                print(f"APT='{estado_raw}' → normalizado='{estado_norm}'")

                # Actualizar metadata
                conn = sqlite3.connect("data/catastro.db")
                r = conn.execute("SELECT metadata_json FROM expedientes WHERE id=?",
                                 (exp["id"],)).fetchone()
                meta = json.loads(r[0] or "{}")
                meta["apt_estado"] = estado_raw or ""
                meta["apt_fecha_ultima_consulta"] = datetime.now().isoformat()
                # Si es la primera consulta y hay un estado, guardar como fecha estimada de presentación
                if "apt_fecha_presentacion" not in meta and estado_raw:
                    meta["apt_fecha_presentacion"] = datetime.now().isoformat()

                conn.execute("UPDATE expedientes SET metadata_json=? WHERE id=?",
                             (json.dumps(meta, ensure_ascii=False), exp["id"]))

                # Si cambió a inscrito o defectuoso, actualizar estado_actual
                if estado_norm == "inscrito" and exp["estado_actual"] != "inscrito":
                    conn.execute("UPDATE expedientes SET estado_actual=? WHERE id=?",
                                 ("inscrito", exp["id"]))
                    print(f"    🎉 INSCRITO — actualizado en BD")
                elif estado_norm == "respondido" and exp["estado_actual"] in (
                    "presentado_apt_r1", "enviado_cfia"):
                    conn.execute("UPDATE expedientes SET estado_actual=? WHERE id=?",
                                 ("defectuoso", exp["id"]))
                    print(f"    ⚠️ DEFECTUOSO — actualizado en BD")

                conn.commit()
                conn.close()
                actualizados += 1
            except Exception as e:
                print(f"ERROR: {str(e)[:80]}")
                errores += 1

    print()
    print(f"✅ Actualizados: {actualizados}")
    if errores:
        print(f"❌ Errores: {errores}")
    return 0 if errores == 0 else 4


if __name__ == "__main__":
    sys.exit(main())
