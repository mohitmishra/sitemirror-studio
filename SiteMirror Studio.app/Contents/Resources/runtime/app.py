from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import threading
import webbrowser
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from engine.crawler import ProjectStore, SiteCrawler, zip_project
from engine.compare import compare_projects
from engine.metadata import wayback_first_seen

ROOT = Path(__file__).resolve().parent
DATA = Path(os.getenv("SITEMIRROR_DATA_DIR", str(ROOT / "data"))).expanduser().resolve()
EXPORTS = Path(os.getenv("SITEMIRROR_EXPORTS_DIR", str(ROOT / "exports"))).expanduser().resolve()
WEB = ROOT / "web"
DATA.mkdir(parents=True, exist_ok=True)
EXPORTS.mkdir(parents=True, exist_ok=True)
store = ProjectStore(DATA)

app = FastAPI(title="SiteMirror Studio", version="2.5.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

ACTIVE_CRAWLS: dict[str, SiteCrawler] = {}
ACTIVE_CRAWLS_LOCK = threading.Lock()


def normalize_user_url(value: str) -> str:
    """Accept a typed domain/path and turn it into a crawlable absolute HTTP(S) URL."""
    raw = (value or "").strip()
    if not raw:
        raise HTTPException(400, "URL is required")
    if raw.startswith("//"):
        raw = "https:" + raw
    elif "://" not in raw:
        raw = "https://" + raw
    parsed = urlparse(raw)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise HTTPException(400, "Enter a valid website domain or http(s) URL")
    return parsed._replace(scheme=parsed.scheme.lower()).geturl()


def _start_crawler(pid: str, path: Path, url: str, max_pages: int = 1000) -> bool:
    crawler = SiteCrawler(
        store, pid, path, url,
        max_pages=max(1, min(max_pages, 5000)),
    )
    with ACTIVE_CRAWLS_LOCK:
        if pid in ACTIVE_CRAWLS:
            return False
        ACTIVE_CRAWLS[pid] = crawler

    def runner():
        try:
            crawler.run()
        finally:
            with ACTIVE_CRAWLS_LOCK:
                ACTIVE_CRAWLS.pop(pid, None)

    thread = threading.Thread(target=runner, daemon=True, name=f"SiteMirror-{pid}")
    thread.start()
    return True


class CrawlRequest(BaseModel):
    url: str
    max_pages: int = 1000


class CompareRequest(BaseModel):
    source_project: str
    target_project: str


class OpenPathRequest(BaseModel):
    path: str = "/"


def _friendly_export_name(pid: str) -> str:
    state = store.get(pid) or {}
    host = urlparse(state.get("url") or "").hostname or pid
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in host).strip("-.") or pid
    return f"{safe}-{pid[:6]}-sitemirror.zip"


def _build_export(pid: str) -> Path:
    project_dir = DATA / pid
    if not project_dir.exists():
        raise HTTPException(404, "Project not found")
    out = EXPORTS / _friendly_export_name(pid)
    zip_project(project_dir, out)
    return out


def _is_local_request(request: Request) -> bool:
    if request.client is None:
        return True
    return request.client.host in {"127.0.0.1", "::1", "localhost", "testclient"}


def _require_local(request: Request):
    if not _is_local_request(request):
        raise HTTPException(403, "Desktop action is only available from this computer")


def _reveal(path: Path, select_file: bool = False):
    path = path.expanduser().resolve()
    if not path.exists():
        raise HTTPException(404, f"Path not found: {path}")
    system = platform.system().lower()
    try:
        if system == "darwin":
            cmd = ["open", "-R", str(path)] if select_file and path.is_file() else ["open", str(path)]
            subprocess.Popen(cmd)
        elif system == "windows":
            if select_file and path.is_file():
                subprocess.Popen(["explorer", "/select,", str(path)])
            else:
                subprocess.Popen(["explorer", str(path)])
        else:
            target = path if path.is_dir() else path.parent
            subprocess.Popen(["xdg-open", str(target)])
    except Exception as exc:
        raise HTTPException(500, f"Could not open file manager: {exc}")


def _choose_save_path(default_name: str) -> Optional[Path]:
    system = platform.system().lower()
    try:
        if system == "darwin":
            safe_name = default_name.replace('"', '\\"')
            script = (
                'set f to choose file name with prompt "Save SiteMirror Website Package" '
                f'default name "{safe_name}" default location (path to downloads folder)\n'
                'POSIX path of f'
            )
            result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
            if result.returncode != 0:
                return None
            raw = result.stdout.strip()
            return Path(raw) if raw else None

        if system == "windows":
            clean_name = default_name.replace('"', '')
            ps = (
                "Add-Type -AssemblyName System.Windows.Forms; "
                "$d = New-Object System.Windows.Forms.SaveFileDialog; "
                "$d.Filter = 'ZIP files (*.zip)|*.zip|All files (*.*)|*.*'; "
                f"$d.FileName = '{clean_name}'; "
                "$d.InitialDirectory = [Environment]::GetFolderPath('UserProfile') + '\\Downloads'; "
                "if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $d.FileName }"
            )
            result = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
            raw = result.stdout.strip()
            return Path(raw) if result.returncode == 0 and raw else None
    except Exception:
        return None
    return None


