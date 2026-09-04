from __future__ import annotations

import json

from scraper import run
from scraper.models import Review


class _NullChrome:
    name = "chrome"

    def close(self):
        return None


class _NullPool:
    def stats(self):
        return {}

    def close(self):
        return None


def test_dry_run_writes_json_and_touches_no_sheet(tmp_path, monkeypatch, capsys):
    output = tmp_path / "out.json"

    class FakeAdapter:
        platform = "Trustpilot"

        def __init__(self, *args, **kwargs):
            pass

        def collect(self, domain, target=None, on_view=None):
            reviews = [Review(review_id="trustpilot:a", platform="Trustpilot",
                              company_domain=domain, rating=5)]
            if on_view:
                on_view("languages=all", reviews)
            return run.CollectResult(reviews=reviews,
                                     stats={"pages_fetched": 3, "unique_reviews": 1})

    monkeypatch.setattr(run, "TrustpilotAdapter", FakeAdapter)
    monkeypatch.setattr(run, "build_pool", lambda **kwargs: _NullPool())

    exit_code = run.main(["--company", "Trademax",
                          "--trustpilot", "www.trademax.se",
                          "--dry-run", "--output", str(output),
                          "--checkpoint-dir", str(tmp_path)])

    assert exit_code == 0
    rows = json.loads(output.read_text(encoding="utf-8"))
    assert rows[0]["review_id"] == "trustpilot:a"
    assert "pages_fetched" in capsys.readouterr().out


def test_requires_at_least_one_platform_target(capsys):
    assert run.main(["--company", "Trademax"]) == 2
    assert "at least one of --trustpilot" in capsys.readouterr().err.lower()


def test_both_platforms_write_into_one_tab(tmp_path, monkeypatch):
    output = tmp_path / "out.json"

    class FakeTrustpilot:
        def __init__(self, *a, **k):
            pass

        def collect(self, domain, target=None, on_view=None):
            return run.CollectResult(
                reviews=[Review(review_id="trustpilot:a", platform="Trustpilot")],
                stats={"unique_reviews": 1})

    class FakeGoogle:
        def __init__(self, *a, **k):
            pass

        def collect(self, query, company_name, max_scrolls=80):
            return run.CollectResult(
                reviews=[Review(review_id="google:b", platform="Google")],
                stats={"unique_reviews": 1})

    monkeypatch.setattr(run, "TrustpilotAdapter", FakeTrustpilot)
    monkeypatch.setattr(run, "GoogleMapsAdapter", FakeGoogle)
    monkeypatch.setattr(run, "build_pool", lambda **kwargs: _NullPool())
    monkeypatch.setattr(run, "ChromeCDPFetcher", lambda **kwargs: _NullChrome())

    exit_code = run.main(["--company", "Trademax",
                          "--trustpilot", "www.trademax.se",
                          "--google", "Trademax Stockholm",
                          "--dry-run", "--output", str(output),
                          "--checkpoint-dir", str(tmp_path)])

    assert exit_code == 0
    platforms = {row["platform"] for row in json.loads(output.read_text())}
    assert platforms == {"Trustpilot", "Google"}


def test_google_only_run_needs_no_trustpilot_domain(tmp_path, monkeypatch):
    class FakeGoogle:
        def __init__(self, *a, **k):
            pass

        def collect(self, query, company_name, max_scrolls=80):
            return run.CollectResult(
                reviews=[Review(review_id="google:b", platform="Google")],
                stats={})

    monkeypatch.setattr(run, "GoogleMapsAdapter", FakeGoogle)
    monkeypatch.setattr(run, "build_pool", lambda **kwargs: _NullPool())
    monkeypatch.setattr(run, "ChromeCDPFetcher", lambda **kwargs: _NullChrome())

    assert run.main(["--company", "T", "--google", "T Stockholm", "--dry-run",
                     "--output", str(tmp_path / "o.json"),
                     "--checkpoint-dir", str(tmp_path)]) == 0


