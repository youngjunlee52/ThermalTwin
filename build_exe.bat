@echo off
rem Build the Windows distribution: release\ThermalTwin\ThermalTwin.exe (+ _internal) and release\ThermalTwin_v0.4_Windows.zip
rem Needs: 1_setup.bat done once. Build files go to %TEMP% to keep OneDrive quiet.
rem Code signing (see docs\CODE_SIGNING.md). Set ONE of these before running to sign every binary:
rem   set THERMALTWIN_SIGN_THUMBPRINT=<certificate thumbprint>     (USB token / cloud signing app / installed cert)
rem   set THERMALTWIN_SIGN_PFX=C:\path\codesign.pfx                (asks for the password)
rem Without them the build is UNSIGNED: PCs with Smart App Control ON will block it.
cd /d "%~dp0"
title ThermalTwin - Build exe
set "PY=backend\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo Run 1_setup.bat first.
  pause
  exit /b 1
)
"%PY%" -m pip install -q -r packaging\requirements-build.txt -r tools\requirements-can.txt || goto :fail
"%PY%" -m PyInstaller packaging\ThermalTwin.spec --noconfirm --clean --workpath "%TEMP%\thermaltwin_build" --distpath release || goto :fail
copy /y packaging\README_distribution.txt release\ThermalTwin\README.txt >nul
set "SIGNARGS="
if defined THERMALTWIN_SIGN_THUMBPRINT set "SIGNARGS=-Thumbprint %THERMALTWIN_SIGN_THUMBPRINT%"
if defined THERMALTWIN_SIGN_PFX set "SIGNARGS=-PfxPath "%THERMALTWIN_SIGN_PFX%""
if defined SIGNARGS (
  powershell -NoProfile -ExecutionPolicy Bypass -File packaging\sign_release.ps1 -Path release\ThermalTwin %SIGNARGS% || goto :fail
) else (
  echo [WARN] No signing certificate set: this build is UNSIGNED. See docs\CODE_SIGNING.md
)
powershell -NoProfile -Command "Compress-Archive -Path 'release\ThermalTwin' -DestinationPath 'release\ThermalTwin_v0.4_Windows.zip' -Force" || goto :fail
echo.
echo Done: release\ThermalTwin_v0.4_Windows.zip
pause
exit /b 0

:fail
echo Build failed. Copy the error lines above and ask for help.
pause
exit /b 1
