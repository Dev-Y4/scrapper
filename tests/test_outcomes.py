from __future__ import annotations

from pathlib import Path

from scraper.outcomes import Outcome, classify

FIXTURES = Path(__file__).parent / "fixtures"


def read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_ok_page():
    assert classify(read("trustpilot_page.html")) is Outcome.OK


def test_login_redirect_is_gated_not_blocked():
    assert classify(read("tp_gated.html")) is Outcome.GATED


def test_waf_interstitial_is_transient():
    assert classify(read("tp_interstitial.html")) is Outcome.TRANSIENT_BLOCK


def test_tiny_body_is_transient():
    assert classify("<html><body>nope</body></html>") is Outcome.TRANSIENT_BLOCK


def test_valid_page_with_no_reviews_is_empty():
    assert classify(read("tp_empty.html")) is Outcome.EMPTY


def test_empty_string_is_transient():
    assert classify("") is Outcome.TRANSIENT_BLOCK


def test_only_transient_is_retryable():
    assert Outcome.TRANSIENT_BLOCK.retryable is True
    assert Outcome.GATED.retryable is False
    assert Outcome.OK.retryable is False
    assert Outcome.EMPTY.retryable is False
