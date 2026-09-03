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
               target: Optional[int] = None,
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
        return _trim(star_views, target)

    # -- estimate language x star from counts the page already gave us
    ranked = sorted((lang for lang in languages if lang.get("count")),
                    key=lambda lang: -lang["count"])
    star_share = {star: total / float(root_total) for star, total in star_totals.items()}

    planned: List[PlannedView] = []
    for lang in ranked:
        for star in sorted(star_totals):
            estimate = int(lang["count"] * star_share[star])
            if estimate < MIN_VIEW_ESTIMATE:
                continue
            view = View.of(languages=lang["code"]).with_("stars", star)
            # One extra page absorbs a low estimate; an overshoot ends on an
            # empty page and costs one wasted fetch.
            pages = pages_for(min(estimate + PER_PAGE, VIEW_CAP))
            planned.append(PlannedView(view, pages, estimate))
        if target is not None and sum(p.expected for p in planned) >= target:
            break

    planned.extend(star_views)   # cross-language recency, already probed
    return _trim(planned, target)


def _trim(plans: List[PlannedView], target: Optional[int]) -> List[PlannedView]:
    """Spend the target ACROSS a language's star slices, not down them.

    Filling view by view would return a 600-review target as 200 one-star plus
    200 two-star plus 200 three-star, and a competitor comparison built on that
    reads far more negative than the company actually is."""
    if target is None:
        return plans

    groups: "OrderedDict[str, List[PlannedView]]" = OrderedDict()
    for plan in plans:
        groups.setdefault(plan.view.get("languages") or "", []).append(plan)

    chosen: List[PlannedView] = []
    remaining = target
    for group in groups.values():
        if remaining <= 0:
            break
        share = int(math.ceil(remaining / float(len(group))))
        for plan in group:
            if remaining <= 0:
                break
            take = min(plan.expected, max(share, PER_PAGE))
            pages = pages_for(take)
            if not pages:
                continue
            chosen.append(PlannedView(plan.view, pages, plan.total))
            remaining -= len(pages) * PER_PAGE
    return chosen
