"""Launcher robusto de Chrome con CDP para el bot APT.

Maneja el problema clásico de Chrome en Windows: cuando hay procesos chrome.exe
en background (services, updaters, helpers), un nuevo `chrome.exe --remote-debugging-port=9222`
se enruta a la instancia existente y los flags se ignoran. Este script:

  1. Detecta y mata TODOS los procesos chrome.exe (con reintentos por respawn).
  2. Lanza Chrome dedicado con perfil aislado y debug port.
  3. Verifica el puerto polleando hasta 20s.

USO:
  .venv\\Scripts\\python tools\\start_chrome_bot.py
  .venv\\Scripts\\python tools\\start_chrome_bot.py --no-kill   # no mata procesos existentes (más rápido pero puede fallar)
"""
from __future__ import annotations
import io
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


CDP_URL       = "http://localhost:9222/json/version"
BOT_PROFILE   = Path(r"C:\catastro-bot\data\temp\chrome_profile_apt")
CHROME_PATHS  = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
]
APT_HOME      = "https://apt.cfia.or.cr/APT2/Home"


def find_chrome() -> str:
    for p in CHROME_PATHS:
        if Path(p).exists():
            return p
    return ""


def kill_chrome_del_bot(profile_dir: Path, rounds: int = 2) -> int:
    """Mata SOLO los procesos chrome.exe que corren con el perfil del bot.

    NO toca los chrome.exe del navegador personal del usuario (otros perfiles).
    Identifica los procesos del bot por:
      1. CommandLine contiene `--user-data-dir=<profile_dir>`
      2. CommandLine contiene `--remote-debugging-port=9222`

    Si ninguno de los Chrome del usuario tiene ese flag/path, solo muere
    el del bot. Bug corregido 2026-05-15 — antes mataba TODOS los chrome.exe.
    """
    profile_str = str(profile_dir).replace("\\", "\\\\")  # escapar para WMI

    total = 0
    for r in range(rounds):
        # PowerShell: encuentra procesos chrome.exe que matchean el perfil del bot
        ps_cmd = (
            f"Get-WmiObject Win32_Process | "
            f"Where-Object {{ "
            f"  $_.Name -eq 'chrome.exe' -and "
            f"  ($_.CommandLine -like '*{profile_str}*' -or "
            f"   $_.CommandLine -like '*remote-debugging-port=9222*') "
            f"}} | "
            f"ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force; "
            f"Write-Output \"matado $($_.ProcessId)\" }}"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True, text=True,
        )
        n = result.stdout.count("matado")
        total += n
        if n > 0:
            print(f"  Ronda {r+1}: matados {n} procesos del bot")
        time.sleep(1.0)

    # Verificar cuántos chrome.exe quedan (TOTAL — incluye personales del usuario)
    check = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq chrome.exe"],
        capture_output=True, text=True,
    )
    quedan_total = check.stdout.lower().count("chrome.exe")
    print(f"  Procesos chrome.exe restantes (todos los usuarios): {quedan_total}")
    print(f"  → Esos son tus Chrome personales — NO se tocaron ✅")
    return total


# Alias retro-compatible (otros tools pueden llamar kill_chrome)
def kill_chrome(rounds: int = 3) -> int:
    """DEPRECATED — usa kill_chrome_del_bot(). Mantenido por compatibilidad."""
    return kill_chrome_del_bot(BOT_PROFILE, rounds=rounds)


