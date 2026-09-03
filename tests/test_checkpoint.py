from __future__ import annotations

from scraper.checkpoint import Checkpoint
from scraper.models import Review


def test_extend_then_reload_from_disk(tmp_path):
    path = tmp_path / "trademax.json"
    checkpoint = Checkpoint(str(path))
    checkpoint.extend([Review(review_id="trustpilot:a", platform="Trustpilot", rating=3)])

    reloaded = Checkpoint(str(path))
    assert [row["review_id"] for row in reloaded.reviews()] == ["trustpilot:a"]
    assert reloaded.reviews()[0]["rating"] == 3


def test_extend_accumulates_across_calls(tmp_path):
    checkpoint = Checkpoint(str(tmp_path / "c.json"))
    checkpoint.extend([Review(review_id="t:a", platform="Trustpilot")])
    checkpoint.extend([Review(review_id="t:b", platform="Trustpilot")])
    assert len(Checkpoint(str(tmp_path / "c.json")).reviews()) == 2


def test_clear_removes_the_file(tmp_path):
    path = tmp_path / "c.json"
    checkpoint = Checkpoint(str(path))
    checkpoint.extend([Review(review_id="t:a", platform="Trustpilot")])
    checkpoint.clear()
    assert not path.exists()
    assert Checkpoint(str(path)).reviews() == []


def test_missing_file_is_not_an_error(tmp_path):
    assert Checkpoint(str(tmp_path / "nope.json")).reviews() == []


def test_corrupt_file_is_ignored_rather_than_crashing_a_run(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("{not json", encoding="utf-8")
    assert Checkpoint(str(path)).reviews() == []
