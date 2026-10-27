@echo off
setlocal
rem SiteMirror Studio — Windows build (PyInstaller one-dir + optional Inno installer).
rem MUST run on Windows: PyInstaller cannot cross-compile from macOS/Linux.
cd /d "%~dp0\..\.."
where python >nul 2>nul || (echo ERROR: install Python 3.11+ from https://www.python.org/downloads/ and retry & exit /b 1)
if not exist .venv-win python -m venv .venv-win
call .venv-win\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements-core.txt -r requirements-native.txt pyinstaller
rem Chromium for JS-heavy / protected sites (direct-HTTP crawling works without it).
python -m playwright install chromium
pyinstaller packaging\windows\sitemirror-win.spec --noconfirm --clean
echo.
echo DONE: dist\SiteMirrorStudio\SiteMirrorStudio.exe
echo For a single-file installer, open packaging\windows\installer.iss in Inno Setup 6 and build.
