<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>aggregate_en.py</title></head><body style="font-family:sans-serif;margin:1rem"><h3>aggregate_en.py</h3><p><b>Barron's now parses "X hours ago" into real timestamps</b></p><p>Tap inside box → Select All → Copy → paste into GitHub replacing whole file.</p><textarea readonly style="width:100%;height:78vh;font-family:monospace;font-size:12px;white-space:pre;">#!/usr/bin/env python3
&quot;&quot;&quot;
English finance news aggregator — WSJ (official RSS) + Barron&#x27;s (browser scrape).

Writes en-feed.xml and en.html (separate from the Hebrew feed).

Empirical findings (2026-07): Bloomberg hard-blocks scrapers (CAPTCHA) and has
no public RSS, so it&#x27;s dropped. WSJ blocks scraping but publishes official RSS
feeds, so we use those. Barron&#x27;s scrape works, so we keep it.

WSJ pulls multiple sections including Real Estate. Barron&#x27;s is rendered with a
headless browser and extracted by article-URL pattern.
&quot;&quot;&quot;

import re
import sys
import html
import datetime as dt

import feedparser
from feedgen.feed import FeedGenerator
from playwright.sync_api import sync_playwright

# ---- WSJ official RSS feeds (section -&gt; URL) ----
WSJ_FEEDS = {
    &quot;Markets&quot;:     &quot;https://feeds.content.dowjones.io/public/rss/RSSMarketsMain&quot;,
    &quot;Business&quot;:    &quot;https://feeds.content.dowjones.io/public/rss/WSJcomUSBusiness&quot;,
    &quot;Real Estate&quot;: &quot;https://feeds.content.dowjones.io/public/rss/latestnewsrealestate&quot;,
}

# ---- Google News RSS: gets recent articles from sites with no own feed ----
# Technique: news.google.com/rss/search?q=when:24h+allinurl:&lt;domain&gt;
# Returns real RSS with timestamps. Used because Barron&#x27;s publishes no feed
# and its own pages only expose ~20 links to a scraper.
# Google News returned 0 items for Barron&#x27;s in testing, so it&#x27;s disabled.
GNEWS_FEEDS = {}

BARRONS = {
    &quot;name&quot;: &quot;Barron&#x27;s&quot;,
    &quot;pattern&quot;: r&quot;barrons\.com/articles/[A-Za-z0-9\-]+&quot;,
    &quot;min_title&quot;: 15,
    # /real-time renders only ~20 items. Rather than guess section URLs,
    # we start from these seeds and then AUTO-DISCOVER further section pages
    # from Barron&#x27;s own navigation links.
    # Barron&#x27;s paginates with a PATH segment: /real-time/2, /real-time/3 ...
    # (query params like ?page=2 are ignored and serve page 1). ~20 per page.
    &quot;seeds&quot;: [
        &quot;https://www.barrons.com/real-time&quot;,
        &quot;https://www.barrons.com/real-time/2&quot;,
        &quot;https://www.barrons.com/real-time/3&quot;,
        &quot;https://www.barrons.com/real-time/4&quot;,
        &quot;https://www.barrons.com/real-time/5&quot;,
        &quot;https://www.barrons.com/real-time/6&quot;,
        &quot;https://www.barrons.com/real-time/7&quot;,
        &quot;https://www.barrons.com/real-time/8&quot;,
    ],
    # how many auto-discovered section pages to visit
    &quot;max_discovered&quot;: 0,
    # skip obviously non-editorial paths when discovering
    &quot;skip_words&quot;: (&quot;subscribe&quot;, &quot;login&quot;, &quot;signin&quot;, &quot;account&quot;, &quot;customer&quot;,
                   &quot;advertis&quot;, &quot;privacy&quot;, &quot;terms&quot;, &quot;help&quot;, &quot;podcast&quot;, &quot;video&quot;,
                   &quot;watchlist&quot;, &quot;market-data&quot;, &quot;quote&quot;, &quot;author&quot;, &quot;newsletter&quot;,
                   &quot;magazine&quot;, &quot;print&quot;, &quot;gift&quot;, &quot;corporate&quot;, &quot;legal&quot;),
}

UA = (&quot;Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 &quot;
      &quot;(KHTML, like Gecko) Chrome/122.0 Safari/537.36&quot;)


def clean(t):
    if not t:
        return &quot;&quot;
    return html.unescape(&quot; &quot;.join(t.split())).strip()


