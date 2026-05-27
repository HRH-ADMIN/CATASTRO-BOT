"""CLI unificado del bot — `catastro-bot <subcomando>`.

Reemplaza la dispersión de `tools/*.py` con un solo entry point que enruta
a los handlers específicos. Cada subcomando es delgado: importa el módulo
correspondiente y delega.

USO:
  catastro-bot crear RDF-2026-005 rectificacion --provincia ALAJUELA ...
  catastro-bot extraer RDF-2026-005 --save
  catastro-bot dashboard
  catastro-bot listar
  catastro-bot listar RDF-2026-005
  catastro-bot backup
  catastro-bot apt-crear RDF-2026-005
  catastro-bot apt-plano RDF-2026-005
  catastro-bot apt-guardar RDF-2026-005
  catastro-bot health
  catastro-bot enviar-digest

Para verlos todos:
  catastro-bot --help
  catastro-bot <subcomando> --help
"""
from __future__ import annotations
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("CATASTRO_BOT_DEV_MODE", "1")
# Los handlers internos (health, enviar-digest) importan src.* directamente —
# no van por subprocess. Asegurar que ROOT esté en sys.path desde el inicio.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)


def _ejecutar_tool(nombre_archivo: str, extra_args: list[str]) -> int:
    """Lanza un tool como subprocess para aislar el contexto.

    Cada tool de `tools/*.py` hace su propio `sys.stdout = ...` y otros
    setups module-level. Importarlos directamente desde aquí pollute el
    estado. Subprocess los mantiene independientes (~50ms de overhead).
    """
    tool_path = ROOT / "tools" / nombre_archivo
    if not tool_path.exists():
        print(f"[ERROR] {nombre_archivo} no existe")
        return 1
    cmd = [sys.executable, str(tool_path)] + extra_args
    return subprocess.call(cmd)


# ─── Subcomandos ──────────────────────────────────────────────────────

def _cmd_crear(extra_args: list[str]) -> int:
    return _ejecutar_tool("crear_expediente_test.py", extra_args)


def _cmd_extraer(extra_args: list[str]) -> int:
    return _ejecutar_tool("extraer_datos_apt.py", extra_args)


def _cmd_listar(extra_args: list[str]) -> int:
    return _ejecutar_tool("listar_planos.py", extra_args)


def _cmd_dashboard(extra_args: list[str]) -> int:
    return _ejecutar_tool("dashboard.py", extra_args)


# Nota: el handler `_cmd_backup` está más abajo (línea ~119) y apunta a
# `src.utils.backup_completo`, que es el flujo completo (BD + .env + config +
# código + LEEME). Hubo una definición previa apuntando a `backup_db.py` que
# quedaba sobrescrita silenciosamente — eliminada en housekeeping 2026-05-22
# (ver PLAN_MEJORAS_catastro-bot_3.md sección "Bugs varios documentados", #1).


def _cmd_apt_crear(extra_args: list[str]) -> int:
    return _ejecutar_tool("run_apt_crear_auto.py", extra_args)


def _cmd_apt_plano(extra_args: list[str]) -> int:
    return _ejecutar_tool("run_apt_plano_auto.py", extra_args)


def _cmd_apt_guardar(extra_args: list[str]) -> int:
    return _ejecutar_tool("guardar_contrato_apt.py", extra_args)


def _cmd_apt_r2(extra_args: list[str]) -> int:
    return _ejecutar_tool("run_apt_r2.py", extra_args)


def _cmd_apt_enviar(extra_args: list[str]) -> int:
    return _ejecutar_tool("apt_enviar.py", extra_args)


def _cmd_apt_flujo(extra_args: list[str]) -> int:
    return _ejecutar_tool("apt_flujo.py", extra_args)


def _cmd_resumen(extra_args: list[str]) -> int:
    return _ejecutar_tool("resumen.py", extra_args)


def _cmd_dashboard_web(extra_args: list[str]) -> int:
    """Arranca el dashboard web (http://localhost:9224)."""
    import subprocess
    cmd = [sys.executable, "-m", "src.utils.dashboard_web"] + extra_args
    return subprocess.call(cmd)


