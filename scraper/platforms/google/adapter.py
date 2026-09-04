from __future__ import annotations

import time
from collections import Counter
from datetime import date, datetime
from typing import Any, Callable, Dict, List

from scraper.platforms.google.extract import EXTRACT_JS, SCROLL_JS, build_reviews
from scraper.platforms.trustpilot.adapter import CollectResult

MAPS_SEARCH = "https://www.google.com/maps/search/{query}"
PLATEAU_ROUNDS = 5

REVIEW_TAB_SELECTORS = ('button[aria-label*="Reviews"]', 'button[aria-label*="review"]',
                        'button[aria-label*="ecension"]')
SORT_SELECTORS = ('button[aria-label*="Sort"]', 'button[data-value="Sort"]',
                  'button[aria-label*="Sortera"]')


def _utc_now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


class GoogleMapsAdapter:
    """No filter slicing: Google has no login gate, so one place yields all of
    its reviews. The plan is simply 'scroll until it stops growing'."""

    platform = "Google"

    def __init__(self, chrome: Any, now: Callable[[], str] = _utc_now,
                 today: Callable[[], date] = date.today,
                 sleep: Callable[[float], Any] = time.sleep):
        self.chrome = chrome
        self.now = now
        self.today = today
        self._sleep = sleep

    def _click_first(self, page, selectors) -> bool:
        for selector in selectors:
            try:
                page.locator(selector).first.click(timeout=8000)
                return True
            except Exception:
                continue
        return False

    def collect(self, query: str, company_name: str,
                max_scrolls: int = 80) -> CollectResult:
        counts: Counter = Counter()
        page = self.chrome.page()

        url = MAPS_SEARCH.format(query=query.replace(" ", "+"))
        try:
            page.goto(url, wait_until="domcontentloaded")
        except Exception as error:
            # A long Trustpilot run leaves the renderer exhausted; it dies on
            # the first Maps navigation. Rebuild the page and try once more.
            if "crash" not in str(error).lower() or not hasattr(self.chrome,
                                                                "restart_page"):
                raise
            counts["page_rebuilt"] += 1
            page = self.chrome.restart_page()
            page.goto(url, wait_until="domcontentloaded")
        self._sleep(5)

        if "/maps/place/" not in getattr(page, "url", ""):
            try:
                page.locator("a.hfpxzc").first.click(timeout=15000)
                self._sleep(5)
            except Exception:
                counts["place_click_failed"] += 1

        def reviews_present():
            try:
                return bool(page.evaluate(EXTRACT_JS))
            except Exception:
                return False

        def revert():
            """Undo a click that emptied the pane by reloading the place."""
            counts["reverted_after_click"] = 1
            page.goto(page.url, wait_until="domcontentloaded")
            self._sleep(4)

        # Only open the Reviews tab if reviews are not already on screen. A
        # search that lands straight on a place shows them, and clicking then
        # navigated AWAY from them: 28 review nodes before the click, 0 after.
        if not reviews_present():
            if self._click_first(page, REVIEW_TAB_SELECTORS):
                self._sleep(3)
            else:
                counts["reviews_tab_not_found"] += 1
        else:
            counts["reviews_already_visible"] = 1

        # Sorting by Newest makes date bracketing meaningful. The control's
        # accessible name is locale-dependent, so treat it as optional.
        if self._click_first(page, SORT_SELECTORS):
            self._sleep(1.5)
            try:
                page.get_by_role("menuitemradio").nth(1).click(timeout=6000)
                counts["sorted_newest"] = 1
                self._sleep(3)
            except Exception:
                counts["sort_option_not_found"] += 1
        else:
            counts["sort_control_not_found"] += 1

        # Sorting is a convenience; the reviews are the point. If the click
        # emptied the pane, take the reviews unsorted rather than nothing.
        if not reviews_present():
            revert()

        nodes: List[dict] = []
        seen_ids = set()
        plateau = 0

        for _ in range(max_scrolls):
            if page.evaluate(SCROLL_JS) == -1:
                counts["no_scroll_container"] += 1
                break
            self._sleep(1.4)
            nodes = page.evaluate(EXTRACT_JS)
            unique_now = set(n.get("id") for n in nodes if n.get("id"))
            if unique_now == seen_ids:
                plateau += 1
                if plateau >= PLATEAU_ROUNDS:
                    break
            else:
                plateau = 0
                seen_ids = unique_now
            counts["scrolls"] += 1

        reviews = build_reviews(nodes, company_name=company_name, place=query,
                                run_date=self.today(), fetched_at=self.now(),
                                fetcher=getattr(self.chrome, "name", "chrome"))
        counts["raw_nodes"] = len(nodes)
        counts["unique_reviews"] = len(reviews)
        return CollectResult(reviews=reviews, stats=dict(counts))
