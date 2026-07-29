#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import shutil
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from common import ROOT, clean_text, load_json, parse_date, utc_now


SITE = ROOT / "site"
EASTERN = ZoneInfo("America/New_York")
EVIDENCE_ORDER = [
    "MEASURED",
    "ESTIMATED",
    "FILED",
    "ADJUDGED",
    "REPORTED",
    "INTERPRETED",
]
EVIDENCE_DEFINITIONS = {
    "MEASURED": "An instrument, transaction system, administrative process, or accounting record directly produced the underlying observation.",
    "ESTIMATED": "Sampling, modeling, seasonal adjustment, imputation, or projection materially contributes to the reported value.",
    "FILED": "A party formally submitted the record. This verifies the submission and attribution, not every assertion in it.",
    "ADJUDGED": "A court issued the disposition or opinion. The label identifies legal effect, not independent proof of every factual recital.",
    "REPORTED": "An identified institution or person made the statement or published the finding.",
    "INTERPRETED": "The item primarily synthesizes, argues, explains, or forecasts from other facts.",
}


def esc(value: Any) -> str:
    return html.escape(clean_text(value), quote=True)


def root_for(path: str) -> str:
    depth = len([part for part in Path(path).parent.parts if part not in {".", ""}])
    return "../" * depth or "./"


def page(
    path: str,
    title: str,
    description: str,
    content: str,
    active_nav: str,
    updated: str,
) -> None:
    root = root_for(path)
    template = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
    values = {
        "TITLE": esc(title),
        "DESCRIPTION": esc(description),
        "ROOT": root,
        "CONTENT": content,
        "UPDATED": esc(updated),
        "NAV_LATEST": ' aria-current="page"' if active_nav == "latest" else "",
        "NAV_SOURCES": ' aria-current="page"' if active_nav == "sources" else "",
        "NAV_ARCHIVE": ' aria-current="page"' if active_nav == "archive" else "",
        "NAV_METHOD": ' aria-current="page"' if active_nav == "method" else "",
    }
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", value)
    target = SITE / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(template, encoding="utf-8")


def item_local(item: dict[str, Any]) -> datetime:
    published = parse_date(item["published"])
    if item.get("published_precision") == "date":
        return datetime(
            published.year,
            published.month,
            published.day,
            tzinfo=EASTERN,
        )
    return published.astimezone(EASTERN)


def render_item(item: dict[str, Any]) -> str:
    local = item_local(item)
    time_display = (
        f"{local.strftime('%b %-d')}<br>{local.strftime('%Y')}"
        if item.get("published_precision") == "date"
        else f"{local.strftime('%-I:%M %p')}<br>{local.strftime('%b %-d')}"
    )
    evidence = clean_text(item["evidence"]).upper()
    channel = clean_text(item["channel"]).lower()
    author = esc(item.get("author"))
    source = esc(item.get("publisher") or item.get("source_name"))
    meta_bits = [source]
    if author and author.casefold() != source.casefold():
        meta_bits.append(author)
    meta_bits.append(esc(item.get("geography")))
    if item.get("person_name"):
        meta_bits.append(f"Subject: {esc(item['person_name'])}")
    summary = (
        f'<p class="item-summary">{esc(item["summary"])}</p>'
        if clean_text(item.get("summary"))
        else ""
    )
    people_badge = (
        '<span class="badge badge-people">People</span>'
        if item.get("person_id")
        else ""
    )
    match = (
        f'<span title="Automatic match rule">{esc(item.get("match_basis"))}</span>'
        if item.get("match_basis")
        else ""
    )
    return f"""
<article class="item" data-item="{esc(item['id'])}" data-evidence="{esc(evidence)}" data-channel="{esc(channel)}">
  <time class="item-time" datetime="{esc(item['published'])}">{time_display}</time>
  <div>
    <div class="item-meta">
      <span class="badge badge-{evidence.lower()}">{esc(evidence)}</span>
      {people_badge}
      <span>{esc(channel)}</span>
    </div>
    <h3><a class="item-link" href="{esc(item['url'])}" rel="noopener">{esc(item['title'])}</a></h3>
    {summary}
    <div class="item-meta">
      <span>{" · ".join(meta_bits)}</span>
      {match}
      <button class="save-button" type="button" data-save="{esc(item['id'])}" aria-pressed="false">Save for later</button>
    </div>
  </div>
</article>"""


