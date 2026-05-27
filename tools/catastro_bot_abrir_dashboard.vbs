' ============================================================================
'  Catastro Bot - Abrir dashboard (hotfix shortcut escritorio, 2026-05-27)
'
'  Script invocado por el shortcut "Catastro-Bot Dashboard.lnk" del escritorio.
'  Hace lo que el rundll32 NO hacia: si el dashboard no esta corriendo, lo
'  levanta antes de abrir el browser.
'
'  Flujo:
'    1. Buscar si ya hay un proceso python(w) corriendo src.main o
'       src.utils.dashboard_web (idempotencia).
'    2. Si SI -> abrir browser y terminar.
'    3. Si NO -> lanzar `cmd /c start "" /B python -m src.main` con
'       stdout redirigido a logs/dashboard_stdout.log. Esto arranca el
'       bot completo (scheduler + Flask + dashboard en puerto 9224).
'    4. Polletar /api/health (vía HTTP) hasta 15s a que Flask haga bind.
'    5. Si responde -> abrir browser. Si no -> MsgBox con diagnostico.
'
'  No abre ventanas CMD (cmd /c start /B + HIDDEN).
' ============================================================================

Option Explicit

Const PROJECT_ROOT  = "C:\catastro-bot"
' Usamos python.exe (no pythonw) porque pythonw tiene problemas con
' Flask/waitress en Python 3.13 Windows: el server hace bind del puerto
' pero queda colgado sin servir requests (probablemente subprocess o
' threading hangs cuando stdout=None). python.exe funciona bien,
' y para evitar la ventana CMD usamos `cmd /c start "" /B` con HIDDEN.
Const PYTHON_EXE    = "C:\catastro-bot\.venv\Scripts\python.exe"
Const DASHBOARD_URL = "http://localhost:9224/"
Const PUERTO        = 9224
Const HIDDEN        = 0
Const NO_WAIT       = False

Dim WshShell, fso
Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' --- Log opcional para debug ---
' Se escribe solo si la carpeta logs/ ya existe. Util si el operador
' reporta "el shortcut no funciona": revisar logs/dashboard_shortcut.log.
Dim LOG_PATH, LOG_ENABLED
LOG_PATH = PROJECT_ROOT & "\logs\dashboard_shortcut.log"
LOG_ENABLED = fso.FolderExists(PROJECT_ROOT & "\logs")
Sub LogLine(msg)
    If Not LOG_ENABLED Then Exit Sub
    Dim f
    On Error Resume Next
    Set f = fso.OpenTextFile(LOG_PATH, 8, True)  ' 8 = append, True = create
    If Not f Is Nothing Then
        f.WriteLine Now & " | " & msg
        f.Close
    End If
    On Error Goto 0
End Sub
LogLine "==== VBS arrancado ===="

' Verificar que el venv existe
If Not fso.FileExists(PYTHON_EXE) Then
    MsgBox "ERROR: " & PYTHON_EXE & " no existe." & vbCrLf & vbCrLf & _
           "Ejecutar 'uv sync' en " & PROJECT_ROOT, _
           vbCritical, "Catastro Bot - Dashboard"
    WScript.Quit 1
End If

' --- Funcion: verificar si hay un python(w) del dashboard corriendo ---
' Para chequeo INICIAL de idempotencia (no relanzar si ya hay uno corriendo).
' Mas confiable que HTTP probe porque WinHttp/MSXML2 dan falsos positivos
' con sockets en TIME_WAIT.
Function DashboardProcVivo()
    Dim wmi, procesos, p
    On Error Resume Next
    Set wmi = GetObject("winmgmts:\\.\root\cimv2")
    Set procesos = wmi.ExecQuery( _
        "SELECT ProcessId, CommandLine FROM Win32_Process " & _
        "WHERE Name='pythonw.exe' OR Name='python.exe'")
    DashboardProcVivo = False
    If Err.Number = 0 Then
        For Each p In procesos
            If Not IsNull(p.CommandLine) Then
                If InStr(p.CommandLine, "src.utils.dashboard_web") > 0 _
                   Or InStr(p.CommandLine, "src.main") > 0 Then
                    DashboardProcVivo = True
                    Exit For
                End If
            End If
        Next
    End If
    On Error Goto 0
End Function

