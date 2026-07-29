#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus, urljoin
from urllib.request import Request, urlopen

from common import (
    ROOT,
    canonical_url,
    clean_text,
    fingerprint,
    iso_utc,
    load_json,
    load_people,
    normalized,
    parse_date,
    utc_now,
    validate_people,
    write_json,
)


USER_AGENT = "Observatory/0.1 (+https://github.com/barasch/observatory)"
TIMEOUT_SECONDS = 30
RETENTION_DAYS = 30
MONTH_DATE_RE = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+\d{1,2},\s+\d{4}\b"
)
DATE_ONLY_RE = re.compile(
    r"(?:\d{4}-\d{2}-\d{2}|"
    r"(?:January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+\d{1,2},\s+\d{4}|"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+\d{4}|"
    r"\d{1,2}/\d{1,2}/\d{2})"
)


def http_get(url: str, accept: str = "*/*") -> tuple[bytes, str]:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": accept,
            "Accept-Encoding": "identity",
        },
    )
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                return response.read(), response.geturl()
        except (HTTPError, URLError, TimeoutError, OSError) as error:
            last_error = error
            if attempt == 0 and not isinstance(error, HTTPError):
                time.sleep(1)
                continue
            break
    raise RuntimeError(str(last_error) if last_error else "Unknown request failure")


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def child_text(node: ET.Element, names: tuple[str, ...]) -> str:
    wanted = set(names)
    for child in node.iter():
        if local_name(child.tag) in wanted:
            if local_name(child.tag) == "link" and child.attrib.get("href"):
                return child.attrib["href"]
            value = "".join(child.itertext()).strip()
            if value:
                return value
    return ""


def feed_entries(payload: bytes, base_url: str) -> list[dict[str, str]]:
    root = ET.fromstring(payload.lstrip(b"\xef\xbb\xbf"))
    entries: list[dict[str, str]] = []
    for node in root.iter():
        kind = local_name(node.tag)
        if kind not in {"item", "entry"}:
            continue
        link = ""
        for child in list(node):
            if local_name(child.tag) == "link":
                rel = child.attrib.get("rel", "alternate")
                href = child.attrib.get("href")
                if href and rel in {"alternate", ""}:
                    link = href
                    break
                if child.text and not link:
                    link = child.text
        if not link:
            link = child_text(node, ("link",))
        title = child_text(node, ("title",))
        if not title or not link:
            continue
        entries.append(
            {
                "title": title,
                "url": urljoin(base_url, clean_text(link)),
                "external_id": child_text(node, ("guid", "id")) or link,
                "summary": child_text(
                    node, ("description", "summary", "encoded", "content")
                ),
                "published": child_text(
                    node, ("pubdate", "published", "updated", "date")
                ),
                "author": child_text(node, ("author", "creator")),
                "feed_source": child_text(node, ("source",)),
            }
        )
    return entries


def make_item(
    source: dict[str, Any],
    raw: dict[str, Any],
    fetched_at: datetime,
    **overrides: Any,
) -> dict[str, Any]:
    title = clean_text(raw.get("title"), 240)
    url = canonical_url(raw.get("url", ""))
    published_value = clean_text(raw.get("published"))
    if not published_value and source.get("date_from_summary"):
        date_match = MONTH_DATE_RE.search(clean_text(raw.get("summary"), 600))
        if date_match:
            published_value = date_match.group(0)
    published = parse_date(published_value, fetched_at)
    published_precision = (
        "date" if DATE_ONLY_RE.fullmatch(published_value) else "datetime"
    )
    external_id = clean_text(raw.get("external_id")) or url
    item = {
        "id": fingerprint(source["id"], external_id, url, title),
        "source_id": source["id"],
        "source_name": source["name"],
        "publisher": source.get("publisher", source["name"]),
        "title": title,
        "url": url,
        "summary": clean_text(raw.get("summary"), 420),
        "author": clean_text(raw.get("author"), 120),
        "published": iso_utc(published),
        "published_precision": published_precision,
        "first_seen": iso_utc(fetched_at),
        "last_seen": iso_utc(fetched_at),
        "evidence": source["evidence"],
        "channel": source["channel"],
        "geography": source["geography"],
        "person_id": None,
        "person_name": None,
        "match_basis": None,
    }
    item.update(overrides)
    return item


