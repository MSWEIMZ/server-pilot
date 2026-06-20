@echo off
echo ========================================
echo   Server Pilot - Web Dashboard
echo ========================================
echo.
cd /d "%~dp0.."
python scripts\web\dashboard.py %*
