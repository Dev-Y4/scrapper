# Multi-Platform Review Collection — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collect rated customer reviews from Trustpilot and Google Maps into one Google Sheet tab per company, ~3,000 reviews per run, on free infrastructure.

**Architecture:** A platform adapter (`plan / fetch / parse / classify`) per source, sitting on a shared fetcher layer with failover between a real-Chrome-over-CDP fetcher and a Jina fetcher. Trustpilot's filter-slicing planner is pure logic with no network, so it is unit-testable offline. Rows are deduped by a platform-namespaced `review_id` and appended to the company's sheet tab.

**Tech Stack:** Python 3.9.6, pytest, playwright (CDP attach only — no bundled browser), requests, gspread + google-auth, python-dotenv.

**Spec:** `docs/superpowers/specs/2026-09-04-trustpilot-scraper-design.md`

## Global Constraints

- **Python is 3.9.6.** Every module starts with `from __future__ import annotations`. No `X | Y` runtime annotations, no `match`, no `dict | dict` merge operator.
- **Never authenticate to Trustpilot or Google.** No account credentials in `.env`, in code, or in a browser profile. Reading reviews requires no login.
- **Trustpilot pagination hard cap: page 10 per view** (`MAX_PAGES = 10`, `PER_PAGE = 20`, `VIEW_CAP = 200`). Page 11 is a login redirect. Never plan a page above 10.
- **`GATED` never retries and never fails over.** It is a product gate, not a block.
- **Language codes are enumerated from `filters.reviewStatistics.reviewLanguages`, never guessed.** An unrecognised code silently returns the unfiltered `all` result.
- **Invalid-split guard:** a child view whose `totalCount` equals its parent's is discarded.
- **`consumer_name` is never written to the sheet unless `INCLUDE_CONSUMER_NAME=true`.** Default false.
- **One tab per company, both platforms mixed**, separated by the `platform` column.
- **Politeness:** jittered 3.5–7s between Trustpilot page loads, single tab, no concurrency against either site.
- **Do not delete or rewrite `fetch_reviews.py`, `fetch_reviews_selenium.py`, `setup_sheet.py`, `test_jina.py`, or the committed `*_reviews.json` files.** They are the repo owner's work. New code goes in the `scraper/` package. `setup_sheet.py` is read for reference and superseded, not edited.
- **Never `git add` the whole directory.** Add named paths only — `.env` and `scrapper.json` are live secrets sitting in the working tree.

## File Structure

| Path | Responsibility |
|---|---|
| `scraper/models.py` | `Review` dataclass, sheet column order, row rendering |
| `scraper/outcomes.py` | `Outcome` enum + `classify(html)` |
| `scraper/fetchers/base.py` | `Fetcher` protocol, `FetchResult` |
| `scraper/fetchers/jina.py` | `JinaFetcher` |
| `scraper/fetchers/chrome.py` | `ChromeCDPFetcher` — launches/attaches real Chrome |
| `scraper/fetchers/pool.py` | `FetcherPool` — retry, failover, circuit breaker |
| `scraper/platforms/trustpilot/parser.py` | `__NEXT_DATA__` extraction → rows, pagination, languages |
| `scraper/platforms/trustpilot/planner.py` | Pure view-planning logic |
| `scraper/platforms/trustpilot/adapter.py` | Wires planner + pool + parser |
| `scraper/platforms/google/dates.py` | Relative label → bracketed absolute date |
| `scraper/platforms/google/extract.py` | DOM extraction JS, rating parse, node dedup |
| `scraper/platforms/google/adapter.py` | Place resolve, scroll loop, row assembly |
| `scraper/sheets.py` | Tab creation, existing-id read, batched append |
| `scraper/checkpoint.py` | Local JSON mid-run checkpoint |
| `scraper/run.py` | CLI orchestrator + run summary |
| `tests/` | Mirrors the above; fixtures in `tests/fixtures/` |

---

### Task 1: Scaffolding, secret hygiene, and the row model

**Files:**
- Modify: `.gitignore`
- Create: `requirements.txt`, `scraper/__init__.py`, `scraper/models.py`, `tests/__init__.py`, `tests/test_models.py`, `pytest.ini`

**Interfaces:**
- Consumes: nothing
- Produces: `BASE_COLUMNS: List[str]`, `sheet_columns(include_consumer_name: bool = False) -> List[str]`, `Review` dataclass with `.to_row(columns: List[str]) -> List[object]`

- [ ] **Step 1: Harden `.gitignore` first**

A service-account key is already in the working tree. Prepend these lines to `.gitignore`:

```
# --- secrets: broad rules first, exceptions below ---
*.json
!www_trademax_se_reviews.json
!www_trademax_se_reviews_INPROGRESS.json
!www_trademax_se_selenium_reviews.json
!tests/fixtures/*.json
!package.json
.venv/
checkpoints/
```

Then verify — this MUST print `IGNORED` for both:

```bash
cd /Users/nilaysharma/scrapper && for f in .env scrapper.json; do printf "%s " "$f"; git check-ignore -q "$f" && echo IGNORED || echo "DANGER: TRACKED"; done
```

Then verify the owner's committed data files are still tracked (must print three paths):

```bash
git ls-files '*_reviews*.json'
```

- [ ] **Step 2: Create the virtualenv and requirements**

```bash
cd /Users/nilaysharma/scrapper && python3 -m venv .venv && ./.venv/bin/pip install -q --upgrade pip
```

`requirements.txt`:

```
requests>=2.31
gspread>=6.0
google-auth>=2.28
python-dotenv>=1.0
playwright>=1.40
pytest>=8.0
```

```bash
./.venv/bin/pip install -q -r requirements.txt && ./.venv/bin/python -c "import requests, gspread, playwright, pytest; print('deps ok')"
```

Expected: `deps ok`. Do NOT run `playwright install` — this project attaches to the real Chrome already on the machine and never launches a bundled browser.

`pytest.ini`:

```ini
[pytest]
testpaths = tests
markers =
    live: hits the network or a real browser; excluded from the default run
addopts = -m "not live"
```

- [ ] **Step 3: Write the failing test**

`tests/test_models.py`:

```python
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
```

- [ ] **Step 4: Run it to make sure it fails**

Run: `./.venv/bin/pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scraper'`

- [ ] **Step 5: Implement the minimal code**

`scraper/__init__.py` and `tests/__init__.py`: empty files.

`scraper/models.py`:

```python
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
```

- [ ] **Step 6: Run the tests and make sure they pass**

Run: `./.venv/bin/pytest tests/test_models.py -v`
Expected: 6 passed

- [ ] **Step 7: Commit (named paths only)**

```bash
git add .gitignore requirements.txt pytest.ini scraper/__init__.py scraper/models.py tests/__init__.py tests/test_models.py
git commit -m "Add row model and harden gitignore against key leaks"
```

---

### Task 2: Trustpilot parser

**Files:**
- Create: `scraper/platforms/__init__.py`, `scraper/platforms/trustpilot/__init__.py`, `scraper/platforms/trustpilot/parser.py`, `tests/fixtures/trustpilot_page.html`, `tests/test_trustpilot_parser.py`

**Interfaces:**
- Consumes: `Review` from `scraper.models`
- Produces: `page_props(html) -> Optional[dict]`, `parse_pagination(props) -> Optional[dict]` returning `{"total": int, "pages": int}`, `parse_languages(props) -> List[dict]` of `{"code": str, "count": int}`, `parse_reviews(props, *, domain, source_view, fetcher, fetched_at) -> List[Review]`

- [ ] **Step 1: Create the fixture**

`tests/fixtures/trustpilot_page.html` — a minimal page carrying a real-shaped `__NEXT_DATA__`. Two reviews: one with a reply, one without.

```html
<!doctype html><html><body>
<script id="__NEXT_DATA__" type="application/json">
{"buildId":"testbuild","props":{"pageProps":{
 "businessUnit":{"displayName":"Trademax.se","identifyingName":"www.trademax.se","numberOfReviews":73356,"trustScore":3.7},
 "filters":{"pagination":{"currentPage":1,"perPage":20,"totalCount":797,"totalPages":40},
   "selected":{"languages":"en","stars":null,"date":null},
   "reviewStatistics":{"hasMultipleLanguages":true,"reviewLanguages":[
     {"isoCode":"all","reviewCount":66724,"displayName":"all"},
     {"isoCode":"sv","reviewCount":64871,"displayName":"svenska"},
     {"isoCode":"en","reviewCount":1177,"displayName":"English"},
     {"isoCode":"da","reviewCount":154,"displayName":"dansk"}]}},
 "reviews":[
  {"id":"aaa111","rating":1,"title":"Missing parts","text":"Arrived with parts missing.",
   "language":"en","location":"SE","consumer":{"displayName":"Ada L"},
   "dates":{"experiencedDate":"2026-09-02T00:00:00.000Z","publishedDate":"2026-09-02T11:27:14.000Z"},
   "reply":{"message":"Hej! We are sorry.","publishedDate":"2026-09-03T10:13:56.000Z"},
   "labels":{"verification":{"isVerified":false,"reviewSourceName":"Organic"}}},
  {"id":"bbb222","rating":5,"title":"Great sofa","text":"Very happy with it.",
   "language":"en","location":"NO","consumer":{"displayName":"Bo K"},
   "dates":{"experiencedDate":"2026-08-20T00:00:00.000Z","publishedDate":"2026-08-21T09:00:00.000Z"},
   "reply":null,
   "labels":{"verification":{"isVerified":true,"reviewSourceName":"Invitation"}}}
 ]}}}
</script></body></html>
```

- [ ] **Step 2: Write the failing test**

`tests/test_trustpilot_parser.py`:

```python
from __future__ import annotations

from pathlib import Path

from scraper.platforms.trustpilot import parser

FIXTURE = Path(__file__).parent / "fixtures" / "trustpilot_page.html"


def props():
    return parser.page_props(FIXTURE.read_text(encoding="utf-8"))


def test_page_props_extracted():
    assert props() is not None


def test_page_props_returns_none_without_next_data():
    assert parser.page_props("<html><body>Verifying your connection</body></html>") is None


def test_parse_pagination():
    assert parser.parse_pagination(props()) == {"total": 797, "pages": 40}


def test_parse_languages_excludes_all_and_keeps_counts():
    languages = parser.parse_languages(props())
    codes = [lang["code"] for lang in languages]
    assert "all" not in codes
    assert {"code": "sv", "count": 64871} in languages
    assert codes == ["sv", "en", "da"]


def test_parse_reviews_maps_every_field():
    reviews = parser.parse_reviews(props(), domain="www.trademax.se",
                                   source_view="languages=en", fetcher="chrome",
                                   fetched_at="2026-09-04T10:00:00Z")
    assert len(reviews) == 2
    first = reviews[0]
    assert first.review_id == "trustpilot:aaa111"
    assert first.platform == "Trustpilot"
    assert first.url == "https://www.trustpilot.com/reviews/aaa111"
    assert first.rating == 1
    assert first.raw_text == "Arrived with parts missing."
    assert first.review_date == "2026-09-02"
    assert first.experience_date == "2026-09-02"
    assert first.date_precision == "exact"
    assert first.support_reply == "Hej! We are sorry."
    assert first.support_reply_date == "2026-09-03"
    assert first.review_source == "Organic"
    assert first.is_verified == "FALSE"
    assert first.country == "SE"
    assert first.source_view == "languages=en"
    assert first.fetcher == "chrome"


def test_review_without_reply_has_empty_reply_fields():
    second = parser.parse_reviews(props(), domain="www.trademax.se",
                                  source_view="languages=en", fetcher="chrome",
                                  fetched_at="2026-09-04T10:00:00Z")[1]
    assert second.support_reply == ""
    assert second.support_reply_date == ""
    assert second.is_verified == "TRUE"


def test_consumer_name_is_parsed_but_lives_only_on_the_object():
    first = parser.parse_reviews(props(), domain="www.trademax.se", source_view="v",
                                 fetcher="chrome", fetched_at="t")[0]
    assert first.consumer_name == "Ada L"
```