def _cmd_apt_sync(extra_args: list[str]) -> int:
    return _ejecutar_tool("apt_sync.py", extra_args)


def _cmd_config(extra_args: list[str]) -> int:
    return _ejecutar_tool("config.py", extra_args)


def _cmd_backup(extra_args: list[str]) -> int:
    """Backup completo del bot (BD + config + archivos + reglas → zip)."""
    import subprocess
    cmd = [sys.executable, "-m", "src.utils.backup_completo"] + extra_args
    return subprocess.call(cmd)


def _cmd_drive(extra_args: list[str]) -> int:
    return _ejecutar_tool("drive.py", extra_args)


def _cmd_esquema(extra_args: list[str]) -> int:
    return _ejecutar_tool("generar_esquema_pdf.py", extra_args)


def _cmd_abrir_dashboard(extra_args: list[str]) -> int:
    """Abre el dashboard en el navegador por defecto. Si no está corriendo,
    lo arranca también."""
    import subprocess, time, urllib.request, webbrowser
    url = "http://localhost:9224/"
    # ¿está vivo?
    try:
        urllib.request.urlopen(url, timeout=1.5)
        webbrowser.open(url)
        print(f"[OK] Dashboard ya está corriendo. Abierto en navegador: {url}")
        return 0
    except Exception:
        pass
    # No está vivo — arrancarlo en background
    print(f"[INFO] Dashboard no estaba corriendo. Arrancándolo...")
    cmd = [sys.executable, "-m", "src.utils.dashboard_web", "--port", "9224"]
    subprocess.Popen(
        cmd,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) |
                      getattr(subprocess, "DETACHED_PROCESS", 0),
        close_fds=True,
    )
    # Esperar a que responda
    for _ in range(15):
        try:
            urllib.request.urlopen(url, timeout=1.0)
            webbrowser.open(url)
            print(f"[OK] Dashboard arrancado y abierto: {url}")
            return 0
        except Exception:
            time.sleep(0.5)
    print(f"[WARN] Dashboard no respondió en 7.5s, intentá manualmente: {url}")
    return 1


def _cmd_lote(extra_args: list[str]) -> int:
    return _ejecutar_tool("lote.py", extra_args)


def _cmd_reglas(extra_args: list[str]) -> int:
    return _ejecutar_tool("reglas.py", extra_args)


def _cmd_muni(extra_args: list[str]) -> int:
    return _ejecutar_tool("muni.py", extra_args)


def _cmd_debug(extra_args: list[str]) -> int:
    return _ejecutar_tool("debug.py", extra_args)


def _cmd_health(extra_args: list[str]) -> int:
    """Imprime el estado del sistema."""
    import json
    from src.core.credential_manager import CredentialManager
    from src.core.database import Database
    from src.utils.healthcheck import verificar_salud
    creds = CredentialManager()
    db = Database(Path("data/catastro.db"), creds)
    try:
        db.initialize_schema()
    except Exception:
        pass
    salud = verificar_salud(db)
    print(json.dumps(salud, ensure_ascii=False, indent=2, default=str))
    return 0 if salud["status"] != "error" else 1


def _cmd_install_shortcut(extra_args: list[str]) -> int:
    """Instala (idempotentemente) un shortcut al dashboard en el Escritorio.

    Delega en tools/install_desktop_shortcut.ps1. Re-ejecutar es seguro:
    sobreescribe sin error. El icono customizado en assets/icon.ico se usa
    automáticamente si existe.

    Plan: PLAN_MEJORAS Sprint 1 / U-01.
    """
    import subprocess
    ps_script = ROOT / "tools" / "install_desktop_shortcut.ps1"
    if not ps_script.exists():
        print(f"[ERROR] {ps_script} no existe")
        return 1
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", str(ps_script),
    ] + extra_args
    return subprocess.call(cmd)


def _cmd_enviar_digest(extra_args: list[str]) -> int:
    """Dispara el digest semanal por email."""
    from src.core.credential_manager import CredentialManager
    from src.core.database import Database
    from src.utils.email_digest import enviar_digest_semanal
    creds = CredentialManager()
    db = Database(Path("data/catastro.db"), creds)
    try:
        db.initialize_schema()
    except Exception:
        pass
    res = enviar_digest_semanal(db, creds)
    print(f"Enviados: {res['enviados']}")
    if res.get("destinatarios"):
        for d in res["destinatarios"]:
            print(f"  ✓ {d}")
    if res.get("errores"):
        print("\nErrores:")
        for e in res["errores"]:
            print(f"  ✗ {e}")
    return 0 if res["enviados"] > 0 or not res["errores"] else 1


