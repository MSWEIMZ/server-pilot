@echo off
chcp 65001 >nul 2>&1
set SP_DIR=%~dp0scripts
set PYTHONIOENCODING=utf-8

if "%~1"=="" goto help
if "%~1"=="server" goto server
if "%~1"=="gpu" goto gpu
if "%~1"=="train" goto train
if "%~1"=="tasks" goto tasks
if "%~1"=="run" goto run
if "%~1"=="logs" goto logs
if "%~1"=="stop" goto stop
if "%~1"=="upload" goto upload
if "%~1"=="download" goto download
if "%~1"=="ls" goto ls
if "%~1"=="cat" goto cat
if "%~1"=="dash" goto dash
if "%~1"=="help" goto help
if "%~1"=="-h" goto help
if "%~1"=="--help" goto help

echo Unknown command: %~1
goto help

:server
python "%SP_DIR%\server_monitor.py" %2 %3 %4 %5
goto end

:gpu
python "%SP_DIR%\server_monitor.py" --gpu %2 %3
goto end

:train
python "%SP_DIR%\server_monitor.py" --train --logs %2 %3
goto end

:tasks
python "%SP_DIR%\task_mgr.py" list %2 %3
goto end

:run
shift
python "%SP_DIR%\task_mgr.py" run %1 %2 %3 %4 %5 %6 %7 %8 %9
goto end

:logs
shift
python "%SP_DIR%\task_mgr.py" logs %1 %2 %3 %4 %5 %6 %7 %8
goto end

:stop
shift
python "%SP_DIR%\task_mgr.py" stop %1 %2 %3 %4 %5
goto end

:upload
shift
python "%SP_DIR%\ssh_exec.py" --upload %1 %2
goto end

:download
shift
python "%SP_DIR%\ssh_exec.py" --download %1 %2
goto end

:ls
shift
python "%SP_DIR%\file_ops.py" ls %1 %2 %3
goto end

:cat
shift
python "%SP_DIR%\file_ops.py" cat %1 %2 %3 %4
goto end

:dash
python "%SP_DIR%\web\dashboard.py" %2 %3
goto end

:help
echo.
echo   Server Pilot - Remote Server Management
echo.
echo   Usage: sp ^<command^> [options]
echo.
echo   Commands:
echo     server          Full server status report
echo     gpu             GPU status only
echo     train           Training processes + log parsing
echo     tasks           List background tasks
echo     run ^<cmd^>       Run command in background
echo     logs ^<name^>     View task logs  (-n 100, -f follow)
echo     stop ^<name^>     Stop task  (--all to stop all)
echo     upload ^<l^> ^<r^>  Upload local file to server
echo     download ^<r^> ^<l^> Download remote file
echo     ls ^<path^>       List remote directory  (-t tree)
echo     cat ^<file^>      View remote file content
echo     dash            Launch web dashboard
echo     help            Show this help
echo.
echo   Examples:
echo     sp gpu
echo     sp run "python train.py --epochs 100" --name v1
echo     sp logs v1 -f
echo     sp upload ./data /root/data
echo     sp dash
echo.

:end