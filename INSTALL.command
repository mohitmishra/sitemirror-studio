#!/bin/bash
set -uo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

OS="$(uname -s)"
echo ""
echo "=============================================="
echo "  SiteMirror Studio — Universal Installer"
echo "=============================================="
echo "Detected OS: $OS"
echo ""

case "$OS" in
  Darwin*)
    echo "Launching macOS installer..."
    exec "$ROOT/INSTALL-MAC-APP.command"
    ;;
  Linux*)
    echo "Launching Linux installer..."
    exec "$ROOT/INSTALL-LINUX.sh"
    ;;
  *)
    echo "Unsupported OS: $OS"
    echo "Please install Python 3.10+ manually, then run:"
    echo "  pip install -r requirements-core.txt -r requirements-native.txt"
    echo "  python native_app.py"
    exit 1
    ;;
esac