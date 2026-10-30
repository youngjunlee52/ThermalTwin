@echo off
rem (Optional) Retrain the PINN and rebuild sensor-placement / validation results (about 3 minutes).
cd /d "%~dp0backend"
title ThermalTwin - Retrain models
".venv\Scripts\python.exe" build_models.py
echo Done. Restart 2_start.bat to load the new models.
pause
