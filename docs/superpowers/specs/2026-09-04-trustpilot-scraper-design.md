# Trustpilot Review Collection — Design

Date: 2026-09-04
Status: approved, pending implementation plan

## 1. Goal

Collect customer-review data for competitor sets, reliably and repeatedly, into a
Google Sheet with one tab per company. Target: **~3,000 reviews per run, well
inside an hour**, on free infrastructure.

The consumer is a customer-intelligence workflow: an agency compares an SMB
client against many competitors, so breadth across companies matters more than
exhaustive depth on any one of them.

### Non-goals (this spec)

- LLM enrichment of `journey_stage` / `review_type` / `customer_order_status`.
  Columns are reserved and left blank; enrichment is a separate spec.
- Platforms other than Trustpilot. The fetcher/parser seam exists so Reddit,
  Google Reviews, G2 etc. are additive, but none are built here.
- Any paid unblocker, proxy, or API subscription. Free path only. Paid options
  are pitched to the company only after this works.
- Scheduling/automation. Runs are launched by hand, one company at a time.

## 2. Verified findings

Established by probe on 2026-09-04 against `www.trademax.se`, not assumed:

1. **Trustpilot still ships `__NEXT_DATA__`.** One page load yields 20 fully
   structured reviews: id, rating, title, full text, `dates.publishedDate`,
   `dates.experiencedDate`, `reply.message` **and** `reply.publishedDate`,
   `labels.verification`, language, location, plus `filters.pagination`
   (`totalCount`, `totalPages`) and `businessUnit`.
2. **Reply dates arrive with the listing.** The existing script's extra fetch per
   replied review is unnecessary — ~90 requests collapse to ~10 for the same data.
3. **Pagination past page 10 is login-gated.** Page 11 returns a server-side
   `__N_REDIRECT` to `/users/connect`. This is a product gate, not bot defense:
   proxies, unblockers, residential IPs and retries all hit it identically.
   Anonymous ceiling is **200 reviews per filter view**.
4. **The cap is per view, not per company.** Each filter combination carries its
   own `totalCount` and its own 200-review ceiling. Measured on trademax.se:

   | View | totalCount | Anon-reachable |
   |---|---|---|
   | baseline (en) | 797 | 200 |
   | `stars=1` | 292 | 200 |
   | `stars=2` | 61 | 61 (all) |
   | `stars=3` | 66 | 66 (all) |
   | `stars=4` | 183 | 183 (all) |
   | `stars=5` | 678 | 200 |
   | `languages=all` | 66,724 | 200 |

   Star-slicing alone lifts one company from 200 to ~710 English reviews.
5. **Views overlap.** Nine view-page-1s returned 180 rows containing 118 unique
   ids. Dedup by `review_id` is mandatory.
6. **Real Chrome over CDP passes the WAF.** Cold first hit ate a challenge; the
   following 10/10 pages were clean at ~4s/page, with the clearance cookie
   persisted in the profile. Playwright- and Selenium-launched browsers were
   walled — attaching to a real Chrome is the difference.
7. **`r.jina.ai` with `X-Return-Format: html` returns the same `__NEXT_DATA__`,
   keyless.** Intermittent: first attempt hit the WAF interstitial, retry
   succeeded. Usable as an independent fallback path.

## 3. Decisions and rationale

| Decision | Rationale |
|---|---|
| No Trustpilot account credentials, ever | Login is not required to read reviews. Automating an authenticated session converts a per-IP, expiring block into an account ban on a real person, and moves a contested-but-common practice into unambiguous ToS breach. Rotate IPs, not identities. |
| Google Sheet is the store; no SQLite | Keeps the system one artifact. Dedup and resume both work by reading the tab's existing `review_id` column. |
| `consumer_name` collected but **off by default** | Reviewer names are personal data under GDPR and most Trustpilot reviewers are EU residents. Analysis doesn't need it; easier to add later than to unpick from client sheets. Config flag. |
| Planner is pure logic, no network | The cap/overlap/nesting rules are the likeliest source of subtle bugs. Testable offline with fabricated counts. |
| Fetcher returns HTML, not parsed data | One parser for all fetchers; swapping fetchers cannot change parsed output. |

## 4. Architecture

