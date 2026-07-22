#!/usr/bin/env python3
"""
Aggregates headlines from four Israeli financial news sites into one RSS feed
AND a browser reading page (index.html), sorted newest-first across all sites.

Sources: Calcalist, Bizportal, Globes, TheMarker.

Recency signals (best available per site):
  - TheMarker: date in URL (/YYYY-MM-DD/) + displayed HH:MM  -> real datetime
  - Globes:    numeric did=NNN in URL (rises over time)      -> recency proxy
  - Bizportal: position on page (page is newest-first)       -> recency proxy
  - Calcalist: position on page (via proxy markdown)         -> recency proxy
"""

import re
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
                browser={"browser": "chrome", "platform": "windows", "mobile": False})
            r = scraper.get(url, timeout=TIMEOUT)
            if r.status_code == 200:
                r.encoding = r.apparent_encoding or "utf-8"
                return r.text
            print(f"    cloudscraper returned {r.status_code}", file=sys.stderr)
        except Exception as e:
            print(f"    cloudscraper error: {e}", file=sys.stderr)

    try:
        proxied = "https://r.jina.ai/" + url
        r = requests.get(proxied, headers={"User-Agent": UA}, timeout=45)
        if r.status_code == 200 and len(r.text) > 2000:
            print("    fetched via r.jina.ai proxy (markdown)", file=sys.stderr)
            return "PROXY_MD::" + r.text
        print(f"    proxy returned {r.status_code}", file=sys.stderr)
    except Exception as e:
        print(f"    proxy error: {e}", file=sys.stderr)
    return None


def clean(text):
    if not text:
        return ""
    return html.unescape(" ".join(text.split())).strip()


def parse_bizportal(html_text, base):
    soup = BeautifulSoup(html_text, "lxml")
    items, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/news/article/" not in href:
            continue
        title = clean(a.get_text())
        if len(title) < 15:
            continue
        link = urljoin(base, href)
        if link in seen:
            continue
        seen.add(link)
        items.append({"title": title, "link": link, "source": "Bizportal",
                      "ts": None, "order": len(items)})
    return items


def parse_calcalist(html_text, base):
    items, seen = [], set()
    if html_text.startswith("PROXY_MD::"):
        md = html_text[len("PROXY_MD::"):]
        for m in re.finditer(r"\[([^\]]+)\]\((https?://[^)]*?/article/[^)]+)\)", md):
            title = clean(m.group(1))
            link = m.group(2).split(" ")[0].strip()
            if len(title) < 15 or link in seen:
                continue
            seen.add(link)
            items.append({"title": title, "link": link, "source": "Calcalist",
                          "ts": None, "order": len(items)})
        return items

    soup = BeautifulSoup(html_text, "lxml")
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
        items.append({"title": title, "link": link, "source": "Calcalist",
                      "ts": None, "order": len(items)})
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
        m = re.search(r"did=(\d+)", href)
        did = int(m.group(1)) if m else 0
        items.append({"title": title, "link": link, "source": "Globes",
                      "ts": None, "order": len(items), "_did": did})
    return items


def parse_themarker(html_text, base):
    soup = BeautifulSoup(html_text, "lxml")
    items, seen = [], set()
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
        ts = None
        dm = re.search(r"/(\d{4})-(\d{2})-(\d{2})/", href)
        block_text = ""
        parent = h.find_parent()
        if parent:
            block_text = parent.get_text(" ", strip=True)
        tm = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", block_text)
        if dm:
            y, mo, d = int(dm.group(1)), int(dm.group(2)), int(dm.group(3))
            hh, mm = (int(tm.group(1)), int(tm.group(2))) if tm else (12, 0)
            try:
                ts = dt.datetime(y, mo, d, hh, mm, tzinfo=dt.timezone.utc)
            except ValueError:
                ts = None
        items.append({"title": title, "link": link, "source": "TheMarker",
                      "ts": ts, "order": len(items)})
    return items


SOURCES = [
    ("Calcalist", "https://www.calcalist.co.il/allnews", parse_calcalist),
    ("Bizportal", "https://www.bizportal.co.il/todays_headlines", parse_bizportal),
    ("Globes",    "https://www.globes.co.il/news/home.aspx?fid=9473", parse_globes),
    ("TheMarker", "https://www.themarker.com/misc/all-headlines", parse_themarker),
]


def assign_ranks(items):
    now = dt.datetime.now(dt.timezone.utc)
    dids = [it["_did"] for it in items if it["source"] == "Globes" and "_did" in it]
    dmin, dmax = (min(dids), max(dids)) if dids else (0, 1)
    counts = {}
    for it in items:
        counts[it["source"]] = max(counts.get(it["source"], 0), it["order"] + 1)
    for it in items:
        src = it["source"]
        if it.get("ts"):
            it["sort_dt"] = it["ts"]
        elif src == "Globes" and dmax > dmin:
            frac = (it["_did"] - dmin) / (dmax - dmin)
            it["sort_dt"] = now - dt.timedelta(hours=12 * (1 - frac))
        else:
            n = counts.get(src, 1)
            frac = 1 - (it["order"] / max(n, 1))
            it["sort_dt"] = now - dt.timedelta(hours=12 * (1 - frac))
    return items


