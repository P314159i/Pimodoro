@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "APP_DIR=%~dp0"
set "SRC_DIR=%APP_DIR%Src"
set "VENV_DIR=%APP_DIR%.venv"

echo Installing PiModoro for Windows...

where py >nul 2>&1
if %errorlevel% equ 0 (
    set "PYTHON_LAUNCHER=py -3"
) else (
    where python >nul 2>&1
    if errorlevel 1 goto :python_missing
    set "PYTHON_LAUNCHER=python"
)

if not exist "%SRC_DIR%" mkdir "%SRC_DIR%"

if exist "%APP_DIR%app.py" (
    echo Moving app.py into Src...
    move /Y "%APP_DIR%app.py" "%SRC_DIR%\app.py" >nul
)

if not exist "%SRC_DIR%\app.py" goto :app_missing
if not exist "%SRC_DIR%\pimodoro_db.py" goto :modules_missing
if not exist "%SRC_DIR%\recurrence.py" goto :modules_missing

if not exist "%VENV_DIR%\Scripts\python.exe" (
    echo Creating Python virtual environment...
    %PYTHON_LAUNCHER% -m venv "%VENV_DIR%"
    if errorlevel 1 goto :venv_failed
)

echo Installing PySide6...
"%VENV_DIR%\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :dependency_failed
"%VENV_DIR%\Scripts\python.exe" -m pip install PySide6
if errorlevel 1 goto :dependency_failed

echo Creating desktop shortcut...
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "$shell = New-Object -ComObject WScript.Shell;" ^
  "$shortcut = $shell.CreateShortcut([IO.Path]::Combine([Environment]::GetFolderPath('Desktop'), 'PiModoro.lnk'));" ^
  "$shortcut.TargetPath = '%VENV_DIR%\Scripts\pythonw.exe';" ^
  "$shortcut.Arguments = '""%SRC_DIR%\app.py""';" ^
  "$shortcut.WorkingDirectory = '%APP_DIR%';" ^
  "$shortcut.IconLocation = '%VENV_DIR%\Scripts\pythonw.exe,0';" ^
  "$shortcut.Save()"
if errorlevel 1 goto :shortcut_failed

echo.
echo PiModoro installed successfully.
echo Open it using the PiModoro shortcut on your desktop.
pause
exit /b 0

:python_missing
echo.
echo Error: Python 3 was not found.
echo Install Python 3 from https://www.python.org/downloads/windows/
echo Enable "Add python.exe to PATH" during installation, then run this file again.
pause
exit /b 1

:app_missing
echo Error: Src\app.py was not found.
pause
exit /b 1

:modules_missing
echo Error: Src\pimodoro_db.py and Src\recurrence.py are required.
pause
exit /b 1

:venv_failed
echo Error: The Python virtual environment could not be created.
pause
exit /b 1

:dependency_failed
echo Error: PySide6 could not be installed. Check your internet connection.
pause
exit /b 1

:shortcut_failed
echo Error: The app was installed, but the desktop shortcut could not be created.
echo Run run_windows.bat to start PiModoro.
pause
exit /b 1
