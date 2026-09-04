from __future__ import annotations

from pathlib import Path

from scraper.fetchers.pool import FetcherPool
from scraper.platforms.trustpilot.adapter import TrustpilotAdapter
from scraper.platforms.trustpilot.planner import View

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


def test_on_view_callback_fires_per_view_for_checkpointing():
    seen = []
    adapter = adapter_with(RecordingFetcher())
    adapter.collect("www.trademax.se",
                    on_view=lambda label, reviews: seen.append((label, len(reviews))))
    assert seen
    assert all(isinstance(label, str) for label, _ in seen)


def test_page_one_url_omits_the_page_param():
    """Trustpilot canonicalises `page=1` to the unfiltered default view and
    silently drops every filter. Page 1 of a view must be requested without it,
    or all filter slicing collapses to one view."""
    adapter = adapter_with(RecordingFetcher())
    view = View.of(languages="sv").with_("stars", 1)

    assert adapter._url("www.trademax.se", view, 1) == (
        "https://www.trustpilot.com/review/www.trademax.se?languages=sv&stars=1")
    assert adapter._url("www.trademax.se", view, 2) == (
        "https://www.trustpilot.com/review/www.trademax.se?languages=sv&stars=1&page=2")


def test_no_request_ever_carries_page_one():
    fetcher = RecordingFetcher()
    adapter_with(fetcher).collect("www.trademax.se")
    assert not any("page=1&" in url or url.endswith("page=1") for url in fetcher.urls)


NO_TOPICS = GOOD.replace(
    '"topicSummaryLocalizedTopics":[{"id":"product","displayName":"Product"},'
    '{"id":"delivery_service","displayName":"Delivery service"},'
    '{"id":"quality","displayName":"Quality"}],', "")


def test_planning_probes_the_richest_language_to_discover_topics():
    """Topic ids are published only on the dominant language's page, never on
    languages=all, so they must be fetched from there or the topic dimension
    silently never engages — which is exactly why a 3000-target run came back
    with 2173."""
    assert "topicSummaryLocalizedTopics" not in NO_TOPICS

    def responder(url):
        return NO_TOPICS if url.endswith("?languages=all") else GOOD

    fetcher = RecordingFetcher(responder=responder)
    adapter_with(fetcher).collect("www.trademax.se", target=50)
    assert any(url.endswith("?languages=sv") for url in fetcher.urls), fetcher.urls


def test_each_view_is_probed_at_most_once():
    fetcher = RecordingFetcher()
    adapter_with(fetcher).collect("www.trademax.se", target=50)
    root = [url for url in fetcher.urls if url.endswith("?languages=all")]
    assert len(root) == 1, root


def page_with(total, ids, topics=True):
    """A Trustpilot page carrying a chosen totalCount and review ids."""
    reviews = ",".join(
        '{{"id":"{0}","rating":5,"title":"t","text":"body","language":"en",'
        '"location":"GB","consumer":{{"displayName":"X"}},'
        '"dates":{{"experiencedDate":"2026-01-01T00:00:00.000Z",'
        '"publishedDate":"2026-01-02T00:00:00.000Z"}},"reply":null,'
        '"labels":{{"verification":{{"isVerified":true,"reviewSourceName":"Organic"}}}}}}'.format(i)
        for i in ids)
    topic_block = ('"topicSummaryLocalizedTopics":[{"id":"product","displayName":"P"}],'
                   if topics else "")
    return ('<html><body><script id="__NEXT_DATA__" type="application/json">'
            '{{"props":{{"pageProps":{{{topics}'
            '"businessUnit":{{"displayName":"Acme"}},'
            '"filters":{{"pagination":{{"currentPage":1,"perPage":20,'
            '"totalCount":{total},"totalPages":{pages}}},'
            '"reviewStatistics":{{"reviewLanguages":['
            '{{"isoCode":"all","reviewCount":50000,"displayName":"all"}},'
            '{{"isoCode":"en","reviewCount":40000,"displayName":"English"}}]}}}},'
            '"reviews":[{reviews}]}}}}}}</script></body></html>').format(
                topics=topic_block, total=total, pages=max(1, total // 20),
                reviews=reviews)


def test_harvest_visits_every_base_view_even_after_the_target_is_met():
    """The planner balances the sample across star ratings; stopping the moment
    the count is reached threw that away. A target of 100 on an English company
    returned only 1-star and 2-star reviews."""
    counter = {"n": 0}

    def responder(url):
        # Distinct ids per request so every view yields new rows.
        counter["n"] += 1
        base = counter["n"] * 1000
        total = 50000 if "stars=" not in url else 5000
        return page_with(total, ["r{0}".format(base + i) for i in range(20)])

    fetcher = RecordingFetcher(responder=responder)
    result = adapter_with(fetcher).collect("acme.com", target=40)

    # Only harvest URLs count — the five languages=all probes are planning.
    stars = {url.split("stars=")[1].split("&")[0]
             for url in fetcher.urls
             if "stars=" in url and "languages=en" in url and "topics=" not in url}
    assert stars >= {"1", "2", "3", "4", "5"}, stars
    assert result.stats["unique_reviews"] >= 40
