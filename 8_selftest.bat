@echo off
rem Self-test: checks the main features and past bug fixes (1-2 minutes). Run 1_setup.bat first.
cd /d "%~dp0"
title ThermalTwin Self-test
"backend\.venv\Scripts\python.exe" -u tools\selftest.py
pause
