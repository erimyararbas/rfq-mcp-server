@echo off
REM Windows setup: virtual environment, dependencies, tests, Claude Desktop registration
cd /d "%~dp0"
where py >nul 2>nul && (py -3 -m venv .venv) || (python -m venv .venv)
if errorlevel 1 (
  echo Python 3.10+ not found. Install it from https://www.python.org/downloads/ with "Add to PATH".
  pause & exit /b 1
)
".venv\Scripts\python.exe" -m pip install -q --upgrade pip
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if not exist ".env" copy ".env.example" ".env" >nul
".venv\Scripts\python.exe" -m pytest -q
".venv\Scripts\python.exe" scripts\add_to_claude_desktop.py
pause
