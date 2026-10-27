#!/usr/bin/env python3
"""Collect compact public metadata for the media-attention shadow panel.

Stage 2 stops before story clustering.  This collector writes a normalized
pre-clustering batch plus a channel-health report; it never invents story ids,
publishes a network page, or retains article bodies and transcripts.
"""
from __future__ import annotations

import argparse
import email.utils
import hashlib
import html
import json
import re
import tempfile
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from media_validate import (
    load_document,
    validate_collection_batch,
    validate_collection_health,
    validate_observer_registry,
)


USER_AGENT = "Observatory-media-attention/0.2 (+https://github.com/barasch/observatory)"
TIMEOUT_SECONDS = 25
# Podcast feeds often retain long episode archives. The bound protects the
# collector while leaving room for those public XML indexes; bodies and
# descriptions are still discarded immediately after title/link extraction.
MAX_RESPONSE_BYTES = 32_000_000
MIRROR_WINDOW = timedelta(hours=36)
SUPPORTED_METHODS = {"feed", "bluesky-author"}
UTC = timezone.utc


class CollectionFailure(RuntimeError):
    def __init__(self, status: str, code: str, *, http_status: int | None = None):
        super().__init__(code)
        self.status = status
        self.code = code
        self.http_status = http_status


@dataclass(frozen=True)
class FetchResult:
    payload: bytes
    final_url: str
    http_status: int
    content_type: str


def iso_utc(value: datetime) -> str:
    value = value.astimezone(UTC).replace(microsecond=0)
    return value.isoformat().replace("+00:00", "Z")


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip()
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except ValueError:
        return None


def clean_text(value: Any, maximum: int = 300) -> str:
    value = html.unescape(str(value or ""))
    value = re.sub(r"<[^>]+>", " ", value)
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:maximum].rstrip()


def canonical_url(value: str, base_url: str = "") -> str:
    value = clean_text(value, 2048)
    if base_url:
        value = urllib.parse.urljoin(base_url, value)
    try:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            return ""
        query = [
            (key, item)
            for key, item in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            if not key.lower().startswith("utm_")
            and key.lower() not in {"fbclid", "gclid", "mc_cid", "mc_eid"}
        ]
        return urllib.parse.urlunsplit(
            (parsed.scheme, parsed.netloc.lower(), parsed.path or "/", urllib.parse.urlencode(query), "")
        )
    except ValueError:
        return ""


def stable_slug(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}-{digest}"


