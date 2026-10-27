@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
echo.
echo =============================================
echo  SiteMirror Studio - Browser Mode
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
echo [2/3] Installing Playwright Chromium...
"%PY%" -m playwright install chromium >nul 2>&1

:: --- Start backend and wait for it to be ready -----------------------------
echo [3/3] Starting backend on http://127.0.0.1:8787 ...
"%PY%" app.py > "%TEMP%\sitemirror-backend.log" 2>&1 &
set BACKEND_PID=!

set READY=0
for /L %%i in (1,1,120) do (
  "%PY%" -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/api/health', timeout=2)" >nul 2>&1
  if errorlevel 1 (
    timeout /t 1 >nul
  ) else (
    set READY=1
    goto :opened
  )
)

:opened
if %READY%==1 (
  echo Backend is ready. Opening browser...
  start "" http://127.0.0.1:8787
) else (
  echo WARNING: Backend did not respond in time. Check %TEMP%\sitemirror-backend.log
  echo Starting browser anyway...
  start "" http://127.0.0.1:8787
)

echo.
echo SiteMirror Studio is running. Press Ctrl+C in this window to stop.
echo Backend log: %TEMP%\sitemirror-backend.log
pause
goto :eof
