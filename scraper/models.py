from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional

BASE_COLUMNS: List[str] = [
    "review_id",
    "platform",
    "company_domain",
    "company_name",
    "location",
    "url",
    "rating",
    "title",
    "raw_text",
    "review_date",
    "date_precision",
    "experience_date",
    "language",
    "country",
    "is_verified",
    "review_source",
    "support_reply",
    "support_reply_date",
    "source_view",
    "fetched_at",
    "fetcher",
    "customer_order_status",
    "journey_stage",
    "review_type",
]


def sheet_columns(include_consumer_name: bool = False) -> List[str]:
    """Sheet header order. Reviewer names are personal data and stay out
    unless explicitly switched on."""
    columns = list(BASE_COLUMNS)
    if include_consumer_name:
        columns.insert(columns.index("company_name") + 1, "consumer_name")
    return columns


@dataclass
class Review:
    review_id: str
    platform: str
    company_domain: str = ""
    company_name: str = ""
    consumer_name: str = ""
    location: str = ""
    url: str = ""
    rating: Optional[int] = None
    title: str = ""
    raw_text: str = ""
    review_date: str = ""
    date_precision: str = "exact"
    experience_date: str = ""
    language: str = ""
    country: str = ""
    is_verified: str = ""
    review_source: str = ""
    support_reply: str = ""
    support_reply_date: str = ""
    source_view: str = ""
    fetched_at: str = ""
    fetcher: str = ""
    customer_order_status: str = ""
    journey_stage: str = ""
    review_type: str = ""

    def to_row(self, columns: List[str]) -> List[Any]:
        row: List[Any] = []
        for column in columns:
            value = getattr(self, column, "")
            row.append("" if value is None else value)
        return row
