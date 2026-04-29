import feedparser
import requests
from bs4 import BeautifulSoup
import json
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import trafilatura
from urllib.parse import urlparse, urljoin, urlunparse, parse_qsl, urlencode

from playwright.sync_api import sync_playwright


# -----------------------------
# CONFIG
# -----------------------------
COMMON_RSS_PATHS = [
    "/rss",
    "/feed",
    "/rss.xml",
    "/feed.xml",
    "/?feed=rss2",
    "/?format=rss"
]

seen_feeds = set()
seen_articles = set()


# -----------------------------
# PLAYWRIGHT (persistent browser)
# -----------------------------
_playwright = sync_playwright().start()
_browser = _playwright.chromium.launch(
    headless=True,
    args=["--no-sandbox", "--disable-dev-shm-usage"]
)


def fetch_rendered(url, wait_ms=1500):
    try:
        page = _browser.new_page()
        page.goto(url, timeout=30000)
        page.wait_for_timeout(wait_ms)

        html = page.content()
        page.close()

        return html

    except Exception as e:
        print(f"❌ Playwright failed: {url} → {e}")
        return None


def extract_article_playwright(url):
    html = fetch_rendered(url)

    if not html:
        return ""

    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    paragraphs = soup.find_all("p")
    return "\n".join(p.get_text(" ", strip=True) for p in paragraphs).strip()


def extract_links_from_js_site(base_url, limit=15):
    html = fetch_rendered(base_url)

    if not html:
        return []

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

    return list(links)[:limit]


# -----------------------------
# HTTP FETCH
# -----------------------------
def fetch(url):
    try:
        r = requests.get(
            url,
            timeout=15,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "text/html,application/xml;q=0.9,*/*;q=0.8"
            }
        )
        if r.status_code != 200:
            return None
        return r.text
    except Exception:
        return None


# -----------------------------
# NORMALIZE URL
# -----------------------------
def normalize_url(url):
    try:
        parsed = urlparse(url)
        clean_query = [
            (k, v) for k, v in parse_qsl(parsed.query)
            if not k.startswith("utm")
        ]
        return urlunparse(parsed._replace(query=urlencode(clean_query)))
    except Exception:
        return url


# -----------------------------
# RSS CHECK
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
def discover_rss(url):
    html = fetch(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    feeds = set()

    for link in soup.find_all("link"):
        if link.get("type") in ["application/rss+xml", "application/atom+xml"]:
            href = link.get("href")
            if href:
                feeds.add(urljoin(url, href))

    return list(feeds)


def generate_candidates(url):
    parsed = urlparse(url)
    root = f"{parsed.scheme}://{parsed.netloc}"
    return [root + p for p in COMMON_RSS_PATHS]


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
# DATE PARSING
# -----------------------------
def parse_date(entry):
    for key in ["published_parsed", "updated_parsed"]:
        value = getattr(entry, key, None)
        if value:
            return datetime(*value[:6], tzinfo=timezone.utc)

    for key in ["published", "updated"]:
        value = getattr(entry, key, None)
        if value:
            try:
                dt = parsedate_to_datetime(value)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except Exception:
                pass

    return None


# -----------------------------
# ARTICLE EXTRACTION
# -----------------------------
def get_summary(entry, link=None):
    text = (
        entry.get("summary")
        or entry.get("description")
        or (entry.content[0].value if hasattr(entry, "content") and entry.content else "")
        or ""
    )

    text = BeautifulSoup(text, "html.parser").get_text().strip()

    if not text and link:
        try:
            downloaded = trafilatura.fetch_url(link)
            if downloaded:
                extracted = trafilatura.extract(downloaded)
                if extracted:
                    return extracted.strip()
        except Exception:
            pass

    return text


# -----------------------------
# STATIC CRAWLER
# -----------------------------
def crawl_site(url, limit=15):
    base = url.rstrip("/")

    pages = [
        base,
        base + "/news",
        base + "/latest",
        base + "/category/news",
        base + "/articles",
        base + "/post"
    ]

    links = set()

    for page in pages:
        html = fetch(page)
        if not html:
            continue

        soup = BeautifulSoup(html, "html.parser")

        for a in soup.find_all("a", href=True):
            full = urljoin(page, a["href"])

            if urlparse(full).netloc != urlparse(base).netloc:
                continue

            if any(x in full.lower() for x in [
                "login", "signup", "tag", "category",
                "privacy", "about", "contact", "#"
            ]):
                continue

            links.add(full)

    results = []

    for link in list(links)[:limit]:
        link = normalize_url(link)

        if link in seen_articles:
            continue
        seen_articles.add(link)

        content = get_summary({"summary": ""}, link)

        if not content:
            continue

        results.append({
            "title": link.split("/")[-1],
            "link": link,
            "published": None,
            "source": base,
            "summary": content[:800]
        })

    return results


# -----------------------------
# MAIN PIPELINE
# -----------------------------
with open("rss_feeds.txt") as f:
    urls = [u.strip() for u in f if u.strip()]

now = datetime.now(timezone.utc)
cutoff = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)

all_news = []

for url in urls:
    feed_url, feed = resolve_feed(url)

    if not feed:
        print(f"🧠 No RSS → crawling: {url}")
        articles = crawl_site(url)

        if not articles:
            print(f"🧠 Playwright fallback: {url}")

            links = extract_links_from_js_site(url)

            for link in links:
                if link in seen_articles:
                    continue
                seen_articles.add(link)

                content = extract_article_playwright(link)

                if not content or len(content) < 200:
                    continue

                all_news.append({
                    "title": link.split("/")[-1],
                    "link": link,
                    "published": None,
                    "source": url,
                    "summary": content[:800]
                })

            continue

        all_news.extend(articles)
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
            "title": entry.get("title", "").strip(),
            "link": link,
            "published": published.isoformat() if published else None,
            "source": getattr(feed, "feed", {}).get("title", url),
            "summary": get_summary(entry, link)
        })


# -----------------------------
# OUTPUT
# -----------------------------
print(f"✅ Collected {len(all_news)} articles")

with open("news.json", "w", encoding="utf-8") as f:
    json.dump({
        "cutoff": cutoff.isoformat(),
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "news": all_news
    }, f, indent=2, ensure_ascii=False)


# -----------------------------
# CLEANUP PLAYWRIGHT
# -----------------------------
_browser.close()
_playwright.stop()
