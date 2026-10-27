from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.request import urlopen
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, Browser, BrowserContext, Playwright, Page


def _strip_www(host: str) -> str:
    """Remove a leading ``www.`` from a hostname, compatible with Python < 3.9."""
    host = (host or "").lower()
    return host[4:] if host.startswith("www.") else host


@dataclass
class BrowserSession:
    playwright: Playwright
    browser: Browser
    context: BrowserContext
    engine: str
    executable: Optional[str] = None
    process: Optional[subprocess.Popen] = None
    debug_port: Optional[int] = None
    persistent: bool = False
    minimized: bool = False

    def main_page(self, preferred_url: Optional[str] = None) -> Page:
        """Return the most useful page in the attached browser context.

        Externally launched Chrome can briefly create more than one tab (for example an
        empty New Tab plus the requested target URL). The original implementation always
        returned pages[0], which could accidentally select the blank tab and force an
        automation-driven navigation. For CDN/WAF sites that defeats the whole point of
        launching the first request natively.
        """
        pages = list(self.context.pages)
        if preferred_url and pages:
            try:
                want = urlparse(preferred_url)
                want_host = _strip_www(want.hostname)
                # Prefer the exact URL/host, scanning newest tabs first.
                for page in reversed(pages):
                    pu = page.url or ''
                    if pu == preferred_url:
                        return page
                for page in reversed(pages):
                    pu = page.url or ''
                    if pu in {'', 'about:blank', 'chrome://newtab/'}:
                        continue
                    phost = _strip_www(urlparse(pu).hostname)
                    if phost and phost == want_host:
                        return page
            except Exception:
                pass
        for page in reversed(pages):
            if (page.url or '') not in {'', 'about:blank', 'chrome://newtab/'}:
                return page
        if pages:
            return pages[-1]
        return self.context.new_page()

    def wait_for_page(self, preferred_url: str, timeout: float = 8.0) -> Page:
        deadline = time.time() + max(0.5, timeout)
        page = self.main_page(preferred_url)
        while time.time() < deadline:
            try:
                if page.url not in {'', 'about:blank', 'chrome://newtab/'}:
                    return page
            except Exception:
                pass
            time.sleep(0.15)
            page = self.main_page(preferred_url)
        return page

    def _window_id(self, page: Optional[Page] = None):
        page = page or self.main_page()
        try:
            cdp = self.context.new_cdp_session(page)
            info = cdp.send("Browser.getWindowForTarget")
            return cdp, info.get("windowId")
        except Exception:
            return None, None

    def _mac_app_name(self) -> Optional[str]:
        low = (self.engine or "").lower()
        if "edge" in low:
            return "Microsoft Edge"
        if "brave" in low:
            return "Brave Browser"
        if "playwright" in low:
            return "Google Chrome for Testing"
        if "chrome" in low:
            return "Google Chrome"
        return None

    def minimize(self, page: Optional[Page] = None) -> bool:
        cdp, wid = self._window_id(page)
        if cdp and wid is not None:
            try:
                cdp.send("Browser.setWindowBounds", {
                    "windowId": wid,
                    "bounds": {"windowState": "minimized"},
                })
                self.minimized = True
                return True
            except Exception:
                pass
        if platform.system().lower() == "darwin":
            app_name = self._mac_app_name()
            if app_name:
                try:
                    subprocess.run(["osascript", "-e", 'tell application "%s" to set miniaturized of every window to true' % app_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
                    self.minimized = True
                    return True
                except Exception:
                    pass
        return False

    def restore(self, page: Optional[Page] = None, bring_to_front: bool = True) -> bool:
        page = page or self.main_page()
        cdp, wid = self._window_id(page)
        ok = False
        if cdp and wid is not None:
            try:
                cdp.send("Browser.setWindowBounds", {
                    "windowId": wid,
                    "bounds": {"windowState": "normal"},
                })
                ok = True
            except Exception:
                pass
        if platform.system().lower() == "darwin" and not ok:
            app_name = self._mac_app_name()
            if app_name:
                try:
                    subprocess.run(["osascript", "-e", 'tell application "%s" to set miniaturized of every window to false' % app_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
                    ok = True
                except Exception:
                    pass
        if bring_to_front:
            try:
                page.bring_to_front()
                ok = True
            except Exception:
                pass
        self.minimized = False
        return ok

    def close(self) -> None:
        try:
            self.browser.close()
        except Exception:
            pass
        try:
            self.playwright.stop()
        except Exception:
            pass
        if self.process is not None and self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=3)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass


@dataclass
class PendingBrowserLaunch:
    engine: str
    executable: str
    process: subprocess.Popen
    debug_port: int
    profile_dir: Path
    start_url: str

    def terminate(self) -> None:
        if self.process is not None and self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=3)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass


def _launch_external_user_browser(*, profile_dir: Path, start_url: str = "about:blank") -> PendingBrowserLaunch:
    profile_dir = Path(profile_dir).expanduser().resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)

    candidates = _candidate_executables()
    if candidates:
        label, executable = candidates[0]
    else:
        label, executable = "playwright-chromium", _managed_chromium_executable()

    port = _free_port()
    args = [
        executable,
        "--remote-debugging-port=%d" % port,
        "--remote-allow-origins=*",
        "--user-data-dir=%s" % str(profile_dir),
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
        "--disable-features=TranslateUI",
        "--disable-background-timer-throttling",
        "--disable-backgrounding-occluded-windows",
        "--disable-renderer-backgrounding",
        "--new-window",
        start_url or "about:blank",
    ]
    process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        _wait_debug_port(port)
        return PendingBrowserLaunch(
            engine="%s-dedicated-session" % label,
            executable=executable,
            process=process,
            debug_port=port,
            profile_dir=profile_dir,
            start_url=start_url or "about:blank",
        )
    except Exception:
        try:
            process.terminate()
        except Exception:
            pass
        raise


def attach_user_session_browser(pending: PendingBrowserLaunch) -> BrowserSession:
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.connect_over_cdp("http://127.0.0.1:%d" % pending.debug_port)
        contexts = list(browser.contexts)
        context = contexts[0] if contexts else browser.new_context(ignore_https_errors=True)
        session = BrowserSession(
            pw, browser, context, pending.engine,
            executable=pending.executable, process=pending.process, debug_port=pending.debug_port,
            persistent=True,
        )
        if pending.start_url and pending.start_url != "about:blank":
            session.wait_for_page(pending.start_url, timeout=8.0)
        return session
    except Exception:
        try:
            pw.stop()
        except Exception:
            pass
        pending.terminate()
        raise


def launch_user_session_browser(*, profile_dir: Path, start_url: str = "about:blank") -> PendingBrowserLaunch:
    """Launch the dedicated real browser but do not attach automation yet.

    This mirrors the successful original SiteMirror script: the user gets a completely
    normal first navigation and confirms that the real page is visible before crawler
    control attaches to that same browser process/profile.
    """
    return _launch_external_user_browser(profile_dir=profile_dir, start_url=start_url)


def _candidate_executables():
    system = platform.system().lower()
    candidates = []
    if system == "windows":
        env = os.environ
        roots = [env.get("PROGRAMFILES"), env.get("PROGRAMFILES(X86)"), env.get("LOCALAPPDATA")]
        paths = [
            ("chrome", r"Google\Chrome\Application\chrome.exe"),
            ("edge", r"Microsoft\Edge\Application\msedge.exe"),
            ("brave", r"BraveSoftware\Brave-Browser\Application\brave.exe"),
        ]
        for root in roots:
            if not root:
                continue
            for label, rel in paths:
                p = Path(root) / rel
                if p.exists():
                    candidates.append((label, str(p)))
    elif system == "darwin":
        for label, path in [
            ("chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            ("edge", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
            ("brave", "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"),
            ("chrome", str(Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome")),
            ("edge", str(Path.home() / "Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")),
            ("brave", str(Path.home() / "Applications/Brave Browser.app/Contents/MacOS/Brave Browser")),
        ]:
            if Path(path).exists():
                candidates.append((label, path))
    else:
        for label, name in [
            ("chrome", "google-chrome"), ("edge", "microsoft-edge"),
            ("brave", "brave-browser"), ("chromium", "chromium")
        ]:
            p = shutil.which(name)
            if p:
                candidates.append((label, p))
    return candidates


def _managed_chromium_executable() -> str:
    pw = sync_playwright().start()
    try:
        executable = pw.chromium.executable_path
    finally:
        pw.stop()
    if executable and Path(executable).exists():
        return executable
    subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
    pw = sync_playwright().start()
    try:
        return pw.chromium.executable_path
    finally:
        pw.stop()


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


def _wait_debug_port(port: int, timeout: float = 20.0) -> None:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            with urlopen("http://127.0.0.1:%d/json/version" % port, timeout=0.5) as r:
                if r.status == 200:
                    json.loads(r.read().decode("utf-8", errors="replace"))
                    return
        except Exception as exc:
            last = exc
        time.sleep(0.15)
    raise RuntimeError("Crawler browser did not expose its debugging session: %s" % (last or "timeout"))


def open_user_session_browser(*, profile_dir: Path, start_url: str = "about:blank", attach_delay: float = 0.0) -> BrowserSession:
    pending = launch_user_session_browser(profile_dir=profile_dir, start_url=start_url)
    try:
        if attach_delay and start_url and start_url != "about:blank":
            time.sleep(max(0.0, min(float(attach_delay), 10.0)))
        return attach_user_session_browser(pending)
    except Exception:
        pending.terminate()
        raise


def open_browser(*, headless: bool = False, cookies=None, user_agent: Optional[str] = None) -> BrowserSession:
    """Ordinary background rendering browser used only when no human challenge exists."""
    pw = sync_playwright().start()
    launch_args = ["--disable-blink-features=AutomationControlled"]
    browser = None
    label = "playwright-chromium"
    executable = None

    for candidate_label, candidate_path in _candidate_executables():
        try:
            browser = pw.chromium.launch(
                headless=headless,
                executable_path=candidate_path,
                args=launch_args,
            )
            label = candidate_label
            executable = candidate_path
            break
        except Exception:
            continue

    if browser is None:
        try:
            browser = pw.chromium.launch(headless=headless, args=launch_args)
        except Exception:
            try:
                pw.stop()
            except Exception:
                pass
            subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True)
            pw = sync_playwright().start()
            browser = pw.chromium.launch(headless=headless, args=launch_args)

    kwargs = {"viewport": {"width": 1440, "height": 1000}, "ignore_https_errors": True}
    if user_agent:
        kwargs["user_agent"] = user_agent
    context = browser.new_context(**kwargs)
    if cookies:
        normalized = []
        for c in cookies:
            item = {k: v for k, v in c.items() if k in {"name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite"}}
            if item.get("sameSite") not in {"Strict", "Lax", "None"}:
                item.pop("sameSite", None)
            if item.get("name") and item.get("value"):
                normalized.append(item)
        if normalized:
            try:
                context.add_cookies(normalized)
            except Exception:
                pass
    return BrowserSession(pw, browser, context, label, executable)
