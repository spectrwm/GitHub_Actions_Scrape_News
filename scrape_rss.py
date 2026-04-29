import requests
import feedparser
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin
from datetime import datetime, timezone
import json, time, random

from playwright.sync_api import sync_playwright

# =========================
# GLOBAL CONFIG
# =========================
HEADERS = {
    "User-Agent": random.choice([
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/119",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0) AppleWebKit/537.36 Safari/537.36"
    ]),
    "Accept": "text/html,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9"
}

MAX_RETRIES = 3
PLAYWRIGHT_POOL_SIZE = 2


# =========================
# FETCH WITH BACKOFF
# =========================
def fetch(url):
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            if r.status_code == 200:
                return r.text
        except:
            pass

        sleep = 2 ** attempt + random.random()
        time.sleep(sleep)

    return None


# =========================
# GOOGLE CACHE FALLBACK
# =========================
def fetch_google_cache(url):
    cache_url = f"https://webcache.googleusercontent.com/search?q=cache:{url}"
    print(f"🌐 Google cache fallback: {url}")
    return fetch(cache_url)


# =========================
# CONTENT TYPE DETECTION
# =========================
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


# =========================
# CMS DETECTION
# =========================
def detect_cms(url, html):
    if not html:
        return None

    if "wp-content" in html or "wp-json" in html:
        return "wordpress"

    if "ghost" in html:
        return "ghost"

    if "drupal" in html:
        return "drupal"

    return None


# =========================
# WORDPRESS API (🔥 BEST)
# =========================
def wordpress_api(url):
    base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    api = base + "/wp-json/wp/v2/posts?per_page=10"

    data = fetch(api)
    if not data:
        return []

    try:
        posts = json.loads(data)
    except:
        return []

    results = []

    for p in posts:
        results.append({
            "title": BeautifulSoup(p["title"]["rendered"], "html.parser").get_text(),
            "link": p["link"],
            "published": p["date"],
            "summary": BeautifulSoup(p["excerpt"]["rendered"], "html.parser").get_text(),
            "source": base
        })

    print("🚀 WordPress API success")
    return results


# =========================
# RSS PARSER
# =========================
def parse_rss(url, text):
    feed = feedparser.parse(text)

    results = []

    for e in feed.entries:
        results.append({
            "title": e.get("title", ""),
            "link": e.get("link", ""),
            "published": e.get("published", ""),
            "summary": BeautifulSoup(
                e.get("summary", ""), "html.parser"
            ).get_text(),
            "source": url
        })

    return results


# =========================
# SITEMAP PARSER
# =========================
def parse_sitemap(base_url, text):
    soup = BeautifulSoup(text, "xml")

    links = [loc.text for loc in soup.find_all("loc")]

    return [{"link": l, "title": l.split("/")[-1], "source": base_url} for l in links[:10]]


# =========================
# HTML PARSER
# =========================
def parse_html(base_url, html):
    soup = BeautifulSoup(html, "html.parser")

    links = set()

    for a in soup.find_all("a", href=True):
        full = urljoin(base_url, a["href"])

        if urlparse(full).netloc != urlparse(base_url).netloc:
            continue

        if len(full.split("/")) < 4:
            continue

        links.add(full)

    results = []

    for l in list(links)[:10]:
        results.append({
            "title": l.split("/")[-1],
            "link": l,
            "summary": "",
            "source": base_url
        })

    return results


# =========================
# PLAYWRIGHT POOL (🔥 FAST)
# =========================
class BrowserPool:
    def __init__(self, size=2):
        self.p = sync_playwright().start()
        self.browsers = [
            self.p.chromium.launch(headless=True, args=["--no-sandbox"])
            for _ in range(size)
        ]
        self.idx = 0

    def get_page(self):
        browser = self.browsers[self.idx]
        self.idx = (self.idx + 1) % len(self.browsers)
        return browser.new_page()

    def close(self):
        for b in self.browsers:
            b.close()
        self.p.stop()


def playwright_fetch(pool, url):
    try:
        page = pool.get_page()
        page.goto(url, timeout=20000)
        page.wait_for_timeout(2000)
        html = page.content()
        page.close()
        return html
    except:
        return None


# =========================
# SMART ROUTER
# =========================
def process_url(url, pool):
    print(f"\n🔎 Processing: {url}")

    html = fetch(url)

    if not html:
        html = fetch_google_cache(url)

    typ = detect_type(html)

    # ---- RSS
    if typ == "rss":
        print("✅ RSS detected")
        return parse_rss(url, html)

    # ---- CMS detection
    cms = detect_cms(url, html)

    if cms == "wordpress":
        data = wordpress_api(url)
        if data:
            return data

    # ---- Sitemap fallback
    sitemap = url.rstrip("/") + "/sitemap.xml"
    sm = fetch(sitemap)

    if sm and detect_type(sm) == "sitemap":
        print("🧠 Sitemap used")
        return parse_sitemap(url, sm)

    # ---- HTML parse
    if html and typ == "html":
        print("🧠 HTML parse")
        data = parse_html(url, html)
        if data:
            return data

    # ---- PLAYWRIGHT LAST
    print("🧠 Playwright fallback")
    html = playwright_fetch(pool, url)

    if html:
        return parse_html(url, html)

    return []


# =========================
# MAIN
# =========================
def main():
    with open("rss_feeds.txt") as f:
        urls = [u.strip() for u in f if u.strip()]

    pool = BrowserPool(PLAYWRIGHT_POOL_SIZE)

    all_news = []

    for url in urls:
        data = process_url(url, pool)
        all_news.extend(data)

    pool.close()

    print(f"\n✅ Collected: {len(all_news)}")

    with open("news.json", "w", encoding="utf-8") as f:
        json.dump({
            "last_updated": datetime.now(timezone.utc).isoformat(),
            "news": all_news
        }, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
