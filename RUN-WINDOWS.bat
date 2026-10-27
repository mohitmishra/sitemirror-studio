@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
echo.
echo =============================================
echo  SiteMirror Studio - Windows
echo =============================================
echo.

:: --- Find or install Python 3.11+ ------------------------------------------
set PY=
where python >nul 2>&1 && set PY=python
where py >nul 2>&1 && set PY=py -3

if defined PY (
  "%PY%" --version >nul 2>&1
  if errorlevel 1 set PY=
)

if not defined PY (
  echo Python not found. Run INSTALL-WINDOWS.bat first to install Python + all deps.
  echo Or install Python 3.11+ from https://www.python.org/downloads/windows/
  echo.
  pause
  goto :eof
)

:: --- Install missing dependencies ------------------------------------------
echo [1/3] Installing Python dependencies...
"%PY%" -m pip install --disable-pip-version-check -r requirements.txt >nul 2>&1
if errorlevel 1 (
  echo   WARNING: Some dependencies may be missing.
)
echo [2/3] Installing pywebview...
"%PY%" -m pip install --disable-pip-version-check pywebview >nul 2>&1
echo [3/3] Installing Playwright Chromium...
"%PY%" -m playwright install chromium >nul 2>&1

:: --- Launch native window --------------------------------------------------
echo.
echo Starting SiteMirror Studio native window...
"%PY%" native_app.py
goto :eof
