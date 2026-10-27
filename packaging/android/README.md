# SiteMirror Studio — Android APK (Capacitor client, v2.6+)

Mobile `mobile/` today is only a stub: `package.json` + `capacitor.config.ts`
with `webDir: ../web` and NO `android/` platform, NO backend.

A practical APK cannot bundle this Python + Playwright crawler on-device.
So v2.6 APK = Capacitor WebView client for the existing `web/` UI, talking
to a reachable SiteMirror backend:

- easiest: backend on your laptop (`docker compose up`, port 8787) + phone on same Wi-Fi
- better: backend on home server / VPS with HTTPS
- the APK itself does NOT crawl locally; it is a remote-control/viewer client

## Prerequisites

- Node 20+, Java 17, Android Studio SDK + platform-tools
- backend reachable at e.g. `http://192.168.1.10:8787` or `https://sitemirror.example.com`

## Build debug APK (sideloadable)

```bash
cd mobile
npm install
npx cap add android   # first time only, creates android/
# point the client at your backend:
export SITEMIRROR_API_URL="http://192.168.1.10:8787"
npm run cap:sync
./packaging/android/build-android.sh
```

Outputs (on the machine that has Android SDK):

- `mobile/android/app/build/outputs/apk/debug/app-debug.apk`

For a signed release APK/AAB you need a Java keystore:

```bash
export SITEMIRROR_API_URL="https://sitemirror.example.com"
export ANDROID_KEYSTORE=/path/to/sitemirror.jks
export ANDROID_KEY_ALIAS=sitemirror
# ANDROID_KEYSTORE_PASSWORD / ANDROID_KEY_PASSWORD via env or prompt
./packaging/android/build-android.sh --release
```

## How the API URL is baked in

`packaging/android/build-android.sh`:

1. copies `../web/` into Capacitor `webDir` (already `../web`)
2. writes `mobile/www-sitemirror-config.json` → later read by `web/app.js`
   (`?api=` override, then `localStorage sitemirror_api`, then baked config)
3. runs `npx cap sync android`
4. runs `./gradlew assembleDebug` (or `assembleRelease` with `--release`)

In the app: Settings/landing keeps working; if backend unreachable you get
a friendly toast, not a blank screen.

## Play Store / sideload notes

- debug APK installs directly (`adb install app-debug.apk`), no Play signing needed
- release to Play needs AAB + Play App Signing; see RELEASE-CHECKLIST.md
- Cleartext HTTP (`http://192.168...`) needs `usesCleartextTraffic` — already
  handled in `build-android.sh` via manifest patch for debug builds only.
  Use HTTPS for anything outside your LAN.
