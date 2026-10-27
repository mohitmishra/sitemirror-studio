# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for SiteMirror Studio Windows stable (v2.6+).

Build with (on Windows):
    pyinstaller packaging/windows/sitemirror-win.spec

Produces a one-folder bundle so `web/`, `engine/` data and native
dependencies stay inspectable and Playwright can remain optional.
"""
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent.parent  # packaging/windows -> repo root
VERSION_FILE = ROOT / "packaging" / "VERSION"
VERSION = VERSION_FILE.read_text(encoding="utf-8").strip() if VERSION_FILE.exists() else "2.6.0-stable"

a = Analysis(
    [str(ROOT / "native_app.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(ROOT / "web"), "web"),
        (str(ROOT / "engine"), "engine"),
    ],
    hiddenimports=[
        "app",
        "engine.crawler",
        "engine.compare",
        "engine.metadata",
        "engine.discovery",
        "engine.seo",
        "engine.browser",
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.protocols.http.auto",
        "webview",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "notebook",
        "pytest",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SiteMirrorStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="SiteMirror Studio",
)