def collect_feed(source: dict[str, Any], fetched_at: datetime) -> list[dict[str, Any]]:
    payload, final_url = http_get(
        source["endpoint"], "application/rss+xml, application/atom+xml, application/xml, text/xml"
    )
    raws = feed_entries(payload, final_url)
    return [make_item(source, raw, fetched_at) for raw in raws]


def collect_federal_register(
    source: dict[str, Any], fetched_at: datetime
) -> list[dict[str, Any]]:
    payload, _ = http_get(source["endpoint"], "application/json")
    data = json.loads(payload)
    items: list[dict[str, Any]] = []
    for result in data.get("results", []):
        agencies = ", ".join(
            clean_text(agency.get("name"))
            for agency in result.get("agencies", [])
            if clean_text(agency.get("name"))
        )
        document_type = clean_text(result.get("type"))
        summary = clean_text(result.get("abstract"), 360)
        if agencies:
            summary = f"{agencies}. {summary}".strip()
        items.append(
            make_item(
                source,
                {
                    "title": result.get("title"),
                    "url": result.get("html_url") or result.get("pdf_url"),
                    "external_id": result.get("document_number"),
                    "summary": summary,
                    "published": result.get("publication_date"),
                    "author": document_type,
                },
                fetched_at,
            )
        )
    return items


def collect_mmwr(source: dict[str, Any], fetched_at: datetime) -> list[dict[str, Any]]:
    payload, final_url = http_get(source["endpoint"], "text/html")
    page = payload.decode("utf-8", "replace")
    pattern = re.compile(
        r'<a\b[^>]*href=["\'](?P<href>[^"\']*/mmwr/volumes/[^"\']+\.htm[^"\']*)["\'][^>]*>(?P<title>.*?)</a>',
        re.I | re.S,
    )
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in pattern.finditer(page):
        url = canonical_url(urljoin(final_url, match.group("href")))
        if url in seen:
            continue
        seen.add(url)
        preceding = clean_text(page[max(0, match.start() - 900) : match.start()])
        dates = re.findall(
            r"(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+20\d{2}",
            preceding,
        )
        title = clean_text(match.group("title"))
        if not title:
            continue
        items.append(
            make_item(
                source,
                {
                    "title": title,
                    "url": url,
                    "external_id": url,
                    "summary": "",
                    "published": dates[-1] if dates else None,
                },
                fetched_at,
            )
        )
    return items


def collect_scotus(source: dict[str, Any], fetched_at: datetime) -> list[dict[str, Any]]:
    payload, final_url = http_get(source["endpoint"], "text/html")
    page = payload.decode("utf-8", "replace")
    items: list[dict[str, Any]] = []
    for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", page, flags=re.I | re.S):
        anchor = re.search(
            r"<a\b(?P<attrs>[^>]*)>(?P<title>.*?)</a>",
            row,
            flags=re.I | re.S,
        )
        if not anchor:
            continue
        href = re.search(
            r'href=["\'](?P<value>[^"\']*/opinions/[^"\']+\.pdf)["\']',
            anchor.group("attrs"),
            flags=re.I,
        )
        if not href:
            continue
        summary = re.search(
            r'title=["\'](?P<value>[^"\']*)["\']',
            anchor.group("attrs"),
            flags=re.I,
        )
        cells = [clean_text(cell) for cell in re.findall(r"<td\b[^>]*>(.*?)</td>", row, re.I | re.S)]
        date_value = cells[1] if len(cells) > 1 else ""
        docket = cells[2] if len(cells) > 2 else ""
        items.append(
            make_item(
                source,
                {
                    "title": clean_text(anchor.group("title")),
                    "url": urljoin(final_url, href.group("value")),
                    "external_id": docket or href.group("value"),
                    "summary": clean_text(summary.group("value")) if summary else "",
                    "published": date_value,
                    "author": f"Docket {docket}" if docket else "",
                },
                fetched_at,
            )
        )
    return items


ADAPTERS = {
    "feed": collect_feed,
    "federal_register": collect_federal_register,
    "mmwr": collect_mmwr,
    "scotus": collect_scotus,
}


def person_matches(person: dict[str, Any], raw: dict[str, Any]) -> bool:
    haystack = normalized(
        " ".join(
            [
                clean_text(raw.get("title")),
                clean_text(raw.get("summary")),
                clean_text(raw.get("feed_source")),
            ]
        )
    )
    names = [person["display_name"], *person.get("aliases", [])]
    if not any(normalized(name) in haystack for name in names if normalized(name)):
        return False
    if any(normalized(term) in haystack for term in person.get("exclude_any", [])):
        return False
    required = [normalized(term) for term in person.get("require_any", []) if normalized(term)]
    if required and not person.get("distinctive_name", False):
        return any(term in haystack for term in required)
    return True


