import json
import time
import random
import requests
import feedparser
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin, parse_qs
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright


# =========================
# HEADERS (stable, safe)
# =========================
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Accept": "text/html,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9"
}


# =========================
# BLOCK RULES (FIX YOUR ISSUE)
# =========================
BLOCK_PATTERNS = [
    "search",
    "cache:",
    "httpservice",
    "enablejs",
    "retry",
    "login",
    "signup",
    "wp-login"
]

TRACKING_PARAMS = {
    "sca_esv",
    "emsg",
    "sei",
    "utm_source",
    "utm_medium",
    "utm_campaign"
}


# =========================
# URL VALIDATION (IMPORTANT FIX)
# =========================
def is_valid_url(url):
    if not url:
        return False

    u = url.lower()

    # block junk paths
    if any(p in u for p in BLOCK_PATTERNS):
        return False

    parsed = urlparse(url)

    # remove tracking params
    qs = parse_qs(parsed.query)
    if any(k in TRACKING_PARAMS for k in qs.keys()):
        return False

    # must have meaningful path
    if len(parsed.path) < 2:
        return False

    return True


# =========================
# SAFE FETCH
# =========================
def fetch(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code == 200:
            return r.text
    except:
        pass
    return None


# =========================
# GOOGLE CACHE (optional fallback)
# =========================
def google_cache(url):
    cache_url = f"https://webcache.googleusercontent.com/search?q=cache:{url}"
    return fetch(cache_url)


# =========================
# TYPE DETECTION
# =========================
def detect_type(html):
    if not html:
        return None

    h = html.lower()

    if "<rss" in h or "<feed" in h:
        return "rss"

    if "<urlset" in h:
        return "sitemap"

    if "<html" in h:
        return "html"

    return None


# =========================
# WORDPRESS API (BEST PATH)
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

    out = []
    for p in posts:
        out.append({
            "title": BeautifulSoup(p["title"]["rendered"], "html.parser").get_text(),
            "link": p["link"],
            "published": p.get("date"),
            "summary": BeautifulSoup(p["excerpt"]["rendered"], "html.parser").get_text(),
            "source": base
        })

    return out


# =========================
# RSS PARSER
# =========================
def parse_rss(url, html):
    feed = feedparser.parse(html)

    return [{
        "title": e.get("title", ""),
        "link": e.get("link", ""),
        "published": e.get("published", ""),
        "summary": BeautifulSoup(e.get("summary", ""), "html.parser").get_text(),
        "source": url
    } for e in feed.entries]


# =========================
# SITEMAP PARSER
# =========================
def parse_sitemap(url, html):
    soup = BeautifulSoup(html, "xml")
    links = [l.text for l in soup.find_all("loc")]

    return [{
        "title": l.split("/")[-1],
        "link": l,
        "source": url
    } for l in links if is_valid_url(l)][:15]


# =========================
# HTML PARSER (FIXED)
# =========================
def parse_html(url, html):
    soup = BeautifulSoup(html, "html.parser")

    links = set()

    for a in soup.find_all("a", href=True):
        full = urljoin(url, a["href"])

        if not is_valid_url(full):
            continue

        if urlparse(full).netloc != urlparse(url).netloc:
            continue

        links.add(full)

    return [{
        "title": l.split("/")[-1],
        "link": l,
        "source": url
    } for l in list(links)[:15]]


# =========================
# PLAYWRIGHT FALLBACK
# =========================
def fetch_rendered(url):
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            page.goto(url, timeout=30000)
            page.wait_for_timeout(2000)

            html = page.content()

            browser.close()
            return html
    except:
        return None


# =========================
# SMART ROUTER
# =========================
def process(url):
    print(f"\n🔎 Processing: {url}")

    html = fetch(url)

    if not html:
        html = google_cache(url)

    t = detect_type(html)

    # RSS
    if t == "rss":
        print("✅ RSS detected")
        return parse_rss(url, html)

    # WordPress API
    wp = wordpress_api(url)
    if wp:
        print("🚀 WP API")
        return wp

    # Sitemap
    sitemap = fetch(url.rstrip("/") + "/sitemap.xml")
    if sitemap and detect_type(sitemap) == "sitemap":
        print("🧠 Sitemap")
        return parse_sitemap(url, sitemap)

    # HTML
    if html:
        print("🧠 HTML")
        return parse_html(url, html)

    # Playwright fallback
    print("🧠 Playwright fallback")
    html = fetch_rendered(url)

    if html:
        return parse_html(url, html)

    return []


# =========================
# MAIN
# =========================
def main():
    with open("rss_feeds.txt") as f:
        urls = [u.strip() for u in f if u.strip()]

    all_data = []

    for url in urls:
        data = process(url)
        all_data.extend(data)

    print(f"\n✅ Collected: {len(all_data)}")

    with open("news.json", "w", encoding="utf-8") as f:
        json.dump({
            "updated": datetime.now(timezone.utc).isoformat(),
            "data": all_data
        }, f, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    main()
