@echo off
setlocal
cd /d "%~dp0"
title ThermalTwin - Setup

echo ==================================================
echo   ThermalTwin v0.4 setup (run this only once)
echo ==================================================
echo.

set "PY="
py -3 --version >nul 2>&1 && set "PY=py -3"
if not defined PY (
  python --version >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo [ERROR] Python is not installed.
  echo         Install Python 3.12 or newer from https://www.python.org/downloads/
  echo         and CHECK "Add python.exe to PATH" on the first install screen.
  goto :fail
)
echo [OK] Python found:
%PY% --version
echo.

echo Installing backend packages... (1-2 minutes)
if not exist "backend\.venv\Scripts\python.exe" (
  %PY% -m venv backend\.venv
  if errorlevel 1 goto :fail
)
"backend\.venv\Scripts\python.exe" -m pip install --upgrade pip
"backend\.venv\Scripts\python.exe" -m pip install -r backend\requirements.txt
if errorlevel 1 goto :fail
rem Optional: CAN gateway packages (python-can, cantools). Setup continues even if this fails.
"backend\.venv\Scripts\python.exe" -m pip install -r tools\requirements-can.txt
if errorlevel 1 echo [WARN] CAN gateway packages were not installed. Only tools\can_gateway.py needs them.

echo.
echo ==================================================
echo   SETUP COMPLETE!  Now double-click 2_start.bat
echo ==================================================
pause
exit /b 0

:fail
echo.
echo ==================================================
echo   Setup failed. Copy the ERROR lines above and ask for help.
echo ==================================================
pause
exit /b 1
