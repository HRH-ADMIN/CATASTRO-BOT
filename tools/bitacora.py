"""catastro-bot bitacora [--fecha YYYY-MM-DD] [--guardar] [--enviar] [--formato md|text|json]

Genera la bitácora del día ad-hoc (sin esperar al job de las 19:00 CR).

USO:
  catastro-bot bitacora                              # imprime markdown del día CR
  catastro-bot bitacora --fecha 2026-05-22           # un día específico
  catastro-bot bitacora --formato text               # versión compacta WhatsApp
  catastro-bot bitacora --guardar                    # persiste en docs/bitacoras/
  catastro-bot bitacora --enviar                     # envía por email + guarda
  catastro-bot bitacora --fecha 2026-05-22 --guardar --enviar

Plan: PLAN_MEJORAS Sprint 5 / N-09 sub-paso D.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Insertar ROOT al inicio de sys.path para que `config` y `src` se resuelvan
# como paquetes del proyecto (no como pip packages del venv que puedan
# coincidir con esos nombres).
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
os.environ.setdefault("CATASTRO_BOT_DEV_MODE", "1")

# UTF-8 en stdout para emojis en consola Windows
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                   errors="replace")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="catastro-bot bitacora",
        description="Generar bitácora diaria del bot.",
    )
    parser.add_argument("--fecha", help="YYYY-MM-DD (default: hoy CR)")
    parser.add_argument(
        "--formato", choices=("md", "text", "json"), default="md",
        help="Formato de salida (default: md)",
    )
    parser.add_argument("--guardar", action="store_true",
                        help="Persistir en docs/bitacoras/<fecha>.md")
    parser.add_argument("--enviar", action="store_true",
                        help="Enviar por email al operador "
                             "(usa credenciales muni-san-ramon)")
    parser.add_argument("--quiet", action="store_true",
                        help="No imprimir el contenido a stdout")
    args = parser.parse_args(argv)

    from config.settings import DATABASE_PATH
    from src.core.credential_manager import CredentialManager
    from src.utils import daily_log

    fecha = args.fecha or daily_log._fecha_default()
    print(f"[bitacora] Recopilando datos del {fecha}...", file=sys.stderr)

    data = daily_log.recopilar(DATABASE_PATH, fecha)

    if args.formato == "md":
        out = daily_log.formatear_markdown(data)
    elif args.formato == "text":
        out = daily_log.formatear_texto(data)
    else:
        out = json.dumps(data, ensure_ascii=False, indent=2, default=str)

    if not args.quiet:
        print(out)

    if args.guardar:
        md = daily_log.formatear_markdown(data)
        path = daily_log.guardar_bitacora(ROOT, fecha, md)
        print(f"[bitacora] Guardada: {path}", file=sys.stderr)

    if args.enviar:
        try:
            from src.utils.email_digest import enviar_email_smtp
            creds = CredentialManager()
            user, password = creds.get_muni_san_ramon()
            html = daily_log.formatear_html(data)
            md = daily_log.formatear_markdown(data)
            ok = enviar_email_smtp(
                from_addr=user, password=password, to=user,
                subject=f"[catastro-bot] Bitácora — {fecha}",
                body_text=md, body_html=html,
            )
            if ok:
                print(f"[bitacora] Enviado por email a {user}", file=sys.stderr)
            else:
                print("[bitacora] Email no salió (ver logs)", file=sys.stderr)
                return 1
        except Exception as exc:
            print(f"[bitacora] Error enviando email: {exc}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
