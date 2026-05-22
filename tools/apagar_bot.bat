@echo off
REM ────────────────────────────────────────────────────────────
REM  Apagar Catastro Bot — cierra todos los procesos del bot
REM  NO toca tu Chrome personal (otros perfiles)
REM ────────────────────────────────────────────────────────────

echo.
echo ============================================================
echo   APAGANDO CATASTRO BOT
echo ============================================================
echo.

powershell -NoProfile -Command "& {Get-WmiObject Win32_Process | Where-Object {$_.Name -eq 'python.exe' -and ($_.CommandLine -like '*src.main*' -or $_.CommandLine -like '*dashboard_web*' -or $_.CommandLine -like '*healthcheck*' -or $_.CommandLine -like '*start_chrome_bot*' -or $_.CommandLine -like '*muni_subir*')} | ForEach-Object {Write-Host ('  Matando python PID' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force}}"

powershell -NoProfile -Command "& {Get-WmiObject Win32_Process | Where-Object {$_.Name -eq 'chrome.exe' -and ($_.CommandLine -like '*chrome_profile_apt*' -or $_.CommandLine -like '*remote-debugging-port=9222*')} | ForEach-Object {Write-Host ('  Matando chrome bot PID' + $_.ProcessId); Stop-Process -Id $_.ProcessId -Force}}"

echo.
echo ============================================================
echo   BOT APAGADO
echo ============================================================
echo   Tus Chrome personales NO se tocaron.
echo   Para volver a prender: tools\catastro_bot_autostart.bat
echo ============================================================
echo.
pause
