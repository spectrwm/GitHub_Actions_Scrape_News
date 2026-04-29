import feedparser
import requests
from bs4 import BeautifulSoup
import json
from datetime import datetime, timezone
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


# -----------------------------
# URL normalization (remove tracking params)
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
# HTTP fetch
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
# RSS validation
# -----------------------------
def is_valid_feed(feed):
    return feed and hasattr(feed, "entries") and len(feed.entries) > 0


def looks_like_html(text):
    return text and ("<html" in text.lower() or "<!doctype html" in text.lower())


# -----------------------------
# Try RSS URL
# -----------------------------
def try_feed(url):
    text = fetch(url)
    if not text or looks_like_html(text):
        return None

    feed = feedparser.parse(text)
    return feed if is_valid_feed(feed) else None


# -----------------------------
# RSS discovery from homepage
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


# -----------------------------
# fallback RSS candidates
# -----------------------------
def generate_candidates(url):
    parsed = urlparse(url)
    root = f"{parsed.scheme}://{parsed.netloc}"

    return [root + path for path in COMMON_RSS_PATHS]


# -----------------------------
# resolve feed
# -----------------------------
def resolve_feed(url):
    # direct
    feed = try_feed(url)
    if feed:
        return url, feed

    # common paths
    for c in generate_candidates(url):
        if c in seen_feeds:
            continue
        seen_feeds.add(c)

        feed = try_feed(c)
        if feed:
            return c, feed

    # homepage discovery
    for c in discover_rss(url):
        if c in seen_feeds:
            continue
        seen_feeds.add(c)

        feed = try_feed(c)
        if feed:
            return c, feed

    return None, None


# -----------------------------
# safe date parsing
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
# article summary / extraction
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
# fallback crawler (no RSS sites)
# -----------------------------
def crawl_site(url, limit=15):
    base = url.rstrip("/")

    # 🔥 multiple likely article hubs
    start_pages = [
        base,
        base + "/news",
        base + "/latest",
        base + "/category/news",
        base + "/articles",
        base + "/post",
        base + "/2026",
        base + "/2025"
    ]

    links = set()

    for page in start_pages:
        html = fetch(page)
        if not html:
            continue

        soup = BeautifulSoup(html, "html.parser")

        for a in soup.find_all("a", href=True):
            full = urljoin(page, a["href"])

            if urlparse(full).netloc != urlparse(base).netloc:
                continue

            # filter noise
            if any(x in full.lower() for x in [
                "login", "signup", "tag", "category",
                "privacy", "about", "contact", "#", "author"
            ]:
                continue

            # keep likely articles
            if len(full.split("/")) < 4:
                continue

            links.add(full)

    # rank (important!)
    links = sorted(list(links), key=lambda x: (
        ("2026" in x or "2025" in x),
        ("news" in x),
        -len(x.split("/"))
    ), reverse=True)

    results = []

    for link in links[:limit]:
        link = normalize_url(link)

        if link in seen_articles:
            continue
        seen_articles.add(link)

        content = extract_full_article(link)

        if not content or len(content) < 200:
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
        all_news.extend(crawl_site(url))
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
# SAVE OUTPUT
# -----------------------------
print(f"✅ Collected {len(all_news)} articles")

with open("news.json", "w", encoding="utf-8") as f:
    json.dump({
        "cutoff": cutoff.isoformat(),
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "news": all_news
    }, f, indent=2, ensure_ascii=False)
