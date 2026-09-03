# Multi-Platform Review Collection — Design

Date: 2026-09-04
Status: approved, pending implementation plan

## 1. Goal

Collect customer-review data for competitor sets, reliably and repeatedly, into a
Google Sheet with one tab per company. Target: **~3,000 reviews per run, well
inside an hour**, on free infrastructure.

The consumer is a customer-intelligence workflow: an agency compares an SMB
client against many competitors, so breadth across companies matters more than
exhaustive depth on any one of them.

Two platforms, both rated and both universal across a competitor set:
**Trustpilot** and **Google Maps**.

### Non-goals (this spec)

- LLM enrichment of `journey_stage` / `review_type` / `customer_order_status`.
  Columns are reserved and left blank; enrichment is a separate spec.
- Platforms beyond Trustpilot and Google Maps. Reddit, Facebook and Instagram
  are **dropped on purpose**: they carry no star rating, and rating is the anchor
  for competitor comparison. Company-website reviews are dropped too — see §9.
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

### 2.1 Google Maps findings

Probed 2026-09-04 against the Trademax Möbler Stockholm listing:

| Property | Measured |
|---|---|
| Coverage | **348 of 348 — complete. No login gate at any depth** |
| Rate | ~385 reviews/min (348 in 54s of scrolling) |
| Rating | present on 348/348 |
| Review text | 202/348 — the rest are rating-only, normal for Google |
| Owner reply | 24/348 |
| Date | **relative strings only** — "8 months ago", "Edited a year ago" |

Google is the stronger source for SMB competitor sets: complete coverage, faster,
and a star on every row. Trustpilot's advantage is exact timestamps.

Multi-location chains list each showroom as its own place with its own reviews,
so a chain multiplies yield. Not yet measured.

### 2.2 Trustpilot filter dimensions

`filters.selected` exposes: `languages`, `date`, `stars`, `topics`, `search`,
`locationId`, `sort`, `verified`, `replies`.

`filters.reviewStatistics.reviewLanguages` lists **every valid language code with
its review count**, so the planner enumerates languages rather than guessing them.
This matters: an unrecognised code silently falls back to `all`. Probing `nb`
(not a valid code here; Norwegian is `no`) returned `all`'s 66,724 verbatim. A
planner that guessed codes would duplicate work and over-report coverage.

Measured yield for `www.trademax.se` (59 languages; `sv` 64,871, `en` 1,177,
`no` 276, `da` 154, long tail after):

| Strategy | Reachable | Requests |
|---|---|---|
| baseline only (current build) | 182 | ~10 + 182 reply fetches |
| stars only | ~710 | ~40 |
| languages only (200/lang) | ~1,000 | ~60 |
| **languages x stars** | **~2,676** | ~70 |
| + date buckets on capped views | **3,000+** | ~150 |

So ~3,000 from a single large company is achievable. **It is not achievable from a
small one** — a company with 500 total reviews yields 500 however it is sliced.
The 3,000-per-run target is met across the competitor set, not per company, and
the run summary must report per-company yield honestly.

## 3. Decisions and rationale

| Decision | Rationale |
|---|---|
| No Trustpilot account credentials, ever | Login is not required to read reviews. Automating an authenticated session converts a per-IP, expiring block into an account ban on a real person, and moves a contested-but-common practice into unambiguous ToS breach. Rotate IPs, not identities. |
| Google Sheet is the store; no SQLite | Keeps the system one artifact. Dedup and resume both work by reading the tab's existing `review_id` column. |
| `consumer_name` collected but **off by default** | Reviewer names are personal data under GDPR and most Trustpilot reviewers are EU residents. Analysis doesn't need it; easier to add later than to unpick from client sheets. Config flag. |
| Reddit / Facebook / Instagram dropped | No star rating, and rating anchors the comparison. Meta additionally offers no lawful free path. |
| Company-website reviews dropped | Present for only a minority of companies, so they add holes rather than columns to a bulk comparison, and on-site testimonials are curated by the company — the least representative input available. |
| Both platforms share one tab per company | Comparison work wants a company's whole voice in one place; the `platform` column filters trivially. |
| Language codes enumerated, never guessed | Invalid codes silently return `all`. |
| Planner is pure logic, no network | The cap/overlap/nesting rules are the likeliest source of subtle bugs. Testable offline with fabricated counts. |
| Fetcher returns HTML, not parsed data | One parser for all fetchers; swapping fetchers cannot change parsed output. |

## 4. Architecture

```
target list (company -> trustpilot domain + google place)
   ↓
platform adapter   [ trustpilot | google-maps ]
   ├─ plan   pure logic: what to fetch
   ├─ fetch  [ chrome-cdp | jina ]   swappable, failover
   └─ parse  platform-specific -> normalized row
   ↓
dedup by (platform, review_id)
   ↓
sheet writer       ONE tab per company, both platforms mixed, batched appends
```

