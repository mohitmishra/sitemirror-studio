from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid
import zipfile
from collections import deque
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin, urlparse, urldefrag, unquote
from typing import Optional

import requests
from bs4 import BeautifulSoup

from .browser import open_browser, open_user_session_browser, launch_user_session_browser, attach_user_session_browser
from .metadata import extract_page_metadata, rdap_lookup, age_details
from .seo import audit_page, finalize_site_seo
from .discovery import DirectDiscovery, COMMON_SITEMAP_PATHS

PAGE_EXTS = {"", ".html", ".htm", ".php", ".asp", ".aspx"}
TRACKING = ("google-analytics", "googletagmanager", "doubleclick", "facebook.com/tr", "hotjar", "clarity.ms", "recaptcha")
CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.I)
CSS_IMPORT_RE = re.compile(r"@import\s+(?:url\()?\s*['\"]([^'\"]+)['\"]\s*\)?", re.I)


class CrawlStopped(RuntimeError):
    """Raised internally when the user cancels a crawl."""


def norm(url: Optional[str], base: str) -> Optional[str]:
    if not url:
        return None
    raw = str(url).strip()
    if not raw or raw.lower().startswith(("mailto:", "tel:", "sms:", "javascript:", "data:", "#")):
        return None
    try:
        u = urljoin(base, raw)
        u, _ = urldefrag(u)
        p = urlparse(u)
        if p.scheme not in {"http", "https"}:
            return None
        return p._replace(fragment="").geturl()
    except Exception:
        return None


def rootish(host: str) -> str:
    host = host.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def same_site(url: str, root_host: str) -> bool:
    return rootish(urlparse(url).netloc) == rootish(root_host)


def likely_page(url: str) -> bool:
    return Path(unquote(urlparse(url).path)).suffix.lower() in PAGE_EXTS


def safe(s: str) -> str:
    s = unquote(s)
    s = re.sub(r'[<>:"\\|?*\x00-\x1f]', "_", s).strip(" .")
    return s[:160] or "_"


def qhash(url: str) -> str:
    q = urlparse(url).query
    return "__q_" + hashlib.sha1(q.encode()).hexdigest()[:10] if q else ""


def page_path(project: Path, url: str) -> Path:
    p = urlparse(url)
    parts = [safe(x) for x in PurePosixPath(unquote(p.path)).parts if x not in ("/", "")]
    if not parts:
        return project / "mirror" / "index.html"
    leaf = parts[-1]
    ext = Path(leaf).suffix.lower()
    if ext in {".html", ".htm"}:
        parts[-1] = Path(leaf).stem + qhash(url) + ext
        return project / "mirror" / Path(*parts)
    return project / "mirror" / Path(*parts) / ("index" + qhash(url) + ".html")


def asset_path(project: Path, url: str) -> Path:
    p = urlparse(url)
    host = safe(p.netloc.split(":")[0])
    parts = [safe(x) for x in PurePosixPath(unquote(p.path)).parts if x not in ("/", "")]
    if not parts:
        parts = ["asset"]
    leaf = parts[-1]
    ext = Path(leaf).suffix
    if p.query:
        stem = leaf[:-len(ext)] if ext else leaf
        parts[-1] = stem + qhash(url) + ext
    return project / "mirror" / "_assets" / host / Path(*parts)


def rel(from_file: Path, to_file: Path) -> str:
    return os.path.relpath(to_file, start=from_file.parent).replace(os.sep, "/")


class ProjectStore:
    def __init__(self, base: Path):
        self.base = base
        self.base.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.state: dict[str, dict] = {}
        self.history_path = self.base / "_history.json"
        self._history: dict[str, dict] = self._load_history()
        self._bootstrap_existing_projects()
        self._reconcile_pending_stops()

    def _reconcile_pending_stops(self):
        """Finish stop requests left by an older/crashed SiteMirror process.

        A newly launched backend has no crawler threads from the previous process. If a
        project was already in stop_requested, the user's intent is unambiguous: mark it
        stopped while keeping all downloaded data so it can be removed/exported normally.
        """
        pending = [pid for pid, item in self._history.items() if item.get("status") == "stop_requested"]
        for pid in pending:
            self.update(pid, status="stopped", current_url=None, browser_reason="Stopped · completed pending stop request from previous SiteMirror session")

    def _load_history(self) -> dict[str, dict]:
        if not self.history_path.exists():
            return {}
        try:
            raw = json.loads(self.history_path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                return {str(item.get("id")): item for item in raw if isinstance(item, dict) and item.get("id")}
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}

    def _write_history(self):
        tmp = self.history_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self._history, indent=2), encoding='utf-8')
        tmp.replace(self.history_path)

    def _bootstrap_existing_projects(self):
        changed = False
        for path in self.base.iterdir():
            if not path.is_dir() or path.name.startswith('_'):
                continue
            if (path / '.sitemirror-hidden').exists():
                continue
            state_path = path / 'state.json'
            if not state_path.exists():
                continue
            try:
                state = json.loads(state_path.read_text(encoding='utf-8'))
            except Exception:
                continue
            pid = str(state.get('id') or path.name)
            state['id'] = pid
            state.setdefault('data_present', True)
            state.setdefault('hidden_from_recents', False)
            if pid not in self._history:
                self._history[pid] = state
                changed = True
        if changed:
            self._write_history()

    def _stop_marker(self, pid: str) -> Path:
        return self.base / pid / ".stop-requested"

    def request_stop(self, pid: str) -> None:
        project_dir = self.base / pid
        project_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._stop_marker(pid).write_text("stop\n", encoding="utf-8")
        except Exception:
            pass
        self.update(pid, status="stop_requested", browser_reason="Stopping crawl…")

    def is_stop_requested(self, pid: str) -> bool:
        if self._stop_marker(pid).exists():
            return True
        state = self.get(pid) or {}
        return state.get("status") == "stop_requested"

    def clear_stop(self, pid: str) -> None:
        marker = self._stop_marker(pid)
        try:
            if marker.exists():
                marker.unlink()
        except Exception:
            pass

    def create(self, url: str) -> tuple[str, Path]:
        pid = uuid.uuid4().hex[:12]
        path = self.base / pid
        path.mkdir(parents=True, exist_ok=True)
        self.clear_stop(pid)
        state = {
            "id": pid,
            "url": url,
            "status": "queued",
            "progress": 0,
            "pages": 0,
            "assets": 0,
            "errors": 0,
            "current_url": None,
            "browser_engine": None,
            "website": None,
            "started_at": time.time(),
            "updated_at": time.time(),
            "data_present": True,
            "hidden_from_recents": False,
        }
        with self.lock:
            self.state[pid] = dict(state)
            self._history[pid] = dict(state)
            (path / 'state.json').write_text(json.dumps(state, indent=2), encoding='utf-8')
            self._write_history()
        return pid, path

    def update(self, pid: str, **kwargs):
        with self.lock:
            current = self.state.get(pid) or self._history.get(pid)
            if current is None:
                state_path = self.base / pid / 'state.json'
                if state_path.exists():
                    try:
                        current = json.loads(state_path.read_text(encoding='utf-8'))
                    except Exception:
                        current = {"id": pid}
                else:
                    current = {"id": pid}
            current = dict(current)
            incoming_status = kwargs.get('status')
            stop_pending = self._stop_marker(pid).exists() or current.get('status') == 'stop_requested'
            if stop_pending and incoming_status not in {None, 'stop_requested', 'stopped'}:
                kwargs = dict(kwargs)
                kwargs.pop('status', None)
            current.update(kwargs)
            current['id'] = pid
            current['updated_at'] = time.time()
            current.setdefault('data_present', (self.base / pid).exists())
            current.setdefault('hidden_from_recents', False)
            self.state[pid] = current
            self._history[pid] = dict(current)
            project_dir = self.base / pid
            if current.get('data_present', True) or project_dir.exists():
                project_dir.mkdir(parents=True, exist_ok=True)
                (project_dir / 'state.json').write_text(json.dumps(current, indent=2), encoding='utf-8')
            self._write_history()
        if kwargs.get('status') == 'stopped':
            self.clear_stop(pid)

    def get(self, pid: str) -> Optional[dict]:
        with self.lock:
            if pid in self.state:
                return dict(self.state[pid])
            if pid in self._history:
                state = dict(self._history[pid])
                self.state[pid] = dict(state)
                return state
        p = self.base / pid / 'state.json'
        if p.exists():
            try:
                state = json.loads(p.read_text(encoding='utf-8'))
                with self.lock:
                    self.state[pid] = dict(state)
                    self._history[pid] = dict(state)
                    self._write_history()
                return state
            except Exception:
                return None
        return None

    def list(self, include_hidden: bool = False) -> list[dict]:
        with self.lock:
            items = [dict(item) for item in self._history.values()]
        if not include_hidden:
            items = [x for x in items if not x.get('hidden_from_recents')]
        return sorted(items, key=lambda x: x.get('started_at', 0), reverse=True)

    def remove_history(self, pid: str) -> bool:
        project_dir = self.base / pid
        with self.lock:
            existed = pid in self._history or pid in self.state
            self._history.pop(pid, None)
            self.state.pop(pid, None)
            self._write_history()
        if project_dir.exists():
            try:
                (project_dir / '.sitemirror-hidden').write_text('Removed from SiteMirror history; project files intentionally retained.\n', encoding='utf-8')
            except Exception:
                pass
        return existed

    def delete_data(self, pid: str) -> Optional[dict]:
        self.clear_stop(pid)
        state = self.get(pid)
        if not state:
            return None
        project_dir = self.base / pid
        if project_dir.exists():
            shutil.rmtree(project_dir, ignore_errors=True)
        profile_dir = self.base / '_browser_profiles' / pid
        if profile_dir.exists():
            shutil.rmtree(profile_dir, ignore_errors=True)
        state = dict(state)
        state.update({
            'status': 'data_removed',
            'progress': 0,
            'current_url': None,
            'data_present': False,
            'data_removed_at': time.time(),
            'updated_at': time.time(),
        })
        with self.lock:
            self.state[pid] = dict(state)
            self._history[pid] = dict(state)
            self._write_history()
        return state

    def delete_all(self, pid: str) -> bool:
        self.clear_stop(pid)
        project_dir = self.base / pid
        profile_dir = self.base / '_browser_profiles' / pid
        if project_dir.exists():
            shutil.rmtree(project_dir, ignore_errors=True)
        if profile_dir.exists():
            shutil.rmtree(profile_dir, ignore_errors=True)
        with self.lock:
            existed = pid in self._history or pid in self.state
            self._history.pop(pid, None)
            self.state.pop(pid, None)
            self._write_history()
        return existed


