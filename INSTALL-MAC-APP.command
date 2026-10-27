#!/bin/bash
set -uo pipefail
IFS=$'\n\t'

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo ""
echo "=============================================="
echo " SiteMirror Studio — Native Mac Installer"
echo "=============================================="
echo "This one-time setup installs Python (if needed),"
echo "all Python dependencies, and the SiteMirror app."
echo ""

# --- 1. Find or install Python 3.10+ ---------------------------------------
PYTHON="$(command -v python3 || true)"
PYTHON_OK=0

if [ -n "$PYTHON" ]; then
  MAJOR=$("$PYTHON" -c 'import sys; print(sys.version_info.major)' 2>/dev/null || echo 0)
  MINOR=$("$PYTHON" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null || echo 0)
  if [ "$MAJOR" -eq 3 ] && [ "$MINOR" -ge 10 ]; then
    PYTHON_OK=1
    echo "✓ Python $MAJOR.$MINOR found at $PYTHON"
  fi
fi

if [ "$PYTHON_OK" -eq 0 ]; then
  echo "Python 3.10+ not found. Installing via Homebrew..."
  if command -v brew >/dev/null 2>&1; then
    brew install python@3.12 >/dev/null 2>&1 || true
    PYTHON="$(command -v python3 || true)"
  else
    echo "Homebrew not found. Installing Homebrew first..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" >/dev/null 2>&1 || true
    if command -v brew >/dev/null 2>&1; then
      brew install python@3.12 >/dev/null 2>&1 || true
      PYTHON="$(command -v python3 || true)"
    fi
  fi
fi

if [ -z "$PYTHON" ] || [ "$PYTHON_OK" -eq 0 ]; then
  echo ""
  echo "=============================================="
  echo "  Could not install Python automatically."
  echo "=============================================="
  echo "Please install Python 3.10+ manually:"
  echo "  1. Download from https://www.python.org/downloads/macos/"
  echo "  2. Install it, then run this script again."
  echo ""
  /usr/bin/osascript -e 'display dialog "SiteMirror needs Python 3.10+. Please install Python from python.org, then run this installer again." with title "SiteMirror Studio" buttons {"OK"} default button "OK"' 2>/dev/null || true
  exit 1
fi

MAJOR=$("$PYTHON" -c 'import sys; print(sys.version_info.major)' 2>/dev/null)
MINOR=$("$PYTHON" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null)
echo "✓ Using Python $MAJOR.$MINOR at $PYTHON"

# --- 2. Close older SiteMirror backend instances ---------------------------
"$ROOT/CLEAN-OLD-SITEMIRROR.command" >/dev/null 2>&1 || true
sleep 0.5

# --- 3. Install Python dependencies ----------------------------------------
echo ""
echo "Installing Python dependencies (this may take a minute)..."

if "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info < (3,10) else 1)' >/dev/null 2>&1; then
  "$PYTHON" -m pip install --disable-pip-version-check --quiet 'urllib3<2' 2>/dev/null || true
fi

"$PYTHON" -m pip install --disable-pip-version-check --quiet -r "$ROOT/requirements-core.txt" || {
  echo "ERROR: Failed to install core requirements."
  exit 1
}

"$PYTHON" -m pip install --disable-pip-version-check --quiet -r "$ROOT/requirements-native.txt" || {
  echo "ERROR: Failed to install native requirements."
  exit 1
}

echo "✓ Python dependencies installed."

# --- 4. Verify all imports -------------------------------------------------
if ! "$PYTHON" -c 'import webview,fastapi,uvicorn,requests,bs4,lxml,playwright,tldextract' >/dev/null 2>&1; then
  echo ""
  echo "WARNING: Some native imports missing. Installing playwright browsers..."
  "$PYTHON" -m playwright install chromium >/dev/null 2>&1 || true
fi

# --- 5. Install the .app bundle --------------------------------------------
TARGET="$HOME/Applications/SiteMirror Studio.app"
mkdir -p "$HOME/Applications"

if [ -d "$ROOT/SiteMirror Studio.app" ]; then
  rm -rf "$TARGET"
  cp -R "$ROOT/SiteMirror Studio.app" "$TARGET"
  xattr -dr com.apple.quarantine "$TARGET" 2>/dev/null || true
  /usr/bin/codesign --force --deep --sign - "$TARGET" >/dev/null 2>&1 || true
  echo "✓ App installed to: $TARGET"
else
  echo "⚠ No prebuilt .app found in source folder. You can still run from Terminal:"
  echo "    $PYTHON $ROOT/native_app.py"
fi

# --- 6. Launch -------------------------------------------------------------
echo ""
echo "✓ Installation complete!"
echo "✓ Projects: $HOME/Library/Application Support/SiteMirror Studio"
echo "✓ Logs:     $HOME/Library/Logs/SiteMirror Studio"
echo ""
echo "Launching SiteMirror Studio..."
open "$TARGET" 2>/dev/null || exec "$PYTHON" "$ROOT/native_app.py"
echo ""
echo "You can close this Terminal window. Future launches: open the SiteMirror Studio app."
