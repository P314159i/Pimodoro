@echo off
setlocal EnableExtensions
cd /d "%~dp0"

rem Run from a flat folder containing the Python files, fonts, and pomo.png.
set "PACKAGE_ROOT=%~dp0"
set "INSTALL_DIR=%PACKAGE_ROOT%PiModoro (Windows)"
set "SRC_DIR=%INSTALL_DIR%\Src"
set "MISC_DIR=%INSTALL_DIR%\misc"
set "VENV_DIR=%INSTALL_DIR%\.venv"

echo Installing PiModoro for Windows...

where py >nul 2>&1
if %errorlevel% equ 0 (
    set "PYTHON_LAUNCHER=py -3"
) else (
    where python >nul 2>&1
    if errorlevel 1 goto :python_missing
    set "PYTHON_LAUNCHER=python"
)

rem Validate the entire package before moving anything.
call :require_source "app.py" "%SRC_DIR%\app.py"
if errorlevel 1 goto :missing_file
call :require_source "pimodoro_db.py" "%SRC_DIR%\pimodoro_db.py"
if errorlevel 1 goto :missing_file
call :require_source "recurrence.py" "%SRC_DIR%\recurrence.py"
if errorlevel 1 goto :missing_file
call :require_source "Flighty.ttf" "%MISC_DIR%\Flighty.ttf"
if errorlevel 1 goto :missing_file
call :require_source "Head.ttf" "%MISC_DIR%\Head.ttf"
if errorlevel 1 goto :missing_file
call :require_source "Mighty-X34Z2.ttf" "%MISC_DIR%\Mighty-X34Z2.ttf"
if errorlevel 1 goto :missing_file
call :require_source "NotoColorEmoji-Regular.ttf" "%MISC_DIR%\NotoColorEmoji-Regular.ttf"
if errorlevel 1 goto :missing_file
call :require_source "pomo.png" "%MISC_DIR%\pomo.png"
if errorlevel 1 goto :missing_file

if not exist "%SRC_DIR%" mkdir "%SRC_DIR%"
if not exist "%MISC_DIR%" mkdir "%MISC_DIR%"

call :move_if_root "app.py" "%SRC_DIR%\app.py"
call :move_if_root "pimodoro_db.py" "%SRC_DIR%\pimodoro_db.py"
call :move_if_root "recurrence.py" "%SRC_DIR%\recurrence.py"
call :move_if_root "Flighty.ttf" "%MISC_DIR%\Flighty.ttf"
call :move_if_root "Head.ttf" "%MISC_DIR%\Head.ttf"
call :move_if_root "Mighty-X34Z2.ttf" "%MISC_DIR%\Mighty-X34Z2.ttf"
call :move_if_root "NotoColorEmoji-Regular.ttf" "%MISC_DIR%\NotoColorEmoji-Regular.ttf"
call :move_if_root "pomo.png" "%MISC_DIR%\pomo.png"
if exist "%PACKAGE_ROOT%README.md" move /Y "%PACKAGE_ROOT%README.md" "%MISC_DIR%\README.md" >nul

rem Remove empty accidental nesting made by the older installer.
rmdir "%INSTALL_DIR%\PiModoro (Windows)\Src" 2>nul
rmdir "%INSTALL_DIR%\PiModoro (Windows)" 2>nul

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

rem Generate the launcher inside the installed Windows folder.
(
    echo @echo off
    echo setlocal
    echo cd /d "%%~dp0"
    echo if not exist ".venv\Scripts\pythonw.exe" ^(
    echo     echo PiModoro is not installed. Run the outer install_windows.bat again.
    echo     pause
    echo     exit /b 1
    echo ^)
    echo start "PiModoro" ".venv\Scripts\pythonw.exe" "Src\app.py"
    echo exit /b 0
) > "%INSTALL_DIR%\run_windows.bat"

echo Creating desktop shortcut...
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
  "$shell = New-Object -ComObject WScript.Shell;" ^
  "$shortcut = $shell.CreateShortcut([IO.Path]::Combine([Environment]::GetFolderPath('Desktop'), 'PiModoro.lnk'));" ^
  "$shortcut.TargetPath = '%VENV_DIR%\Scripts\pythonw.exe';" ^
  "$shortcut.Arguments = '""%SRC_DIR%\app.py""';" ^
  "$shortcut.WorkingDirectory = '%INSTALL_DIR%';" ^
  "$shortcut.IconLocation = '%VENV_DIR%\Scripts\pythonw.exe,0';" ^
  "$shortcut.Save()"
if errorlevel 1 goto :shortcut_failed

echo.
echo PiModoro installed in: %INSTALL_DIR%
echo The original payload files were moved out of the package root.
echo Open PiModoro using the desktop shortcut.
pause
exit /b 0

:require_source
if exist "%PACKAGE_ROOT%%~1" exit /b 0
if exist "%~2" exit /b 0
echo Error: %~1 was not found beside install_windows.bat.
exit /b 1

:move_if_root
if exist "%PACKAGE_ROOT%%~1" (
    echo Moving %~1 into PiModoro ^(Windows^)...
    move /Y "%PACKAGE_ROOT%%~1" "%~2" >nul
)
exit /b 0

:missing_file
pause
exit /b 1

:python_missing
echo.
echo Error: Python 3 was not found.
echo Install Python 3 from https://www.python.org/downloads/windows/
echo Enable "Add python.exe to PATH" during installation, then run this file again.
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
echo The app was installed, but the desktop shortcut could not be created.
echo Run PiModoro ^(Windows^)\run_windows.bat to start it.
pause
exit /b 1
