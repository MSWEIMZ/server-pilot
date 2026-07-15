@echo off
setlocal
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_dashboard.ps1"
if errorlevel 1 (
    echo.
    pause
)

endlocal