def google_news_url(person: dict[str, Any]) -> str:
    query = clean_text(person.get("search_query"))
    if not query:
        name = person["display_name"].replace('"', "")
        query = f'"{name}"'
        terms = [clean_text(term) for term in person.get("require_any", []) if clean_text(term)]
        if terms:
            query += " (" + " OR ".join(f'"{term}"' if " " in term else term for term in terms) + ")"
    return (
        "https://news.google.com/rss/search?q="
        + quote_plus(query)
        + "&hl=en-US&gl=US&ceid=US:en"
    )


def collect_person_news(
    person: dict[str, Any], fetched_at: datetime
) -> list[dict[str, Any]]:
    source = {
        "id": f"person-{person['id']}",
        "name": f"People watch · {person['display_name']}",
        "publisher": "Matched public source",
        "evidence": "REPORTED",
        "channel": "people",
        "geography": "Public web",
    }
    payload, final_url = http_get(
        google_news_url(person),
        "application/rss+xml, application/xml, text/xml",
    )
    results: list[dict[str, Any]] = []
    for raw in feed_entries(payload, final_url):
        if not person_matches(person, raw):
            continue
        publisher = clean_text(raw.get("feed_source")) or "Google News index"
        results.append(
            make_item(
                source,
                raw,
                fetched_at,
                publisher=publisher,
                person_id=person["id"],
                person_name=person["display_name"],
                match_basis="NAME + CONTEXT" if person.get("require_any") else "EXACT NAME",
            )
        )
    return results


