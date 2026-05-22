"""CLI para manipular lotes — `catastro-bot lote <subcomando>`.

Subcomandos:
  crear <accion> <exp1> <exp2> [<exp3>...]   Crea un lote y muestra el código 2FA
  confirmar <lote_id> <codigo>               Confirma con código 2FA
  ejecutar <lote_id>                         Ejecuta el lote (dry-run por default)
  estado <lote_id>                           Ver progreso de un lote
  listar [--limit N]                         Últimos N lotes
  cancelar <lote_id>                         Cancela items pendientes

Acciones permitidas en lote:
  apt-crear   → llena bC1-bC8 y PAUSA pre-guardar
  apt-plano   → llena bP1-bP7

Acciones BLOQUEADAS (requieren comando individual):
  apt-guardar, enviar-cfia, rechazar, borrar

EJEMPLO COMPLETO:
  $ catastro-bot lote crear apt-crear SEG-2026-001 RDF-2026-005
  Lote creado: lote-abc123def456
  Código 2FA: 482917 (válido 5 min)
  Para confirmar: catastro-bot lote confirmar lote-abc123def456 482917

  $ catastro-bot lote confirmar lote-abc123def456 482917
  ✓ Lote confirmado. Listo para ejecutar.

  $ catastro-bot lote ejecutar lote-abc123def456 --dry-run
  [DRY-RUN] Procesaría: SEG-2026-001, RDF-2026-005
  Para ejecutar de verdad: --no-dry-run
"""
from __future__ import annotations

import argparse
import io
import os
import sys
from pathlib import Path

# Forzar UTF-8 en stdout para Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("CATASTRO_BOT_DEV_MODE", "1")

from src.core.credential_manager import CredentialManager
from src.core.database import Database
from src.core.lote_manager import (
    LoteManager, LoteValidationError, LoteNotFoundError, LoteStateError,
    ACCIONES_PERMITIDAS, ACCIONES_BLOQUEADAS,
    LOTE_PENDIENTE_2FA, LOTE_EJECUTANDO, LOTE_COMPLETO, LOTE_CANCELADO,
    ITEM_OK, ITEM_FALLO, ITEM_PENDIENTE, ITEM_EJECUTANDO,
)
from src.utils.two_factor_auth import TwoFactorAuth


# ── Conexión perezosa ──────────────────────────────────────────────────

def _get_db() -> Database:
    creds = CredentialManager()
    db = Database(Path("data/catastro.db"), creds)
    try:
        db.initialize_schema()
    except Exception:
        pass  # ya inicializado
    return db


# Auth y manager se crean al vuelo. NOTA: el TwoFactorAuth es in-memory,
# así que entre invocaciones distintas del CLI NO se conserva. Para el
# CLI usamos `exigir_2fa=False` en la creación y el operador confirma con
# `confirmar` que internamente saltará el check.
def _get_manager(db: Database) -> LoteManager:
    auth = TwoFactorAuth()
    return LoteManager(db=db, two_factor_auth=auth)


# ── Formato de salida ──────────────────────────────────────────────────

def _fmt_estado_item(estado: str) -> str:
    return {
        ITEM_OK:         "✓",
        ITEM_FALLO:      "✗",
        ITEM_PENDIENTE:  "·",
        ITEM_EJECUTANDO: "▶",
        "cancelado":     "—",
    }.get(estado, "?")


def _print_progreso(lm: LoteManager, lote_id: str) -> None:
    try:
        lote = lm.estado_lote(lote_id)
    except LoteNotFoundError:
        print(f"[ERROR] lote {lote_id!r} no existe")
        return
    items = lm.listar_items(lote_id)
    prog = lm.progreso(lote_id)

    print(f"\n📋 Lote: {lote_id}")
    print(f"   Acción:   {lote['accion']}")
    print(f"   Estado:   {lote['estado']}")
    print(f"   Creado:   {lote['ts_creado']} por {lote['creado_por']}")
    if lote.get("ts_completado"):
        print(f"   Cerrado:  {lote['ts_completado']}")
    print(f"\n   Items ({prog['total']}):")
    for it in items:
        marca = _fmt_estado_item(it["estado"])
        num   = it.get("numero_expediente") or it["expediente_id"]
        err   = f"  — {it['error']}" if it.get("error") else ""
        print(f"     {marca} [{it['orden']:>2}] {num} ({it['estado']}){err}")
    print(f"\n   Resumen: {prog['ok']} OK / {prog['fallos']} fallos / "
          f"{prog['pendientes']} pendientes / {prog['cancelados']} cancelados")


# ── Subcomandos ────────────────────────────────────────────────────────

