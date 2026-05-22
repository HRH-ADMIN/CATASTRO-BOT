@echo off
REM ─────────────────────────────────────────────────────────────────────
REM  Catastro Bot — Autostart al iniciar sesión Windows (LEGACY)
REM
REM  ⚠️  DEPRECATED (2026-05-22, U-02 paso C):
REM      Usá `catastro_bot_autostart.vbs` en su lugar — sin ventanas CMD,
REM      sin conflicto de doble dashboard (este .bat arranca
REM      dashboard_web standalone Y src.main, ambos intentan bind a 9224).
REM
REM      Este .bat sigue funcional para DEBUGGING (ver consolas), pero
REM      el flujo recomendado de producción es:
REM         cscript //nologo tools\catastro_bot_autostart.vbs
REM
REM  ─────────────────────────────────────────────────────────────────────
REM  Lo que arranca:
REM
REM  Lo que arranca:
REM    1. Chrome del bot (CDP 9222) con perfil dedicado
REM    2. Watchdog del Chrome (relanza si cae)
REM    3. Bot principal (scheduler, sync APT, etc.)
REM
REM  Para detener todo: cerrar la ventana de cmd.
REM  Para pausar: comentar la línea con REM al inicio.
REM ─────────────────────────────────────────────────────────────────────

cd /d C:\catastro-bot

REM  ── PRODUCCION: simulaciones APT DESACTIVADAS ──
REM  CATASTRO_BOT_DEV_MODE=1   → BD sin SQLCipher (necesario Windows)
REM  CATASTRO_BOT_SIMULAR_APT=0 → NO simular respuestas APT (regla critica)
set CATASTRO_BOT_DEV_MODE=1
set CATASTRO_BOT_SIMULAR_APT=0

echo.
echo ============================================================
echo   CATASTRO BOT - AUTOSTART
echo   %DATE% %TIME%
echo   MODO: PRODUCCION (sin simulaciones APT)
echo ============================================================
echo.

REM 1) Chrome del bot (CDP 9222)
echo [1/3] Lanzando Chrome del bot (CDP 9222)...
start "ChromeBot" /MIN .venv\Scripts\python.exe tools\start_chrome_bot.py

REM Esperar 8 segundos a que Chrome arranque
timeout /t 8 /nobreak >nul

REM 2) Watchdog Chrome (relanza si cae)
echo [2/3] Lanzando watchdog Chrome (chequea cada 60s)...
start "ChromeWatchdog" /MIN .venv\Scripts\python.exe -m src.utils.healthcheck --interval 60

REM 3) Dashboard web local (puerto 9224)
echo [3/4] Lanzando dashboard web (http://localhost:9224)...
start "Dashboard" /MIN .venv\Scripts\python.exe -m src.utils.dashboard_web --port 9224

REM Esperar 3s a que dashboard arranque
timeout /t 3 /nobreak >nul

REM Abrir dashboard en el browser por defecto
echo Abriendo dashboard en navegador...
start http://localhost:9224

REM 4) Bot principal (scheduler + sync APT + workflows)
echo.
echo [4/4] Lanzando bot principal (scheduler)...
echo.
echo ============================================================
echo   TODO LISTO. Dashboard: http://localhost:9224
echo   Para detener TODO: cierre esta ventana.
echo   Logs en logs/catastro-bot.log
echo ============================================================
echo.

.venv\Scripts\python.exe -m src.main

REM Si llega aqui es porque src.main termino
echo.
echo [FIN] Bot principal termino. Esta ventana se cerrara en 30s.
timeout /t 30
