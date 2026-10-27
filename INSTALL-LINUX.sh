#!/bin/bash
set -uo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo ""
echo "=============================================="
echo " SiteMirror Studio — Linux Installer"
echo "=============================================="
echo "This one-time setup installs Python (if needed),"
echo "all Python dependencies, and launches SiteMirror."
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
  echo "Python 3.10+ not found. Installing via system package manager..."
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -qq && sudo apt-get install -y -qq python3 python3-pip python3-venv >/dev/null 2>&1 || true
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y -q python3 python3-pip >/dev/null 2>&1 || true
  elif command -v yum >/dev/null 2>&1; then
    sudo yum install -y -q python3 python3-pip >/dev/null 2>&1 || true
  elif command -v pacman >/dev/null 2>&1; then
    sudo pacman -Sy --noconfirm python python-pip >/dev/null 2>&1 || true
  elif command -v brew >/dev/null 2>&1; then
    brew install python@3.12 >/dev/null 2>&1 || true
  fi
  PYTHON="$(command -v python3 || true)"
fi

if [ -z "$PYTHON" ] || [ "$PYTHON_OK" -eq 0 ]; then
  echo ""
  echo "ERROR: Could not install Python automatically."
  echo "Please install Python 3.10+ manually, then run this script again."
  exit 1
fi

MAJOR=$("$PYTHON" -c 'import sys; print(sys.version_info.major)' 2>/dev/null)
MINOR=$("$PYTHON" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null)
echo "✓ Using Python $MAJOR.$MINOR at $PYTHON"

# --- 2. Install Python dependencies ----------------------------------------
echo ""
echo "Installing Python dependencies (this may take a minute)..."
"$PYTHON" -m pip install --disable-pip-version-check --quiet -r "$ROOT/requirements-core.txt" || {
  echo "ERROR: Failed to install core requirements."
  exit 1
}
"$PYTHON" -m pip install --disable-pip-version-check --quiet -r "$ROOT/requirements-native.txt" || {
  echo "ERROR: Failed to install native requirements."
  exit 1
}
echo "✓ Python dependencies installed."

# --- 3. Verify imports -----------------------------------------------------
if ! "$PYTHON" -c 'import webview,fastapi,uvicorn,requests,bs4,lxml,playwright,tldextract' >/dev/null 2>&1; then
  echo "Installing Playwright Chromium..."
  "$PYTHON" -m playwright install chromium >/dev/null 2>&1 || true
fi

# --- 4. Launch -------------------------------------------------------------
echo ""
echo "✓ Installation complete!"
echo "✓ Projects: ~/.sitemirror-studio/data"
echo "✓ Logs:     ~/.sitemirror-studio/logs"
echo ""
echo "Launching SiteMirror Studio..."
exec "$PYTHON" "$ROOT/native_app.py"