```
target list (domains)
   ↓
view planner      pure logic: expands a domain into filter views, plans pages
   ↓
fetcher           [ chrome-cdp | jina ]   swappable, failover
   ↓
parser            [ trustpilot __NEXT_DATA__ ]   swappable per platform
   ↓
dedup by (platform, review_id)
   ↓
normalized rows
   ↓
sheet writer      tab per company, batched appends
```

Each unit is independently testable: the planner with fake counts, the parser
with a saved HTML fixture, the sheet writer against a scratch tab, the fetchers
by live smoke test.

### 4.1 View planner

A **view** is one filter combination over a domain. Recursive split-until-it-fits:

```
plan(view):
  probe page 1 -> totalCount
  if totalCount == 0    -> drop
  if totalCount <= 200  -> harvest fully: ceil(totalCount / 20) pages
  else                  -> split on next unused dimension, recurse
```

Split dimensions in priority order:

1. `stars` 1–5 — disjoint, no wasted overlap. Always first.
2. `languages` — disjoint. The large lever for non-English companies.
3. `date` buckets (last30days / 3mo / 6mo / 12mo) — these **nest** rather than
   partition, so later resort.
4. `sort=recency` vs default — same filter, different 200-window, partial
   overlap. Last resort.

Hard rule: never plan a page number above 10 for any view.

The planner is target-aware. The caller passes a review target (e.g. 3,000) or
"everything reachable"; views are ordered by expected-new-per-request and
planning stops once the target is met.

Expected yield, trademax.se:

| Plan | Views | Requests | Unique reviews |
|---|---|---|---|
| baseline only (current build) | 1 | ~10 + 182 reply fetches | 182 |
| + star split | 6 | ~40 | ~710 |
| + languages x stars | ~20 | ~130 | ~2,500–3,500 |

Stated limit: for a company with tens of thousands of reviews, anonymous full
coverage is impossible. The result is a **stratified sample deliberately spread
across star ratings**, which is more useful for competitor sentiment comparison
than 200 consecutive recent reviews. This limitation is reported to the caller,
not hidden.

### 4.2 Fetchers

Interface: `fetch(url) -> html`.

**ChromeCDPFetcher** (primary). Launches or reuses a real Chrome on a debug port
with a persistent profile (`~/.trustpilot-chrome`), attaches over CDP, navigates,
returns `document.documentElement.outerHTML`. Jittered 3.5–7s between loads.
Profile persistence makes the WAF challenge a one-time cost.

**JinaFetcher** (fallback). `https://r.jina.ai/<url>` with `X-Return-Format: html`.
Works keyless; `JINA_API_KEY` raises the rate limit. Independent IP path, so it
fails for different reasons than Chrome.

### 4.3 Response classification

Every fetch is classified before use:

| Outcome | Signal | Action |
|---|---|---|
| `OK` | `__NEXT_DATA__` present, `reviews.length > 0` | parse, continue |
| `TRANSIENT_BLOCK` | title `Verifying Connection`, or body < ~5KB | retry with backoff, then failover to the other fetcher |
| `GATED` | `__N_REDIRECT` to `/users/connect` | stop this view. Never retry, never failover, never escalate |
| `EMPTY` | `__NEXT_DATA__` present, 0 reviews, no redirect | view exhausted, move on |

`GATED` must remain distinct from `TRANSIENT_BLOCK`. Conflating them is what
produced the earlier dead ends (harder retries, a different browser, and finally
a credentials plan — all responses to a wall that none of them can move).

**Circuit breaker:** three consecutive `TRANSIENT_BLOCK`s demote a fetcher for the
remainder of the run; the other serves. The serving fetcher is recorded per row.

### 4.4 Schema

One flat row per review. Sheet column order:

| Column | Source |
|---|---|
| `review_id` | `id` — dedup key |
| `platform` | literal `Trustpilot` |
| `company_domain` | run input |
| `company_name` | `businessUnit.displayName` |
| `url` | `https://www.trustpilot.com/reviews/{id}` |
| `rating` | `rating` |
| `title` | `title` |
| `raw_text` | `text` |
| `review_date` | `dates.publishedDate` |
| `experience_date` | `dates.experiencedDate` |
| `language` | `language` |
| `country` | `location` |
| `is_verified` | `labels.verification.isVerified` |
| `review_source` | `labels.verification.reviewSourceName` |
| `support_reply` | `reply.message` |
| `support_reply_date` | `reply.publishedDate` |
| `source_view` | planner: which view produced this row |
| `fetched_at` | run timestamp |
| `fetcher` | `chrome` or `jina` |
| `customer_order_status` | blank — enrichment |
| `journey_stage` | blank — enrichment |
| `review_type` | blank — enrichment |

