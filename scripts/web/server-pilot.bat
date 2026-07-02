@echo off
:: Server Pilot Dashboard - Silent Start with System Tray Icon
:: Double-click this file to launch. A tray icon will appear.
:: Right-click the tray icon for: Open / Refresh / Stop / Restart / Exit

set "SCRIPT_DIR=%~dp0"
powershell.exe -ExecutionPolicy Bypass -WindowStyle Hidden -File "%SCRIPT_DIR%server-pilot-tray.ps1"
