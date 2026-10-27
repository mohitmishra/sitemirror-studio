# iOS / IPA build

The iOS project is generated at `mobile/ios/App/App.xcodeproj`. It is a Capacitor client for the SiteMirror backend; the Python crawler does not run on iPhone or iPad.

## Requirements

- macOS with full Xcode (not only Command Line Tools)
- Apple Developer Program membership for a device-installable signed IPA
- An Apple Developer Team ID and a provisioning profile, or automatic signing with a connected Apple device
- The Mac crawler running on the same network, for example `SITEMIRROR_HOST=0.0.0.0 SITEMIRROR_PORT=8787 .venv-pack/bin/python app.py`

## Build

From the project root:

```bash
export DEVELOPER_TEAM="ABCDE12345"
./packaging/ios/build-ios.sh
```

For an unsigned archive, omit `DEVELOPMENT_TEAM`:

```bash
./packaging/ios/build-ios.sh
```

The signed IPA is written under `dist/ios/`. The unsigned `.xcarchive` cannot be installed on an iPhone.

The app permits HTTP connections to the local crawler and declares the iOS local-network usage prompt. HTTPS is recommended when the backend is outside a trusted private network.
