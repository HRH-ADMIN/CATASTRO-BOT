@echo off
REM ============================================================
REM  Inicia Google Chrome con puerto de debug 9222 usando un
REM  PERFIL DEDICADO del bot (separado del Chrome personal).
REM
REM  Su Chrome de siempre puede seguir abierto sin conflicto.
REM  Como Firma Digital BCR usa apps locales (Fortify, Gaudi,
REM  Monitor Agent), funciona igualmente en este perfil.
REM ============================================================

echo.
echo === catastro-bot: iniciar Chrome dedicado con CDP ===
echo.

REM 1) Localizar chrome.exe
set "CHROME="
if exist "C:\Program Files\Google\Chrome\Application\chrome.exe" (
    set "CHROME=C:\Program Files\Google\Chrome\Application\chrome.exe"
    goto :found
)
if exist "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" (
    set "CHROME=C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"
    goto :found
)
if exist "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe" (
    set "CHROME=%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
    goto :found
)

echo [ERROR] No se encontro chrome.exe instalado.
pause
exit /b 1

:found
echo Chrome encontrado en: %CHROME%

REM 2) Perfil DEDICADO del bot (no conflicta con el Chrome personal)
set "BOT_PROFILE=C:\catastro-bot\data\temp\chrome_profile_apt"
if not exist "%BOT_PROFILE%" mkdir "%BOT_PROFILE%" 2>NUL
echo Perfil del bot: %BOT_PROFILE%
echo.

REM 3) Lanzar Chrome con su propia instancia (independiente del Chrome personal)
echo Lanzando Chrome dedicado con --remote-debugging-port=9222...
start "" "%CHROME%" --remote-debugging-port=9222 --user-data-dir="%BOT_PROFILE%" --no-first-run --no-default-browser-check --new-window https://apt.cfia.or.cr/APT2/Home

REM 4) Polling del puerto hasta 15 segundos
echo Esperando que el puerto 9222 responda (hasta 15s)...

powershell -NoProfile -Command ^
  "$ProgressPreference='SilentlyContinue';" ^
  "$ok=$false;" ^
  "for ($i=1; $i -le 15; $i++) {" ^
  "  try {" ^
  "    $r = Invoke-WebRequest -Uri http://localhost:9222/json/version -TimeoutSec 1 -UseBasicParsing;" ^
  "    if ($r.StatusCode -eq 200) {" ^
  "      Write-Host ('[OK] CDP activo en puerto 9222 (tras {0}s)' -f $i) -ForegroundColor Green;" ^
  "      $ok=$true;" ^
  "      break;" ^
  "    }" ^
  "  } catch {}" ^
  "  Start-Sleep -Seconds 1;" ^
  "}" ^
  "if (-not $ok) {" ^
  "  Write-Host '[ERROR] Puerto 9222 no respondio en 15s.' -ForegroundColor Red;" ^
  "  exit 1;" ^
  "}"

if %errorlevel% neq 0 (
    echo.
    echo Diagnostico manual:
    echo   1. Verifique que Chrome se abrio en pantalla con la pestana de APT
    echo   2. Si no abrio, ejecute en cmd:
    echo      "%CHROME%" --remote-debugging-port=9222 --user-data-dir="%BOT_PROFILE%"
    echo   3. Verifique en su navegador: http://localhost:9222/json/version
    echo.
    pause
    exit /b 1
)

echo.
echo ==========================================
echo  Listo.
echo  Haga Firma Digital BCR en la ventana de Chrome que se abrio.
echo  Cuando vea el portal APT con su nombre arriba, ya puede correr la prueba.
echo ==========================================
echo.

if "%1"=="" pause
exit /b 0
