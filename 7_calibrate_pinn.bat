@echo off
rem Recalibrate the PINN core-temperature sensor with cell test data (core thermocouple).
rem Put CSV files in data\cell_test (columns: t_s, current_a, t_surf_c, t_core_c, and q_w or v_cell+soc).
rem No data yet? Run:  backend\.venv\Scripts\python.exe backend\calibrate_pinn.py --make-example data\cell_test_example
rem The model is replaced only if it is more accurate on the held-out test; the old model is backed up.
cd /d "%~dp0"
title ThermalTwin PINN Calibration
if not exist "data\cell_test\*.csv" (
  echo No CSV files in data\cell_test
  pause
  exit /b 1
)
"backend\.venv\Scripts\python.exe" -u backend\calibrate_pinn.py "data\cell_test\*.csv" --apply
echo.
echo Restart 2_start.bat to load the new model.
pause
