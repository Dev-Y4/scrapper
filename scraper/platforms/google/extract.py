from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, List, Optional

from scraper.models import Review
from scraper.platforms.google.dates import bracket_dates

RATING_RE = re.compile(r"(\d+)")

# Runs in the page. Returns one entry per [data-review-id] node; several nodes
# belong to the same review, so dedupe_nodes() collapses them Python-side.
EXTRACT_JS = """() => {
  const out = [];
  document.querySelectorAll('[data-review-id]').forEach(node => {
    const star = node.querySelector('[role="img"][aria-label*="star"],[role="img"][aria-label*="stjar"],[role="img"][aria-label*="stjär"]');
    if (!star) return;
    const text = node.querySelector('.wiI7pd');
    const when = node.querySelector('.rsqaWe, .xRkPPb');
    const reply = node.querySelector('.CDe7pd');
    out.push({
      id: node.getAttribute('data-review-id') || '',
      rating: star.getAttribute('aria-label') || '',
      text: text ? text.innerText.trim() : '',
      when: when ? when.innerText.trim() : '',
      reply: reply ? reply.innerText.trim() : ''
    });
  });
  return out;
}"""

SCROLL_JS = """() => {
  const panes = [...document.querySelectorAll('div.m6QErb')]
    .filter(d => d.scrollHeight > d.clientHeight + 200);
  const pane = panes[panes.length - 1];
  if (!pane) return -1;
  pane.scrollTop = pane.scrollHeight;
  return 1;
}"""


def parse_rating(aria_label: str) -> Optional[int]:
    match = RATING_RE.search(aria_label or "")
    return int(match.group(1)) if match else None


def dedupe_nodes(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One review contributes several DOM nodes — measured at ~3.8 nodes per
    review. Keep one per id, preferring the node carrying the review body."""
    best: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for node in nodes:
        review_id = node.get("id") or ""
        if not review_id:
            continue
        if review_id not in best:
            best[review_id] = node
            order.append(review_id)
        elif not (best[review_id].get("text") or "") and (node.get("text") or ""):
            best[review_id] = node
    return [best[review_id] for review_id in order]


def build_reviews(nodes: List[Dict[str, Any]], *, company_name: str, place: str,
                  run_date: date, fetched_at: str, fetcher: str) -> List[Review]:
    unique = dedupe_nodes(nodes)
    dates = bracket_dates([node.get("when") or "" for node in unique], run_date)

    reviews: List[Review] = []
    for node, review_date in zip(unique, dates):
        reviews.append(Review(
            review_id="google:{0}".format(node["id"]),
            platform="Google",
            company_name=company_name,
            location=place,
            rating=parse_rating(node.get("rating") or ""),
            raw_text=node.get("text") or "",
            review_date=review_date,
            date_precision="relative",
            support_reply=node.get("reply") or "",
            source_view="place={0}".format(place),
            fetched_at=fetched_at,
            fetcher=fetcher,
        ))
    return reviews
