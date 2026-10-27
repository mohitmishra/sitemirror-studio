from __future__ import annotations
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn
from app import app as fastapi_app


def server():
    uvicorn.run(
        fastapi_app,
        host=os.getenv("SITEMIRROR_HOST", "127.0.0.1"),
        port=int(os.getenv("SITEMIRROR_PORT", "8787")),
        log_level="warning",
    )


def wait_port(port=8787):
    for _ in range(120):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=.2):
                return True
        except OSError:
            time.sleep(.1)
    return False


def main():
    threading.Thread(target=server, daemon=True).start()
    if not wait_port():
        raise RuntimeError("SiteMirror server did not start on port 8787")
    webbrowser.open("http://127.0.0.1:8787")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
