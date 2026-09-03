from __future__ import annotations

from datetime import date

from scraper.platforms.google.dates import bracket_dates, relative_to_days


def test_singular_and_plural_units():
    assert relative_to_days("a month ago") == 30
    assert relative_to_days("2 months ago") == 60
    assert relative_to_days("a week ago") == 7
    assert relative_to_days("3 weeks ago") == 21
    assert relative_to_days("a year ago") == 365
    assert relative_to_days("2 years ago") == 730
    assert relative_to_days("a day ago") == 1
    assert relative_to_days("5 days ago") == 5


def test_edited_prefix_is_ignored():
    assert relative_to_days("Edited a year ago") == 365


def test_sub_day_labels_collapse_to_today():
    assert relative_to_days("2 hours ago") == 0
    assert relative_to_days("a moment ago") == 0


def test_unparseable_label_returns_none():
    assert relative_to_days("last winter") is None
    assert relative_to_days("") is None


def test_bracket_dates_converts_relative_to_absolute():
    dates = bracket_dates(["a month ago", "2 months ago"], date(2026, 9, 4))
    assert dates == ["2026-08-05", "2026-07-06"]


def test_bracket_dates_never_goes_forward_in_a_newest_first_list():
    """Coarse labels can round the wrong way; order is the stronger signal."""
    dates = bracket_dates(["2 months ago", "a month ago", "3 months ago"],
                          date(2026, 9, 4))
    assert dates[0] >= dates[1] >= dates[2]


def test_unparseable_entries_render_empty_without_breaking_neighbours():
    dates = bracket_dates(["a month ago", "sometime", "2 months ago"],
                          date(2026, 9, 4))
    assert dates[1] == ""
    assert dates[0] == "2026-08-05"
    assert dates[2] == "2026-07-06"


def test_empty_input():
    assert bracket_dates([], date(2026, 9, 4)) == []
