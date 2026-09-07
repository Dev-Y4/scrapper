from __future__ import annotations

from datetime import date

from scraper.platforms.google.extract import (build_reviews, dedupe_nodes,
                                              parse_rating)


def node(review_id, rating="5 stars", text="good", when="a month ago", reply=""):
    return {"id": review_id, "rating": rating, "text": text, "when": when,
            "reply": reply}


def test_parse_rating_from_aria_label():
    assert parse_rating("5 stars") == 5
    assert parse_rating("1 star") == 1
    assert parse_rating("4,0 stjärnor") == 4


def test_parse_rating_of_junk_is_none():
    assert parse_rating("") is None
    assert parse_rating("no number here") is None


def test_dedupe_collapses_repeated_nodes_for_one_review():
    nodes = [node("aaa"), node("aaa"), node("bbb")]
    assert [n["id"] for n in dedupe_nodes(nodes)] == ["aaa", "bbb"]


def test_dedupe_prefers_the_node_carrying_review_text():
    nodes = [node("aaa", text=""), node("aaa", text="the real body")]
    assert dedupe_nodes(nodes)[0]["text"] == "the real body"


def test_build_reviews_maps_to_the_shared_schema():
    reviews = build_reviews([node("aaa", rating="2 stars", when="2 months ago")],
                            company_name="Trademax", place="Trademax Stockholm",
                            run_date=date(2026, 9, 4), fetched_at="2026-09-04T10:00:00Z",
                            fetcher="chrome")
    review = reviews[0]
    assert review.review_id == "google:aaa"
    assert review.platform == "Google"
    assert review.rating == 2
    assert review.review_date == "2026-07-06"
    assert review.date_precision == "relative"
    assert review.location == "Trademax Stockholm"
    assert review.company_name == "Trademax"
    assert review.source_view == "place=Trademax Stockholm"
    assert review.fetcher == "chrome"


def test_rating_only_reviews_are_kept():
    """~40% of Google reviews carry a star and no text. They still count."""
    reviews = build_reviews([node("aaa", text="")], company_name="T", place="P",
                            run_date=date(2026, 9, 4), fetched_at="t", fetcher="chrome")
    assert len(reviews) == 1
    assert reviews[0].raw_text == ""
    assert reviews[0].rating == 5


def test_owner_reply_is_carried_over():
    reviews = build_reviews([node("aaa", reply="Thanks for the feedback")],
                            company_name="T", place="P", run_date=date(2026, 9, 4),
                            fetched_at="t", fetcher="chrome")
    assert reviews[0].support_reply == "Thanks for the feedback"


def test_nodes_without_an_id_are_dropped():
    assert build_reviews([node("")], company_name="T", place="P",
                         run_date=date(2026, 9, 4), fetched_at="t",
                         fetcher="chrome") == []
