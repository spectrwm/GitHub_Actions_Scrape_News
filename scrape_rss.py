import feedparser
import requests
from bs4 import BeautifulSoup
import json
import time
import random
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
import trafilatura

# -------------------------
# CONFIG
# -------------------------
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Accept": "text/html,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9"
}

MAX_RETRIES = 3
BACKOFF_BASE = 2
TIMEOUT = 15

seen_articles = set()


# -------------------------
# SMART FETCH (retry + backoff)
# -------------------------
def fetch(url):
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)

            if r.status_code == 200:
                return r.text

            # retry only on temporary errors
            if r.status_code in [429, 500, 502, 503]:
                raise Exception(f"Retryable error {r.status_code}")

            return None

        except Exception as e:
            sleep_time = BACKOFF_BASE ** attempt + random.uniform(0.5, 1.5)
            print(f"⚠️ Retry {attempt+1} for {url} in {sleep_time:.1f}s")
            time.sleep(sleep_time)

    return None


# -------------------------
# GOOGLE CACHE FALLBACK
# -------------------------
def fetch_google_cache(url):
    cache_url = f"https://webcache.googleusercontent.com/search?q=cache:{url}"
    print(f"🌐 Trying Google cache: {url}")
    return fetch(cache_url)


# -------------------------
# TYPE DETECTION
# -------------------------
def detect_type(text):
    if not text:
        return "none"

    t = text.lower()

    if "<rss" in t or "<feed" in t:
        return "rss"

    if "<urlset" in t:
        return "sitemap"

    if "<html" in t:
        return "html"

    return "unknown"


# -------------------------
# RSS PARSER
# -------------------------
def parse_rss(text, source):
    feed = feedparser.parse(text)

    results = []

    for entry in feed.entries:
        link = entry.get("link")
        if not link or link in seen_articles:
            continue

        seen_articles.add(link)

        results.append({
            "title": entry.get("title", ""),
            "link": link,
            "summary": entry.get("summary", ""),
            "source": source
        })

    return results


# -------------------------
# SITEMAP PARSER
# -------------------------
def parse_sitemap(text):
    soup = BeautifulSoup(text, "xml")
    return [loc.text for loc in soup.find_all("loc")][:20]


# -------------------------
# HTML LINK EXTRACTOR
# -------------------------
def extract_links(base_url, html):
    soup = BeautifulSoup(html, "html.parser")
    links = set()

    for a in soup.find_all("a", href=True):
        full = urljoin(base_url, a["href"])

        if urlparse(full).netloc != urlparse(base_url).netloc:
            continue

        if any(x in full.lower() for x in [
            "category", "tag", "login", "about", "contact", "#"
        ]):
            continue

        if len(full.split("/")) < 4:
            continue

        links.add(full)

    return list(links)


# -------------------------
# ARTICLE EXTRACTOR
# -------------------------
def extract_article(url):
    downloaded = trafilatura.fetch_url(url)

    if not downloaded:
        return ""

    text = trafilatura.extract(downloaded)

    return text or ""


# -------------------------
# MAIN ROUTER
# -------------------------
def process_url(url):
    print(f"\n🔎 Processing: {url}")

    html = fetch(url)

    # try cache if blocked
    if not html:
        html = fetch_google_cache(url)

    if not html:
        print("❌ Failed completely")
        return []

    page_type = detect_type(html)

    if page_type == "rss":
        print("✅ RSS detected")
        return parse_rss(html, url)

    elif page_type == "sitemap":
        print("🧭 Sitemap detected")
        links = parse_sitemap(html)

        results = []
        for link in links:
            content = extract_article(link)
            if content:
                results.append({
                    "title": link.split("/")[-1],
                    "link": link,
                    "summary": content[:500],
                    "source": url
                })
        return results

    elif page_type == "html":
        print("🧠 HTML fallback")

        links = extract_links(url, html)

        results = []
        for link in links[:15]:
            if link in seen_articles:
                continue

            seen_articles.add(link)

            content = extract_article(link)

            if not content or len(content) < 200:
                continue

            results.append({
                "title": link.split("/")[-1],
                "link": link,
                "summary": content[:500],
                "source": url
            })

        return results

    else:
        print("❓ Unknown format")
        return []


# -------------------------
# RUN PIPELINE
# -------------------------
def main():
    with open("rss_feeds.txt") as f:
        urls = [u.strip() for u in f if u.strip()]

    all_news = []

    for url in urls:
        articles = process_url(url)
        all_news.extend(articles)

    print(f"\n✅ Collected: {len(all_news)}")

    with open("news.json", "w", encoding="utf-8") as f:
        json.dump({
            "updated": datetime.now(timezone.utc).isoformat(),
            "news": all_news
        }, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