def render_stream(items: list[dict[str, Any]], controls: bool = True) -> str:
    if not items:
        return '<p class="empty-state">No collected items are available yet.</p>'
    evidence_values = [value for value in EVIDENCE_ORDER if any(item["evidence"] == value for item in items)]
    channel_values = sorted({clean_text(item["channel"]).lower() for item in items})
    tools = ""
    if controls:
        evidence_options = "".join(
            f'<option value="{esc(value)}">{esc(value.title())}</option>'
            for value in evidence_values
        )
        channel_options = "".join(
            f'<option value="{esc(value)}">{esc(value.title())}</option>'
            for value in channel_values
        )
        tools = f"""
<div class="stream-tools" aria-label="Filter the record">
  <div class="tool">
    <label for="filter-evidence">Record type</label>
    <select id="filter-evidence"><option value="">All types</option>{evidence_options}</select>
  </div>
  <div class="tool">
    <label for="filter-channel">Subject</label>
    <select id="filter-channel"><option value="">All subjects</option>{channel_options}</select>
  </div>
  <div class="tool">
    <label for="filter-search">Within these items</label>
    <input id="filter-search" type="search" autocomplete="off" placeholder="Filter words">
  </div>
  <button id="filter-saved" type="button" aria-pressed="false">Saved only</button>
</div>"""
    chunks = [tools, '<div data-stream>']
    current_date = None
    for item in items:
        local = item_local(item)
        date_key = local.date().isoformat()
        if date_key != current_date:
            chunks.append(
                f'<h2 class="date-heading" data-date-group>{local.strftime("%A · %B %-d, %Y")}</h2>'
            )
            current_date = date_key
        chunks.append(render_item(item))
    chunks.append("</div>")
    return "".join(chunks)


def freshness_text(updated_at: str | None) -> str:
    if not updated_at:
        return "Awaiting first collection"
    local = parse_date(updated_at).astimezone(EASTERN)
    return local.strftime("%B %-d, %Y at %-I:%M %p ET")


def render_home(
    items: list[dict[str, Any]],
    statuses: dict[str, Any],
    updated_at: str | None,
) -> str:
    shown = items[:30]
    active_statuses = [
        status for key, status in statuses.items() if key != "people-watch"
    ]
    good = sum(1 for status in active_statuses if status.get("ok"))
    failed = sum(1 for status in active_statuses if not status.get("ok"))
    people_items = sum(1 for item in shown if item.get("person_id"))
    evidence_count = Counter(item["evidence"] for item in shown)
    evidence_line = " · ".join(
        f"{evidence_count[key]} {key.lower()}"
        for key in EVIDENCE_ORDER
        if evidence_count[key]
    )
    status_class = "status-good" if failed == 0 else "status-warn"
    content = f"""
<section class="lede-grid">
  <div>
    <p class="eyebrow">A chronological public record</p>
    <h1>Look past<br>the argument.</h1>
    <p class="subtitle">Measurements, estimates, filings, adjudications, and attributed reports—kept distinct, linked to their sources, and ordered by time.</p>
  </div>
  <aside class="margin-note">
    <p><strong>Selection is not neutrality.</strong> This observatory recommends attention by deciding what enters the frame. It therefore exposes its source list, labels the kind of evidence, and declines to rank by engagement.</p>
    <p>Source-supplied titles and descriptions are reproduced in abbreviated form. No machine-written synopsis is inserted between the record and the reader.</p>
  </aside>
</section>
<div class="stream-layout">
  <section class="stream" aria-labelledby="latest-heading">
    <div class="section-label"><span id="latest-heading">Latest <span data-visible-count>{len(shown)}</span> of {len(items)}</span></div>
    {render_stream(shown)}
  </section>
  <aside class="side-panel" aria-label="Field notes">
    <section>
      <h2>Collection status</h2>
      <p class="{status_class}">{good} source adapters responding · {failed} reporting a collection failure.</p>
      <p>Last assembled {esc(freshness_text(updated_at))}.</p>
    </section>
    <section>
      <h2>This page</h2>
      <p>{esc(evidence_line or "No classified items yet")}.</p>
      <p>{people_items} people-watch match{"es" if people_items != 1 else ""} in the visible record.</p>
    </section>
    <section>
      <h2>Reading order</h2>
      <ul>
        <li>Newest publication time first.</li>
        <li>Visited links change color locally.</li>
        <li>“Save for later” remains in this browser.</li>
        <li>Overflow remains in the date archive.</li>
      </ul>
    </section>
    <section>
      <h2>Classification</h2>
      <p>The badge describes what the linked item can directly establish. It is not a general quality score.</p>
      <p><a href="./method/index.html">Read the operational rules</a>.</p>
    </section>
  </aside>
</div>"""
    return content


