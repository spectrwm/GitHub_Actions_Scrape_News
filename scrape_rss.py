import feedparser
import requests
from bs4 import BeautifulSoup
import json
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import trafilatura
from urllib.parse import urlparse, urljoin, urlunparse, parse_qsl, urlencode
import time
from playwright.sync_api import sync_playwright


# -----------------------------
# GLOBAL STATE
# -----------------------------
seen_feeds = set()
seen_articles = set()


# -----------------------------
# HEADERS / FETCH
# -----------------------------
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122 Safari/537.36"
}

def fetch(url, retries=3):
    for i in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=12)
            if r.status_code == 200:
                return r.text
        except:
            time.sleep(1.5 * (i + 1))
    return None


# -----------------------------
# NORMALIZATION
# -----------------------------
def normalize_url(url):
    try:
        parsed = urlparse(url)
        clean_query = [(k, v) for k, v in parse_qsl(parsed.query) if not k.startswith("utm")]
        return urlunparse(parsed._replace(query=urlencode(clean_query)))
    except:
        return url


# -----------------------------
# ARTICLE EXTRACTION
# -----------------------------
def extract_full_article(url):
    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return ""

        text = trafilatura.extract(
            downloaded,
            include_comments=False,
            include_tables=False,
            favor_recall=True
        )

        return text.strip() if text else ""

    except:
        return ""


# -----------------------------
# RSS VALIDATION
# -----------------------------
def is_valid_feed(feed):
    return feed and hasattr(feed, "entries") and len(feed.entries) > 0


def looks_like_html(text):
    return text and ("<html" in text.lower() or "<!doctype html" in text.lower())


def try_feed(url):
    text = fetch(url)
    if not text or looks_like_html(text):
        return None

    feed = feedparser.parse(text)
    return feed if is_valid_feed(feed) else None


# -----------------------------
# RSS DISCOVERY
# -----------------------------
COMMON_RSS_PATHS = [
    "/rss",
    "/feed",
    "/rss.xml",
    "/feed.xml",
    "/?feed=rss2",
    "/?format=rss"
]

