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
                 sleep: Callable[[float], Any] = time.sleep):
        if not fetchers:
            raise ValueError("FetcherPool needs at least one fetcher")
        self.fetchers = list(fetchers)
        self.max_retries = max_retries
        self.failure_threshold = failure_threshold
        self._sleep = sleep
        self._consecutive_failures: Dict[str, int] = {}
        self._demoted: Dict[str, bool] = {}
        self._counts: Counter = Counter()

    def _available(self) -> List[Fetcher]:
        return [f for f in self.fetchers if not self._demoted.get(f.name)]

    def _record(self, fetcher: Fetcher, outcome: Outcome) -> None:
        self._counts["{0}_{1}".format(outcome.value.split("_")[0], fetcher.name)] += 1
        if outcome is Outcome.TRANSIENT_BLOCK:
            failures = self._consecutive_failures.get(fetcher.name, 0) + 1
            self._consecutive_failures[fetcher.name] = failures
            if failures >= self.failure_threshold and not self._demoted.get(fetcher.name):
                self._demoted[fetcher.name] = True
                self._counts["demoted_{0}".format(fetcher.name)] += 1
        else:
            self._consecutive_failures[fetcher.name] = 0

    def fetch(self, url: str) -> FetchResult:
        last = FetchResult("", Outcome.TRANSIENT_BLOCK, "none")

        for fetcher in self._available():
            for attempt in range(self.max_retries + 1):
                html = fetcher.fetch(url)
                outcome = classify(html)
                self._record(fetcher, outcome)
                last = FetchResult(html, outcome, fetcher.name)

                if outcome is not Outcome.TRANSIENT_BLOCK:
                    return last
                if self._demoted.get(fetcher.name):
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
