import requests

url = "https://r.jina.ai/https://www.trustpilot.com/review/www.trademax.se?page=1&languages=en&replies=true"

headers = {
    "X-Return-Format": "html"
}

resp = requests.get(url, headers=headers, timeout=30)
print("Status:", resp.status_code)
print("Length:", len(resp.text))
print("Contains __NEXT_DATA__:", "__NEXT_DATA__" in resp.text)

with open("jina_test_output.html", "w", encoding="utf-8") as f:
    f.write(resp.text)