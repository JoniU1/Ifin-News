#!/usr/bin/env python3
"""
English finance news aggregator — WSJ (official RSS) + Barron's (browser scrape).

Writes en-feed.xml and en.html (separate from the Hebrew feed).

Empirical findings (2026-07): Bloomberg hard-blocks scrapers (CAPTCHA) and has
no public RSS, so it's dropped. WSJ blocks scraping but publishes official RSS
feeds, so we use those. Barron's scrape works, so we keep it.

WSJ pulls multiple sections including Real Estate. Barron's is rendered with a
headless browser and extracted by article-URL pattern.
"""

import re
import sys
import html
import datetime as dt

import feedparser
from feedgen.feed import FeedGenerator
from playwright.sync_api import sync_playwright

# ---- WSJ official RSS feeds (section -> URL) ----
WSJ_FEEDS = {
    "Markets":     "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain",
    "Business":    "https://feeds.content.dowjones.io/public/rss/WSJcomUSBusiness",
    "Real Estate": "https://feeds.content.dowjones.io/public/rss/latestnewsrealestate",
}

BARRONS = {
    "name": "Barron's",
    "url": "https://www.barrons.com/real-time",
    "pattern": r"barrons\.com/articles/[A-Za-z0-9\-]+",
    "min_title": 15,
}

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")


def clean(t):
    if not t:
        return ""
    return html.unescape(" ".join(t.split())).strip()


def fetch_wsj():
    """Pull all WSJ section feeds via RSS. Returns list of item dicts."""
    items, seen = [], set()
    for section, url in WSJ_FEEDS.items():
        print(f"[WSJ/{section}] fetching {url}", file=sys.stderr)
        try:
            # feedparser fetches and parses; set a UA to be safe
            fp = feedparser.parse(url, request_headers={"User-Agent": UA})
            n = 0
            for e in fp.entries:
                title = clean(e.get("title"))
                link = (e.get("link") or "").split("#")[0]
                if len(title) < 12 or not link or link in seen:
                    continue
                seen.add(link)
                ts = None
                if e.get("published_parsed"):
                    try:
                        ts = dt.datetime(*e.published_parsed[:6],
                                         tzinfo=dt.timezone.utc)
                    except Exception:
                        ts = None
                items.append({"title": title, "link": link, "source": "WSJ",
                              "section": section, "ts": ts, "order": len(items)})
                n += 1
            print(f"[WSJ/{section}] {n} items", file=sys.stderr)
        except Exception as ex:
            print(f"[WSJ/{section}] error: {ex}", file=sys.stderr)
    print(f"[WSJ] total {len(items)} items", file=sys.stderr)
    return items


def scrape_barrons(page):
    site = BARRONS
    print(f"[Barron's] loading {site['url']}", file=sys.stderr)
    try:
        page.goto(site["url"], wait_until="domcontentloaded", timeout=45000)
    except Exception as e:
        print(f"[Barron's] goto error: {e}", file=sys.stderr)
        return []
    page.wait_for_timeout(4000)
    try:
        for _ in range(3):
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(700)
    except Exception:
        pass
    try:
        anchors = page.eval_on_selector_all(
            "a[href]", "els => els.map(e => ({href: e.href, text: e.innerText}))")
    except Exception as e:
        print(f"[Barron's] anchor read error: {e}", file=sys.stderr)
        return []

    pat = re.compile(site["pattern"])
    items, seen = [], set()
    for a in anchors:
        href = a.get("href") or ""
        if not pat.search(href):
            continue
        title = clean(a.get("text"))
        if len(title) < site["min_title"]:
            continue
        link = href.split("#")[0].split("?")[0]
        if link in seen:
            continue
        seen.add(link)
        items.append({"title": title, "link": link, "source": "Barron's",
                      "section": "", "ts": None, "order": len(items)})
    print(f"[Barron's] extracted {len(items)} headlines", file=sys.stderr)
    return items


def assign_ranks(items):
    now = dt.datetime.now(dt.timezone.utc)
    counts = {}
    for it in items:
        counts[it["source"]] = max(counts.get(it["source"], 0), it["order"] + 1)
    for it in items:
        if it.get("ts"):
            it["sort_dt"] = it["ts"]
        else:
            n = counts.get(it["source"], 1)
            frac = 1 - (it["order"] / max(n, 1))
            it["sort_dt"] = now - dt.timedelta(hours=12 * (1 - frac))