def launch_chrome(chrome_exe: str, profile_dir: Path) -> subprocess.Popen:
    profile_dir.mkdir(parents=True, exist_ok=True)
    args = [
        chrome_exe,
        "--remote-debugging-port=9222",
        # Bind CDP solo a localhost — sin esto, cualquier proceso en la
        # red local podría conectarse al CDP y controlar Chrome del bot.
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=ProfilePicker,UserDataSnapshot",
        # HOTFIX 2026-05-22: flags para evitar ventanas de control extra.
        # --no-startup-window suprime la ventana inicial cuando Chrome ya
        # tenía pestañas en el perfil (sino abre una en blanco además).
        # --disable-session-crashed-bubble evita el popup "Restaurar"
        # cuando el proceso anterior fue forzado.
        "--disable-session-crashed-bubble",
        "--disable-infobars",
        # Antes había --new-window acá: forzaba siempre ventana adicional
        # aunque Chrome ya tuviera el perfil abierto. Removido.
        APT_HOME,
    ]
    print(f"  Comando: {chrome_exe} --remote-debugging-port=9222 --remote-debugging-address=127.0.0.1 --user-data-dir={profile_dir} ...")
    # DETACHED_PROCESS = 0x00000008 (Windows) — Chrome corre independiente del terminal
    creation_flags = 0x00000008 if sys.platform == "win32" else 0
    return subprocess.Popen(args, creationflags=creation_flags)


def wait_cdp(timeout: int = 20) -> bool:
    print(f"  Polling http://localhost:9222/json/version (hasta {timeout}s)...")
    for i in range(1, timeout + 1):
        try:
            with urllib.request.urlopen(CDP_URL, timeout=1) as r:
                if r.status == 200:
                    print(f"  ✅ CDP activo (tras {i}s)")
                    return True
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            pass
        time.sleep(1)
    print(f"  ❌ Puerto 9222 no respondió en {timeout}s")
    return False


def _cdp_ya_responde() -> bool:
    """¿El puerto 9222 ya tiene Chrome respondiendo? Si sí, no hay que
    relanzar nada. Idempotente — evita el spam si el operador clickea
    'Encender Chrome' varias veces seguidas desde el dashboard."""
    try:
        with urllib.request.urlopen(CDP_URL, timeout=1) as r:
            return r.status == 200
    except (urllib.error.URLError, ConnectionError, TimeoutError):
        return False


def main() -> int:
    no_kill = "--no-kill" in sys.argv
    force = "--force" in sys.argv  # ignora chequeo idempotente

    print("═" * 60)
    print("  catastro-bot — launcher Chrome con CDP")
    print("═" * 60)

    # HOTFIX 2026-05-22: idempotencia. Si Chrome del bot ya responde en
    # CDP, no relanzamos nada — evita acumulación de ventanas cuando el
    # operador clickea "Encender Chrome" varias veces seguidas.
    if not force and _cdp_ya_responde():
        print("\n[OK] Chrome del bot ya está vivo en CDP 9222.")
        print("    No se relanza nada (--force para forzar relanzo).")
        return 0

    chrome = find_chrome()
    if not chrome:
        print("[ERROR] No se encontró chrome.exe instalado.")
        return 1
    print(f"\n[1] Chrome encontrado: {chrome}")
    print(f"    Perfil dedicado: {BOT_PROFILE}")

    if not no_kill:
        print("\n[2] Cerrando SOLO los Chrome del bot (perfil dedicado)...")
        print(f"    No toca tus Chrome personales con otros perfiles.")
        kill_chrome_del_bot(BOT_PROFILE, rounds=2)
    else:
        print("\n[2] Saltado --no-kill — no se cerraron procesos existentes")

    print("\n[3] Lanzando Chrome dedicado...")
    launch_chrome(chrome, BOT_PROFILE)

    print("\n[4] Verificando CDP...")
    ok = wait_cdp(timeout=20)
    if not ok:
        print("\n[FAIL] Chrome arrancó pero el debug port no abre.")
        print("       Posibles causas:")
        print("        - Antivirus/Windows Defender bloqueando el puerto")
        print("        - Otro proceso usa puerto 9222 — verifique con:  netstat -ano | findstr 9222")
        print("        - Política de grupo deshabilita debug remoto")
        return 1

    print()
    print("═" * 60)
    print("  ✅ LISTO")
    print("  Haga Firma Digital BCR en la ventana de Chrome que se abrió.")
    print("  Cuando vea el portal APT con su nombre arriba, ejecute:")
    print()
    print("    .venv\\Scripts\\python tools\\run_apt_crear_pasos.py SEG-2026-001")
    print("═" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
