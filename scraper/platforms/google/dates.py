from __future__ import annotations

import re
from datetime import date, timedelta
from typing import List, Optional

UNIT_DAYS = {
    "second": 0, "seconds": 0, "minute": 0, "minutes": 0,
    "hour": 0, "hours": 0, "moment": 0, "moments": 0,
    "day": 1, "days": 1,
    "week": 7, "weeks": 7,
    "month": 30, "months": 30,
    "year": 365, "years": 365,
}

RELATIVE_RE = re.compile(
    r"(?:edited\s+)?(?:(\d+)|an?)\s+"
    r"(seconds?|minutes?|hours?|moments?|days?|weeks?|months?|years?)\s+ago",
    re.IGNORECASE,
)


def relative_to_days(label: str) -> Optional[int]:
    """"3 weeks ago" -> 21. None when the label is not a relative age."""
    if not label:
        return None
    text = label.strip().lower()
    if text in ("yesterday", "edited yesterday"):
        return 1
    match = RELATIVE_RE.search(text)
    if not match:
        return None
    count = int(match.group(1)) if match.group(1) else 1
    return count * UNIT_DAYS[match.group(2)]


def bracket_dates(labels: List[str], run_date: date) -> List[str]:
    """Convert a newest-first list of relative labels into absolute dates.

    Month-level accuracy only. Ordering is enforced: Google's labels are coarse
    enough that two adjacent reviews can round out of order, and the list order
    is the more reliable signal. Rows carry date_precision='relative'."""
    results: List[str] = []
    previous: Optional[date] = None
    for label in labels:
        days = relative_to_days(label)
        if days is None:
            results.append("")
            continue
        value = run_date - timedelta(days=days)
        if previous is not None and value > previous:
            value = previous
        previous = value
        results.append(value.isoformat())
    return results
