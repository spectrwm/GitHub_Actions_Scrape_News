import feedparser
import requests
from bs4 import BeautifulSoup
import json
from urllib.parse import urlparse, urljoin, urlunparse, parse_qsl, urlencode
import trafilatura

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

# -----------------------------
# CONFIG
# -----------------------------
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; NewsBot/2.0)"
}

seen_articles = set()

# -----------------------------
# FETCH
# -----------------------------
def fetch(url, timeout=15):
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
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
        clean = [(k, v) for k, v in parse_qsl(parsed.query) if not k.startswith("utm")]
        return urlunparse(parsed._replace(query=urlencode(clean)))
    except:
        return url

# -----------------------------
# DETECT TYPE
# -----------------------------
def detect_type(text):
    if not text:
        return "unknown"

    t = text.lower()
    if "<rss" in t or "<feed" in t:
        return "rss"
    if "<html" in t:
        return "html"
    if "<?xml" in t:
        return "xml"

    return "unknown"

# -----------------------------
# RSS
# -----------------------------
def parse_rss(url):
    text = fetch(url)
    if not text:
        return None

    if detect_type(text) not in ["rss", "xml"]:
        return None

    feed = feedparser.parse(text)
    return feed if feed.entries else None

# -----------------------------
# WORDPRESS API (🔥 KEY)
# -----------------------------
def try_wordpress_api(base_url):
    api = base_url.rstrip("/") + "/wp-json/wp/v2/posts?per_page=20"

    try:
        r = requests.get(api, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return []

        data = r.json()
        results = []

        for post in data:
            link = post.get("link")
            title = post.get("title", {}).get("rendered", "")

            if link:
                results.append({
                    "title": BeautifulSoup(title, "html.parser").get_text(),
                    "link": link,
                    "summary": "",
                    "source": base_url
                })

        if results:
            print("🧠 WordPress API used")

        return results

    except:
        return []

# -----------------------------
# SITEMAP (ADVANCED)
# -----------------------------
def try_sitemap(base_url):
    root = f"{urlparse(base_url).scheme}://{urlparse(base_url).netloc}"

    sitemap_paths = [
        "/sitemap.xml",
        "/sitemap_index.xml",
        "/wp-sitemap.xml",
        "/post-sitemap.xml"
    ]

    links = []

    for path in sitemap_paths:
        xml = fetch(root + path)
        if not xml:
            continue

        soup = BeautifulSoup(xml, "xml")

        # nested sitemap
        if soup.find_all("sitemap"):
            for loc in soup.find_all("loc"):
                sub = fetch(loc.text)
                if not sub:
                    continue

                sub_soup = BeautifulSoup(sub, "xml")
                for u in sub_soup.find_all("loc"):
                    links.append(u.text)

        else:
            for loc in soup.find_all("loc"):
                links.append(loc.text)

    if links:
        print("🧠 Sitemap used")

    return links[:30]

# -----------------------------
# ARTICLE FILTER
# -----------------------------
def is_article_url(url):
    u = url.lower()

    bad = ["category", "tag", "page", "author", "login", "contact"]
    if any(b in u for b in bad):
        return False

    if any(x in u for x in ["202", "/news/", "/article/", "/post/"]):
        return True

    return len(u.split("/")) > 5

# -----------------------------
# HTML CRAWL
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

        if is_article_url(link):
            links.add(link)

    return list(links)[:20]

# -----------------------------
# JS CRAWL (LAST RESORT)
# -----------------------------
def crawl_js(url):
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
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

                if is_article_url(link):
                    links.add(link)

            return list(links)[:20]

    except PlaywrightTimeoutError:
        print("⏱️ Playwright timeout")
        return []
    except Exception as e:
        print(f"❌ Playwright error: {e}")
        return []

# -----------------------------
# ARTICLE EXTRACTION
# -----------------------------
def extract_article(url):
    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return ""
        text = trafilatura.extract(downloaded)
        return text.strip() if text else ""
    except:
        return ""

# -----------------------------
# SMART ROUTER
# -----------------------------
def resolve(url):
    print(f"\n🔎 Processing: {url}")

    # 1. RSS
    feed = parse_rss(url)
    if feed:
        print("✅ RSS detected")
        return ("rss", feed, url)

    # 2. WordPress API
    wp = try_wordpress_api(url)
    if wp:
        return ("wp", wp, url)

    # 3. Sitemap
    sm = try_sitemap(url)
    if sm:
        return ("links", sm, url)

    # 4. HTML
    html_links = crawl_html(url)
    if html_links:
        print("🧠 HTML crawl")
        return ("links", html_links, url)

    # 5. JS
    print("🧠 JS fallback")
    js_links = crawl_js(url)
    return ("links", js_links, url)

# -----------------------------
# MAIN PIPELINE
# -----------------------------
def run(urls):
    all_news = []

    for url in urls:
        mode, data, base = resolve(url)

        if mode == "rss":
            for e in data.entries:
                link = normalize_url(e.get("link", ""))

                if link in seen_articles:
                    continue
                seen_articles.add(link)

                all_news.append({
                    "title": e.get("title", ""),
                    "link": link,
                    "summary": e.get("summary", "")[:500],
                    "source": base
                })

        elif mode == "wp":
            all_news.extend(data)

        else:
            for link in data:
                link = normalize_url(link)

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
