# SiteMirror Studio — macOS Stable Build (v2.6+)

Current v2.5 `SiteMirror Studio.app` is NOT standalone. It is a bash shim:

- `Contents/MacOS/SiteMirrorStudio` calls `command -v python3`
- then `python native_app.py`, which imports system `webview,fastapi,uvicorn`
- `INSTALL-MAC-APP.command` pip-installs into the system Python

Result: breaks when the user upgrades Python, has no bundled Chromium,
no versioned DMG, only ad-hoc codesign.

## Stable target

- One-folder PyInstaller bundle: `dist/SiteMirror Studio.app`
- Bundled `web/`, `engine/`, `app.py`, `native_app.py`
- Direct crawler works offline/out-of-box
- Playwright/Chromium optional: if missing, app still starts and uses
  direct-first discovery; browser fallback shows a friendly message
- Version comes from `packaging/VERSION`
- DMG created with `hdiutil`, ad-hoc signed by default,
  Developer-ID signed + notarized only if env provides identity

## Build on a Mac with Python 3.11/3.12

```bash
python3 -m venv .venv-pack
source .venv-pack/bin/activate
pip install -r requirements-core.txt -r requirements-native.txt pyinstaller
./packaging/mac/build-mac.sh
```

Output:

- `dist/SiteMirror Studio.app`
- `dist/SiteMirror-Studio-2.6.0-stable-mac-arm64.dmg` (arch auto-detected)

## Files

- `sitemirror-mac.spec` — PyInstaller spec (onedir, windowed)
- `build-mac.sh` — venv check, clean, pyinstaller, DMG, ad-hoc sign, smoke test
- `entitlements.plist` — minimal hardened-runtime entitlements
