@echo off
rem Demo: a virtual BMS sends CAN frames on a virtual CAN bus. The CAN gateway decodes them with the DBC,
rem sends telemetry to ThermalTwin as vehicle "can_demo", and writes the advisory commands back as TT_Command frames.
rem Start 2_start.bat first, then pick "can_demo" in the vehicle selector at the top of the page.
cd /d "%~dp0"
title ThermalTwin CAN Gateway Demo
"backend\.venv\Scripts\python.exe" -c "import can, cantools" 2>nul || "backend\.venv\Scripts\python.exe" -m pip install -r tools\requirements-can.txt
"backend\.venv\Scripts\python.exe" -u tools\can_gateway.py --demo --vehicle can_demo --speed 30 --power 190
pause
