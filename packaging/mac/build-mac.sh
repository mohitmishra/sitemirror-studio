#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
VERSION="$(tr -d ' \t\n\r' < packaging/VERSION)"
ARCH="$(uname -m)"
APP="dist/SiteMirror Studio.app"
DMG="dist/SiteMirror-Studio-${VERSION}-mac-${ARCH}.dmg"

echo "==> SiteMirror Studio macOS stable build ${VERSION} (${ARCH})"
# Prefer the release venv; fall back to PATH python3.
if [ -x "$ROOT/.venv-pack/bin/python" ]; then
  export PATH="$ROOT/.venv-pack/bin:$PATH"
fi
command -v python3 >/dev/null || { echo "python3 not found"; exit 1; }
python3 -c 'import sys; print("build python:", sys.executable)'
python3 -c 'import PyInstaller, webview, fastapi, uvicorn' || {
  echo "Missing deps. Run: pip install -r requirements-core.txt -r requirements-native.txt pyinstaller"
  exit 1
}

rm -rf build "dist/SiteMirror Studio" "$APP" "$DMG"
pyinstaller packaging/mac/sitemirror-mac.spec --noconfirm

# Ad-hoc sign by default; real Developer-ID signing happens in CI/release box.
if [ -n "${APPLE_DEVELOPER_IDENTITY:-}" ]; then
  codesign --force --deep --options runtime \
    --entitlements packaging/mac/entitlements.plist \
    --sign "$APPLE_DEVELOPER_IDENTITY" "$APP"
else
  codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || true
fi

# DMG
rm -f "$DMG"
hdiutil create -volname "SiteMirror Studio ${VERSION}" \
  -srcfolder "$APP" -ov -format UDZO "$DMG"

# Smoke test: backend boots and serves /api/health (headless, no GUI).
# NOTE: the bundled binary may take ~10-20s on first boot (x86_64 + cold
# .app), so give it a long grace period after SMOKE_PORT appears.
rm -f /tmp/sitemirror-mac-smoke.log
# Free the default smoke port: a previous smoke run may still hold it.
pkill -f "SiteMirror Studio.app/Contents/MacOS/SiteMirrorStudio" 2>/dev/null || true
sleep 2
SITEMIRROR_SMOKE=1 "$APP/Contents/MacOS/SiteMirrorStudio" >/tmp/sitemirror-mac-smoke.log 2>&1 &
SMOKE_PID=$!
SMOKE_PORT=""
SMOKE_OK=0
for _ in $(seq 1 150); do
  SMOKE_PORT="$(grep -Eo 'SMOKE_PORT=[0-9]+' /tmp/sitemirror-mac-smoke.log 2>/dev/null | tail -n 1 | cut -d= -f2 || true)"
  if [ -n "$SMOKE_PORT" ] && curl -fsS -m 2 "http://127.0.0.1:${SMOKE_PORT}/api/health" >/dev/null 2>&1; then
    SMOKE_OK=1; break
  fi
  # Fall back to the default local ports if log parsing lags.
  if curl -fsS -m 2 http://127.0.0.1:18787/api/health >/dev/null 2>&1 \
  || curl -fsS -m 2 http://127.0.0.1:8787/api/health >/dev/null 2>&1; then
    SMOKE_OK=1; break
  fi
  if ! kill -0 $SMOKE_PID 2>/dev/null; then break; fi
  sleep 1
done
kill $SMOKE_PID >/dev/null 2>&1 || true
wait $SMOKE_PID 2>/dev/null || true
if [ "$SMOKE_OK" != "1" ]; then
  echo "Smoke test did not detect /api/health. See /tmp/sitemirror-mac-smoke.log"
  echo "Build artifact still created: $APP / $DMG"
  exit 2
fi

echo "OK: $APP"
echo "OK: $DMG"
shasum -a 256 "$DMG" | tee "$DMG.sha256.txt"
