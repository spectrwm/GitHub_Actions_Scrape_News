import feedparser
import json
from datetime import datetime, time, timezone
from bs4 import BeautifulSoup

# Set cutoff to today at 00:00 UTC
now = datetime.now(timezone.utc)
cutoff_time = datetime.combine(now.date(), time(0, 0), tzinfo=timezone.utc)

# Load RSS feed URLs
with open("rss_feeds.txt") as f:
    urls = [line.strip() for line in f if line.strip()]

# Collect news
all_news = []

for url in urls:
    feed = feedparser.parse(url)

    for entry in feed.entries:
        if 'published_parsed' in entry and entry.published_parsed:
            published_dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)

            if published_dt: #> cutoff_time:
                summary = ""

                if "summary" in entry:
                    summary = entry.summary
                elif "description" in entry:
                    summary = entry.description
                elif "content" in entry and len(entry.content) > 0:
                    summary = entry.content[0].value

                # Clean HTML
                summary = BeautifulSoup(summary, "html.parser").get_text()

                all_news.append({
                    "title": entry.get("title"),
                    "link": entry.get("link"),
                    "published": published_dt.isoformat(),
                    "source": feed.feed.get("title", url),
                    "summary": summary
                })

# Save to JSON
with open("news.json", "w", encoding="utf-8") as f:
    json.dump({
        "cutoff": cutoff_time.isoformat(),
        "last_updated": datetime.utcnow().isoformat() + "Z",
        "news": all_news
    }, f, indent=2, ensure_ascii=False)

print(f"✅ Scraped {len(all_news)} items published after {cutoff_time.isoformat()}.")