def main():
    all_items = []
    for name, url, parser in SOURCES:
        print(f"[{name}] fetching {url}")
        page = fetch(url)
        if not page:
            print(f"[{name}] FAILED to fetch. Skipping.", file=sys.stderr)
            continue
        try:
            got = parser(page, url)
            print(f"[{name}] parsed {len(got)} headlines")
            all_items.extend(got)
        except Exception as e:
            print(f"[{name}] parse error: {e}", file=sys.stderr)
        time.sleep(1)

    seen, deduped = set(), []
    for it in all_items:
        if it["link"] in seen:
            continue
        seen.add(it["link"])
        deduped.append(it)

    assign_ranks(deduped)
    deduped.sort(key=lambda it: it["sort_dt"], reverse=True)
    print(f"TOTAL: {len(deduped)} unique headlines (sorted newest-first)")

    now = dt.datetime.now(dt.timezone.utc)

    fg = FeedGenerator()
    fg.title("חדשות כלכלה — Israeli Finance Aggregator")
    fg.link(href="https://example.github.io/israeli-finance-feed/feed.xml", rel="self")
    fg.description("Combined headlines: Calcalist, Bizportal, Globes, TheMarker")
    fg.language("he")
    fg.lastBuildDate(now)
    for it in reversed(deduped):
        fe = fg.add_entry()
        fe.title(f"[{it['source']}] {it['title']}")
        fe.link(href=it["link"])
        fe.guid(it["link"], permalink=True)
        fe.description(it["title"])
        fe.pubDate(it["sort_dt"])
    fg.rss_file("feed.xml", pretty=True)
    print("Wrote feed.xml")

    write_html(deduped, now)
    print("Wrote index.html")

    if len(deduped) == 0:
        print("WARNING: zero headlines — all sources failed.", file=sys.stderr)
        sys.exit(1)


SITE_COLORS = {
    "Calcalist": "#c8102e",
    "Bizportal": "#0a6cff",
    "Globes":    "#e67e00",
    "TheMarker": "#00875a",
}


def write_html(items, now):
    il = now + dt.timedelta(hours=3)
    rows = []
    for it in items:
        color = SITE_COLORS.get(it["source"], "#666")
        t = it["sort_dt"] + dt.timedelta(hours=3)
        tstr = t.strftime("%H:%M")
        title = html.escape(it["title"])
        rows.append(
            f'<a class="item" href="{html.escape(it["link"])}" target="_blank" rel="noopener">'
            f'<span class="tag" style="background:{color}">{it["source"]}</span>'
            f'<span class="ttl">{title}</span>'
            f'<span class="tm">{tstr}</span></a>'
        )
    body = "\n".join(rows)
    doc = f"""<!doctype html><html lang="he" dir="rtl"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>מצרף חדשות כלכלה</title>
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system, "Segoe UI", Arial, sans-serif;
         max-width: 780px; margin: 0 auto; padding: 1rem;
         background: #fafafa; color: #111; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #16181c; color: #eee; }}
    .item {{ border-color: #2a2d33 !important; }}
    .item:hover {{ background: #1e2127 !important; }}
  }}
  h1 {{ font-size: 1.3rem; margin: .3rem 0; }}
  .sub {{ color: #888; font-size: .8rem; margin-bottom: 1rem; }}
  .item {{ display: flex; align-items: center; gap: .6rem;
          text-decoration: none; color: inherit;
          padding: .7rem .5rem; border-bottom: 1px solid #e5e5e5; }}
  .item:hover {{ background: #f0f0f0; }}
  .tag {{ flex: 0 0 auto; color: #fff; font-size: .68rem; font-weight: 600;
         padding: .12rem .45rem; border-radius: 4px; min-width: 62px;
         text-align: center; }}
  .ttl {{ flex: 1 1 auto; font-size: .95rem; line-height: 1.35; }}
  .tm {{ flex: 0 0 auto; color: #999; font-size: .72rem;
        font-variant-numeric: tabular-nums; }}
</style></head>
<body>
<h1>מצרף חדשות כלכלה</h1>
<div class="sub">כלכליסט · ביזפורטל · גלובס · דה־מרקר — {len(items)} כותרות ·
עודכן {il.strftime('%d/%m %H:%M')} (שעון ישראל) ·
<a href="feed.xml">RSS</a></div>
{body}
</body></html>"""
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(doc)


if __name__ == "__main__":
    main()
