#!/bin/bash
set -uo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

# --- Find or install Python 3.10+ ------------------------------------------
PYTHON="$(command -v python3 || true)"
if [ -z "$PYTHON" ]; then
  if command -v brew >/dev/null 2>&1; then
    brew install python@3.12 >/dev/null 2>&1 || true
    PYTHON="$(command -v python3 || true)"
  fi
fi
if [ -z "$PYTHON" ]; then
  echo "Python 3.10+ not found. Run INSTALL-MAC-APP.command first."
  exit 1
fi

# --- Install missing dependencies ------------------------------------------
if ! "$PYTHON" -c 'import webview,fastapi,uvicorn,requests,bs4,lxml,playwright,tldextract' >/dev/null 2>&1; then
  echo "Installing missing dependencies..."
  "$PYTHON" -m pip install --disable-pip-version-check --quiet -r "$ROOT/requirements-core.txt" 2>/dev/null || true
  "$PYTHON" -m pip install --disable-pip-version-check --quiet -r "$ROOT/requirements-native.txt" 2>/dev/null || true
  "$PYTHON" -m playwright install chromium >/dev/null 2>&1 || true
fi

echo "Starting SiteMirror Studio native Mac window..."
exec "$PYTHON" "$ROOT/native_app.py"