class SiteCrawler:
    def __init__(self, store: ProjectStore, pid: str, project: Path, start_url: str, cookies: Optional[list[dict]] = None, user_agent: Optional[str] = None, max_pages: int = 1000):
        self.store = store
        self.pid = pid
        self.project = project
        self.start_url = start_url if "://" in start_url else "https://" + start_url
        self.max_pages = max_pages
        self.cookies = cookies or []
        self.root_host = urlparse(self.start_url).netloc
        self.queue = deque()
        self.queued: set[str] = set()
        self.done: set[str] = set()
        self.assets_done: set[str] = set()
        self.asset_map: dict[str, Path] = {}
        self.pages: list[dict] = []
        self.assets: list[dict] = []
        self.errors: list[dict] = []
        self.redirects: list[dict] = []
        self.sitemap_lastmod: dict[str, str] = {}
        self.sitemap_urls: set[str] = set()
        self.feed_published: dict[str, str] = {}
        self.session = requests.Session()
        self.user_agent = user_agent or "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        self.session.headers.update({
            "User-Agent": self.user_agent,
            "Accept-Language": "en-US,en;q=0.9",
        })
        for c in self.cookies:
            try:
                self.session.cookies.set(c.get("name"), c.get("value"), domain=c.get("domain") or None, path=c.get("path") or "/")
            except Exception:
                pass
        self.discovery_report: dict = {}
        self.browser_used = False
        self.visible_browser_used = False
        self.browser_reason = None
        self.browser_discovery_done = False
        # Protected sites use one dedicated external Chromium profile for the entire crawl.
        # This mirrors the original successful browser-attached scraper: verify once, then
        # keep the exact same browser process/context/cookies for every URL.
        # One isolated profile per crawl/project. It cannot accidentally attach to the
        # user's older Chrome profile or another SiteMirror job.
        self.browser_profile_dir = self.project.parent / "_browser_profiles" / self.pid
        self.single_session_mode = False
        self.session_verified = False
        self._stop_event = threading.Event()
        self._browser_ready_event = threading.Event()
        self._active_browser = None
        self._pending_browser = None
        (project / "mirror").mkdir(parents=True, exist_ok=True)

    def confirm_browser_ready(self) -> None:
        """User confirmed the dedicated browser is showing the real website."""
        self._browser_ready_event.set()

    def request_stop(self) -> None:
        self._stop_event.set()
        self.store.request_stop(self.pid)
        browser = self._active_browser
        proc = getattr(browser, "process", None) if browser is not None else None
        if proc is not None and getattr(proc, "poll", lambda: 0)() is None:
            try:
                proc.terminate()
            except Exception:
                pass
        pending = self._pending_browser
        if pending is not None:
            try:
                pending.terminate()
            except Exception:
                pass
        self._browser_ready_event.set()

    def stop_requested(self) -> bool:
        return self._stop_event.is_set() or self.store.is_stop_requested(self.pid)

    def raise_if_stopped(self) -> None:
        if self.stop_requested():
            raise CrawlStopped("Crawl stopped by user")

    def _launch_browser_and_wait_for_user(self, url: str, *, reason: str, provider: Optional[str] = None):
        """Launch a real browser first; attach only after the user confirms the real page is visible.

        This intentionally mirrors the original working BAT/Python flow. No extension and
        no cookie transfer are involved. The browser process/profile opened here is the one
        later attached and reused for the entire crawl.
        """
        self._browser_ready_event.clear()
        pending = launch_user_session_browser(profile_dir=self.browser_profile_dir, start_url=url)
        self._pending_browser = pending
        self.browser_used = True
        self.visible_browser_used = True
        self.store.update(
            self.pid,
            status="browser_waiting_user",
            current_url=url,
            browser_engine=f"{pending.engine} (not attached yet)",
            browser_reason=(
                f"{provider or 'Protected site'} blocked direct crawl. A dedicated browser is open. "
                "When the real website is visible there, click Continue in SiteMirror. "
                "Crawler control will attach only after your confirmation."
            ),
        )
        deadline = time.time() + 900.0
        while time.time() < deadline:
            if self.stop_requested():
                try:
                    pending.terminate()
                except Exception:
                    pass
                self._pending_browser = None
                raise CrawlStopped("Crawl stopped by user")
            if self._browser_ready_event.wait(0.20):
                break
            if pending.process.poll() is not None:
                self._pending_browser = None
                raise RuntimeError("Dedicated browser was closed before SiteMirror could attach")
        else:
            try:
                pending.terminate()
            except Exception:
                pass
            self._pending_browser = None
            raise RuntimeError("Timed out waiting for browser confirmation")

        self.raise_if_stopped()
        browser = attach_user_session_browser(pending)
        self._pending_browser = None
        self._active_browser = browser
        self.store.update(
            self.pid,
            status="browser_attaching",
            browser_engine=f"{browser.engine} (same confirmed session)",
            browser_reason="Browser confirmed — attaching crawler control to the same session",
        )
        return browser

    def _http_get(self, url: str, **kwargs):
        """Run requests.get in a daemon worker so Stop can interrupt immediately.

        requests itself has no cross-thread cancel primitive. The request worker may finish
        quietly after cancellation, but the crawl thread can stop within ~100 ms and no
        further project work is performed.
        """
        self.raise_if_stopped()
        result = {}
        done = threading.Event()

        def worker():
            try:
                result["response"] = self.session.get(url, **kwargs)
            except Exception as exc:
                result["error"] = exc
            finally:
                done.set()

        threading.Thread(target=worker, daemon=True, name="SiteMirrorHTTP").start()
        while not done.wait(0.10):
            self.raise_if_stopped()
        self.raise_if_stopped()
        if "error" in result:
            raise result["error"]
        return result["response"]

    def add_page(self, url: Optional[str]):
        if not url:
            return
        u = norm(url, self.start_url)
        if not u or not same_site(u, self.root_host) or not likely_page(u):
            return
        if any(x in u.lower() for x in ("/wp-admin", "/wp-login", "/feed/")):
            return
        if u not in self.queued:
            self.queued.add(u)
            self.queue.append(u)

    def discover_sitemaps(self):
        """Discover robots, XML/text sitemaps, sitemap indexes, HTML sitemaps and feeds
        without launching a browser. Browser fallback is reserved for page rendering or
        anti-bot/human-verification cases.
        """
        self.raise_if_stopped()
        discovery = DirectDiscovery(self.start_url, self.session, should_stop=self.stop_requested)
        self.discovery_report = discovery.run()
        self.sitemap_lastmod.update(discovery.lastmod)
        self.sitemap_urls.update(getattr(discovery, "sitemap_urls", set()))
        self.feed_published.update(discovery.feed_published)
        for u in sorted(discovery.urls):
            self.add_page(u)
        (self.project / "discovery.json").write_text(
            json.dumps(self.discovery_report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return discovery

    def _challenge_reason(self, status: Optional[int], html: str) -> Optional[str]:
        low = (html or "").lower()[:20000]
        strong_markers = (
            "performing security verification",
            "this website uses a security service to protect against malicious bots",
            "verify you are human",
            "checking your browser",
            "just a moment",
            "cf-chl-",
            "/cdn-cgi/challenge-platform/",
            "challenge-platform",
            "cloudflare ray id",
            "performance & security by cloudflare",
            "performance and security by cloudflare",
            "security verification",
        )
        if any(marker in low for marker in strong_markers):
            return "Security verification required"
        if status in {401, 403, 429}:
            return f"HTTP {status} blocked direct crawl"
        for marker in ("attention required", "captcha", "access denied", "security check"):
            if marker in low:
                return "Human/bot verification detected"
        return None

    def _is_explicit_human_challenge(self, html: str) -> bool:
        low = (html or "").lower()[:20000]
        markers = (
            "performing security verification",
            "this website uses a security service to protect against malicious bots",
            "verify you are human",
            "checking your browser",
            "just a moment",
            "cf-chl-",
            "/cdn-cgi/challenge-platform/",
            "challenge-platform",
            "cloudflare ray id",
            "performance & security by cloudflare",
            "performance and security by cloudflare",
        )
        return any(marker in low for marker in markers)

    def _edge_block_info(self, status: Optional[int], html: str, headers: Optional[dict] = None) -> dict:
        """Classify static CDN/WAF denials separately from interactive human challenges.

        These pages normally have nothing for a user to solve. The right fallback is to
        try one dedicated real-browser session, not to sit on a fake verification wait loop.
        """
        low = (html or "").lower()[:30000]
        hdr = {str(k).lower(): str(v).lower() for k, v in (headers or {}).items()}
        provider = None
        markers = []

        if (
            "generated by cloudfront" in low
            or "the request could not be satisfied" in low
            or "request blocked. we can't connect to the server for this app or website" in low
            or "x-amz-cf-id" in hdr
            or "cloudfront" in hdr.get("server", "")
            or "cloudfront" in hdr.get("via", "")
        ):
            provider = "CloudFront"
            markers.append("cloudfront")
        elif "akamai" in low or "akamai" in hdr.get("server", ""):
            provider = "Akamai"
            markers.append("akamai")
        elif "fastly" in low or "fastly" in hdr.get("via", "") or "x-served-by" in hdr:
            provider = "Fastly"
            markers.append("fastly")
        elif "cloudflare" in hdr.get("server", "") and not self._is_explicit_human_challenge(html):
            provider = "Cloudflare"
            markers.append("cloudflare-static-denial")

        blocked_status = status in {401, 403, 429, 503}
        return {
            "blocked": bool(provider and (blocked_status or markers)),
            "provider": provider,
            "status": status,
            "markers": markers,
        }

    def _pause_for_verification(self, url: str, reason: str) -> None:
        self.browser_reason = reason or "Security verification required"
        self.discovery_report["browser_reason"] = self.browser_reason
        self.discovery_report["verification_required"] = True
        self.discovery_report["verification_url"] = url
        try:
            (self.project / "discovery.json").write_text(
                json.dumps(self.discovery_report or {}, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:
            pass
        # Keep everything collected so far, but do not mark the crawl as failed.
        self.write_outputs(None)
        self.store.update(
            self.pid,
            status="verification_required",
            current_url=url,
            verification_url=url,
            browser_reason=self.browser_reason,
            browser_engine="user-browser verification",
            pages=len(self.pages),
            assets=len(self.assets),
            errors=len(self.errors),
        )

    def _looks_js_shell(self, soup: BeautifulSoup) -> bool:
        try:
            visible = re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()
            scripts = len(soup.find_all("script"))
            roots = soup.select("#root, #app, #__next, [data-reactroot]")
            return len(visible) < 180 and scripts >= 3 and bool(roots or scripts >= 6)
        except Exception:
            return False

    def _needs_high_fidelity_render(self, soup: BeautifulSoup, html: str) -> bool:
        """Use an invisible browser when the source indicates that important visible DOM or
        assets are likely to be created after JavaScript runs. This is intentionally
        conservative so ordinary server-rendered sites remain browser-free.
        """
        score = 0
        if soup.select("[data-src], [data-lazy-src], [data-srcset], [data-background-image], [data-bg]"):
            score += 1
        if soup.find("script", attrs={"type": "module"}) or soup.find("link", rel=lambda v: v and "modulepreload" in [str(x).lower() for x in (v if isinstance(v, list) else [v])]):
            score += 1
        low = (html or "").lower()
        if "fetch(" in low or "xmlhttprequest" in low or "axios." in low:
            score += 2
        if soup.select("template, #__next, #root, #app, [data-reactroot]") and len(soup.find_all("script")) >= 5:
            score += 1
        return score >= 2

    def _page_issues_and_relationships(self, meta: dict, soup: BeautifulSoup):
        anchors = soup.find_all("a", href=True)
        internal_links = []
        external_links = []
        for a in anchors:
            u = norm(a.get("href"), meta.get("url") or self.start_url)
            if not u:
                continue
            if same_site(u, self.root_host) and likely_page(u):
                internal_links.append(u)
            elif urlparse(u).scheme in {"http", "https"}:
                external_links.append(u)
        meta["links"] = len(anchors)
        meta["internal_links"] = list(dict.fromkeys(internal_links))
        meta["external_links_count"] = len(set(external_links))
        meta["images"] = len(soup.find_all("img"))
        meta["assets_count"] = len(soup.find_all(["img", "script", "link", "source", "video", "audio", "iframe"]))
        meta["seo"] = audit_page(meta)
        meta["issues"] = [x.get("message") for x in meta["seo"].get("issues", [])]

    def _discover_links_from_soup(self, soup: BeautifulSoup, final_url: str):
        for a in soup.find_all("a", href=True):
            link = norm(a.get("href"), final_url)
            if link and same_site(link, self.root_host) and likely_page(link):
                self.add_page(link)

    def crawl_http_page(self, u: str) -> dict:
        """Try a normal direct HTTP crawl first. Returns a result descriptor.

        This path keeps browsers completely closed for ordinary static/server-rendered sites.
        """
        self.raise_if_stopped()
        try:
            r = self._http_get(
                u, timeout=(5, 10), allow_redirects=True,
                headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
            )
        except Exception as exc:
            msg = str(exc)
            # A browser cannot fix broken DNS. Skip the expensive browser cycle
            # (this caused multi-minute stalls on unreachable hosts).
            dns_error = any(t in msg for t in (
                "NameResolutionError", "nodename nor servname",
                "Name or service not known", "getaddrinfo failed",
                "Temporary failure in name resolution",
            ))
            return {
                "ok": False,
                "needs_browser": not dns_error,
                "reason": f"Direct HTTP failed: {msg}",
                "dns_error": dns_error,
            }

        self.raise_if_stopped()
        ctype = (r.headers.get("content-type") or "").lower()
        html = r.text if ("html" in ctype or "<html" in r.text[:1000].lower()) else ""
        reason = self._challenge_reason(r.status_code, html or r.text)
        if reason:
            edge = self._edge_block_info(r.status_code, html or r.text, dict(r.headers))
            return {
                "ok": False,
                "needs_browser": True,
                "reason": (f"{edge.get('provider')} blocked direct crawl" if edge.get("blocked") else reason),
                "human_verification": self._is_explicit_human_challenge(html or r.text),
                "edge_block": bool(edge.get("blocked")),
                "edge_provider": edge.get("provider"),
                "status": r.status_code,
            }
        if r.status_code >= 500:
            # A real origin 5xx should be recorded, not hidden behind browser automation.
            html = html or f"<html><head><title>HTTP {r.status_code}</title></head><body></body></html>"
        if not html:
            return {"ok": False, "needs_browser": False, "reason": "Response is not an HTML page", "status": r.status_code}

        final_url = r.url
        if final_url != u:
            self.redirects.append({"from": u, "to": final_url})
        soup = BeautifulSoup(html, "html.parser")
        if r.status_code < 400 and self._looks_js_shell(soup):
            return {"ok": False, "needs_browser": True, "reason": "JavaScript-rendered page detected"}
        if r.status_code < 400 and self._needs_high_fidelity_render(soup, html):
            return {"ok": False, "needs_browser": True, "reason": "Dynamic page requires high-fidelity background rendering"}

        meta = extract_page_metadata(final_url, html, dict(r.headers))
        if not meta.get("modified_at") and self.sitemap_lastmod.get(final_url):
            meta["modified_at"] = self.sitemap_lastmod[final_url]
            meta["modified_source"] = "sitemap:lastmod"
        if not meta.get("published_at") and self.feed_published.get(final_url):
            from .metadata import parse_iso
            meta["published_at"] = parse_iso(self.feed_published[final_url])
            meta["published_source"] = "rss/atom"
            _age = age_details(meta.get("published_at"))
            meta["url_age"] = {
                "date": meta.get("published_at"), "source": "rss/atom",
                "confidence": "medium", "label": _age.get("label"), "days": _age.get("days"),
                "meaning": "Published-date evidence, not an exact URL creation date"
            }
        meta["status"] = r.status_code
        meta["render_source"] = "direct-http"
        self._page_issues_and_relationships(meta, soup)
        self._discover_links_from_soup(soup, final_url)

        out = page_path(self.project, final_url)
        out.parent.mkdir(parents=True, exist_ok=True)
        critical_urls = set()
        for node in soup.find_all("script", src=True):
            cu = norm(node.get("src"), final_url)
            if cu:
                critical_urls.add(cu)
        for node in soup.find_all("link", href=True):
            rels = {str(x).lower() for x in (node.get("rel") or [])}
            if "stylesheet" in rels or "preload" in rels:
                cu = norm(node.get("href"), final_url)
                if cu:
                    critical_urls.add(cu)
        error_start = len(self.errors)
        _fidelity = self.rewrite(soup, final_url, out)
        meta["mirror_fidelity"] = _fidelity
        new_errors = self.errors[error_start:]
        critical_failed = any(e.get("type") == "asset" and e.get("url") in critical_urls for e in new_errors)
        if critical_failed:
            return {"ok": False, "needs_browser": True, "reason": "Protected CSS/JS assets require browser capture"}
        out.write_text(str(soup), encoding="utf-8")
        meta["saved_path"] = str(out.relative_to(self.project))
        self.pages.append(meta)
        return {"ok": True, "needs_browser": False, "status": r.status_code}

    def _cookies_for_browser(self) -> list[dict]:
        cookies = []
        for c in self.session.cookies:
            item = {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path or "/"}
            if item["domain"]:
                cookies.append(item)
        return cookies

    def crawl_browser_page(self, u: str, browser, visible: bool = False, reuse_main: bool = False, use_current_document: bool = False) -> dict:
        page = browser.main_page(u if use_current_document else None) if reuse_main and hasattr(browser, "main_page") else browser.context.new_page()
        browser_responses = []
        def _capture_response(r):
            browser_responses.append(r)
        page.on("response", _capture_response)
        try:
            self.raise_if_stopped()
            if visible and hasattr(browser, "restore"):
                browser.restore(page, bring_to_front=True)

            response = None
            current_before = page.url or ""
            current_matches = (
                use_current_document
                and current_before not in {"", "about:blank"}
                and rootish(urlparse(current_before).netloc) == rootish(self.root_host)
            )
            if not current_matches:
                response = page.goto(u, wait_until="domcontentloaded", timeout=20000)
            else:
                # The browser was launched directly at the blocked URL before CDP attached.
                # Preserve that native first navigation instead of immediately issuing a new
                # automation-driven request. This mirrors the original successful scraper.
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=5000)
                except Exception:
                    pass
            self.raise_if_stopped()
            try:
                page.wait_for_load_state("networkidle", timeout=3000)
            except Exception:
                pass
            page.wait_for_timeout(500)
            try:
                body_text = page.locator("body").inner_text(timeout=3500).lower()[:8000]
            except Exception:
                body_text = ""
            status = response.status if response else None
            reason = self._challenge_reason(status, body_text)
            edge = self._edge_block_info(status, body_text, response.headers if response else {})
            explicit_human = self._is_explicit_human_challenge(body_text)

            if (reason or edge.get("blocked")) and not visible:
                self.sync_browser_cookies(browser.context)
                return {
                    "ok": False,
                    "needs_visible": True,
                    "reason": (f"{edge.get('provider')} denied background browser" if edge.get("blocked") else reason),
                    "edge_denied": bool(edge.get("blocked")),
                }

            if visible and explicit_human:
                self.visible_browser_used = True
                self.store.update(
                    self.pid,
                    status="browser_verification",
                    current_url=u,
                    browser_reason="Complete verification once in the dedicated SiteMirror browser. The same session will be reused for the full crawl.",
                    browser_engine=getattr(browser, "engine", "dedicated browser session"),
                )
                cleared = False
                # Keep this exact tab/context alive. The user solves only the website's own
                # challenge; SiteMirror does not automate the verification itself.
                for _ in range(1200):  # up to 10 minutes
                    if self.stop_requested():
                        return {"ok": False, "stopped": True, "reason": "Crawl stopped by user"}
                    page.wait_for_timeout(500)
                    try:
                        text = page.locator("body").inner_text(timeout=1500).lower()[:8000]
                    except Exception:
                        text = ""
                    current = page.url or u
                    if text and not self._is_explicit_human_challenge(text) and rootish(urlparse(current).netloc) == rootish(self.root_host):
                        # A post-verification page can still be a static CDN denial. Do not
                        # treat that as successfully verified.
                        post_edge = self._edge_block_info(None, text, {})
                        if not post_edge.get("blocked"):
                            cleared = True
                            break
                self.sync_browser_cookies(browser.context)
                if not cleared:
                    return {"ok": False, "needs_visible": True, "reason": "Human verification was not completed"}

                self.session_verified = True
                self.single_session_mode = True
                minimized = False
                if hasattr(browser, "minimize"):
                    minimized = bool(browser.minimize(page))
                self.store.update(
                    self.pid,
                    status="crawling_verified_session",
                    browser_reason="Verification complete — reusing the same dedicated browser session for every page" + (" (browser minimized)" if minimized else ""),
                    browser_engine=getattr(browser, "engine", "dedicated browser session"),
                )
            elif visible and edge.get("blocked"):
                # This is a static CDN/WAF denial, not a CAPTCHA. There is nothing meaningful
                # for the user to solve. The dedicated browser has already been given one
                # native first-navigation opportunity; if it is still denied, report it
                # accurately instead of waiting ten minutes on a fake verification loop.
                self.sync_browser_cookies(browser.context)
                return {
                    "ok": False,
                    "needs_visible": False,
                    "edge_denied": True,
                    "reason": f"{edge.get('provider') or 'CDN/WAF'} denied the dedicated browser session",
                }
            elif visible and reason and status in {401, 403, 429, 503}:
                self.sync_browser_cookies(browser.context)
                return {
                    "ok": False,
                    "needs_visible": False,
                    "edge_denied": True,
                    "reason": f"HTTP {status} denied the dedicated browser session",
                }

            try:
                height = page.evaluate("Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)") or 0
                steps = max(3, min(14, int(height / 700) + 1))
                for i in range(1, steps + 1):
                    if self.stop_requested():
                        return {"ok": False, "stopped": True, "reason": "Crawl stopped by user"}
                    page.evaluate("(y) => window.scrollTo(0, y)", int(height * i / steps))
                    page.wait_for_timeout(120)
                page.wait_for_timeout(300)
                page.evaluate("window.scrollTo(0,0)")
                page.wait_for_timeout(120)
            except Exception:
                pass

            final_url = page.url
            self.sync_browser_cookies(browser.context)
            for br in browser_responses:
                self.capture_browser_response(br)
            html = page.content()
            soup = BeautifulSoup(html, "html.parser")
            doc_responses = [br for br in browser_responses if getattr(br.request, "resource_type", "") == "document"]
            if doc_responses:
                response = doc_responses[-1]
            headers = response.headers if response else {}
            meta = extract_page_metadata(final_url, html, headers)
            if not meta.get("modified_at") and self.sitemap_lastmod.get(final_url):
                meta["modified_at"] = self.sitemap_lastmod[final_url]
                meta["modified_source"] = "sitemap:lastmod"
            if not meta.get("published_at") and self.feed_published.get(final_url):
                from .metadata import parse_iso
                meta["published_at"] = parse_iso(self.feed_published[final_url])
                meta["published_source"] = "rss/atom"
                _age = age_details(meta.get("published_at"))
                meta["url_age"] = {
                    "date": meta.get("published_at"), "source": "rss/atom",
                    "confidence": "medium", "label": _age.get("label"), "days": _age.get("days"),
                    "meaning": "Published-date evidence, not an exact URL creation date"
                }
            meta["status"] = response.status if response else None
            meta["render_source"] = "dedicated-browser-session" if self.single_session_mode else ("visible-browser" if visible else "headless-browser")
            self._page_issues_and_relationships(meta, soup)
            self._discover_links_from_soup(soup, final_url)
            out = page_path(self.project, final_url)
            out.parent.mkdir(parents=True, exist_ok=True)
            _fidelity = self.rewrite(soup, final_url, out)
            meta["mirror_fidelity"] = _fidelity
            out.write_text(str(soup), encoding="utf-8")
            meta["saved_path"] = str(out.relative_to(self.project))
            # Avoid duplicate Page objects when direct HTTP partially captured the URL before
            # a protected asset forced browser fallback. Browser capture is authoritative.
            self.pages = [p for p in self.pages if p.get("url") != meta.get("url")]
            self.pages.append(meta)
            return {"ok": True, "needs_visible": False}
        except CrawlStopped:
            return {"ok": False, "stopped": True, "needs_visible": False, "reason": "Crawl stopped by user"}
        except Exception as exc:
            if self.stop_requested():
                return {"ok": False, "stopped": True, "needs_visible": False, "reason": "Crawl stopped by user"}
            self.errors.append({"type": "page", "url": u, "error": str(exc), "source": "browser"})
            return {"ok": False, "needs_visible": False, "reason": str(exc)}
        finally:
            try:
                page.remove_listener("response", _capture_response)
            except Exception:
                pass
            if not reuse_main:
                try:
                    page.close()
                except Exception:
                    pass

    def discover_sitemaps_via_browser(self, context):
        """Re-check sitemap structure inside an authenticated/verified browser context.

        This preserves the successful behavior of the earlier browser scraper for sites
        where robots/sitemap/page URLs return 403 to plain HTTP but work after a real
        browser session has received the site's verification cookies.
        """
        if self.browser_discovery_done:
            return
        self.browser_discovery_done = True

        direct_valid = [x for x in self.discovery_report.get("sitemaps", []) if x.get("valid")]
        home_challenge = self.discovery_report.get("homepage", {}).get("challenge")
        if direct_valid and not home_challenge:
            return

        candidates = []
        for u in self.discovery_report.get("robots", {}).get("sitemaps", []):
            candidates.append((u, "robots.txt"))
        for item in self.discovery_report.get("sitemaps", []):
            if item.get("url"):
                candidates.append((item["url"], item.get("source") or "browser-session"))
        protected_discovery = bool(
            self.discovery_report.get("homepage", {}).get("edge_block")
            or self.discovery_report.get("homepage", {}).get("challenge")
        )
        common_paths = COMMON_SITEMAP_PATHS[:8] if protected_discovery else COMMON_SITEMAP_PATHS
        candidates.extend((urljoin(self.start_url, p), "common-location") for p in common_paths)

        todo = deque()
        seen = set()
        queued = set()
        for u, source in candidates:
            nu = norm(u, self.start_url)
            if nu and nu not in queued:
                queued.add(nu)
                todo.append((nu, source))

        # Prefer same-origin fetches executed inside the accepted browser page. This uses
        # the browser's real network stack/cookies instead of Playwright's separate
        # APIRequestContext, which is often exactly what CloudFront/Akamai block.
        anchor = None
        for pg in reversed(list(context.pages)):
            try:
                if same_site(pg.url or "", self.root_host) and (pg.url or "") not in {"", "about:blank", "chrome://newtab/"}:
                    anchor = pg
                    break
            except Exception:
                pass

        def browser_fetch_text(target_url):
            if anchor is None:
                return None, None, target_url
            try:
                a = urlparse(anchor.url)
                t = urlparse(target_url)
                if (a.scheme, a.netloc) != (t.scheme, t.netloc):
                    return None, None, target_url
                data = anchor.evaluate(
                    """async (u) => {
                        try {
                          const r = await fetch(u, {credentials:'include', cache:'no-store', redirect:'follow'});
                          return {status:r.status, url:r.url, text:await r.text()};
                        } catch (e) { return {error:String(e)}; }
                    }""",
                    target_url,
                )
                if data and not data.get("error"):
                    return data.get("status"), data.get("text") or "", data.get("url") or target_url
            except Exception:
                pass
            return None, None, target_url

        # Recover robots.txt through the browser first; it may declare non-standard sitemap names.
        robots_status, robots_text, robots_final = browser_fetch_text(urljoin(self.start_url, "/robots.txt"))
        if robots_text and robots_status is not None and robots_status < 400:
            for line in robots_text.splitlines():
                if line.lower().lstrip().startswith("sitemap:"):
                    ru = norm(line.split(":", 1)[1].strip(), robots_final)
                    if ru and ru not in queued:
                        queued.add(ru)
                        todo.appendleft((ru, "robots.txt/browser"))

        found_items = []
        while todo and len(seen) < 250:
            self.raise_if_stopped()
            sm, source = todo.popleft()
            if sm in seen:
                continue
            seen.add(sm)
            text = ""
            status = None
            final_url = sm

            # 1) Real browser fetch for same-origin sitemap resources.
            status, fetched_text, final_url = browser_fetch_text(sm)
            if fetched_text and (status is None or status < 400):
                text = fetched_text

            # 2) Browser request context is only a fallback. It may have a different TLS/client
            # fingerprint, so don't prefer it on edge-protected sites.
            if not text:
                try:
                    br = context.request.get(sm, timeout=10000, fail_on_status_code=False)
                    status = br.status
                    final_url = br.url
                    if status < 400:
                        text = br.text()
                except Exception:
                    pass

            # 3) For declared/indexed sitemap URLs, real page navigation is the final fallback.
            should_navigate = (
                not text
                and source != "common-location"
                and (status is None or status in {401, 403, 429})
            )
            if should_navigate:
                pg = context.new_page()
                try:
                    rr = pg.goto(sm, wait_until="domcontentloaded", timeout=12000)
                    status = rr.status if rr else status
                    final_url = pg.url
                    try:
                        text = pg.locator("body").inner_text(timeout=3000)
                    except Exception:
                        text = pg.content()
                    if "<loc>" not in text.lower():
                        source_html = pg.content()
                        if "<loc>" in source_html.lower():
                            text = source_html
                except Exception:
                    text = ""
                finally:
                    try:
                        pg.close()
                    except Exception:
                        pass

            if not text or (status is not None and status >= 400):
                continue
            low = text.lower()
            if "<urlset" not in low and "<sitemapindex" not in low and "<loc>" not in low:
                continue

            soup = BeautifulSoup(text, "xml")
            root = soup.find(["sitemapindex", "urlset"])
            if not root:
                # Chrome XML viewer sometimes escapes XML; regex extraction is a fallback.
                locs = re.findall(r"<loc>\s*(.*?)\s*</loc>", text, re.I | re.S)
                if not locs:
                    locs = re.findall(r"https?://[^\s<>\"']+", text)
                for loc in locs:
                    clean = re.sub(r"<[^>]+>", "", loc).strip()
                    if clean.lower().endswith((".xml", ".xml.gz")):
                        todo.append((clean, "browser-sitemap-index"))
                    else:
                        self.add_page(clean)
                if locs:
                    found_items.append({
                        "url": sm, "final_url": final_url, "source": source,
                        "status": status, "kind": "xml/browser", "valid": True,
                        "url_count": len([x for x in locs if not str(x).lower().endswith((".xml", ".xml.gz"))]),
                        "child_sitemaps": len([x for x in locs if str(x).lower().endswith((".xml", ".xml.gz"))]),
                        "lastmod_count": 0, "issues": [],
                        "notes": ["Recovered through verified browser session"],
                    })
                continue

            kind = root.name.lower()
            url_count = child_count = lastmod_count = 0
            if kind == "sitemapindex":
                for node in root.find_all("sitemap", recursive=False):
                    loc_node = node.find("loc")
                    if not loc_node:
                        continue
                    loc = loc_node.get_text(strip=True)
                    if loc:
                        child_count += 1
                        todo.append((loc, "browser-sitemap-index"))
                    if node.find("lastmod"):
                        lastmod_count += 1
            else:
                for node in root.find_all("url", recursive=False):
                    loc_node = node.find("loc")
                    if not loc_node:
                        continue
                    loc = loc_node.get_text(strip=True)
                    if not loc:
                        continue
                    url_count += 1
                    self.add_page(loc)
                    self.sitemap_urls.add(loc)
                    lm = node.find("lastmod")
                    if lm and lm.get_text(strip=True):
                        lastmod_count += 1
                        self.sitemap_lastmod[loc] = lm.get_text(strip=True)
            found_items.append({
                "url": sm, "final_url": final_url, "source": source,
                "status": status, "kind": kind, "valid": True,
                "url_count": url_count, "child_sitemaps": child_count,
                "lastmod_count": lastmod_count, "issues": [],
                "notes": ["Recovered through verified browser session"],
            })

        if found_items:
            by_url = {x.get("url"): x for x in self.discovery_report.get("sitemaps", [])}
            for item in found_items:
                if item.get("url") in by_url and not by_url[item["url"]].get("valid"):
                    previous_source = by_url[item["url"]].get("source")
                    item["recovery_source"] = "browser-session"
                    if previous_source:
                        item["source"] = previous_source
                    by_url[item["url"]].update(item)
                elif item.get("url") not in by_url:
                    self.discovery_report.setdefault("sitemaps", []).append(item)
                    by_url[item.get("url")] = item
            self.discovery_report["urls_discovered"] = len(self.queued)
            valid_now = [x for x in self.discovery_report.get("sitemaps", []) if x.get("valid")]
            declared_now = [x for x in valid_now if x.get("source") == "robots.txt"]
            for standard in self.discovery_report.get("standards", []):
                if standard.get("name") == "Sitemap discovery":
                    standard["status"] = "good" if valid_now else "warning"
                    standard["detail"] = f"{len(valid_now)} valid sitemap file(s) found" if valid_now else "No valid XML/text sitemap found"
                elif standard.get("name") == "Sitemap declared in robots.txt":
                    standard["status"] = "good" if declared_now else "info"
                    standard["detail"] = f"{len(declared_now)} valid declared sitemap(s)" if declared_now else "Not declared; optional but recommended for discovery"
                elif standard.get("name") == "Nested sitemap indexes":
                    standard["detail"] = f"{len(self.discovery_report.get('sitemaps', []))} sitemap file(s) inspected recursively"
            (self.project / "discovery.json").write_text(
                json.dumps(self.discovery_report, indent=2, ensure_ascii=False), encoding="utf-8"
            )

    def download_asset(self, raw: str, referer: str) -> Optional[Path]:
        self.raise_if_stopped()
        u = norm(raw, referer)
        if not u or any(t in u.lower() for t in TRACKING):
            return None
        if u in self.asset_map:
            return self.asset_map[u]
        if u in self.assets_done:
            return None
        self.assets_done.add(u)
        try:
            r = self._http_get(u, timeout=(4, 8), allow_redirects=True, headers={"Referer": referer})
            self.raise_if_stopped()
            if r.status_code >= 400:
                self.errors.append({"type": "asset", "url": u, "status": r.status_code})
                return None
            path = asset_path(self.project, u)
            path.parent.mkdir(parents=True, exist_ok=True)
            ctype = (r.headers.get("content-type") or "").split(";")[0].lower()
            data = r.content
            if "text/css" in ctype or Path(urlparse(u).path).suffix.lower() == ".css":
                try:
                    css = r.text
                    def repl(m):
                        dep = m.group(2).strip()
                        if not dep or dep.startswith("data:"):
                            return m.group(0)
                        p = self.download_asset(dep, r.url)
                        return f"url('{rel(path, p)}')" if p else m.group(0)
                    css = CSS_URL_RE.sub(repl, css)
                    def import_repl(m):
                        dep = m.group(1).strip()
                        p = self.download_asset(dep, r.url)
                        return "@import url('%s')" % rel(path, p) if p else m.group(0)
                    css = CSS_IMPORT_RE.sub(import_repl, css)
                    data = css.encode("utf-8")
                except Exception:
                    pass
            path.write_bytes(data)
            self.asset_map[u] = path
            final_u = norm(r.url, u) or r.url
            self.asset_map[final_u] = path
            self.assets.append({"url": u, "final_url": r.url, "saved_path": str(path.relative_to(self.project)), "content_type": ctype, "bytes": len(data), "source": "http"})
            return path
        except CrawlStopped:
            raise
        except Exception as exc:
            self.errors.append({"type": "asset", "url": u, "error": str(exc)})
            return None

    def rewrite(self, soup: BeautifulSoup, page_url: str, out_file: Path):
        localized = 0
        unresolved = 0

        def localize(raw, referer=page_url):
            nonlocal localized, unresolved
            self.raise_if_stopped()
            if not raw or str(raw).startswith(("data:", "blob:", "#")):
                return None
            p = self.download_asset(str(raw), referer)
            if p:
                localized += 1
                return rel(out_file, p)
            u = norm(str(raw), referer)
            if u and urlparse(u).scheme in {"http", "https"} and not any(t in u.lower() for t in TRACKING):
                unresolved += 1
            return None

        # Internal page navigation becomes local. Non-page same-site downloads are localized.
        for a in soup.find_all("a", href=True):
            u = norm(a.get("href"), page_url)
            if not u:
                continue
            if same_site(u, self.root_host) and likely_page(u):
                a["href"] = rel(out_file, page_path(self.project, u))
            elif same_site(u, self.root_host):
                loc = localize(a.get("href"))
                if loc:
                    a["href"] = loc

        for tag, attr in [
            ("img", "src"), ("script", "src"), ("source", "src"),
            ("video", "src"), ("audio", "src"), ("input", "src"),
            ("track", "src"), ("embed", "src"), ("object", "data"),
            ("video", "poster")
        ]:
            for node in soup.find_all(tag):
                raw = node.get(attr)
                if raw:
                    loc = localize(raw)
                    if loc:
                        node[attr] = loc

        # Common lazy-load / builder attributes.
        for attr in (
            "data-src", "data-lazy-src", "data-original", "data-bg",
            "data-background", "data-background-image", "data-poster"
        ):
            for node in soup.find_all(attrs={attr: True}):
                loc = localize(node.get(attr))
                if loc:
                    node[attr] = loc

        for node in soup.find_all(["img", "source"]):
            for attr in ("srcset", "data-srcset", "data-lazy-srcset"):
                value = node.get(attr)
                if not value:
                    continue
                items = []
                for piece in value.split(","):
                    bits = piece.strip().split()
                    if not bits:
                        continue
                    loc = localize(bits[0])
                    if loc:
                        bits[0] = loc
                    items.append(" ".join(bits))
                node[attr] = ", ".join(items)

        for node in soup.find_all("link", href=True):
            rels = {str(x).lower() for x in (node.get("rel") or [])}
            if {
                "stylesheet", "icon", "preload", "prefetch", "apple-touch-icon",
                "modulepreload", "manifest", "mask-icon"
            } & rels:
                loc = localize(node.get("href"))
                if loc:
                    node["href"] = loc

        # Inline background/font URLs used by Elementor and other builders.
        for node in soup.find_all(style=True):
            css = node.get("style") or ""
            def inline_repl(m):
                raw = m.group(2).strip()
                loc = localize(raw)
                return "url('%s')" % loc if loc else m.group(0)
            node["style"] = CSS_URL_RE.sub(inline_repl, css)

        for style_tag in soup.find_all("style"):
            css = style_tag.string or style_tag.get_text() or ""
            if not css:
                continue
            def style_repl(m):
                raw = m.group(2).strip()
                loc = localize(raw)
                return "url('%s')" % loc if loc else m.group(0)
            style_tag.string = CSS_URL_RE.sub(style_repl, css)

        # Keep form submissions on the live origin rather than accidentally posting to localhost.
        for form in soup.find_all("form"):
            action = form.get("action")
            if action and not str(action).lower().startswith(("javascript:", "#")):
                u = norm(action, page_url)
                if u:
                    form["action"] = u

        # Same-origin iframes can point to a local mirrored page. External embeds remain live.
        for frame in soup.find_all("iframe", src=True):
            u = norm(frame.get("src"), page_url)
            if u and same_site(u, self.root_host) and likely_page(u):
                self.add_page(u)
                frame["src"] = rel(out_file, page_path(self.project, u))

        # Localized files no longer match source SRI hashes. CSP meta can also block localhost assets.
        for node in soup.find_all(attrs={"integrity": True}):
            node.attrs.pop("integrity", None)
        for node in soup.find_all("meta"):
            equiv = (node.get("http-equiv") or "").lower()
            if equiv in {"content-security-policy", "content-security-policy-report-only"}:
                node.decompose()
        for node in soup.find_all("base"):
            node.decompose()

        # Preserve the source URL for diagnostics and JS fallbacks.
        html_tag = soup.find("html")
        if html_tag:
            html_tag["data-sitemirror-source"] = page_url

        total = localized + unresolved
        fidelity = 100 if total == 0 else int(round(localized / float(total) * 100))
        return {"localized": localized, "unresolved": unresolved, "asset_fidelity": fidelity}

    def sync_browser_cookies(self, context):
        try:
            for c in context.cookies():
                self.session.cookies.set(c.get("name"), c.get("value"), domain=c.get("domain") or None, path=c.get("path") or "/")
        except Exception:
            pass

    def capture_browser_response(self, response):
        """Save resources from the real browser response when possible.

        This is the key fallback for protected sites whose CSS/JS/image URLs return 403
        to a second requests-based fetch even though the visible browser loaded them.
        """
        try:
            u = response.url
            resource_type = getattr(response.request, "resource_type", "")
            if not u or any(t in u.lower() for t in TRACKING) or resource_type == "document":
                return
            if u in self.asset_map:
                return
            body = response.body()
            if body is None:
                return
            path = asset_path(self.project, u)
            path.parent.mkdir(parents=True, exist_ok=True)
            headers = response.headers
            ctype = (headers.get("content-type") or "").split(";")[0].lower()
            data = body
            if "text/css" in ctype or Path(urlparse(u).path).suffix.lower() == ".css":
                try:
                    css = body.decode("utf-8", errors="replace")
                    # Browser-captured CSS is stored immediately; nested dependencies are
                    # subsequently discovered by browser performance entries and requests.
                    data = css.encode("utf-8")
                except Exception:
                    pass
            path.write_bytes(data)
            self.asset_map[u] = path
            try:
                request_u = norm(getattr(response.request, "url", None), u)
                if request_u:
                    self.asset_map[request_u] = path
            except Exception:
                pass
            self.assets_done.add(u)
            self.errors = [e for e in self.errors if not (e.get("type") == "asset" and e.get("url") == u)]
            if not any(a.get("url") == u for a in self.assets):
                self.assets.append({"url": u, "saved_path": str(path.relative_to(self.project)), "content_type": ctype, "bytes": len(data), "source": "browser"})
        except Exception:
            pass

    def finalize_css(self):
        """Rewrite CSS url(...) references after resource discovery is complete."""
        for asset in self.assets:
            self.raise_if_stopped()
            ctype = (asset.get("content_type") or "").lower()
            if "text/css" not in ctype and not str(asset.get("url", "")).lower().split("?", 1)[0].endswith(".css"):
                continue
            path = self.project / asset.get("saved_path", "")
            if not path.exists():
                continue
            try:
                css = path.read_text(encoding="utf-8", errors="replace")
                source_url = asset.get("url")
                def repl(m):
                    raw = m.group(2).strip()
                    if not raw or raw.startswith("data:") or raw.startswith("#"):
                        return m.group(0)
                    u = norm(raw, source_url)
                    if not u:
                        return m.group(0)
                    target = self.asset_map.get(u) or self.download_asset(u, source_url)
                    return f"url('{rel(path, target)}')" if target else m.group(0)
                css = CSS_URL_RE.sub(repl, css)
                def import_repl(m):
                    raw = m.group(1).strip()
                    u = norm(raw, source_url)
                    if not u:
                        return m.group(0)
                    target = self.asset_map.get(u) or self.download_asset(u, source_url)
                    return "@import url('%s')" % rel(path, target) if target else m.group(0)
                css = CSS_IMPORT_RE.sub(import_repl, css)
                path.write_text(css, encoding="utf-8")
            except CrawlStopped:
                raise
            except Exception:
                continue

    def run(self):
        self.store.update(self.pid, status="discovering_direct", browser_engine="direct-http")
        browser = None
        browser_visible = False
        try:
            # Phase 1: standards/discovery scan. No browser is launched here.
            discovery = self.discover_sitemaps()
            self.raise_if_stopped()
            self.add_page(self.start_url)
            home_discovery = self.discovery_report.get("homepage", {}) or {}
            preempt_browser_once = bool(home_discovery.get("edge_block") or home_discovery.get("explicit_human_challenge"))
            total_hint = max(len(self.queue), 1)
            self.store.update(
                self.pid,
                status="crawling_direct",
                pages=0,
                assets=0,
                errors=0,
                discovery_urls=len(discovery.urls),
            )

            while self.queue and len(self.pages) < self.max_pages:
                if self.stop_requested():
                    self.write_outputs(None)
                    self.store.update(self.pid, status="stopped", browser_reason="Crawl stopped by user", pages=len(self.pages), assets=len(self.assets), errors=len(self.errors))
                    return

                u = self.queue.popleft()
                if u in self.done:
                    continue
                self.done.add(u)
                self.store.update(self.pid, current_url=u)

                # Once the user has verified a protected site, do NOT jump back to requests
                # or another browser/profile. Reuse the exact same browser context for every URL.
                if self.single_session_mode and browser is not None:
                    self.store.update(
                        self.pid, status="crawling_verified_session",
                        browser_engine=f"{browser.engine} (same session)",
                        browser_reason="Verified once — crawling every page in the same dedicated session",
                    )
                    result = self.crawl_browser_page(u, browser, visible=False, reuse_main=True)
                    if result.get("needs_visible"):
                        # Clearance expired or the site explicitly challenged again. Bring back the
                        # SAME dedicated browser; never create another profile or ask for extension handoff.
                        if hasattr(browser, "restore"):
                            browser.restore(browser.main_page(), bring_to_front=True)
                        result = self.crawl_browser_page(u, browser, visible=True, reuse_main=True)
                    if result.get("stopped"):
                        self.write_outputs(None)
                        self.store.update(self.pid, status="stopped", browser_reason="Crawl stopped by user")
                        return
                    if not result.get("ok"):
                        self.errors.append({"type": "page", "url": u, "error": result.get("reason") or "Dedicated browser crawl failed", "source": "dedicated-browser-session"})
                else:
                    # Direct-first remains the default for ordinary websites.
                    if browser is None:
                        self.store.update(self.pid, status="crawling_direct", browser_engine="direct-http")
                    if preempt_browser_once and browser is None:
                        preempt_browser_once = False
                        is_edge = bool(home_discovery.get("edge_block"))
                        provider = home_discovery.get("edge_provider") or "CDN/WAF"
                        direct = {
                            "ok": False,
                            "needs_browser": True,
                            "human_verification": bool(home_discovery.get("explicit_human_challenge") and not is_edge),
                            "edge_block": is_edge,
                            "edge_provider": provider if is_edge else None,
                            "reason": (f"{provider} blocked direct discovery" if is_edge else "Security verification required"),
                        }
                    else:
                        direct = self.crawl_http_page(u)

                    if not direct.get("ok") and direct.get("needs_browser"):
                        self.browser_reason = direct.get("reason")

                        if direct.get("human_verification") or direct.get("edge_block"):
                            # Proven browser-attached flow: launch ONE real dedicated browser
                            # directly at the blocked URL BEFORE SiteMirror drives navigation.
                            # This is important for CDN/WAF blocks (CloudFront, Akamai, etc.)
                            # where an ordinary native browser request can succeed while direct
                            # HTTP/headless requests are rejected.
                            if browser is not None:
                                try:
                                    browser.close()
                                except Exception:
                                    pass
                            provider = direct.get("edge_provider") or ("Security verification" if direct.get("human_verification") else "CDN/WAF")
                            browser = self._launch_browser_and_wait_for_user(
                                u,
                                reason=direct.get("reason") or "Protected site requires a real browser",
                                provider=provider,
                            )
                            self._active_browser = browser
                            browser_visible = True
                            self.store.update(
                                self.pid,
                                status="browser_attaching",
                                browser_engine=f"{browser.engine} (dedicated session)",
                                browser_reason="Using the exact browser session you confirmed",
                            )
                            result = self.crawl_browser_page(
                                u, browser, visible=True, reuse_main=True, use_current_document=True
                            )
                            if result.get("stopped"):
                                self.write_outputs(None)
                                self.store.update(self.pid, status="stopped", browser_reason="Crawl stopped by user")
                                return
                            if not result.get("ok"):
                                self.errors.append({
                                    "type": "page", "url": u,
                                    "error": result.get("reason") or "Dedicated browser session was denied",
                                    "source": "dedicated-browser-session",
                                })
                                self.store.update(
                                    self.pid, status="failed",
                                    browser_reason=result.get("reason") or "Dedicated browser session was denied"
                                )
                                return
                            # Whether a human challenge was solved or the edge simply accepted
                            # the native browser request, keep this exact context for every page.
                            self.single_session_mode = True
                            self.session_verified = True
                            minimized = False
                            if hasattr(browser, "minimize"):
                                minimized = bool(browser.minimize(browser.main_page()))
                            self.store.update(
                                self.pid, status="crawling_verified_session",
                                browser_engine=f"{browser.engine} (same session)",
                                browser_reason="Dedicated browser session accepted — reusing it for the full crawl" + (" (browser minimized)" if minimized else ""),
                            )
                            browser_visible = False
                            self.discover_sitemaps_via_browser(browser.context)
                        else:
                            # Ordinary JS/high-fidelity fallback can stay headless.
                            self.store.update(self.pid, status="browser_fallback", browser_reason=self.browser_reason)
                            if browser is None:
                                browser = open_browser(
                                    headless=True,
                                    cookies=self._cookies_for_browser() or self.cookies,
                                    user_agent=self.user_agent,
                                )
                                self._active_browser = browser
                                browser_visible = False
                                self.browser_used = True
                                self.sync_browser_cookies(browser.context)
                            self.store.update(self.pid, browser_engine=f"{browser.engine} (headless)")
                            result = self.crawl_browser_page(u, browser, visible=False)
                            if result.get("stopped"):
                                self.write_outputs(None)
                                self.store.update(self.pid, status="stopped", browser_reason="Crawl stopped by user")
                                return
                            if result.get("ok"):
                                self.discover_sitemaps_via_browser(browser.context)
                            elif not result.get("needs_visible"):
                                # Record headless browser failures instead of
                                # silently churning with 0 pages for minutes.
                                self.errors.append({
                                    "type": "page", "url": u,
                                    "error": result.get("reason") or "Headless browser crawl failed",
                                    "source": "headless-browser",
                                })
                            if result.get("needs_visible"):
                                # Replace the headless context with ONE dedicated user-verified session.
                                try:
                                    browser.close()
                                except Exception:
                                    pass
                                browser = self._launch_browser_and_wait_for_user(
                                    u, reason=result.get("reason") or "Visible browser confirmation required", provider="Protected site"
                                )
                                self._active_browser = browser
                                browser_visible = True
                                result = self.crawl_browser_page(u, browser, visible=True, reuse_main=True, use_current_document=True)
                                if result.get("ok"):
                                    self.single_session_mode = True
                                    browser_visible = False
                                    self.discover_sitemaps_via_browser(browser.context)
                                elif result.get("stopped"):
                                    self.write_outputs(None)
                                    self.store.update(self.pid, status="stopped", browser_reason="Crawl stopped by user")
                                    return
                                else:
                                    self.errors.append({"type": "page", "url": u, "error": result.get("reason") or "Verification did not complete", "source": "dedicated-browser-session"})
                                    self.store.update(self.pid, status="failed", browser_reason=result.get("reason") or "Verification did not complete")
                                    return
                    elif not direct.get("ok") and not direct.get("needs_browser"):
                        self.errors.append({
                            "type": "page", "url": u,
                            "status": direct.get("status"),
                            "error": direct.get("reason") or "Page could not be mirrored",
                            "source": "direct-http",
                        })

                total_hint = max(total_hint, len(self.done) + len(self.queue))
                progress = min(98, int((len(self.done) / max(total_hint, 1)) * 100))
                self.store.update(
                    self.pid, progress=progress, pages=len(self.pages),
                    assets=len(self.assets), errors=len(self.errors)
                )

            # A crawl is only successful when at least one page was actually
            # fetched and persisted. Discovery metadata (robots/sitemaps) is
            # useful, but it is not a mirrored website. Previously a DNS or
            # connection failure could finish the queue and incorrectly report
            # `complete` with pages=0.
            self.raise_if_stopped()
            if not self.pages:
                reason = "No pages could be fetched"
                page_errors = [
                    str(item.get("error") or item.get("reason") or "").strip()
                    for item in self.errors
                    if item.get("type") == "page" and (item.get("error") or item.get("reason"))
                ]
                if page_errors:
                    reason += ": " + page_errors[0]
                self.errors.append({"type": "fatal", "url": self.start_url, "error": reason})
                self.write_outputs(None)
                try:
                    (self.project / "discovery.json").write_text(
                        json.dumps(self.discovery_report or {}, indent=2, ensure_ascii=False), encoding="utf-8"
                    )
                except Exception:
                    pass
                self.store.update(
                    self.pid, status="failed", progress=100,
                    pages=0, assets=len(self.assets), errors=len(self.errors),
                    current_url=None, browser_reason=reason,
                )
                return

            self.finalize_css()
            self.raise_if_stopped()
            seo_summary = finalize_site_seo(self.pages, self.redirects, self.sitemap_urls)
            # Promote discovery/standards problems into the main SEO issue inbox.
            site_issues = []
            robots_obj = self.discovery_report.get("robots", {})
            if robots_obj.get("valid") is False:
                site_issues.append({
                    "url": robots_obj.get("url") or urljoin(self.start_url, "/robots.txt"),
                    "title": "robots.txt", "code": "robots_unavailable", "severity": "fail",
                    "category": "discovery", "message": "robots.txt could not be read correctly",
                    "evidence": " · ".join(robots_obj.get("issues") or []) or str(robots_obj.get("status") or "unavailable"),
                    "recommendation": "Make robots.txt publicly fetchable if you publish crawler directives.",
                    "standard": True
                })
            valid_sitemaps = [x for x in self.discovery_report.get("sitemaps", []) if x.get("valid")]
            declared_bad = [x for x in self.discovery_report.get("sitemaps", []) if not x.get("valid") and x.get("source") in {"robots.txt", "homepage", "sitemap-index"}]
            for sm in declared_bad:
                site_issues.append({
                    "url": sm.get("url") or self.start_url, "title": "Sitemap",
                    "code": "sitemap_invalid", "severity": "fail", "category": "discovery",
                    "message": "Declared or indexed sitemap is invalid/unavailable",
                    "evidence": " · ".join(sm.get("issues") or []) or str(sm.get("status") or "unavailable"),
                    "recommendation": "Return a valid XML/text sitemap at the declared URL and update the sitemap index/robots directive.",
                    "standard": True
                })
            if not valid_sitemaps:
                site_issues.append({
                    "url": self.start_url, "title": "Sitemap discovery",
                    "code": "sitemap_not_found", "severity": "warning", "category": "discovery",
                    "message": "No valid XML/text sitemap was discovered",
                    "evidence": None,
                    "recommendation": "Publish a sitemap for easier discovery, especially on larger sites. This is recommended, not required for indexing.",
                    "standard": False
                })
            if site_issues:
                seo_summary.setdefault("issues", []).extend(site_issues)
                seo_summary["failed"] = seo_summary.get("failed", 0) + sum(1 for x in site_issues if x.get("severity") == "fail")
                seo_summary["warnings"] = seo_summary.get("warnings", 0) + sum(1 for x in site_issues if x.get("severity") == "warning")
                seo_summary["issues_total"] = len(seo_summary.get("issues", []))
                categories = seo_summary.setdefault("categories", {})
                categories["discovery"] = categories.get("discovery", 0) + len(site_issues)
                seo_summary["score"] = max(0, seo_summary.get("score", 100) - sum(7 if x.get("severity") == "fail" else 2 for x in site_issues))
            domain_meta = rdap_lookup(self.root_host)
            modified_values = [p.get("modified_at") for p in self.pages if p.get("modified_at")]
            published_values = [p.get("published_at") for p in self.pages if p.get("published_at")]
            home = next((p for p in self.pages if urlparse(p["url"]).path in {"", "/"}), self.pages[0] if self.pages else {})
            browser_label = "direct-http"
            if browser is not None:
                if self.single_session_mode:
                    browser_label = f"{browser.engine} (verified once · same session)"
                else:
                    browser_label = f"{browser.engine} ({'visible verification' if self.visible_browser_used else 'headless fallback'})"

            # Aggregate contact signals discovered across all crawled pages. Keep provenance
            # so the Website object can show where an email/phone was actually found.
            email_map = {}
            phone_map = {}
            for page in self.pages:
                page_url = page.get("url")
                contacts = page.get("contacts") or {}
                for item in contacts.get("emails") or []:
                    value = str((item or {}).get("value") or "").strip().lower()
                    if not value:
                        continue
                    rec = email_map.setdefault(value, {"value": value, "sources": set(), "pages": []})
                    if (item or {}).get("source"):
                        rec["sources"].add(str(item.get("source")))
                    if page_url and page_url not in rec["pages"]:
                        rec["pages"].append(page_url)
                for item in contacts.get("phones") or []:
                    value = str((item or {}).get("value") or "").strip()
                    digits = re.sub(r"\D", "", value)
                    if not value or not digits:
                        continue
                    rec = phone_map.setdefault(digits, {"value": value, "sources": set(), "pages": []})
                    if (item or {}).get("source"):
                        rec["sources"].add(str(item.get("source")))
                    if page_url and page_url not in rec["pages"]:
                        rec["pages"].append(page_url)

            contacts = {
                "emails": [
                    {"value": r["value"], "sources": sorted(r["sources"]), "pages": r["pages"][:20], "page_count": len(r["pages"])}
                    for r in sorted(email_map.values(), key=lambda x: (-len(x["pages"]), x["value"]))
                ],
                "phones": [
                    {"value": r["value"], "sources": sorted(r["sources"]), "pages": r["pages"][:20], "page_count": len(r["pages"])}
                    for r in sorted(phone_map.values(), key=lambda x: (-len(x["pages"]), x["value"]))
                ],
            }

            website = {
                "url": self.start_url,
                "domain": self.root_host,
                "title": home.get("title") or self.root_host,
                "favicon": (home.get("favicons") or [None])[0],
                "logo": (home.get("logo_candidates") or [None])[0],
                "domain_registration": domain_meta,
                "last_modified": max(modified_values) if modified_values else None,
                "last_published": max(published_values) if published_values else None,
                "pages": len(self.pages),
                "assets": len(self.assets),
                "issues": seo_summary.get("issues_total", 0) + len(self.errors),
                "seo": seo_summary,
                "mirror_fidelity": {
                    "score": int(round(sum((p.get("mirror_fidelity") or {}).get("asset_fidelity", 100) for p in self.pages) / max(len(self.pages), 1))),
                    "localized": sum((p.get("mirror_fidelity") or {}).get("localized", 0) for p in self.pages),
                    "unresolved": sum((p.get("mirror_fidelity") or {}).get("unresolved", 0) for p in self.pages),
                    "mode": "local-first-live-fallback",
                    "note": "Failed-to-localize resources retain their original live URL where possible; server-side actions and third-party services may still require the live origin."
                },
                "browser_engine": browser_label,
                "crawl_strategy": "direct-first-then-single-verified-browser-session",
                "browser_used": self.browser_used,
                "visible_browser_used": self.visible_browser_used,
                "browser_reason": self.browser_reason,
                "contacts": contacts,
                "discovery": {
                    "urls_discovered": self.discovery_report.get("urls_discovered", 0),
                    "sitemaps_found": len([x for x in self.discovery_report.get("sitemaps", []) if x.get("valid")]),
                    "robots_present": self.discovery_report.get("robots", {}).get("present", False),
                },
            }
            # Persist final discovery usage state as part of the Discovery object.
            self.discovery_report["browser_used"] = self.browser_used
            self.discovery_report["visible_browser_used"] = self.visible_browser_used
            self.discovery_report["browser_reason"] = self.browser_reason
            (self.project / "discovery.json").write_text(
                json.dumps(self.discovery_report, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            self.write_outputs(website)
            self.store.update(
                self.pid, status="complete", progress=100, pages=len(self.pages),
                assets=len(self.assets), errors=len(self.errors), website=website,
                browser_engine=browser_label, current_url=None
            )
        except CrawlStopped:
            self.write_outputs(None)
            try:
                (self.project / "discovery.json").write_text(
                    json.dumps(self.discovery_report or {}, indent=2, ensure_ascii=False), encoding="utf-8"
                )
            except Exception:
                pass
            self.store.update(
                self.pid, status="stopped", progress=min(99, int((len(self.done) / max(len(self.done) + len(self.queue), 1)) * 100)),
                pages=len(self.pages), assets=len(self.assets), errors=len(self.errors),
                current_url=None, browser_reason="Crawl stopped by user · downloaded data kept"
            )
        except Exception as exc:
            if self.stop_requested():
                self.write_outputs(None)
                self.store.update(self.pid, status="stopped", pages=len(self.pages), assets=len(self.assets), errors=len(self.errors), current_url=None, browser_reason="Crawl stopped by user · downloaded data kept")
                return
            self.errors.append({"type": "fatal", "error": str(exc)})
            self.write_outputs(None)
            try:
                (self.project / "discovery.json").write_text(
                    json.dumps(self.discovery_report or {}, indent=2, ensure_ascii=False), encoding="utf-8"
                )
            except Exception:
                pass
            self.store.update(self.pid, status="failed", errors=len(self.errors), current_url=None, browser_reason=str(exc))
        finally:
            self._active_browser = None
            pending = self._pending_browser
            self._pending_browser = None
            if pending is not None:
                try:
                    pending.terminate()
                except Exception:
                    pass
            if browser:
                browser.close()
            # The dedicated crawler profile exists only for the lifetime of this crawl.
            # Project/mirror/export data is preserved separately.
            try:
                shutil.rmtree(self.browser_profile_dir, ignore_errors=True)
            except Exception:
                pass

    def write_outputs(self, website):
        (self.project / "website.json").write_text(json.dumps(website or {}, indent=2, ensure_ascii=False), encoding="utf-8")
        (self.project / "pages.json").write_text(json.dumps(self.pages, indent=2, ensure_ascii=False), encoding="utf-8")
        (self.project / "assets.json").write_text(json.dumps(self.assets, indent=2, ensure_ascii=False), encoding="utf-8")
        (self.project / "errors.json").write_text(json.dumps(self.errors, indent=2, ensure_ascii=False), encoding="utf-8")
        (self.project / "redirects.json").write_text(json.dumps(self.redirects, indent=2, ensure_ascii=False), encoding="utf-8")
        contacts = (website or {}).get("contacts") or {"emails": [], "phones": []}
        (self.project / "contacts.json").write_text(json.dumps(contacts, indent=2, ensure_ascii=False), encoding="utf-8")
        with (self.project / "contacts.csv").open("w", newline="", encoding="utf-8-sig") as f:
            fields = ["type", "value", "page_count", "sources", "pages"]
            cw = csv.DictWriter(f, fieldnames=fields)
            cw.writeheader()
            for kind in ("emails", "phones"):
                for item in contacts.get(kind) or []:
                    cw.writerow({
                        "type": kind[:-1],
                        "value": item.get("value"),
                        "page_count": item.get("page_count", 0),
                        "sources": " | ".join(item.get("sources") or []),
                        "pages": " | ".join(item.get("pages") or []),
                    })
        seo_summary = (website or {}).get("seo") or {}
        (self.project / "seo-issues.json").write_text(json.dumps(seo_summary.get("issues", []), indent=2, ensure_ascii=False), encoding="utf-8")
        with (self.project / "url-manifest.csv").open("w", newline="", encoding="utf-8-sig") as f:
            fields = ["url", "status", "title", "h1", "meta_description", "published_at", "published_source", "modified_at", "modified_source", "canonical", "assets_count", "images", "links", "inlinks", "saved_path"]
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(self.pages)
        with (self.project / "seo-report.csv").open("w", newline="", encoding="utf-8-sig") as f:
            fields = ["url", "title", "severity", "category", "code", "message", "evidence", "recommendation"]
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(seo_summary.get("issues", []))
        with (self.project / "pages-seo.csv").open("w", newline="", encoding="utf-8-sig") as f:
            fields = [
                "url", "status", "title", "title_status", "title_chars", "title_estimated_px",
                "meta_description", "description_status", "description_chars", "description_estimated_px",
                "h1", "h1_count", "canonical", "published_at", "published_source",
                "modified_at", "modified_source", "url_age", "url_age_source", "inlinks",
                "mirror_asset_fidelity", "seo_issue_count"
            ]
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for page in self.pages:
                seo = page.get("seo") or {}
                title_obj = seo.get("title") or {}
                desc_obj = seo.get("description") or {}
                age = page.get("url_age") or {}
                fidelity = page.get("mirror_fidelity") or {}
                w.writerow({
                    "url": page.get("url"), "status": page.get("status"), "title": page.get("title"),
                    "title_status": title_obj.get("status"), "title_chars": title_obj.get("chars"),
                    "title_estimated_px": title_obj.get("estimated_px"),
                    "meta_description": page.get("meta_description"), "description_status": desc_obj.get("status"),
                    "description_chars": desc_obj.get("chars"), "description_estimated_px": desc_obj.get("estimated_px"),
                    "h1": page.get("h1"), "h1_count": seo.get("h1_count"), "canonical": page.get("canonical"),
                    "published_at": page.get("published_at"), "published_source": page.get("published_source"),
                    "modified_at": page.get("modified_at"), "modified_source": page.get("modified_source"),
                    "url_age": age.get("label"), "url_age_source": age.get("source"), "inlinks": page.get("inlinks"),
                    "mirror_asset_fidelity": fidelity.get("asset_fidelity"),
                    "seo_issue_count": len(seo.get("issues") or []),
                })


def zip_project(project: Path, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
        for p in project.rglob("*"):
            if p.is_file():
                z.write(p, p.relative_to(project))
    return output
