import feedparser
import httpx
import json
import trafilatura
from bs4 import BeautifulSoup
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse, urljoin, urlunparse, parse_qsl, urlencode
from fake_useragent import UserAgent
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
ua = UserAgent()

# -----------------------------
# URL normalization
# -----------------------------
def normalize_url(url):
    try:
        parsed = urlparse(url)
        clean_query = [(k, v) for k, v in parse_qsl(parsed.query) if not k.startswith("utm")]
        return urlunparse(parsed._replace(query=urlencode(clean_query)))
    except Exception:
        return url

# -----------------------------
# FAST HTTP FETCH
# -----------------------------
def fetch_http(url):
    try:
        headers = {
            "User-Agent": ua.random,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.google.com/"
        }

        with httpx.Client(timeout=10, follow_redirects=True, headers=headers) as client:
            r = client.get(url)
            if r.status_code == 200 and len(r.text) > 500:
                return r.text
    except Exception:
        pass
    return None

# -----------------------------
# PLAYWRIGHT FALLBACK (STEALTH)
# -----------------------------
def fetch_playwright(url):
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
            )

            context = browser.new_context(
                user_agent=ua.random,
                viewport={"width": 1280, "height": 800}
            )

            page = context.new_page()
            page.goto(url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(1500)

            html = page.content()
            browser.close()
            return html

    except Exception:
        return None

# -----------------------------
# UNIFIED FETCHER (ANTI-BOT LAYER)
# -----------------------------
def fetch(url):
    html = fetch_http(url)
    if html:
        return html

    return fetch_playwright(url)

# -----------------------------
# RSS VALIDATION
# -----------------------------
def is_valid_feed(feed):
    return feed and hasattr(feed, "entries") and len(feed.entries) > 0

def try_feed(url):
    html = fetch(url)
    if not html or "<html" in html.lower():
        return None

    feed = feedparser.parse(html)
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
        v = getattr(entry, key, None)
        if v:
            return datetime(*v[:6], tzinfo=timezone.utc)

    for key in ["published", "updated"]:
        v = getattr(entry, key, None)
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
# ARTICLE EXTRACTION
# -----------------------------
def extract_article(url):
    html = fetch(url)
    if not html:
        return ""

    return trafilatura.extract(
        html,
        include_comments=False,
        include_tables=False,
        favor_recall=True
    ) or ""

def get_summary(entry, link):
    text = (
        entry.get("summary")
        or entry.get("description")
        or (entry.content[0].value if hasattr(entry, "content") and entry.content else "")
        or ""
    )

    text = BeautifulSoup(text, "html.parser").get_text().strip()

    if not text and link:
        return extract_article(link)

    return text

# -----------------------------
# FALLBACK CRAWLER
# -----------------------------
def crawl_site(url, limit=10):
    html = fetch(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    links = set()

    for a in soup.find_all("a", href=True):
        full = urljoin(url, a["href"])

        if urlparse(full).netloc != urlparse(url).netloc:
            continue

        if any(x in full.lower() for x in ["login", "signup", "tag", "category", "contact", "#"]):
            continue

        links.add(full)

    results = []

    for link in list(links)[:limit]:
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

cutoff = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

all_news = []

for url in urls:
    feed_url, feed = resolve_feed(url)

    if not feed:
        print(f"🧠 No RSS → crawling: {url}")
        articles = crawl_site(url)
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
