"""Resumen general de todos los planos — tabla consultable rápida.

USO:
  catastro-bot resumen                     # tabla completa
  catastro-bot resumen --activos           # solo los que NO están inscritos
  catastro-bot resumen --apt               # solo los que tienen trámite APT
  catastro-bot resumen --muni              # los que están en flujo muni
  catastro-bot resumen --hoy               # solo creados hoy
"""
from __future__ import annotations
import argparse
import io
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

os.chdir(r"C:\catastro-bot")
os.environ.setdefault("CATASTRO_BOT_DEV_MODE", "1")
sys.path.insert(0, os.getcwd())
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")


# ── Mapeo estado → etapa humana + icono ────────────────────────────────

ETAPAS = {
    # Etapa 1: Preparación
    "recibido":              ("📥 1. Expediente creado",                "Subir archivos a 01_Campo"),
    "en_extraccion":         ("🔍 1. Extrayendo datos PDFs",            "Vision/pypdf procesando"),
    "listo_para_apt":        ("✅ 2. Datos listos para APT",            "catastro-bot apt-flujo"),
    # Etapa 3: Llenado en portal CFIA
    "en_llenado_contrato":   ("📝 3a. Llenando contrato APT",           "catastro-bot apt-plano"),
    "en_llenado_plano":      ("📐 3b. Llenando plano APT",              "Revisar + apt-enviar"),
    # Etapa 4: Revisión R1
    "presentado_apt_r1":     ("🚀 4. En revisión R1 (CFIA, 5-7 días)",  "Esperar respuesta CFIA"),
    "enviado_cfia":          ("🚀 4. Enviado al CFIA",                  "Esperar respuesta R1"),
    "en_calificacion":       ("⏳ 4. En calificación del RNP",          "Calificador revisando"),
    # Etapa 5: Resultado R1
    "respondido_r1":         ("📨 5. CFIA respondió R1 con minuta",     "Revisar + tramitar muni"),
    "aprobado_r1":           ("✅ 5. R1 aprobado",                      "Continuar muni o R2"),
    "defectuoso":            ("⚠️ 5. R1 defectuoso",                    "Corregir y re-presentar"),
    # Etapa 6: Documentos pendientes
    "carta_agua_requerida":  ("💧 6. Falta carta agua AyA",             "Tramitar carta agua AyA"),
    "carta_agua_pendiente":  ("💧 6. Carta agua presentada al AyA",     "Esperar respuesta AyA"),
    "documento_pendiente":   ("📄 6. Documento pendiente",              "Completar documento"),
    # Etapa 7: Corrección
    "en_correccion":         ("🔧 7. Corrigiendo plano",                "Re-presentar correcciones"),
    # Etapa 8: Muni
    "enviado_muni":          ("🏛️ 8. Enviado a la muni",                "Esperar visado muni"),
    "muni_morosidad":        ("💰 8. Muni: cliente con morosidad",      "Avisar cliente"),
    "muni_aprobado":         ("✅ 8. Muni visó — listo R2",             "catastro-bot apt-r2"),
    "muni_rechazado":        ("❌ 8. Muni rechazó",                     "Revisar observaciones"),
    # Etapa 9-10: R2 y cierre
    "presentado_apt_r2":     ("🚀 9. En revisión R2 (CFIA)",            "Esperar inscripción"),
    "inscrito":              ("🎉 10. INSCRITO",                        "✅ Plano completo"),
    "cerrado":               ("🔒 Cerrado",                             "Trámite finalizado"),
}


def _etapa_humana(estado: str) -> tuple[str, str]:
    return ETAPAS.get(estado, (f"❓ {estado}", "-"))