@app.get("/api/health")
def health():
    return {"ok": True, "app": "SiteMirror Studio", "version": "2.5.0"}


@app.get("/api/system/info")
def system_info(request: Request):
    system = platform.system().lower()
    file_manager = "Finder" if system == "darwin" else "Explorer" if system == "windows" else "File manager"
    return {
        "platform": system,
        "file_manager": file_manager,
        "data_dir": str(DATA),
        "exports_dir": str(EXPORTS),
        "native_shell": os.getenv("SITEMIRROR_NATIVE", "0") == "1",
        "app_url": str(request.base_url).rstrip("/"),
    }




@app.post("/api/system/open-browser")
def system_open_browser(payload: OpenPathRequest, request: Request):
    _require_local(request)
    path = payload.path or "/"
    if not path.startswith("/"):
        path = "/" + path
    url = str(request.base_url).rstrip("/") + path
    webbrowser.open(url, new=2)
    return {"ok": True, "url": url}


@app.post("/api/system/reveal-data")
def reveal_data(request: Request):
    _require_local(request)
    _reveal(DATA)
    return {"ok": True, "path": str(DATA)}


@app.post("/api/system/reveal-exports")
def reveal_exports(request: Request):
    _require_local(request)
    EXPORTS.mkdir(parents=True, exist_ok=True)
    _reveal(EXPORTS)
    return {"ok": True, "path": str(EXPORTS)}


@app.get("/api/projects")
def projects(include_hidden: bool = False):
    return store.list(include_hidden=include_hidden)




@app.delete("/api/projects/{pid}")
def delete_project(pid: str, mode: str = "all"):
    state = store.get(pid)
    if not state:
        raise HTTPException(404, "Project not found")
    terminal = {"complete", "failed", "stopped", "data_removed"}
    with ACTIVE_CRAWLS_LOCK:
        active = pid in ACTIVE_CRAWLS
    if active or state.get("status") not in terminal:
        raise HTTPException(409, "Stop the active crawl before deleting its history or data")

    mode = (mode or "all").lower().strip()
    if mode not in {"history", "data", "all"}:
        raise HTTPException(400, "mode must be history, data, or all")

    export_path = EXPORTS / _friendly_export_name(pid)

    if mode == "history":
        store.remove_history(pid)
        return {
            "ok": True,
            "mode": mode,
            "message": "Removed from SiteMirror history. Downloaded project files were kept.",
            "data_kept": True,
        }

    if mode == "data":
        state = store.delete_data(pid)
        if export_path.exists():
            export_path.unlink(missing_ok=True)
        return {
            "ok": True,
            "mode": mode,
            "message": "Downloaded mirror, assets, reports and generated export were deleted. History was kept.",
            "state": state,
        }

    store.delete_all(pid)
    if export_path.exists():
        export_path.unlink(missing_ok=True)
    return {
        "ok": True,
        "mode": mode,
        "message": "Project history and downloaded data were permanently deleted.",
    }

@app.get("/api/projects/{pid}")
def project(pid: str):
    state = store.get(pid)
    if not state:
        raise HTTPException(404, "Project not found")
    base = DATA / pid
    out = {"state": state}
    for name in ("website", "pages", "assets", "errors", "discovery"):
        p = base / f"{name}.json"
        if p.exists():
            out[name] = json.loads(p.read_text(encoding="utf-8"))
        elif name == "website":
            out[name] = state.get("website") or {}
        elif name == "discovery":
            out[name] = {}
        else:
            out[name] = []
    return out


@app.get("/api/projects/{pid}/locations")
def project_locations(pid: str):
    state = store.get(pid)
    if not state:
        raise HTTPException(404, "Project not found")
    project_dir = (DATA / pid).resolve()
    return {
        "project": str(project_dir),
        "project_exists": project_dir.exists(),
        "mirror": str(project_dir / "mirror"),
        "mirror_exists": (project_dir / "mirror").exists(),
        "exports": str(EXPORTS),
        "suggested_export": str(EXPORTS / _friendly_export_name(pid)),
        "data_present": bool(state.get("data_present", project_dir.exists())),
    }


@app.post("/api/crawl")
def crawl(req: CrawlRequest):
    url = normalize_user_url(req.url)
    pid, path = store.create(url)
    store.update(pid, max_pages=max(1, min(req.max_pages, 5000)))
    _start_crawler(pid, path, url, max_pages=req.max_pages)
    return {"project_id": pid, "status": "queued", "verification_mode": "single-dedicated-session"}