' --- Funcion: verificar si HTTP responde realmente ---
' Para polling DESPUES de lanzar (queremos abrir el browser solo cuando
' el server ya esta listo, no apenas el proceso existe).
Function DashboardHttpResponde()
    Dim http
    On Error Resume Next
    Set http = CreateObject("WinHttp.WinHttpRequest.5.1")
    http.SetTimeouts 800, 800, 800, 800
    http.Open "GET", DASHBOARD_URL, False
    http.SetRequestHeader "Cache-Control", "no-cache"
    http.Send
    If Err.Number = 0 And http.Status = 200 Then
        DashboardHttpResponde = True
    Else
        DashboardHttpResponde = False
    End If
    On Error Goto 0
End Function

' Variables de entorno
Dim env
Set env = WshShell.Environment("Process")
env("CATASTRO_BOT_DEV_MODE") = "1"

WshShell.CurrentDirectory = PROJECT_ROOT

' --- 1. Si ya hay un proceso del dashboard corriendo, abrir browser y salir ---
LogLine "Chequeando proceso dashboard..."
If DashboardProcVivo() Then
    LogLine "Dashboard YA corriendo - abriendo browser"
    WshShell.Run DASHBOARD_URL, 1, NO_WAIT
    WScript.Quit 0
End If
LogLine "Dashboard NO corriendo - lanzando python"

' --- 2. No esta vivo: levantar el bot completo (src.main) ---
'    src.main arranca scheduler + Flask + dashboard en el puerto 9224.
'    Es lo que el operador realmente necesita cuando el dashboard
'    estaba caido (probablemente todo el bot lo estaba).
'    NO usamos `python -m src.utils.dashboard_web` standalone porque ese
'    usa http.server de stdlib que es fragil; src.main usa Flask+waitress
'    que es robusto.
'
'    `cmd /c start "" /B python ...`:
'      cmd /c   = ejecutar y cerrar la shell padre
'      start "" = titulo vacio (obligatorio cuando el comando tiene comillas)
'      /B       = sin nueva ventana (HIDDEN la oculta encima)
'    Redirigir stdout/stderr a archivo evita cuelgues por print() en
'    proceso detached. El log queda en logs/dashboard_stdout.log para
'    debug si algo falla.
Dim CMD, STDOUT_LOG
STDOUT_LOG = PROJECT_ROOT & "\logs\dashboard_stdout.log"
CMD = "cmd /c start """" /B """ & PYTHON_EXE & """ -m src.main" _
      & " > """ & STDOUT_LOG & """ 2>&1"
LogLine "Comando: " & CMD
LogLine "CurrentDirectory: " & WshShell.CurrentDirectory
Dim launchErr
launchErr = WshShell.Run(CMD, HIDDEN, NO_WAIT)
LogLine "WshShell.Run devolvio: " & launchErr

' --- 3. Polletar hasta 30s a que haga bind ---
' src.main tarda ~10-25s en arrancar (carga scheduler, jobs, chrome init).
' 30s deja margen suficiente. El operador ve el browser abrirse cuando ya
' responde de verdad, no antes (evita el "no se puede acceder" frustrante).
Dim i, vivo
vivo = False
For i = 1 To 30
    WScript.Sleep 1000
    If DashboardHttpResponde() Then
        vivo = True
        Exit For
    End If
Next

LogLine "Loop poll termino - vivo=" & vivo & " (iteraciones=" & i & ")"
If vivo Then
    LogLine "Abriendo browser"
    WshShell.Run DASHBOARD_URL, 1, NO_WAIT
    WScript.Quit 0
Else
    LogLine "Dashboard NO arranco - mostrando MsgBox"
    MsgBox "El dashboard no respondio en " & PUERTO & " despues de 15s." & vbCrLf & vbCrLf & _
           "Posibles causas:" & vbCrLf & _
           "  - El puerto " & PUERTO & " esta ocupado por otra app." & vbCrLf & _
           "  - Falta sincronizar el venv: ejecutar 'uv sync'." & vbCrLf & _
           "  - Hay un error en logs/catastro-bot.log al arrancar Flask." & vbCrLf & vbCrLf & _
           "Intentar manualmente:" & vbCrLf & _
           "  cd " & PROJECT_ROOT & vbCrLf & _
           "  .venv\Scripts\python.exe -m src.utils.dashboard_web --port " & PUERTO, _
           vbCritical, "Catastro Bot - Dashboard no arranco"
    WScript.Quit 2
End If