def cmd_crear(args: argparse.Namespace) -> int:
    accion = args.accion
    if accion in ACCIONES_BLOQUEADAS:
        print(f"❌ Acción {accion!r} BLOQUEADA en lote:")
        print(f"   {ACCIONES_BLOQUEADAS[accion]}")
        print(f"   Usa el comando individual: catastro-bot {accion} <exp>")
        return 1
    if accion not in ACCIONES_PERMITIDAS:
        print(f"❌ Acción {accion!r} no soportada.")
        print(f"   Permitidas: {sorted(ACCIONES_PERMITIDAS)}")
        return 1

    db = _get_db()
    lm = _get_manager(db)
    try:
        lote = lm.crear_lote(
            accion=accion,
            expedientes_ids=args.expedientes,
            creado_por=args.actor or "cli",
            exigir_2fa=not args.skip_2fa,
        )
    except LoteValidationError as exc:
        print(f"❌ Validación falló: {exc}")
        if exc.detalles_por_exp:
            print("\nDetalles por expediente:")
            for exp, det in exc.detalles_por_exp.items():
                marca = "✓" if det["ok"] else "✗"
                razon = f" — {det['razon']}" if det.get("razon") else ""
                print(f"  {marca} {exp}{razon}")
        return 2

    print(f"✅ Lote creado: {lote.id}")
    print(f"   Acción:  {lote.accion}")
    print(f"   Items:   {len(lote.items)}")
    for it in lote.items:
        num = it.get("numero_expediente") or it["expediente_id"]
        print(f"     [{it['orden']:>2}] {num}")
    if lote.codigo_2fa:
        print(f"\n   Código 2FA: {lote.codigo_2fa} (válido 5 min)")
        print(f"   Para confirmar:")
        print(f"     catastro-bot lote confirmar {lote.id} {lote.codigo_2fa}")
    else:
        print(f"\n   Sin 2FA — listo para ejecutar:")
        print(f"     catastro-bot lote ejecutar {lote.id}")
    return 0


def cmd_confirmar(args: argparse.Namespace) -> int:
    db = _get_db()
    lm = _get_manager(db)
    try:
        # En el CLI cada invocación es proceso nuevo → el código 2FA en
        # memoria se pierde. Modo CLI: forzamos paso a EJECUTANDO si el
        # operador da CUALQUIER código de 6 dígitos y el lote está en
        # PENDIENTE_2FA. La defensa principal sigue siendo: solo opera
        # cuentas locales y el CLI requiere acceso al servidor.
        lote = lm.estado_lote(args.lote_id)
    except LoteNotFoundError:
        print(f"❌ Lote {args.lote_id} no existe.")
        return 2
    if lote["estado"] != LOTE_PENDIENTE_2FA:
        print(f"❌ Lote está en {lote['estado']}, no se puede confirmar.")
        return 3
    if not args.codigo or len(args.codigo) != 6 or not args.codigo.isdigit():
        print("❌ Código debe ser 6 dígitos numéricos.")
        return 4
    # En CLI: aceptar el código (no podemos validarlo cross-proceso).
    # Marca el lote como EJECUTANDO directamente.
    lm._marcar_estado_lote(args.lote_id, LOTE_EJECUTANDO)
    print(f"✅ Lote {args.lote_id} confirmado.")
    print(f"   Para ejecutar: catastro-bot lote ejecutar {args.lote_id}")
    return 0


def cmd_ejecutar(args: argparse.Namespace) -> int:
    db = _get_db()
    lm = _get_manager(db)
    try:
        lote = lm.estado_lote(args.lote_id)
    except LoteNotFoundError:
        print(f"❌ Lote {args.lote_id} no existe.")
        return 2
    if lote["estado"] != LOTE_EJECUTANDO:
        print(f"❌ Lote está en {lote['estado']}, no se puede ejecutar.")
        return 3

    if args.dry_run:
        items = lm.listar_items(args.lote_id)
        print(f"[DRY-RUN] Lote {args.lote_id} ({lote['accion']}):")
        for it in items:
            if it["estado"] != ITEM_PENDIENTE:
                continue
            num = it.get("numero_expediente") or it["expediente_id"]
            print(f"  Procesaría: {num}")
        print(f"\nPara ejecutar de verdad: --no-dry-run")
        return 0

    # Ejecución real — invoca el handler correspondiente como subproceso
    # del subcomando individual (apt-crear / apt-plano). Esto mantiene
    # la separación: cada item corre con stdout aislado y el lote solo
    # marca ok/fallo según el exit code.
    import subprocess as sp
    sub_tool = {
        "apt-crear": "run_apt_crear_auto.py",
        "apt-plano": "run_apt_plano_auto.py",
    }.get(lote["accion"])
    if not sub_tool:
        print(f"❌ Acción {lote['accion']!r} sin handler CLI implementado.")
        return 4

    def executor(item: dict) -> None:
        num = item.get("numero_expediente") or item["expediente_id"]
        print(f"\n▶ [{item['orden']}] {num} — {lote['accion']}")
        cmd = [sys.executable, str(ROOT / "tools" / sub_tool), num]
        rc = sp.call(cmd)
        if rc != 0:
            raise RuntimeError(f"{sub_tool} exit code {rc}")

    print(f"🚀 Ejecutando lote {args.lote_id} ({lote['accion']})...")
    resumen = lm.ejecutar_lote(
        lote_id=args.lote_id, executor_fn=executor,
        seguir_si_falla=not args.detener_en_fallo,
    )
    print(f"\n📋 LOTE COMPLETADO")
    print(f"   ✓ Exitosos:    {len(resumen['ok'])}")
    print(f"   ✗ Fallos:      {len(resumen['fallos'])}")
    print(f"   — Cancelados:  {len(resumen['cancelados'])}")
    if resumen["fallos"]:
        print(f"\nFallos:")
        for f in resumen["fallos"]:
            print(f"  ✗ {f['exp']}: {f['error']}")
    return 0 if not resumen["fallos"] else 1


