from __future__ import annotations

import pytest

from scraper.models import Review, sheet_columns
from scraper.sheets import append_reviews, existing_review_ids, get_or_create_tab


class WorksheetNotFound(Exception):
    pass


class FakeWorksheet:
    def __init__(self, title, rows=None):
        self.title = title
        self.rows = list(rows or [])
        self.appended_batches = []

    def append_row(self, row):
        self.rows.append(row)

    def append_rows(self, rows, value_input_option=None):
        self.appended_batches.append(list(rows))
        self.rows.extend(rows)

    def col_values(self, index):
        return [row[index - 1] if len(row) >= index else "" for row in self.rows]


class FakeSpreadsheet:
    def __init__(self, worksheets=None):
        self._worksheets = {ws.title: ws for ws in (worksheets or [])}
        self.created = []

    def worksheet(self, title):
        if title not in self._worksheets:
            raise WorksheetNotFound(title)
        return self._worksheets[title]

    def add_worksheet(self, title, rows, cols):
        ws = FakeWorksheet(title)
        self._worksheets[title] = ws
        self.created.append(title)
        return ws


@pytest.fixture(autouse=True)
def patch_not_found(monkeypatch):
    monkeypatch.setattr("scraper.sheets.WorksheetNotFound", WorksheetNotFound)


def test_creates_tab_with_headers_when_missing():
    spreadsheet = FakeSpreadsheet()
    columns = sheet_columns()
    worksheet = get_or_create_tab(spreadsheet, "Trademax", columns)

    assert spreadsheet.created == ["Trademax"]
    assert worksheet.rows[0] == columns


def test_reuses_an_existing_tab_without_rewriting_headers():
    existing = FakeWorksheet("Trademax", rows=[sheet_columns()])
    spreadsheet = FakeSpreadsheet([existing])
    worksheet = get_or_create_tab(spreadsheet, "Trademax", sheet_columns())

    assert spreadsheet.created == []
    assert worksheet is existing
    assert len(worksheet.rows) == 1


def test_tab_titles_are_truncated_to_the_sheets_limit():
    spreadsheet = FakeSpreadsheet()
    get_or_create_tab(spreadsheet, "x" * 150, sheet_columns())
    assert len(spreadsheet.created[0]) == 100


def test_existing_review_ids_skips_the_header():
    worksheet = FakeWorksheet("t", rows=[["review_id"], ["trustpilot:a"], ["google:b"]])
    assert existing_review_ids(worksheet) == {"trustpilot:a", "google:b"}


def test_existing_review_ids_on_an_empty_tab():
    assert existing_review_ids(FakeWorksheet("t", rows=[["review_id"]])) == set()


def test_append_writes_rows_in_column_order():
    worksheet = FakeWorksheet("t", rows=[sheet_columns()])
    columns = sheet_columns()
    written = append_reviews(worksheet, [
        Review(review_id="trustpilot:a", platform="Trustpilot", rating=5)], columns)

    assert written == 1
    row = worksheet.appended_batches[0][0]
    assert row[columns.index("review_id")] == "trustpilot:a"
    assert row[columns.index("rating")] == 5


def test_append_batches_instead_of_writing_row_by_row():
    worksheet = FakeWorksheet("t", rows=[sheet_columns()])
    reviews = [Review(review_id="t:{0}".format(i), platform="Trustpilot")
               for i in range(1200)]
    written = append_reviews(worksheet, reviews, sheet_columns(), chunk_size=500)

    assert written == 1200
    assert [len(batch) for batch in worksheet.appended_batches] == [500, 500, 200]


def test_append_of_nothing_makes_no_api_call():
    worksheet = FakeWorksheet("t", rows=[sheet_columns()])
    assert append_reviews(worksheet, [], sheet_columns()) == 0
    assert worksheet.appended_batches == []
