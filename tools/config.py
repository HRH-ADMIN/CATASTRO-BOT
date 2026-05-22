"""Gestión de credenciales y configuración del bot.

CLI seguro para listar, verificar y actualizar credenciales sin tener que
escribir comandos largos de CredentialManager.

USO:
  catastro-bot config list           # ver qué credenciales están configuradas (sin mostrar valores)
  catastro-bot config check          # verifica que cada credencial sea válida
  catastro-bot config set <key>      # actualizar una credencial (te pregunta el valor en stdin)
  catastro-bot config rm <key>       # eliminar una credencial

Credenciales soportadas:
  anthropic-api          → API key de Anthropic (sk-ant-...)
  green-api              → Green API instance_id + token (WhatsApp)
  muni-san-ramon         → email Gmail + App Password (IMAP)
  apt-cfia               → usuario + password APT
  email-bot              → email Hotmail/Office365 del bot
  drive-oauth            → tokens OAuth de Google Drive
  rnp-digital            → RNP Digital (raramente usado, pago manual)
  admin-phone            → WhatsApp del admin
"""
from __future__ import annotations
import argparse
import getpass
import io
import os
import sys
from pathlib import Path

os.chdir(r"C:\catastro-bot")
os.environ.setdefault("CATASTRO_BOT_DEV_MODE", "1")
sys.path.insert(0, os.getcwd())
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")


# Mapeo: clave-cli → (descripción humana, setter, getter)
# El setter recibe `(cm, valor_o_tupla)` y guarda en CredMgr.
# El getter recibe `(cm)` y devuelve "OK" / "FALTA: <razón>"

def _set_anthropic(cm, val: str):
    cm.set_secret("anthropic-api", val)


def _check_anthropic(cm) -> str:
    try:
        k = cm.get_anthropic_key()
        if k and k.startswith("sk-ant-"):
            return f"OK (len={len(k)}, prefix=sk-ant-...{k[-4:]})"
        return f"BAD FORMAT (esperaba sk-ant-..., len={len(k or '')})"
    except Exception as e:
        return f"FALTA ({str(e)[:60]})"


def _set_green_api(cm, vals: tuple[str, str]):
    instance_id, token = vals
    cm.set_secret("green-api-instance", instance_id)
    cm.set_secret("green-api-token", token)


def _check_green_api(cm) -> str:
    try:
        data = cm.get_green_api()
        iid = str(data.get("instance_id", ""))
        tok = str(data.get("token", ""))
        if iid and tok:
            return f"OK (instance={iid[:8]}..., token len={len(tok)})"
        return "INCOMPLETO"
    except Exception as e:
        return f"FALTA ({str(e)[:60]})"


def _set_muni(cm, vals: tuple[str, str]):
    user, pw = vals
    cm.set_muni_san_ramon(user, pw)


def _check_muni(cm) -> str:
    try:
        user, pw = cm.get_muni_san_ramon()
        if "@" in user and len(pw) >= 10:
            return f"OK ({user[:30]}..., pw len={len(pw)})"
        return f"BAD FORMAT (user={user!r}, pw_len={len(pw) if pw else 0})"
    except Exception as e:
        return f"FALTA ({str(e)[:60]})"


def _set_apt_cfia(cm, vals: tuple[str, str]):
    user, pw = vals
    cm.set_secret("apt-cfia-user", user)
    cm.set_secret("apt-cfia-pass", pw)


def _check_apt_cfia(cm) -> str:
    try:
        user = cm.get_secret("apt-cfia-user")
        pw = cm.get_secret("apt-cfia-pass")
        if user and pw:
            return f"OK (user={user[:30]}, pw len={len(pw)})"
        return "INCOMPLETO"
    except Exception as e:
        return f"FALTA ({str(e)[:60]})"


def _set_email_bot(cm, vals: tuple[str, str]):
    user, pw = vals
    cm.set_secret("email-bot-user", user)
    cm.set_secret("email-bot-pass", pw)


def _check_email_bot(cm) -> str:
    try:
        user = cm.get_secret("email-bot-user")
        pw = cm.get_secret("email-bot-pass")
        if user and pw:
            return f"OK ({user[:30]}, pw len={len(pw)})"
        return "INCOMPLETO"
    except Exception as e:
        return f"FALTA ({str(e)[:60]})"


CREDS = {
    "anthropic-api": {
        "desc":   "API key de Anthropic (sk-ant-...)",
        "set":    _set_anthropic,
        "check":  _check_anthropic,
        "prompt": "API key Anthropic (sk-ant-...): ",
        "secret": True,  # no echo en input
    },
    "green-api": {
        "desc":   "Green API — WhatsApp (instance_id + token)",
        "set":    _set_green_api,
        "check":  _check_green_api,
        "prompt_multi": [
            ("Instance ID Green API: ", False),
            ("Token Green API: ",         True),
        ],
    },
    "muni-san-ramon": {
        "desc":   "Gmail IMAP muni (email + App Password)",
        "set":    _set_muni,
        "check":  _check_muni,
        "prompt_multi": [
            ("Email Gmail (ej topografiahrh@gmail.com): ", False),
            ("App Password Gmail (16 chars):              ", True),
        ],
    },
    "apt-cfia": {
        "desc":   "APT CFIA (usuario + password del topógrafo)",
        "set":    _set_apt_cfia,
        "check":  _check_apt_cfia,
        "prompt_multi": [
            ("Usuario APT CFIA:  ", False),
            ("Password APT CFIA: ", True),
        ],
    },
    "email-bot": {
        "desc":   "Email bot (SMTP Hotmail/Office365) — para enviar reportes",
        "set":    _set_email_bot,
        "check":  _check_email_bot,
        "prompt_multi": [
            ("Email del bot (Hotmail): ", False),
            ("App Password:            ", True),
        ],
    },
}