`consumer_name` is parsed but **not written** unless `INCLUDE_CONSUMER_NAME=true`.

### 4.5 Sheet as store

- Tab per company, created with headers on first run (extends existing
  `setup_sheet.py`).
- **Dedup across runs:** read the tab's existing `review_id` column once per run;
  skip ids already present; append only new rows.
- **Resume:** same mechanism. A run that dies mid-way is re-run and continues.
- **Mid-run checkpoint:** local gitignored JSON so a crash between flushes does
  not re-fetch already-paid-for pages.
- **Batched writes:** one `append_rows` per view, never per review.

Accepted cost: dedup is bounded by sheet contents, so hand-deleting rows
reintroduces duplicates on the next run.

## 5. Configuration

`.env` (gitignored, already in place):

```
GOOGLE_SHEET_URL=...
GOOGLE_CREDS_FILE=scrapper.json
JINA_API_KEY=...                 # optional; keyless works
INCLUDE_CONSUMER_NAME=false      # new, default false
```

Service account `scraperrc@scrapper-507419.iam.gserviceaccount.com` must have
Editor access on the target sheet.

**`.gitignore` hardening is task one of implementation.** Current rules cover
`.env`, `sa.json`, `scrapper.json`, `client_secret*.json`, `*.json.key` — but not
`*.json` generally, while scraped output `.json` files are committed. A
service-account key downloaded under Google's default filename would not be
ignored.

## 6. Error handling

- Per-view failures are isolated: a dead view is logged and skipped, the run
  continues.
- Both fetchers demoted (circuit breaker on each) aborts the run with whatever is
  already written to the sheet retained.
- `GATED` on a page <= 10 signals a planner bug — logged loudly, not swallowed.
- A run summary reports: views planned, pages fetched, rows appended, duplicates
  skipped, per-fetcher counts, views truncated by the 200 cap.

## 7. Testing

| Unit | Test |
|---|---|
| planner | offline, fabricated `totalCount`s: split order, never exceeds page 10, target-aware stop, exhaustion |
| parser | saved `__NEXT_DATA__` HTML fixture -> expected normalized rows; a fixture per outcome class (OK / gated / interstitial / empty) |
| classifier | one fixture per outcome, asserting `GATED` never routes to retry or failover |
| dedup | overlapping view outputs collapse to unique ids |
| sheet writer | scratch tab: header creation, batched append, existing-id read-back |
| fetchers | live smoke tests, run on demand, not in the default suite |

## 8. Legal and ethical position

- Public review pages only; no authentication, no access-control bypass.
- Automated collection is against Trustpilot's terms. Acceptable for internal
  research at this scale; it is a real risk the agency should be told about, not
  a solved problem. If this becomes a billed client deliverable, Trustpilot's
  official API or a licensed data provider is the correct route.
- Polite pacing (jittered multi-second delays, single tab, no concurrency
  against Trustpilot).
- Personal data minimised by default (`consumer_name` off).

## 9. Future work

- LLM enrichment of the three blank columns.
- Additional platforms behind the same seam. Order by feasibility, not by the
  wish list: **Reddit** (official API, commercial tier applies) and
  **Google Reviews** (Places API returns ~5 reviews per place, so depth needs a
  vendor such as SerpApi / Outscraper / Apify) are tractable.
  **Facebook and Instagram are not** — Graph API only covers pages you manage and
  Meta actively litigates scraping. That should be said to the company before it
  is promised to a client.
- Discovery step: Tavily / DDGS to build competitor target lists. Right tool for
  finding review-page URLs, wrong tool for extracting review bodies.
- Paid unblocker as a third fetcher, if the company funds it. Note it raises
  throughput and block-resistance but **does not** lift the 200-per-view login
  gate — that ceiling is unaffected by spend.