def main():
    all_items = []
    # WSJ via RSS (no browser needed)
    all_items.extend(fetch_wsj())
    # Barron's via browser
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--no-sandbox"])
            ctx = browser.new_context(
                locale="en-US", timezone_id="America/New_York",
                user_agent=UA, viewport={"width": 1280, "height": 1600})
            page = ctx.new_page()
            all_items.extend(scrape_barrons(page))
            browser.close()
    except Exception as e:
        print(f"[Barron's] browser error: {e}", file=sys.stderr)

    # Dedup
    seen, deduped = set(), []
    for it in all_items:
        if it["link"] in seen:
            continue
        seen.add(it["link"])
        deduped.append(it)

    assign_ranks(deduped)
    deduped.sort(key=lambda it: it["sort_dt"], reverse=True)
    print(f"TOTAL: {len(deduped)} unique headlines")

    now = dt.datetime.now(dt.timezone.utc)
    fg = FeedGenerator()
    fg.title("English Finance Aggregator — WSJ & Barron's")
    fg.link(href="https://example.github.io/Ifin-News/en/feed.xml", rel="self")
    fg.description("Combined: WSJ (Markets, Business, Real Estate) + Barron's")
    fg.language("en")
    fg.lastBuildDate(now)
    for it in reversed(deduped):
        fe = fg.add_entry()
        label = it["source"] + (f"/{it['section']}" if it.get("section") else "")
        fe.title(f"[{label}] {it['title']}")
        fe.link(href=it["link"])
        fe.guid(it["link"], permalink=True)
        fe.description(it["title"])
        fe.pubDate(it["sort_dt"])
    fg.rss_file("en-feed.xml", pretty=True)
    print("Wrote en-feed.xml")

    write_html(deduped, now)
    print("Wrote en.html")

    from collections import Counter
    c = Counter(it["source"] for it in deduped)
    for s in ["WSJ", "Barron's"]:
        print(f"  {s}: {c.get(s,0)}")


SITE_COLORS = {"WSJ": "#0080c6", "Barron's": "#00625b"}


def write_html(items, now):
    rows = []
    for it in items:
        color = SITE_COLORS.get(it["source"], "#666")
        t = it["sort_dt"].strftime("%H:%M") if it.get("ts") else "—"
        label = it["source"] + (f" · {it['section']}" if it.get("section") else "")
        title = html.escape(it["title"])
        rows.append(
            f'<a class="item" data-src="{html.escape(it["source"])}" '
            f'href="{html.escape(it["link"])}" target="_blank" rel="noopener">'
            f'<span class="tag" style="background:{color}">{html.escape(label)}</span>'
            f'<span class="ttl">{title}</span>'
            f'<span class="tm">{t}</span></a>')
    body = "\n".join(rows)
    btns = ['<button class="fbtn active" data-f="all" onclick="flt(this)">All</button>']
    for name in ["WSJ", "Barron's"]:
        color = SITE_COLORS[name]
        btns.append(
            f'<button class="fbtn" data-f="{html.escape(name)}" onclick="flt(this)" '
            f'style="--c:{color}">{html.escape(name)}</button>')
    buttons = "\n".join(btns)

    doc = f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>English Finance Aggregator</title>
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system,"Segoe UI",Arial,sans-serif; max-width:780px;
         margin:0 auto; padding:1rem; background:#fafafa; color:#111; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background:#16181c; color:#eee; }}
    .item {{ border-color:#2a2d33 !important; }} .item:hover {{ background:#1e2127 !important; }}
    .fbtn {{ background:#23262d; color:#ddd; border-color:#333 !important; }} }}
  h1 {{ font-size:1.3rem; margin:.3rem 0; }}
  .sub {{ color:#888; font-size:.8rem; margin-bottom:.7rem; }}
  .filters {{ display:flex; flex-wrap:wrap; gap:.4rem; margin-bottom:1rem;
             position:sticky; top:0; background:inherit; padding:.4rem 0; z-index:5; }}
  .fbtn {{ cursor:pointer; border:1px solid #ccc; border-radius:999px;
          padding:.35rem .8rem; font-size:.8rem; font-weight:600; background:#fff; color:#333; }}
  .fbtn.active {{ background:var(--c,#333); color:#fff; border-color:var(--c,#333); }}
  .item {{ display:flex; align-items:center; gap:.6rem; text-decoration:none;
          color:inherit; padding:.7rem .5rem; border-bottom:1px solid #e5e5e5; }}
  .item:hover {{ background:#f0f0f0; }}
  .item.hide {{ display:none; }}
  .tag {{ flex:0 0 auto; color:#fff; font-size:.66rem; font-weight:600;
         padding:.12rem .45rem; border-radius:4px; min-width:96px; text-align:center; }}
  .ttl {{ flex:1 1 auto; font-size:.95rem; line-height:1.35; }}
  .tm {{ flex:0 0 auto; color:#999; font-size:.72rem; font-variant-numeric:tabular-nums; }}
</style></head><body>
<h1>English Finance Aggregator</h1>
<div class="sub">WSJ · Barron's — {len(items)} headlines ·
updated {now.strftime('%d/%m %H:%M')} UTC · <a href="feed.xml">RSS</a> · <a href="../">‹ עברית</a></div>
<div class="filters">
{buttons}
</div>
{body}
<script>
function flt(btn) {{
  var f = btn.getAttribute('data-f');
  document.querySelectorAll('.fbtn').forEach(function(b){{ b.classList.remove('active'); }});
  btn.classList.add('active');
  document.querySelectorAll('.item').forEach(function(it){{
    if (f === 'all' || it.getAttribute('data-src') === f) it.classList.remove('hide');
    else it.classList.add('hide');
  }});
}}
</script>
</body></html>"""
    with open("en.html", "w", encoding="utf-8") as f:
        f.write(doc)


if __name__ == "__main__":
    main()