- [ ] **Step 3: Run it to make sure it fails**

Run: `./.venv/bin/pytest tests/test_trustpilot_parser.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scraper.platforms'`

- [ ] **Step 4: Implement**

`scraper/platforms/__init__.py` and `scraper/platforms/trustpilot/__init__.py`: empty files.

`scraper/platforms/trustpilot/parser.py`:

```python
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
```

- [ ] **Step 5: Run the tests and make sure they pass**

Run: `./.venv/bin/pytest tests/test_trustpilot_parser.py -v`
Expected: 8 passed

- [ ] **Step 6: Commit**

```bash
git add scraper/platforms tests/test_trustpilot_parser.py tests/fixtures/trustpilot_page.html
git commit -m "Parse Trustpilot __NEXT_DATA__ into normalized rows"
```

---

### Task 3: Response classifier

The distinction this task encodes is the one that cost the previous attempt weeks: a login redirect is not a block, and must never be retried, failed over, or escalated to credentials.

**Files:**
- Create: `scraper/outcomes.py`, `tests/fixtures/tp_gated.html`, `tests/fixtures/tp_interstitial.html`, `tests/fixtures/tp_empty.html`, `tests/test_outcomes.py`

**Interfaces:**
- Consumes: `page_props` from `scraper.platforms.trustpilot.parser`
- Produces: `Outcome` enum with members `OK`, `TRANSIENT_BLOCK`, `GATED`, `EMPTY`; `classify(html: str) -> Outcome`

- [ ] **Step 1: Create the three fixtures**

`tests/fixtures/tp_gated.html` (the page-11 login redirect):

```html
<!doctype html><html><body>
<script id="__NEXT_DATA__" type="application/json">
{"props":{"pageProps":{"__N_REDIRECT":"/users/connect?redirect=%2Freview%2Fwww.trademax.se%3Fpage%3D11","__N_REDIRECT_STATUS":307}}}
</script>
<p>Log in or sign up below</p></body></html>
```

`tests/fixtures/tp_interstitial.html` (the AWS WAF challenge):

```html
<html lang="en"><head><title>Verifying Connection</title></head>
<body><h1>Verifying your connection...</h1><p>Please wait while we verify your browser.</p></body></html>
```

`tests/fixtures/tp_empty.html` (a valid page past the end of a view):

```html
<!doctype html><html><body>
<script id="__NEXT_DATA__" type="application/json">
{"props":{"pageProps":{"businessUnit":{"displayName":"Trademax.se"},"reviews":[],
 "filters":{"pagination":{"currentPage":3,"perPage":20,"totalCount":40,"totalPages":2}}}}}
</script></body></html>
```

- [ ] **Step 2: Write the failing test**

`tests/test_outcomes.py`:

```python
from __future__ import annotations

from pathlib import Path

from scraper.outcomes import Outcome, classify

FIXTURES = Path(__file__).parent / "fixtures"


def read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_ok_page():
    assert classify(read("trustpilot_page.html")) is Outcome.OK


def test_login_redirect_is_gated_not_blocked():
    assert classify(read("tp_gated.html")) is Outcome.GATED


def test_waf_interstitial_is_transient():
    assert classify(read("tp_interstitial.html")) is Outcome.TRANSIENT_BLOCK


def test_tiny_body_is_transient():
    assert classify("<html><body>nope</body></html>") is Outcome.TRANSIENT_BLOCK


def test_valid_page_with_no_reviews_is_empty():
    assert classify(read("tp_empty.html")) is Outcome.EMPTY


def test_empty_string_is_transient():
    assert classify("") is Outcome.TRANSIENT_BLOCK


def test_only_transient_is_retryable():
    assert Outcome.TRANSIENT_BLOCK.retryable is True
    assert Outcome.GATED.retryable is False
    assert Outcome.OK.retryable is False
    assert Outcome.EMPTY.retryable is False
```

- [ ] **Step 3: Run it to make sure it fails**

Run: `./.venv/bin/pytest tests/test_outcomes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scraper.outcomes'`

- [ ] **Step 4: Implement**

`scraper/outcomes.py`:

```python
from __future__ import annotations

from enum import Enum

from scraper.platforms.trustpilot.parser import page_props

MIN_REAL_PAGE_BYTES = 5000
GATE_MARKERS = ("__N_REDIRECT", "/users/connect")
CHALLENGE_MARKERS = ("Verifying Connection", "Verifying your connection")


class Outcome(Enum):
    OK = "ok"
    TRANSIENT_BLOCK = "transient_block"
    GATED = "gated"
    EMPTY = "empty"

    @property
    def retryable(self) -> bool:
        """Only a transient block is worth trying again. GATED is a product
        gate: no proxy, fetcher, IP or credential moves it."""
        return self is Outcome.TRANSIENT_BLOCK


def classify(html: str) -> Outcome:
    text = html or ""

    if all(marker in text for marker in GATE_MARKERS):
        return Outcome.GATED

    props = page_props(text)
    if props is not None:
        if props.get("__N_REDIRECT"):
            return Outcome.GATED
        return Outcome.OK if props.get("reviews") else Outcome.EMPTY

    if any(marker in text for marker in CHALLENGE_MARKERS):
        return Outcome.TRANSIENT_BLOCK
    if len(text) < MIN_REAL_PAGE_BYTES:
        return Outcome.TRANSIENT_BLOCK
    return Outcome.TRANSIENT_BLOCK
```

- [ ] **Step 5: Run the tests and make sure they pass**

Run: `./.venv/bin/pytest tests/test_outcomes.py -v`
Expected: 7 passed

- [ ] **Step 6: Commit**

```bash
git add scraper/outcomes.py tests/test_outcomes.py tests/fixtures/tp_gated.html tests/fixtures/tp_interstitial.html tests/fixtures/tp_empty.html
git commit -m "Classify fetch outcomes, keeping the login gate distinct from blocks"
```

---

### Task 4: Trustpilot view planner

Pure logic, no network. The planner receives a `probe` callable and returns the pages to fetch.

**Files:**
- Create: `scraper/platforms/trustpilot/planner.py`, `tests/test_trustpilot_planner.py`

**Interfaces:**
- Consumes: nothing
- Produces: constants `PER_PAGE = 20`, `MAX_PAGES = 10`, `VIEW_CAP = 200`, `DATE_BUCKETS`; `View` frozen dataclass with `.of(**params)`, `.with_(key, value)`, `.has(key)`, `.get(key, default=None)`, `.query()`, `.label()`; `PlannedView` with `.view`, `.pages`, `.total`, `.expected`; `pages_for(total) -> List[int]`; `plan_views(probe, *, languages, target=None) -> List[PlannedView]` where `probe: Callable[[View], Optional[int]]`

- [ ] **Step 1: Write the failing test**

`tests/test_trustpilot_planner.py`:

```python
from __future__ import annotations

from scraper.platforms.trustpilot.planner import (
    MAX_PAGES, VIEW_CAP, PlannedView, View, pages_for, plan_views,
)

LANGUAGES = [{"code": "sv", "count": 64871}, {"code": "en", "count": 1177},
             {"code": "da", "count": 154}]


def probe_from(totals):
    """totals maps a view label to its totalCount."""
    def probe(view):
        return totals.get(view.label())
    return probe


def test_pages_for_small_view():
    assert pages_for(61) == [1, 2, 3, 4]


def test_pages_for_exact_multiple():
    assert pages_for(40) == [1, 2]


def test_pages_never_exceed_the_cap():
    assert pages_for(64871) == list(range(1, MAX_PAGES + 1))
    assert len(pages_for(64871)) * 20 == VIEW_CAP


def test_view_query_and_label_are_stable():
    view = View.of(languages="en").with_("stars", 5)
    assert view.query() == "?languages=en&stars=5"
    assert view.label() == "languages=en,stars=5"


def test_small_root_is_not_split():
    plans = plan_views(probe_from({"languages=all": 150}), languages=LANGUAGES)
    assert len(plans) == 1
    assert plans[0].view.label() == "languages=all"
    assert plans[0].pages == [1, 2, 3, 4, 5, 6, 7, 8]


def test_large_root_splits_by_stars_first():
    totals = {"languages=all": 797,
              "languages=all,stars=1": 292, "languages=all,stars=2": 61,
              "languages=all,stars=3": 66, "languages=all,stars=4": 183,
              "languages=all,stars=5": 678}
    plans = plan_views(probe_from(totals), languages=[])
    labels = sorted(plan.view.label() for plan in plans)
    assert labels == sorted(k for k in totals if "stars=" in k)


def test_star_view_over_the_cap_splits_by_language():
    totals = {"languages=all": 66724,
              "languages=all,stars=5": 42020,
              "languages=sv,stars=5": 40000,
              "languages=en,stars=5": 700,
              "languages=da,stars=5": 80}
    for star in (1, 2, 3, 4):
        totals["languages=all,stars={0}".format(star)] = 100
    plans = plan_views(probe_from(totals), languages=LANGUAGES)
    labels = [plan.view.label() for plan in plans]
    assert "languages=sv,stars=5" in labels
    assert "languages=en,stars=5" in labels
    assert "languages=all,stars=5" not in labels


def test_invalid_language_code_echoing_parent_total_is_discarded():
    """An unrecognised code silently returns the unfiltered result. A child
    whose total equals its parent's is a fallback, not a real slice."""
    totals = {"languages=all": 66724, "languages=all,stars=5": 42020,
              "languages=sv,stars=5": 42020,   # <- the fallback
              "languages=en,stars=5": 700,
              "languages=da,stars=5": 80}
    for star in (1, 2, 3, 4):
        totals["languages=all,stars={0}".format(star)] = 100
    plans = plan_views(probe_from(totals), languages=LANGUAGES)
    labels = [plan.view.label() for plan in plans]
    assert "languages=sv,stars=5" not in labels
    assert "languages=en,stars=5" in labels


def test_unreachable_view_is_dropped():
    plans = plan_views(probe_from({"languages=all": 0}), languages=LANGUAGES)
    assert plans == []


def test_target_stops_planning_early():
    totals = {"languages=all": 797,
              "languages=all,stars=1": 292, "languages=all,stars=2": 61,
              "languages=all,stars=3": 66, "languages=all,stars=4": 183,
              "languages=all,stars=5": 678}
    plans = plan_views(probe_from(totals), languages=[], target=250)
    assert sum(plan.expected for plan in plans) >= 250
    assert len(plans) == 2  # the two 200-yield views, biggest first


def test_expected_is_capped_at_two_hundred():
    assert PlannedView(View.of(), [1], 42020).expected == VIEW_CAP
    assert PlannedView(View.of(), [1], 61).expected == 61


def test_no_plan_ever_exceeds_page_ten():
    totals = {"languages=all": 66724}
    for star in (1, 2, 3, 4, 5):
        totals["languages=all,stars={0}".format(star)] = 42020
        for lang in LANGUAGES:
            totals["languages={0},stars={1}".format(lang["code"], star)] = 30000
    plans = plan_views(probe_from(totals), languages=LANGUAGES)
    assert plans
    for plan in plans:
        assert max(plan.pages) <= MAX_PAGES
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `./.venv/bin/pytest tests/test_trustpilot_planner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scraper.platforms.trustpilot.planner'`