def generate_candidates(url):
    parsed = urlparse(url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    return [root + p for p in COMMON_RSS_PATHS]


def discover_rss(url):
    html = fetch(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    feeds = set()

    for link in soup.find_all("link"):
        if link.get("type") in ["application/rss+xml", "application/atom+xml"]:
            if link.get("href"):
                feeds.add(urljoin(url, link["href"]))

    return list(feeds)


# -----------------------------
# CMS DETECTION
# -----------------------------
def detect_cms(html):
    if not html:
        return None
    if "wp-content" in html or "wordpress" in html.lower():
        return "wordpress"
    if "ghost" in html.lower():
        return "ghost"
    if "drupal" in html.lower():
        return "drupal"
    return None


def try_cms_api(base):
    apis = [
        base + "/wp-json/wp/v2/posts",
        base + "/ghost/api/content/posts/",
        base + "/jsonapi/node/article"
    ]

    for api in apis:
        try:
            r = requests.get(api, headers=HEADERS, timeout=10)
            if r.status_code == 200 and len(r.text) > 100:
                return r.json()
        except:
            pass

    return None


# -----------------------------
# SITEMAP
# -----------------------------
def try_sitemap(base):
    urls = [
        base + "/sitemap.xml",
        base + "/sitemap_index.xml"
    ]

    for sm in urls:
        xml = fetch(sm)
        if xml and "<loc>" in xml:
            soup = BeautifulSoup(xml, "xml")
            return [x.text for x in soup.find_all("loc")]

    return []


# -----------------------------
# PLAYWRIGHT CRAWLER
# -----------------------------
def crawl_js(base, limit=15):
    results = []

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            page.goto(base, timeout=30000)
            page.wait_for_timeout(3000)

            html = page.content()
            soup = BeautifulSoup(html, "html.parser")

            links = set()

            for a in soup.find_all("a", href=True):
                full = urljoin(base, a["href"])

                if urlparse(full).netloc != urlparse(base).netloc:
                    continue

                if any(x in full.lower() for x in ["tag", "category", "login", "#"]):
                    continue

                links.add(full)

            for link in list(links)[:limit]:
                if link in seen_articles:
                    continue
                seen_articles.add(link)

                page.goto(link, timeout=20000)
                page.wait_for_timeout(1500)

                article_html = page.content()
                article_soup = BeautifulSoup(article_html, "html.parser")

                text = "\n".join([p.get_text() for p in article_soup.find_all("p")])

                if len(text) < 200:
                    continue

                results.append({
                    "title": link.split("/")[-1],
                    "link": link,
                    "summary": text[:800],
                    "source": base
                })

            browser.close()

    except Exception as e:
        print("Playwright error:", e)

    return results


# -----------------------------
# FALLBACK ENGINE
# -----------------------------
def crawl_site_fallback(base):
    print(f"🧠 Fallback: {base}")

    html = fetch(base)
    cms = detect_cms(html)

    # CMS API
    if cms:
        api = try_cms_api(base)
        if api:
            print("⚙️ CMS API used")
            return api

    # Sitemap
    sitemap = try_sitemap(base)
    if sitemap:
        print("🧭 Sitemap used")

        results = []
        for link in sitemap[:15]:
            if link in seen_articles:
                continue
            seen_articles.add(link)

            text = extract_full_article(link)
            if not text:
                continue

            results.append({
                "title": link.split("/")[-1],
                "link": link,
                "summary": text[:800],
                "source": base
            })

        return results

    # JS fallback
    print("🧠 JS fallback")
    return crawl_js(base)


# -----------------------------
# RSS RESOLVER
# -----------------------------
def resolve_feed(url):
    feed = try_feed(url)
    if feed:
        return url, feed

    for c in generate_candidates(url):
        if c in seen_feeds:
            continue
        seen_feeds.add(c)

        feed = try_feed(c)
        if feed:
            return c, feed

    for c in discover_rss(url):
        if c in seen_feeds:
            continue
        seen_feeds.add(c)

        feed = try_feed(c)
        if feed:
            return c, feed

    return None, None


# -----------------------------
# DATE PARSER
# -----------------------------
def parse_date(entry):
    for k in ["published_parsed", "updated_parsed"]:
        v = getattr(entry, k, None)
        if v:
            return datetime(*v[:6], tzinfo=timezone.utc)

    for k in ["published", "updated"]:
        v = getattr(entry, k, None)
        if v:
            try:
                dt = parsedate_to_datetime(v)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except:
                pass

    return None


# -----------------------------
# SUMMARY
# -----------------------------
def get_summary(entry, link):
    text = (
        entry.get("summary")
        or entry.get("description")
        or ""
    )

    text = BeautifulSoup(text, "html.parser").get_text().strip()

    if not text:
        return extract_full_article(link)

    return text


# -----------------------------
# MAIN PIPELINE
# -----------------------------
with open("rss_feeds.txt") as f:
    urls = [x.strip() for x in f if x.strip()]

cutoff = datetime.now(timezone.utc)

all_news = []

for url in urls:
    print(f"\n🔎 Processing: {url}")

    feed_url, feed = resolve_feed(url)

    if not feed:
        print("🧠 No RSS → fallback")
        all_news.extend(crawl_site_fallback(url))
        continue

    for entry in feed.entries:
        link = normalize_url(entry.get("link", ""))

        if link in seen_articles:
            continue
        seen_articles.add(link)

        published = parse_date(entry)
        if published and published <= cutoff:
            continue

        all_news.append({
            "title": entry.get("title", ""),
            "link": link,
            "published": published.isoformat() if published else None,
            "source": feed_url,
            "summary": get_summary(entry, link)
        })


# -----------------------------
# SAVE
# -----------------------------
print("\n✅ Collected:", len(all_news))

with open("news.json", "w", encoding="utf-8") as f:
    json.dump(all_news, f, indent=2, ensure_ascii=False)
