# install_desktop_shortcut.ps1
# ASCII-only para evitar problemas de encoding en PowerShell 5.1.
# ----------------------------------------------------------------------------
# Crea (o sobrescribe idempotentemente) un shortcut en el Escritorio del
# usuario actual que abre el dashboard de catastro-bot en el navegador
# por defecto.
#
# Plan: PLAN_MEJORAS_catastro-bot_3.md Sprint 1 / U-01.
#
# Uso:
#   powershell -ExecutionPolicy Bypass -File tools\install_desktop_shortcut.ps1
#   powershell -ExecutionPolicy Bypass -File tools\install_desktop_shortcut.ps1 -Port 9224
#
# Idempotente: re-ejecutar sobreescribe sin error.
# ----------------------------------------------------------------------------

param(
    [int]$Port = 0,
    [switch]$Quiet
)

$ErrorActionPreference = 'Stop'

# Resolver la raiz del proyecto (un nivel arriba de tools/)
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir

# 1) Resolver puerto: preferir parametro; si no, leer de config/settings.py
if ($Port -eq 0) {
    try {
        $Py = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
        if (-not (Test-Path $Py)) { $Py = 'python' }
        Push-Location $ProjectRoot
        $Port = & $Py -c "from config.settings import DASHBOARD_PORT; print(DASHBOARD_PORT)" 2>$null
        Pop-Location
        if (-not $Port) { $Port = 9224 }
    } catch {
        $Port = 9224
    }
}

$Url = "http://localhost:$Port/"

# 2) Resolver path del shortcut
$Desktop = [Environment]::GetFolderPath('Desktop')
$ShortcutPath = Join-Path $Desktop 'Catastro-Bot Dashboard.lnk'

# 3) Resolver path del icono (opcional)
$IconPath = Join-Path $ProjectRoot 'assets\icon.ico'
$UseIcon = Test-Path $IconPath

# 4) Crear/sobrescribir el shortcut via WScript.Shell COM.
#    TargetPath apunta a rundll32 para abrir la URL en el browser default
#    sin depender del path de Chrome/Firefox.
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = "$env:SystemRoot\System32\rundll32.exe"
$Shortcut.Arguments = "url.dll,FileProtocolHandler $Url"
$Shortcut.Description = "Catastro-Bot Dashboard ($Url)"
$Shortcut.WorkingDirectory = $ProjectRoot
if ($UseIcon) {
    $Shortcut.IconLocation = "$IconPath,0"
} else {
    # Fallback: globo terraqueo de SHELL32.dll (icon index 14).
    $Shortcut.IconLocation = "$env:SystemRoot\System32\SHELL32.dll,14"
}
$Shortcut.Save()

if (-not $Quiet) {
    Write-Host ""
    Write-Host "[OK] Shortcut creado/actualizado:" -ForegroundColor Green
    Write-Host "     $ShortcutPath"
    Write-Host "     URL: $Url"
    if ($UseIcon) {
        Write-Host "     Icono: $IconPath"
    } else {
        Write-Host "     Icono: default Windows. Colocar assets\icon.ico para customizar." -ForegroundColor Yellow
    }
    Write-Host ""
}

exit 0
