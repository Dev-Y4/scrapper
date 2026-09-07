from __future__ import annotations

import collections

from scraper.platforms.trustpilot.planner import (
    MAX_PAGES, VIEW_CAP, PlannedView, View, pages_for, plan_views,
)

LANGUAGES = [{"code": "sv", "count": 64871}, {"code": "en", "count": 1177},
             {"code": "da", "count": 154}, {"code": "zz", "count": 0}]


def probe_from(totals):
    calls = []

    def probe(view):
        calls.append(view.label())
        return totals.get(view.label())
    return probe, calls


BIG = {"languages=all": 66724,
       "languages=all,stars=1": 10193, "languages=all,stars=2": 3481,
       "languages=all,stars=3": 4674, "languages=all,stars=4": 12992,
       "languages=all,stars=5": 42020}


def labels(plans):
    return [plan.view.label() for plan in plans]


def test_pages_for_small_view():
    assert pages_for(61) == [1, 2, 3, 4]


def test_pages_never_exceed_the_cap():
    assert pages_for(64871) == list(range(1, MAX_PAGES + 1))
    assert len(pages_for(64871)) * 20 == VIEW_CAP


def test_view_query_and_label_are_stable():
    view = View.of(languages="en").with_("stars", 5)
    assert view.query() == "?languages=en&stars=5"
    assert view.label() == "languages=en,stars=5"


def test_small_company_is_one_view_and_one_probe():
    probe, calls = probe_from({"languages=all": 150})
    plans = plan_views(probe, languages=LANGUAGES)
    assert labels(plans) == ["languages=all"]
    assert plans[0].pages == [1, 2, 3, 4, 5, 6, 7, 8]
    assert len(calls) == 1


def test_medium_company_stops_at_star_views():
    totals = {"languages=all": 900, "languages=all,stars=1": 100,
              "languages=all,stars=2": 100, "languages=all,stars=3": 100,
              "languages=all,stars=4": 300, "languages=all,stars=5": 300}
    probe, calls = probe_from(totals)
    plans = plan_views(probe, languages=[])
    assert sorted(labels(plans)) == sorted(k for k in totals if "stars=" in k)
    assert len(calls) == 6  # root + five stars, nothing more


def test_planning_a_large_company_costs_only_six_probes():
    """Language x star sizes are estimated from counts the page already gave
    us. Probing all of them would cost minutes of page fetches."""
    probe, calls = probe_from(BIG)
    plan_views(probe, languages=LANGUAGES, target=3000)
    assert len(calls) == 6


def test_language_star_views_are_planned_for_a_large_company():
    probe, _ = probe_from(BIG)
    got = labels(plan_views(probe, languages=LANGUAGES, target=3000))
    assert "languages=sv,stars=1" in got
    assert "languages=sv,stars=5" in got
    assert "languages=en,stars=1" in got


def test_every_star_of_a_language_is_planned_before_the_next_language():
    """A sample skewed to one star rating is useless for comparing sentiment,
    so breadth across stars comes before depth into more languages."""
    probe, _ = probe_from(BIG)
    got = [label for label in labels(plan_views(probe, languages=LANGUAGES, target=1000))
           if label.startswith("languages=sv") or label.startswith("languages=en")]
    sv_positions = [i for i, label in enumerate(got) if "languages=sv" in label]
    en_positions = [i for i, label in enumerate(got) if "languages=en" in label]
    assert len(sv_positions) == 5, got
    if en_positions:
        assert max(sv_positions) < min(en_positions)


def test_zero_count_languages_are_never_planned():
    probe, _ = probe_from(BIG)
    assert not any("languages=zz" in label
                   for label in labels(plan_views(probe, languages=LANGUAGES,
                                                  target=100000)))


def test_invalid_star_slice_echoing_the_root_total_is_discarded():
    """An unrecognised filter value silently returns the unfiltered result."""
    totals = dict(BIG)
    totals["languages=all,stars=3"] = 66724   # <- the fallback
    probe, _ = probe_from(totals)
    got = labels(plan_views(probe, languages=[], target=3000))
    assert "languages=all,stars=3" not in got
    assert "languages=all,stars=1" in got


def test_target_is_spread_across_star_ratings_not_filled_one_at_a_time():
    """A 600-review target must not come back as 200 one-star + 200 two-star +
    200 three-star. Sentiment comparison needs every rating represented."""
    probe, _ = probe_from(BIG)
    plans = plan_views(probe, languages=LANGUAGES, target=600)
    stars = sorted(int(plan.view.get("stars")) for plan in plans
                   if plan.view.get("languages") == "sv")
    assert stars == [1, 2, 3, 4, 5], stars
    planned_rows = sum(len(plan.pages) * 20 for plan in plans)
    assert 600 <= planned_rows <= 1000


def test_a_small_target_still_touches_every_star():
    probe, _ = probe_from(BIG)
    plans = plan_views(probe, languages=LANGUAGES, target=200)
    stars = sorted(int(plan.view.get("stars")) for plan in plans
                   if plan.view.get("languages") == "sv")
    assert stars == [1, 2, 3, 4, 5]
    assert all(len(plan.pages) >= 1 for plan in plans)


def test_expected_is_capped_at_two_hundred():
    assert PlannedView(View.of(), [1], 42020).expected == VIEW_CAP
    assert PlannedView(View.of(), [1], 61).expected == 61


