from __future__ import annotations

import os
import socket
import sys
import shutil
import threading
import time
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)

# Persistent projects/exports live outside the .app bundle so updates do not delete them.
SUPPORT = Path.home() / "Library" / "Application Support" / "SiteMirror Studio"
LOGS = Path.home() / "Library" / "Logs" / "SiteMirror Studio"
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
    try:
        import webview
        import uvicorn
    except Exception as exc:
        # The .app shell also checks this, but keep the Python failure friendly.
        msg = (
            "SiteMirror native components are not installed.\n\n"
            "Run INSTALL-MAC-APP.command once, then open SiteMirror Studio again.\n\n"
            f"Details: {exc}"
        )
        os.system("osascript -e 'display dialog " + repr(msg)[1:-1].replace('"', '\\"') + " with title \"SiteMirror Studio\" buttons {\"OK\"} default button \"OK\"' >/dev/null 2>&1")
        return 2

    # Import only after environment paths have been established.
    import app as app_module

    port = free_port()
    os.environ["SITEMIRROR_PORT"] = str(port)
    config = uvicorn.Config(
        app_module.app,
        host="127.0.0.1",
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

    # Native Cocoa/WebKit window: no browser address bar, no browser tabs.
    # The crawler itself remains direct-first/headless; a separate visible browser is
    # created only when a target site explicitly requires human verification.
    try:
        webview.start(gui="cocoa", debug=False)
    finally:
        server.should_exit = True
        thread.join(timeout=3)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