def default_fetch(url: str) -> FetchResult:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, application/json;q=0.9, */*;q=0.1",
            "Accept-Encoding": "identity",
        },
    )
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = response.read(MAX_RESPONSE_BYTES + 1)
            if len(payload) > MAX_RESPONSE_BYTES:
                raise CollectionFailure("parse-error", "response-too-large")
            final_url = response.geturl()
            if urllib.parse.urlsplit(final_url).scheme != "https":
                raise CollectionFailure("unsafe-redirect", "non-https-redirect")
            return FetchResult(
                payload=payload,
                final_url=final_url,
                http_status=int(response.status),
                content_type=response.headers.get("Content-Type", ""),
            )
    except HTTPError as error:
        raise CollectionFailure("http-error", f"http-{error.code}", http_status=error.code) from error
    except CollectionFailure:
        raise
    except (URLError, TimeoutError, OSError) as error:
        reason = getattr(error, "reason", None)
        code = "timeout" if isinstance(reason, TimeoutError) or isinstance(error, TimeoutError) else "network-failure"
        raise CollectionFailure("network-error", code) from error


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def child_text(node: ET.Element, names: tuple[str, ...]) -> str:
    wanted = set(names)
    for child in node.iter():
        if child is node or local_name(child.tag) not in wanted:
            continue
        if local_name(child.tag) == "link" and child.attrib.get("href"):
            return child.attrib["href"]
        value = "".join(child.itertext()).strip()
        if value:
            return value
    return ""


def child_link(node: ET.Element, base_url: str) -> str:
    fallback = ""
    for child in node.iter():
        if child is node or local_name(child.tag) != "link":
            continue
        href = child.attrib.get("href") or (child.text or "").strip()
        if not href:
            continue
        rel = child.attrib.get("rel", "alternate")
        if rel in {"alternate", ""}:
            return canonical_url(href, base_url)
        fallback = fallback or href
    return canonical_url(fallback, base_url)


def parse_feed(payload: bytes, base_url: str) -> list[dict[str, str]]:
    text = payload.decode("utf-8", "replace").lstrip("\ufeff")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as error:
        raise CollectionFailure("parse-error", "invalid-xml") from error
    if local_name(root.tag) not in {"rss", "feed", "rdf"}:
        raise CollectionFailure("parse-error", "unexpected-feed-root")
    entries: list[dict[str, str]] = []
    for node in root.iter():
        if local_name(node.tag) not in {"item", "entry"}:
            continue
        title = clean_text(child_text(node, ("title",)))
        link = child_link(node, base_url)
        external_id = clean_text(child_text(node, ("guid", "id")), 2048) or link
        if not title or not link:
            continue
        entries.append(
            {
                "title": title,
                "url": link,
                "external_id": external_id,
                "published": child_text(node, ("pubdate", "published", "updated", "date")),
            }
        )
    return entries


def parse_bluesky(payload: bytes, expected_actor: str) -> list[dict[str, str]]:
    try:
        data = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CollectionFailure("parse-error", "invalid-json") from error
    if not isinstance(data, dict) or not isinstance(data.get("feed"), list):
        raise CollectionFailure("parse-error", "invalid-bluesky-response")
    entries: list[dict[str, str]] = []
    for wrapper in data["feed"]:
        if not isinstance(wrapper, dict) or wrapper.get("reason") is not None:
            # A repost is an act of attention, but its timestamp and semantics
            # differ from an authored post. Stage 2 does not conflate them.
            continue
        post = wrapper.get("post", {}) if isinstance(wrapper, dict) else {}
        record = post.get("record", {}) if isinstance(post, dict) else {}
        author = post.get("author", {}) if isinstance(post, dict) else {}
        uri = post.get("uri", "")
        text = clean_text(record.get("text")) if isinstance(record, dict) else ""
        handle = clean_text(author.get("handle"), 240) if isinstance(author, dict) else ""
        did = clean_text(author.get("did"), 240) if isinstance(author, dict) else ""
        matches_actor = (
            did == expected_actor
            if expected_actor.startswith("did:")
            else handle.casefold() == expected_actor.casefold()
        )
        if not matches_actor or not text or not handle or not isinstance(uri, str) or "/app.bsky.feed.post/" not in uri:
            continue
        record_key = uri.rsplit("/", 1)[-1]
        entries.append(
            {
                "title": text,
                "url": f"https://bsky.app/profile/{handle}/post/{record_key}",
                "external_id": uri,
                "published": record.get("createdAt", ""),
            }
        )
    return entries


def load_state(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("first_seen"), dict):
        return {}
    return {
        str(key): str(value)
        for key, value in data["first_seen"].items()
        if isinstance(key, str) and isinstance(value, str) and parse_timestamp(value) is not None
    }


def write_json_atomic(path: Path, document: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(document, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def make_item(
    observer: dict[str, Any],
    channel: dict[str, Any],
    raw: dict[str, str],
    collected_at: datetime,
    lookback_started_at: datetime,
    first_seen: dict[str, str],
) -> tuple[dict[str, Any] | None, bool]:
    title = clean_text(raw.get("title"))
    url = canonical_url(raw.get("url", ""))
    external_id = clean_text(raw.get("external_id"), 2048) or url
    if not title or not url or not external_id:
        return None, False
    state_key = hashlib.sha256(
        f"{channel['id']}\x1f{external_id}\x1f{url}".encode("utf-8")
    ).hexdigest()
    published = parse_timestamp(raw.get("published"))
    if published is None:
        stored = parse_timestamp(first_seen.get(state_key))
        attention_at = stored or collected_at
        first_seen[state_key] = iso_utc(attention_at)
        basis = "first-seen"
    else:
        attention_at = published
        basis = "published"
    if attention_at < lookback_started_at or attention_at > collected_at + timedelta(days=1):
        return None, True
    item_id = stable_slug("item", channel["id"], external_id, url)
    return (
        {
            "id": item_id,
            "observer_id": observer["id"],
            "channel_id": channel["id"],
            "medium": channel["medium"],
            "normalized_title": title,
            "url": url,
            "external_id": external_id,
            "attention_at": iso_utc(attention_at),
            "timestamp_basis": basis,
            "collected_at": iso_utc(collected_at),
            "mirror_group_id": "pending-group",
        },
        True,
    )


def collect_channel(
    observer: dict[str, Any],
    channel: dict[str, Any],
    *,
    run_started_at: datetime,
    lookback_started_at: datetime,
    first_seen: dict[str, str],
    fetcher: Callable[[str], FetchResult],
    clock: Callable[[], datetime],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    attempted_at = clock().astimezone(UTC)
    row = {
        "channel_id": channel["id"],
        "observer_id": observer["id"],
        "medium": channel["medium"],
        "status": "network-error",
        "attempted_at": iso_utc(attempted_at),
        "completed_at": None,
        "http_status": None,
        "final_url": None,
        "item_count": 0,
        "qualifying_item_count": 0,
        "newest_attention_at": None,
        "error_code": "network-failure",
    }
    if channel["collection_method"] not in SUPPORTED_METHODS:
        completed_at = clock().astimezone(UTC)
        row.update(
            status="unsupported-method",
            completed_at=iso_utc(max(completed_at, attempted_at)),
            error_code="unsupported-method",
        )
        return [], row
    try:
        fetched = fetcher(channel["endpoint_url"])
        row["http_status"] = fetched.http_status
        row["final_url"] = canonical_url(fetched.final_url)
        if not row["final_url"]:
            raise CollectionFailure("unsafe-redirect", "unsafe-final-url")
        if channel["collection_method"] == "feed":
            raws = parse_feed(fetched.payload, fetched.final_url)
        else:
            endpoint = urllib.parse.urlsplit(channel["endpoint_url"])
            actors = urllib.parse.parse_qs(endpoint.query).get("actor", [])
            if (
                endpoint.hostname != "public.api.bsky.app"
                or endpoint.path != "/xrpc/app.bsky.feed.getAuthorFeed"
                or len(actors) != 1
                or not actors[0]
            ):
                raise CollectionFailure("parse-error", "invalid-bluesky-endpoint")
            raws = parse_bluesky(fetched.payload, actors[0])
        row["item_count"] = len(raws)
        collected_at = max(clock().astimezone(UTC), attempted_at, run_started_at)
        items: list[dict[str, Any]] = []
        for raw in raws:
            item, parseable = make_item(
                observer,
                channel,
                raw,
                collected_at,
                lookback_started_at,
                first_seen,
            )
            if item is not None:
                items.append(item)
            elif not parseable:
                continue
        # Feeds occasionally repeat a GUID. Keep the newest copy deterministically.
        unique: dict[tuple[str, str], dict[str, Any]] = {}
        for item in items:
            key = (item["channel_id"], item["external_id"])
            prior = unique.get(key)
            if prior is None or (item["attention_at"], item["id"]) > (prior["attention_at"], prior["id"]):
                unique[key] = item
        items = sorted(unique.values(), key=lambda item: (item["attention_at"], item["id"]))
        row["qualifying_item_count"] = len(items)
        row["newest_attention_at"] = max((item["attention_at"] for item in items), default=None)
        row["status"] = "ok" if items else "empty"
        row["error_code"] = None
        row["completed_at"] = iso_utc(collected_at)
        return items, row
    except CollectionFailure as error:
        completed_at = max(clock().astimezone(UTC), attempted_at, run_started_at)
        row.update(
            status=error.status,
            completed_at=iso_utc(completed_at),
            http_status=error.http_status,
            error_code=error.code,
            qualifying_item_count=0,
            newest_attention_at=None,
        )
        return [], row
    except (ValueError, TypeError, KeyError):
        completed_at = max(clock().astimezone(UTC), attempted_at, run_started_at)
        row.update(
            status="parse-error",
            completed_at=iso_utc(completed_at),
            error_code="invalid-item-metadata",
            qualifying_item_count=0,
            newest_attention_at=None,
        )
        return [], row


class DisjointSet:
    def __init__(self, size: int):
        self.parent = list(range(size))

    def find(self, value: int) -> int:
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, left: int, right: int) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def assign_mirror_groups(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_observer: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        by_observer.setdefault(item["observer_id"], []).append(item)
    groups: list[dict[str, Any]] = []
    for observer_id, observer_items in sorted(by_observer.items()):
        observer_items.sort(key=lambda item: (item["attention_at"], item["id"]))
        disjoint = DisjointSet(len(observer_items))
        parsed_times = [parse_timestamp(item["attention_at"]) for item in observer_items]

        # URL equality is the strongest identity signal. Build those groups
        # first so a later title match cannot create a transitive group whose
        # members share neither one URL nor one exact title.
        for left in range(len(observer_items)):
            for right in range(left + 1, len(observer_items)):
                left_item, right_item = observer_items[left], observer_items[right]
                if left_item["url"] == right_item["url"]:
                    disjoint.union(left, right)

        url_partitions: dict[int, list[int]] = {}
        for index in range(len(observer_items)):
            url_partitions.setdefault(disjoint.find(index), []).append(index)
        title_safe_partitions = [
            indexes
            for indexes in url_partitions.values()
            if len({observer_items[index]["normalized_title"].casefold() for index in indexes}) == 1
        ]
        for left_position, left_indexes in enumerate(title_safe_partitions):
            left_title = observer_items[left_indexes[0]]["normalized_title"].casefold()
            for right_indexes in title_safe_partitions[left_position + 1:]:
                if observer_items[right_indexes[0]]["normalized_title"].casefold() != left_title:
                    continue
                pairs = (
                    (left, right)
                    for left in left_indexes
                    for right in right_indexes
                    if observer_items[left]["channel_id"] != observer_items[right]["channel_id"]
                )
                if any(
                    abs(parsed_times[left] - parsed_times[right]) <= MIRROR_WINDOW  # type: ignore[operator]
                    for left, right in pairs
                ):
                    disjoint.union(left_indexes[0], right_indexes[0])
        partitions: dict[int, list[dict[str, Any]]] = {}
        for index, item in enumerate(observer_items):
            partitions.setdefault(disjoint.find(index), []).append(item)
        for partition in partitions.values():
            partition.sort(
                key=lambda item: (
                    item["attention_at"],
                    0 if item["timestamp_basis"] == "published" else 1,
                    item["id"],
                )
            )
            canonical = partition[0]
            group_id = stable_slug("mirror", observer_id, canonical["id"])
            for item in partition:
                item["mirror_group_id"] = group_id
            if len(partition) == 1:
                basis = "singleton"
            elif len({item["url"] for item in partition}) == 1:
                basis = "canonical-url"
            else:
                basis = "exact-title"
            groups.append(
                {
                    "id": group_id,
                    "observer_id": observer_id,
                    "canonical_item_id": canonical["id"],
                    "item_ids": [item["id"] for item in partition],
                    "match_basis": basis,
                }
            )
    return sorted(groups, key=lambda group: group["id"])


def collect_registry(
    registry: dict[str, Any],
    *,
    lookback_days: int = 35,
    workers: int = 12,
    first_seen: dict[str, str] | None = None,
    fetcher: Callable[[str], FetchResult] = default_fetch,
    clock: Callable[[], datetime] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    validate_observer_registry(registry)
    if lookback_days < 1 or lookback_days > 120:
        raise ValueError("lookback_days must be between 1 and 120")
    if workers < 1 or workers > 32:
        raise ValueError("workers must be between 1 and 32")
    clock = clock or (lambda: datetime.now(UTC))
    first_seen = dict(first_seen or {})
    started_at = clock().astimezone(UTC).replace(microsecond=0)
    lookback_started_at = started_at - timedelta(days=lookback_days)
    run_id = f"collect-{started_at.strftime('%Y%m%d-%H%M%S')}"

    enabled: list[tuple[dict[str, Any], dict[str, Any]]] = []
    health_rows: list[dict[str, Any]] = []
    for observer in registry["observers"]:
        for channel in observer["channels"]:
            if channel["enabled"]:
                enabled.append((observer, channel))
            else:
                health_rows.append(
                    {
                        "channel_id": channel["id"],
                        "observer_id": observer["id"],
                        "medium": channel["medium"],
                        "status": "disabled",
                        "attempted_at": None,
                        "completed_at": None,
                        "http_status": None,
                        "final_url": None,
                        "item_count": 0,
                        "qualifying_item_count": 0,
                        "newest_attention_at": None,
                        "error_code": None,
                    }
                )

    items: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=min(workers, max(1, len(enabled)))) as pool:
        futures = {
            pool.submit(
                collect_channel,
                observer,
                channel,
                run_started_at=started_at,
                lookback_started_at=lookback_started_at,
                first_seen=first_seen,
                fetcher=fetcher,
                clock=clock,
            ): channel["id"]
            for observer, channel in enabled
        }
        for future in as_completed(futures):
            channel_items, row = future.result()
            items.extend(channel_items)
            health_rows.append(row)

    items.sort(key=lambda item: (item["attention_at"], item["observer_id"], item["id"]))
    mirror_groups = assign_mirror_groups(items)
    completed_at = max(clock().astimezone(UTC).replace(microsecond=0), started_at)
    # Collection timestamps are created inside worker calls and can contain
    # sub-second values; normalize the run boundary without truncating below them.
    item_completed = [parse_timestamp(item["collected_at"]) for item in items]
    row_completed = [
        parse_timestamp(row["completed_at"])
        for row in health_rows
        if row["completed_at"] is not None
    ]
    completed_at = max([completed_at] + [value for value in item_completed + row_completed if value is not None])
    completed_text = iso_utc(completed_at)
    started_text = iso_utc(started_at)

    batch = {
        "document_type": "media-collection-batch",
        "schema_version": 1,
        "run_id": run_id,
        "registry_updated_at": registry["updated_at"],
        "started_at": started_text,
        "completed_at": completed_text,
        "timezone": "America/New_York",
        "lookback_started_at": iso_utc(lookback_started_at),
        "items": items,
        "mirror_groups": mirror_groups,
    }

    health_rows.sort(key=lambda row: row["channel_id"])
    successful_statuses = {"ok", "empty"}
    successful_observers = {
        row["observer_id"] for row in health_rows if row["status"] in successful_statuses
    }
    attempted_observers = {
        row["observer_id"] for row in health_rows if row["status"] != "disabled"
    }
    attempted_media = sorted(
        {row["medium"] for row in health_rows if row["status"] != "disabled"}
    )
    health = {
        "document_type": "media-collection-health",
        "schema_version": 1,
        "run_id": run_id,
        "registry_updated_at": registry["updated_at"],
        "started_at": started_text,
        "completed_at": completed_text,
        "summary": {
            "configured_channel_count": len(health_rows),
            "enabled_channel_count": sum(row["status"] != "disabled" for row in health_rows),
            "attempted_channel_count": sum(row["status"] != "disabled" for row in health_rows),
            "succeeded_channel_count": sum(row["status"] in successful_statuses for row in health_rows),
            "failed_channel_count": sum(
                row["status"] not in successful_statuses | {"disabled"} for row in health_rows
            ),
            "disabled_channel_count": sum(row["status"] == "disabled" for row in health_rows),
            "retained_item_count": len(items),
            "mirror_group_count": len(mirror_groups),
            "successful_observer_ids": sorted(successful_observers),
            "failed_observer_ids": sorted(attempted_observers - successful_observers),
            "attempted_media": attempted_media,
        },
        "channels": health_rows,
    }
    validate_collection_batch(batch, registry)
    validate_collection_health(health, registry, batch)
    return batch, health, first_seen


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect private shadow-panel metadata without clustering or publication."
    )
    parser.add_argument("--registry", type=Path, default=Path("config/media-observers.json"))
    parser.add_argument("--output-dir", type=Path, default=Path(".work/media-networks"))
    parser.add_argument("--lookback-days", type=int, default=35)
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args()

    registry = load_document(args.registry)
    state_path = args.output_dir / "state.json"
    batch, health, first_seen = collect_registry(
        registry,
        lookback_days=args.lookback_days,
        workers=args.workers,
        first_seen=load_state(state_path),
    )
    run_dir = args.output_dir / batch["run_id"]
    write_json_atomic(run_dir / "collection.json", batch)
    write_json_atomic(run_dir / "health.json", health)
    write_json_atomic(
        state_path,
        {"schema_version": 1, "updated_at": batch["completed_at"], "first_seen": first_seen},
    )
    print(
        json.dumps(
            {
                "run_id": batch["run_id"],
                "collection": str(run_dir / "collection.json"),
                "health": str(run_dir / "health.json"),
                **health["summary"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