- [ ] **Step 3: Implement**

`scraper/platforms/trustpilot/planner.py`:

```python
from __future__ import annotations

import math
from dataclasses import dataclass, field
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
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `./.venv/bin/pytest tests/test_trustpilot_planner.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add scraper/platforms/trustpilot/planner.py tests/test_trustpilot_planner.py
git commit -m "Plan Trustpilot filter views around the 200-per-view gate"
```

---

### Task 5: Fetcher base + Jina fetcher

**Files:**
- Create: `scraper/fetchers/__init__.py`, `scraper/fetchers/base.py`, `scraper/fetchers/jina.py`, `tests/test_jina_fetcher.py`

**Interfaces:**
- Consumes: nothing
- Produces: `Fetcher` base class with attribute `name: str`, methods `fetch(url: str) -> str` and `close() -> None`; `JinaFetcher(api_key: Optional[str] = None, timeout: int = 45)` with `name == "jina"`

- [ ] **Step 1: Write the failing test**

`tests/test_jina_fetcher.py`:

```python
from __future__ import annotations

import pytest

from scraper.fetchers.jina import JINA_BASE, JinaFetcher


class FakeResponse:
    def __init__(self, text: str):
        self.text = text
        self.status_code = 200

    def raise_for_status(self):
        return None


def test_prefixes_the_reader_base_and_asks_for_html(monkeypatch):
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers or {}
        return FakeResponse("<html>ok</html>")

    monkeypatch.setattr("scraper.fetchers.jina.requests.get", fake_get)
    fetcher = JinaFetcher()

    assert fetcher.fetch("https://www.trustpilot.com/review/x") == "<html>ok</html>"
    assert captured["url"] == JINA_BASE + "https://www.trustpilot.com/review/x"
    assert captured["headers"]["X-Return-Format"] == "html"
    assert "Authorization" not in captured["headers"]


def test_sends_bearer_token_when_a_key_is_configured(monkeypatch):
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured["headers"] = headers or {}
        return FakeResponse("<html>ok</html>")

    monkeypatch.setattr("scraper.fetchers.jina.requests.get", fake_get)
    JinaFetcher(api_key="jina_secret").fetch("https://example.com")

    assert captured["headers"]["Authorization"] == "Bearer jina_secret"


def test_name_is_recorded_for_row_provenance():
    assert JinaFetcher().name == "jina"