@app.post("/api/projects/{pid}/browser-ready")
def browser_ready(pid: str):
    state = store.get(pid) or {}
    if not state:
        raise HTTPException(404, "Project not found")
    with ACTIVE_CRAWLS_LOCK:
        crawler = ACTIVE_CRAWLS.get(pid)
    if crawler is None:
        raise HTTPException(409, "No active crawler is waiting for browser confirmation")
    if state.get("status") not in {"browser_waiting_user", "browser_verification", "browser_edge_retry"}:
        return {"ok": True, "status": state.get("status"), "message": "Crawler is not currently waiting for browser confirmation"}
    crawler.confirm_browser_ready()
    store.update(pid, status="browser_attaching", browser_reason="Browser confirmed by user — attaching to that exact session")
    return {"ok": True, "status": "browser_attaching"}


@app.post("/api/projects/{pid}/stop")
def stop_project(pid: str):
    state = store.get(pid) or {}
    if not state:
        raise HTTPException(404, "Project not found")
    if state.get("status") in {"complete", "failed", "stopped", "data_removed"}:
        return {"ok": True, "status": state.get("status")}

    with ACTIVE_CRAWLS_LOCK:
        crawler = ACTIVE_CRAWLS.get(pid)

    if crawler is not None:
        crawler.request_stop()
        # Give the runtime a short chance to flush partial reports and transition to stopped.
        for _ in range(30):
            current = store.get(pid) or {}
            if current.get("status") == "stopped":
                return {"ok": True, "status": "stopped"}
            time.sleep(0.05)
        return {"ok": True, "status": "stop_requested"}

    # No crawler exists in this SiteMirror process. This is normally an orphaned project
    # from an older app instance/crash, so make it removable immediately instead of
    # leaving it stuck forever in "Stopping".
    store.request_stop(pid)
    store.update(pid, status="stopped", current_url=None, browser_reason="Stopped · no active crawler process was attached")
    return {"ok": True, "status": "stopped", "orphaned": True}


@app.get("/api/url-history")
def url_history(url: str):
    url = (url or "").strip()
    if not url:
        raise HTTPException(400, "URL is required")
    if "://" not in url:
        url = "https://" + url
    return wayback_first_seen(url)


@app.post("/api/compare")
def compare(req: CompareRequest):
    a, b = DATA / req.source_project, DATA / req.target_project
    if not (a / "pages.json").exists() or not (b / "pages.json").exists():
        raise HTTPException(400, "Both projects must be complete")
    return compare_projects(a, b)


@app.get("/api/projects/{pid}/export")
def export(pid: str):
    out = _build_export(pid)
    return FileResponse(out, filename=out.name, media_type="application/zip")


@app.post("/api/projects/{pid}/save-export")
def save_export(pid: str, request: Request):
    _require_local(request)
    source = _build_export(pid)
    chosen = _choose_save_path(source.name)
    if chosen is None:
        return {"ok": False, "cancelled": True, "prepared_path": str(source)}
    if chosen.suffix.lower() != ".zip":
        chosen = chosen.with_suffix(".zip")
    chosen.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, chosen)
    return {"ok": True, "path": str(chosen), "filename": chosen.name}


@app.post("/api/projects/{pid}/reveal")
def reveal_project(pid: str, request: Request, kind: str = "project"):
    _require_local(request)
    project_dir = (DATA / pid).resolve()
    if not project_dir.exists():
        raise HTTPException(404, "Project not found")
    if kind == "project":
        target, select = project_dir, False
    elif kind == "mirror":
        target, select = project_dir / "mirror", False
    elif kind == "exports":
        EXPORTS.mkdir(parents=True, exist_ok=True)
        target, select = EXPORTS, False
    elif kind == "zip":
        target, select = _build_export(pid), True
    else:
        raise HTTPException(400, "Unknown location")
    _reveal(target, select_file=select)
    return {"ok": True, "path": str(target), "kind": kind}


@app.post("/api/projects/{pid}/open-mirror")
def open_mirror(pid: str, request: Request):
    _require_local(request)
    mirror_dir = DATA / pid / "mirror"
    if not mirror_dir.exists():
        raise HTTPException(404, "Mirror not available yet")
    url = str(request.base_url).rstrip("/") + f"/api/projects/{pid}/mirror/"
    webbrowser.open(url, new=2)
    return {"ok": True, "url": url}


@app.post("/api/projects/{pid}/open-in-browser")
def open_project_in_browser(pid: str, request: Request):
    _require_local(request)
    if not (DATA / pid).exists():
        raise HTTPException(404, "Project not found")
    url = str(request.base_url).rstrip("/") + f"/?project={pid}"
    webbrowser.open(url, new=2)
    return {"ok": True, "url": url}


@app.get("/api/projects/{pid}/mirror/{path:path}")
def mirror(pid: str, path: str = ""):
    base = (DATA / pid / "mirror").resolve()
    target = (base / (path or "index.html")).resolve()
    if target.is_dir():
        target = target / "index.html"
    if base not in target.parents and target != base:
        raise HTTPException(403, "Invalid path")
    if not target.exists():
        raise HTTPException(404, "Mirror file not found")
    return FileResponse(target)


app.mount("/", StaticFiles(directory=WEB, html=True), name="web")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("SITEMIRROR_PORT", "8787")), reload=False, access_log=False)
