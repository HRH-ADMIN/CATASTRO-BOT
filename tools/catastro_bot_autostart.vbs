' ============================================================================
'  Catastro Bot - Autostart sin ventanas (U-02 paso C, 2026-05-22)
'  Reemplazo del .bat: usa pythonw (sin consola) y deja al operador ver
'  el output via /config/runtime (panel HTML).
'
'  Componentes arrancados:
'    1. tools/start_chrome_bot.py        (Chrome con CDP 9222)
'    2. python -m src.utils.healthcheck  (watchdog Chrome)
'    3. python -m src.main               (scheduler + dashboard Flask)
'
'  NO arranca el dashboard legacy standalone porque src.main ya levanta
'  el Flask en el puerto 9224 (eso causaba conflicto de bind en el
'  .bat anterior).
'
'  Para el modo "ver consolas" (debug), usar el .bat viejo o
'  toggle futuro en /config/runtime.
' ============================================================================

Option Explicit

Const PROJECT_ROOT = "C:\catastro-bot"
Const PYTHONW    = "C:\catastro-bot\.venv\Scripts\pythonw.exe"
Const HIDDEN     = 0      ' WshShell.Run mode: 0 = ventana oculta
Const NO_WAIT    = False  ' no esperar a que termine

Dim WshShell, Args, fso
Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' Verificar que el venv existe - si no, abortar con mensaje claro
If Not fso.FileExists(PYTHONW) Then
    MsgBox "ERROR: " & PYTHONW & " no existe. Ejecutar 'uv sync' en " & PROJECT_ROOT, _
           vbCritical, "Catastro Bot - Autostart"
    WScript.Quit 1
End If

' Variables de entorno críticas (mismas que el .bat)
Dim env
Set env = WshShell.Environment("Process")
env("CATASTRO_BOT_DEV_MODE") = "1"
env("CATASTRO_BOT_SIMULAR_APT") = "0"

' Cambiar al directorio del proyecto antes de lanzar los procesos
WshShell.CurrentDirectory = PROJECT_ROOT

' --- 1. Chrome del bot ---
WshShell.Run """" & PYTHONW & """ tools\start_chrome_bot.py", HIDDEN, NO_WAIT

' Esperar 5s a que Chrome levante el CDP
WScript.Sleep 5000

' --- 2. Watchdog Chrome (relanza si Chrome cae) ---
WshShell.Run """" & PYTHONW & """ -m src.utils.healthcheck --interval 60", HIDDEN, NO_WAIT

' --- 3. Scheduler + dashboard Flask (mismo proceso) ---
WshShell.Run """" & PYTHONW & """ -m src.main", HIDDEN, NO_WAIT

' --- 4. Abrir el dashboard en el browser por defecto, una sola vez ---
WScript.Sleep 4000  ' dar tiempo a que Flask haga bind
WshShell.Run "http://localhost:9224/", 1, NO_WAIT

' Fin del script. Los 3 procesos quedan corriendo en background.
WScript.Quit 0
