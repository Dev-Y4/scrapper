import json
import time
import re
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

BASE_URL = "https://www.trustpilot.com/review/{domain}?page={page}&languages=en&replies=true"


def get_next_data(driver, url, timeout=20):
    driver.get(url)
    WebDriverWait(driver, timeout).until(
        EC.presence_of_element_located((By.ID, "__NEXT_DATA__"))
    )
    raw_json = driver.execute_script(
        "return document.getElementById('__NEXT_DATA__').textContent;"
    )
    return json.loads(raw_json)


def normalize_review(raw: dict, domain: str) -> dict:
    reply = raw.get("reply") or {}
    dates = raw.get("dates") or {}
    return {
        "platform": "Trustpilot",
        "url": f"https://www.trustpilot.com/reviews/{raw.get('id')}",
        "raw_text": (raw.get("text") or "").strip(),
        "review_date": dates.get("publishedDate"),
        "experience_date": dates.get("experiencedDate"),
        "support_reply": (reply.get("message") or "").strip(),
        "support_reply_date": reply.get("publishedDate") or "",
        "company_domain": domain,
        "customer_order_status": "",
        "journey_stage": "",
        "review_type": "",
    }


def scrape_company(domain: str, start_page: int = 1, max_pages: int = None, delay: float = 2.5):
    all_reviews = []

    options = webdriver.ChromeOptions()
    options.add_argument("--start-maximized")
    options.add_argument(r"--user-data-dir=C:\Users\Asus\SeleniumChromeProfile")
    options.add_argument("--profile-directory=Profile 11")
    driver = webdriver.Chrome(options=options)

    page = start_page
    try:
        while True:
            url = BASE_URL.format(domain=domain, page=page)
            print(f"Fetching page {page}: {url}")

            try:
                data = get_next_data(driver, url)
            except Exception as e:
                print(f"  [!] Failed to load/parse page {page}: {e}")
                break

            page_props = data.get("props", {}).get("pageProps", {})
            reviews = page_props.get("reviews", [])

            if not reviews:
                print("No reviews found on this page. Stopping.")
                break

            for raw in reviews:
                all_reviews.append(normalize_review(raw, domain))

            print(f"  -> Got {len(reviews)} reviews (total so far: {len(all_reviews)})")

            checkpoint_file = f"{domain.replace('.', '_')}_selenium_INPROGRESS.json"
            with open(checkpoint_file, "w", encoding="utf-8") as f:
                json.dump(all_reviews, f, indent=2, ensure_ascii=False)

            if max_pages and (page - start_page + 1) >= max_pages:
                print(f"Reached max_pages limit ({max_pages}).")
                break

            page += 1
            time.sleep(delay)
    finally:
        driver.quit()

    return all_reviews


if __name__ == "__main__":
    domain = input("Enter Trustpilot domain (e.g. www.trademax.se): ").strip()
    start_page_input = input("Start from page (default 1): ").strip()
    start_page = int(start_page_input) if start_page_input else 1
    max_pages_input = input("Max pages to fetch this run (leave blank for all remaining): ").strip()
    max_pages = int(max_pages_input) if max_pages_input else None

    results = scrape_company(domain, start_page=start_page, max_pages=max_pages)

    output_file = f"{domain.replace('.', '_')}_selenium_reviews.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\nDone. Saved {len(results)} reviews to {output_file}")