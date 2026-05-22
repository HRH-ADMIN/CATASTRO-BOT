"""Notificaciones emergentes en el escritorio del operador (Windows).

Dos modos:
  1. **Centro de pantalla (urgencia=alta)** — MessageBox modal en el centro,
     visible y obligando atención. Para ANOMALÍAS donde el bot detuvo el
     ciclo y necesita intervención del operador.
  2. **Toast esquina inferior (urgencia=normal/baja)** — winotify, menos
     intrusivo. Para avisos informativos.

Fallback chain para urgencia=alta:
  1. MessageBox vía ctypes (centro, modal, system-wide topmost)
  2. PowerShell .NET Forms MessageBox (mismo efecto)
  3. winotify (toast — menos visible pero al menos algo)

Para urgencia=normal:
  1. winotify
  2. PowerShell BalloonTip
  3. nada

NO bloquea el hilo principal — la MessageBox corre en un thread daemon.

USO:
    from src.utils.desktop_notify import notificar_escritorio
    notificar_escritorio(
        titulo="catastro-bot — ANOMALÍA",
        mensaje="bC5 falló validación: canton sin distrito.\\nRDF-2026-003",
        urgencia="alta",   # centro de pantalla
    )
"""
from __future__ import annotations
import logging
import shutil
import subprocess
import threading
from typing import Literal

_log = logging.getLogger("catastro.desktop_notify")

URGENCIA_DURACION = {"alta": "long", "normal": "short", "baja": "short"}


def _notificar_via_winotify(titulo: str, mensaje: str, duracion: str = "short") -> bool:
    """Toast nativo Windows 10/11. Devuelve True si funcionó."""
    try:
        from winotify import Notification  # type: ignore
        toast = Notification(
            app_id="catastro-bot",
            title=titulo,
            msg=mensaje,
            duration=duracion,
        )
        toast.show()
        return True
    except Exception as exc:
        _log.debug("winotify falló: %s", exc)
        return False


def _notificar_via_powershell_balloon(titulo: str, mensaje: str) -> bool:
    """BalloonTip via PowerShell + System.Windows.Forms.NotifyIcon.
    No requiere instalar nada — incluido en .NET de Windows.
    """
    if not shutil.which("powershell.exe") and not shutil.which("pwsh.exe"):
        return False
    # Escapar comillas y newlines para PowerShell single-quoted strings
    t = (titulo or "catastro-bot").replace("'", "''")
    m = (mensaje or "").replace("'", "''")
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$ni = New-Object System.Windows.Forms.NotifyIcon; "
        "$ni.Icon = [System.Drawing.SystemIcons]::Warning; "
        "$ni.BalloonTipIcon = 'Warning'; "
        f"$ni.BalloonTipTitle = '{t}'; "
        f"$ni.BalloonTipText = '{m}'; "
        "$ni.Visible = $true; "
        "$ni.ShowBalloonTip(10000); "
        "Start-Sleep -Seconds 10; "
        "$ni.Dispose();"
    )
    try:
        # Ejecutar en background para no bloquear
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return True
    except Exception as exc:
        _log.debug("PS balloon falló: %s", exc)
        return False


def _notificar_via_messagebox(titulo: str, mensaje: str) -> bool:
    """MessageBox modal centrado en pantalla. No bloquea el script principal
    (corre en thread daemon) pero SÍ bloquea la UI hasta que el operador
    haga click en OK. Es lo más visible para anomalías críticas.

    Flags Win32:
      MB_OK          = 0x0
      MB_ICONWARNING = 0x30   (icono triángulo amarillo)
      MB_TOPMOST     = 0x40000  (siempre encima)
      MB_SYSTEMMODAL = 0x1000   (system-modal, fuerza foco)
      MB_SETFOREGROUND = 0x10000 (al frente)
    """
    try:
        import ctypes
        flags = 0x30 | 0x40000 | 0x1000 | 0x10000
        def _show():
            try:
                ctypes.windll.user32.MessageBoxW(0, mensaje, titulo, flags)
            except Exception:
                pass
        threading.Thread(target=_show, daemon=True).start()
        return True
    except Exception as exc:
        _log.debug("MessageBox falló: %s", exc)
        return False


def _notificar_via_powershell_messagebox(titulo: str, mensaje: str) -> bool:
    """Fallback: PowerShell + System.Windows.Forms.MessageBox (centrado).
    También bloquea hasta que el usuario acepte, pero corre desacoplado
    del proceso Python principal vía Popen.
    """
    if not shutil.which("powershell.exe") and not shutil.which("pwsh.exe"):
        return False
    t = (titulo or "catastro-bot").replace("'", "''")
    m = (mensaje or "").replace("'", "''")
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "[System.Windows.Forms.MessageBox]::Show("
        f"'{m}', '{t}', "
        "[System.Windows.Forms.MessageBoxButtons]::OK, "
        "[System.Windows.Forms.MessageBoxIcon]::Warning, "
        "[System.Windows.Forms.MessageBoxDefaultButton]::Button1, "
        "[System.Windows.Forms.MessageBoxOptions]::DefaultDesktopOnly) | Out-Null"
    )
    try:
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return True
    except Exception as exc:
        _log.debug("PS MessageBox falló: %s", exc)
        return False


def notificar_escritorio(
    *,
    titulo: str,
    mensaje: str,
    urgencia: Literal["alta", "normal", "baja"] = "normal",
) -> str:
    """Dispara una notificación en el escritorio del operador.

    Args:
        titulo: línea superior corta (≤120 chars).
        mensaje: cuerpo legible (puede tener saltos de línea).
        urgencia:
          - 'alta'   → MessageBox MODAL CENTRADA en pantalla (system-modal,
                       topmost, fuerza foco). El operador DEBE hacer click
                       para descartar. Es lo más visible — usar para
                       anomalías que detienen el ciclo.
          - 'normal' → Toast nativo Windows en la esquina inferior derecha.
                       No bloquea, se desvanece. Para avisos informativos.
          - 'baja'   → igual que normal pero duración más corta.

    Returns: nombre del método que tuvo éxito, o "" si todos fallaron.

    NO bloquea el hilo principal — la MessageBox se muestra en thread daemon.
    """
    duracion = URGENCIA_DURACION.get(urgencia, "short")
    titulo = (titulo or "catastro-bot")[:120]
    mensaje = (mensaje or "")[:500]

    if urgencia == "alta":
        # Anomalía — usar MessageBox centrada como método primario.
        # Es system-modal y topmost — imposible no verla.
        if _notificar_via_messagebox(titulo, mensaje):
            _log.info("desktop notify (alta) ok via messagebox centrada")
            return "messagebox"
        if _notificar_via_powershell_messagebox(titulo, mensaje):
            _log.info("desktop notify (alta) ok via ps-messagebox")
            return "ps-messagebox"
        # Último fallback: toast (al menos algo)
        if _notificar_via_winotify(titulo, mensaje, "long"):
            _log.info("desktop notify (alta) fallback a winotify toast")
            return "winotify-fallback"
        _log.warning("no se pudo mostrar notificación urgencia alta")
        return ""

    # Urgencia normal/baja — toast en esquina (no intrusivo)
    if _notificar_via_winotify(titulo, mensaje, duracion):
        _log.info("desktop notify (normal) ok via winotify")
        return "winotify"
    if _notificar_via_powershell_balloon(titulo, mensaje):
        _log.info("desktop notify (normal) ok via ps-balloon")
        return "ps-balloon"
    _log.warning("no se pudo mostrar notificación de escritorio")
    return ""


__all__ = ["notificar_escritorio"]
