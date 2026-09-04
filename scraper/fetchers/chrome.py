from __future__ import annotations

import random
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional, Tuple

from scraper.fetchers.base import Fetcher

CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
DEFAULT_PROFILE = str(Path.home() / ".trustpilot-chrome")
DEFAULT_PORT = 9333


def _is_crash(error: Exception) -> bool:
    return "crash" in str(error).lower()


class ChromeCDPFetcher(Fetcher):
    """Real Chrome, attached over CDP. The profile persists the WAF clearance
    cookie, so the challenge is paid once, not once per run."""

    name = "chrome"

    def __init__(self, port: int = DEFAULT_PORT, profile: Optional[str] = None,
                 delay_range: Tuple[float, float] = (3.5, 7.0),
                 sleep: Callable[[float], Any] = time.sleep,
                 rng: Callable[[float, float], float] = random.uniform,
                 headless: bool = False):
        self.port = port
        self.profile = profile or DEFAULT_PROFILE
        self.delay_range = delay_range
        self._sleep = sleep
        self._rng = rng
        self.headless = headless
        self._process = None
        self._playwright = None
        self._browser = None
        self._page = None

    # -- lifecycle -------------------------------------------------------
    def _port_open(self) -> bool:
        try:
            urllib.request.urlopen(
                "http://localhost:{0}/json/version".format(self.port), timeout=2)
            return True
        except Exception:
            return False

    def _launch(self) -> None:
        args = [CHROME_PATH,
                "--remote-debugging-port={0}".format(self.port),
                "--user-data-dir={0}".format(self.profile),
                "--no-first-run", "--no-default-browser-check",
                "--hide-crash-restore-bubble", "--disable-session-crashed-bubble",
                "--restore-last-session=false"]
        if self.headless:
            args.append("--headless=new")
        self._process = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL)
        for _ in range(30):
            if self._port_open():
                return
            time.sleep(1)
        raise RuntimeError(
            "Chrome did not expose the debug port on {0} — is another Chrome "
            "already using this profile?".format(self.port))

    def _ensure_page(self):
        if self._page is not None:
            return self._page
        if not self._port_open():
            self._launch()
        from playwright.sync_api import sync_playwright
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.connect_over_cdp(
            "http://localhost:{0}".format(self.port))
        context = (self._browser.contexts[0] if self._browser.contexts
                   else self._browser.new_context())
        self._page = context.pages[0] if context.pages else context.new_page()
        self._page.set_default_timeout(45000)
        return self._page

    def page(self):
        """The live Playwright page. Adapters that must interact with a page
        (scroll, click) use this instead of fetch()."""
        return self._ensure_page()

    def restart_page(self):
        """Drop a dead renderer and open a fresh one. A long run accumulates
        hundreds of navigations in one tab and the renderer eventually crashes;
        that should cost a page, not the run."""
        try:
            if self._page is not None:
                self._page.close()
        except Exception:
            pass
        self._page = None
        return self._ensure_page()

    # -- fetching --------------------------------------------------------
    def fetch(self, url: str) -> str:
        page = self._ensure_page()
        try:
            page.goto(url, wait_until="domcontentloaded")
            return page.content()
        except Exception as error:
            if not _is_crash(error):
                return ""
        finally:
            self._sleep(self._rng(*self.delay_range))

        # The renderer died. Rebuild once and try again before giving up.
        try:
            page = self.restart_page()
            page.goto(url, wait_until="domcontentloaded")
            return page.content()
        except Exception:
            return ""

    def close(self) -> None:
        for closer in (lambda: self._browser and self._browser.close(),
                       lambda: self._playwright and self._playwright.stop(),
                       lambda: self._process and self._process.terminate()):
            try:
                closer()
            except Exception:
                pass
        self._page = None
        self._browser = None
        self._playwright = None
        self._process = None
