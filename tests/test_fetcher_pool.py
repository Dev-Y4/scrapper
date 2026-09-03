from __future__ import annotations

from pathlib import Path

from scraper.fetchers.pool import FetcherPool
from scraper.outcomes import Outcome

FIXTURES = Path(__file__).parent / "fixtures"
GOOD = (FIXTURES / "trustpilot_page.html").read_text(encoding="utf-8")
GATED = (FIXTURES / "tp_gated.html").read_text(encoding="utf-8")
BLOCKED = (FIXTURES / "tp_interstitial.html").read_text(encoding="utf-8")


class ScriptedFetcher:
    def __init__(self, name, responses):
        self.name = name
        self.responses = list(responses)
        self.calls = 0
        self.closed = False

    def fetch(self, url):
        self.calls += 1
        if not self.responses:
            return BLOCKED
        return self.responses.pop(0)

    def close(self):
        self.closed = True


def pool_of(*fetchers, **kwargs):
    kwargs.setdefault("sleep", lambda s: None)
    return FetcherPool(list(fetchers), **kwargs)


def test_happy_path_uses_the_first_fetcher():
    primary = ScriptedFetcher("chrome", [GOOD])
    backup = ScriptedFetcher("jina", [GOOD])
    result = pool_of(primary, backup).fetch("https://x")

    assert result.outcome is Outcome.OK
    assert result.fetcher == "chrome"
    assert backup.calls == 0


def test_transient_block_retries_the_same_fetcher_first():
    primary = ScriptedFetcher("chrome", [BLOCKED, GOOD])
    result = pool_of(primary, ScriptedFetcher("jina", [GOOD])).fetch("https://x")

    assert result.outcome is Outcome.OK
    assert result.fetcher == "chrome"
    assert primary.calls == 2


def test_exhausted_retries_fail_over_to_the_backup():
    primary = ScriptedFetcher("chrome", [BLOCKED, BLOCKED, BLOCKED])
    backup = ScriptedFetcher("jina", [GOOD])
    result = pool_of(primary, backup, max_retries=2).fetch("https://x")

    assert result.outcome is Outcome.OK
    assert result.fetcher == "jina"


def test_gated_never_retries_and_never_fails_over():
    primary = ScriptedFetcher("chrome", [GATED, GOOD])
    backup = ScriptedFetcher("jina", [GOOD])
    result = pool_of(primary, backup).fetch("https://x")

    assert result.outcome is Outcome.GATED
    assert primary.calls == 1
    assert backup.calls == 0


def test_circuit_breaker_demotes_a_failing_fetcher_for_the_rest_of_the_run():
    primary = ScriptedFetcher("chrome", [BLOCKED] * 20)
    backup = ScriptedFetcher("jina", [GOOD] * 20)
    pool = pool_of(primary, backup, max_retries=0, failure_threshold=3)

    for _ in range(3):
        pool.fetch("https://x")
    calls_after_tripping = primary.calls

    pool.fetch("https://x")
    pool.fetch("https://x")

    assert primary.calls == calls_after_tripping  # never called again
    assert pool.stats()["demoted_chrome"] == 1


def test_all_fetchers_down_returns_the_last_outcome():
    pool = pool_of(ScriptedFetcher("chrome", [BLOCKED] * 5),
                   ScriptedFetcher("jina", [BLOCKED] * 5), max_retries=1)
    result = pool.fetch("https://x")
    assert result.outcome is Outcome.TRANSIENT_BLOCK


def test_stats_count_outcomes_per_fetcher():
    pool = pool_of(ScriptedFetcher("chrome", [GOOD, GOOD]))
    pool.fetch("https://x")
    pool.fetch("https://y")
    assert pool.stats()["ok_chrome"] == 2


def test_close_closes_every_fetcher():
    primary = ScriptedFetcher("chrome", [GOOD])
    backup = ScriptedFetcher("jina", [GOOD])
    pool_of(primary, backup).close()
    assert primary.closed and backup.closed
