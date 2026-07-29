from __future__ import annotations

import hashlib
import html
import json
import os
import re
import tempfile
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parents[1]
TRACKING_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "s_cid",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False
    ) as handle:
        handle.write(payload)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def clean_text(value: Any, limit: int | None = None) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = re.sub(r"<script\b[^>]*>.*?</script>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<style\b[^>]*>.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if limit and len(text) > limit:
        clipped = text[: max(0, limit - 1)].rsplit(" ", 1)[0].rstrip(" ,;:")
        return (clipped or text[: max(0, limit - 1)]) + "…"
    return text


def parse_date(value: Any, fallback: datetime | None = None) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    else:
        raw = clean_text(value)
        parsed = None
        if raw:
            try:
                parsed = parsedate_to_datetime(raw)
            except (TypeError, ValueError, OverflowError):
                pass
            if parsed is None:
                normalized = raw.replace("Z", "+00:00")
                try:
                    parsed = datetime.fromisoformat(normalized)
                except ValueError:
                    pass
            if parsed is None:
                for pattern in ("%B %d, %Y", "%b %d, %Y", "%m/%d/%y", "%Y-%m-%d"):
                    try:
                        parsed = datetime.strptime(raw, pattern)
                        break
                    except ValueError:
                        continue
        if parsed is None:
            parsed = fallback or utc_now()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def canonical_url(value: str) -> str:
    raw = clean_text(value)
    if not raw:
        return ""
    parts = urlsplit(raw)
    query = []
    for key, val in parse_qsl(parts.query, keep_blank_values=True):
        lower = key.lower()
        if lower.startswith("utm_") or lower in TRACKING_KEYS:
            continue
        query.append((key, val))
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            path,
            urlencode(query, doseq=True),
            "",
        )
    )


def fingerprint(source_id: str, external_id: str, url: str, title: str) -> str:
    basis = "\x1f".join(
        [clean_text(source_id), clean_text(external_id), canonical_url(url), clean_text(title)]
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:20]


def normalized(value: str) -> str:
    value = html.unescape(value).casefold()
    value = re.sub(r"[^\w\s]", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def load_people() -> dict[str, Any]:
    secret = os.environ.get("PEOPLE_WATCHLIST_JSON", "").strip()
    if secret:
        return json.loads(secret)
    private_path = ROOT / ".private" / "people.json"
    if private_path.exists():
        return load_json(private_path, {"schema_version": 1, "people": []})
    return {"schema_version": 1, "people": []}


def validate_people(registry: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(registry, dict):
        return ["The watchlist must be a JSON object."]
    if registry.get("schema_version") != 1:
        errors.append("schema_version must be 1.")
    people = registry.get("people")
    if not isinstance(people, list):
        return errors + ["people must be an array."]
    seen: set[str] = set()
    for index, person in enumerate(people):
        prefix = f"people[{index}]"
        if not isinstance(person, dict):
            errors.append(f"{prefix} must be an object.")
            continue
        person_id = person.get("id")
        if not isinstance(person_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", person_id):
            errors.append(f"{prefix}.id must be a lowercase slug.")
        elif person_id in seen:
            errors.append(f"{prefix}.id duplicates {person_id!r}.")
        else:
            seen.add(person_id)
        if not clean_text(person.get("display_name")):
            errors.append(f"{prefix}.display_name is required.")
        for field in ("aliases", "require_any", "exclude_any", "official_feeds"):
            if field in person and not isinstance(person[field], list):
                errors.append(f"{prefix}.{field} must be an array.")
        for feed_index, feed in enumerate(person.get("official_feeds", [])):
            if not isinstance(feed, dict) or not clean_text(feed.get("url")):
                errors.append(f"{prefix}.official_feeds[{feed_index}] requires a url.")
    return errors

