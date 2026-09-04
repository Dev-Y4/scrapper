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


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_a_demoted_fetcher_returns_after_the_cooldown():
    """A rough patch must not kill a fetcher for the rest of a long run. A
    3000-review run lost both fetchers to transient blocks and stopped early
    at 2753 with plan left unspent."""
    clock = FakeClock()
    primary = ScriptedFetcher("chrome", [BLOCKED, BLOCKED, BLOCKED])
    backup = ScriptedFetcher("jina", [GOOD] * 10)
    pool = FetcherPool([primary, backup], max_retries=0, failure_threshold=3,
                       sleep=lambda s: None, clock=clock, cooldown=120.0)

    for _ in range(3):
        pool.fetch("https://x")
    assert pool.stats()["demoted_chrome"] == 1

    calls_when_demoted = primary.calls
    pool.fetch("https://x")
    assert primary.calls == calls_when_demoted, "still demoted before cooldown"

    clock.advance(121)
    primary.responses = [GOOD]
    result = pool.fetch("https://x")
    assert primary.calls > calls_when_demoted, "should be retried after cooldown"
    assert result.fetcher == "chrome"


def test_recovery_resets_the_failure_streak():
    clock = FakeClock()
    primary = ScriptedFetcher("chrome", [BLOCKED, BLOCKED, BLOCKED])
    pool = FetcherPool([primary, ScriptedFetcher("jina", [GOOD] * 10)],
                       max_retries=0, failure_threshold=3, sleep=lambda s: None,
                       clock=clock, cooldown=60.0)
    for _ in range(3):
        pool.fetch("https://x")
    clock.advance(61)
    primary.responses = [GOOD]
    pool.fetch("https://x")
    clock.advance(1)
    primary.responses = [GOOD]
    assert pool.fetch("https://x").fetcher == "chrome"


def test_every_fetcher_cooling_down_is_reported_not_silent():
    clock = FakeClock()
    pool = FetcherPool([ScriptedFetcher("chrome", [BLOCKED] * 9),
                        ScriptedFetcher("jina", [BLOCKED] * 9)],
                       max_retries=0, failure_threshold=3, sleep=lambda s: None,
                       clock=clock, cooldown=60.0)
    for _ in range(6):
        pool.fetch("https://x")
    result = pool.fetch("https://x")
    assert result.outcome is Outcome.TRANSIENT_BLOCK
    assert pool.stats()["all_fetchers_cooling"] >= 1


def test_all_cooling_waits_for_the_soonest_recovery_instead_of_burning_the_plan():
    """Returning instantly while everything is cooling makes the adapter chew
    through every remaining view in seconds and end the run with nothing."""
    clock = FakeClock()
    slept = []

    def sleep(seconds):
        slept.append(seconds)
        clock.advance(seconds)

    primary = ScriptedFetcher("chrome", [BLOCKED] * 3)
    backup = ScriptedFetcher("jina", [BLOCKED] * 3)
    pool = FetcherPool([primary, backup], max_retries=0, failure_threshold=3,
                       sleep=sleep, clock=clock, cooldown=60.0)
    for _ in range(3):
        pool.fetch("https://x")

    primary.responses = [GOOD]
    backup.responses = [GOOD]
    result = pool.fetch("https://x")

    assert any(s >= 1 for s in slept), "expected a wait, got {0}".format(slept)
    assert result.outcome is Outcome.OK
