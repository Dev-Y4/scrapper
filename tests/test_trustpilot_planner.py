from __future__ import annotations

from scraper.platforms.trustpilot.planner import (
    MAX_PAGES, VIEW_CAP, PlannedView, View, pages_for, plan_views,
)

LANGUAGES = [{"code": "sv", "count": 64871}, {"code": "en", "count": 1177},
             {"code": "da", "count": 154}]


def probe_from(totals):
    """totals maps a view label to its totalCount."""
    def probe(view):
        return totals.get(view.label())
    return probe


def test_pages_for_small_view():
    assert pages_for(61) == [1, 2, 3, 4]


def test_pages_for_exact_multiple():
    assert pages_for(40) == [1, 2]


def test_pages_never_exceed_the_cap():
    assert pages_for(64871) == list(range(1, MAX_PAGES + 1))
    assert len(pages_for(64871)) * 20 == VIEW_CAP


def test_view_query_and_label_are_stable():
    view = View.of(languages="en").with_("stars", 5)
    assert view.query() == "?languages=en&stars=5"
    assert view.label() == "languages=en,stars=5"


def test_small_root_is_not_split():
    plans = plan_views(probe_from({"languages=all": 150}), languages=LANGUAGES)
    assert len(plans) == 1
    assert plans[0].view.label() == "languages=all"
    assert plans[0].pages == [1, 2, 3, 4, 5, 6, 7, 8]


def test_large_root_splits_by_stars_first():
    totals = {"languages=all": 797,
              "languages=all,stars=1": 292, "languages=all,stars=2": 61,
              "languages=all,stars=3": 66, "languages=all,stars=4": 183,
              "languages=all,stars=5": 678}
    plans = plan_views(probe_from(totals), languages=[])
    labels = sorted(plan.view.label() for plan in plans)
    assert labels == sorted(k for k in totals if "stars=" in k)


def test_star_view_over_the_cap_splits_by_language():
    totals = {"languages=all": 66724,
              "languages=all,stars=5": 42020,
              "languages=sv,stars=5": 40000,
              "languages=en,stars=5": 700,
              "languages=da,stars=5": 80}
    for star in (1, 2, 3, 4):
        totals["languages=all,stars={0}".format(star)] = 100
    plans = plan_views(probe_from(totals), languages=LANGUAGES)
    labels = [plan.view.label() for plan in plans]
    assert "languages=sv,stars=5" in labels
    assert "languages=en,stars=5" in labels
    assert "languages=all,stars=5" not in labels


def test_invalid_language_code_echoing_parent_total_is_discarded():
    """An unrecognised code silently returns the unfiltered result. A child
    whose total equals its parent's is a fallback, not a real slice."""
    totals = {"languages=all": 66724, "languages=all,stars=5": 42020,
              "languages=sv,stars=5": 42020,   # <- the fallback
              "languages=en,stars=5": 700,
              "languages=da,stars=5": 80}
    for star in (1, 2, 3, 4):
        totals["languages=all,stars={0}".format(star)] = 100
    plans = plan_views(probe_from(totals), languages=LANGUAGES)
    labels = [plan.view.label() for plan in plans]
    assert "languages=sv,stars=5" not in labels
    assert "languages=en,stars=5" in labels


def test_unreachable_view_is_dropped():
    plans = plan_views(probe_from({"languages=all": 0}), languages=LANGUAGES)
    assert plans == []


def test_target_stops_planning_early():
    totals = {"languages=all": 797,
              "languages=all,stars=1": 292, "languages=all,stars=2": 61,
              "languages=all,stars=3": 66, "languages=all,stars=4": 183,
              "languages=all,stars=5": 678}
    plans = plan_views(probe_from(totals), languages=[], target=250)
    assert sum(plan.expected for plan in plans) >= 250
    assert len(plans) == 2  # the two 200-yield views, biggest first


def test_expected_is_capped_at_two_hundred():
    assert PlannedView(View.of(), [1], 42020).expected == VIEW_CAP
    assert PlannedView(View.of(), [1], 61).expected == 61


def test_no_plan_ever_exceeds_page_ten():
    totals = {"languages=all": 66724}
    for star in (1, 2, 3, 4, 5):
        totals["languages=all,stars={0}".format(star)] = 42020
        for lang in LANGUAGES:
            totals["languages={0},stars={1}".format(lang["code"], star)] = 30000
    plans = plan_views(probe_from(totals), languages=LANGUAGES)
    assert plans
    for plan in plans:
        assert max(plan.pages) <= MAX_PAGES
