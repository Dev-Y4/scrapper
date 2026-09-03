from __future__ import annotations

from typing import Any, List, Set

import gspread
from google.oauth2.service_account import Credentials
from gspread.exceptions import WorksheetNotFound

from scraper.models import Review

SCOPES = ["https://www.googleapis.com/auth/spreadsheets",
          "https://www.googleapis.com/auth/drive"]
MAX_TAB_TITLE = 100
REVIEW_ID_COLUMN = 1


def open_spreadsheet(sheet_url: str, creds_file: str):
    credentials = Credentials.from_service_account_file(creds_file, scopes=SCOPES)
    return gspread.authorize(credentials).open_by_url(sheet_url)


def get_or_create_tab(spreadsheet: Any, title: str, columns: List[str]) -> Any:
    """One tab per company, both platforms mixed. Headers written once."""
    tab_title = title.strip()[:MAX_TAB_TITLE]
    try:
        return spreadsheet.worksheet(tab_title)
    except WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=tab_title, rows=2000,
                                              cols=len(columns))
        worksheet.append_row(columns)
        return worksheet


def existing_review_ids(worksheet: Any) -> Set[str]:
    """The sheet is the store: this is how dedup and resume both work."""
    values = worksheet.col_values(REVIEW_ID_COLUMN)
    return set(value for value in values[1:] if value)


def append_reviews(worksheet: Any, reviews: List[Review], columns: List[str],
                   chunk_size: int = 500) -> int:
    rows = [review.to_row(columns) for review in reviews]
    for start in range(0, len(rows), chunk_size):
        worksheet.append_rows(rows[start:start + chunk_size],
                              value_input_option="RAW")
    return len(rows)
