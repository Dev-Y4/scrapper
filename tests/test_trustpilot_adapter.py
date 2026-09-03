from __future__ import annotations

from pathlib import Path

from scraper.fetchers.pool import FetcherPool
from scraper.platforms.trustpilot.adapter import TrustpilotAdapter

FIXTURES = Path(__file__).parent / "fixtures"
GOOD = (FIXTURES / "trustpilot_page.html").read_text(encoding="utf-8")
GATED = (FIXTURES / "tp_gated.html").read_text(encoding="utf-8")
EMPTY = (FIXTURES / "tp_empty.html").read_text(encoding="utf-8")


class RecordingFetcher:
    def __init__(self, name="chrome", responder=None):
        self.name = name
        self.urls = []
        self.responder = responder or (lambda url: GOOD)

    def fetch(self, url):
        self.urls.append(url)
        return self.responder(url)

    def close(self):
        return None


def adapter_with(fetcher):
    pool = FetcherPool([fetcher], max_retries=0, sleep=lambda s: None)
    return TrustpilotAdapter(pool, now=lambda: "2026-09-04T10:00:00Z")


def test_collect_dedupes_repeated_reviews_across_pages():
    fetcher = RecordingFetcher()
    result = adapter_with(fetcher).collect("www.trademax.se")

    ids = [review.review_id for review in result.reviews]
    assert sorted(ids) == ["trustpilot:aaa111", "trustpilot:bbb222"]
    assert result.stats["duplicates_skipped"] > 0


def test_collect_never_requests_a_page_above_ten():
    fetcher = RecordingFetcher()
    adapter_with(fetcher).collect("www.trademax.se")

    pages = [int(url.split("page=")[1].split("&")[0])
             for url in fetcher.urls if "page=" in url]
    assert pages, "expected paged requests"
    assert max(pages) <= 10


def test_gated_page_stops_that_view_immediately():
    def responder(url):
        if "page=3" in url:
            return GATED
        return GOOD

    fetcher = RecordingFetcher(responder=responder)
    result = adapter_with(fetcher).collect("www.trademax.se")

    pages = [int(url.split("page=")[1].split("&")[0])
             for url in fetcher.urls if "page=" in url]
    assert 4 not in pages
    assert result.stats["gated"] >= 1


def test_empty_page_ends_the_view_without_error():
    fetcher = RecordingFetcher(responder=lambda url: EMPTY if "page=2" in url else GOOD)
    result = adapter_with(fetcher).collect("www.trademax.se")
    assert result.stats["pages_fetched"] >= 1


def test_rows_carry_provenance():
    result = adapter_with(RecordingFetcher()).collect("www.trademax.se")
    review = result.reviews[0]
    assert review.fetcher == "chrome"
    assert review.fetched_at == "2026-09-04T10:00:00Z"
    assert review.company_domain == "www.trademax.se"
    assert review.source_view


def test_target_limits_the_work():
    fetcher = RecordingFetcher()
    result = adapter_with(fetcher).collect("www.trademax.se", target=20)
    assert result.stats["views_planned"] >= 1
