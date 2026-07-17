@echo off
REM One-double-click start for the CS:GO Revival inventory server (Windows).
REM Run this from the server\ folder. Passes any extra args to revival_server.py.

cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python is not installed or not on PATH.
    echo Install it from https://www.python.org/downloads/ and tick "Add Python to PATH".
    pause
    exit /b 1
)

if not exist "data\catalog.json" (
    if exist "data\catalog.sample.json" (
        echo [start-server] no data\catalog.json found - using the sample catalog for now.
        echo [start-server] run build_catalog.py against your items_game.txt for the full list.
        copy /y "data\catalog.sample.json" "data\catalog.json" >nul
    )
)

python revival_server.py --host 0.0.0.0 --port 8787 %*
pause
