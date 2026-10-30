@echo off
rem (Optional) Only for editing the React code with live reload. Needs Node.js LTS.
rem Keep 2_start.bat running, then run this and open http://localhost:5173
cd /d "%~dp0frontend"
title ThermalTwin Frontend (dev)
node --version >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Node.js is not installed. Install the LTS version from https://nodejs.org/
  pause
  exit /b 1
)
if not exist "node_modules" call npm install
call npm run dev
pause
