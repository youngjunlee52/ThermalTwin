@echo off
cd /d "%~dp0"
title ThermalTwin Server

if not exist "backend\.venv\Scripts\python.exe" (
  echo Run 1_setup.bat first.
  pause
  exit /b 1
)

rem TLS (https / wss): put key.pem and cert.pem in backend\certs to enable it automatically
set "URL=http://localhost:8000/"
set "TLS="
if exist "backend\certs\key.pem" if exist "backend\certs\cert.pem" (
  set "URL=https://localhost:8443/"
  set "TLS=--ssl-keyfile backend\certs\key.pem --ssl-certfile backend\certs\cert.pem"
)
set "PORT=8000"
if defined TLS set "PORT=8443"
rem Other devices can connect only when an ingest token is set (otherwise this PC only)
set "HOST=127.0.0.1"
if defined THERMALTWIN_INGEST_TOKEN set "HOST=0.0.0.0"

echo ==================================================
echo   ThermalTwin is running:  %URL%
echo   The browser opens automatically in a few seconds.
echo   To stop: close this window.
if not defined THERMALTWIN_INGEST_TOKEN echo   [i] No THERMALTWIN_INGEST_TOKEN: only this PC can connect.
if defined THERMALTWIN_INGEST_TOKEN echo   [i] Token set: other devices on the network can send data to this PC's IP.
echo ==================================================

rem Open the browser after the server has started
start "" cmd /c "timeout /t 4 /nobreak >nul & start %URL%"

"backend\.venv\Scripts\python.exe" -m uvicorn main:app --app-dir backend --host %HOST% --port %PORT% %TLS%
pause
