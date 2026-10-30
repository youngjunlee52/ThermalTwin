@echo off
rem Demo: a separate "vehicle" sends live BMS data to ThermalTwin and follows its commands.
rem Start 2_start.bat first. The web page switches to external-input mode automatically.
cd /d "%~dp0"
title ThermalTwin Demo Gateway
"backend\.venv\Scripts\python.exe" -u tools\fake_bms_gateway.py --speed 30 --power 190 --soh 0.78
pause
