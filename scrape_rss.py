import feedparser
import requests
from bs4 import BeautifulSoup
import json
from datetime import datetime, time, timezone
from email.utils import parsedate_to_datetime
import trafilatura
from urllib.parse import urlparse, urljoin, urlunparse, parse_qsl, urlencode




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

def normalize_url(url):
    try:
        parsed = urlparse(url)
        clean_query = [(k, v) for k, v in parse_qsl(parsed.query) if not k.startswith("utm")]
        return urlunparse(parsed._replace(query=urlencode(clean_query)))
    except Exception:
        return url
def extract_full_article(url):
    try:
        html = fetch(url)
        if not html:
            return ""

        text = trafilatura.extract(
            html,
            include_comments=False,
            include_tables=False,
            favor_precision=True
        )

        return text.strip() if text else ""

    except Exception:
        return ""
# -----------------------------
# HTTP fetch helper
# -----------------------------
def fetch(url):
    try:
        r = requests.get(
            url,
            timeout=15,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8"
            },
            allow_redirects=True
        )

        if r.status_code != 200:
            return None

        return r.text

    except Exception:
        return None


# -----------------------------
# Check if response is real RSS
# -----------------------------
def is_valid_feed(feed):
    return feed and hasattr(feed, "entries") and len(feed.entries) > 0


def looks_like_html(text):
    return "<html" in text.lower() or "<!doctype html" in text.lower()


# -----------------------------
# Try direct feed URL
# -----------------------------
def try_feed(url):
    text = fetch(url)
    if not text or looks_like_html(text):
        return None

    feed = feedparser.parse(text)

    if is_valid_feed(feed):
        return feed

    return None


# -----------------------------
# Auto RSS discovery from homepage
# -----------------------------
def discover_rss_from_homepage(url):
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

    # ALSO detect WordPress meta feeds
    for a in soup.find_all("a", href=True):
        if "rss" in a["href"] or "feed" in a["href"]:
            feeds.add(urljoin(url, a["href"]))

    return list(feeds)


# -----------------------------
# Generate fallback RSS candidates
# -----------------------------
def generate_candidates(url):
    parsed = urlparse(url)
    root = f"{parsed.scheme}://{parsed.netloc}"

    candidates = [url]

    for path in COMMON_RSS_PATHS:
        candidates.append(root + path)

    return candidates


# -----------------------------
# Smart resolver (core engine)
# -----------------------------
def resolve_feed(url):
    """
    Try:
    1. direct URL
    2. common RSS paths
    3. homepage discovery
    """

    # 1. direct
    feed = try_feed(url)
    if feed:
        print(f"✅ RSS found (direct): {url}")
        return url, feed

    # 2. common paths
    for candidate in generate_candidates(url):
    
        if candidate in seen_feeds:
            continue
        seen_feeds.add(candidate)
    
        feed = try_feed(candidate)
        if feed:
            print(f"✅ RSS found (fallback): {candidate}")
            return candidate, feed

    # 3. auto discovery
    discovered = discover_rss_from_homepage(url)
    
    for candidate in discovered:
    
        if candidate in seen_feeds:
            continue
        seen_feeds.add(candidate)
    
        feed = try_feed(candidate)
        if feed:
            print(f"✅ RSS found (discovered): {candidate}")
            return candidate, feed

    print(f"❌ No RSS found: {url}")
    return None, None

# Cutoff (optional filtering)
now = datetime.now(timezone.utc)
cutoff_time = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

with open("rss_feeds.txt") as f:
    urls = [line.strip() for line in f if line.strip()]


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
    
                # make timezone-safe
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
    
                return dt.astimezone(timezone.utc)
    
            except Exception:
                pass

    return None


def get_summary(entry, link=None):
    summary = (
        entry.get("summary")
        or entry.get("description")
        or (entry.content[0].value if hasattr(entry, "content") and entry.content else "")
        or ""
    )

    summary = BeautifulSoup(summary, "html.parser").get_text().strip()

    if not summary and link:
        return extract_full_article(link)

    return summary


all_news = []

for url in urls:
    resolved_url, feed = resolve_feed(url)


    if not feed:
        print(f"⚠️ Empty feed: {url}")
        continue

    for entry in feed.entries:
        url_hash = normalize_url(entry.get("link", ""))
        if url_hash in seen_articles:
            continue
        seen_articles.add(url_hash)
        published_dt = parse_date(entry)

        if published_dt and published_dt <= cutoff_time:
            continue

        all_news.append({
            "title": entry.get("title", "").strip(),
            "link": entry.get("link", ""),
            "published": published_dt.isoformat() if published_dt else None,
            "source": getattr(feed, "feed", {}).get("title") if hasattr(feed, "feed") else resolved_url,
            "summary": get_summary(entry, entry.get("link"))
        })

print(f"✅ Collected {len(all_news)} articles")

with open("news.json", "w", encoding="utf-8") as f:
    json.dump({
        "cutoff": cutoff_time.isoformat(),
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "news": all_news
    }, f, indent=2, ensure_ascii=False)
