@echo off
cd /d "%~dp0"
if not exist server_agent.json (
  echo.
  echo Copy server_agent.example.json to server_agent.json and edit it first.
  echo.
  pause
  exit /b 1
)
py -3 agent.py
if errorlevel 1 python agent.py
pause
