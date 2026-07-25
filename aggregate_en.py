#!/usr/bin/env python3
"""
English finance news aggregator (Bloomberg, WSJ, Barron's) — headless browser.

Writes en-feed.xml and en.html (separate from the Hebrew feed).

HONEST NOTE: these three sites have strong anti-bot protection and paywalls.
This is an experiment to see empirically what loads from GitHub's servers.
Each site is wrapped so one failing doesn't kill the others, and the log
reports exactly how many headlines each yielded.

Extraction is by stable URL patterns (article URL shapes), since we can't
assume CSS class structure. Timestamps are best-effort.
"""

import re
import sys
import html
import datetime as dt

from feedgen.feed import FeedGenerator
from playwright.sync_api import sync_playwright

SITES = [
    {"name": "Bloomberg",
     "url": "https://www.bloomberg.com/latest",
     "pattern": r"bloomberg\.com/news/(articles|features)/[0-9]{4}-[0-9]{2}-[0-9]{2}/",
     "min_title": 15},
    {"name": "WSJ",
     "url": "https://www.wsj.com/news/latest-headlines",
     "pattern": r"wsj\.com/(articles|finance|economy|business|world|tech|markets)[/A-Za-z0-9\-]*",
     "min_title": 15},
    {"name": "Barron's",
     "url": "https://www.barrons.com/real-time",
     "pattern": r"barrons\.com/articles/[A-Za-z0-9\-]+",
     "min_title": 15},
]


def clean(t):
    if not t:
        return ""
    return html.unescape(" ".join(t.split())).strip()


def scrape_site(page, site):
    print(f"[{site['name']}] loading {site['url']}", file=sys.stderr)
    try:
        page.goto(site["url"], wait_until="domcontentloaded", timeout=45000)
    except Exception as e:
        print(f"[{site['name']}] goto error: {e}", file=sys.stderr)
        return []

    page.wait_for_timeout(4000)
    # Report the page title + a snippet so we can SEE if we hit a block/captcha
    try:
        title = page.title()
        print(f"[{site['name']}] page title: {title!r}", file=sys.stderr)
    except Exception:
        pass
    try:
        body_sample = page.eval_on_selector("body", "el => el.innerText.slice(0, 200)")
        oneline = " ".join((body_sample or "").split())
        print(f"[{site['name']}] body sample: {oneline[:160]!r}", file=sys.stderr)
    except Exception as e:
        print(f"[{site['name']}] body sample error: {e}", file=sys.stderr)

    try:
        for _ in range(3):
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(700)
    except Exception:
        pass

    try:
        anchors = page.eval_on_selector_all(
            "a[href]",
            "els => els.map(e => ({href: e.href, text: e.innerText}))"
        )
    except Exception as e:
        print(f"[{site['name']}] anchor read error: {e}", file=sys.stderr)
        return []

    pat = re.compile(site["pattern"])
    now = dt.datetime.now(dt.timezone.utc)
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
        ts = None
        dm = re.search(r"/(\d{4})-(\d{2})-(\d{2})/", href)
        if dm:
            y, mo, d = map(int, dm.groups())
            try:
                ts = dt.datetime(y, mo, d, 12, 0, tzinfo=dt.timezone.utc)
            except ValueError:
                pass
        items.append({"title": title, "link": link, "source": site["name"],
                      "ts": ts, "order": len(items)})

    print(f"[{site['name']}] extracted {len(items)} headlines", file=sys.stderr)
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
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        ctx = browser.new_context(
            locale="en-US",
            timezone_id="America/New_York",
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
    fg.title("English Finance Aggregator — Bloomberg, WSJ, Barron's")
    fg.link(href="https://example.github.io/Ifin-News/en-feed.xml", rel="self")
    fg.description("Combined: Bloomberg, WSJ, Barron's")
    fg.language("en")
    fg.lastBuildDate(now)
    for it in reversed(deduped):
        fe = fg.add_entry()
        fe.title(f"[{it['source']}] {it['title']}")
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
    for s in ["Bloomberg", "WSJ", "Barron's"]:
        print(f"  {s}: {c.get(s,0)}")


SITE_COLORS = {"Bloomberg": "#000000", "WSJ": "#0080c6", "Barron's": "#00625b"}


def write_html(items, now):
    il = now
    rows = []
    for it in items:
        color = SITE_COLORS.get(it["source"], "#666")
        t = it["sort_dt"].strftime("%H:%M") if it.get("ts") else "—"
        title = html.escape(it["title"])
        rows.append(
            f'<a class="item" data-src="{html.escape(it["source"])}" '
            f'href="{html.escape(it["link"])}" target="_blank" rel="noopener">'
            f'<span class="tag" style="background:{color}">{html.escape(it["source"])}</span>'
            f'<span class="ttl">{title}</span>'
            f'<span class="tm">{t}</span></a>')
    body = "\n".join(rows)
    btns = ['<button class="fbtn active" data-f="all" onclick="flt(this)">All</button>']
    for name in ["Bloomberg", "WSJ", "Barron's"]:
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
  .tag {{ flex:0 0 auto; color:#fff; font-size:.68rem; font-weight:600;
         padding:.12rem .45rem; border-radius:4px; min-width:70px; text-align:center; }}
  .ttl {{ flex:1 1 auto; font-size:.95rem; line-height:1.35; }}
  .tm {{ flex:0 0 auto; color:#999; font-size:.72rem; font-variant-numeric:tabular-nums; }}
</style></head><body>
<h1>English Finance Aggregator</h1>
<div class="sub">Bloomberg · WSJ · Barron's — {len(items)} headlines ·
updated {il.strftime('%d/%m %H:%M')} UTC · <a href="feed.xml">RSS</a> · <a href="../">‹ עברית</a></div>
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
