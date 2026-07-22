#!/usr/bin/env python3
"""
Israeli finance news aggregator — headless-browser version.

Renders each site with Chromium (executing its JavaScript, like a real visitor),
then extracts headlines by STABLE URL PATTERNS rather than brittle CSS classes.
Produces feed.xml (RSS) and index.html (browser reading page), newest-first.

Sites & article-URL patterns:
  Calcalist  https://www.calcalist.co.il/allnews          -> /article/<slug>
  Bizportal  https://www.bizportal.co.il/todays_headlines -> /news/article/<num>
  Globes     https://www.globes.co.il/news/home.aspx?fid=9473 -> article.aspx?did=<num>
  TheMarker  https://www.themarker.com/misc/all-headlines -> /ty-article...  (date in URL)

Recency:
  TheMarker  date in URL + time text -> real datetime
  Globes     did number              -> proxy (higher = newer)
  Others     DOM order after render  -> proxy (top = newer)
Because the browser renders the LIVE feed (not a static HTML block), the DOM
order now reflects the real current stream.
"""

import re
import sys
import html
import datetime as dt

from feedgen.feed import FeedGenerator
from playwright.sync_api import sync_playwright

SITES = [
    {"name": "Calcalist",
     "url": "https://www.calcalist.co.il/allnews",
     "pattern": r"calcalist\.co\.il/[^\"']*/article/[A-Za-z0-9]+",
     "min_title": 15},
    {"name": "Bizportal",
     "url": "https://www.bizportal.co.il/todays_headlines",
     "pattern": r"bizportal\.co\.il/[^\"']*/news/article/\d+",
     "min_title": 15},
    {"name": "Globes",
     "url": "https://www.globes.co.il/news/home.aspx?fid=9473",
     "pattern": r"globes\.co\.il/news/article\.aspx\?did=\d+",
     "min_title": 15},
    {"name": "TheMarker",
     "url": "https://www.themarker.com/misc/all-headlines",
     "pattern": r"themarker\.com/[^\"']*ty-article[^\"']*",
     "min_title": 15},
]


def clean(t):
    if not t:
        return ""
    return html.unescape(" ".join(t.split())).strip()


def scrape_site(page, site):
    """Render the site and return list of {title, link, source, ts, order}."""
    print(f"[{site['name']}] loading {site['url']}", file=sys.stderr)
    try:
        page.goto(site["url"], wait_until="domcontentloaded", timeout=45000)
    except Exception as e:
        print(f"[{site['name']}] goto error: {e}", file=sys.stderr)
        return []
    # Let JS build the feed, then scroll to trigger lazy-loading
    page.wait_for_timeout(3500)
    try:
        for _ in range(3):
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(800)
    except Exception:
        pass

    # For each matching link, grab its href, text, AND the text of a nearby
    # container (row/card) so we can read the date/time shown beside it.
    anchors = page.eval_on_selector_all(
        "a[href]",
        """els => els.map(e => {
            let ctx = e;
            for (let i = 0; i < 4 && ctx.parentElement; i++) {
                ctx = ctx.parentElement;
                if (ctx.innerText && ctx.innerText.length > e.innerText.length + 3) break;
            }
            return {href: e.href, text: e.innerText, ctx: ctx ? ctx.innerText : ""};
        })"""
    )

    pat = re.compile(site["pattern"])
    now = dt.datetime.now(dt.timezone.utc)
    today_il = (now + dt.timedelta(hours=3)).date()
    items, seen = [], set()
    for a in anchors:
        href = a.get("href") or ""
        if not pat.search(href):
            continue
        title = clean(a.get("text"))
        if len(title) < site["min_title"]:
            continue
        link = href.split("#")[0]
        if link in seen:
            continue
        seen.add(link)
        item = {"title": title, "link": link, "source": site["name"],
                "ts": None, "order": len(items)}

        ctx = a.get("ctx") or ""

        if site["name"] == "TheMarker":
            dm = re.search(r"/(\d{4})-(\d{2})-(\d{2})/", href)
            tm = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", ctx)
            if dm:
                y, mo, d = map(int, dm.groups())
                hh, mm = (int(tm.group(1)), int(tm.group(2))) if tm else (12, 0)
                try:
                    item["ts"] = dt.datetime(y, mo, d, hh, mm, tzinfo=dt.timezone.utc)
                except ValueError:
                    pass

        elif site["name"] in ("Calcalist", "Bizportal"):
            dmatch = re.search(r"\b(\d{2})/(\d{2})/(\d{4})\b", ctx)
            tmatch = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", ctx)
            if dmatch:
                d, mo, y = map(int, dmatch.groups())
                try:
                    hh, mm = (int(tmatch.group(1)), int(tmatch.group(2))) if tmatch else (12, 0)
                    item["ts"] = dt.datetime(y, mo, d, hh, mm, tzinfo=dt.timezone.utc)
                except ValueError:
                    pass
            elif tmatch:
                hh, mm = int(tmatch.group(1)), int(tmatch.group(2))
                try:
                    il_dt = dt.datetime(today_il.year, today_il.month, today_il.day,
                                        hh, mm) - dt.timedelta(hours=3)
                    item["ts"] = il_dt.replace(tzinfo=dt.timezone.utc)
                except ValueError:
                    pass

        if site["name"] == "Globes":
            m = re.search(r"did=(\d+)", href)
            item["_did"] = int(m.group(1)) if m else 0

        items.append(item)

    dated = sum(1 for it in items if it.get("ts"))
    print(f"[{site['name']}] extracted {len(items)} headlines "
          f"({dated} with real timestamp)", file=sys.stderr)
    return items


