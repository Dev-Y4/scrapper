from __future__ import annotations

from datetime import date

import pytest

from scraper.platforms.google.adapter import GoogleMapsAdapter


class FakeLocator:
    def __init__(self, page, name):
        self.page = page
        self.name = name

    @property
    def first(self):
        return self

    def click(self, timeout=None):
        self.page.clicks.append(self.name)


class FakePage:
    """Grows the node list on each scroll, then plateaus — the real loop's
    stop condition is 'unique count stopped growing'."""

    def __init__(self, batches):
        self.batches = list(batches)
        self.nodes = []
        self.clicks = []
        self.url = "https://www.google.com/maps/place/Trademax"

    def set_default_timeout(self, ms):
        return None

    def goto(self, url, wait_until=None):
        self.url = url

    def locator(self, selector):
        return FakeLocator(self, selector)

    def get_by_role(self, role):
        raise RuntimeError("no sort menu in the fake")

    def evaluate(self, js, *args):
        if "scrollTop" in js:
            if self.batches:
                self.nodes.extend(self.batches.pop(0))
            return 1
        return list(self.nodes)


def node(review_id):
    return {"id": review_id, "rating": "5 stars", "text": "ok",
            "when": "a month ago", "reply": ""}


class FakeChrome:
    name = "chrome"

    def __init__(self, page):
        self._page = page

    def page(self):
        return self._page


def adapter_for(page):
    return GoogleMapsAdapter(FakeChrome(page), now=lambda: "2026-09-04T10:00:00Z",
                             today=lambda: date(2026, 9, 4), sleep=lambda s: None)


def test_scrolls_until_the_unique_count_plateaus():
    page = FakePage([[node("a"), node("b")], [node("c")], [], [], [], []])
    result = adapter_for(page).collect("Trademax Stockholm", "Trademax")

    assert sorted(r.review_id for r in result.reviews) == ["google:a", "google:b", "google:c"]
    assert result.stats["unique_reviews"] == 3


def test_duplicate_dom_nodes_do_not_inflate_the_count():
    page = FakePage([[node("a"), node("a"), node("a")], [], [], [], []])
    result = adapter_for(page).collect("Trademax Stockholm", "Trademax")
    assert result.stats["unique_reviews"] == 1
    assert result.stats["raw_nodes"] == 3


def test_rows_are_tagged_with_the_place_and_company():
    page = FakePage([[node("a")], [], [], [], []])
    review = adapter_for(page).collect("Trademax Stockholm", "Trademax").reviews[0]
    assert review.location == "Trademax Stockholm"
    assert review.company_name == "Trademax"
    assert review.platform == "Google"
    assert review.date_precision == "relative"


def test_missing_scroll_container_ends_cleanly():
    class NoPane(FakePage):
        def evaluate(self, js, *args):
            if "scrollTop" in js:
                return -1
            return []

    result = adapter_for(NoPane([])).collect("q", "c")
    assert result.reviews == []


@pytest.mark.live
def test_live_google_collection():
    """Real Chrome against real Maps. Run explicitly: pytest -m live"""
    from scraper.fetchers.chrome import ChromeCDPFetcher

    chrome = ChromeCDPFetcher()
    try:
        result = GoogleMapsAdapter(chrome).collect("Trademax Möbler Stockholm",
                                                   "Trademax")
        assert result.stats["unique_reviews"] > 50
        assert all(r.rating for r in result.reviews[:20])
    finally:
        chrome.close()
