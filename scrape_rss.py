import feedparser
import requests
import json
import time
import re
from datetime import datetime, timezone
from urllib.parse import urlparse, urljoin, urlunparse, parse_qsl, urlencode
from bs4 import BeautifulSoup
from email.utils import parsedate_to_datetime

# -----------------------------
# OPTIONAL: PLAYWRIGHT
# -----------------------------
from playwright.sync_api import sync_playwright

# -----------------------------
# GLOBAL STATE
# -----------------------------
seen_feeds = set()
seen_articles = set()

# -----------------------------
# URL NORMALIZATION
# -----------------------------
def normalize_url(url):
    try:
        parsed = urlparse(url)
        clean_query = [(k, v) for k, v in parse_qsl(parsed.query)
                       if not k.startswith("utm")]
        return urlunparse(parsed._replace(query=urlencode(clean_query)))
    except:
        return url

# -----------------------------
# FETCH (STATIC)
# -----------------------------
def fetch(url):
    try:
        r = requests.get(
            url,
            timeout=15,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        if r.status_code != 200:
            return None
        return r.text
    except:
        return None

# -----------------------------
# PLAYWRIGHT FETCH
# -----------------------------
def fetch_rendered(url, wait=2000):
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=30000)
            page.wait_for_timeout(wait)
            html = page.content()
            browser.close()
            return html
    except:
        return None

# -----------------------------
# RSS CHECK
# -----------------------------
def is_valid_feed(feed):
    return feed and hasattr(feed, "entries") and len(feed.entries) > 0

def try_feed(url):
    text = fetch(url)
    if not text:
        return None
    feed = feedparser.parse(text)
    return feed if is_valid_feed(feed) else None

# -----------------------------
# SITEMAP SCRAPER (IMPORTANT FIX)
# -----------------------------
def try_sitemap(base_url):
    candidates = [
        base_url.rstrip("/") + "/sitemap.xml",
        base_url.rstrip("/") + "/sitemap_index.xml"
    ]

    for url in candidates:
        xml = fetch(url)
        if not xml:
            continue

        soup = BeautifulSoup(xml, "xml")
        links = [loc.text.strip() for loc in soup.find_all("loc")]

        # filter likely articles
        articles = [
            l for l in links
            if any(x in l.lower() for x in ["news", "article", "post", "202", "blog"])
        ]

        if articles:
            return articles[:20]

    return []

# -----------------------------
# HOMEPAGE LINK SCRAPER
# -----------------------------
def extract_links_static(base_url, html):
    soup = BeautifulSoup(html, "html.parser")
    links = set()

    for a in soup.find_all("a", href=True):
        full = urljoin(base_url, a["href"])

        if urlparse(full).netloc != urlparse(base_url).netloc:
            continue

        if any(x in full.lower() for x in [
            "login", "signup", "tag", "category",
            "privacy", "about", "contact", "#"
        ]):
            continue

        links.add(full)

    return list(links)

# -----------------------------
# PLAYWRIGHT ARTICLE EXTRACTION
# -----------------------------
def extract_links_js(base_url):
    html = fetch_rendered(base_url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")

    selectors = [
        "article a",
        "h2 a",
        "h3 a",
        ".post a",
        ".entry a"
    ]

    links = set()

    for sel in selectors:
        for a in soup.select(sel):
            href = a.get("href")
            if not href:
                continue

            full = urljoin(base_url, href)

            if urlparse(full).netloc == urlparse(base_url).netloc:
                links.add(full)

    return list(links)

# -----------------------------
# ARTICLE EXTRACTION
# -----------------------------
def extract_article(url):
    html = fetch_rendered(url)
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    text = "\n".join(p.get_text() for p in soup.find_all("p"))

    return text.strip() if text else None

# -----------------------------
# RESOLVE RSS
# -----------------------------
def resolve_feed(url):
    feed = try_feed(url)
    if feed:
        return url, feed
    return None, None

# -----------------------------
# SITEMAP FALLBACK
# -----------------------------
def sitemap_fallback(url):
    return try_sitemap(url)

# -----------------------------
# PLAYWRIGHT FALLBACK
# -----------------------------
def playwright_fallback(url):
    links = extract_links_js(url)

    results = []

    for link in links:
        link = normalize_url(link)

        if link in seen_articles:
            continue
        seen_articles.add(link)

        content = extract_article(link)

        if not content or len(content) < 200:
            continue

        results.append({
            "title": link.split("/")[-1],
            "link": link,
            "published": None,
            "source": url,
            "summary": content[:800]
        })

    return results

# -----------------------------
# MAIN PIPELINE
# -----------------------------
with open("rss_feeds.txt") as f:
    urls = [u.strip() for u in f if u.strip()]

all_news = []

for url in urls:

    print(f"\n🔎 Processing: {url}")

    # 1. RSS
    feed_url, feed = resolve_feed(url)

    if feed:
        for entry in feed.entries:
            link = normalize_url(entry.get("link", ""))

            if link in seen_articles:
                continue
            seen_articles.add(link)

            all_news.append({
                "title": entry.get("title", ""),
                "link": link,
                "published": entry.get("published"),
                "source": feed.feed.get("title", url),
                "summary": entry.get("summary", "")
            })

        continue

    # 2. SITEMAP
    print(f"🧠 No RSS → sitemap: {url}")
    sitemap_links = sitemap_fallback(url)

    if sitemap_links:
        for link in sitemap_links:
            link = normalize_url(link)

            if link in seen_articles:
                continue
            seen_articles.add(link)

            content = extract_article(link)

            if content:
                all_news.append({
                    "title": link.split("/")[-1],
                    "link": link,
                    "published": None,
                    "source": url,
                    "summary": content[:800]
                })
        continue

    # 3. PLAYWRIGHT
    print(f"🧠 No sitemap → JS crawl: {url}")
    articles = playwright_fallback(url)

    all_news.extend(articles)

# -----------------------------
# SAVE
# -----------------------------
print(f"\n✅ Total articles: {len(all_news)}")

with open("news.json", "w", encoding="utf-8") as f:
    json.dump({
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "news": all_news
    }, f, indent=2, ensure_ascii=False)
