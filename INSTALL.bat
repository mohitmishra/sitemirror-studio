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
call "%~dp0INSTALL-WINDOWS.bat"