# ─── Router ───────────────────────────────────────────────────────────

SUBCOMANDOS: dict[str, tuple] = {
    # nombre: (handler, descripción corta)
    "crear":         (_cmd_crear,         "Crear expediente + carpetas"),
    "extraer":       (_cmd_extraer,       "Extraer datos del plano (Vision)"),
    "listar":        (_cmd_listar,        "Listar planos / ver detalle"),
    "dashboard":     (_cmd_dashboard,     "Dashboard de métricas operativas"),
    "backup":        (_cmd_backup,        "Backup completo del bot (--con-archivos --listar --purgar N)"),
    "config":        (_cmd_config,        "Gestionar credenciales (list / set / check / rm)"),
    "drive":         (_cmd_drive,         "Backup Drive (autorizar / subir-backup / listar / estado)"),
    "esquema":       (_cmd_esquema,       "Generar PDF con esquema lineal del bot"),
    "apt-crear":     (_cmd_apt_crear,     "Llenar contrato APT (paso a paso)"),
    "apt-plano":     (_cmd_apt_plano,     "Llenar plano APT (bP1-bP7)"),
    "apt-guardar":   (_cmd_apt_guardar,   "Click GUARDAR del contrato"),
    "apt-r2":        (_cmd_apt_r2,        "Subir archivos APT R2 (anverso corregido + visado muni)"),
    "apt-enviar":    (_cmd_apt_enviar,    "Click ENVIAR AL CFIA del plano (no requiere FD)"),
    "apt-flujo":     (_cmd_apt_flujo,     "TODO de corrido (crear+guardar+plano), pausa antes de enviar"),
    "resumen":       (_cmd_resumen,       "Resumen rápido de todos los planos y su estado"),
    "dashboard-web": (_cmd_dashboard_web, "Dashboard web bonito en http://localhost:9224 (auto-refresh)"),
    "apt-sync":      (_cmd_apt_sync,      "Consultar APT por estado real de cada trámite (vía CDP)"),
    "abrir":         (_cmd_abrir_dashboard, "Abrir dashboard en navegador (arranca si no está)"),
    "lote":          (_cmd_lote,          "Procesar varios expedientes en cola"),
    "reglas":        (_cmd_reglas,        "Consultar reglas operativas aprendidas"),
    "muni":          (_cmd_muni,          "Flujo municipal (URL Google Form, etc.)"),
    "debug":         (_cmd_debug,         "Inspección del bot (bp6/bp7/ddl/estado/chrome)"),
    "health":        (_cmd_health,        "Estado del sistema (JSON)"),
    "enviar-digest": (_cmd_enviar_digest, "Enviar digest semanal por email"),
    "install-shortcut": (_cmd_install_shortcut, "Crear shortcut del dashboard en el Escritorio"),
}


def main() -> int:
    if len(sys.argv) < 2:
        _imprimir_ayuda()
        return 0
    sub = sys.argv[1]
    if sub in ("-h", "--help", "help"):
        _imprimir_ayuda()
        return 0
    if sub not in SUBCOMANDOS:
        print(f"[ERROR] Subcomando desconocido: {sub!r}")
        print()
        _imprimir_ayuda()
        return 1
    handler, _ = SUBCOMANDOS[sub]
    return handler(sys.argv[2:])


def _imprimir_ayuda() -> None:
    print("catastro-bot — CLI unificado")
    print("Uso: catastro-bot <subcomando> [args...]")
    print()
    print("Subcomandos:")
    ancho = max(len(n) for n in SUBCOMANDOS)
    for nombre, (_, desc) in SUBCOMANDOS.items():
        print(f"  {nombre:<{ancho}}  {desc}")
    print()
    print("Ayuda específica:")
    print("  catastro-bot <subcomando> --help")


if __name__ == "__main__":
    sys.exit(main())
