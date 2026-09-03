from __future__ import annotations

import json

from scraper import run
from scraper.models import Review


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
    assert "at least one of" in capsys.readouterr().err.lower()
