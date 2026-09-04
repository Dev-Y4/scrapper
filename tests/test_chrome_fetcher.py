from __future__ import annotations

import pytest

from scraper.fetchers.chrome import ChromeCDPFetcher


class FakePage:
    def __init__(self, html: str):
        self._html = html
        self.visited = []

    def set_default_timeout(self, ms):
        return None

    def goto(self, url, wait_until=None):
        self.visited.append(url)

    def content(self):
        return self._html


def test_fetch_returns_page_html_and_paces_itself():
    slept = []
    fetcher = ChromeCDPFetcher(sleep=lambda s: slept.append(s),
                               rng=lambda a, b: 5.0)
    fetcher._page = FakePage("<html>real page</html>")

    assert fetcher.fetch("https://example.com/a") == "<html>real page</html>"
    assert fetcher._page.visited == ["https://example.com/a"]
    assert slept == [5.0]


def test_navigation_failure_returns_empty_html():
    class Boom(FakePage):
        def goto(self, url, wait_until=None):
            raise RuntimeError("navigation timeout")

    fetcher = ChromeCDPFetcher(sleep=lambda s: None, rng=lambda a, b: 0.0)
    fetcher._page = Boom("")
    assert fetcher.fetch("https://example.com") == ""


def test_name_is_recorded_for_row_provenance():
    assert ChromeCDPFetcher().name == "chrome"


@pytest.mark.live
def test_live_fetch_of_a_trustpilot_page():
    """Launches real Chrome. Run explicitly: pytest -m live"""
    from scraper.outcomes import Outcome, classify

    fetcher = ChromeCDPFetcher()
    try:
        html = fetcher.fetch(
            "https://www.trustpilot.com/review/www.trademax.se?languages=en")
        outcome = classify(html)
        # A cold profile may eat one challenge; retry once.
        if outcome is Outcome.TRANSIENT_BLOCK:
            outcome = classify(fetcher.fetch(
                "https://www.trustpilot.com/review/www.trademax.se?languages=en"))
        assert outcome is Outcome.OK
    finally:
        fetcher.close()


def test_a_crashed_page_is_replaced_rather_than_being_fatal():
    """After ~300 navigations the renderer crashed and killed the whole run.
    A dead page is replaced on the next request."""
    fetcher = ChromeCDPFetcher(sleep=lambda s: None, rng=lambda a, b: 0.0)
    rebuilt = {"n": 0}

    class Dead(FakePage):
        def goto(self, url, wait_until=None):
            raise RuntimeError("Page.goto: Page crashed")

    def fake_ensure():
        rebuilt["n"] += 1
        page = FakePage("<html>fresh</html>") if rebuilt["n"] > 1 else Dead("")
        fetcher._page = page
        return page

    fetcher._ensure_page = fake_ensure
    assert fetcher.fetch("https://example.com") == "<html>fresh</html>"
    assert rebuilt["n"] == 2, "expected one rebuild after the crash"


def test_a_normal_navigation_failure_still_returns_empty_without_rebuilding():
    fetcher = ChromeCDPFetcher(sleep=lambda s: None, rng=lambda a, b: 0.0)
    calls = {"n": 0}

    class Timeout(FakePage):
        def goto(self, url, wait_until=None):
            raise RuntimeError("Timeout 45000ms exceeded")

    def fake_ensure():
        calls["n"] += 1
        page = Timeout("")
        fetcher._page = page
        return page

    fetcher._ensure_page = fake_ensure
    assert fetcher.fetch("https://example.com") == ""
    assert calls["n"] == 1