def _dias_desde(ts: str) -> int:
    if not ts:
        return -1
    try:
        # ts puede venir con o sin TZ
        if "+" in ts or ts.endswith("Z"):
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
    except Exception:
        return -1
    delta = datetime.now(timezone.utc) - dt
    return int(delta.total_seconds() // 86400)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--activos", action="store_true",
                        help="Solo los que NO están inscritos")
    parser.add_argument("--apt", action="store_true",
                        help="Solo los que tienen trámite APT")
    parser.add_argument("--muni", action="store_true",
                        help="Solo los que están en flujo municipal")
    parser.add_argument("--hoy", action="store_true",
                        help="Solo creados hoy")
    parser.add_argument("--exp", help="Filtrar por número de expediente (parcial)")
    args = parser.parse_args()

    conn = sqlite3.connect("data/catastro.db")
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT numero_expediente, tipo_plano, estado_actual, "
        "       fecha_creacion, fecha_actualizacion, nombre_cliente, "
        "       telefono_cliente, metadata_json "
        "FROM expedientes ORDER BY fecha_creacion DESC"
    ).fetchall()

    # Filtros
    filtered = []
    hoy_str = datetime.now().strftime("%Y-%m-%d")
    for r in rows:
        if args.activos and r["estado_actual"] == "inscrito":
            continue
        meta = json.loads(r["metadata_json"] or "{}")
        if args.apt and not meta.get("apt_tramite"):
            continue
        if args.muni and "muni" not in (r["estado_actual"] or ""):
            continue
        if args.hoy and not (r["fecha_creacion"] or "").startswith(hoy_str):
            continue
        if args.exp and args.exp.upper() not in r["numero_expediente"].upper():
            continue
        filtered.append((r, meta))

    if not filtered:
        print("(sin expedientes que coincidan con el filtro)")
        return 0

    # Resumen por etapa
    por_etapa: dict[str, int] = {}
    for _, m in filtered:
        # Si tiene apt_estado real de APT, usar ese; sino estado del bot
        # En realidad usamos estado_actual del bot como fuente principal
        pass

    print()
    print("═" * 110)
    print(f"  RESUMEN DE PLANOS — {len(filtered)} expediente(s)")
    print("═" * 110)
    print()
    print(f"  {'#':<4}{'Expediente':<16}{'Etapa':<42}{'Trámite':<11}"
          f"{'Proyecto':<14}{'Cliente':<28}{'Días':<6}{'Próximo paso'}")
    print(f"  {'─'*4} {'─'*15} {'─'*41} {'─'*10} {'─'*13} {'─'*27} {'─'*5} {'─'*30}")

    contador_etapa: dict[str, int] = {}
    for i, (r, meta) in enumerate(filtered, 1):
        estado = r["estado_actual"] or "?"
        etapa, descripcion = _etapa_humana(estado)
        contador_etapa[etapa] = contador_etapa.get(etapa, 0) + 1

        tramite = meta.get("apt_tramite", "-")
        if tramite == "-" and meta.get("nombre_proyecto"):
            tramite = "(sin APT)"

        proyecto = (meta.get("nombre_proyecto") or "-")[:13]

        # Cliente = propietario real (de datos_apt)
        prop = meta.get("datos_apt", {}).get("propietario", {})
        if prop.get("nombre"):
            cliente = f"{prop['nombre']} {prop.get('apellido1', '')}".strip()
        else:
            cliente = (r["nombre_cliente"] or "-")
        cliente = cliente[:27]

        dias = _dias_desde(r["fecha_actualizacion"])
        dias_str = f"{dias}d" if dias >= 0 else "?"

        # Próximo paso esperado
        proxs = {
            "recibido":              "Subir archivos a 01_Campo",
            "listo_para_apt":        "catastro-bot apt-flujo",
            "en_llenado_contrato":   "catastro-bot apt-plano",
            "en_llenado_plano":      "catastro-bot apt-enviar",
            "presentado_apt_r1":     "Esperar R1 CFIA (5-7 días)",
            "respondido_r1":         "Revisar minuta + tramitar muni",
            "aprobado_r1":           "Continuar a muni o R2",
            "en_correccion":         "Corregir DWG + re-presentar",
            "enviado_muni":          "Esperar visado muni",
            "muni_morosidad":        "Avisar cliente que pague",
            "muni_aprobado":         "catastro-bot apt-r2",
            "muni_rechazado":        "Revisar observaciones muni",
            "presentado_apt_r2":     "Esperar inscripción CFIA",
            "inscrito":              "✅ Plano completo",
            "defectuoso":            "Revisar errores + R2",
            "enviado_cfia":          "Esperar R1 CFIA",
            "carta_agua_requerida":  "Tramitar carta agua AyA",
            "carta_agua_pendiente":  "Esperar respuesta AyA",
            "documento_pendiente":   "Completar documento",
            "en_calificacion":       "Esperar calificador RNP",
            "cerrado":               "Trámite finalizado",
        }
        proximo = proxs.get(estado, "-")[:30]

        print(f"  {i:<4}{r['numero_expediente']:<16}{etapa:<42}"
              f"{str(tramite):<11}{proyecto:<14}{cliente:<28}{dias_str:<6}{proximo}")

    print()
    print("─" * 140)
    print(f"  Resumen por etapa:")
    for etapa, n in sorted(contador_etapa.items(), key=lambda x: -x[1]):
        print(f"    {etapa:<42} {n} plano(s)")
    print()
    print(f"  📅 Reporte generado: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print()
    print("  Ver detalle de uno:    catastro-bot debug estado <expediente>")
    print("  Filtrar solo activos:  catastro-bot resumen --activos")
    print("  Filtrar con APT:       catastro-bot resumen --apt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
