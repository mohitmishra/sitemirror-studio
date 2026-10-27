# SiteMirror Studio — Windows build (v2.6+)

A Windows `.exe` **cannot be produced on macOS**: PyInstaller has no
cross-compilation, and the native window needs the Windows WebView2 stack.
Build it on any Windows 10/11 PC (or VM), or use the CI job below — the whole
pipeline is checked in here.

## Option A — build on a Windows PC (5–10 min)

Prerequisites (one time):

1. Python 3.11+ from https://www.python.org/downloads/ (tick **Add to PATH**)
2. Microsoft Edge WebView2 Runtime — preinstalled on Win10/11; otherwise the
   free installer from Microsoft
3. *(Optional, single-file installer)* Inno Setup 6 from https://jrsoftware.org/

Then, from this repo folder:

```bat
packaging\windows\build-windows.bat
```

Output:

- `dist\SiteMirrorStudio\SiteMirrorStudio.exe` (+ bundled `web/`, Python 3.12,
  FastAPI engine — no Python install needed on the target PC)
- Optional: open `packaging\windows\installer.iss` in Inno Setup →
  `dist\SiteMirror-Studio-2.6.0-stable-windows-x64-setup.exe`

Projects/exports live in `%LOCALAPPDATA%\SiteMirror Studio`, so reinstalls
keep your data (same layout idea as the Mac `~/Library` paths).

Direct-HTTP crawling works out of the box. Chromium (for JS-heavy /
verification sites) is fetched by `build-windows.bat` via
`python -m playwright install chromium`; ships separately from the bundle.

## Option B — GitHub Actions (no Windows PC needed)

1. `git init`, commit, push this folder to a GitHub repo
2. Actions → **build-windows-exe** → Run workflow (or push a `v*` tag)
3. Download the `SiteMirrorStudio-windows-x64` artifact

See `.github/workflows/build-windows.yml`. For a signed public release you'd
add a code-signing cert as a repo secret (unsigned works, SmartScreen warns).

## Files

- `sitemirror-win.spec` — PyInstaller one-dir spec (mirrors the Mac spec)
- `build-windows.bat` — venv, deps, Playwright, PyInstaller
- `installer.iss` — optional Inno Setup single-file installer
