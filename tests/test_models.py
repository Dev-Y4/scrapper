from __future__ import annotations

from scraper.models import BASE_COLUMNS, Review, sheet_columns


def test_consumer_name_absent_by_default():
    assert "consumer_name" not in sheet_columns()


def test_consumer_name_present_when_enabled():
    cols = sheet_columns(include_consumer_name=True)
    assert "consumer_name" in cols
    assert cols.index("consumer_name") == cols.index("company_name") + 1


def test_review_id_is_first_column():
    assert BASE_COLUMNS[0] == "review_id"


def test_to_row_follows_column_order():
    review = Review(review_id="trustpilot:abc", platform="Trustpilot",
                    company_name="Trademax", rating=4, raw_text="fine")
    row = review.to_row(BASE_COLUMNS)
    assert len(row) == len(BASE_COLUMNS)
    assert row[BASE_COLUMNS.index("review_id")] == "trustpilot:abc"
    assert row[BASE_COLUMNS.index("rating")] == 4


def test_missing_values_render_as_empty_string():
    review = Review(review_id="google:xyz", platform="Google")
    row = review.to_row(BASE_COLUMNS)
    assert row[BASE_COLUMNS.index("rating")] == ""
    assert row[BASE_COLUMNS.index("support_reply")] == ""


def test_to_row_omits_consumer_name_unless_column_requested():
    review = Review(review_id="g:1", platform="Google", consumer_name="Ada L")
    assert "Ada L" not in review.to_row(BASE_COLUMNS)
    assert "Ada L" in review.to_row(sheet_columns(include_consumer_name=True))