def render_sources(
    sources: list[dict[str, Any]], statuses: dict[str, Any]
) -> str:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for source in sources:
        grouped[clean_text(source.get("channel")) or "other"].append(source)
    chunks = [
        """
<article class="prose">
  <p class="eyebrow">Visible inclusion rules</p>
  <h1>Sources</h1>
  <p class="subtitle">The catalog separates direct records from estimates and interpretation. “Collected” means an adapter currently places new items in the chronology; “reference” means the source is approved but awaits a narrow query or adapter.</p>
</article>"""
    ]
    for channel in sorted(grouped):
        chunks.append(f'<section class="source-group"><h2>{esc(channel.title())}</h2>')
        for source in sorted(grouped[channel], key=lambda value: value["name"]):
            status = statuses.get(source["id"])
            if source.get("active"):
                operational = (
                    "Collected · responding"
                    if status and status.get("ok")
                    else "Collected · adapter issue"
                    if status
                    else "Collected · awaiting first run"
                )
            else:
                operational = "Reference · adapter not yet configured"
            chunks.append(
                f"""
<article class="source-card">
  <div>
    <span class="badge badge-{esc(source['evidence'].lower())}">{esc(source['evidence'])}</span>
    <h3><a href="{esc(source['homepage'])}" rel="noopener">{esc(source['name'])}</a></h3>
    <p class="item-meta">{esc(source.get('geography'))} · {esc(source.get('cadence'))}</p>
    <p class="item-meta">{esc(operational)}</p>
  </div>
  <div class="source-details">
    <p>{esc(source.get('why'))}</p>
    <p class="caveat">Limit: {esc(source.get('caveat'))}</p>
  </div>
</article>"""
            )
        chunks.append("</section>")
    return "".join(chunks)


def render_method() -> str:
    definitions = "".join(
        f'<dt><span class="badge badge-{key.lower()}">{key}</span></dt><dd>{esc(EVIDENCE_DEFINITIONS[key])}</dd>'
        for key in EVIDENCE_ORDER
    )
    return f"""
<article class="prose">
  <p class="eyebrow">Rules before rankings</p>
  <h1>Method</h1>
  <p class="subtitle">Reliability belongs to a claim made from a record, not permanently to a publisher. The system therefore describes provenance and evidence class rather than issuing a universal source score.</p>

  <h2>What the badges mean</h2>
  <dl class="definition-list">{definitions}</dl>

  <h2>What “verified” does not mean</h2>
  <p>The collector verifies that it retrieved an item from the configured endpoint and preserves the source, title, time, and link. It does not independently verify every proposition in the item. A company filing establishes a representation; a court opinion establishes a disposition; a survey release establishes an estimate produced under a stated method.</p>

  <h2>Selection and order</h2>
  <p>There is no engagement score, sentiment score, or personalized importance score. Items enter through explicit sources or subject queries and appear by publication time. The home page shows the newest thirty. Once observed, items with publication dates in the preceding 365 days remain accessible by date.</p>

  <h2>People-watch matches</h2>
  <p>The private registry is not committed to the public repository. Direct feeds and stable author identifiers are accepted as direct matches. Broad-news results must contain the canonical name or an alias and, unless the name is marked distinctive, at least one configured disambiguating term. Configured exclusions are then applied. The process has no review queue; ambiguous results below the fixed threshold are discarded.</p>

  <h2>Text and attribution</h2>
  <p>The site republishes only compact source-supplied metadata: title, a shortened description when present, publisher, date, and link. It does not generate summaries. A linked source may later revise, retract, remove, or correct an item; the next collection can update metadata but cannot guarantee immediate detection of every change.</p>

  <h2>Operational limits</h2>
  <p>Automated access can fail, feeds can change without notice, timestamps can be wrong, and public search indexes are incomplete. Source status is displayed. Silence means “nothing was collected,” not “nothing happened.” The source catalog states the principal structural limitation of each source.</p>

  <blockquote>This page is generated automatically from public sources. Inclusion means only that an item matched a configured source or subject query; it does not imply endorsement, verification, importance, or agreement.</blockquote>
</article>"""