def fetch_wsj():
    &quot;&quot;&quot;Pull all WSJ section feeds via RSS. Returns list of item dicts.&quot;&quot;&quot;
    items, seen = [], set()
    for section, url in WSJ_FEEDS.items():
        print(f&quot;[WSJ/{section}] fetching {url}&quot;, file=sys.stderr)
        try:
            # feedparser fetches and parses; set a UA to be safe
            fp = feedparser.parse(url, request_headers={&quot;User-Agent&quot;: UA})
            n = 0
            for e in fp.entries:
                title = clean(e.get(&quot;title&quot;))
                link = (e.get(&quot;link&quot;) or &quot;&quot;).split(&quot;#&quot;)[0]
                if len(title) &lt; 12 or not link or link in seen:
                    continue
                seen.add(link)
                ts = None
                if e.get(&quot;published_parsed&quot;):
                    try:
                        ts = dt.datetime(*e.published_parsed[:6],
                                         tzinfo=dt.timezone.utc)
                    except Exception:
                        ts = None
                items.append({&quot;title&quot;: title, &quot;link&quot;: link, &quot;source&quot;: &quot;WSJ&quot;,
                              &quot;section&quot;: section, &quot;ts&quot;: ts, &quot;order&quot;: len(items)})
                n += 1
            print(f&quot;[WSJ/{section}] {n} items&quot;, file=sys.stderr)
        except Exception as ex:
            print(f&quot;[WSJ/{section}] error: {ex}&quot;, file=sys.stderr)
    print(f&quot;[WSJ] total {len(items)} items&quot;, file=sys.stderr)
    return items


def fetch_gnews():
    &quot;&quot;&quot;
    Pull recent articles via Google News RSS for sources with no own feed.
    Gives real publish timestamps. Titles arrive as &quot;Headline - Barron&#x27;s&quot;,
    so we strip the trailing source name.
    &quot;&quot;&quot;
    items = []
    for source, url in GNEWS_FEEDS.items():
        print(f&quot;[GNews/{source}] fetching {url}&quot;, file=sys.stderr)
        try:
            fp = feedparser.parse(url, request_headers={&quot;User-Agent&quot;: UA})
            seen = set()
            n = 0
            for e in fp.entries:
                title = clean(e.get(&quot;title&quot;))
                # strip trailing &quot; - Barron&#x27;s&quot; / &quot; - Barrons&quot; style suffix
                title = re.sub(r&quot;\s+-\s+[^-]{2,30}$&quot;, &quot;&quot;, title).strip()
                link = (e.get(&quot;link&quot;) or &quot;&quot;).split(&quot;#&quot;)[0]
                if len(title) &lt; 12 or not link or link in seen:
                    continue
                seen.add(link)
                ts = None
                if e.get(&quot;published_parsed&quot;):
                    try:
                        ts = dt.datetime(*e.published_parsed[:6],
                                         tzinfo=dt.timezone.utc)
                    except Exception:
                        ts = None
                items.append({&quot;title&quot;: title, &quot;link&quot;: link, &quot;source&quot;: source,
                              &quot;section&quot;: &quot;&quot;, &quot;ts&quot;: ts, &quot;order&quot;: len(items)})
                n += 1
            print(f&quot;[GNews/{source}] {n} items&quot;, file=sys.stderr)
        except Exception as ex:
            print(f&quot;[GNews/{source}] error: {ex}&quot;, file=sys.stderr)
    return items


def scrape_barrons_page(page, url, pat, min_title, seen):
    &quot;&quot;&quot;Scrape one Barron&#x27;s section page. Returns list of new items.&quot;&quot;&quot;
    print(f&quot;[Barron&#x27;s] loading {url}&quot;, file=sys.stderr)
    try:
        page.goto(url, wait_until=&quot;domcontentloaded&quot;, timeout=45000)
    except Exception as e:
        print(f&quot;[Barron&#x27;s]   goto error: {e}&quot;, file=sys.stderr)
        return []
    page.wait_for_timeout(3000)
    # Diagnostics: is this page real content, or a block/paywall shell?
    try:
        ttl = page.title()
        print(f&quot;[Barron&#x27;s]   title: {ttl!r}&quot;, file=sys.stderr)
    except Exception:
        pass
    try:
        sample = page.eval_on_selector(&quot;body&quot;, &quot;el =&gt; el.innerText.slice(0,140)&quot;)
        print(f&quot;[Barron&#x27;s]   body: {&#x27; &#x27;.join((sample or &#x27;&#x27;).split())[:120]!r}&quot;,
              file=sys.stderr)
    except Exception:
        pass
    # Light scroll to ensure lazy images/links render (pagination does the
    # heavy lifting via /real-time/N, so no load-more clicking needed).
    try:
        for _ in range(3):
            page.mouse.wheel(0, 5000)
            page.wait_for_timeout(600)
    except Exception:
        pass

    # how many barrons links exist at all vs. article links?
    try:
        stats = page.eval_on_selector_all(
            &quot;a[href]&quot;,
            &quot;&quot;&quot;els =&gt; {
                const all = els.filter(e =&gt; (e.href||&#x27;&#x27;).includes(&#x27;barrons.com&#x27;));
                const arts = all.filter(e =&gt; (e.href||&#x27;&#x27;).includes(&#x27;/articles/&#x27;));
                return {allLinks: els.length, barrons: all.length, articles: arts.length};
            }&quot;&quot;&quot;)
        print(f&quot;[Barron&#x27;s]   links: {stats}&quot;, file=sys.stderr)
    except Exception:
        pass
    try:
        anchors = page.eval_on_selector_all(
            &quot;a[href]&quot;,
            &quot;&quot;&quot;els =&gt; els.map(e =&gt; {
                // climb to a container likely holding the &quot;X hours ago&quot; label
                let ctx = e;
                for (let i = 0; i &lt; 5 &amp;&amp; ctx.parentElement; i++) {
                    ctx = ctx.parentElement;
                    if (ctx.innerText &amp;&amp; ctx.innerText.length &gt; e.innerText.length + 8) break;
                }
                let extra = &quot;&quot;;
                try {
                    if (ctx.previousElementSibling) extra += &quot; &quot; + ctx.previousElementSibling.innerText;
                    if (ctx.nextElementSibling)     extra += &quot; &quot; + ctx.nextElementSibling.innerText;
                    if (e.nextElementSibling)       extra += &quot; &quot; + e.nextElementSibling.innerText;
                } catch (err) {}
                return {href: e.href, text: e.innerText,
                        ctx: (ctx ? ctx.innerText : &quot;&quot;) + extra};
            })&quot;&quot;&quot;)
    except Exception as e:
        print(f&quot;[Barron&#x27;s]   anchor read error: {e}&quot;, file=sys.stderr)
        return []

    now_utc = dt.datetime.now(dt.timezone.utc)
    found = []
    dated = 0
    for a in anchors:
        href = a.get(&quot;href&quot;) or &quot;&quot;
        if not pat.search(href):
            continue
        title = clean(a.get(&quot;text&quot;))
        if len(title) &lt; min_title:
            continue
        link = href.split(&quot;#&quot;)[0].split(&quot;?&quot;)[0]
        if link in seen:
            continue
        seen.add(link)

        # Barron&#x27;s shows relative times like &quot;2 hours ago&quot; / &quot;15 minutes ago&quot;
        ts = None
        ctx = (a.get(&quot;ctx&quot;) or &quot;&quot;).lower()
        m = re.search(
            r&quot;(\d+)\s*(second|sec|minute|min|hour|hr|day)s?\s*ago&quot;, ctx)
        if m:
            qty = int(m.group(1))
            unit = m.group(2)
            if unit in (&quot;second&quot;, &quot;sec&quot;):
                delta = dt.timedelta(seconds=qty)
            elif unit in (&quot;minute&quot;, &quot;min&quot;):
                delta = dt.timedelta(minutes=qty)
            elif unit in (&quot;hour&quot;, &quot;hr&quot;):
                delta = dt.timedelta(hours=qty)
            else:
                delta = dt.timedelta(days=qty)
            ts = now_utc - delta
            dated += 1

        found.append({&quot;title&quot;: title, &quot;link&quot;: link, &quot;source&quot;: &quot;Barron&#x27;s&quot;,
                      &quot;section&quot;: &quot;&quot;, &quot;ts&quot;: ts, &quot;order&quot;: 0})
    print(f&quot;[Barron&#x27;s]   +{len(found)} new from this page &quot;
          f&quot;({dated} with time)&quot;, file=sys.stderr)
    return found


def scrape_barrons(page):
    &quot;&quot;&quot;
    Barron&#x27;s has no public RSS and /real-time shows only ~20 items.
    Strategy: scrape seed pages, then auto-discover more section pages from
    Barron&#x27;s own nav links (so we never depend on guessed URLs), and scrape
    those too. All results deduped by article URL.
    &quot;&quot;&quot;
    site = BARRONS
    pat = re.compile(site[&quot;pattern&quot;])
    seen = set()
    items = []
    visited = set()

    # 1) scrape the seed pages
    for url in site[&quot;seeds&quot;]:
        if url in visited:
            continue
        visited.add(url)
        try:
            items.extend(scrape_barrons_page(page, url, pat,
                                             site[&quot;min_title&quot;], seen))
        except Exception as e:
            print(f&quot;[Barron&#x27;s] page error {url}: {e}&quot;, file=sys.stderr)

    # 2) discover candidate section pages from the links already on the page
    discovered = []
    try:
        hrefs = page.eval_on_selector_all(
            &quot;a[href]&quot;, &quot;els =&gt; els.map(e =&gt; e.href)&quot;)
        for h in hrefs:
            if &quot;barrons.com&quot; not in h:
                continue
            if &quot;/articles/&quot; in h:          # that&#x27;s an article, not a section
                continue
            low = h.lower()
            if any(w in low for w in site[&quot;skip_words&quot;]):
                continue
            clean_url = h.split(&quot;#&quot;)[0].split(&quot;?&quot;)[0].rstrip(&quot;/&quot;)
            # want section-ish paths: barrons.com/&lt;something&gt;[/&lt;something&gt;]
            tail = clean_url.replace(&quot;https://www.barrons.com&quot;, &quot;&quot;).strip(&quot;/&quot;)
            if not tail or tail.count(&quot;/&quot;) &gt; 1 or len(tail) &lt; 3:
                continue
            if clean_url in visited or clean_url in discovered:
                continue
            discovered.append(clean_url)
    except Exception as e:
        print(f&quot;[Barron&#x27;s] discovery error: {e}&quot;, file=sys.stderr)

    discovered = discovered[: site[&quot;max_discovered&quot;]]
    if discovered:
        print(f&quot;[Barron&#x27;s] auto-discovered {len(discovered)} section pages&quot;,
              file=sys.stderr)

    # 3) scrape the discovered sections
    for url in discovered:
        if url in visited:
            continue
        visited.add(url)
        try:
            items.extend(scrape_barrons_page(page, url, pat,
                                             site[&quot;min_title&quot;], seen))
        except Exception as e:
            print(f&quot;[Barron&#x27;s] page error {url}: {e}&quot;, file=sys.stderr)

    for i, it in enumerate(items):
        it[&quot;order&quot;] = i
    print(f&quot;[Barron&#x27;s] extracted {len(items)} headlines total&quot;, file=sys.stderr)
    return items


def assign_ranks(items):
    now = dt.datetime.now(dt.timezone.utc)
    counts = {}
    for it in items:
        counts[it[&quot;source&quot;]] = max(counts.get(it[&quot;source&quot;], 0), it[&quot;order&quot;] + 1)
    for it in items:
        if it.get(&quot;ts&quot;):
            it[&quot;sort_dt&quot;] = it[&quot;ts&quot;]
        else:
            n = counts.get(it[&quot;source&quot;], 1)
            frac = 1 - (it[&quot;order&quot;] / max(n, 1))
            it[&quot;sort_dt&quot;] = now - dt.timedelta(hours=12 * (1 - frac))


def main():
    all_items = []
    # WSJ via RSS (no browser needed)
    all_items.extend(fetch_wsj())
    # Barron&#x27;s via Google News RSS (last 24h) — its own site has no feed
    all_items.extend(fetch_gnews())
    # Barron&#x27;s via browser
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(args=[&quot;--no-sandbox&quot;])
            ctx = browser.new_context(
                locale=&quot;en-US&quot;, timezone_id=&quot;America/New_York&quot;,
                user_agent=UA, viewport={&quot;width&quot;: 1280, &quot;height&quot;: 1600})
            page = ctx.new_page()
            all_items.extend(scrape_barrons(page))
            browser.close()
    except Exception as e:
        print(f&quot;[Barron&#x27;s] browser error: {e}&quot;, file=sys.stderr)

    # Dedup by link AND by normalized title (Google News links differ from
    # direct barrons.com links for the same article)
    seen, seen_titles, deduped = set(), set(), []
    for it in all_items:
        key_t = re.sub(r&quot;[^a-z0-9]+&quot;, &quot;&quot;, it[&quot;title&quot;].lower())[:60]
        if it[&quot;link&quot;] in seen or (key_t and key_t in seen_titles):
            continue
        seen.add(it[&quot;link&quot;])
        if key_t:
            seen_titles.add(key_t)
        deduped.append(it)

    assign_ranks(deduped)
    deduped.sort(key=lambda it: it[&quot;sort_dt&quot;], reverse=True)
    print(f&quot;TOTAL: {len(deduped)} unique headlines&quot;)

    now = dt.datetime.now(dt.timezone.utc)
    fg = FeedGenerator()
    fg.title(&quot;English Finance Aggregator — WSJ &amp; Barron&#x27;s&quot;)
    fg.link(href=&quot;https://example.github.io/Ifin-News/en/feed.xml&quot;, rel=&quot;self&quot;)
    fg.description(&quot;Combined: WSJ (Markets, Business, Real Estate) + Barron&#x27;s&quot;)
    fg.language(&quot;en&quot;)
    fg.lastBuildDate(now)
    for it in reversed(deduped):
        fe = fg.add_entry()
        label = it[&quot;source&quot;] + (f&quot;/{it[&#x27;section&#x27;]}&quot; if it.get(&quot;section&quot;) else &quot;&quot;)
        fe.title(f&quot;[{label}] {it[&#x27;title&#x27;]}&quot;)
        fe.link(href=it[&quot;link&quot;])
        fe.guid(it[&quot;link&quot;], permalink=True)
        fe.description(it[&quot;title&quot;])
        fe.pubDate(it[&quot;sort_dt&quot;])
    fg.rss_file(&quot;en-feed.xml&quot;, pretty=True)
    print(&quot;Wrote en-feed.xml&quot;)

    write_html(deduped, now)
    print(&quot;Wrote en.html&quot;)

    from collections import Counter
    c = Counter(it[&quot;source&quot;] for it in deduped)
    for s in [&quot;WSJ&quot;, &quot;Barron&#x27;s&quot;]:
        print(f&quot;  {s}: {c.get(s,0)}&quot;)


SITE_COLORS = {&quot;WSJ&quot;: &quot;#0080c6&quot;, &quot;Barron&#x27;s&quot;: &quot;#00625b&quot;}


def write_html(items, now):
    rows = []
    for it in items:
        color = SITE_COLORS.get(it[&quot;source&quot;], &quot;#666&quot;)
        t = it[&quot;sort_dt&quot;].strftime(&quot;%H:%M&quot;) if it.get(&quot;ts&quot;) else &quot;—&quot;
        label = it[&quot;source&quot;] + (f&quot; · {it[&#x27;section&#x27;]}&quot; if it.get(&quot;section&quot;) else &quot;&quot;)
        title = html.escape(it[&quot;title&quot;])
        rows.append(
            f&#x27;&lt;a class=&quot;item&quot; data-src=&quot;{html.escape(it[&quot;source&quot;])}&quot; data-title=&quot;{title.lower()}&quot; &#x27;
            f&#x27;href=&quot;{html.escape(it[&quot;link&quot;])}&quot; target=&quot;_blank&quot; rel=&quot;noopener&quot;&gt;&#x27;
            f&#x27;&lt;span class=&quot;tag&quot; style=&quot;background:{color}&quot;&gt;{html.escape(label)}&lt;/span&gt;&#x27;
            f&#x27;&lt;span class=&quot;ttl&quot;&gt;{title}&lt;/span&gt;&#x27;
            f&#x27;&lt;span class=&quot;tm&quot;&gt;{t}&lt;/span&gt;&lt;/a&gt;&#x27;)
    body = &quot;\n&quot;.join(rows)
    btns = [&#x27;&lt;button class=&quot;fbtn active&quot; data-f=&quot;all&quot; onclick=&quot;flt(this)&quot;&gt;All&lt;/button&gt;&#x27;]
    for name in [&quot;WSJ&quot;, &quot;Barron&#x27;s&quot;]:
        color = SITE_COLORS[name]
        btns.append(
            f&#x27;&lt;button class=&quot;fbtn&quot; data-f=&quot;{html.escape(name)}&quot; onclick=&quot;flt(this)&quot; &#x27;
            f&#x27;style=&quot;--c:{color}&quot;&gt;{html.escape(name)}&lt;/button&gt;&#x27;)
    buttons = &quot;\n&quot;.join(btns)

    doc = f&quot;&quot;&quot;&lt;!doctype html&gt;&lt;html lang=&quot;en&quot;&gt;&lt;head&gt;
&lt;meta charset=&quot;utf-8&quot;&gt;&lt;meta name=&quot;viewport&quot; content=&quot;width=device-width,initial-scale=1&quot;&gt;
&lt;title&gt;English Finance Aggregator&lt;/title&gt;
&lt;style&gt;
  :root {{ color-scheme: light dark; }}
  * {{ box-sizing: border-box; }}
  body {{ font-family: -apple-system,&quot;Segoe UI&quot;,Arial,sans-serif; max-width:780px;
         margin:0 auto; padding:1rem; background:#fafafa; color:#111; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background:#16181c; color:#eee; }}
    .item {{ border-color:#2a2d33 !important; }} .item:hover {{ background:#1e2127 !important; }}
    .fbtn {{ background:#23262d; color:#ddd; border-color:#333 !important; }}
    .stickybar {{ background:#16181c !important; border-bottom-color:#2a2d33 !important; }}
    #q {{ background:#23262d !important; color:#eee !important; border-color:#333 !important; }} }}
  h1 {{ font-size:1.3rem; margin:.3rem 0; }}
  .sub {{ color:#888; font-size:.8rem; margin-bottom:.7rem; }}
  .stickybar {{ position:sticky; top:0; z-index:20; background:#fafafa;
               padding:.5rem 0 .4rem; border-bottom:1px solid #e5e5e5; }}
  #q {{ width:100%; padding:.6rem .8rem; margin-bottom:.5rem; border:1px solid #ccc;
       border-radius:8px; font-size:.95rem; background:#fff; color:#111; }}
  .filters {{ display:flex; flex-wrap:wrap; gap:.4rem; }}
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
&lt;/style&gt;&lt;/head&gt;&lt;body&gt;
&lt;h1&gt;English Finance Aggregator&lt;/h1&gt;
&lt;div class=&quot;sub&quot;&gt;WSJ · Barron&#x27;s — {len(items)} headlines ·
updated {now.strftime(&#x27;%d/%m %H:%M&#x27;)} UTC · &lt;a href=&quot;feed.xml&quot;&gt;RSS&lt;/a&gt; · &lt;a href=&quot;../&quot;&gt;‹ עברית&lt;/a&gt;&lt;/div&gt;
&lt;div class=&quot;stickybar&quot;&gt;
&lt;input id=&quot;q&quot; type=&quot;search&quot; placeholder=&quot;Search headlines…&quot; oninput=&quot;applyFilters()&quot; /&gt;
&lt;div class=&quot;filters&quot;&gt;
{buttons}
&lt;/div&gt;
&lt;/div&gt;
{body}
&lt;script&gt;
var curSrc = &#x27;all&#x27;;
function flt(btn) {{
  curSrc = btn.getAttribute(&#x27;data-f&#x27;);
  document.querySelectorAll(&#x27;.fbtn&#x27;).forEach(function(b){{ b.classList.remove(&#x27;active&#x27;); }});
  btn.classList.add(&#x27;active&#x27;);
  applyFilters();
}}
function applyFilters() {{
  var q = (document.getElementById(&#x27;q&#x27;).value || &#x27;&#x27;).trim().toLowerCase();
  document.querySelectorAll(&#x27;.item&#x27;).forEach(function(it){{
    var okSrc = (curSrc === &#x27;all&#x27; || it.getAttribute(&#x27;data-src&#x27;) === curSrc);
    var okQ = (q === &#x27;&#x27; || (it.getAttribute(&#x27;data-title&#x27;) || &#x27;&#x27;).indexOf(q) !== -1);
    if (okSrc &amp;&amp; okQ) it.classList.remove(&#x27;hide&#x27;);
    else it.classList.add(&#x27;hide&#x27;);
  }});
}}
&lt;/script&gt;
&lt;/body&gt;&lt;/html&gt;&quot;&quot;&quot;
    with open(&quot;en.html&quot;, &quot;w&quot;, encoding=&quot;utf-8&quot;) as f:
        f.write(doc)


if __name__ == &quot;__main__&quot;:
    main()
</textarea></body></html>
