import requests
import feedparser
from bs4 import BeautifulSoup
import json
import time
import random
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
import trafilatura
from collections import defaultdict
from playwright.sync_api import sync_playwright

# -------------------------
# CONFIG
# -------------------------
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Accept": "text/html,application/xml;q=0.9,*/*;q=0.8"
}

MAX_RETRIES = 3
TIMEOUT = 15

seen_articles = set()
domain_last_request = defaultdict(float)

# -------------------------
# RATE LIMIT (per domain)
# -------------------------
def rate_limit(url, delay=1.5):
    domain = urlparse(url).netloc
    elapsed = time.time() - domain_last_request[domain]

    if elapsed < delay:
        time.sleep(delay - elapsed)

    domain_last_request[domain] = time.time()


# -------------------------
# FETCH
# -------------------------
def fetch(url):
    rate_limit(url)

    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)

            if r.status_code == 200:
                return r.text

            if r.status_code in [429, 500, 502, 503]:
                raise Exception("retry")

            return None

        except Exception:
            time.sleep(2 ** attempt + random.random())

    return None


# -------------------------
# CMS DETECTION
# -------------------------
def detect_wordpress_api(url):
    parsed = urlparse(url)
    api_url = f"{parsed.scheme}://{parsed.netloc}/wp-json/wp/v2/posts"

    data = fetch(api_url)

    if data and data.strip().startswith("["):
        print("🧠 WordPress API detected")
        return api_url

    return None


# -------------------------
# WORDPRESS FETCH
# -------------------------
def fetch_wordpress(api_url):
    data = fetch(api_url)

    if not data:
        return []

    posts = json.loads(data)
    results = []

    for p in posts[:20]:
        link = p.get("link")

        if not link or link in seen_articles:
            continue

        seen_articles.add(link)

        results.append({
            "title": BeautifulSoup(p["title"]["rendered"], "html.parser").text,
            "link": link,
            "summary": BeautifulSoup(p["excerpt"]["rendered"], "html.parser").text,
            "source": api_url
        })

    return results


# -------------------------
# RSS
# -------------------------
def parse_rss(url):
    text = fetch(url)
    if not text:
        return []

    feed = feedparser.parse(text)
    results = []

    for e in feed.entries:
        link = e.get("link")

        if not link or link in seen_articles:
            continue

        seen_articles.add(link)

        results.append({
            "title": e.get("title", ""),
            "link": link,
            "summary": e.get("summary", ""),
            "source": url
        })

    return results


# -------------------------
# HTML LINKS
# -------------------------
def extract_links(base_url, html):
    soup = BeautifulSoup(html, "html.parser")
    links = set()

    for a in soup.find_all("a", href=True):
        full = urljoin(base_url, a["href"])

        if urlparse(full).netloc != urlparse(base_url).netloc:
            continue

        if len(full.split("/")) < 4:
            continue

        if any(x in full.lower() for x in ["tag", "category", "login"]):
            continue

        links.add(full)

    return list(links)


# -------------------------
# ARTICLE
# -------------------------
def extract_article(url):
    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        return ""

    return trafilatura.extract(downloaded) or ""


# -------------------------
# PLAYWRIGHT POOL
# -------------------------
class BrowserPool:
    def __init__(self):
        self.p = sync_playwright().start()
        self.browser = self.p.chromium.launch(headless=True)
        self.context = self.browser.new_context()

    def fetch(self, url):
        try:
            page = self.context.new_page()
            page.goto(url, timeout=20000, wait_until="domcontentloaded")
            page.wait_for_timeout(1000)
            html = page.content()
            page.close()
            return html
        except:
            return None

    def close(self):
        self.browser.close()
        self.p.stop()


browser_pool = BrowserPool()


# -------------------------
# MAIN ROUTER
# -------------------------
def process_url(url):
    print(f"\n🔎 {url}")

    # 1. Try RSS directly
    rss = parse_rss(url)
    if rss:
        print("✅ RSS")
        return rss

    # 2. WordPress API
    wp_api = detect_wordpress_api(url)
    if wp_api:
        return fetch_wordpress(wp_api)

    # 3. HTML
    html = fetch(url)

    if not html:
        print("⚠️ HTML failed → Playwright")
        html = browser_pool.fetch(url)

    if not html:
        return []

    links = extract_links(url, html)

    results = []

    for link in links[:15]:
        if link in seen_articles:
            continue

        seen_articles.add(link)

        content = extract_article(link)

        # fallback → Playwright
        if not content or len(content) < 200:
            rendered = browser_pool.fetch(link)

            if rendered:
                content = trafilatura.extract(rendered) or ""

        if not content or len(content) < 200:
            continue

        results.append({
            "title": link.split("/")[-1],
            "link": link,
            "summary": content[:500],
            "source": url
        })

    return results


# -------------------------
# MAIN
# -------------------------
def main():
    with open("rss_feeds.txt") as f:
        urls = [u.strip() for u in f if u.strip()]

    all_news = []

    for url in urls:
        all_news.extend(process_url(url))

    print(f"\n✅ Collected: {len(all_news)}")

    with open("news.json", "w", encoding="utf-8") as f:
        json.dump({
            "updated": datetime.now(timezone.utc).isoformat(),
            "news": all_news
        }, f, indent=2, ensure_ascii=False)

    browser_pool.close()


if __name__ == "__main__":
    main()