def render_archive_index(items: list[dict[str, Any]], root: str = "../") -> str:
    counts = Counter(item_local(item).date().isoformat() for item in items)
    rows = []
    for date_key in sorted(counts, reverse=True):
        date_value = datetime.fromisoformat(date_key)
        rows.append(
            f'<li><a href="./{date_key}/index.html">{date_value.strftime("%B %-d, %Y")}</a><span>{counts[date_key]} item{"s" if counts[date_key] != 1 else ""}</span></li>'
        )
    listing = "".join(rows) or "<li>No archived dates yet.</li>"
    return f"""
<article class="prose">
  <p class="eyebrow">Prospective record</p>
  <h1>Archive</h1>
  <p class="subtitle">Items are retained from the date collection began. The archive is chronological and does not imply a retrospective claim of completeness.</p>
  <ul class="archive-list">{listing}</ul>
</article>"""


def write_feed(items: list[dict[str, Any]], updated_at: str | None) -> None:
    def xml(value: Any) -> str:
        return html.escape(clean_text(value), quote=False)

    entries = []
    for item in items[:50]:
        entries.append(
            f"""  <item>
    <guid isPermaLink="false">{xml(item['id'])}</guid>
    <title>{xml(item['title'])}</title>
    <link>{xml(item['url'])}</link>
    <pubDate>{parse_date(item['published']).strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate>
    <description>{xml(item.get('summary'))}</description>
    <category>{xml(item['evidence'])}</category>
    <source url="{xml(item['url'])}">{xml(item.get('publisher'))}</source>
  </item>"""
        )
    feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <title>Observatory</title>
  <link>https://barasch.github.io/observatory/</link>
  <description>Measurements, estimates, filings, adjudications, and attributed reports.</description>
  <lastBuildDate>{parse_date(updated_at or utc_now()).strftime('%a, %d %b %Y %H:%M:%S +0000')}</lastBuildDate>
{chr(10).join(entries)}
</channel>
</rss>
"""
    (SITE / "feed.xml").write_text(feed, encoding="utf-8")


def build() -> int:
    source_data = load_json(ROOT / "config" / "sources.json", {"sources": []})
    item_data = load_json(ROOT / "data" / "items.json", {"items": [], "updated_at": None})
    status_data = load_json(ROOT / "data" / "status.json", {"sources": {}})
    sources = source_data.get("sources", [])
    items = item_data.get("items", [])
    statuses = status_data.get("sources", {})
    items.sort(key=lambda item: (item["published"], item["id"]), reverse=True)
    updated_display = freshness_text(item_data.get("updated_at"))

    if SITE.exists():
        shutil.rmtree(SITE)
    (SITE / "assets").mkdir(parents=True)
    shutil.copy2(ROOT / "site_src" / "styles.css", SITE / "assets" / "styles.css")
    shutil.copy2(ROOT / "site_src" / "app.js", SITE / "assets" / "app.js")
    shutil.copy2(ROOT / "site_src" / "mark.svg", SITE / "assets" / "mark.svg")

    page(
        "index.html",
        "Latest",
        "A chronological record of measurements, estimates, filings, adjudications, and attributed reports.",
        render_home(items, statuses, item_data.get("updated_at")),
        "latest",
        updated_display,
    )
    page(
        "sources/index.html",
        "Sources",
        "The Observatory source catalog, collection status, and structural limitations.",
        render_sources(sources, statuses),
        "sources",
        updated_display,
    )
    page(
        "method/index.html",
        "Method",
        "The Observatory inclusion, classification, ordering, and people-watch rules.",
        render_method(),
        "method",
        updated_display,
    )
    page(
        "archive/index.html",
        "Archive",
        "The Observatory prospective date archive.",
        render_archive_index(items),
        "archive",
        updated_display,
    )

    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        by_date[item_local(item).date().isoformat()].append(item)
    for date_key, date_items in by_date.items():
        heading = datetime.fromisoformat(date_key).strftime("%B %-d, %Y")
        content = f"""
