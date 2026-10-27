@echo off
setlocal
cd /d "%~dp0"
echo.
echo =============================================
echo  SiteMirror Studio - Windows
echo =============================================
set PY=python
where py >nul 2>nul && set PY=py -3
%PY% -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :err
%PY% -m pip install --disable-pip-version-check pywebview >nul 2>nul
%PY% desktop\launcher.py
goto :eof
:err
echo.
echo Setup failed. Confirm Python 3.9+ is installed and available in PATH.
pause