def test_one_chrome_is_shared_by_both_platforms(tmp_path, monkeypatch):
    """Playwright's sync API cannot be started twice in a process. Closing the
    Trustpilot pool and then launching a second Chrome for Google raises
    'Sync API inside the asyncio loop', so both platforms share one browser."""
    built = []

    class CountingChrome:
        name = "chrome"

        def __init__(self, *a, **k):
            built.append(1)

        def close(self):
            return None

    class FakeTrustpilot:
        def __init__(self, *a, **k):
            pass

        def collect(self, domain, target=None, on_view=None):
            return run.CollectResult(reviews=[], stats={})

    class FakeGoogle:
        def __init__(self, chrome, *a, **k):
            self.chrome = chrome

        def collect(self, query, company_name, max_scrolls=80):
            assert isinstance(self.chrome, CountingChrome)
            return run.CollectResult(reviews=[], stats={})

    monkeypatch.setattr(run, "ChromeCDPFetcher", CountingChrome)
    monkeypatch.setattr(run, "TrustpilotAdapter", FakeTrustpilot)
    monkeypatch.setattr(run, "GoogleMapsAdapter", FakeGoogle)

    run.main(["--company", "T", "--trustpilot", "d.com", "--google", "T Sthlm",
              "--dry-run", "--output", str(tmp_path / "o.json"),
              "--checkpoint-dir", str(tmp_path)])

    assert len(built) == 1, "expected exactly one Chrome, got {0}".format(len(built))


def test_running_bare_asks_which_company(tmp_path, monkeypatch):
    """Run with no flags, it should ask — the original script did, and that is
    how a person actually uses this."""
    asked = []
    answers = iter(["Gymshark", "www.gymshark.com", "", "250"])

    class FakeTrustpilot:
        def __init__(self, *a, **k):
            pass

        def collect(self, domain, target=None, on_view=None):
            assert domain == "www.gymshark.com"
            assert target == 250
            return run.CollectResult(
                reviews=[Review(review_id="trustpilot:a", platform="Trustpilot")],
                stats={})

    def fake_ask(prompt):
        asked.append(prompt)
        return next(answers)

    monkeypatch.setattr(run, "TrustpilotAdapter", FakeTrustpilot)
    monkeypatch.setattr(run, "build_pool", lambda **kwargs: _NullPool())
    monkeypatch.setattr(run, "ChromeCDPFetcher", lambda **kwargs: _NullChrome())
    monkeypatch.setattr(run, "_ask", fake_ask)
    monkeypatch.setattr(run, "_interactive", lambda: True)

    code = run.main(["--dry-run", "--output", str(tmp_path / "o.json"),
                     "--checkpoint-dir", str(tmp_path)])

    assert code == 0
    assert any("company" in p.lower() for p in asked)
    assert any("trustpilot" in p.lower() for p in asked)


def test_flags_skip_the_questions(tmp_path, monkeypatch):
    def boom(prompt):
        raise AssertionError("should not prompt when flags are given")

    class FakeTrustpilot:
        def __init__(self, *a, **k):
            pass

        def collect(self, domain, target=None, on_view=None):
            return run.CollectResult(reviews=[], stats={})

    monkeypatch.setattr(run, "TrustpilotAdapter", FakeTrustpilot)
    monkeypatch.setattr(run, "build_pool", lambda **kwargs: _NullPool())
    monkeypatch.setattr(run, "ChromeCDPFetcher", lambda **kwargs: _NullChrome())
    monkeypatch.setattr(run, "_ask", boom)
    monkeypatch.setattr(run, "_interactive", lambda: True)

    assert run.main(["--company", "X", "--trustpilot", "d.com", "--dry-run",
                     "--output", str(tmp_path / "o.json"),
                     "--checkpoint-dir", str(tmp_path)]) == 0


def test_non_interactive_with_no_flags_errors_instead_of_hanging(monkeypatch, capsys):
    """A cron job or a piped run must fail loudly, never block on a prompt."""
    monkeypatch.setattr(run, "_interactive", lambda: False)
    assert run.main([]) == 2
    assert "at least one of --trustpilot" in capsys.readouterr().err.lower()


def test_blank_answer_skips_that_platform(tmp_path, monkeypatch):
    answers = iter(["Acme", "", "Acme Stockholm", ""])

    class FakeGoogle:
        def __init__(self, *a, **k):
            pass

        def collect(self, query, company_name, max_scrolls=80):
            assert query == "Acme Stockholm"
            return run.CollectResult(reviews=[], stats={})

    monkeypatch.setattr(run, "GoogleMapsAdapter", FakeGoogle)
    monkeypatch.setattr(run, "build_pool", lambda **kwargs: _NullPool())
    monkeypatch.setattr(run, "ChromeCDPFetcher", lambda **kwargs: _NullChrome())
    monkeypatch.setattr(run, "_ask", lambda prompt: next(answers))
    monkeypatch.setattr(run, "_interactive", lambda: True)

    assert run.main(["--dry-run", "--output", str(tmp_path / "o.json"),
                     "--checkpoint-dir", str(tmp_path)]) == 0
