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
from scraper.platforms.google.adapter import GoogleMapsAdapter
from scraper.platforms.trustpilot.adapter import CollectResult, TrustpilotAdapter
from scraper.sheets import (append_reviews, existing_review_ids,
                            get_or_create_tab, open_spreadsheet)


def build_pool(chrome=None, headless: bool = False) -> FetcherPool:
    """Chrome first (unlimited, free), Jina as the independent fallback."""
    return FetcherPool([chrome or ChromeCDPFetcher(headless=headless), JinaFetcher()])


def _ask(prompt: str) -> str:
    return input(prompt)


def _interactive() -> bool:
    return sys.stdin.isatty()


def _fill_in_by_asking(args) -> None:
    """Run bare and it asks. Flags win, so scripted runs over a whole
    competitor set never stop to prompt."""
    print("\nWhich company do you want reviews for?\n")
    if not args.company:
        args.company = _ask("  Company name (becomes the sheet tab): ").strip()
    args.trustpilot = _ask(
        "  Trustpilot domain (e.g. www.trademax.se, blank to skip): ").strip()
    args.google = _ask(
        '  Google Maps search (e.g. "Trademax Stockholm", blank to skip): ').strip()
    answer = _ask("  How many reviews? [{0}]: ".format(args.target)).strip()
    if answer:
        try:
            args.target = int(answer)
        except ValueError:
            print("  not a number — keeping {0}".format(args.target))
    print("")


def _safe_slug(text: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in text)[:60]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scraper.run",
        description="Collect rated reviews into one Google Sheet tab per company.")
    parser.add_argument("--company", help="Sheet tab name")
    parser.add_argument("--trustpilot", help="Trustpilot domain, e.g. www.trademax.se")
    parser.add_argument("--google", help='Google Maps search query, e.g. "Trademax Stockholm"')
    parser.add_argument("--target", type=int, default=3000,
                        help="Stop planning once this many reviews are reachable")
    parser.add_argument("--dry-run", action="store_true",
                        help="Write JSON instead of touching the sheet")
    parser.add_argument("--output", default="out.json", help="--dry-run output path")
    parser.add_argument("--checkpoint-dir", default="checkpoints")
    parser.add_argument("--headless", action="store_true")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    load_dotenv()
    args = _parser().parse_args(argv)

    if not args.trustpilot and not args.google and _interactive():
        _fill_in_by_asking(args)

    if not args.trustpilot and not args.google:
        sys.stderr.write(
            "error: at least one of --trustpilot or --google must be given\n")
        return 2
    if not args.company:
        sys.stderr.write("error: --company is required\n")
        return 2

    checkpoint = Checkpoint(os.path.join(
        args.checkpoint_dir, "{0}.json".format(_safe_slug(args.company))))

    # One Chrome for the whole run. Playwright's sync API cannot be started a
    # second time in the same process, so a per-platform browser would fail the
    # moment the first one closed.
    chrome = ChromeCDPFetcher(headless=args.headless)
    pool = None
    reviews: List[Review] = []
    stats: Dict[str, int] = {}

    # Resume: rows already fetched by a run that died are on disk. Without this
    # the checkpoint protects nothing — a crashed Google phase once threw away
    # 3001 collected Trustpilot reviews.
    for row in checkpoint.reviews():
        known = set(Review.__dataclass_fields__)
        reviews.append(Review(**{k: v for k, v in row.items() if k in known}))
    if reviews:
        stats["resumed_from_checkpoint"] = len(reviews)

    seen = set(review.review_id for review in reviews)

    def keep(new_rows):
        added = 0
        for review in new_rows:
            if review.review_id in seen:
                continue
            seen.add(review.review_id)
            reviews.append(review)
            added += 1
        return added

    try:
        if args.trustpilot:
            pool = build_pool(chrome=chrome, headless=args.headless)
            try:
                result: CollectResult = TrustpilotAdapter(pool).collect(
                    args.trustpilot, target=args.target,
                    on_view=lambda label, rows: checkpoint.extend(rows))
                keep(result.reviews)
                stats.update(result.stats)
            except Exception as error:   # noqa: BLE001 - one platform must not
                stats["trustpilot_failed"] = 1   # take the other one down
                print("  Trustpilot phase failed: {0}".format(error))

        if args.google:
            try:
                google_result = GoogleMapsAdapter(chrome).collect(args.google,
                                                                  args.company)
                keep(google_result.reviews)
                checkpoint.extend(google_result.reviews)
                for key, value in google_result.stats.items():
                    stats["google_{0}".format(key)] = value
            except Exception as error:   # noqa: BLE001
                stats["google_failed"] = 1
                print("  Google phase failed: {0}".format(error))
    finally:
        # Closing the pool closes Chrome too, so never close both.
        if pool is not None:
            pool.close()
        elif hasattr(chrome, "close"):
            chrome.close()

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
