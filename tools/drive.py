"""CLI para gestión de Google Drive del bot — backups automáticos.

USO:
  catastro-bot drive autorizar               # primera vez: autoriza acceso a Drive
  catastro-bot drive subir-backup            # genera backup local + sube a Drive
  catastro-bot drive subir-backup --completo # con archivos (PDFs)
  catastro-bot drive listar                  # ver backups en Drive
  catastro-bot drive activar                 # marca DRIVE_BACKUP_ENABLED=1 en .env
  catastro-bot drive desactivar              # comenta DRIVE_BACKUP_ENABLED
  catastro-bot drive estado                  # ¿está activo? ¿hay tokens?
"""
from __future__ import annotations
import argparse
import io
import os
import sys
from pathlib import Path

os.chdir(r"C:\catastro-bot")
os.environ.setdefault("CATASTRO_BOT_DEV_MODE", "1")
sys.path.insert(0, os.getcwd())
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")


def _get_agent():
    from src.agents.drive_agent import DriveAgent
    from src.core.credential_manager import CredentialManager
    from src.core.database import Database
    creds = CredentialManager()
    db = Database(Path("data/catastro.db"), creds)
    return DriveAgent(db, creds)


def cmd_autorizar(args) -> int:
    """Ejecuta el flujo OAuth — abre browser, te pide confirmar acceso."""
    print()
    print("🔐 Autorizando acceso a Google Drive...")
    print()
    print("   Se abrirá el navegador con la pantalla de Google.")
    print("   Logueate con: topografiahrh@gmail.com")
    print("   Luego click 'Continuar' y 'Permitir'.")
    print()
    print("   Si dice 'Esta app no está verificada':")
    print("     → Click 'Avanzado'")
    print("     → Click 'Ir a catastro-bot-backups (no seguro)'")
    print("     → Permitir todo")
    print()
    input("   Presioná ENTER para abrir el navegador...")

    agent = _get_agent()
    try:
        resultado = agent.authorize()
        print()
        print("=" * 60)
        print(f"  ✅ {resultado}")
        print("=" * 60)
        print()
        print("  Próximo paso: catastro-bot drive subir-backup")
        return 0
    except Exception as e:
        print()
        print(f"❌ Error: {e}")
        return 1


def cmd_subir_backup(args) -> int:
    """Genera backup local + sube a Drive."""
    from src.utils.backup_completo import generar_backup

    print()
    print("📦 Generando backup local...")
    try:
        zip_path = generar_backup(con_archivos=args.completo)
        print(f"   Backup: {zip_path.name} ({zip_path.stat().st_size/(1024*1024):.2f} MB)")
    except Exception as e:
        print(f"❌ Error generando backup: {e}")
        return 1

    print()
    print("☁️  Subiendo a Google Drive...")
    agent = _get_agent()
    res = agent.subir_backup(zip_path)
    if res.get("ok"):
        print(f"   ✅ Subido: {res['nombre']} ({res['size_mb']} MB)")
        print(f"   File ID: {res['file_id']}")
        if res.get("web_link"):
            print(f"   Link:    {res['web_link']}")
        return 0
    else:
        print(f"   ❌ Falló: {res.get('error', 'unknown')}")
        return 2


def cmd_listar(args) -> int:
    """Lista los backups en Drive."""
    agent = _get_agent()
    backups = agent.listar_backups_drive()
    print()
    if not backups:
        print("(no hay backups en Drive)")
        return 0
    print(f"Backups en Drive (catastro-bot/backups/): {len(backups)}")
    print()
    print(f"  {'Archivo':<48} {'Tamaño':<10} {'Subido':<22}")
    print(f"  {'─'*47} {'─'*9} {'─'*21}")
    for b in backups:
        size_mb = int(b.get("size", 0)) / (1024 * 1024)
        fecha = b.get("createdTime", "")[:19]
        print(f"  {b['name'][:48]:<48} {size_mb:>7.2f}MB {fecha:<22}")
    return 0


