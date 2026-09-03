import json
import re
import time
import os
from datetime import datetime
import requests
from dotenv import load_dotenv

load_dotenv()

JINA_BASE = "https://r.jina.ai/"
LISTING_URL = "https://www.trustpilot.com/review/{domain}?page={page}&languages=en&replies=true"
JINA_API_KEY = os.getenv("JINA_API_KEY", "")

DATE_RE = re.compile(r'^([A-Z][a-z]+ \d{1,2}, \d{4})$', re.MULTILINE)
REVIEW_BLOCK_SPLIT = re.compile(r'\n(?=## \[)')
TITLE_URL_RE = re.compile(r'^## \[(.*?)\]\((https://www\.trustpilot\.com/reviews/([a-f0-9]+))\)', re.DOTALL)
MARKER_STRIP_RE = re.compile(r'^(Unprompted review|Invited review)\s*\n*')
BOILERPLATE_CUT_MARKERS = [
    "See if a website is trustworthy",
    "### The Trustpilot Experience",
    "Advertisement",
    "are you human?",
    "## Company details",
    "### Top mentions",
]


def jina_get(target_url: str) -> str:
    headers = {}
    if JINA_API_KEY:
        headers["Authorization"] = f"Bearer {JINA_API_KEY}"
    resp = requests.get(JINA_BASE + target_url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.text


def parse_date(date_str: str):
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return date_str


def clean_trailing_boilerplate(text: str) -> str:
    for marker in BOILERPLATE_CUT_MARKERS:
        idx = text.find(marker)
        if idx != -1:
            text = text[:idx]
    return text.strip()


def parse_listing_markdown(markdown_text: str, domain: str) -> list:
    reviews = []
    blocks = REVIEW_BLOCK_SPLIT.split(markdown_text)

    for block in blocks:
        block = block.strip()
        title_match = TITLE_URL_RE.match(block)
        if not title_match:
            continue  # not a review block (header/footer/promo junk)

        title, review_url, review_id = title_match.groups()
        remaining = TITLE_URL_RE.sub("", block, count=1).strip()

        # The review's own date is the FIRST date-shaped line after the title.
        date_match = DATE_RE.search(remaining)
        if not date_match:
            continue  # can't reliably anchor this block, skip rather than guess

        review_date = parse_date(date_match.group(1))
        body_text = remaining[:date_match.start()].strip()
        if not body_text:
            body_text = title

        after_date = remaining[date_match.end():].strip()

        # Strip optional "Unprompted review"/"Invited review" marker if present
        after_date = MARKER_STRIP_RE.sub("", after_date).strip()

        # Strip stray standalone numeric lines (vote-count artifacts seen in older layout)
        after_date = re.sub(r'^\d+\s*\n+', '', after_date).strip()

        reply_text = clean_trailing_boilerplate(after_date)
        support_reply = reply_text if len(reply_text) > 2 else ""

        reviews.append({
            "platform": "Trustpilot",
            "url": review_url,
            "review_id": review_id,
            "raw_text": body_text,
            "review_date": review_date,
            "support_reply": support_reply,
            "support_reply_date": "",
            "company_domain": domain,
            "customer_order_status": "",
            "journey_stage": "",
            "review_type": "",
        })

    return reviews


def fetch_reply_date(review_url: str, retries: int = 2) -> str:
    for attempt in range(retries + 1):
        try:
            markdown_text = jina_get(review_url)
            match = re.search(r'Reply from .*?\n\n([A-Z][a-z]+ \d{1,2}, \d{4})', markdown_text)
            if match:
                return parse_date(match.group(1))
            return ""
        except requests.RequestException as e:
            if attempt < retries:
                print(f"    [retry {attempt+1}/{retries}] {review_url}: {e}")
                time.sleep(3)
            else:
                print(f"    [!] Giving up on reply date for {review_url}: {e}")
                return ""


def scrape_company(domain: str, max_pages: int = None, delay: float = 1.5):
    all_reviews = []
    page = 1
    consecutive_empty = 0

    while True:
        listing_url = LISTING_URL.format(domain=domain, page=page)
        print(f"Fetching listing page {page}...")

        try:
            markdown_text = jina_get(listing_url)
        except requests.RequestException as e:
            print(f"  [!] Failed to fetch page {page}: {e}")
            break

        page_reviews = parse_listing_markdown(markdown_text, domain)

        if not page_reviews:
            consecutive_empty += 1
            if consecutive_empty == 1:
                print(f"  [!] Page {page} returned 0 reviews — retrying once (possible transient blip)...")
                time.sleep(4)
                continue  # retry same page number without incrementing
            else:
                print("Two consecutive empty pages. Assuming genuine end of reviews. Stopping.")
                break

        consecutive_empty = 0  # reset on any successful page
        print(f"  -> Parsed {len(page_reviews)} reviews from page {page}")

        for review in page_reviews:
            if review["support_reply"]:
                time.sleep(delay)
                review["support_reply_date"] = fetch_reply_date(review["url"])

        all_reviews.extend(page_reviews)
        print(f"  -> Total so far: {len(all_reviews)}")

        checkpoint_file = f"{domain.replace('.', '_')}_reviews_INPROGRESS.json"
        with open(checkpoint_file, "w", encoding="utf-8") as f:
            json.dump(all_reviews, f, indent=2, ensure_ascii=False)

        if max_pages and page >= max_pages:
            print(f"Reached max_pages limit ({max_pages}).")
            break

        page += 1
        time.sleep(delay)

    return all_reviews


if __name__ == "__main__":
    domain = input("Enter Trustpilot domain (e.g. www.trademax.se): ").strip()
    max_pages_input = input("Max pages to fetch (leave blank for all): ").strip()
    max_pages = int(max_pages_input) if max_pages_input else None

    results = scrape_company(domain, max_pages=max_pages)

    output_file = f"{domain.replace('.', '_')}_reviews.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\nDone. Saved {len(results)} reviews to {output_file}")