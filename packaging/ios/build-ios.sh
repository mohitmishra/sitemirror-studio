#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
MOBILE="$ROOT/mobile"
OUTPUT="$ROOT/dist/ios"
ARCHIVE="$OUTPUT/SiteMirror-Studio.xcarchive"
EXPORT_OPTIONS="$OUTPUT/ExportOptions.plist"

if ! command -v xcodebuild >/dev/null 2>&1; then
  echo "ERROR: Full Xcode is required. Install Xcode from the Mac App Store, then run: sudo xcode-select -s /Applications/Xcode.app" >&2
  exit 1
fi
if ! xcodebuild -version >/dev/null 2>&1; then
  echo "ERROR: xcodebuild is present but the active developer directory is not full Xcode." >&2
  exit 1
fi

cd "$MOBILE"
npx cap sync ios

mkdir -p "$OUTPUT"
rm -rf "$ARCHIVE"

DEVELOPMENT_TEAM="${DEVELOPMENT_TEAM:-}"
CODE_SIGN_STYLE="${CODE_SIGN_STYLE:-Automatic}"
if [ -n "$DEVELOPMENT_TEAM" ]; then
  xcodebuild \
    -project ios/App/App.xcodeproj \
    -scheme App \
    -configuration Release \
    -archivePath "$ARCHIVE" \
    -destination 'generic/platform=iOS' \
    DEVELOPMENT_TEAM="$DEVELOPMENT_TEAM" \
    CODE_SIGN_STYLE="$CODE_SIGN_STYLE" \
    CODE_SIGNING_ALLOWED=YES \
    archive
else
  echo "WARNING: DEVELOPMENT_TEAM is not set; creating an unsigned archive."
  xcodebuild \
    -project ios/App/App.xcodeproj \
    -scheme App \
    -configuration Release \
    -archivePath "$ARCHIVE" \
    -destination 'generic/platform=iOS' \
    CODE_SIGN_STYLE=Manual \
    CODE_SIGNING_ALLOWED=NO \
    archive
fi

if [ -n "$DEVELOPMENT_TEAM" ]; then
  cat > "$EXPORT_OPTIONS" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>teamID</key><string>$DEVELOPMENT_TEAM</string>
  <key>method</key><string>development</string>
  <key>signingStyle</key><string>$CODE_SIGN_STYLE</string>
  <key>stripSwiftSymbols</key><true/>
</dict></plist>
PLIST
  rm -f "$OUTPUT"/*.ipa
  xcodebuild -exportArchive -archivePath "$ARCHIVE" -exportOptionsPlist "$EXPORT_OPTIONS" -exportPath "$OUTPUT"
  echo "Signed IPA created in: $OUTPUT"
else
  echo "Unsigned archive created in: $ARCHIVE"
  echo "It cannot be installed on an iPhone until signed with an Apple Developer Team."
fi
