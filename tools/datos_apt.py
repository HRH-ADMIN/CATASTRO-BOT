"""CLI interactivo para llenar `datos_apt` de un expediente.

Pregunta uno por uno los campos que el bot necesita para crear el contrato APT,
y guarda el resultado en metadata.datos_apt del expediente.

USO:
  python tools/datos_apt.py SEG-2026-001          # llenar de cero
  python tools/datos_apt.py SEG-2026-001 --edit   # editar uno existente
  python tools/datos_apt.py SEG-2026-001 --show   # solo mostrar lo que hay
"""
from __future__ import annotations
import io
import json
import os
import sqlite3
import sys
from pathlib import Path

os.environ.setdefault("CATASTRO_BOT_DEV_MODE", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


# ═══ Catálogos ═══

TIPO_CEDULA_OPCIONES = {
    "1":  "FISICA",
    "2":  "JURIDICA",
    "3":  "MENOR NACIONAL",
    "4":  "CÉDULA DE RESIDENCIA",
    "5":  "CARNÉ DE PENSIONADO",
    "6":  "PASAPORTE",
    "7":  "CARNÉ DE REFUGIADO",
    "8":  "CARNÉ DE SEGURO SOCIAL",
    "9":  "LICENCIA DE CONDUCIR",
    "11": "DIMEX",
    "12": "NITÉ",
}

PROVINCIAS = {
    "1": "SAN JOSÉ",
    "2": "ALAJUELA",
    "3": "CARTAGO",
    "4": "HEREDIA",
    "5": "GUANACASTE",
    "6": "PUNTARENAS",
    "7": "LIMÓN",
}

NATURALEZA = {
    "1": "Derecho",
    "2": "Equidad",
    "3": "Pericial",
}

MONEDA = {
    "1": "COLÓN(ES)",
    "2": "DÓLAR(ES)",
    "5": "EURO(S)",
}

# tipo_plano del bot → siempre "27" (Plano Simple) según política del topógrafo
TIPO_PLANO_APT_DEFAULT = "27"


# ═══ Helpers ═══

def ask(label: str, default: str = "", *, opciones: dict = None) -> str:
    """Prompt interactivo. Enter = aceptar default."""
    suffix = f" [{default}]" if default else ""
    if opciones:
        print()
        for k, v in opciones.items():
            print(f"  {k:>3} = {v}")
    print(f">>> {label}{suffix}: ", end="", flush=True)
    try:
        ans = input().strip()
    except EOFError:
        ans = ""
    return ans or default


def ask_bool(label: str, default: bool = True) -> bool:
    d = "S" if default else "N"
    while True:
        ans = ask(f"{label} [s/n]", d).lower()
        if ans in ("s", "si", "y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        print("  Responda s o n.")


def section(titulo: str) -> None:
    print()
    print("═" * 60)
    print(f"  {titulo}")
    print("═" * 60)


# ═══ Lógica principal ═══

def cargar_expediente(numero: str) -> dict | None:
    conn = sqlite3.connect("data/catastro.db")
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT id, numero_expediente, tipo_plano, nombre_cliente, "
        "nombre_topografo, estado_actual, metadata FROM expedientes "
        "WHERE numero_expediente = ?",
        (numero,),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return dict(row)


def guardar_datos_apt(exp_id: str, datos_apt: dict) -> None:
    conn = sqlite3.connect("data/catastro.db")
    cur = conn.execute("SELECT metadata FROM expedientes WHERE id = ?", (exp_id,))
    row = cur.fetchone()
    meta = json.loads(row[0]) if row and row[0] else {}
    meta["datos_apt"] = datos_apt
    conn.execute(
        "UPDATE expedientes SET metadata = ? WHERE id = ?",
        (json.dumps(meta, ensure_ascii=False), exp_id),
    )
    conn.commit()
    conn.close()


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    numero = sys.argv[1].upper().strip()
    show_only = "--show" in sys.argv
    edit_mode = "--edit" in sys.argv

    exp = cargar_expediente(numero)
    if not exp:
        print(f"[ERROR] Expediente {numero} no existe.")
        return 1

    print()
    print("═" * 60)
    print(f"  datos_apt — Expediente {numero}")
    print(f"  tipo_plano = {exp['tipo_plano']}")
    print("═" * 60)

    meta = json.loads(exp["metadata"]) if exp["metadata"] else {}
    actuales = meta.get("datos_apt", {})

    if show_only or edit_mode:
        if actuales:
            print("\n[ACTUAL] datos_apt en metadata:\n")
            print(json.dumps(actuales, ensure_ascii=False, indent=2))
        else:
            print("\n[INFO] No hay datos_apt aún.")
        if show_only:
            return 0

    print("\nResponda cada pregunta. Enter para usar el valor por defecto.")
    print("Ctrl+C en cualquier momento para abortar.\n")

    datos = dict(actuales) if edit_mode else {}

    # ─── PROPIETARIO ─────────────────────────────────────────────────────
    section("PROPIETARIO (dueño del terreno)")
    prop = datos.get("propietario", {}) if edit_mode else {}
    p = {}
    p["tipo_cedula"] = ask("Tipo de cédula", prop.get("tipo_cedula", "1"), opciones=TIPO_CEDULA_OPCIONES)
    p["cedula"]      = ask("Cédula", prop.get("cedula", ""))
    if p["tipo_cedula"] == "2":
        p["nombre"]    = ask("Nombre/Razón social (jurídica)", prop.get("nombre", ""))
        p["apellido1"] = ""
        p["apellido2"] = ""
    else:
        p["nombre"]    = ask("Nombre", prop.get("nombre", ""))
        p["apellido1"] = ask("Primer apellido", prop.get("apellido1", ""))
        p["apellido2"] = ask("Segundo apellido", prop.get("apellido2", ""))
    p["correo"] = ask("Correo electrónico", prop.get("correo", ""))
    datos["propietario"] = p

    # ─── CONTRATANTE ─────────────────────────────────────────────────────
    section("CONTRATANTE (quien contrata el servicio)")
    es_mismo_default = datos.get("contratante_es_propietario", True)
    es_mismo = ask_bool("¿El contratante es el mismo propietario?", es_mismo_default)
    datos["contratante_es_propietario"] = es_mismo
    if not es_mismo:
        cont = datos.get("contratante", {}) if edit_mode else {}
        c = {}
        c["tipo_cedula"] = ask("Tipo de cédula del contratante", cont.get("tipo_cedula", "1"), opciones=TIPO_CEDULA_OPCIONES)
        c["cedula"]      = ask("Cédula del contratante", cont.get("cedula", ""))
        c["nombre"]      = ask("Nombre completo del contratante", cont.get("nombre", ""))
        c["correo"]      = ask("Correo del contratante", cont.get("correo", ""))
        datos["contratante"] = c

    # ─── PROTOCOLO ───────────────────────────────────────────────────────
    section("PROTOCOLO")
    prot = datos.get("protocolo", {}) if edit_mode else {}
    pr = {}
    pr["numero"]               = ask("Número de protocolo (valor del dropdown)", prot.get("numero", ""))
    pr["folio"]                = ask("Folio", prot.get("folio", ""))
    pr["tipo_proyecto_modal"]  = ask("Tipo de proyecto modal (igual al APT, def=27)", prot.get("tipo_proyecto_modal", TIPO_PLANO_APT_DEFAULT))
    datos["protocolo"] = pr

    # ─── PROYECTO ────────────────────────────────────────────────────────
    section("PROYECTO")
    proy = datos.get("proyecto", {}) if edit_mode else {}
    py = {}
    py["tipo_plano_apt"] = ask("Tipo de plano APT (siempre 27=Plano Simple)", proy.get("tipo_plano_apt", TIPO_PLANO_APT_DEFAULT))
    py["provincia"]      = ask("Provincia (ubicación del terreno)", proy.get("provincia", ""), opciones=PROVINCIAS)
    py["canton"]         = ask("Cantón (código numérico que ve en APT)", proy.get("canton", ""))
    py["distrito"]       = ask("Distrito (código numérico que ve en APT)", proy.get("distrito", ""))
    py["descripcion"]    = ask("Descripción del proyecto", proy.get("descripcion", ""))
    py["naturaleza"]     = ask("Naturaleza", proy.get("naturaleza", "1"), opciones=NATURALEZA)
    print("\n  -- DONDE SE FIRMA EL CONTRATO --")
    py["firma_provincia"] = ask("Provincia de firma", proy.get("firma_provincia", py["provincia"]), opciones=PROVINCIAS)
    py["firma_canton"]    = ask("Cantón de firma", proy.get("firma_canton", py["canton"]))
    py["firma_distrito"]  = ask("Distrito de firma", proy.get("firma_distrito", py["distrito"]))
    datos["proyecto"] = py

    # ─── GENERAL (área, honorarios, entero) ──────────────────────────────
    section("DATOS GENERALES")
    gen = datos.get("general", {}) if edit_mode else {}
    g = {}
    g["area_predio"]            = ask("Área aproximada del predio (m²)", gen.get("area_predio", ""))
    g["area_real"]              = ask("Área real a catastrar (m²)", gen.get("area_real", ""))
    g["moneda"]                 = ask("Moneda", gen.get("moneda", "1"), opciones=MONEDA)
    g["honorarios"]             = ask("Monto tentativo honorarios", gen.get("honorarios", ""))
    g["honorarios_letras"]      = ask("Honorarios en letras (opcional)", gen.get("honorarios_letras", ""))
    g["exoneracion_honorarios"] = ask_bool("¿Exoneración de honorarios?", gen.get("exoneracion_honorarios", False))
    g["adelanto"]               = ask("Monto adelanto (opcional)", gen.get("adelanto", ""))
    g["pagos_parciales"]        = ask("Pagos parciales (opcional)", gen.get("pagos_parciales", ""))
    g["plazo_entrega"]          = ask("Plazo de entrega (opcional)", gen.get("plazo_entrega", ""))
    g["max_planos"]             = ask("Máximo de planos del contrato", gen.get("max_planos", "1"))
    g["observaciones"]          = ask("Observaciones (opcional)", gen.get("observaciones", ""))
    g["norte"]                  = ask("Coordenada Norte (opcional)", gen.get("norte", ""))
    g["este"]                   = ask("Coordenada Este (opcional)", gen.get("este", ""))
    g["composicion"]            = ask("Composición [unipersonal/colegiado]", gen.get("composicion", "unipersonal"))

    # Datos Entero (BCR)
    print("\n  -- ENTERO (comprobante de pago BCR) --")
    ent = gen.get("entero", {}) if edit_mode else {}
    e = {}
    e["numero"]       = ask("Número de entero (BCR)", ent.get("numero", ""))
    e["monto"]        = ask("Monto pagado al CFIA", ent.get("monto", ""))
    e["fecha"]        = ask("Fecha de pago (YYYY-MM-DD)", ent.get("fecha", ""))
    e["archivo_pdf"]  = ask("Ruta absoluta al PDF entero (opcional)", ent.get("archivo_pdf", ""))
    g["entero"] = e
    datos["general"] = g

    # ─── FIRMAS ──────────────────────────────────────────────────────────
    section("FIRMAS")
    f = datos.get("firmas", {}) if edit_mode else {}
    firmas = {}
    from datetime import date
    firmas["fecha"] = ask("Fecha de firma del contrato (YYYY-MM-DD)", f.get("fecha", date.today().isoformat()))
    datos["firmas"] = firmas

    # ─── REVISIÓN ────────────────────────────────────────────────────────
    section("REVISIÓN — datos a guardar")
    print(json.dumps(datos, ensure_ascii=False, indent=2))
    print()
    if not ask_bool("¿Guardar estos datos en metadata.datos_apt?", True):
        print("Cancelado — no se guardaron cambios.")
        return 0

    guardar_datos_apt(exp["id"], datos)
    print(f"\n✅ Guardado en metadata.datos_apt del expediente {numero}.")
    print(f"   Próximo paso: WhatsApp → APT CREAR {numero}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