**Adapter contract** — every platform implements the same four calls:

```
plan(target, budget)   -> [unit]      what to fetch, ordered by expected new rows
fetch(unit)            -> raw         via the shared fetcher layer
parse(raw)             -> [row]       normalized schema
classify(raw)          -> outcome     OK | TRANSIENT_BLOCK | GATED | EMPTY
```

Trustpilot's filter-slicing lives inside *its* adapter, not in core. Google's
adapter plans differently — scroll rounds against a place, not filter views — and
core neither knows nor cares.

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
2. `languages` — disjoint, and the largest lever for non-English companies.
   Codes are **enumerated from `filters.reviewStatistics.reviewLanguages`** with
   their counts, never guessed.
3. `date` buckets (last30days / 3mo / 6mo / 12mo) — these **nest** rather than
   partition, so later resort.
4. `sort=recency` vs default — same filter, different 200-window, partial
   overlap. Last resort.

Hard rules:

- Never plan a page number above 10 for any view.
- **Invalid-split guard:** if a child view's `totalCount` equals its parent's, the
  filter value was not recognised and silently fell back. Discard the view. This
  is what stops the `nb`-returns-`all` trap from duplicating work and inflating
  reported coverage.
- Per-language counts are known before fetching, so expected yield is computed up
  front and the cheapest views are planned first.

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

### 4.1b Google Maps adapter

No filter slicing: reviews load completely, so the plan is one unit per place.

- Resolve target -> place via Maps search, click through to `/maps/place/`.
- Open the Reviews pane, sort by **Newest** where the control is available.
- Scroll the review container until unique-id count stops growing for 5 rounds.
- Extract per `[data-review-id]` card: rating (from the star `aria-label`), text,
  relative date, owner reply. **Dedup by review id during extraction** — each
  review contributes several `[data-review-id]` nodes, so raw node counts
  overstate by ~3.8x (1,320 nodes for 348 reviews in the probe).

Known rough edges, to handle in implementation:
- The sort control's accessible name is locale-dependent; the probe's selector
  failed. Needs a locale-robust selector, with sort treated as optional.
- Relative dates are converted to an absolute date **bracketed between
  neighbouring reviews** once sorted by Newest. Month-level accuracy, not day.
  Every Google row carries `date_precision = relative`.
- Multi-location companies: one unit per place, aggregated under the company.

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
| `review_id` | platform-namespaced (`trustpilot:<id>` / `google:<id>`) — dedup key |
| `platform` | `Trustpilot` or `Google` |
| `company_domain` | run input |
| `company_name` | `businessUnit.displayName` / Maps place name |
| `location` | Google: which place produced the row. Blank for Trustpilot |
| `url` | `https://www.trustpilot.com/reviews/{id}` |
| `rating` | `rating` |
| `title` | `title` |
| `raw_text` | `text` |
| `review_date` | Trustpilot: `dates.publishedDate`. Google: bracketed from relative label |
| `date_precision` | `exact` (Trustpilot) or `relative` (Google) |
| `experience_date` | `dates.experiencedDate` |
| `language` | `language` |
| `country` | `location` |
| `is_verified` | `labels.verification.isVerified` |
| `review_source` | `labels.verification.reviewSourceName` |
| `support_reply` | `reply.message` |
| `support_reply_date` | `reply.publishedDate` |
| `source_view` | Trustpilot: which filter view. Google: which place/scroll unit |
| `fetched_at` | run timestamp |
| `fetcher` | `chrome` or `jina` |
| `customer_order_status` | blank — enrichment |
| `journey_stage` | blank — enrichment |
| `review_type` | blank — enrichment |

`consumer_name` is parsed but **not written** unless `INCLUDE_CONSUMER_NAME=true`.
This applies to both platforms.

`date_precision` exists so nobody silently treats a Google date as a Trustpilot
one. Any time-series analysis must respect it.

### 4.5 Sheet as store

- **One tab per company, both platforms mixed**, created with headers on first
  run (extends existing `setup_sheet.py`). The `platform` column separates them.
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
| planner guard | a child view echoing its parent's totalCount is discarded |
| google extractor | saved DOM fixture: node-level duplicates collapse to unique reviews; ratings parsed from aria-labels |
| date bracketing | ordered relative labels -> bounded absolute dates, `date_precision=relative` |
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
- Discovery step: Tavily / DDGS to build competitor target lists. Right tool for
  finding review-page URLs, wrong tool for extracting review bodies.
- Paid unblocker as a third fetcher, if the company funds it. Note it raises
  throughput and block-resistance but **does not** lift the 200-per-view login
  gate — that ceiling is unaffected by spend.
