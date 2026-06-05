@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_server.ps1" -Foreground
echo.
echo Press any key to close this window.
pause >nul
