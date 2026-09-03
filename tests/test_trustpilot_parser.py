from __future__ import annotations

from pathlib import Path

from scraper.platforms.trustpilot import parser

FIXTURE = Path(__file__).parent / "fixtures" / "trustpilot_page.html"


def props():
    return parser.page_props(FIXTURE.read_text(encoding="utf-8"))


def test_page_props_extracted():
    assert props() is not None


def test_page_props_returns_none_without_next_data():
    assert parser.page_props("<html><body>Verifying your connection</body></html>") is None


def test_parse_pagination():
    assert parser.parse_pagination(props()) == {"total": 797, "pages": 40}


def test_parse_languages_excludes_all_and_keeps_counts():
    languages = parser.parse_languages(props())
    codes = [lang["code"] for lang in languages]
    assert "all" not in codes
    assert {"code": "sv", "count": 64871} in languages
    assert codes == ["sv", "en", "da"]


def test_parse_reviews_maps_every_field():
    reviews = parser.parse_reviews(props(), domain="www.trademax.se",
                                   source_view="languages=en", fetcher="chrome",
                                   fetched_at="2026-09-04T10:00:00Z")
    assert len(reviews) == 2
    first = reviews[0]
    assert first.review_id == "trustpilot:aaa111"
    assert first.platform == "Trustpilot"
    assert first.url == "https://www.trustpilot.com/reviews/aaa111"
    assert first.rating == 1
    assert first.raw_text == "Arrived with parts missing."
    assert first.review_date == "2026-09-02"
    assert first.experience_date == "2026-09-02"
    assert first.date_precision == "exact"
    assert first.support_reply == "Hej! We are sorry."
    assert first.support_reply_date == "2026-09-03"
    assert first.review_source == "Organic"
    assert first.is_verified == "FALSE"
    assert first.country == "SE"
    assert first.source_view == "languages=en"
    assert first.fetcher == "chrome"


def test_review_without_reply_has_empty_reply_fields():
    second = parser.parse_reviews(props(), domain="www.trademax.se",
                                  source_view="languages=en", fetcher="chrome",
                                  fetched_at="2026-09-04T10:00:00Z")[1]
    assert second.support_reply == ""
    assert second.support_reply_date == ""
    assert second.is_verified == "TRUE"


def test_consumer_name_is_parsed_but_lives_only_on_the_object():
    first = parser.parse_reviews(props(), domain="www.trademax.se", source_view="v",
                                 fetcher="chrome", fetched_at="t")[0]
    assert first.consumer_name == "Ada L"