# ── Comandos CLI ────────────────────────────────────────────────────────

def cmd_list(args) -> int:
    """Lista las credenciales configuradas (sin mostrar valores)."""
    from src.core.credential_manager import CredentialManager
    cm = CredentialManager()
    print()
    print(f"  {'Credencial':<22} {'Descripción':<48} Estado")
    print(f"  {'─'*22} {'─'*48} {'─'*40}")
    for key, info in CREDS.items():
        estado = info["check"](cm)
        # Color emoji por estado
        if estado.startswith("OK"):
            icon = "✅"
        elif "FALTA" in estado:
            icon = "❌"
        else:
            icon = "⚠️"
        print(f"  {key:<22} {info['desc'][:48]:<48} {icon} {estado[:38]}")
    print()
    return 0


def cmd_check(args) -> int:
    """Verifica que cada credencial sea utilizable (intentar conectar/login)."""
    # Por ahora alias de list — futuro: hace conexiones reales
    return cmd_list(args)


def cmd_set(args) -> int:
    """Actualiza una credencial. Pregunta valores por stdin (sin echo si es secreto)."""
    from src.core.credential_manager import CredentialManager
    cm = CredentialManager()

    key = args.key
    if key not in CREDS:
        print(f"❌ Clave desconocida: {key!r}")
        print(f"   Disponibles: {', '.join(CREDS.keys())}")
        return 1

    info = CREDS[key]
    print(f"\n📝 Actualizar: {info['desc']}\n")

    # Single-value
    if "prompt" in info:
        val = (
            getpass.getpass(info["prompt"])
            if info.get("secret", False)
            else input(info["prompt"])
        ).strip()
        if not val:
            print("(cancelado — valor vacío)")
            return 0
        info["set"](cm, val)
        print(f"\n✅ Guardado: {key}")
        print(f"   Estado: {info['check'](cm)}")
        return 0

    # Multi-value (tupla)
    valores = []
    for prompt_text, es_secreto in info["prompt_multi"]:
        v = (
            getpass.getpass(prompt_text)
            if es_secreto
            else input(prompt_text)
        ).strip()
        if not v:
            print("(cancelado — valor vacío)")
            return 0
        # Limpiar espacios típicos de App Passwords
        if es_secreto and len(v) > 10:
            v = v.replace(" ", "")
        valores.append(v)

    info["set"](cm, tuple(valores))
    print(f"\n✅ Guardado: {key}")
    print(f"   Estado: {info['check'](cm)}")
    return 0


def cmd_rm(args) -> int:
    """Elimina una credencial."""
    from src.core.credential_manager import CredentialManager
    cm = CredentialManager()
    key = args.key
    if key not in CREDS:
        print(f"❌ Clave desconocida: {key!r}")
        return 1
    print(f"\n⚠️  Vas a ELIMINAR la credencial: {key}")
    print(f"   {CREDS[key]['desc']}")
    resp = input("\n¿Confirmar? Escribí 'BORRAR': ").strip()
    if resp != "BORRAR":
        print("(cancelado)")
        return 0
    # Borrar — depende de la credencial
    try:
        if key == "anthropic-api":
            cm.delete_secret("anthropic-api")
        elif key == "green-api":
            cm.delete_secret("green-api-instance")
            cm.delete_secret("green-api-token")
        elif key == "muni-san-ramon":
            cm.delete_secret("muni-san-ramon-user")
            cm.delete_secret("muni-san-ramon-pass")
        elif key == "apt-cfia":
            cm.delete_secret("apt-cfia-user")
            cm.delete_secret("apt-cfia-pass")
        elif key == "email-bot":
            cm.delete_secret("email-bot-user")
            cm.delete_secret("email-bot-pass")
        print(f"\n✅ {key} eliminado")
        return 0
    except Exception as e:
        print(f"❌ Error: {e}")
        return 2


def main() -> int:
    parser = argparse.ArgumentParser(prog="catastro-bot config",
                                      description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="sub")

    p_list = sub.add_parser("list", help="Listar credenciales configuradas")
    p_list.set_defaults(func=cmd_list)

    p_check = sub.add_parser("check", help="Verificar credenciales")
    p_check.set_defaults(func=cmd_check)

    p_set = sub.add_parser("set", help="Actualizar una credencial")
    p_set.add_argument("key", help=f"Cuál: {', '.join(CREDS.keys())}")
    p_set.set_defaults(func=cmd_set)

    p_rm = sub.add_parser("rm", help="Eliminar una credencial")
    p_rm.add_argument("key", help=f"Cuál: {', '.join(CREDS.keys())}")
    p_rm.set_defaults(func=cmd_rm)

    args = parser.parse_args()
    if not args.sub:
        parser.print_help()
        print()
        return cmd_list(args)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
