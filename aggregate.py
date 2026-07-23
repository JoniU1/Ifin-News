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


def scrape_globes(page, site):
    """
    Globes structure (confirmed via DevTools):
      <div class="AllDayleNews">
        <h2 class="newsTitle">יום רביעי 22 יולי 2026</h2>   day header
        <div class="itemP">
          <h2><span class="info">16:30</span>
              <a href="/news/article.aspx?did=NNN">TITLE</a></h2>
        </div>
      </div>
    We read each .itemP: its .info time + the did link, dating it from the most
    recent .newsTitle day header above it.
    """
    HEB_MONTHS = {
        "ינואר": 1, "פברואר": 2, "מרץ": 3, "מרס": 3, "אפריל": 4, "מאי": 5,
        "יוני": 6, "יולי": 7, "אוגוסט": 8, "ספטמבר": 9, "אוקטובר": 10,
        "נובמבר": 11, "דצמבר": 12,
    }
    now = dt.datetime.now(dt.timezone.utc)
    today_il = (now + dt.timedelta(hours=3)).date()

    print(f"[Globes] loading {site['url']}", file=sys.stderr)
    try:
        page.goto(site["url"], wait_until="domcontentloaded", timeout=45000)
    except Exception as e:
        print(f"[Globes] goto error: {e}", file=sys.stderr)
        return []
    # Wait for the JS-built article list to actually appear (up to 20s),
    # rather than guessing with a fixed delay.
    try:
        page.wait_for_selector(".itemP", timeout=20000)
    except Exception:
        print("[Globes] .itemP never appeared within 20s", file=sys.stderr)
    page.wait_for_timeout(1500)
    try:
        for _ in range(4):
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(800)
    except Exception:
        pass

    rows = page.eval_on_selector_all(
        ".newsTitle, .itemP",
        """els => els.map(e => {
            if (e.classList.contains('newsTitle')) {
                return {kind: 'day', text: e.innerText};
            }
            // an .itemP may have two did= links: a thumbnail (class "im", no
            // text) and the headline link (has text). Prefer the one with text.
            const links = Array.from(e.querySelectorAll('a[href*="did="]'));
            let a = links.find(l => (l.innerText || '').trim().length > 10) || links[0];
            const info = e.querySelector('.info');
            return {
                kind: 'item',
                href: a ? a.href : '',
                title: a ? a.innerText : '',
                info: info ? info.innerText : ''
            };
        })"""
    )

    items, seen = [], set()
    cur_date = today_il
    for r in rows:
        if r.get("kind") == "day":
            txt = r.get("text", "")
            dm = re.search(r"(\d{1,2})\s+(\S+)\s+(\d{4})", txt)
            if dm:
                d = int(dm.group(1))
                mo = HEB_MONTHS.get(dm.group(2))
                y = int(dm.group(3))
                if mo:
                    try:
                        cur_date = dt.date(y, mo, d)
                    except ValueError:
                        pass
            continue

        href = r.get("href") or ""
        if "did=" not in href:
            continue
        title = clean(r.get("title"))
        if len(title) < site["min_title"]:
            continue
        link = href.split("#")[0]
        if link in seen:
            continue
        seen.add(link)

        ts = None
        info = r.get("info") or ""
        tmatch = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", info)
        if tmatch:
            hh, mm = int(tmatch.group(1)), int(tmatch.group(2))
            try:
                il_dt = dt.datetime(cur_date.year, cur_date.month, cur_date.day,
                                    hh, mm) - dt.timedelta(hours=3)
                ts = il_dt.replace(tzinfo=dt.timezone.utc)
            except ValueError:
                pass

        m = re.search(r"did=(\d+)", href)
        item = {"title": title, "link": link, "source": "Globes",
                "ts": ts, "order": len(items), "_did": int(m.group(1)) if m else 0}
        items.append(item)

    dated = sum(1 for it in items if it.get("ts"))
    print(f"[Globes] extracted {len(items)} headlines "
          f"({dated} with real timestamp) [structured]", file=sys.stderr)

    # Safety net: if the structured selector found almost nothing, fall back to
    # the generic scraper (which previously returned ~82 Globes articles).
    if len(items) < 5:
        print("[Globes] structured found too few; falling back to generic",
              file=sys.stderr)
        try:
            fallback = scrape_site(page, site)
            if len(fallback) > len(items):
                return fallback
        except Exception as e:
            print(f"[Globes] fallback error: {e}", file=sys.stderr)
    return items


