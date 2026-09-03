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


def build_pool(headless: bool = False) -> FetcherPool:
    """Chrome first (unlimited, free), Jina as the independent fallback."""
    return FetcherPool([ChromeCDPFetcher(headless=headless), JinaFetcher()])


def _safe_slug(text: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in text)[:60]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scraper.run",
        description="Collect rated reviews into one Google Sheet tab per company.")
    parser.add_argument("--company", required=True, help="Sheet tab name")
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

    if not args.trustpilot and not args.google:
        sys.stderr.write(
            "error: at least one of --trustpilot or --google must be given\n")
        return 2

    checkpoint = Checkpoint(os.path.join(
        args.checkpoint_dir, "{0}.json".format(_safe_slug(args.company))))

    pool = None
    chrome = None
    reviews: List[Review] = []
    stats: Dict[str, int] = {}

    try:
        if args.trustpilot:
            pool = build_pool(headless=args.headless)
            result: CollectResult = TrustpilotAdapter(pool).collect(
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
