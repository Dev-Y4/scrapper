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

Just run it and it asks:

    ./.venv/bin/python -m scraper.run

    Which company do you want reviews for?

      Company name (becomes the sheet tab): Gymshark
      Trustpilot domain (e.g. www.trademax.se, blank to skip): www.gymshark.com
      Google Maps search (e.g. "Trademax Stockholm", blank to skip):
      How many reviews? [3000]:

Leave either platform blank to skip it. Flags skip the questions entirely,
which is what you want for scripting a whole competitor set:

    ./.venv/bin/python -m scraper.run \
        --company "Trademax" \
        --trustpilot www.trademax.se \
        --google "Trademax Möbler Stockholm" \
        --target 3000

Add `--dry-run --output out.json` to inspect results without touching the sheet.
Re-running the same command is safe: rows already in the tab are skipped.

## What you get

One row per review, both platforms in the same tab, separated by `platform`.
Trustpilot rows carry exact timestamps, star rating, reply text **and reply
date**, language, and verification source. Google rows carry star rating, text
where the reviewer wrote any, and owner replies.

Trustpilot results are a **stratified sample**: the target is spread evenly
across star ratings (a 600-review run returns ~120 of each), because a set
skewed to one rating makes competitor sentiment comparison useless.

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
- **A small target collects a bit more than you asked.** A language's five
  star slices are funded as a set, so a 60-review ask returns 100 (20 per
  rating). Balance beats exactness: a part-funded set would return only 1- and
  2-star reviews.
- **`transient_chrome` in the summary.** A page was refused and retried
  successfully. Only a `demoted_*` line means a fetcher gave up for the run.

## Traps already paid for — do not undo these

- **Page 1 of a view is requested WITHOUT `&page=1`.** Trustpilot canonicalises
  an explicit `page=1` to the unfiltered default view and silently drops every
  filter. Adding it back collapses all slicing into one view.
- **Language codes come from the page** (`reviewStatistics.reviewLanguages`),
  never from a hand-written list. An unrecognised code silently returns the
  unfiltered result, which looks like a working filter.
- **Date buckets are not used for slicing.** They nest, so they re-fetch the
  same reviews: measured at 391 duplicates for 211 unique rows.
- **Google review nodes are deduped during extraction.** One review contributes
  ~3.8 `[data-review-id]` nodes, so raw counts overstate by nearly 4x.

## Never do this

- Do not put Trustpilot or Google credentials in `.env`. Reviews need no login,
  automating a logged-in session risks a ban on a real person's account, and it
  does not lift the page-10 gate.
- Do not `git add .` — `.env` and `scrapper.json` are live secrets in the tree.
