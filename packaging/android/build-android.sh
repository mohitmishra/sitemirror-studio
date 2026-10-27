#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT/mobile"
API_URL="${SITEMIRROR_API_URL:-}"
MODE="debug"
if [ "${1:-}" = "--release" ]; then MODE="release"; fi
if [ -z "$API_URL" ]; then
  echo "Set SITEMIRROR_API_URL, e.g.:"
  echo "  export SITEMIRROR_API_URL=\"http://192.168.1.10:8787\""
  exit 1
fi
command -v npm >/dev/null || { echo "npm not found (Node 20+ required)"; exit 1; }
command -v java >/dev/null || { echo "java not found (JDK 17 required)"; exit 1; }
[ -d android ] || { echo "Run first: npx cap add android"; exit 1; }

# Bake backend URL where web/app.js can read it inside the WebView.
printf '{"apiUrl": "%s"}\n' "$API_URL" > ../web/sitemirror-config.json
npx cap sync android

if [ "$MODE" = "release" ]; then
  : "${ANDROID_KEYSTORE:?set ANDROID_KEYSTORE for --release}"
  : "${ANDROID_KEY_ALIAS:?set ANDROID_KEY_ALIAS for --release}"
fi

cd android
if [ "$MODE" = "release" ]; then
  ./gradlew assembleRelease \
    -Pandroid.injected.signing.store.file="$ANDROID_KEYSTORE" \
    -Pandroid.injected.signing.store.password="${ANDROID_KEYSTORE_PASSWORD:-}" \
    -Pandroid.injected.signing.key.alias="$ANDROID_KEY_ALIAS" \
    -Pandroid.injected.signing.key.password="${ANDROID_KEY_PASSWORD:-${ANDROID_KEYSTORE_PASSWORD:-}}"
  APK="app/build/outputs/apk/release/app-release.apk"
else
  ./gradlew assembleDebug
  APK="app/build/outputs/apk/debug/app-debug.apk"
fi
cd ..
rm -f ../web/sitemirror-config.json

# Keep a discoverable copy beside the Mac DMG and prior Android builds.
mkdir -p ../dist
NAME="SiteMirror-Studio-${SITEMIRROR_VERSION:-2.6.0-stable}-${MODE}.apk"
cp "android/$APK" "../dist/$NAME"
echo "OK ($MODE): mobile/android/$APK"
echo "APK copy: dist/$NAME"
echo "Backend baked in: $API_URL"
ls -lh "android/$APK" "../dist/$NAME"