<article class="prose">
  <p class="eyebrow">Date record</p>
  <h1>{esc(heading)}</h1>
  <p class="subtitle">{len(date_items)} collected item{"s" if len(date_items) != 1 else ""}, ordered by publication time.</p>
</article>
<section class="stream" aria-label="{esc(heading)} items">
  {render_stream(date_items)}
</section>"""
        page(
            f"archive/{date_key}/index.html",
            heading,
            f"Observatory items published on {heading}.",
            content,
            "archive",
            updated_display,
        )

    (SITE / "data").mkdir(parents=True)
    shutil.copy2(ROOT / "data" / "items.json", SITE / "data" / "items.json")
    shutil.copy2(ROOT / "data" / "status.json", SITE / "data" / "status.json")
    (SITE / ".nojekyll").write_text("", encoding="utf-8")
    (SITE / "robots.txt").write_text(
        "User-agent: *\nAllow: /\nSitemap: https://barasch.github.io/observatory/sitemap.xml\n",
        encoding="utf-8",
    )
    urls = [
        "https://barasch.github.io/observatory/",
        "https://barasch.github.io/observatory/sources/",
        "https://barasch.github.io/observatory/archive/",
        "https://barasch.github.io/observatory/method/",
        *[
            f"https://barasch.github.io/observatory/archive/{date_key}/"
            for date_key in sorted(by_date, reverse=True)
        ],
    ]
    (SITE / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(f"  <url><loc>{html.escape(url)}</loc></url>\n" for url in urls)
        + "</urlset>\n",
        encoding="utf-8",
    )
    write_feed(items, item_data.get("updated_at"))
    (SITE / "404.html").write_text(
        (SITE / "index.html")
        .read_text(encoding="utf-8")
        .replace("<title>Latest · Observatory</title>", "<title>Not found · Observatory</title>")
        .replace(
            '<section class="lede-grid">',
            '<p class="eyebrow" style="margin-top:4rem">The requested page was not found.</p><section class="lede-grid">',
            1,
        ),
        encoding="utf-8",
    )
    return len(items)


def check_site() -> list[str]:
    errors = []
    required = [
        SITE / "index.html",
        SITE / "sources" / "index.html",
        SITE / "method" / "index.html",
        SITE / "archive" / "index.html",
        SITE / "assets" / "styles.css",
        SITE / "assets" / "app.js",
        SITE / "feed.xml",
    ]
    for path in required:
        if not path.exists() or path.stat().st_size == 0:
            errors.append(f"Missing or empty: {path.relative_to(ROOT)}")
    for path in SITE.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        if "{{" in text or "}}" in text:
            errors.append(f"Unresolved template token: {path.relative_to(ROOT)}")
        if "<title>" not in text or "<main" not in text:
            errors.append(f"Incomplete HTML shell: {path.relative_to(ROOT)}")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the Observatory static site.")
    parser.add_argument("--check", action="store_true", help="Validate generated output.")
    args = parser.parse_args()
    count = build()
    errors = check_site() if args.check else []
    if errors:
        raise SystemExit("\n".join(errors))
    print(f"Built site with {count} retained items.")


if __name__ == "__main__":
    main()