def scrape_calcalist(page, site):
    """
    Calcalist has a precise structure (confirmed via DevTools):
      <div class="dayHeader">22.07.26 (היום)</div>
      <div class="item">
        <span class="date">16:00</span>
        <div class="textDiv"><a class="itemTitle" href=".../article/..">TITLE</a></div>
      </div>
    We walk the feed in document order, tracking the current day from dayHeader,
    and read each item's .date (HH:MM or a date) + .itemTitle. This ignores the
    "most viewed" block, which is not built from .item/.dayHeader rows.
    """
    now = dt.datetime.now(dt.timezone.utc)
    today_il = (now + dt.timedelta(hours=3)).date()

    print(f"[Calcalist] loading {site['url']}", file=sys.stderr)
    try:
        page.goto(site["url"], wait_until="domcontentloaded", timeout=45000)
    except Exception as e:
        print(f"[Calcalist] goto error: {e}", file=sys.stderr)
        return []
    page.wait_for_timeout(3500)
    try:
        for _ in range(3):
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(800)
    except Exception:
        pass

    rows = page.eval_on_selector_all(
        ".dayHeader, .item",
        """els => els.map(e => {
            if (e.classList.contains('dayHeader')) {
                return {kind: 'day', text: e.innerText};
            }
            const a = e.querySelector('a.itemTitle') || e.querySelector('a[href*="/article/"]');
            const d = e.querySelector('.date');
            return {
                kind: 'item',
                href: a ? a.href : '',
                title: a ? a.innerText : '',
                date: d ? d.innerText : ''
            };
        })"""
    )

    items, seen = [], set()
    cur_date = today_il  # default until we see a dayHeader
    for r in rows:
        if r.get("kind") == "day":
            # dayHeader like "יום רביעי 22.07.26 (היום)"
            dm = re.search(r"(\d{2})\.(\d{2})\.(\d{2})", r.get("text", ""))
            if dm:
                d, mo, yy = map(int, dm.groups())
                try:
                    cur_date = dt.date(2000 + yy, mo, d)
                except ValueError:
                    pass
            continue

        href = r.get("href") or ""
        if "/article/" not in href:
            continue
        title = clean(r.get("title"))
        if len(title) < site["min_title"]:
            continue
        link = href.split("#")[0]
        if link in seen:
            continue
        seen.add(link)

        ts = None
        dtext = r.get("date") or ""
        tmatch = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", dtext)
        dmatch = re.search(r"\b(\d{2})[./](\d{2})[./](\d{2,4})\b", dtext)
        if tmatch:
            hh, mm = int(tmatch.group(1)), int(tmatch.group(2))
            try:
                il_dt = dt.datetime(cur_date.year, cur_date.month, cur_date.day,
                                    hh, mm) - dt.timedelta(hours=3)
                ts = il_dt.replace(tzinfo=dt.timezone.utc)
            except ValueError:
                pass
        elif dmatch:
            d, mo, yy = dmatch.groups()
            d, mo = int(d), int(mo)
            yy = int(yy)
            yy = yy + 2000 if yy < 100 else yy
            try:
                il_dt = dt.datetime(yy, mo, d, 12, 0) - dt.timedelta(hours=3)
                ts = il_dt.replace(tzinfo=dt.timezone.utc)
            except ValueError:
                pass

        items.append({"title": title, "link": link, "source": "Calcalist",
                      "ts": ts, "order": len(items)})

        # TEMP DEBUG: show raw date text vs parsed, for first 5 items
        if len([1 for _ in items]) <= 5:
            disp = (ts + dt.timedelta(hours=3)).strftime("%H:%M") if ts else "—"
            print(f"[Calcalist DEBUG] raw_date='{dtext}' -> displays {disp} "
                  f"| title={title[:30]}", file=sys.stderr)

    dated = sum(1 for it in items if it.get("ts"))
    print(f"[Calcalist] extracted {len(items)} headlines "
          f"({dated} with real timestamp) [structured]", file=sys.stderr)
    return items


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
    # container so we can read the date/time shown beside it. Some sites
    # (Calcalist) put the time in a sibling/further ancestor, so climb until
    # the container is clearly larger than the headline, then also fold in
    # the previous/next sibling text to catch times placed alongside.
    anchors = page.eval_on_selector_all(
        "a[href]",
        """els => els.map(e => {
            let ctx = e;
            for (let i = 0; i < 6 && ctx.parentElement; i++) {
                ctx = ctx.parentElement;
                // stop once the block is substantially bigger than the link text
                if (ctx.innerText && ctx.innerText.length > e.innerText.length + 8) break;
            }
            let extra = "";
            try {
                if (ctx.previousElementSibling) extra += " " + ctx.previousElementSibling.innerText;
                if (ctx.nextElementSibling)     extra += " " + ctx.nextElementSibling.innerText;
                if (e.previousElementSibling)   extra += " " + e.previousElementSibling.innerText;
                if (e.nextElementSibling)       extra += " " + e.nextElementSibling.innerText;
            } catch (err) {}
            return {href: e.href, text: e.innerText,
                    ctx: (ctx ? ctx.innerText : "") + extra};
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
                    # page shows Israel local time -> convert to UTC (-3)
                    il_dt = dt.datetime(y, mo, d, hh, mm) - dt.timedelta(hours=3)
                    item["ts"] = il_dt.replace(tzinfo=dt.timezone.utc)
                except ValueError:
                    pass

        elif site["name"] in ("Calcalist", "Bizportal"):
            dmatch = re.search(r"\b(\d{2})/(\d{2})/(\d{4})\b", ctx)
            tmatch = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", ctx)
            if dmatch:
                d, mo, y = map(int, dmatch.groups())
                try:
                    hh, mm = (int(tmatch.group(1)), int(tmatch.group(2))) if tmatch else (12, 0)
                    # page shows Israel local time -> convert to UTC (-3)
                    il_dt = dt.datetime(y, mo, d, hh, mm) - dt.timedelta(hours=3)
                    item["ts"] = il_dt.replace(tzinfo=dt.timezone.utc)
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

    # Calcalist-specific: the "most viewed" block (old popular articles) renders
    # near the top without the live feed's time label. If timestamp parsing is
    # working for a decent share of items, keep ONLY timestamped ones so the
    # undated featured block can't float to the top. If parsing mostly failed
    # (dated very low), keep everything rather than emptying the source.
    if site["name"] == "Calcalist" and len(items) > 0:
        share = dated / len(items)
        if share >= 0.4:
            before = len(items)
            items = [it for it in items if it.get("ts")]
            print(f"[Calcalist] dropped {before - len(items)} undated "
                  f"(most-viewed) items", file=sys.stderr)
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
            timezone_id="Asia/Jerusalem",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/122.0 Safari/537.36"),
            viewport={"width": 1280, "height": 1600},
        )
        page = ctx.new_page()
        for site in SITES:
            try:
                if site["name"] == "Calcalist":
                    all_items.extend(scrape_calcalist(page, site))
                elif site["name"] == "Globes":
                    all_items.extend(scrape_globes(page, site))
                else:
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
        # Show a real clock time only when we actually parsed one.
        # Globes (and any proxy-ranked item) has no true time -> show a dash.
        if it.get("ts"):
            t = (it["ts"] + dt.timedelta(hours=3)).strftime("%H:%M")
        else:
            t = "—"
        title = html.escape(it["title"])
        rows.append(
            f'<a class="item" data-src="{it["source"]}" '
            f'href="{html.escape(it["link"])}" target="_blank" rel="noopener">'
            f'<span class="tag" style="background:{color}">{it["source"]}</span>'
            f'<span class="ttl">{title}</span>'
            f'<span class="tm">{t}</span></a>')
    body = "\n".join(rows)

    # Filter buttons: All + one per source
    btns = ['<button class="fbtn active" data-f="all" onclick="flt(this)">הכל</button>']
    for name in ["Calcalist", "Bizportal", "Globes", "TheMarker"]:
        color = SITE_COLORS[name]
        btns.append(
            f'<button class="fbtn" data-f="{name}" onclick="flt(this)" '
            f'style="--c:{color}">{name}</button>')
    buttons = "\n".join(btns)

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
    .item {{ border-color:#2a2d33 !important; }} .item:hover {{ background:#1e2127 !important; }}
    .fbtn {{ background:#23262d; color:#ddd; border-color:#333 !important; }} }}
  h1 {{ font-size:1.3rem; margin:.3rem 0; }}
  .sub {{ color:#888; font-size:.8rem; margin-bottom:.7rem; }}
  .filters {{ display:flex; flex-wrap:wrap; gap:.4rem; margin-bottom:1rem;
             position:sticky; top:0; background:inherit; padding:.4rem 0; z-index:5; }}
  .fbtn {{ cursor:pointer; border:1px solid #ccc; border-radius:999px;
          padding:.35rem .8rem; font-size:.8rem; font-weight:600;
          background:#fff; color:#333; }}
  .fbtn.active {{ background:var(--c,#333); color:#fff; border-color:var(--c,#333); }}
  .item {{ display:flex; align-items:center; gap:.6rem; text-decoration:none;
          color:inherit; padding:.7rem .5rem; border-bottom:1px solid #e5e5e5; }}
  .item:hover {{ background:#f0f0f0; }}
  .item.hide {{ display:none; }}
  .tag {{ flex:0 0 auto; color:#fff; font-size:.68rem; font-weight:600;
         padding:.12rem .45rem; border-radius:4px; min-width:62px; text-align:center; }}
  .ttl {{ flex:1 1 auto; font-size:.95rem; line-height:1.35; }}
  .tm {{ flex:0 0 auto; color:#999; font-size:.72rem; font-variant-numeric:tabular-nums; }}
</style></head><body>
<h1>מצרף חדשות כלכלה</h1>
<div class="sub">{len(items)} כותרות · עודכן {il.strftime('%d/%m %H:%M')} (שעון ישראל) ·
<a href="feed.xml">RSS</a></div>
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
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(doc)


if __name__ == "__main__":
    main()
