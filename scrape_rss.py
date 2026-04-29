import feedparser
import requests
from bs4 import BeautifulSoup
import json
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import trafilatura
from urllib.parse import urlparse, urljoin, urlunparse, parse_qsl, urlencode
import time

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# -----------------------------
# CONFIG
# -----------------------------
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)",
    "Accept": "*/*"
}

seen_articles = set()

# -----------------------------
# FETCH (HTTP SAFE)
# -----------------------------
def fetch(url, timeout=15):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout, allow_redirects=True)
        if r.status_code != 200:
            return None
        return r.text
    except Exception:
        return None


# -----------------------------
# URL NORMALIZATION
# -----------------------------
def normalize_url(url):
    try:
        parsed = urlparse(url)
        clean = [(k, v) for k, v in parse_qsl(parsed.query) if not k.startswith("utm")]
        return urlunparse(parsed._replace(query=urlencode(clean)))
    except Exception:
        return url


# -----------------------------
# DETECT CONTENT TYPE
# -----------------------------
def detect_type(text):
    if not text:
        return "unknown"

    t = text.strip().lower()

    if "<rss" in t or "<feed" in t:
        return "rss"
    if "<html" in t or "<!doctype html" in t:
        return "html"
    if "<?xml" in t:
        return "xml"

    return "unknown"


# -----------------------------
# RSS HANDLER
# -----------------------------
def parse_rss(url):
    text = fetch(url)
    if not text:
        return None

    if detect_type(text) not in ["rss", "xml"]:
        return None

    feed = feedparser.parse(text)
    if not feed.entries:
        return None

    return feed


# -----------------------------
# SITEMAP DETECTION
# -----------------------------
def try_sitemap(base_url):
    root = f"{urlparse(base_url).scheme}://{urlparse(base_url).netloc}"
    sitemap_urls = [
        root + "/sitemap.xml",
        root + "/sitemap_index.xml",
        root + "/wp-sitemap.xml"
    ]

    for sm in sitemap_urls:
        xml = fetch(sm)
        if not xml or "<url" not in xml.lower():
            continue

        soup = BeautifulSoup(xml, "xml")
        links = [loc.text for loc in soup.find_all("loc") if loc.text]

        if links:
            return links[:20]

    return []


# -----------------------------
# HTML LINK SCRAPER (FAST)
# -----------------------------
def crawl_html(base_url):
    html = fetch(base_url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    links = set()

    for a in soup.find_all("a", href=True):
        link = urljoin(base_url, a["href"])

        if urlparse(link).netloc != urlparse(base_url).netloc:
            continue

        if any(x in link.lower() for x in [
            "login", "signup", "tag", "category",
            "privacy", "about", "contact"
        ]):
            continue

        links.add(link)

    return list(links)[:15]


# -----------------------------
# PLAYWRIGHT (ONLY LAST RESORT)
# -----------------------------
def crawl_js(url):
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox"]
            )
            page = browser.new_page()

            page.goto(url, timeout=20000, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)

            html = page.content()
            browser.close()

            soup = BeautifulSoup(html, "html.parser")

            links = set()
            for a in soup.find_all("a", href=True):
                link = urljoin(url, a["href"])

                if urlparse(link).netloc != urlparse(url).netloc:
                    continue

                links.add(link)

            return list(links)[:15]

    except PlaywrightTimeoutError:
        print(f"⏱️ Playwright timeout: {url}")
        return []
    except Exception as e:
        print(f"❌ Playwright error: {e}")
        return []


# -----------------------------
# ARTICLE EXTRACTION
# -----------------------------
def extract_article(url):
    downloaded = trafilatura.fetch_url(url)
    if not downloaded:
        return ""

    text = trafilatura.extract(downloaded)
    return text.strip() if text else ""


# -----------------------------
# SMART ROUTER (IMPORTANT FIX)
# -----------------------------
def resolve(url):
    print(f"\n🔎 Processing: {url}")

    # 1. RSS FIRST (IMPORTANT FIX)
    feed = parse_rss(url)
    if feed:
        print("✅ RSS detected")
        return ("rss", feed, url)

    # 2. sitemap (FAST)
    sitemap_links = try_sitemap(url)
    if sitemap_links:
        print("🧠 Using sitemap")
        return ("sitemap", sitemap_links, url)

    # 3. HTML crawl
    links = crawl_html(url)
    if links:
        print("🧠 HTML crawl")
        return ("html", links, url)

    # 4. JS fallback ONLY IF NECESSARY
    print("🧠 JS fallback")
    js_links = crawl_js(url)

    return ("js", js_links, url)


# -----------------------------
# MAIN PIPELINE
# -----------------------------
def run(urls):
    all_news = []

    for url in urls:

        mode, data, base = resolve(url)

        # ---------------- RSS ----------------
        if mode == "rss":
            for entry in data.entries:
                link = normalize_url(entry.get("link", ""))
                if link in seen_articles:
                    continue
                seen_articles.add(link)

                all_news.append({
                    "title": entry.get("title", ""),
                    "link": link,
                    "summary": entry.get("summary", "")[:500],
                    "source": base
                })

        # ---------------- SITEMAP / HTML / JS ----------------
        else:
            for link in data:

                if link in seen_articles:
                    continue
                seen_articles.add(link)

                text = extract_article(link)
                if not text or len(text) < 200:
                    continue

                all_news.append({
                    "title": link.split("/")[-1],
                    "link": link,
                    "summary": text[:800],
                    "source": base
                })

    print(f"\n✅ Collected: {len(all_news)}")

    with open("news.json", "w", encoding="utf-8") as f:
        json.dump(all_news, f, ensure_ascii=False, indent=2)


# -----------------------------
# RUN
# -----------------------------
if __name__ == "__main__":
    with open("rss_feeds.txt") as f:
        urls = [x.strip() for x in f if x.strip()]

    run(urls)