def test_no_plan_ever_exceeds_page_ten():
    probe, _ = probe_from(BIG)
    plans = plan_views(probe, languages=LANGUAGES, target=100000)
    assert plans
    for plan in plans:
        assert max(plan.pages) <= MAX_PAGES


def test_unreachable_company_yields_nothing():
    probe, _ = probe_from({"languages=all": 0})
    assert plan_views(probe, languages=LANGUAGES) == []


def test_small_language_gets_fewer_pages_than_a_dominant_one():
    probe, _ = probe_from(BIG)
    plans = {plan.view.label(): plan
             for plan in plan_views(probe, languages=LANGUAGES, target=100000)}
    assert len(plans["languages=da,stars=5"].pages) < len(plans["languages=sv,stars=5"].pages)


TOPICS = ["product", "delivery_service", "quality", "order", "customer_service"]


def test_topics_extend_reach_beyond_the_language_star_ceiling():
    """languages x stars caps at 5 x 200 = 1000 per language, which cannot
    reach a 3000-review target. Topic slices lift that ceiling."""
    probe, _ = probe_from(BIG)
    without = plan_views(probe, languages=LANGUAGES, target=3000)
    probe2, _ = probe_from(BIG)
    with_topics = plan_views(probe2, languages=LANGUAGES, topics=TOPICS, target=3000)

    planned_rows = sum(len(plan.pages) * 20 for plan in with_topics)
    assert planned_rows >= 3000, planned_rows
    assert planned_rows > sum(len(plan.pages) * 20 for plan in without)


def test_topic_views_carry_the_language_and_star_of_their_cell():
    probe, _ = probe_from(BIG)
    plans = plan_views(probe, languages=LANGUAGES, topics=TOPICS, target=3000)
    topic_plans = [p for p in plans if p.view.has("topics")]
    assert topic_plans
    for plan in topic_plans:
        assert plan.view.get("languages")
        assert plan.view.get("stars")
        assert plan.view.get("topics") in TOPICS


def test_star_balance_survives_the_topic_dimension():
    probe, _ = probe_from(BIG)
    plans = plan_views(probe, languages=LANGUAGES, topics=TOPICS, target=3000)
    sv_stars = collections.Counter(
        plan.view.get("stars") for plan in plans if plan.view.get("languages") == "sv")
    assert set(sv_stars) == {"1", "2", "3", "4", "5"}
    assert max(sv_stars.values()) - min(sv_stars.values()) <= 1


def test_topics_are_not_used_when_the_target_is_already_reachable():
    probe, _ = probe_from(BIG)
    plans = plan_views(probe, languages=LANGUAGES, topics=TOPICS, target=400)
    assert not any(plan.view.has("topics") for plan in plans)


def test_planning_with_topics_still_costs_six_probes():
    probe, calls = probe_from(BIG)
    plan_views(probe, languages=LANGUAGES, topics=TOPICS, target=3000)
    assert len(calls) == 6


def test_all_languages_are_planned_before_any_topic_slice():
    """Languages are disjoint — a review has exactly one — so every language x
    star row is new. Topic slices overlap heavily (one review mentions several).
    Spending the plan on topics first cost 436 unique rows and 3366 duplicates
    on a real run, so disjoint dimensions come first."""
    probe, _ = probe_from(BIG)
    plans = plan_views(probe, languages=LANGUAGES, topics=TOPICS, target=5400)
    first_topic = next((i for i, p in enumerate(plans) if p.view.has("topics")), None)
    assert first_topic is not None, "topics should still be planned"
    languages_before = {p.view.get("languages") for p in plans[:first_topic]}
    assert {"sv", "en", "da"} <= languages_before, languages_before


def test_topics_only_top_up_after_languages_are_exhausted():
    """Order of preference: disjoint language slices, then topic slices, and
    the languages=all recency views last — those are supersets of the language
    slices, so they are the most redundant rows in the plan."""
    probe, _ = probe_from(BIG)
    plans = plan_views(probe, languages=LANGUAGES, topics=TOPICS, target=5400)
    per_language = [p for p in plans
                    if not p.view.has("topics") and p.view.get("languages") != "all"]
    topic = [p for p in plans if p.view.has("topics")]
    catch_all = [p for p in plans if p.view.get("languages") == "all"]
    assert per_language and topic and catch_all
    assert plans.index(per_language[-1]) < plans.index(topic[0])
    assert plans.index(topic[-1]) < plans.index(catch_all[0])


def test_a_small_target_is_spread_over_stars_not_spent_on_two_of_them():
    """An English-dominant company with target=100 planned 3 pages per star
    view (300 rows) and the harvest stopped after two stars. Base views are
    funded from the target itself so a small ask stays balanced and cheap."""
    probe, _ = probe_from(BIG)
    plans = plan_views(probe, languages=LANGUAGES, topics=TOPICS, target=100)
    base = [p for p in plans if not p.view.has("topics")]
    assert len(base) >= 5
    planned_rows = sum(len(p.pages) * 20 for p in base[:5])
    assert planned_rows <= 200, planned_rows


def test_topic_budget_is_separate_from_the_base_budget():
    probe, _ = probe_from(BIG)
    plans = plan_views(probe, languages=LANGUAGES, topics=TOPICS, target=3000)
    base_rows = sum(len(p.pages) * 20 for p in plans if not p.view.has("topics"))
    topic_rows = sum(len(p.pages) * 20 for p in plans if p.view.has("topics"))
    assert base_rows >= 2000, base_rows
    assert topic_rows > 0