def cmd_activar(args) -> int:
    """Agrega DRIVE_BACKUP_ENABLED=1 al .env."""
    env_path = Path(".env")
    if not env_path.exists():
        env_path.write_text("", encoding="utf-8")
    content = env_path.read_text(encoding="utf-8")
    lines = content.splitlines()
    new_lines = []
    encontrado = False
    for line in lines:
        if line.strip().startswith("DRIVE_BACKUP_ENABLED"):
            new_lines.append("DRIVE_BACKUP_ENABLED=1")
            encontrado = True
        else:
            new_lines.append(line)
    if not encontrado:
        new_lines.append("")
        new_lines.append("# Backup automatico a Google Drive (cada dia via scheduler)")
        new_lines.append("DRIVE_BACKUP_ENABLED=1")
    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    print("✅ DRIVE_BACKUP_ENABLED=1 en .env")
    print("   Reiniciá el scheduler para que tome el cambio:")
    print("     1. Cerrá la ventana de cmd 'src.main'")
    print("     2. Reabrila: python -m src.main")
    return 0


def cmd_desactivar(args) -> int:
    """Comenta DRIVE_BACKUP_ENABLED en .env."""
    env_path = Path(".env")
    if not env_path.exists():
        print("(.env no existe)")
        return 0
    content = env_path.read_text(encoding="utf-8")
    lines = content.splitlines()
    new_lines = []
    for line in lines:
        if line.strip().startswith("DRIVE_BACKUP_ENABLED"):
            new_lines.append("# " + line)
        else:
            new_lines.append(line)
    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    print("✅ DRIVE_BACKUP_ENABLED comentado en .env")
    return 0


def cmd_estado(args) -> int:
    """Estado del Drive backup."""
    from src.core.credential_manager import CredentialManager
    cm = CredentialManager()
    print()
    print("Estado Drive Backup:")
    print()

    # Tokens OAuth
    try:
        tk = cm.get_drive_token()
        print(f"  🔐 Tokens OAuth:        ✅ configurados")
        print(f"     scopes:              {tk.get('scopes', [])}")
        print(f"     client_id:           {tk.get('client_id', '')[:30]}...")
    except Exception as e:
        print(f"  🔐 Tokens OAuth:        ❌ NO autorizados — corré: catastro-bot drive autorizar")

    # client_secret
    try:
        oauth = cm.get_google_oauth()
        print(f"  📋 client_secret JSON:  ✅ cargado (len {len(oauth)})")
    except Exception:
        print(f"  📋 client_secret JSON:  ❌ FALTA en Credential Manager")

    # Flag .env
    env_path = Path(".env")
    drive_enabled = False
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line_clean = line.strip()
            if line_clean.startswith("DRIVE_BACKUP_ENABLED="):
                drive_enabled = line_clean.endswith("=1")
                break
    print(f"  🤖 Auto-backup activo:  {'✅ SÍ (scheduler subirá diariamente)' if drive_enabled else '❌ NO (corré: catastro-bot drive activar)'}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="catastro-bot drive", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="sub")

    p_aut = sub.add_parser("autorizar", help="Autorizar acceso a Drive (1ra vez)")
    p_aut.set_defaults(func=cmd_autorizar)

    p_sub = sub.add_parser("subir-backup", help="Generar backup local + subir a Drive")
    p_sub.add_argument("--completo", action="store_true",
                        help="Incluir data/files/ (PDFs — backup pesado)")
    p_sub.set_defaults(func=cmd_subir_backup)

    p_lis = sub.add_parser("listar", help="Listar backups en Drive")
    p_lis.set_defaults(func=cmd_listar)

    p_act = sub.add_parser("activar", help="Activar backup automático diario")
    p_act.set_defaults(func=cmd_activar)

    p_des = sub.add_parser("desactivar", help="Desactivar backup automático")
    p_des.set_defaults(func=cmd_desactivar)

    p_est = sub.add_parser("estado", help="Estado actual del Drive backup")
    p_est.set_defaults(func=cmd_estado)

    args = parser.parse_args()
    if not args.sub:
        parser.print_help()
        return cmd_estado(args)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
