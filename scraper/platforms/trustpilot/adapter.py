from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional

from scraper.fetchers.pool import FetcherPool
from scraper.models import Review
from scraper.outcomes import Outcome
from scraper.platforms.trustpilot import parser
from scraper.platforms.trustpilot.planner import View, plan_views

LISTING_URL = "https://www.trustpilot.com/review/{domain}{query}"


def _utc_now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class CollectResult:
    reviews: List[Review] = field(default_factory=list)
    stats: Dict[str, int] = field(default_factory=dict)


class TrustpilotAdapter:
    platform = "Trustpilot"

    def __init__(self, pool: FetcherPool, now: Callable[[], str] = _utc_now):
        self.pool = pool
        self.now = now

    # -- url ------------------------------------------------------------
    def _url(self, domain: str, view: View, page: int) -> str:
        """Page 1 is requested WITHOUT a page parameter on purpose.

        Trustpilot canonicalises an explicit `page=1` to the unfiltered default
        view and silently drops every filter, regardless of parameter order, so
        including it would collapse all filter slicing into a single view."""
        query = view.query()
        base = LISTING_URL.format(domain=domain, query=query)
        if page <= 1:
            return base
        separator = "&" if query else "?"
        return base + "{0}page={1}".format(separator, page)

    # -- collection ------------------------------------------------------
    def collect(self, domain: str, target: Optional[int] = None,
                on_view: Optional[Callable[[str, List[Review]], None]] = None) -> CollectResult:
        counts: Counter = Counter()
        page_one_cache: Dict[str, str] = {}
        languages: List[Dict[str, object]] = []
        topics: List[str] = []

        def probe(view: View) -> Optional[int]:
            """Fetch page 1 of a view for its totalCount. The HTML is cached so
            harvesting never re-fetches page 1."""
            result = self.pool.fetch(self._url(domain, view, 1))
            counts["pages_fetched"] += 1
            counts[result.outcome.value] += 1
            if result.outcome is not Outcome.OK:
                return None
            page_one_cache[view.label()] = result.html
            props = parser.page_props(result.html)
            if props is None:
                return None
            if not languages:
                languages.extend(parser.parse_languages(props))
            if not topics:
                topics.extend(parser.parse_topics(props))
            pagination = parser.parse_pagination(props)
            return pagination["total"] if pagination else None

        planned = plan_views(probe, languages=languages, topics=topics, target=target)
        counts["views_planned"] = len(planned)

        seen = set()
        reviews: List[Review] = []

        for plan in planned:
            label = plan.view.label()
            view_reviews: List[Review] = []
            for page in plan.pages:
                if page == 1 and label in page_one_cache:
                    html = page_one_cache[label]
                    fetcher_name = self._last_fetcher()
                    outcome = Outcome.OK
                else:
                    result = self.pool.fetch(self._url(domain, plan.view, page))
                    counts["pages_fetched"] += 1
                    counts[result.outcome.value] += 1
                    html, outcome, fetcher_name = (result.html, result.outcome,
                                                   result.fetcher)

                if outcome is Outcome.GATED:
                    # A login gate below page 11 means the planner mis-planned.
                    if page <= 10:
                        counts["gated_below_cap"] += 1
                    break
                if outcome is not Outcome.OK:
                    break

                props = parser.page_props(html)
                if props is None:
                    break
                for review in parser.parse_reviews(
                        props, domain=domain, source_view=label,
                        fetcher=fetcher_name, fetched_at=self.now()):
                    if review.review_id in seen:
                        counts["duplicates_skipped"] += 1
                        continue
                    seen.add(review.review_id)
                    view_reviews.append(review)

            reviews.extend(view_reviews)
            if on_view is not None:
                on_view(label, view_reviews)

            if target is not None and len(reviews) >= target:
                break

        counts["unique_reviews"] = len(reviews)
        counts.update(self.pool.stats())
        return CollectResult(reviews=reviews, stats=dict(counts))

    def _last_fetcher(self) -> str:
        for key, value in self.pool.stats().items():
            if key.startswith("ok_") and value:
                return key[len("ok_"):]
        return "unknown"
