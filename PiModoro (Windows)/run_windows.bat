@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo PiModoro is not installed yet. Run install_windows.bat first.
    pause
    exit /b 1
)

if not exist "Src\app.py" (
    echo Src\app.py was not found.
    pause
    exit /b 1
)

start "PiModoro" ".venv\Scripts\pythonw.exe" "Src\app.py"
exit /b 0