def test_network_errors_surface_as_empty_html(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        raise OSError("connection reset")

    monkeypatch.setattr("scraper.fetchers.jina.requests.get", fake_get)
    # Empty html classifies as TRANSIENT_BLOCK, which the pool retries.
    assert JinaFetcher().fetch("https://example.com") == ""
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `./.venv/bin/pytest tests/test_jina_fetcher.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scraper.fetchers'`

- [ ] **Step 3: Implement**

`scraper/fetchers/__init__.py`: empty file.

`scraper/fetchers/base.py`:

```python
from __future__ import annotations


class Fetcher:
    """One way of getting a page's HTML. Fetchers never interpret what they
    fetch — classification is the pool's job, so every fetcher stays swappable."""

    name = "base"

    def fetch(self, url: str) -> str:
        raise NotImplementedError

    def close(self) -> None:
        return None
```

`scraper/fetchers/jina.py`:

```python
from __future__ import annotations

import os
from typing import Dict, Optional

import requests

from scraper.fetchers.base import Fetcher

JINA_BASE = "https://r.jina.ai/"


class JinaFetcher(Fetcher):
    """Reader proxy. html mode returns the full page including __NEXT_DATA__.
    Works without a key; a key only raises the rate limit."""

    name = "jina"

    def __init__(self, api_key: Optional[str] = None, timeout: int = 45):
        self.api_key = api_key if api_key is not None else os.getenv("JINA_API_KEY", "")
        self.timeout = timeout

    def _headers(self) -> Dict[str, str]:
        headers = {"X-Return-Format": "html", "X-Timeout": "40"}
        if self.api_key:
            headers["Authorization"] = "Bearer {0}".format(self.api_key)
        return headers

    def fetch(self, url: str) -> str:
        try:
            response = requests.get(JINA_BASE + url, headers=self._headers(),
                                    timeout=self.timeout)
            response.raise_for_status()
            return response.text
        except Exception:
            # An empty body classifies as TRANSIENT_BLOCK; the pool decides
            # whether to retry or fail over.
            return ""
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `./.venv/bin/pytest tests/test_jina_fetcher.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add scraper/fetchers tests/test_jina_fetcher.py
git commit -m "Add Jina reader fetcher"
```

---

### Task 6: Chrome CDP fetcher

Attaches to a **real** Chrome. A Playwright- or Selenium-launched browser gets walled by the WAF; attaching to real Chrome does not. The profile persists the clearance cookie, so the challenge is a one-time cost per profile.

**Files:**
- Create: `scraper/fetchers/chrome.py`, `tests/test_chrome_fetcher.py`

**Interfaces:**
- Consumes: `Fetcher` from `scraper.fetchers.base`
- Produces: `ChromeCDPFetcher(port: int = 9333, profile: Optional[str] = None, delay_range: Tuple[float, float] = (3.5, 7.0), sleep=time.sleep, rng=random.uniform)` with `name == "chrome"`, `.fetch(url)`, `.close()`; module constants `CHROME_PATH`, `DEFAULT_PROFILE`

- [ ] **Step 1: Write the failing test**

`tests/test_chrome_fetcher.py`:

```python
from __future__ import annotations

import pytest

from scraper.fetchers.chrome import ChromeCDPFetcher


class FakePage:
    def __init__(self, html: str):
        self._html = html
        self.visited = []

    def set_default_timeout(self, ms):
        return None

    def goto(self, url, wait_until=None):
        self.visited.append(url)

    def content(self):
        return self._html


def test_fetch_returns_page_html_and_paces_itself():
    slept = []
    fetcher = ChromeCDPFetcher(sleep=lambda s: slept.append(s),
                               rng=lambda a, b: 5.0)
    fetcher._page = FakePage("<html>real page</html>")

    assert fetcher.fetch("https://example.com/a") == "<html>real page</html>"
    assert fetcher._page.visited == ["https://example.com/a"]
    assert slept == [5.0]


def test_navigation_failure_returns_empty_html():
    class Boom(FakePage):
        def goto(self, url, wait_until=None):
            raise RuntimeError("navigation timeout")

    fetcher = ChromeCDPFetcher(sleep=lambda s: None, rng=lambda a, b: 0.0)
    fetcher._page = Boom("")
    assert fetcher.fetch("https://example.com") == ""


def test_name_is_recorded_for_row_provenance():
    assert ChromeCDPFetcher().name == "chrome"


@pytest.mark.live
def test_live_fetch_of_a_trustpilot_page():
    """Launches real Chrome. Run explicitly: pytest -m live"""
    from scraper.outcomes import Outcome, classify

    fetcher = ChromeCDPFetcher()
    try:
        html = fetcher.fetch(
            "https://www.trustpilot.com/review/www.trademax.se?languages=en")
        outcome = classify(html)
        # A cold profile may eat one challenge; retry once.
        if outcome is Outcome.TRANSIENT_BLOCK:
            outcome = classify(fetcher.fetch(
                "https://www.trustpilot.com/review/www.trademax.se?languages=en"))
        assert outcome is Outcome.OK
    finally:
        fetcher.close()
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `./.venv/bin/pytest tests/test_chrome_fetcher.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scraper.fetchers.chrome'`

- [ ] **Step 3: Implement**

`scraper/fetchers/chrome.py`:

```python
from __future__ import annotations

import random
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional, Tuple

from scraper.fetchers.base import Fetcher

CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
DEFAULT_PROFILE = str(Path.home() / ".trustpilot-chrome")
DEFAULT_PORT = 9333


class ChromeCDPFetcher(Fetcher):
    """Real Chrome, attached over CDP. The profile persists the WAF clearance
    cookie, so the challenge is paid once, not once per run."""

    name = "chrome"

    def __init__(self, port: int = DEFAULT_PORT, profile: Optional[str] = None,
                 delay_range: Tuple[float, float] = (3.5, 7.0),
                 sleep: Callable[[float], Any] = time.sleep,
                 rng: Callable[[float, float], float] = random.uniform,
                 headless: bool = False):
        self.port = port
        self.profile = profile or DEFAULT_PROFILE
        self.delay_range = delay_range
        self._sleep = sleep
        self._rng = rng
        self.headless = headless
        self._process = None
        self._playwright = None
        self._browser = None
        self._page = None

    # -- lifecycle -------------------------------------------------------
    def _port_open(self) -> bool:
        try:
            urllib.request.urlopen(
                "http://localhost:{0}/json/version".format(self.port), timeout=2)
            return True
        except Exception:
            return False

    def _launch(self) -> None:
        args = [CHROME_PATH,
                "--remote-debugging-port={0}".format(self.port),
                "--user-data-dir={0}".format(self.profile),
                "--no-first-run", "--no-default-browser-check",
                "--hide-crash-restore-bubble", "--disable-session-crashed-bubble",
                "--restore-last-session=false"]
        if self.headless:
            args.append("--headless=new")
        self._process = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                         stderr=subprocess.DEVNULL)
        for _ in range(30):
            if self._port_open():
                return
            time.sleep(1)
        raise RuntimeError(
            "Chrome did not expose the debug port on {0} — is another Chrome "
            "already using this profile?".format(self.port))

    def _ensure_page(self):
        if self._page is not None:
            return self._page
        if not self._port_open():
            self._launch()
        from playwright.sync_api import sync_playwright
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.connect_over_cdp(
            "http://localhost:{0}".format(self.port))
        context = (self._browser.contexts[0] if self._browser.contexts
                   else self._browser.new_context())
        self._page = context.pages[0] if context.pages else context.new_page()
        self._page.set_default_timeout(45000)
        return self._page

    # -- fetching --------------------------------------------------------
    def fetch(self, url: str) -> str:
        page = self._ensure_page()
        try:
            page.goto(url, wait_until="domcontentloaded")
            html = page.content()
        except Exception:
            return ""
        finally:
            self._sleep(self._rng(*self.delay_range))
        return html

    def close(self) -> None:
        for closer in (lambda: self._browser and self._browser.close(),
                       lambda: self._playwright and self._playwright.stop(),
                       lambda: self._process and self._process.terminate()):
            try:
                closer()
            except Exception:
                pass
        self._page = None
        self._browser = None
        self._playwright = None
        self._process = None
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `./.venv/bin/pytest tests/test_chrome_fetcher.py -v`
Expected: 3 passed, 1 deselected (the live test)

- [ ] **Step 5: Run the live test once, by hand**

Run: `./.venv/bin/pytest tests/test_chrome_fetcher.py -m live -v`
Expected: PASS. A Chrome window opens. If it fails with a challenge twice in a row, open `https://www.trustpilot.com` manually in that Chrome window once, then re-run — the profile keeps the cookie afterwards.

- [ ] **Step 6: Commit**

```bash
git add scraper/fetchers/chrome.py tests/test_chrome_fetcher.py
git commit -m "Add real-Chrome CDP fetcher"
```

---

### Task 7: Fetcher pool — retry, failover, circuit breaker

**Files:**
- Create: `scraper/fetchers/pool.py`, `tests/test_fetcher_pool.py`

**Interfaces:**
- Consumes: `Fetcher`, `Outcome`, `classify`
- Produces: `FetchResult` dataclass with `.html`, `.outcome`, `.fetcher`; `FetcherPool(fetchers: List[Fetcher], max_retries: int = 2, failure_threshold: int = 3, sleep=time.sleep)` with `.fetch(url) -> FetchResult`, `.stats() -> Dict[str, int]`, `.close()`

- [ ] **Step 1: Write the failing test**

`tests/test_fetcher_pool.py`:

```python
from __future__ import annotations

from pathlib import Path

from scraper.fetchers.pool import FetcherPool
from scraper.outcomes import Outcome

FIXTURES = Path(__file__).parent / "fixtures"
GOOD = (FIXTURES / "trustpilot_page.html").read_text(encoding="utf-8")
GATED = (FIXTURES / "tp_gated.html").read_text(encoding="utf-8")
BLOCKED = (FIXTURES / "tp_interstitial.html").read_text(encoding="utf-8")


class ScriptedFetcher:
    def __init__(self, name, responses):
        self.name = name
        self.responses = list(responses)
        self.calls = 0
        self.closed = False

    def fetch(self, url):
        self.calls += 1
        if not self.responses:
            return BLOCKED
        return self.responses.pop(0)

    def close(self):
        self.closed = True


def pool_of(*fetchers, **kwargs):
    kwargs.setdefault("sleep", lambda s: None)
    return FetcherPool(list(fetchers), **kwargs)


def test_happy_path_uses_the_first_fetcher():
    primary = ScriptedFetcher("chrome", [GOOD])
    backup = ScriptedFetcher("jina", [GOOD])
    result = pool_of(primary, backup).fetch("https://x")

    assert result.outcome is Outcome.OK
    assert result.fetcher == "chrome"
    assert backup.calls == 0


def test_transient_block_retries_the_same_fetcher_first():
    primary = ScriptedFetcher("chrome", [BLOCKED, GOOD])
    result = pool_of(primary, ScriptedFetcher("jina", [GOOD])).fetch("https://x")

    assert result.outcome is Outcome.OK
    assert result.fetcher == "chrome"
    assert primary.calls == 2


def test_exhausted_retries_fail_over_to_the_backup():
    primary = ScriptedFetcher("chrome", [BLOCKED, BLOCKED, BLOCKED])
    backup = ScriptedFetcher("jina", [GOOD])
    result = pool_of(primary, backup, max_retries=2).fetch("https://x")

    assert result.outcome is Outcome.OK
    assert result.fetcher == "jina"


def test_gated_never_retries_and_never_fails_over():
    primary = ScriptedFetcher("chrome", [GATED, GOOD])
    backup = ScriptedFetcher("jina", [GOOD])
    result = pool_of(primary, backup).fetch("https://x")

    assert result.outcome is Outcome.GATED
    assert primary.calls == 1
    assert backup.calls == 0


def test_circuit_breaker_demotes_a_failing_fetcher_for_the_rest_of_the_run():
    primary = ScriptedFetcher("chrome", [BLOCKED] * 20)
    backup = ScriptedFetcher("jina", [GOOD] * 20)
    pool = pool_of(primary, backup, max_retries=0, failure_threshold=3)

    for _ in range(3):
        pool.fetch("https://x")
    calls_after_tripping = primary.calls

    pool.fetch("https://x")
    pool.fetch("https://x")

    assert primary.calls == calls_after_tripping  # never called again
    assert pool.stats()["demoted_chrome"] == 1


def test_all_fetchers_down_returns_the_last_outcome():
    pool = pool_of(ScriptedFetcher("chrome", [BLOCKED] * 5),
                   ScriptedFetcher("jina", [BLOCKED] * 5), max_retries=1)
    result = pool.fetch("https://x")
    assert result.outcome is Outcome.TRANSIENT_BLOCK


def test_stats_count_outcomes_per_fetcher():
    pool = pool_of(ScriptedFetcher("chrome", [GOOD, GOOD]))
    pool.fetch("https://x")
    pool.fetch("https://y")
    assert pool.stats()["ok_chrome"] == 2


def test_close_closes_every_fetcher():
    primary = ScriptedFetcher("chrome", [GOOD])
    backup = ScriptedFetcher("jina", [GOOD])
    pool_of(primary, backup).close()
    assert primary.closed and backup.closed
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `./.venv/bin/pytest tests/test_fetcher_pool.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scraper.fetchers.pool'`

- [ ] **Step 3: Implement**

`scraper/fetchers/pool.py`:

```python
from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Dict, List

from scraper.fetchers.base import Fetcher
from scraper.outcomes import Outcome, classify


@dataclass
class FetchResult:
    html: str
    outcome: Outcome
    fetcher: str


class FetcherPool:
    """Tries fetchers in order. Retries a transient block, then fails over.
    Three consecutive transient blocks demote a fetcher for the rest of the run.

    A GATED response short-circuits everything: it is a product gate, so a
    second attempt from a different IP returns exactly the same thing."""

    def __init__(self, fetchers: List[Fetcher], max_retries: int = 2,
                 failure_threshold: int = 3,
                 sleep: Callable[[float], Any] = time.sleep):
        if not fetchers:
            raise ValueError("FetcherPool needs at least one fetcher")
        self.fetchers = list(fetchers)
        self.max_retries = max_retries
        self.failure_threshold = failure_threshold
        self._sleep = sleep
        self._consecutive_failures: Dict[str, int] = {}
        self._demoted: Dict[str, bool] = {}
        self._counts: Counter = Counter()

    def _available(self) -> List[Fetcher]:
        return [f for f in self.fetchers if not self._demoted.get(f.name)]

    def _record(self, fetcher: Fetcher, outcome: Outcome) -> None:
        self._counts["{0}_{1}".format(outcome.value.split("_")[0], fetcher.name)] += 1
        if outcome is Outcome.TRANSIENT_BLOCK:
            failures = self._consecutive_failures.get(fetcher.name, 0) + 1
            self._consecutive_failures[fetcher.name] = failures
            if failures >= self.failure_threshold and not self._demoted.get(fetcher.name):
                self._demoted[fetcher.name] = True
                self._counts["demoted_{0}".format(fetcher.name)] += 1
        else:
            self._consecutive_failures[fetcher.name] = 0

    def fetch(self, url: str) -> FetchResult:
        last = FetchResult("", Outcome.TRANSIENT_BLOCK, "none")

        for fetcher in self._available():
            for attempt in range(self.max_retries + 1):
                html = fetcher.fetch(url)
                outcome = classify(html)
                self._record(fetcher, outcome)
                last = FetchResult(html, outcome, fetcher.name)

                if outcome is not Outcome.TRANSIENT_BLOCK:
                    return last
                if self._demoted.get(fetcher.name):
                    break
                if attempt < self.max_retries:
                    self._sleep(2.0 * (attempt + 1))
        return last

    def stats(self) -> Dict[str, int]:
        return dict(self._counts)

    def close(self) -> None:
        for fetcher in self.fetchers:
            try:
                fetcher.close()
            except Exception:
                pass
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `./.venv/bin/pytest tests/test_fetcher_pool.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add scraper/fetchers/pool.py tests/test_fetcher_pool.py
git commit -m "Add fetcher pool with failover and circuit breaker"
```

---

### Task 8: Trustpilot adapter

**Files:**
- Create: `scraper/platforms/trustpilot/adapter.py`, `tests/test_trustpilot_adapter.py`

**Interfaces:**
- Consumes: `FetcherPool`, `plan_views`, `View`, parser functions, `Outcome`
- Produces: `CollectResult` dataclass with `.reviews: List[Review]` and `.stats: Dict[str, int]`; `TrustpilotAdapter(pool, now: Callable[[], str] = ...)` with `.platform == "Trustpilot"` and `.collect(domain: str, target: Optional[int] = None) -> CollectResult`

- [ ] **Step 1: Write the failing test**

`tests/test_trustpilot_adapter.py`:

```python
from __future__ import annotations

from pathlib import Path

from scraper.fetchers.pool import FetcherPool
from scraper.platforms.trustpilot.adapter import TrustpilotAdapter

FIXTURES = Path(__file__).parent / "fixtures"
GOOD = (FIXTURES / "trustpilot_page.html").read_text(encoding="utf-8")
GATED = (FIXTURES / "tp_gated.html").read_text(encoding="utf-8")
EMPTY = (FIXTURES / "tp_empty.html").read_text(encoding="utf-8")


class RecordingFetcher:
    def __init__(self, name="chrome", responder=None):
        self.name = name
        self.urls = []
        self.responder = responder or (lambda url: GOOD)

    def fetch(self, url):
        self.urls.append(url)
        return self.responder(url)

    def close(self):
        return None


def adapter_with(fetcher):
    pool = FetcherPool([fetcher], max_retries=0, sleep=lambda s: None)
    return TrustpilotAdapter(pool, now=lambda: "2026-09-04T10:00:00Z")


def test_collect_dedupes_repeated_reviews_across_pages():
    fetcher = RecordingFetcher()
    result = adapter_with(fetcher).collect("www.trademax.se")

    ids = [review.review_id for review in result.reviews]
    assert sorted(ids) == ["trustpilot:aaa111", "trustpilot:bbb222"]
    assert result.stats["duplicates_skipped"] > 0


def test_collect_never_requests_a_page_above_ten():
    fetcher = RecordingFetcher()
    adapter_with(fetcher).collect("www.trademax.se")

    pages = [int(url.split("page=")[1].split("&")[0])
             for url in fetcher.urls if "page=" in url]
    assert pages, "expected paged requests"
    assert max(pages) <= 10


def test_gated_page_stops_that_view_immediately():
    def responder(url):
        if "page=3" in url:
            return GATED
        return GOOD

    fetcher = RecordingFetcher(responder=responder)
    result = adapter_with(fetcher).collect("www.trademax.se")

    pages = [int(url.split("page=")[1].split("&")[0])
             for url in fetcher.urls if "page=" in url]
    assert 4 not in pages
    assert result.stats["gated"] >= 1


def test_empty_page_ends_the_view_without_error():
    fetcher = RecordingFetcher(responder=lambda url: EMPTY if "page=2" in url else GOOD)
    result = adapter_with(fetcher).collect("www.trademax.se")
    assert result.stats["pages_fetched"] >= 1


def test_rows_carry_provenance():
    result = adapter_with(RecordingFetcher()).collect("www.trademax.se")
    review = result.reviews[0]
    assert review.fetcher == "chrome"
    assert review.fetched_at == "2026-09-04T10:00:00Z"
    assert review.company_domain == "www.trademax.se"
    assert review.source_view


def test_target_limits_the_work():
    fetcher = RecordingFetcher()
    result = adapter_with(fetcher).collect("www.trademax.se", target=20)
    assert result.stats["views_planned"] >= 1
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `./.venv/bin/pytest tests/test_trustpilot_adapter.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scraper.platforms.trustpilot.adapter'`

- [ ] **Step 3: Implement**

`scraper/platforms/trustpilot/adapter.py`:

```python
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional

from scraper.fetchers.pool import FetcherPool
from scraper.models import Review
from scraper.outcomes import Outcome
from scraper.platforms.trustpilot import parser
from scraper.platforms.trustpilot.planner import View, plan_views

LISTING_URL = "https://www.trustpilot.com/review/{domain}{query}"


def _utc_now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class CollectResult:
    reviews: List[Review] = field(default_factory=list)
    stats: Dict[str, int] = field(default_factory=dict)


class TrustpilotAdapter:
    platform = "Trustpilot"

    def __init__(self, pool: FetcherPool, now: Callable[[], str] = _utc_now):
        self.pool = pool
        self.now = now

    # -- url ------------------------------------------------------------
    def _url(self, domain: str, view: View, page: int) -> str:
        query = view.query()
        separator = "&" if query else "?"
        return LISTING_URL.format(domain=domain, query=query) + \
            "{0}page={1}".format(separator, page)

    # -- collection ------------------------------------------------------
    def collect(self, domain: str, target: Optional[int] = None) -> CollectResult:
        counts: Counter = Counter()
        page_one_cache: Dict[str, str] = {}
        languages: List[Dict[str, object]] = []

        def probe(view: View) -> Optional[int]:
            """Fetch page 1 of a view for its totalCount. The HTML is cached so
            harvesting never re-fetches page 1."""
            result = self.pool.fetch(self._url(domain, view, 1))
            counts["pages_fetched"] += 1
            counts[result.outcome.value] += 1
            if result.outcome is not Outcome.OK:
                return None
            page_one_cache[view.label()] = result.html
            props = parser.page_props(result.html)
            if props is None:
                return None
            if not languages:
                languages.extend(parser.parse_languages(props))
            pagination = parser.parse_pagination(props)
            return pagination["total"] if pagination else None

        planned = plan_views(probe, languages=languages, target=target)
        counts["views_planned"] = len(planned)

        seen = set()
        reviews: List[Review] = []

        for plan in planned:
            label = plan.view.label()
            for page in plan.pages:
                if page == 1 and label in page_one_cache:
                    html, fetcher_name = page_one_cache[label], self._last_fetcher()
                    outcome = Outcome.OK
                else:
                    result = self.pool.fetch(self._url(domain, plan.view, page))
                    counts["pages_fetched"] += 1
                    counts[result.outcome.value] += 1
                    html, outcome, fetcher_name = (result.html, result.outcome,
                                                   result.fetcher)

                if outcome is Outcome.GATED:
                    # A login gate below page 11 means the planner mis-planned.
                    if page <= 10:
                        counts["gated_below_cap"] += 1
                    break
                if outcome is not Outcome.OK:
                    break

                props = parser.page_props(html)
                if props is None:
                    break
                for review in parser.parse_reviews(
                        props, domain=domain, source_view=label,
                        fetcher=fetcher_name, fetched_at=self.now()):
                    if review.review_id in seen:
                        counts["duplicates_skipped"] += 1
                        continue
                    seen.add(review.review_id)
                    reviews.append(review)

            if target is not None and len(reviews) >= target:
                break

        counts["unique_reviews"] = len(reviews)
        counts.update(self.pool.stats())
        return CollectResult(reviews=reviews, stats=dict(counts))

    def _last_fetcher(self) -> str:
        for key, value in self.pool.stats().items():
            if key.startswith("ok_") and value:
                return key[len("ok_"):]
        return "unknown"
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `./.venv/bin/pytest tests/test_trustpilot_adapter.py -v`
Expected: 6 passed

- [ ] **Step 5: Run the whole suite**

Run: `./.venv/bin/pytest -q`
Expected: all green

- [ ] **Step 6: Commit**

```bash
git add scraper/platforms/trustpilot/adapter.py tests/test_trustpilot_adapter.py
git commit -m "Wire Trustpilot adapter: plan, fetch, parse, dedupe"
```

---

### Task 9: Google Sheets writer

**Files:**
- Create: `scraper/sheets.py`, `tests/test_sheets.py`

**Interfaces:**
- Consumes: `Review`, `sheet_columns`
- Produces: `open_spreadsheet(sheet_url, creds_file) -> gspread.Spreadsheet`; `get_or_create_tab(spreadsheet, title, columns) -> worksheet`; `existing_review_ids(worksheet) -> Set[str]`; `append_reviews(worksheet, reviews, columns, chunk_size=500) -> int`

- [ ] **Step 1: Write the failing test**

`tests/test_sheets.py`:

```python
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
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `./.venv/bin/pytest tests/test_sheets.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scraper.sheets'`

- [ ] **Step 3: Implement**

`scraper/sheets.py`:

```python
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
```

Note on the test's `add_worksheet`: gspread's signature is `add_worksheet(title, rows, cols)`, and the fake accepts the same keyword arguments.

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `./.venv/bin/pytest tests/test_sheets.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add scraper/sheets.py tests/test_sheets.py
git commit -m "Write reviews to per-company sheet tabs with dedup support"
```

---

### Task 10: Checkpoint + CLI (Trustpilot end-to-end)

At the end of this task the system does something useful on its own: scrape a company from Trustpilot into the sheet, resumably.

**Files:**
- Create: `scraper/checkpoint.py`, `scraper/run.py`, `tests/test_checkpoint.py`, `tests/test_run.py`
- Modify: `scraper/platforms/trustpilot/adapter.py` — add an `on_view` callback

**Interfaces:**
- Consumes: `TrustpilotAdapter`, `FetcherPool`, `ChromeCDPFetcher`, `JinaFetcher`, sheets functions
- Produces: `Checkpoint(path)` with `.reviews() -> List[dict]`, `.extend(reviews) -> None`, `.clear() -> None`; `TrustpilotAdapter.collect(..., on_view: Optional[Callable[[str, List[Review]], None]] = None)`; `scraper/run.py` CLI entry point `main(argv=None) -> int`

- [ ] **Step 1: Write the failing checkpoint test**

`tests/test_checkpoint.py`:

```python
from __future__ import annotations

from scraper.checkpoint import Checkpoint
from scraper.models import Review


def test_extend_then_reload_from_disk(tmp_path):
    path = tmp_path / "trademax.json"
    checkpoint = Checkpoint(str(path))
    checkpoint.extend([Review(review_id="trustpilot:a", platform="Trustpilot", rating=3)])

    reloaded = Checkpoint(str(path))
    assert [row["review_id"] for row in reloaded.reviews()] == ["trustpilot:a"]
    assert reloaded.reviews()[0]["rating"] == 3


def test_extend_accumulates_across_calls(tmp_path):
    checkpoint = Checkpoint(str(tmp_path / "c.json"))
    checkpoint.extend([Review(review_id="t:a", platform="Trustpilot")])
    checkpoint.extend([Review(review_id="t:b", platform="Trustpilot")])
    assert len(Checkpoint(str(tmp_path / "c.json")).reviews()) == 2


def test_clear_removes_the_file(tmp_path):
    path = tmp_path / "c.json"
    checkpoint = Checkpoint(str(path))
    checkpoint.extend([Review(review_id="t:a", platform="Trustpilot")])
    checkpoint.clear()
    assert not path.exists()
    assert Checkpoint(str(path)).reviews() == []


def test_missing_file_is_not_an_error(tmp_path):
    assert Checkpoint(str(tmp_path / "nope.json")).reviews() == []


def test_corrupt_file_is_ignored_rather_than_crashing_a_run(tmp_path):
    path = tmp_path / "c.json"
    path.write_text("{not json", encoding="utf-8")
    assert Checkpoint(str(path)).reviews() == []
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `./.venv/bin/pytest tests/test_checkpoint.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scraper.checkpoint'`

- [ ] **Step 3: Implement the checkpoint**

`scraper/checkpoint.py`:

```python
from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Any, Dict, List

from scraper.models import Review


class Checkpoint:
    """Mid-run safety net. Not the store — the sheet is. This only stops a
    crash from throwing away pages that were already fetched."""

    def __init__(self, path: str):
        self.path = path
        self._rows: List[Dict[str, Any]] = self._load()

    def _load(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.path):
            return []
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return data if isinstance(data, list) else []
        except (ValueError, OSError):
            return []

    def reviews(self) -> List[Dict[str, Any]]:
        return list(self._rows)

    def extend(self, reviews: List[Review]) -> None:
        self._rows.extend(asdict(review) for review in reviews)
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(self._rows, handle, ensure_ascii=False, indent=2)

    def clear(self) -> None:
        self._rows = []
        try:
            os.remove(self.path)
        except OSError:
            pass
```

- [ ] **Step 4: Run the checkpoint tests**

Run: `./.venv/bin/pytest tests/test_checkpoint.py -v`
Expected: 5 passed

- [ ] **Step 5: Write the failing test for the adapter callback**

Append to `tests/test_trustpilot_adapter.py`:

```python
def test_on_view_callback_fires_per_view_for_checkpointing():
    seen = []
    adapter = adapter_with(RecordingFetcher())
    adapter.collect("www.trademax.se",
                    on_view=lambda label, reviews: seen.append((label, len(reviews))))
    assert seen
    assert all(isinstance(label, str) for label, _ in seen)
```

- [ ] **Step 6: Run it to make sure it fails**

Run: `./.venv/bin/pytest tests/test_trustpilot_adapter.py::test_on_view_callback_fires_per_view_for_checkpointing -v`
Expected: FAIL — `TypeError: collect() got an unexpected keyword argument 'on_view'`

- [ ] **Step 7: Add the callback to the adapter**

In `scraper/platforms/trustpilot/adapter.py`, change the signature:

```python
    def collect(self, domain: str, target: Optional[int] = None,
                on_view: Optional[Callable[[str, List[Review]], None]] = None) -> CollectResult:
```

Inside the `for plan in planned:` loop, collect that view's new rows and fire the callback. Replace the per-review append block so the loop body reads:

```python
        for plan in planned:
            label = plan.view.label()
            view_reviews: List[Review] = []
            for page in plan.pages:
                ...  # unchanged fetch/classify/break logic
                for review in parser.parse_reviews(
                        props, domain=domain, source_view=label,
                        fetcher=fetcher_name, fetched_at=self.now()):
                    if review.review_id in seen:
                        counts["duplicates_skipped"] += 1
                        continue
                    seen.add(review.review_id)
                    view_reviews.append(review)

            reviews.extend(view_reviews)
            if on_view is not None:
                on_view(label, view_reviews)

            if target is not None and len(reviews) >= target:
                break
```

- [ ] **Step 8: Run the adapter tests**

Run: `./.venv/bin/pytest tests/test_trustpilot_adapter.py -v`
Expected: 7 passed

- [ ] **Step 9: Write the failing CLI test**

`tests/test_run.py`:

```python
from __future__ import annotations

import json

from scraper import run
from scraper.models import Review


def test_dry_run_writes_json_and_touches_no_sheet(tmp_path, monkeypatch, capsys):
    output = tmp_path / "out.json"

    class FakeAdapter:
        platform = "Trustpilot"

        def __init__(self, *args, **kwargs):
            pass

        def collect(self, domain, target=None, on_view=None):
            reviews = [Review(review_id="trustpilot:a", platform="Trustpilot",
                              company_domain=domain, rating=5)]
            if on_view:
                on_view("languages=all", reviews)
            return run.CollectResult(reviews=reviews,
                                     stats={"pages_fetched": 3, "unique_reviews": 1})

    monkeypatch.setattr(run, "TrustpilotAdapter", FakeAdapter)
    monkeypatch.setattr(run, "build_pool", lambda **kwargs: _NullPool())

    exit_code = run.main(["--company", "Trademax",
                          "--trustpilot", "www.trademax.se",
                          "--dry-run", "--output", str(output),
                          "--checkpoint-dir", str(tmp_path)])

    assert exit_code == 0
    rows = json.loads(output.read_text(encoding="utf-8"))
    assert rows[0]["review_id"] == "trustpilot:a"
    assert "pages_fetched" in capsys.readouterr().out


class _NullPool:
    def stats(self):
        return {}

    def close(self):
        return None


def test_requires_at_least_one_platform_target(capsys):
    assert run.main(["--company", "Trademax"]) == 2
    assert "at least one of" in capsys.readouterr().err.lower()
```

- [ ] **Step 10: Run it to make sure it fails**

Run: `./.venv/bin/pytest tests/test_run.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scraper.run'`

- [ ] **Step 11: Implement the CLI**

`scraper/run.py`:

```python
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from typing import Dict, List, Optional

from dotenv import load_dotenv

from scraper.checkpoint import Checkpoint
from scraper.fetchers.chrome import ChromeCDPFetcher
from scraper.fetchers.jina import JinaFetcher
from scraper.fetchers.pool import FetcherPool
from scraper.models import Review, sheet_columns
from scraper.platforms.trustpilot.adapter import CollectResult, TrustpilotAdapter
from scraper.sheets import (append_reviews, existing_review_ids,
                            get_or_create_tab, open_spreadsheet)


def build_pool(headless: bool = False) -> FetcherPool:
    """Chrome first (unlimited, free), Jina as the independent fallback."""
    return FetcherPool([ChromeCDPFetcher(headless=headless), JinaFetcher()])


def _safe_slug(text: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in text)[:60]


def main(argv: Optional[List[str]] = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(
        prog="scraper.run",
        description="Collect rated reviews into one Google Sheet tab per company.")
    parser.add_argument("--company", required=True, help="Sheet tab name")
    parser.add_argument("--trustpilot", help="Trustpilot domain, e.g. www.trademax.se")
    parser.add_argument("--target", type=int, default=3000,
                        help="Stop planning once this many reviews are reachable")
    parser.add_argument("--dry-run", action="store_true",
                        help="Write JSON instead of touching the sheet")
    parser.add_argument("--output", default="out.json", help="--dry-run output path")
    parser.add_argument("--checkpoint-dir", default="checkpoints")
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args(argv)

    if not args.trustpilot:
        parser.error("at least one of --trustpilot must be given")
        return 2

    checkpoint = Checkpoint(os.path.join(
        args.checkpoint_dir, "{0}.json".format(_safe_slug(args.company))))

    pool = build_pool(headless=args.headless)
    reviews: List[Review] = []
    stats: Dict[str, int] = {}

    try:
        adapter = TrustpilotAdapter(pool)
        result: CollectResult = adapter.collect(
            args.trustpilot, target=args.target,
            on_view=lambda label, rows: checkpoint.extend(rows))
        reviews.extend(result.reviews)
        stats.update(result.stats)
    finally:
        pool.close()

    if args.dry_run:
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump([asdict(review) for review in reviews], handle,
                      ensure_ascii=False, indent=2)
        written = len(reviews)
        skipped = 0
    else:
        columns = sheet_columns(
            include_consumer_name=os.getenv("INCLUDE_CONSUMER_NAME", "").lower() == "true")
        spreadsheet = open_spreadsheet(os.environ["GOOGLE_SHEET_URL"],
                                       os.environ["GOOGLE_CREDS_FILE"])
        worksheet = get_or_create_tab(spreadsheet, args.company, columns)
        already = existing_review_ids(worksheet)
        fresh = [review for review in reviews if review.review_id not in already]
        skipped = len(reviews) - len(fresh)
        written = append_reviews(worksheet, fresh, columns)
        checkpoint.clear()

    print("\n=== run summary: {0} ===".format(args.company))
    for key in sorted(stats):
        print("  {0:<24} {1}".format(key, stats[key]))
    print("  {0:<24} {1}".format("rows written", written))
    print("  {0:<24} {1}".format("already in sheet", skipped))
    if stats.get("gated"):
        print("  note: {0} view(s) hit the page-10 login gate — expected, not an error"
              .format(stats["gated"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 12: Run the tests**

Run: `./.venv/bin/pytest tests/test_run.py -v`
Expected: 2 passed

- [ ] **Step 13: Real dry run against Trustpilot**

Run: `./.venv/bin/python -m scraper.run --company Trademax --trustpilot www.trademax.se --target 300 --dry-run --output /tmp/trademax_dry.json`

Expected: a Chrome window opens, pages fetch at ~4s each, and the summary prints `unique_reviews` of roughly 300. Confirm the JSON has ratings and reply dates:

```bash
./.venv/bin/python -c "
import json; rows=json.load(open('/tmp/trademax_dry.json'))
print('rows', len(rows))
print('with rating', sum(1 for r in rows if r['rating']))
print('with reply', sum(1 for r in rows if r['support_reply']))
print('unique ids', len({r['review_id'] for r in rows}))
"
```

- [ ] **Step 14: Commit**

```bash
git add scraper/checkpoint.py scraper/run.py scraper/platforms/trustpilot/adapter.py tests/test_checkpoint.py tests/test_run.py tests/test_trustpilot_adapter.py
git commit -m "Add checkpoint and CLI for end-to-end Trustpilot runs"
```

---

### Task 11: Google relative-date bracketing

Google gives no absolute dates. Sorted newest-first, a relative label plus its neighbours bounds a review to about a month.

**Files:**
- Create: `scraper/platforms/google/__init__.py`, `scraper/platforms/google/dates.py`, `tests/test_google_dates.py`

**Interfaces:**
- Consumes: nothing
- Produces: `relative_to_days(label: str) -> Optional[int]`; `bracket_dates(labels: List[str], run_date: date) -> List[str]` returning ISO `YYYY-MM-DD` strings, `""` where unparseable

- [ ] **Step 1: Write the failing test**

`tests/test_google_dates.py`:

```python
from __future__ import annotations

from datetime import date

from scraper.platforms.google.dates import bracket_dates, relative_to_days


def test_singular_and_plural_units():
    assert relative_to_days("a month ago") == 30
    assert relative_to_days("2 months ago") == 60
    assert relative_to_days("a week ago") == 7
    assert relative_to_days("3 weeks ago") == 21
    assert relative_to_days("a year ago") == 365
    assert relative_to_days("2 years ago") == 730
    assert relative_to_days("a day ago") == 1
    assert relative_to_days("5 days ago") == 5


def test_edited_prefix_is_ignored():
    assert relative_to_days("Edited a year ago") == 365


def test_sub_day_labels_collapse_to_today():
    assert relative_to_days("2 hours ago") == 0
    assert relative_to_days("a moment ago") == 0


def test_unparseable_label_returns_none():
    assert relative_to_days("last winter") is None
    assert relative_to_days("") is None


def test_bracket_dates_converts_relative_to_absolute():
    dates = bracket_dates(["a month ago", "2 months ago"], date(2026, 9, 4))
    assert dates == ["2026-08-05", "2026-07-06"]


def test_bracket_dates_never_goes_forward_in_a_newest_first_list():
    """Coarse labels can round the wrong way; order is the stronger signal."""
    dates = bracket_dates(["2 months ago", "a month ago", "3 months ago"],
                          date(2026, 9, 4))
    assert dates[0] >= dates[1] >= dates[2]


def test_unparseable_entries_render_empty_without_breaking_neighbours():
    dates = bracket_dates(["a month ago", "sometime", "2 months ago"],
                          date(2026, 9, 4))
    assert dates[1] == ""
    assert dates[0] == "2026-08-05"
    assert dates[2] == "2026-07-06"


def test_empty_input():
    assert bracket_dates([], date(2026, 9, 4)) == []
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `./.venv/bin/pytest tests/test_google_dates.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scraper.platforms.google'`

- [ ] **Step 3: Implement**

`scraper/platforms/google/__init__.py`: empty file.

`scraper/platforms/google/dates.py`:

```python
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
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `./.venv/bin/pytest tests/test_google_dates.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add scraper/platforms/google tests/test_google_dates.py
git commit -m "Convert Google relative review ages to bracketed dates"
```

---

### Task 12: Google DOM extraction helpers

Each review contributes several `[data-review-id]` nodes — the probe saw 1,320 nodes for 348 reviews. Dedup at extraction, or every count is inflated ~3.8x.

**Files:**
- Create: `scraper/platforms/google/extract.py`, `tests/test_google_extract.py`

**Interfaces:**
- Consumes: `Review`, `bracket_dates`
- Produces: `EXTRACT_JS: str`; `parse_rating(aria_label: str) -> Optional[int]`; `dedupe_nodes(nodes: List[dict]) -> List[dict]`; `build_reviews(nodes, *, company_name, place, run_date, fetched_at, fetcher) -> List[Review]`

- [ ] **Step 1: Write the failing test**

`tests/test_google_extract.py`:

```python
from __future__ import annotations

from datetime import date

from scraper.platforms.google.extract import (build_reviews, dedupe_nodes,
                                              parse_rating)


def node(review_id, rating="5 stars", text="good", when="a month ago", reply=""):
    return {"id": review_id, "rating": rating, "text": text, "when": when,
            "reply": reply}


def test_parse_rating_from_aria_label():
    assert parse_rating("5 stars") == 5
    assert parse_rating("1 star") == 1
    assert parse_rating("4,0 stjärnor") == 4


def test_parse_rating_of_junk_is_none():
    assert parse_rating("") is None
    assert parse_rating("no number here") is None


def test_dedupe_collapses_repeated_nodes_for_one_review():
    nodes = [node("aaa"), node("aaa"), node("bbb")]
    assert [n["id"] for n in dedupe_nodes(nodes)] == ["aaa", "bbb"]


def test_dedupe_prefers_the_node_carrying_review_text():
    nodes = [node("aaa", text=""), node("aaa", text="the real body")]
    assert dedupe_nodes(nodes)[0]["text"] == "the real body"


def test_build_reviews_maps_to_the_shared_schema():
    reviews = build_reviews([node("aaa", rating="2 stars", when="2 months ago")],
                            company_name="Trademax", place="Trademax Stockholm",
                            run_date=date(2026, 9, 4), fetched_at="2026-09-04T10:00:00Z",
                            fetcher="chrome")
    review = reviews[0]
    assert review.review_id == "google:aaa"
    assert review.platform == "Google"
    assert review.rating == 2
    assert review.review_date == "2026-07-06"
    assert review.date_precision == "relative"
    assert review.location == "Trademax Stockholm"
    assert review.company_name == "Trademax"
    assert review.source_view == "place=Trademax Stockholm"
    assert review.fetcher == "chrome"


def test_rating_only_reviews_are_kept():
    """~40% of Google reviews carry a star and no text. They still count."""
    reviews = build_reviews([node("aaa", text="")], company_name="T", place="P",
                            run_date=date(2026, 9, 4), fetched_at="t", fetcher="chrome")
    assert len(reviews) == 1
    assert reviews[0].raw_text == ""
    assert reviews[0].rating == 5


def test_owner_reply_is_carried_over():
    reviews = build_reviews([node("aaa", reply="Thanks for the feedback")],
                            company_name="T", place="P", run_date=date(2026, 9, 4),
                            fetched_at="t", fetcher="chrome")
    assert reviews[0].support_reply == "Thanks for the feedback"


def test_nodes_without_an_id_are_dropped():
    assert build_reviews([node("")], company_name="T", place="P",
                         run_date=date(2026, 9, 4), fetched_at="t",
                         fetcher="chrome") == []
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `./.venv/bin/pytest tests/test_google_extract.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scraper.platforms.google.extract'`

- [ ] **Step 3: Implement**

`scraper/platforms/google/extract.py`:

```python
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
    const star = node.querySelector('[role="img"][aria-label*="star"],[role="img"][aria-label*="stjär"]');
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
    """One review contributes several DOM nodes. Keep one per id, preferring
    the node that actually carries the review body."""
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
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `./.venv/bin/pytest tests/test_google_extract.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add scraper/platforms/google/extract.py tests/test_google_extract.py
git commit -m "Extract and dedupe Google Maps review nodes"
```

---

### Task 13: Google Maps adapter

Google needs an interactive page, not a fetch-and-parse. It drives the Chrome fetcher's page directly.

**Files:**
- Create: `scraper/platforms/google/adapter.py`, `tests/test_google_adapter.py`
- Modify: `scraper/fetchers/chrome.py` — expose `page()`

**Interfaces:**
- Consumes: `ChromeCDPFetcher`, `EXTRACT_JS`, `SCROLL_JS`, `build_reviews`, `CollectResult`
- Produces: `ChromeCDPFetcher.page()`; `GoogleMapsAdapter(chrome, now=..., today=..., sleep=time.sleep)` with `.platform == "Google"` and `.collect(query: str, company_name: str, max_scrolls: int = 80) -> CollectResult`

- [ ] **Step 1: Expose the page on the Chrome fetcher**

In `scraper/fetchers/chrome.py`, add a public accessor:

```python
    def page(self):
        """The live Playwright page. Adapters that must interact with a page
        (scroll, click) use this instead of fetch()."""
        return self._ensure_page()
```

- [ ] **Step 2: Write the failing test**

`tests/test_google_adapter.py`:

```python
from __future__ import annotations

from datetime import date

import pytest

from scraper.platforms.google.adapter import GoogleMapsAdapter


class FakeLocator:
    def __init__(self, page, name):
        self.page = page
        self.name = name

    @property
    def first(self):
        return self

    def click(self, timeout=None):
        self.page.clicks.append(self.name)


class FakePage:
    """Grows the node list on each scroll, then plateaus — the real loop's
    stop condition is 'unique count stopped growing'."""

    def __init__(self, batches):
        self.batches = list(batches)
        self.nodes = []
        self.clicks = []
        self.url = "https://www.google.com/maps/place/Trademax"

    def set_default_timeout(self, ms):
        return None

    def goto(self, url, wait_until=None):
        self.url = url

    def locator(self, selector):
        return FakeLocator(self, selector)

    def evaluate(self, js, *args):
        if "scrollTop" in js:
            if self.batches:
                self.nodes.extend(self.batches.pop(0))
            return 1
        return list(self.nodes)


def node(review_id):
    return {"id": review_id, "rating": "5 stars", "text": "ok",
            "when": "a month ago", "reply": ""}


class FakeChrome:
    name = "chrome"

    def __init__(self, page):
        self._page = page

    def page(self):
        return self._page


def adapter_for(page):
    return GoogleMapsAdapter(FakeChrome(page), now=lambda: "2026-09-04T10:00:00Z",
                             today=lambda: date(2026, 9, 4), sleep=lambda s: None)


def test_scrolls_until_the_unique_count_plateaus():
    page = FakePage([[node("a"), node("b")], [node("c")], [], [], [], []])
    result = adapter_for(page).collect("Trademax Stockholm", "Trademax")

    assert sorted(r.review_id for r in result.reviews) == ["google:a", "google:b", "google:c"]
    assert result.stats["unique_reviews"] == 3


def test_duplicate_dom_nodes_do_not_inflate_the_count():
    page = FakePage([[node("a"), node("a"), node("a")], [], [], [], []])
    result = adapter_for(page).collect("Trademax Stockholm", "Trademax")
    assert result.stats["unique_reviews"] == 1
    assert result.stats["raw_nodes"] == 3


def test_rows_are_tagged_with_the_place_and_company():
    page = FakePage([[node("a")], [], [], [], []])
    review = adapter_for(page).collect("Trademax Stockholm", "Trademax").reviews[0]
    assert review.location == "Trademax Stockholm"
    assert review.company_name == "Trademax"
    assert review.platform == "Google"
    assert review.date_precision == "relative"


def test_missing_scroll_container_ends_cleanly():
    class NoPane(FakePage):
        def evaluate(self, js, *args):
            if "scrollTop" in js:
                return -1
            return []

    result = adapter_for(NoPane([])).collect("q", "c")
    assert result.reviews == []


@pytest.mark.live
def test_live_google_collection():
    """Real Chrome against real Maps. Run explicitly: pytest -m live"""
    from scraper.fetchers.chrome import ChromeCDPFetcher

    chrome = ChromeCDPFetcher()
    try:
        result = GoogleMapsAdapter(chrome).collect("Trademax Möbler Stockholm",
                                                   "Trademax")
        assert result.stats["unique_reviews"] > 50
        assert all(r.rating for r in result.reviews[:20])
    finally:
        chrome.close()
```

- [ ] **Step 3: Run it to make sure it fails**

Run: `./.venv/bin/pytest tests/test_google_adapter.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scraper.platforms.google.adapter'`

- [ ] **Step 4: Implement**

`scraper/platforms/google/adapter.py`:

```python
from __future__ import annotations

import time
from collections import Counter
from datetime import date, datetime
from typing import Any, Callable, List

from scraper.platforms.google.extract import EXTRACT_JS, SCROLL_JS, build_reviews
from scraper.platforms.trustpilot.adapter import CollectResult

MAPS_SEARCH = "https://www.google.com/maps/search/{query}"
PLATEAU_ROUNDS = 5

REVIEW_TAB_SELECTORS = ('button[aria-label*="Reviews"]', 'button[aria-label*="review"]',
                        'button[aria-label*="ecension"]')
SORT_SELECTORS = ('button[aria-label*="Sort"]', 'button[data-value="Sort"]')


def _utc_now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


class GoogleMapsAdapter:
    """No filter slicing: Google has no login gate, so one place yields all of
    its reviews. The plan is simply 'scroll until it stops growing'."""

    platform = "Google"

    def __init__(self, chrome: Any, now: Callable[[], str] = _utc_now,
                 today: Callable[[], date] = date.today,
                 sleep: Callable[[float], Any] = time.sleep):
        self.chrome = chrome
        self.now = now
        self.today = today
        self._sleep = sleep

    def _click_first(self, page, selectors) -> bool:
        for selector in selectors:
            try:
                page.locator(selector).first.click(timeout=8000)
                return True
            except Exception:
                continue
        return False

    def collect(self, query: str, company_name: str,
                max_scrolls: int = 80) -> CollectResult:
        counts: Counter = Counter()
        page = self.chrome.page()

        page.goto(MAPS_SEARCH.format(query=query.replace(" ", "+")),
                  wait_until="domcontentloaded")
        self._sleep(5)

        if "/maps/place/" not in getattr(page, "url", ""):
            try:
                page.locator("a.hfpxzc").first.click(timeout=15000)
                self._sleep(5)
            except Exception:
                counts["place_click_failed"] += 1

        if self._click_first(page, REVIEW_TAB_SELECTORS):
            self._sleep(3)
        else:
            counts["reviews_tab_not_found"] += 1

        # Sorting by Newest makes date bracketing meaningful. The control's
        # accessible name is locale-dependent, so treat it as optional.
        if self._click_first(page, SORT_SELECTORS):
            self._sleep(1.5)
            try:
                page.get_by_role("menuitemradio").nth(1).click(timeout=6000)
                counts["sorted_newest"] = 1
                self._sleep(3)
            except Exception:
                counts["sort_option_not_found"] += 1
        else:
            counts["sort_control_not_found"] += 1

        nodes: List[dict] = []
        seen_ids = set()
        plateau = 0

        for _ in range(max_scrolls):
            if page.evaluate(SCROLL_JS) == -1:
                counts["no_scroll_container"] += 1
                break
            self._sleep(1.4)
            nodes = page.evaluate(EXTRACT_JS)
            unique_now = set(n.get("id") for n in nodes if n.get("id"))
            if unique_now == seen_ids:
                plateau += 1
                if plateau >= PLATEAU_ROUNDS:
                    break
            else:
                plateau = 0
                seen_ids = unique_now
            counts["scrolls"] += 1

        reviews = build_reviews(nodes, company_name=company_name, place=query,
                                run_date=self.today(), fetched_at=self.now(),
                                fetcher=getattr(self.chrome, "name", "chrome"))
        counts["raw_nodes"] = len(nodes)
        counts["unique_reviews"] = len(reviews)
        return CollectResult(reviews=reviews, stats=dict(counts))
```

- [ ] **Step 5: Run the tests and make sure they pass**

Run: `./.venv/bin/pytest tests/test_google_adapter.py -v`
Expected: 4 passed, 1 deselected

- [ ] **Step 6: Run the live test once**

Run: `./.venv/bin/pytest tests/test_google_adapter.py -m live -v`
Expected: PASS with more than 50 reviews. If it collects 0, the Maps DOM class names have changed — re-derive the selectors in `extract.py` by opening a place page and inspecting a review card, then re-run.

- [ ] **Step 7: Commit**

```bash
git add scraper/platforms/google/adapter.py scraper/fetchers/chrome.py tests/test_google_adapter.py
git commit -m "Add Google Maps adapter driving a real Chrome page"
```

---

### Task 14: Wire Google into the CLI + runbook

**Files:**
- Modify: `scraper/run.py`, `tests/test_run.py`
- Create: `README_SCRAPER.md`

**Interfaces:**
- Consumes: `GoogleMapsAdapter`
- Produces: CLI flag `--google "<maps search query>"`; both platforms writing into one tab

- [ ] **Step 1: Write the failing test**

Append to `tests/test_run.py`:

```python
def test_both_platforms_write_into_one_tab(tmp_path, monkeypatch, capsys):
    output = tmp_path / "out.json"

    class FakeTrustpilot:
        def __init__(self, *a, **k):
            pass

        def collect(self, domain, target=None, on_view=None):
            return run.CollectResult(
                reviews=[Review(review_id="trustpilot:a", platform="Trustpilot")],
                stats={"unique_reviews": 1})

    class FakeGoogle:
        def __init__(self, *a, **k):
            pass

        def collect(self, query, company_name, max_scrolls=80):
            return run.CollectResult(
                reviews=[Review(review_id="google:b", platform="Google")],
                stats={"unique_reviews": 1})

    monkeypatch.setattr(run, "TrustpilotAdapter", FakeTrustpilot)
    monkeypatch.setattr(run, "GoogleMapsAdapter", FakeGoogle)
    monkeypatch.setattr(run, "build_pool", lambda **kwargs: _NullPool())
    monkeypatch.setattr(run, "ChromeCDPFetcher", lambda **kwargs: object())

    exit_code = run.main(["--company", "Trademax",
                          "--trustpilot", "www.trademax.se",
                          "--google", "Trademax Stockholm",
                          "--dry-run", "--output", str(output),
                          "--checkpoint-dir", str(tmp_path)])

    assert exit_code == 0
    platforms = {row["platform"] for row in json.loads(output.read_text())}
    assert platforms == {"Trustpilot", "Google"}


def test_google_only_run_needs_no_trustpilot_domain(tmp_path, monkeypatch):
    class FakeGoogle:
        def __init__(self, *a, **k):
            pass

        def collect(self, query, company_name, max_scrolls=80):
            return run.CollectResult(
                reviews=[Review(review_id="google:b", platform="Google")],
                stats={})

    monkeypatch.setattr(run, "GoogleMapsAdapter", FakeGoogle)
    monkeypatch.setattr(run, "build_pool", lambda **kwargs: _NullPool())
    monkeypatch.setattr(run, "ChromeCDPFetcher", lambda **kwargs: object())

    assert run.main(["--company", "T", "--google", "T Stockholm", "--dry-run",
                     "--output", str(tmp_path / "o.json"),
                     "--checkpoint-dir", str(tmp_path)]) == 0
```

Also update the existing guard test:

```python
def test_requires_at_least_one_platform_target(capsys):
    assert run.main(["--company", "Trademax"]) == 2
    assert "at least one of --trustpilot" in capsys.readouterr().err.lower()
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `./.venv/bin/pytest tests/test_run.py -v`
Expected: FAIL — `AttributeError: module 'scraper.run' has no attribute 'GoogleMapsAdapter'`

- [ ] **Step 3: Implement**

In `scraper/run.py`, add the import:

```python
from scraper.platforms.google.adapter import GoogleMapsAdapter
```

Add the argument:

```python
    parser.add_argument("--google", help='Google Maps search query, e.g. "Trademax Stockholm"')
```

Replace the platform guard:

```python
    if not args.trustpilot and not args.google:
        parser.error("at least one of --trustpilot or --google must be given")
        return 2
```

Replace the collection block:

```python
    pool = None
    chrome = None
    reviews: List[Review] = []
    stats: Dict[str, int] = {}

    try:
        if args.trustpilot:
            pool = build_pool(headless=args.headless)
            result = TrustpilotAdapter(pool).collect(
                args.trustpilot, target=args.target,
                on_view=lambda label, rows: checkpoint.extend(rows))
            reviews.extend(result.reviews)
            stats.update(result.stats)

        if args.google:
            chrome = ChromeCDPFetcher(headless=args.headless)
            google_result = GoogleMapsAdapter(chrome).collect(args.google, args.company)
            reviews.extend(google_result.reviews)
            checkpoint.extend(google_result.reviews)
            for key, value in google_result.stats.items():
                stats["google_{0}".format(key)] = value
    finally:
        if pool is not None:
            pool.close()
        if chrome is not None and hasattr(chrome, "close"):
            chrome.close()
```

- [ ] **Step 4: Run the tests**

Run: `./.venv/bin/pytest tests/test_run.py -v`
Expected: 4 passed

- [ ] **Step 5: Run the whole suite**

Run: `./.venv/bin/pytest -q`
Expected: all green, live tests deselected

- [ ] **Step 6: Real end-to-end run into the sheet**

```bash
./.venv/bin/python -m scraper.run --company "Trademax" --trustpilot www.trademax.se --google "Trademax Möbler Stockholm" --target 1000
```

Expected: the summary prints, and the `Trademax` tab holds both platforms. Verify dedup by running the **exact same command again** — the second run must report `already in sheet` roughly equal to what it collected, and `rows written` near zero.

- [ ] **Step 7: Write the runbook**

`README_SCRAPER.md`:

```markdown
# Review collection

Collects rated reviews from Trustpilot and Google Maps into one Google Sheet tab
per company.

## Setup

    python3 -m venv .venv
    ./.venv/bin/pip install -r requirements.txt

`.env` (never committed):

    GOOGLE_SHEET_URL=...
    GOOGLE_CREDS_FILE=scrapper.json
    JINA_API_KEY=...              # optional, keyless works
    INCLUDE_CONSUMER_NAME=false   # reviewer names are personal data; off by default

The service account in `scrapper.json` must have Editor access on the sheet.

## Run

    ./.venv/bin/python -m scraper.run \
        --company "Trademax" \
        --trustpilot www.trademax.se \
        --google "Trademax Möbler Stockholm" \
        --target 3000

Add `--dry-run --output out.json` to inspect results without touching the sheet.
Re-running the same command is safe: rows already in the tab are skipped.

## Things that look like bugs but are not

- **"gated" in the summary.** Trustpilot redirects to login past page 10 of any
  filter view. That is a product gate, not a block: no proxy, IP or account
  changes it. The planner works around it by slicing filters, so each view
  contributes up to 200 reviews.
- **Google dates are approximate.** Google publishes ages ("8 months ago"), not
  dates. Those rows carry `date_precision=relative` and are accurate to about a
  month. Never compare them against Trustpilot dates without checking that column.
- **A Chrome window opens.** Required. Attaching to real Chrome is what passes
  the WAF; a Playwright- or Selenium-launched browser gets walled.
- **First page of a run gets challenged.** The profile at `~/.trustpilot-chrome`
  keeps the clearance cookie afterwards.

## Never do this

- Do not put Trustpilot or Google credentials in `.env`. Reviews need no login,
  automating a logged-in session risks a ban on a real person's account, and it
  does not lift the page-10 gate.
- Do not `git add .` — `.env` and `scrapper.json` are live secrets in the tree.
```

- [ ] **Step 8: Commit**

```bash
git add scraper/run.py tests/test_run.py README_SCRAPER.md
git commit -m "Collect both platforms in one run and document the runbook"
```

---

## Self-Review

**Spec coverage.** Every section maps to a task: schema and `consumer_name` default (1), Trustpilot parser (2), outcome classification incl. `GATED` (3), view planner with the page-10 cap and invalid-split guard (4), Jina fetcher (5), Chrome CDP fetcher (6), failover and circuit breaker (7), Trustpilot adapter with dedup (8), sheet-as-store with dedup and resume (9), checkpoint plus CLI and run summary (10), Google relative dates (11), Google node dedup and schema mapping (12), Google adapter (13), both platforms in one tab plus runbook (14). `.gitignore` hardening is Task 1 Step 1, as the spec requires.

**Deferred deliberately.** LLM enrichment columns are written blank, per the spec's non-goals. Google multi-location is one place per run — the spec lists the multiplier as unmeasured, so committing to it here would be inventing a requirement.

**Type consistency.** `CollectResult` is defined once in `trustpilot/adapter.py` and imported by the Google adapter and the CLI. `View.label()` is the single key for both the planner's plans and the adapter's `page_one_cache`. `Fetcher.name` flows into `Review.fetcher`. `sheet_columns()` is the only source of column order, used by both the sheet writer and `Review.to_row`.

**Known rough edge, flagged not hidden.** `TrustpilotAdapter._last_fetcher()` infers which fetcher served a cached page-1 from pool stats rather than being told. It is only used for row provenance on cached pages. If provenance accuracy matters more later, have `probe` cache the `FetchResult` instead of the raw html.
