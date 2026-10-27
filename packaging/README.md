# SiteMirror Studio — Stable Release Line (v2.6+)

Goal: one versioned source tree producing:

- macOS: standalone `SiteMirror Studio.app` + DMG (no system-python / pip step)
- Android: sideloadable `app-debug.apk` / `app-release.apk` (Capacitor client)
- Windows `.exe`: full pipeline in `packaging/windows/` (PyInstaller one-dir +
  optional Inno Setup installer + `.github/workflows/build-windows.yml` CI).
  It MUST run on Windows (local PC/VM) or GitHub Actions — PyInstaller cannot
  cross-compile from macOS. See `packaging/windows/README.md`.
- iOS `.ipa`: generated Capacitor iOS project is in `mobile/ios/`; build/signing pipeline is in `packaging/ios/`. A signed IPA still needs full Xcode + an Apple Developer Team (not buildable from the current Command Line Tools-only Mac).

## Why Mac + APK need different architectures

Current v2.5 source is a Python FastAPI backend + static `web/` UI:

- `app.py` + `engine/` requires Python 3.10+, `requests/bs4/lxml/tldextract`,
  optional `playwright` + Chromium for JS-heavy sites.
- `native_app.py` + `pywebview` gives the macOS Cocoa/WebKit window.
- `mobile/` is only a Capacitor shell stub (`webDir: ../web`, no
  `android/` / `ios/` platforms, no bundled backend).

Implication:

- macOS standalone is feasible: bundle the Python backend with PyInstaller.
  Playwright/Chromium makes the bundle very large (~300-500MB), so the
  stable Mac build keeps direct HTTP crawling always available and treats
  Playwright as optional.
- Android cannot run this Python + Playwright backend on-device in a
  practical way. The stable APK is therefore a Capacitor WebView client
  that talks to a SiteMirror backend over HTTP(S):
  same laptop Docker container, home server, or hosted VPS.
  Full on-device crawling on Android is explicitly out of scope for v2.6.

See:

- `packaging/mac/README.md` for the Mac bundle pipeline.
- `packaging/android/README.md` for the APK client pipeline.
- `packaging/RELEASE-CHECKLIST.md` for versioning/signing/upload steps.
