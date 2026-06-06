@echo off
cd /d "%~dp0..\.."
set PYTHONIOENCODING=utf-8
powershell -NoProfile -ExecutionPolicy Bypass -File "%CD%\start_server.ps1" -Foreground
