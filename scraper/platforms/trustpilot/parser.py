from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from scraper.models import Review

NEXT_DATA_RE = re.compile(
    r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL
)
REVIEW_URL = "https://www.trustpilot.com/reviews/{id}"


def page_props(html: str) -> Optional[Dict[str, Any]]:
    """Pull props.pageProps out of the __NEXT_DATA__ blob. None when the page
    is not a rendered Trustpilot listing (challenge page, login redirect)."""
    match = NEXT_DATA_RE.search(html or "")
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
    except ValueError:
        return None
    props = data.get("props") or {}
    result = props.get("pageProps")
    return result if isinstance(result, dict) else None


def parse_pagination(props: Dict[str, Any]) -> Optional[Dict[str, int]]:
    pagination = ((props or {}).get("filters") or {}).get("pagination")
    if not pagination:
        return None
    return {"total": pagination.get("totalCount") or 0,
            "pages": pagination.get("totalPages") or 0}


def parse_languages(props: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Valid language codes with counts, straight from the page. Never guess
    these — an unrecognised code silently returns the unfiltered result."""
    stats = ((props or {}).get("filters") or {}).get("reviewStatistics") or {}
    languages = []
    for entry in stats.get("reviewLanguages") or []:
        code = entry.get("isoCode")
        if not code or code == "all":
            continue
        languages.append({"code": code, "count": entry.get("reviewCount") or 0})
    return languages


def parse_topics(props: Dict[str, Any]) -> List[str]:
    """Topic filter ids offered for THIS company. Enumerated, never guessed: an
    invented id returns zero results (`topics=delivery` gave 0, the real id is
    `delivery_service`), which looks identical to a company having no reviews."""
    topics = (props or {}).get("topicSummaryLocalizedTopics") or []
    return [topic.get("id") for topic in topics if topic.get("id")]


def _date(value: Optional[str]) -> str:
    return (value or "")[:10]


def _bool_cell(value: Any) -> str:
    if value is None:
        return ""
    return "TRUE" if value else "FALSE"


def parse_reviews(props: Dict[str, Any], *, domain: str, source_view: str,
                  fetcher: str, fetched_at: str) -> List[Review]:
    business = (props or {}).get("businessUnit") or {}
    reviews: List[Review] = []
    for raw in (props or {}).get("reviews") or []:
        review_id = raw.get("id")
        if not review_id:
            continue
        dates = raw.get("dates") or {}
        reply = raw.get("reply") or {}
        verification = ((raw.get("labels") or {}).get("verification")) or {}
        consumer = raw.get("consumer") or {}
        reviews.append(Review(
            review_id="trustpilot:{0}".format(review_id),
            platform="Trustpilot",
            company_domain=domain,
            company_name=business.get("displayName") or "",
            consumer_name=consumer.get("displayName") or "",
            url=REVIEW_URL.format(id=review_id),
            rating=raw.get("rating"),
            title=(raw.get("title") or "").strip(),
            raw_text=(raw.get("text") or "").strip(),
            review_date=_date(dates.get("publishedDate")),
            date_precision="exact",
            experience_date=_date(dates.get("experiencedDate")),
            language=raw.get("language") or "",
            country=raw.get("location") or "",
            is_verified=_bool_cell(verification.get("isVerified")),
            review_source=verification.get("reviewSourceName") or "",
            support_reply=(reply.get("message") or "").strip(),
            support_reply_date=_date(reply.get("publishedDate")),
            source_view=source_view,
            fetched_at=fetched_at,
            fetcher=fetcher,
        ))
    return reviews
