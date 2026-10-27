# Release Checklist — v2.6.0-stable (Mac + Android)

Source of truth for version: `packaging/VERSION` (currently `2.6.0-stable`).

## 1. Pre-flight (both tracks)
- [ ] `git status` clean; tag `v2.6.0-stable` matches `packaging/VERSION`
- [ ] `web/sw.js` CACHE matches version (`sitemirror-ui-v2.6`)
- [ ] `app.py` FastAPI `version=` matches (`2.6.0`)
- [ ] No secrets in `web/sitemirror-config.json` (only backend URL, no tokens —
      v2.6 backend has no auth; do NOT expose it to the public internet)
- [ ] Backend CORS: v2.6 `app.py` uses `allow_origins=["*"]`. For a LAN-only
      APK backend this is acceptable; for any hosted backend, restrict to the
      app origin before release.

## 2. Mac standalone
- [ ] Build on Mac (arm64 + x86_64 if you ship both):
      `./packaging/mac/build-mac.sh`
- [ ] Artifacts: `dist/SiteMirror Studio.app`,
      `dist/SiteMirror-Studio-2.6.0-stable-mac-<arch>.dmg` + `.sha256.txt`
- [ ] Test on a clean Mac (no dev Python): open app, paste URL, complete a
      small crawl, export ZIP, Show-in-Finder
- [ ] Signing:
  - ad-hoc (`codesign -s -`) = fine for direct download, Gatekeeper will warn
  - Developer ID + `notarytool submit` required for no-warning distribution

## 3. Android APK (client)
- [ ] Backend deployed first (laptop Docker / home server / VPS) and reachable
      from the phone; note exact `SITEMIRROR_API_URL`
- [ ] `cd mobile && npm install && npx cap add android` (first time)
- [ ] `export SITEMIRROR_API_URL=...; ./packaging/android/build-android.sh`
      → `mobile/android/app/build/outputs/apk/debug/app-debug.apk`
- [ ] `adb install` on a real device on the same network; start crawl, open
      project, verify backend URL baked in works
- [ ] Release: create keystore once, keep backup OFF-machine,
      `./packaging/android/build-android.sh --release`; for Play, build AAB
      (`./gradlew bundleRelease`) and enroll in Play App Signing

## 4. Publish
- [ ] GitHub Release `v2.6.0-stable`: attach DMG(s) + `.sha256.txt` + debug APK
      (or AAB), plus backend `docker-compose` pointer for APK users
- [ ] Release notes must state: Mac = full crawler; APK = remote client,
      needs reachable backend (in-app Connect panel, no rebuild); Windows EXE
      via `packaging/windows` (Windows machine or CI — never from macOS);
      iOS IPA NOT in this release
