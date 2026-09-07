from __future__ import annotations

import math
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

PER_PAGE = 20
MAX_PAGES = 10                    # page 11 is a login redirect. Hard cap.
VIEW_CAP = PER_PAGE * MAX_PAGES   # 200 reviews reachable per view
STARS = (1, 2, 3, 4, 5)
MIN_VIEW_ESTIMATE = 20            # below one page, a view is not worth a fetch

# `date` (last30days / last3months / ...) is a further dimension, but the
# buckets NEST, so slicing by them re-fetches the same reviews. Measured on a
# real run: 391 duplicates for 211 unique rows. Left unused unless yield ever
# demands it. `topics`, `search` and `locationId` are the other untapped levers.


@dataclass(frozen=True)
class View:
    params: Tuple[Tuple[str, str], ...] = ()

    @classmethod
    def of(cls, **params: Any) -> "View":
        return cls(tuple(sorted((k, str(v)) for k, v in params.items())))

    def with_(self, key: str, value: Any) -> "View":
        merged = dict(self.params)
        merged[key] = str(value)
        return View(tuple(sorted(merged.items())))

    def has(self, key: str) -> bool:
        return key in dict(self.params)

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        return dict(self.params).get(key, default)

    def query(self) -> str:
        if not self.params:
            return ""
        return "?" + "&".join("{0}={1}".format(k, v) for k, v in self.params)

    def label(self) -> str:
        if not self.params:
            return "default"
        return ",".join("{0}={1}".format(k, v) for k, v in self.params)


@dataclass
class PlannedView:
    view: View
    pages: List[int]
    total: int

    @property
    def expected(self) -> int:
        """Reachable rows, ignoring cross-view overlap. Dedup handles the rest."""
        return min(self.total, VIEW_CAP)


def pages_for(total: int) -> List[int]:
    if total <= 0:
        return []
    count = min(int(math.ceil(total / float(PER_PAGE))), MAX_PAGES)
    return list(range(1, count + 1))


def plan_views(probe: Callable[[View], Optional[int]], *,
               languages: List[Dict[str, Any]],
               topics: Optional[List[str]] = None,
               target: Optional[int] = None,
               topic_budget: Optional[int] = None,
               max_probes: Optional[int] = None) -> List[PlannedView]:
    """Expand one company into filter views worth fetching.

    Planning itself costs page fetches, so it probes only what it cannot infer:
    the root and the five star slices. Language x star sizes are *estimated*
    from the per-language counts the page already reported, which keeps planning
    at six round-trips instead of hundreds. An estimate that runs long simply
    ends on an empty page; one that runs short costs a little yield.

    Views are ordered for a stratified sample — every star rating of a language
    before moving to the next language — because a set skewed to one rating is
    useless for comparing sentiment across competitors."""
    root = View.of(languages="all")
    root_total = probe(root)
    if not root_total:
        return []

    if root_total <= VIEW_CAP:
        return [PlannedView(root, pages_for(root_total), root_total)]

    # -- probe the five star slices (the only disjoint dimension we can trust)
    star_totals: Dict[int, int] = {}
    for star in STARS:
        star_view = root.with_("stars", star)
        total = probe(star_view)
        if not total:
            continue
        if total == root_total:
            # Unrecognised filter value: silently returned the unfiltered set.
            continue
        star_totals[star] = total

    if not star_totals:
        return [PlannedView(root, pages_for(root_total), root_total)]

    star_views = [PlannedView(root.with_("stars", star), pages_for(total), total)
                  for star, total in sorted(star_totals.items())]

    # If every star slice fits under the cap, language slicing adds nothing.
    if all(plan.total <= VIEW_CAP for plan in star_views):
        return _trim(star_views, target, topic_budget)

    # -- estimate language x star from counts the page already gave us
    ranked = sorted((lang for lang in languages if lang.get("count")),
                    key=lambda lang: -lang["count"])
    star_share = {star: total / float(root_total) for star, total in star_totals.items()}

    planned: List[PlannedView] = []
    at_cap: List[PlannedView] = []

    # Pass 1 — languages x stars. A review has exactly one language, so these
    # slices are disjoint and every row they return is new.
    for lang in ranked:
        for star in sorted(star_totals):
            estimate = int(lang["count"] * star_share[star])
            if estimate < MIN_VIEW_ESTIMATE:
                continue
            view = View.of(languages=lang["code"]).with_("stars", star)
            # One extra page absorbs a low estimate; an overshoot ends on an
            # empty page and costs one wasted fetch.
            pages = pages_for(min(estimate + PER_PAGE, VIEW_CAP))
            cell = PlannedView(view, pages, estimate)
            planned.append(cell)
            if cell.expected >= VIEW_CAP:
                at_cap.append(cell)

    # Pass 2 — topics, only to top up. They overlap heavily (one review mentions
    # several topics), so spending the plan on them before exhausting languages
    # trades disjoint rows for duplicates: measured at 1737 unique against 3366
    # duplicates when topics came first.
    if topics and _short_of(planned, target):
        # One topic across every star before the next topic, so the sample
        # stays balanced across ratings however early the target is reached.
        for topic in topics:
            for cell in at_cap:
                planned.append(PlannedView(cell.view.with_("topics", topic),
                                           pages_for(VIEW_CAP), VIEW_CAP))
        # Every topic is planned rather than stopping at the estimate: real
        # topic cells hold far less than the 200 assumed here (measured 1-167),
        # so an estimate-based stop leaves the target unreachable. Harvesting
        # ends the moment the true target is met, so surplus plans cost nothing.

    planned.extend(star_views)   # cross-language recency, already probed
    return _trim(planned, target, topic_budget)


def _short_of(plans: List[PlannedView], target: Optional[int]) -> bool:
    if target is None:
        return True
    return sum(plan.expected for plan in plans) < target


def _trim(plans: List[PlannedView], target: Optional[int],
          topic_budget: Optional[int] = None) -> List[PlannedView]:
    """Spend the target ACROSS a language's star slices, not down them.

    Filling view by view would return a 600-review target as 200 one-star plus
    200 two-star plus 200 three-star, and a competitor comparison built on that
    reads far more negative than the company actually is."""
    if target is None:
        return plans

    # Base language slices form their own group per language, and topic slices
    # another, so the disjoint ones are funded before the overlapping ones.
    groups: "OrderedDict[str, List[PlannedView]]" = OrderedDict()
    for plan in plans:
        key = "{0}|{1}".format(plan.view.get("languages") or "",
                               "topic" if plan.view.has("topics") else "base")
        groups.setdefault(key, []).append(plan)

    # Two budgets. Base slices are funded from the target itself, so a small
    # ask stays small AND balanced; topic slices draw on a separate surplus,
    # because reaching N unique rows through overlapping views needs more than
    # N fetched rows.
    budgets = {"base": target,
               "topic": target if topic_budget is None else topic_budget}

    chosen: List[PlannedView] = []
    for key, group in groups.items():
        kind = "topic" if key.endswith("|topic") else "base"
        if budgets[kind] <= 0:
            continue
        share = int(math.ceil(budgets[kind] / float(len(group))))
        # A group is funded as a SET, never partially. One page is the smallest
        # unit that can be fetched, so a budget below one page per star would
        # otherwise buy the first two ratings and drop the rest — which is how
        # a 100-review target came back as nothing but 1- and 2-star reviews.
        for plan in group:
            take = min(plan.expected, max(share, PER_PAGE))
            pages = pages_for(take)
            if not pages:
                continue
            chosen.append(PlannedView(plan.view, pages, plan.total))
            budgets[kind] -= len(pages) * PER_PAGE
    return chosen