def assign_ranks(items):
    now = dt.datetime.now(dt.timezone.utc)
    dids = [it["_did"] for it in items if it.get("source") == "Globes" and "_did" in it]
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


def main():
    all_items = []
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        ctx = browser.new_context(
            locale="he-IL",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/122.0 Safari/537.36"),
            viewport={"width": 1280, "height": 1600},
        )
        page = ctx.new_page()
        for site in SITES:
            try:
                all_items.extend(scrape_site(page, site))
            except Exception as e:
                print(f"[{site['name']}] scrape error: {e}", file=sys.stderr)
        browser.close()

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

    # RSS
    fg = FeedGenerator()
    fg.title("חדשות כלכלה — Israeli Finance Aggregator")
    fg.link(href="https://example.github.io/israeli-finance-feed/feed.xml", rel="self")
    fg.description("Combined: Calcalist, Bizportal, Globes, TheMarker")
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

    # Per-site counts to the log so we can see health at a glance
    from collections import Counter
    c = Counter(it["source"] for it in deduped)
    for s in ["Calcalist", "Bizportal", "Globes", "TheMarker"]:
        print(f"  {s}: {c.get(s,0)}")

    if len(deduped) == 0:
        print("WARNING: zero headlines.", file=sys.stderr)
        sys.exit(1)


SITE_COLORS = {"Calcalist": "#c8102e", "Bizportal": "#0a6cff",
               "Globes": "#e67e00", "TheMarker": "#00875a"}


def write_html(items, now):
    il = now + dt.timedelta(hours=3)
    rows = []
    for it in items:
        color = SITE_COLORS.get(it["source"], "#666")
        t = (it["sort_dt"] + dt.timedelta(hours=3)).strftime("%H:%M")
        title = html.escape(it["title"])
        rows.append(
            f'<a class="item" href="{html.escape(it["link"])}" target="_blank" rel="noopener">'
            f'<span class="tag" style="background:{color}">{it["source"]}</span>'
            f'<span class="ttl">{title}</span>'
            f'<span class="tm">{t}</span></a>')
    body = "\n".join(rows)
    doc = f"""<!doctype html><html lang="he" dir="rtl"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>מצרף חדשות כלכלה</title>
<style>
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system,"Segoe UI",Arial,sans-serif; max-width:780px;
         margin:0 auto; padding:1rem; background:#fafafa; color:#111; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background:#16181c; color:#eee; }}
    .item {{ border-color:#2a2d33 !important; }} .item:hover {{ background:#1e2127 !important; }} }}
  h1 {{ font-size:1.3rem; margin:.3rem 0; }}
  .sub {{ color:#888; font-size:.8rem; margin-bottom:1rem; }}
  .item {{ display:flex; align-items:center; gap:.6rem; text-decoration:none;
          color:inherit; padding:.7rem .5rem; border-bottom:1px solid #e5e5e5; }}
  .item:hover {{ background:#f0f0f0; }}
  .tag {{ flex:0 0 auto; color:#fff; font-size:.68rem; font-weight:600;
         padding:.12rem .45rem; border-radius:4px; min-width:62px; text-align:center; }}
  .ttl {{ flex:1 1 auto; font-size:.95rem; line-height:1.35; }}
  .tm {{ flex:0 0 auto; color:#999; font-size:.72rem; font-variant-numeric:tabular-nums; }}
</style></head><body>
<h1>מצרף חדשות כלכלה</h1>
<div class="sub">כלכליסט · ביזפורטל · גלובס · דה־מרקר — {len(items)} כותרות ·
עודכן {il.strftime('%d/%m %H:%M')} (שעון ישראל) · <a href="feed.xml">RSS</a></div>
{body}
</body></html>"""
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(doc)


if __name__ == "__main__":
    main()
