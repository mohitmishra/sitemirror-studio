#!/bin/bash
set -uo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
TARGET="$HOME/Applications/SiteMirror Studio.app"

if [ -d "$TARGET" ]; then
  open "$TARGET"
  exit 0
fi

echo "SiteMirror Studio is not installed yet."
echo "Running installer..."
"$ROOT/INSTALL-MAC-APP.command"
