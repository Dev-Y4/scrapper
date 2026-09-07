from __future__ import annotations

from scraper.fetchers.jina import JINA_BASE, JinaFetcher


class FakeResponse:
    def __init__(self, text: str):
        self.text = text
        self.status_code = 200

    def raise_for_status(self):
        return None


def test_prefixes_the_reader_base_and_asks_for_html(monkeypatch):
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers or {}
        return FakeResponse("<html>ok</html>")

    monkeypatch.setattr("scraper.fetchers.jina.requests.get", fake_get)
    fetcher = JinaFetcher()

    assert fetcher.fetch("https://www.trustpilot.com/review/x") == "<html>ok</html>"
    assert captured["url"] == JINA_BASE + "https://www.trustpilot.com/review/x"
    assert captured["headers"]["X-Return-Format"] == "html"
    assert "Authorization" not in captured["headers"]


def test_sends_bearer_token_when_a_key_is_configured(monkeypatch):
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured["headers"] = headers or {}
        return FakeResponse("<html>ok</html>")

    monkeypatch.setattr("scraper.fetchers.jina.requests.get", fake_get)
    JinaFetcher(api_key="jina_secret").fetch("https://example.com")

    assert captured["headers"]["Authorization"] == "Bearer jina_secret"


def test_name_is_recorded_for_row_provenance():
    assert JinaFetcher().name == "jina"


def test_network_errors_surface_as_empty_html(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        raise OSError("connection reset")

    monkeypatch.setattr("scraper.fetchers.jina.requests.get", fake_get)
    # Empty html classifies as TRANSIENT_BLOCK, which the pool retries.
    assert JinaFetcher().fetch("https://example.com") == ""