def collect_person_feeds(
    person: dict[str, Any], fetched_at: datetime
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for feed in person.get("official_feeds", []):
        source = {
            "id": f"person-{person['id']}-direct",
            "name": f"People watch · {person['display_name']}",
            "publisher": clean_text(feed.get("name")) or person["display_name"],
            "endpoint": feed["url"],
            "evidence": "REPORTED",
            "channel": "people",
            "geography": "Direct source",
        }
        for item in collect_feed(source, fetched_at):
            item.update(
                {
                    "person_id": person["id"],
                    "person_name": person["display_name"],
                    "match_basis": "DIRECT SOURCE",
                }
            )
            results.append(item)
    return results


def collect_openalex(
    person: dict[str, Any], fetched_at: datetime
) -> list[dict[str, Any]]:
    openalex_id = clean_text(person.get("openalex_id"))
    if not openalex_id:
        return []
    since = (fetched_at - timedelta(days=60)).date().isoformat()
    endpoint = (
        "https://api.openalex.org/works?filter="
        + quote_plus(f"author.id:{openalex_id},from_publication_date:{since}")
        + "&sort=publication_date:desc&per-page=25"
    )
    payload, _ = http_get(endpoint, "application/json")
    data = json.loads(payload)
    source = {
        "id": f"person-{person['id']}-openalex",
        "name": f"People watch · {person['display_name']}",
        "publisher": "OpenAlex",
        "evidence": "FILED",
        "channel": "people",
        "geography": "Scholarly record",
    }
    results: list[dict[str, Any]] = []
    for work in data.get("results", []):
        location = work.get("primary_location") or {}
        url = work.get("doi") or location.get("landing_page_url") or work.get("id")
        results.append(
            make_item(
                source,
                {
                    "title": work.get("display_name"),
                    "url": url,
                    "external_id": work.get("id"),
                    "summary": f"Scholarly work · {clean_text(work.get('type')).replace('-', ' ')}",
                    "published": work.get("publication_date"),
                },
                fetched_at,
                person_id=person["id"],
                person_name=person["display_name"],
                match_basis="OPENALEX AUTHOR ID",
            )
        )
    return results


def bounded(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    items.sort(key=lambda item: item["published"], reverse=True)
    return items[: max(0, limit)]


def merge_items(
    existing: list[dict[str, Any]],
    incoming: list[dict[str, Any]],
    fetched_at: datetime,
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {item["id"]: item for item in existing}
    for item in incoming:
        previous = merged.get(item["id"])
        if previous:
            item["first_seen"] = previous.get("first_seen") or item["first_seen"]
        merged[item["id"]] = item
    cutoff = fetched_at - timedelta(days=RETENTION_DAYS)
    retained = [
        item
        for item in merged.values()
        if parse_date(item.get("published"), fetched_at) >= cutoff
    ]
    retained.sort(key=lambda item: (item["published"], item["id"]), reverse=True)
    return retained


def run(selected: set[str] | None = None) -> tuple[int, int]:
    fetched_at = utc_now()
    source_data = load_json(ROOT / "config" / "sources.json", {})
    sources = source_data.get("sources", [])
    previous_data = load_json(
        ROOT / "data" / "items.json",
        {"schema_version": 1, "updated_at": None, "items": []},
    )
    previous_status = load_json(
        ROOT / "data" / "status.json",
        {"schema_version": 1, "updated_at": None, "sources": {}},
    )
    statuses = dict(previous_status.get("sources", {}))
    incoming: list[dict[str, Any]] = []
    active = [
        source
        for source in sources
        if source.get("active")
        and source.get("adapter") in ADAPTERS
        and (not selected or source["id"] in selected)
    ]
    def collect_source(source: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], datetime]:
        started = utc_now()
        items = ADAPTERS[source["adapter"]](source, fetched_at)
        return source, bounded(items, int(source.get("daily_limit", 20))), started

    with ThreadPoolExecutor(max_workers=min(8, max(1, len(active)))) as executor:
        futures = {executor.submit(collect_source, source): source for source in active}
        for future in as_completed(futures):
            source = futures[future]
            try:
                _, items, started = future.result()
                incoming.extend(items)
                statuses[source["id"]] = {
                    "ok": True,
                    "checked_at": iso_utc(started),
                    "item_count": len(items),
                    "error": None,
                }
                print(f"ok   {source['id']}: {len(items)} items", flush=True)
            except Exception as error:
                statuses[source["id"]] = {
                    "ok": False,
                    "checked_at": iso_utc(fetched_at),
                    "item_count": 0,
                    "error": clean_text(f"{type(error).__name__}: {error}", 180),
                }
                print(f"fail {source['id']}: {error}", file=sys.stderr, flush=True)

    registry = load_people()
    errors = validate_people(registry)
    if errors:
        raise SystemExit("Invalid people watchlist:\n- " + "\n- ".join(errors))

    def collect_person(person: dict[str, Any]) -> list[dict[str, Any]]:
        person_items = collect_person_feeds(person, fetched_at)
        person_items.extend(collect_openalex(person, fetched_at))
        if person.get("google_news", True):
            person_items.extend(collect_person_news(person, fetched_at))
        return bounded(person_items, 12)

    enabled_people = [
        person for person in registry.get("people", []) if person.get("enabled", True)
    ]
    people_successes = 0
    people_failures = 0
    if enabled_people:
        with ThreadPoolExecutor(max_workers=min(8, len(enabled_people))) as executor:
            futures = {
                executor.submit(collect_person, person): person for person in enabled_people
            }
            for future in as_completed(futures):
                try:
                    incoming.extend(future.result())
                    people_successes += 1
                except Exception as error:
                    people_failures += 1
                    print(
                        f"fail private people-watch entry: {type(error).__name__}",
                        file=sys.stderr,
                        flush=True,
                    )
    statuses["people-watch"] = {
        "ok": people_failures == 0,
        "checked_at": iso_utc(fetched_at),
        "item_count": sum(1 for item in incoming if item.get("person_id")),
        "error": (
            None
            if people_failures == 0
            else f"{people_failures} private watch entries failed; identities and queries are not published."
        ),
        "configured_entries": None,
        "successful_entries": None,
    }

    merged = merge_items(previous_data.get("items", []), incoming, fetched_at)
    write_json(
        ROOT / "data" / "items.json",
        {
            "schema_version": 1,
            "updated_at": iso_utc(fetched_at),
            "items": merged,
        },
    )
    write_json(
        ROOT / "data" / "status.json",
        {
            "schema_version": 1,
            "updated_at": iso_utc(fetched_at),
            "sources": statuses,
        },
    )
    return len(incoming), len(merged)


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect public Observatory items.")
    parser.add_argument(
        "--source",
        action="append",
        default=[],
        help="Collect only a named active source; may be repeated.",
    )
    args = parser.parse_args()
    incoming, total = run(set(args.source) or None)
    print(f"Stored {total} items ({incoming} observed this run).")


if __name__ == "__main__":
    main()
