@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
echo.
echo =============================================
echo  SiteMirror Studio - Windows Installer
echo =============================================
echo.
echo This one-time setup installs Python (if needed),
echo all Python dependencies, and launches SiteMirror.
echo.

:: --- 1. Find or install Python 3.11+ via winget ----------------------------
set PY=
where python >nul 2>&1 && set PY=python
where py >nul 2>&1 && if not defined PY set PY=py

if defined PY (
  "%PY%" --version >nul 2>&1
  if errorlevel 1 set PY=
)

if not defined PY (
  echo [1/5] Python not found. Installing via winget...
  winget install Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements >nul 2>&1 || (
    echo   winget not available or failed. Trying chocolatey...
    choco install python --version 3.12.0 -y >nul 2>&1
  )
  where python >nul 2>&1 && set PY=python
  where py >nul 2>&1 && if not defined PY set PY=py
)

if not defined PY (
  echo.
  echo =============================================
  echo  Could not install Python automatically.
  echo =============================================
  echo Please install Python 3.11+ manually from:
  echo   https://www.python.org/downloads/windows/
  echo Then run this installer again.
  echo.
  pause
  goto :eof
)

for /f "tokens=*" %%a in ('"%PY%" --version 2^>^&1') do set PYVER=%%a
echo [OK] %PYVER% found.

:: --- 2. Close older SiteMirror backend instances ---------------------------
echo [2/5] Checking for old SiteMirror processes...
"%PY%" -c "import urllib.request; [print('killed') for p in []]" >nul 2>&1
:: Kill any existing app.py on port 8787
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr :8787') do (
  taskkill /PID %%a /F >nul 2>&1
)

:: --- 3. Install Python dependencies ----------------------------------------
echo [3/5] Installing Python dependencies (this may take a minute)...
"%PY%" -m pip install --disable-pip-version-check --upgrade pip >nul 2>&1
"%PY%" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :err_pip
"%PY%" -m pip install --disable-pip-version-check pywebview >nul 2>&1
if errorlevel 1 goto :err_pip
echo [OK] Core dependencies installed.

:: --- 4. Install Playwright Chromium ----------------------------------------
echo [4/5] Installing Playwright Chromium (for protected / JS-heavy sites)...
"%PY%" -m playwright install chromium >nul 2>&1
if errorlevel 1 (
  echo   WARNING: Playwright Chromium install failed.
  echo   Direct HTTP crawling still works; JS-heavy sites may need verification.
) else (
  echo [OK] Playwright Chromium installed.
)

:: --- 5. Verify imports -----------------------------------------------------
echo [5/5] Verifying installation...
"%PY%" -c "import webview,fastapi,uvicorn,requests,bs4,lxml,playwright,tldextract; print('All imports OK')" >nul 2>&1
if errorlevel 1 (
  echo   WARNING: Some imports missing. The app may have limited functionality.
) else (
  echo [OK] All imports verified.
)

:: --- Launch ----------------------------------------------------------------
echo.
echo =============================================
echo  Installation complete! Launching SiteMirror...
echo =============================================
echo.
"%PY%" native_app.py
goto :eof

:err_pip
echo.
echo ERROR: Failed to install Python dependencies.
echo Check your internet connection and try again.
echo.
pause
goto :eof