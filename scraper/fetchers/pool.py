from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Dict, List

from scraper.fetchers.base import Fetcher
from scraper.outcomes import Outcome, classify


@dataclass
class FetchResult:
    html: str
    outcome: Outcome
    fetcher: str


class FetcherPool:
    """Tries fetchers in order. Retries a transient block, then fails over.
    Three consecutive transient blocks demote a fetcher for the rest of the run.

    A GATED response short-circuits everything: it is a product gate, so a
    second attempt from a different IP returns exactly the same thing."""

    def __init__(self, fetchers: List[Fetcher], max_retries: int = 2,
                 failure_threshold: int = 3,
                 sleep: Callable[[float], Any] = time.sleep,
                 clock: Callable[[], float] = time.monotonic,
                 cooldown: float = 180.0):
        if not fetchers:
            raise ValueError("FetcherPool needs at least one fetcher")
        self.fetchers = list(fetchers)
        self.max_retries = max_retries
        self.failure_threshold = failure_threshold
        self.cooldown = cooldown
        self._sleep = sleep
        self._clock = clock
        self._consecutive_failures: Dict[str, int] = {}
        self._demoted_at: Dict[str, float] = {}
        self._counts: Counter = Counter()

    def _is_demoted(self, name: str) -> bool:
        """Demotion expires. A long run passes through rough patches — losing a
        fetcher permanently ends the run with plan left unspent."""
        since = self._demoted_at.get(name)
        if since is None:
            return False
        if self._clock() - since >= self.cooldown:
            del self._demoted_at[name]
            self._consecutive_failures[name] = 0
            self._counts["recovered_{0}".format(name)] += 1
            return False
        return True

    def _available(self) -> List[Fetcher]:
        return [f for f in self.fetchers if not self._is_demoted(f.name)]

    def _record(self, fetcher: Fetcher, outcome: Outcome) -> None:
        self._counts["{0}_{1}".format(outcome.value.split("_")[0], fetcher.name)] += 1
        if outcome is Outcome.TRANSIENT_BLOCK:
            failures = self._consecutive_failures.get(fetcher.name, 0) + 1
            self._consecutive_failures[fetcher.name] = failures
            if failures >= self.failure_threshold and fetcher.name not in self._demoted_at:
                self._demoted_at[fetcher.name] = self._clock()
                self._counts["demoted_{0}".format(fetcher.name)] += 1
        else:
            self._consecutive_failures[fetcher.name] = 0

    def fetch(self, url: str) -> FetchResult:
        last = FetchResult("", Outcome.TRANSIENT_BLOCK, "none")

        available = self._available()
        if not available:
            # Everything is cooling. Waiting it out keeps the run alive;
            # returning immediately would chew through every remaining view in
            # seconds and finish with the plan unspent.
            self._counts["all_fetchers_cooling"] += 1
            soonest = min(self._demoted_at.values())
            wait = (soonest + self.cooldown) - self._clock()
            if wait > 0:
                self._sleep(wait)
            available = self._available()
            if not available:
                return last

        for fetcher in available:
            for attempt in range(self.max_retries + 1):
                html = fetcher.fetch(url)
                outcome = classify(html)
                self._record(fetcher, outcome)
                last = FetchResult(html, outcome, fetcher.name)

                if outcome is not Outcome.TRANSIENT_BLOCK:
                    return last
                if fetcher.name in self._demoted_at:
                    break
                if attempt < self.max_retries:
                    self._sleep(2.0 * (attempt + 1))
        return last

    def stats(self) -> Dict[str, int]:
        return dict(self._counts)

    def close(self) -> None:
        for fetcher in self.fetchers:
            try:
                fetcher.close()
            except Exception:
                pass
