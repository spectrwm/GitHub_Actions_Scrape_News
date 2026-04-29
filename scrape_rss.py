import feedparser
import json
from datetime import datetime, time, timezone
from bs4 import BeautifulSoup

# Cutoff (optional filtering)
now = datetime.now(timezone.utc)
cutoff_time = datetime.combine(now.date(), time(0, 0), tzinfo=timezone.utc)

with open("rss_feeds.txt") as f:
    urls = [line.strip() for line in f if line.strip()]


def parse_date(entry):
    """
    Try all possible RSS/Atom date fields safely
    Returns datetime or None
    """
    # structured parsed dates
    for key in ["published_parsed", "updated_parsed"]:
        if hasattr(entry, key):
            value = getattr(entry, key)
            if value:
                return datetime(*value[:6], tzinfo=timezone.utc)

    # fallback string parsing
    for key in ["published", "updated"]:
        if hasattr(entry, key):
            try:
                return datetime(*feedparser._parse_date(getattr(entry, key))[:6], tzinfo=timezone.utc)
            except Exception:
                pass

    return None


def get_summary(entry):
    summary = (
        entry.get("summary")
        or entry.get("description")
        or (entry.content[0].value if hasattr(entry, "content") and entry.content else "")
        or ""
    )
    return BeautifulSoup(summary, "html.parser").get_text().strip()


all_news = []

for url in urls:
    feed = feedparser.parse(url)

    if not feed.entries:
        print(f"⚠️ Empty feed: {url}")
        continue

    for entry in feed.entries:
        published_dt = parse_date(entry)

        # optional filtering (safe even if None)
        if published_dt and published_dt <= cutoff_time:
            continue

        all_news.append({
            "title": entry.get("title", "").strip(),
            "link": entry.get("link", ""),
            "published": published_dt.isoformat() if published_dt else None,
            "source": feed.feed.get("title", url),
            "summary": get_summary(entry)
        })

print(f"✅ Collected {len(all_news)} articles")

with open("news.json", "w", encoding="utf-8") as f:
    json.dump({
        "cutoff": cutoff_time.isoformat(),
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "news": all_news
    }, f, indent=2, ensure_ascii=False)
