#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from common import ROOT, clean_text, load_json, parse_date, utc_now


SITE = ROOT / "site"
EASTERN = ZoneInfo("America/New_York")
ARCHIVE_DAYS = 30
EVIDENCE_ORDER = [
    "MEASURED",
    "ESTIMATED",
    "FILED",
    "ADJUDGED",
    "REPORTED",
    "INTERPRETED",
]
EVIDENCE_LABELS = {
    "MEASURED": "Measured",
    "ESTIMATED": "Estimate",
    "FILED": "Filing",
    "ADJUDGED": "Adjudged",
    "REPORTED": "Report",
    "INTERPRETED": "Analysis",
}
EVIDENCE_SECTION_LABELS = {
    "MEASURED": "Measurements",
    "ESTIMATED": "Estimates",
    "FILED": "Filings",
    "ADJUDGED": "Adjudications",
    "REPORTED": "Reports",
    "INTERPRETED": "Analysis",
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
) -> None:
    root = root_for(path)
    template = (ROOT / "templates" / "base.html").read_text(encoding="utf-8")
    values = {
        "DOCUMENT_TITLE": esc(
            "Observatory" if title == "Observatory" else f"{title} · Observatory"
        ),
        "DESCRIPTION": esc(description),
        "ROOT": root,
        "CONTENT": content,
        "NAV_HOME": ' aria-current="page"' if active_nav == "home" else "",
        "NAV_ARCHIVE": ' aria-current="page"' if active_nav == "archive" else "",
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


def item_sort_key(item: dict[str, Any]) -> tuple[int, int, int, str]:
    local = item_local(item)
    known_time = item.get("published_precision") != "date"
    seconds = local.hour * 3600 + local.minute * 60 + local.second
    return (
        local.date().toordinal(),
        int(known_time),
        seconds if known_time else 0,
        clean_text(item.get("id")),
    )


def render_item(item: dict[str, Any]) -> str:
    local = item_local(item)
    time_display = (
        f"{local.strftime('%b %-d')}<br>{local.strftime('%Y')}"
        if item.get("published_precision") == "date"
        else f"{local.strftime('%-I:%M %p')}<br>{local.strftime('%b %-d')}"
    )
    evidence = clean_text(item["evidence"]).upper()
    evidence_label = EVIDENCE_LABELS.get(evidence, evidence.title())
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
      <span class="badge badge-{evidence.lower()}">{esc(evidence_label)}</span>
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


def render_tools(items: list[dict[str, Any]]) -> str:
    channel_values = sorted({clean_text(item["channel"]).lower() for item in items})
    channel_options = "".join(
        f'<option value="{esc(value)}">{esc(value.title())}</option>'
        for value in channel_values
    )
    return f"""
<div class="stream-tools" aria-label="Filter the record">
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


def render_stream(items: list[dict[str, Any]], controls: bool = True) -> str:
    if not items:
        return '<p class="empty-state">No collected items are available yet.</p>'
    chunks = [render_tools(items) if controls else "", '<div data-stream>']
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


def render_current_sections(items: list[dict[str, Any]]) -> str:
    category_items = [item for item in items if not item.get("person_id")]
    people_items = [item for item in items if item.get("person_id")]
    chunks = ['<div class="category-grid" data-stream>']

    for evidence in EVIDENCE_ORDER:
        section_items = [
            item for item in category_items if item.get("evidence") == evidence
        ]
        if not section_items:
            continue
        label = EVIDENCE_SECTION_LABELS.get(evidence, evidence.title())
        count = len(section_items)
        chunks.append(
            f"""
<details class="category-panel category-{esc(evidence.lower())}" data-category-section data-evidence-section="{esc(evidence)}" open>
  <summary>
    <span class="panel-title">{esc(label)}</span>
    <span class="panel-count" data-section-count>{count} item{"s" if count != 1 else ""}</span>
  </summary>
  <div class="category-items">
    {"".join(render_item(item) for item in section_items)}
  </div>
</details>"""
        )

    people_count = len(people_items)
    people_content = (
        "".join(render_item(item) for item in people_items)
        if people_items
        else '<p class="empty-state">No people matches in this edition.</p>'
    )
    chunks.append(
        f"""
<details class="category-panel people-panel" data-category-section data-people-section open>
  <summary>
    <span class="panel-title">People</span>
    <span class="panel-count" data-section-count>{people_count} match{"es" if people_count != 1 else ""}</span>
  </summary>
  <div class="category-items">
    {people_content}
  </div>
</details>"""
    )
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
    active_statuses = list(statuses.values())
    good = sum(1 for status in active_statuses if status.get("ok"))
    failed = sum(1 for status in active_statuses if not status.get("ok"))
    people_items = sum(1 for item in shown if item.get("person_id"))
    evidence_count = Counter(
        item["evidence"] for item in shown if not item.get("person_id")
    )
    evidence_summary = "".join(
        f"""<span class="status-count">
          <strong>{evidence_count[key]}</strong>
          <span>{esc(EVIDENCE_LABELS.get(key, key.title()))}</span>
        </span>"""
        for key in EVIDENCE_ORDER
        if evidence_count[key]
    )
    status_class = "status-good" if active_statuses and failed == 0 else "status-warn"
    now = datetime.now(EASTERN)
    content = f"""
<section class="utility-intro">
  <p class="utility-kicker">A personal utility</p>
  <div class="daily-status">
    <h1><time data-current-date datetime="{now.date().isoformat()}">{now.strftime("%A, %B %-d, %Y")}</time></h1>
    <dl class="status-lines">
      <div class="status-row">
        <dt>Assembled</dt>
        <dd>{esc(freshness_text(updated_at))}</dd>
      </div>
      <div class="status-row">
        <dt>Collectors</dt>
        <dd class="collector-status">
          <span>{good} responding</span>
          <span class="{status_class}">{failed} failed</span>
        </dd>
      </div>
      <div class="status-row">
        <dt>Items</dt>
        <dd class="status-counts">{evidence_summary or '<span>None</span>'}</dd>
      </div>
      <div class="status-row">
        <dt>People matches</dt>
        <dd class="people-count">{people_items}</dd>
      </div>
    </dl>
  </div>
</section>
<section class="record-board" aria-label="Current Observatory items">
  {render_current_sections(shown)}
</section>"""
    return content


def render_archive_index(items: list[dict[str, Any]]) -> str:
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
  <p class="eyebrow">Thirty-day record</p>
  <h1>Archive</h1>
  <p class="subtitle">The latest 30 calendar days of collected items, grouped by publication date.</p>
  <ul class="archive-list">{listing}</ul>
</article>"""


def archive_window(
    items: list[dict[str, Any]], updated_at: str | None
) -> list[dict[str, Any]]:
    reference = parse_date(updated_at or utc_now()).astimezone(EASTERN).date()
    cutoff = reference - timedelta(days=ARCHIVE_DAYS - 1)
    return [item for item in items if item_local(item).date() >= cutoff]


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
    <category>{xml(EVIDENCE_LABELS.get(item['evidence'], item['evidence'].title()))}</category>
    <source url="{xml(item['url'])}">{xml(item.get('publisher'))}</source>
  </item>"""
        )
    feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
  <title>Observatory</title>
  <link>https://barasch.github.io/observatory/</link>
  <description>A personal utility for recurring public records and people matches.</description>
  <lastBuildDate>{parse_date(updated_at or utc_now()).strftime('%a, %d %b %Y %H:%M:%S +0000')}</lastBuildDate>
{chr(10).join(entries)}
</channel>
</rss>
"""
    (SITE / "feed.xml").write_text(feed, encoding="utf-8")


def build() -> int:
    item_data = load_json(ROOT / "data" / "items.json", {"items": [], "updated_at": None})
    status_data = load_json(ROOT / "data" / "status.json", {"sources": {}})
    items = item_data.get("items", [])
    statuses = status_data.get("sources", {})
    items.sort(key=item_sort_key, reverse=True)
    archived_items = archive_window(items, item_data.get("updated_at"))

    if SITE.exists():
        shutil.rmtree(SITE)
    (SITE / "assets").mkdir(parents=True)
    shutil.copy2(ROOT / "site_src" / "styles.css", SITE / "assets" / "styles.css")
    shutil.copy2(ROOT / "site_src" / "app.js", SITE / "assets" / "app.js")
    shutil.copy2(ROOT / "site_src" / "mark.svg", SITE / "assets" / "mark.svg")

    page(
        "index.html",
        "Observatory",
        "A personal utility for recurring public records and people matches.",
        render_home(items, statuses, item_data.get("updated_at")),
        "home",
    )
    page(
        "archive/index.html",
        "Archive",
        "The Observatory 30-day date archive.",
        render_archive_index(archived_items),
        "archive",
    )

    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in archived_items:
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
        "https://barasch.github.io/observatory/archive/",
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
    write_feed(archived_items, item_data.get("updated_at"))
    page(
        "404.html",
        "Not found",
        "The requested Observatory page was not found.",
        """
<article class="prose">
  <p class="eyebrow">Not found</p>
  <h1>This page is outside the record.</h1>
  <p><a href="./index.html">Return to Observatory</a>.</p>
</article>""",
        "",
    )
    return len(items)


def check_site() -> list[str]:
    errors = []
    required = [
        SITE / "index.html",
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