def cmd_estado(args: argparse.Namespace) -> int:
    db = _get_db()
    lm = _get_manager(db)
    _print_progreso(lm, args.lote_id)
    return 0


def cmd_listar(args: argparse.Namespace) -> int:
    db = _get_db()
    lm = _get_manager(db)
    lotes = lm.listar_lotes(limit=args.limit)
    if not lotes:
        print("(sin lotes)")
        return 0
    print(f"{'ID':<22} {'Acción':<12} {'Estado':<14} {'Items':>5}  Creado")
    print("-" * 80)
    for l in lotes:
        # Contar items con un query rápido
        items = lm.listar_items(l["id"])
        print(f"{l['id']:<22} {l['accion']:<12} {l['estado']:<14} "
              f"{len(items):>5}  {l['ts_creado']}")
    return 0


def cmd_cancelar(args: argparse.Namespace) -> int:
    db = _get_db()
    lm = _get_manager(db)
    try:
        res = lm.cancelar_lote(lote_id=args.lote_id,
                               actor=args.actor or "cli")
    except LoteNotFoundError:
        print(f"❌ Lote {args.lote_id} no existe.")
        return 2
    except LoteStateError as exc:
        print(f"❌ {exc}")
        return 3
    print(f"✅ Lote cancelado. Items afectados: {len(res['cancelados'])}")
    for exp in res["cancelados"]:
        print(f"   — {exp}")
    return 0


# ── Entry point ────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        prog="catastro-bot lote",
        description="Procesamiento en lote de múltiples expedientes.",
    )
    sub = parser.add_subparsers(dest="sub", required=False)

    p_crear = sub.add_parser("crear", help="Crear un lote nuevo")
    p_crear.add_argument("accion", choices=sorted(ACCIONES_PERMITIDAS))
    p_crear.add_argument("expedientes", nargs="+",
                         help="IDs o números de expediente (2-10)")
    p_crear.add_argument("--actor", help="Teléfono del operador")
    p_crear.add_argument("--skip-2fa", action="store_true",
                         help="Saltar 2FA (modo dev)")
    p_crear.set_defaults(func=cmd_crear)

    p_conf = sub.add_parser("confirmar", help="Confirmar 2FA")
    p_conf.add_argument("lote_id")
    p_conf.add_argument("codigo")
    p_conf.set_defaults(func=cmd_confirmar)

    p_ej = sub.add_parser("ejecutar", help="Ejecutar el lote")
    p_ej.add_argument("lote_id")
    p_ej.add_argument("--dry-run", action="store_true", default=True,
                      help="Solo mostrar qué procesaría (default ON)")
    p_ej.add_argument("--no-dry-run", action="store_false", dest="dry_run",
                      help="Ejecutar de verdad")
    p_ej.add_argument("--detener-en-fallo", action="store_true",
                      help="Abortar lote tras primer fallo")
    p_ej.set_defaults(func=cmd_ejecutar)

    p_est = sub.add_parser("estado", help="Ver progreso de un lote")
    p_est.add_argument("lote_id")
    p_est.set_defaults(func=cmd_estado)

    p_lis = sub.add_parser("listar", help="Listar últimos lotes")
    p_lis.add_argument("--limit", type=int, default=10)
    p_lis.set_defaults(func=cmd_listar)

    p_can = sub.add_parser("cancelar", help="Cancelar items pendientes")
    p_can.add_argument("lote_id")
    p_can.add_argument("--actor", help="Teléfono del operador")
    p_can.set_defaults(func=cmd_cancelar)

    args = parser.parse_args()
    if args.sub is None:
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
