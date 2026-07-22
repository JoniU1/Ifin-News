#!/usr/bin/env python3
"""
Aggregates headlines from four Israeli financial news sites into one RSS feed.

Sources:
  - Calcalist   (https://www.calcalist.co.il/allnews)
  - Bizportal   (https://www.bizportal.co.il/todays_headlines)
  - Globes      (https://www.globes.co.il/news/home.aspx?fid=9473)
  - TheMarker   (https://www.themarker.com/misc/all-headlines)

Output: feed.xml  (RSS 2.0), plus index.html landing page.

Design notes:
  - Each site has its own parser. If one site changes layout or blocks the
    request, the others still work and the run still succeeds (errors are
    caught per-site and logged, not fatal).
  - Two request strategies are tried per site: a normal requests call, and
    (if that 403s) a cloudscraper call that mimics a browser more closely.
    This handles the "cloud IP gets blocked" risk without a headless browser.
"""

import sys
import time
import html
import datetime as dt
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from feedgen.feed import FeedGenerator

try:
    import cloudscraper
    _HAVE_CLOUDSCRAPER = True
except Exception:
    _HAVE_CLOUDSCRAPER = False

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")

HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "he-IL,he;q=0.9,en;q=0.8",
}

TIMEOUT = 25


def fetch(url):
    """Fetch a URL. Try plain requests first; if blocked, try cloudscraper."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code == 200 and len(r.text) > 2000:
            r.encoding = r.apparent_encoding or "utf-8"
            return r.text
        print(f"    plain GET returned {r.status_code} (len={len(r.text)}), "
              f"trying cloudscraper", file=sys.stderr)
    except Exception as e:
        print(f"    plain GET error: {e}", file=sys.stderr)

    if _HAVE_CLOUDSCRAPER:
        try:
            scraper = cloudscraper.create_scraper(
                browser={"browser": "chrome", "platform": "windows", "mobile": False}
            )
            r = scraper.get(url, timeout=TIMEOUT)
            if r.status_code == 200:
                r.encoding = r.apparent_encoding or "utf-8"
                return r.text
            print(f"    cloudscraper returned {r.status_code}", file=sys.stderr)
        except Exception as e:
            print(f"    cloudscraper error: {e}", file=sys.stderr)
    return None


def clean(text):
    if not text:
        return ""
    return html.unescape(" ".join(text.split())).strip()


# ---------------------------------------------------------------------------
# Per-site parsers. Each returns a list of dicts: {title, link, summary, source}
# ---------------------------------------------------------------------------

def parse_bizportal(html_text, base):
    soup = BeautifulSoup(html_text, "lxml")
    items, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/news/article/" not in href:
            continue
        title = clean(a.get_text())
        if len(title) < 15:          # skip image-only / label links
            continue
        link = urljoin(base, href)
        if link in seen:
            continue
        seen.add(link)
        items.append({"title": title, "link": link, "summary": "",
                      "source": "Bizportal"})
    return items


def parse_calcalist(html_text, base):
    soup = BeautifulSoup(html_text, "lxml")
    items, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/article/" not in href:
            continue
        title = clean(a.get_text())
        if len(title) < 15:
            continue
        link = urljoin(base, href)
        if link in seen:
            continue
        seen.add(link)
        items.append({"title": title, "link": link, "summary": "",
                      "source": "Calcalist"})
    return items


def parse_globes(html_text, base):
    soup = BeautifulSoup(html_text, "lxml")
    items, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "article.aspx?did=" not in href:
            continue
        title = clean(a.get_text())
        if len(title) < 15:
            continue
        link = urljoin(base, href)
        if link in seen:
            continue
        seen.add(link)
        items.append({"title": title, "link": link, "summary": "",
                      "source": "Globes"})
    return items


def parse_themarker(html_text, base):
    soup = BeautifulSoup(html_text, "lxml")
    items, seen = [], set()
    # TheMarker headlines sit in <h3> tags wrapping an <a>
    for h in soup.find_all(["h3", "h2"]):
        a = h.find("a", href=True)
        if not a:
            continue
        href = a["href"]
        if "/ty-article" not in href and "/2026" not in href and "/2025" not in href:
            continue
        title = clean(a.get_text())
        if len(title) < 15:
            continue
        link = urljoin(base, href)
        if link in seen:
            continue
        seen.add(link)
        items.append({"title": title, "link": link, "summary": "",
                      "source": "TheMarker"})
    return items


SOURCES = [
    ("Calcalist", "https://www.calcalist.co.il/allnews", parse_calcalist),
    ("Bizportal", "https://www.bizportal.co.il/todays_headlines", parse_bizportal),
    ("Globes",    "https://www.globes.co.il/news/home.aspx?fid=9473", parse_globes),
    ("TheMarker", "https://www.themarker.com/misc/all-headlines", parse_themarker),
]


def main():
    all_items = []
    for name, url, parser in SOURCES:
        print(f"[{name}] fetching {url}")
        page = fetch(url)
        if not page:
            print(f"[{name}] FAILED to fetch (site may block cloud IPs). Skipping.",
                  file=sys.stderr)
            continue
        try:
            got = parser(page, url)
            print(f"[{name}] parsed {len(got)} headlines")
            all_items.extend(got)
        except Exception as e:
            print(f"[{name}] parse error: {e}", file=sys.stderr)
        time.sleep(1)

    # Deduplicate across sources by link
    seen, deduped = set(), []
    for it in all_items:
        if it["link"] in seen:
            continue
        seen.add(it["link"])
        deduped.append(it)

    print(f"TOTAL: {len(deduped)} unique headlines")

    # Build RSS
    fg = FeedGenerator()
    fg.title("חדשות כלכלה — Israeli Finance Aggregator")
    fg.link(href="https://example.github.io/israeli-finance-feed/feed.xml", rel="self")
    fg.description("Combined headlines: Calcalist, Bizportal, Globes, TheMarker")
    fg.language("he")
    now = dt.datetime.now(dt.timezone.utc)
    fg.lastBuildDate(now)

    # feedgen prepends entries in reverse, so add in reverse to keep order
    for it in reversed(deduped):
        fe = fg.add_entry()
        fe.title(f"[{it['source']}] {it['title']}")
        fe.link(href=it["link"])
        fe.guid(it["link"], permalink=True)
        if it["summary"]:
            fe.description(it["summary"])
        else:
            fe.description(it["title"])
        fe.pubDate(now)

    fg.rss_file("feed.xml", pretty=True)
    print("Wrote feed.xml")

    # Minimal landing page
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(f"""<!doctype html><html lang="he" dir="rtl"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Israeli Finance Feed</title></head><body style="font-family:sans-serif;max-width:640px;margin:2rem auto;padding:0 1rem">
<h1>מצרף חדשות כלכלה</h1>
<p>פיד RSS מאוחד: כלכליסט, ביזפורטל, גלובס, דה־מרקר.</p>
<p>כתובת הפיד להוספה לקורא RSS:</p>
<p><a href="feed.xml">feed.xml</a></p>
<p>עודכן לאחרונה: {now.strftime('%Y-%m-%d %H:%M UTC')} — {len(deduped)} כותרות</p>
</body></html>""")
    print("Wrote index.html")

    if len(deduped) == 0:
        print("WARNING: zero headlines — all sources failed.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
