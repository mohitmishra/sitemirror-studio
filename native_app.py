from __future__ import annotations

import os
import platform
import socket
import sys
import shutil
import threading
import time
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)

# Persistent projects/exports live outside the app bundle/install dir so
# updates do not delete them. Locations are platform-aware:
#   macOS   → ~/Library/Application Support + ~/Library/Logs
#   Windows → %LOCALAPPDATA%\SiteMirror Studio
#   Linux   → ~/.sitemirror-studio
_SYSTEM = platform.system().lower()
if _SYSTEM == "windows":
    _BASE = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "SiteMirror Studio"
    SUPPORT = _BASE
    LOGS = _BASE / "Logs"
elif _SYSTEM == "darwin":
    SUPPORT = Path.home() / "Library" / "Application Support" / "SiteMirror Studio"
    LOGS = Path.home() / "Library" / "Logs" / "SiteMirror Studio"
else:
    SUPPORT = Path.home() / ".sitemirror-studio"
    LOGS = SUPPORT / "logs"
SUPPORT.mkdir(parents=True, exist_ok=True)
LOGS.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("SITEMIRROR_DATA_DIR", str(SUPPORT / "data"))
os.environ.setdefault("SITEMIRROR_EXPORTS_DIR", str(SUPPORT / "exports"))
os.environ["SITEMIRROR_NATIVE"] = "1"


def free_port() -> int:
    # Use a small predictable local range while allowing multiple old/dev instances
    # to fail gracefully instead of colliding on one hard-coded port.
    for port in range(18787, 18798):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", port))
            sock.close()
            return port
        except OSError:
            sock.close()
            continue
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def main() -> int:
    smoke = os.getenv("SITEMIRROR_SMOKE") == "1"
    try:
        import webview
        import uvicorn
    except Exception as exc:
        # The .app shell also checks this, but keep the Python failure friendly.
        msg = (
            "SiteMirror native components are not installed.\n\n"
            "See the README for the one-time setup for your platform.\n\n"
            f"Details: {exc}"
        )
        if _SYSTEM == "windows":
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(0, msg, "SiteMirror Studio", 0x40)
            except Exception:
                print(msg, file=sys.stderr)
        elif _SYSTEM == "darwin":
            os.system("osascript -e 'display dialog " + repr(msg)[1:-1].replace('"', '\\"') + " with title \"SiteMirror Studio\" buttons {\"OK\"} default button \"OK\"' >/dev/null 2>&1")
        else:
            print(msg, file=sys.stderr)
        return 2

    # Import only after environment paths have been established.
    import app as app_module

    # The Mac desktop app is also used as the LAN backend for the Android/iOS
    # clients. Keep one documented, stable endpoint for that mode instead of a
    # random loopback-only port. A custom port/host can still be supplied for
    # development or smoke tests.
    default_port = 8787 if _SYSTEM == "darwin" else 0
    port = int(os.getenv("SITEMIRROR_PORT") or default_port) or free_port()
    host = os.getenv("SITEMIRROR_HOST", "0.0.0.0" if _SYSTEM == "darwin" else "127.0.0.1")
    os.environ["SITEMIRROR_PORT"] = str(port)
    config = uvicorn.Config(
        app_module.app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="SiteMirrorBackend", daemon=True)
    thread.start()

    health_url = f"http://127.0.0.1:{port}/api/health"
    for _ in range(120):
        try:
            with urlopen(health_url, timeout=0.5) as response:
                if response.status == 200:
                    break
        except Exception:
            time.sleep(0.1)
    else:
        return 3

    start_url = f"http://127.0.0.1:{port}/?native=1"
    print(f"SMOKE_PORT={port}", flush=True)
    print(f"SMOKE_URL={start_url}", flush=True)
    if smoke:
        # Headless CI/packaging check: prove the backend boots without opening GUI.
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        finally:
            server.should_exit = True
            thread.join(timeout=3)
        return 0
    window = webview.create_window(
        "SiteMirror Studio",
        start_url,
        width=1440,
        height=920,
        min_size=(1000, 680),
        background_color="#f5f6f8",
        text_select=True,
        confirm_close=False,
    )

    # Native window: Cocoa/WebKit on macOS, Edge WebView2 on Windows (default
    # backend when gui=None). Windows 10/11 ship WebView2; older systems need
    # the free WebView2 Runtime from Microsoft.
    # The crawler itself remains direct-first/headless; a separate visible browser is
    # created only when a target site explicitly requires human verification.
    try:
        webview.start(gui=("cocoa" if _SYSTEM == "darwin" else None), debug=False)
    finally:
        server.should_exit = True
        thread.join(timeout=3)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
