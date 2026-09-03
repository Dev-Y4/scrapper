from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

PER_PAGE = 20
MAX_PAGES = 10                    # page 11 is a login redirect. Hard cap.
VIEW_CAP = PER_PAGE * MAX_PAGES   # 200 reviews reachable per view
MAX_DEPTH = 3                     # stars -> languages -> date
DATE_BUCKETS = ("last30days", "last3months", "last6months", "last12months")
STARS = (1, 2, 3, 4, 5)


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


def _children(view: View, languages: List[Dict[str, Any]]) -> List[View]:
    if not view.has("stars"):
        return [view.with_("stars", star) for star in STARS]
    if languages and view.get("languages") == "all":
        return [view.with_("languages", lang["code"]) for lang in languages]
    if not view.has("date"):
        return [view.with_("date", bucket) for bucket in DATE_BUCKETS]
    return []


def _expand(view: View, total: int, probe: Callable[[View], Optional[int]],
            languages: List[Dict[str, Any]], out: List[PlannedView],
            depth: int) -> None:
    if total <= 0:
        return
    if total <= VIEW_CAP or depth >= MAX_DEPTH:
        out.append(PlannedView(view, pages_for(total), total))
        return

    children = _children(view, languages)
    if not children:
        out.append(PlannedView(view, pages_for(total), total))
        return

    kept = 0
    for child in children:
        child_total = probe(child)
        if not child_total:
            continue
        if child_total == total:
            # The filter value was not recognised and silently fell back to the
            # parent's result set. Not a real slice.
            continue
        kept += 1
        _expand(child, child_total, probe, languages, out, depth + 1)

    if kept == 0:
        out.append(PlannedView(view, pages_for(total), total))


def plan_views(probe: Callable[[View], Optional[int]], *,
               languages: List[Dict[str, Any]],
               target: Optional[int] = None) -> List[PlannedView]:
    """Expand one company into filter views worth fetching, richest first."""
    root = View.of(languages="all")
    root_total = probe(root)
    if not root_total:
        return []

    planned: List[PlannedView] = []
    _expand(root, root_total, probe, languages, planned, depth=0)
    planned.sort(key=lambda plan: -plan.expected)

    if target is None:
        return planned

    chosen: List[PlannedView] = []
    running = 0
    for plan in planned:
        if running >= target:
            break
        chosen.append(plan)
        running += plan.expected
    return chosen
