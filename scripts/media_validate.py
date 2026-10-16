#!/usr/bin/env python3
"""Strict contracts for the Observatory media-attention data product.

This module deliberately contains no collection, clustering, inference, scoring,
or rendering logic.  It is the boundary between those later stages and public
data.  Unknown fields are rejected so private notes, credentials, and accidental
collector payloads cannot drift into published files.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo


SCHEMA_VERSION = 1
EASTERN = ZoneInfo("America/New_York")
TOLERANCE = 1e-6

DOCUMENT_TYPES = {
    "media-observer-registry",
    "media-observation-manifest",
    "media-weekly-model",
    "media-current-snapshot",
    "media-model-order-diagnostics",
    "media-lineage-scenarios",
}
MEDIA = {
    "website",
    "newsletter",
    "podcast",
    "youtube",
    "bluesky",
    "x",
    "tiktok",
    "instagram",
}
PANEL_STATUSES = {"candidate", "shadow", "active", "retired"}
TIMESTAMP_BASES = {"published", "first-seen"}
LINEAGE_RELATIONSHIPS = {
    "birth",
    "continuation",
    "split",
    "merger",
    "uncertain",
}
LINEAGE_EVENT_TYPES = LINEAGE_RELATIONSHIPS | {"retirement"}

SLUG = re.compile(r"[a-z0-9][a-z0-9-]{2,119}")
LINEAGE_ID = re.compile(r"lineage-[0-9]{4,}")
NETWORK_LABEL = re.compile(r"Network [A-Z]+")
CODE_VERSION = re.compile(r"[0-9a-f]{7,40}")
ISO_WEEK = re.compile(r"(?P<year>[0-9]{4})-W(?P<week>[0-9]{2})")


class MediaValidationError(ValueError):
    """A public media-network document violates its declared contract."""


def _fail(path: str, message: str) -> None:
    raise MediaValidationError(f"{path}: {message}")


def _object(
    value: Any,
    required: set[str],
    optional: set[str] = frozenset(),
    path: str = "document",
) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(path, "must be an object")
    keys = set(value)
    missing = required - keys
    unknown = keys - required - set(optional)
    if missing:
        _fail(path, f"missing fields: {', '.join(sorted(missing))}")
    if unknown:
        _fail(path, f"unknown fields: {', '.join(sorted(unknown))}")
    return value


def _array(value: Any, path: str, minimum: int = 0, maximum: int | None = None) -> list[Any]:
    if not isinstance(value, list):
        _fail(path, "must be an array")
    if len(value) < minimum:
        _fail(path, f"must contain at least {minimum} entries")
    if maximum is not None and len(value) > maximum:
        _fail(path, f"must contain at most {maximum} entries")
    return value


def _text(value: Any, path: str, maximum: int = 500, minimum: int = 1) -> str:
    if not isinstance(value, str) or len(value.strip()) < minimum:
        _fail(path, "must be nonempty text")
    if value != value.strip():
        _fail(path, "must not have leading or trailing whitespace")
    if len(value) > maximum:
        _fail(path, f"must not exceed {maximum} characters")
    return value


def _slug(value: Any, path: str) -> str:
    value = _text(value, path, maximum=120)
    if not SLUG.fullmatch(value):
        _fail(path, "must be a lowercase slug")
    return value


def _lineage_id(value: Any, path: str) -> str:
    value = _text(value, path, maximum=80)
    if not LINEAGE_ID.fullmatch(value):
        _fail(path, "must have the form lineage-0001")
    return value


def _version_id(value: Any, week: str, path: str) -> str:
    value = _text(value, path, maximum=100)
    lineage, separator, version_week = value.partition("@")
    _lineage_id(lineage, path)
    if not separator or version_week != week:
        _fail(path, f"must end with @{week}")
    return value


def _network_label(value: Any, path: str) -> str:
    value = _text(value, path, maximum=40)
    if not NETWORK_LABEL.fullmatch(value):
        _fail(path, "must have the form Network A (letters are semantically empty)")
    return value


def _number(
    value: Any,
    path: str,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        _fail(path, "must be a finite number")
    result = float(value)
    if minimum is not None and result < minimum:
        _fail(path, f"must be at least {minimum}")
    if maximum is not None and result > maximum:
        _fail(path, f"must be at most {maximum}")
    return result


def _integer(value: Any, path: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(path, "must be an integer")
    if value < minimum:
        _fail(path, f"must be at least {minimum}")
    return value


def _date(value: Any, path: str) -> date:
    value = _text(value, path, maximum=10)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        _fail(path, "must be an ISO date (YYYY-MM-DD)")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        _fail(path, "must be an ISO date (YYYY-MM-DD)")
    return parsed


def _timestamp(value: Any, path: str) -> datetime:
    value = _text(value, path, maximum=40)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(Z|[+-]\d{2}:\d{2})", value):
        _fail(path, "must include seconds and an explicit UTC offset")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(path, "must be an ISO 8601 timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail(path, "must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _week(value: Any, path: str) -> str:
    value = _text(value, path, maximum=8)
    match = ISO_WEEK.fullmatch(value)
    if not match:
        _fail(path, "must be an ISO week (YYYY-Www)")
    try:
        date.fromisocalendar(int(match.group("year")), int(match.group("week")), 1)
    except ValueError:
        _fail(path, "is not a real ISO week")
    return value


def _url(value: Any, path: str) -> str:
    value = _text(value, path, maximum=2048)
    if any(character.isspace() or ord(character) < 32 for character in value) or "\\" in value:
        _fail(path, "must not contain whitespace, control characters, or backslashes")
    try:
        parsed = urlparse(value)
        parsed.port
    except ValueError:
        _fail(path, "must be a valid URL")
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        _fail(path, "must be an HTTPS URL without embedded credentials")
    return value


def _enum(value: Any, allowed: set[str], path: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        _fail(path, f"must be one of: {', '.join(sorted(allowed))}")
    return value


def _unique(values: list[Any], path: str) -> None:
    try:
        unique = set(values)
    except TypeError:
        _fail(path, "entries must be scalar values")
    if len(unique) != len(values):
        _fail(path, "must not contain duplicates")


def _close(left: float, right: float, tolerance: float = TOLERANCE) -> bool:
    return math.isclose(left, right, rel_tol=tolerance, abs_tol=tolerance)


def _header(document: dict[str, Any], expected_type: str) -> None:
    if document.get("document_type") != expected_type:
        _fail("document.document_type", f"must be {expected_type!r}")
    if type(document.get("schema_version")) is not int or document.get("schema_version") != SCHEMA_VERSION:
        _fail("document.schema_version", f"must be {SCHEMA_VERSION}")


def _media_list(value: Any, path: str) -> list[str]:
    values = _array(value, path)
    for index, medium in enumerate(values):
        _enum(medium, MEDIA, f"{path}[{index}]")
    _unique(values, path)
    return values


def validate_observer_registry(document: Any) -> dict[str, Any]:
    document = _object(
        document,
        {"document_type", "schema_version", "updated_at", "brand_families", "observers"},
    )
    _header(document, "media-observer-registry")
    _timestamp(document["updated_at"], "document.updated_at")

    family_ids: set[str] = set()
    for index, family in enumerate(_array(document["brand_families"], "document.brand_families")):
        path = f"document.brand_families[{index}]"
        family = _object(family, {"id", "display_name"}, path=path)
        family_id = _slug(family["id"], f"{path}.id")
        if family_id in family_ids:
            _fail(f"{path}.id", "duplicates another brand family")
        family_ids.add(family_id)
        _text(family["display_name"], f"{path}.display_name", maximum=180)

    observer_ids: set[str] = set()
    channel_ids: set[str] = set()
    endpoint_urls: set[str] = set()
    observers_by_family: dict[str, int] = {}
    for index, observer in enumerate(_array(document["observers"], "document.observers")):
        path = f"document.observers[{index}]"
        observer = _object(
            observer,
            {
                "id",
                "display_name",
                "brand_family_id",
                "panel_status",
                "status_since",
                "independence_basis",
                "channels",
            },
            {"retired_at"},
            path,
        )
        observer_id = _slug(observer["id"], f"{path}.id")
        if observer_id in observer_ids:
            _fail(f"{path}.id", "duplicates another observer")
        observer_ids.add(observer_id)
        _text(observer["display_name"], f"{path}.display_name", maximum=180)
        family_id = _slug(observer["brand_family_id"], f"{path}.brand_family_id")
        if family_id not in family_ids:
            _fail(f"{path}.brand_family_id", "does not name a declared brand family")
        observers_by_family[family_id] = observers_by_family.get(family_id, 0) + 1
        status = _enum(observer["panel_status"], PANEL_STATUSES, f"{path}.panel_status")
        status_since = _date(observer["status_since"], f"{path}.status_since")
        _text(observer["independence_basis"], f"{path}.independence_basis", maximum=500)
        if status == "retired":
            if "retired_at" not in observer:
                _fail(path, "retired observers require retired_at")
            if _date(observer["retired_at"], f"{path}.retired_at") < status_since:
                _fail(f"{path}.retired_at", "cannot precede status_since")
        elif "retired_at" in observer:
            _fail(f"{path}.retired_at", "is allowed only for retired observers")

        channels = _array(observer["channels"], f"{path}.channels", minimum=1)
        for channel_index, channel in enumerate(channels):
            channel_path = f"{path}.channels[{channel_index}]"
            channel = _object(
                channel,
                {"id", "medium", "collection_method", "endpoint_url", "public_url", "enabled"},
                path=channel_path,
            )
            channel_id = _slug(channel["id"], f"{channel_path}.id")
            if channel_id in channel_ids:
                _fail(f"{channel_path}.id", "duplicates another channel")
            channel_ids.add(channel_id)
            _enum(channel["medium"], MEDIA, f"{channel_path}.medium")
            _slug(channel["collection_method"], f"{channel_path}.collection_method")
            endpoint = _url(channel["endpoint_url"], f"{channel_path}.endpoint_url")
            _url(channel["public_url"], f"{channel_path}.public_url")
            if endpoint in endpoint_urls:
                _fail(f"{channel_path}.endpoint_url", "duplicates another collection endpoint")
            endpoint_urls.add(endpoint)
            if not isinstance(channel["enabled"], bool):
                _fail(f"{channel_path}.enabled", "must be true or false")
            if status == "retired" and channel["enabled"]:
                _fail(f"{channel_path}.enabled", "must be false for a retired observer")

    return document


def _registry_indexes(registry: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, tuple[str, dict[str, Any]]]]:
    observers = {observer["id"]: observer for observer in registry["observers"]}
    channels: dict[str, tuple[str, dict[str, Any]]] = {}
    for observer in registry["observers"]:
        for channel in observer["channels"]:
            channels[channel["id"]] = (observer["id"], channel)
    return observers, channels


def validate_observation_manifest(
    document: Any,
    registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    document = _object(
        document,
        {"document_type", "schema_version", "week", "generated_at", "observations", "stories"},
    )
    _header(document, "media-observation-manifest")
    week = _week(document["week"], "document.week")
    generated_at = _timestamp(document["generated_at"], "document.generated_at")
    observer_index: dict[str, dict[str, Any]] = {}
    channel_index: dict[str, tuple[str, dict[str, Any]]] = {}
    if registry is not None:
        validate_observer_registry(registry)
        observer_index, channel_index = _registry_indexes(registry)

    observation_ids: set[str] = set()
    observations: dict[str, dict[str, Any]] = {}
    observation_times: dict[str, datetime] = {}
    for index, observation in enumerate(_array(document["observations"], "document.observations")):
        path = f"document.observations[{index}]"
        observation = _object(
            observation,
            {
                "id",
                "observer_id",
                "channel_id",
                "medium",
                "normalized_title",
                "url",
                "attention_at",
                "timestamp_basis",
                "story_id",
                "cluster_confidence",
            },
            path=path,
        )
        observation_id = _slug(observation["id"], f"{path}.id")
        if observation_id in observation_ids:
            _fail(f"{path}.id", "duplicates another observation")
        observation_ids.add(observation_id)
        observer_id = _slug(observation["observer_id"], f"{path}.observer_id")
        channel_id = _slug(observation["channel_id"], f"{path}.channel_id")
        medium = _enum(observation["medium"], MEDIA, f"{path}.medium")
        _text(observation["normalized_title"], f"{path}.normalized_title", maximum=300)
        _url(observation["url"], f"{path}.url")
        attention_at = _timestamp(observation["attention_at"], f"{path}.attention_at")
        local_iso = attention_at.astimezone(EASTERN).date().isocalendar()
        if f"{local_iso.year:04d}-W{local_iso.week:02d}" != week:
            _fail(f"{path}.attention_at", f"does not fall in {week} in America/New_York")
        if attention_at > generated_at:
            _fail(f"{path}.attention_at", "cannot be after generated_at")
        _enum(observation["timestamp_basis"], TIMESTAMP_BASES, f"{path}.timestamp_basis")
        _slug(observation["story_id"], f"{path}.story_id")
        _number(observation["cluster_confidence"], f"{path}.cluster_confidence", 0, 1)

        if registry is not None:
            if observer_id not in observer_index:
                _fail(f"{path}.observer_id", "does not name a registered observer")
            if channel_id not in channel_index:
                _fail(f"{path}.channel_id", "does not name a registered channel")
            channel_observer, channel = channel_index[channel_id]
            if channel_observer != observer_id:
                _fail(f"{path}.channel_id", "belongs to a different observer")
            if channel["medium"] != medium:
                _fail(f"{path}.medium", "does not match the registered channel")

        observations[observation_id] = observation
        observation_times[observation_id] = attention_at

    story_ids: set[str] = set()
    referenced_observations: set[str] = set()
    for index, story in enumerate(_array(document["stories"], "document.stories")):
        path = f"document.stories[{index}]"
        story = _object(
            story,
            {"id", "canonical_title", "first_attention_at", "last_attention_at", "observation_ids"},
            {"canonical_url"},
            path,
        )
        story_id = _slug(story["id"], f"{path}.id")
        if story_id in story_ids:
            _fail(f"{path}.id", "duplicates another story")
        story_ids.add(story_id)
        _text(story["canonical_title"], f"{path}.canonical_title", maximum=260)
        if "canonical_url" in story:
            _url(story["canonical_url"], f"{path}.canonical_url")
        first_at = _timestamp(story["first_attention_at"], f"{path}.first_attention_at")
        last_at = _timestamp(story["last_attention_at"], f"{path}.last_attention_at")
        if last_at < first_at:
            _fail(f"{path}.last_attention_at", "cannot precede first_attention_at")
        ids = _array(story["observation_ids"], f"{path}.observation_ids", minimum=1)
        for item_index, observation_id in enumerate(ids):
            observation_id = _slug(observation_id, f"{path}.observation_ids[{item_index}]")
            if observation_id not in observations:
                _fail(f"{path}.observation_ids[{item_index}]", "does not name an observation")
            if observations[observation_id]["story_id"] != story_id:
                _fail(f"{path}.observation_ids[{item_index}]", "names an observation assigned to another story")
            if observation_id in referenced_observations:
                _fail(f"{path}.observation_ids[{item_index}]", "is already assigned to another story")
            referenced_observations.add(observation_id)
        times = [observation_times[observation_id] for observation_id in ids]
        if first_at != min(times) or last_at != max(times):
            _fail(path, "story time bounds must equal its observations' time bounds")

    if referenced_observations != observation_ids:
        missing = sorted(observation_ids - referenced_observations)
        _fail("document.stories", f"does not account for observations: {', '.join(missing)}")
    for observation_id, observation in observations.items():
        if observation["story_id"] not in story_ids:
            _fail(f"observation {observation_id}.story_id", "does not name a declared story")
    return document


def _validate_coverage(value: Any, path: str, with_ids: bool) -> dict[str, Any]:
    required = {"included_media", "material_limitations"}
    if with_ids:
        required |= {"attempted_observer_ids", "successful_observer_ids"}
    else:
        required |= {"attempted_observer_count", "successful_observer_count", "excluded_media"}
    value = _object(value, required, path=path)
    included = _media_list(value["included_media"], f"{path}.included_media")
    if not included:
        _fail(f"{path}.included_media", "must identify at least one measured medium")
    limitations = _array(value["material_limitations"], f"{path}.material_limitations")
    for index, limitation in enumerate(limitations):
        _text(limitation, f"{path}.material_limitations[{index}]", maximum=300)
    if with_ids:
        attempted = _array(value["attempted_observer_ids"], f"{path}.attempted_observer_ids", minimum=1)
        successful = _array(value["successful_observer_ids"], f"{path}.successful_observer_ids", minimum=1)
        for index, observer_id in enumerate(attempted):
            _slug(observer_id, f"{path}.attempted_observer_ids[{index}]")
        for index, observer_id in enumerate(successful):
            _slug(observer_id, f"{path}.successful_observer_ids[{index}]")
        _unique(attempted, f"{path}.attempted_observer_ids")
        _unique(successful, f"{path}.successful_observer_ids")
        if not set(successful) <= set(attempted):
            _fail(f"{path}.successful_observer_ids", "must be a subset of attempted observers")
    else:
        excluded = _media_list(value["excluded_media"], f"{path}.excluded_media")
        if set(included) & set(excluded):
            _fail(path, "included_media and excluded_media must not overlap")
        attempted_count = _integer(value["attempted_observer_count"], f"{path}.attempted_observer_count")
        successful_count = _integer(value["successful_observer_count"], f"{path}.successful_observer_count")
        if successful_count > attempted_count:
            _fail(f"{path}.successful_observer_count", "cannot exceed attempted_observer_count")
    return value


def validate_weekly_model(
    document: Any,
    registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    document = _object(
        document,
        {
            "document_type",
            "schema_version",
            "week",
            "previous_model_week",
            "generated_at",
            "effective_at",
            "timezone",
            "inference_window",
            "model",
            "counts",
            "coverage",
            "previous_week_active_observer_ids",
            "memberships",
            "networks",
            "lineage_events",
            "diagnostics",
        },
    )
    _header(document, "media-weekly-model")
    week = _week(document["week"], "document.week")
    previous_week = document["previous_model_week"]
    if previous_week is not None:
        previous_week = _week(previous_week, "document.previous_model_week")
        if previous_week >= week:
            _fail("document.previous_model_week", "must precede week")
    if document["timezone"] != "America/New_York":
        _fail("document.timezone", "must be America/New_York")
    generated_at = _timestamp(document["generated_at"], "document.generated_at")
    effective_at = _timestamp(document["effective_at"], "document.effective_at")
    effective_local = effective_at.astimezone(EASTERN)
    effective_iso = effective_local.date().isocalendar()
    if f"{effective_iso.year:04d}-W{effective_iso.week:02d}" != week:
        _fail("document.effective_at", f"must fall in {week}")
    if (effective_local.weekday(), effective_local.hour, effective_local.minute, effective_local.second) != (0, 4, 0, 0):
        _fail("document.effective_at", "must be Monday at 4:00 a.m. America/New_York")
    if generated_at < effective_at:
        _fail("document.generated_at", "cannot precede effective_at")

    window = _object(
        document["inference_window"],
        {"start_at", "end_at", "completed_weeks", "half_life_weeks"},
        path="document.inference_window",
    )
    start_at = _timestamp(window["start_at"], "document.inference_window.start_at")
    end_at = _timestamp(window["end_at"], "document.inference_window.end_at")
    completed_weeks = _integer(window["completed_weeks"], "document.inference_window.completed_weeks", 1)
    _number(window["half_life_weeks"], "document.inference_window.half_life_weeks", 0.01)
    start_local = start_at.astimezone(EASTERN)
    end_local = end_at.astimezone(EASTERN)
    if any((start_local.hour, start_local.minute, start_local.second, end_local.hour, end_local.minute, end_local.second)):
        _fail("document.inference_window", "boundaries must be local midnight")
    if start_local.weekday() != 0 or end_local.weekday() != 0:
        _fail("document.inference_window", "boundaries must be Mondays in America/New_York")
    if (end_local.date() - start_local.date()).days != 7 * completed_weeks:
        _fail("document.inference_window", "local date span must match completed_weeks")
    if end_local.date() != effective_local.date():
        _fail("document.inference_window.end_at", "must end at midnight before the weekly run")

    model = _object(
        document["model"],
        {
            "algorithm",
            "implementation_version",
            "code_version",
            "factor_count",
            "random_seed",
            "continuity_threshold",
        },
        path="document.model",
    )
    if model["algorithm"] != "non-negative-matrix-factorization":
        _fail("document.model.algorithm", "must be non-negative-matrix-factorization in v1")
    _slug(model["implementation_version"], "document.model.implementation_version")
    if not isinstance(model["code_version"], str) or not CODE_VERSION.fullmatch(model["code_version"]):
        _fail("document.model.code_version", "must be a 7- to 40-character lowercase Git commit SHA")
    factor_count = _integer(model["factor_count"], "document.model.factor_count", 1)
    _integer(model["random_seed"], "document.model.random_seed")
    continuity_threshold = _number(model["continuity_threshold"], "document.model.continuity_threshold", 0, 1)

    counts = _object(
        document["counts"],
        {"observer_count", "story_count", "observation_count"},
        path="document.counts",
    )
    observer_count = _integer(counts["observer_count"], "document.counts.observer_count", 1)
    _integer(counts["story_count"], "document.counts.story_count", 1)
    _integer(counts["observation_count"], "document.counts.observation_count", 1)
    coverage = _validate_coverage(document["coverage"], "document.coverage", with_ids=False)
    if coverage["successful_observer_count"] != observer_count:
        _fail("document.coverage.successful_observer_count", "must equal counts.observer_count")

    registry_observers: set[str] | None = None
    if registry is not None:
        validate_observer_registry(registry)
        registry_observers = {observer["id"] for observer in registry["observers"]}

    networks = _array(document["networks"], "document.networks", minimum=1)
    if len(networks) != factor_count:
        _fail("document.networks", "length must equal model.factor_count")
    lineage_ids: list[str] = []
    version_ids: list[str] = []
    labels: list[str] = []
    factor_indexes: list[int] = []
    network_by_version: dict[str, dict[str, Any]] = {}
    for index, network in enumerate(networks):
        path = f"document.networks[{index}]"
        network = _object(
            network,
            {
                "lineage_id",
                "version_id",
                "label",
                "factor_index",
                "effective_membership",
                "continuity",
                "participants",
            },
            path=path,
        )
        lineage_id = _lineage_id(network["lineage_id"], f"{path}.lineage_id")
        version_id = _version_id(network["version_id"], week, f"{path}.version_id")
        if not version_id.startswith(lineage_id + "@"):
            _fail(f"{path}.version_id", "must begin with lineage_id")
        label = _network_label(network["label"], f"{path}.label")
        factor_index = _integer(network["factor_index"], f"{path}.factor_index")
        _number(network["effective_membership"], f"{path}.effective_membership", 1)
        continuity = _object(
            network["continuity"],
            {"relationship", "primary_predecessor_version_id", "cosine_similarity"},
            path=f"{path}.continuity",
        )
        relationship = _enum(
            continuity["relationship"],
            LINEAGE_RELATIONSHIPS,
            f"{path}.continuity.relationship",
        )
        predecessor = continuity["primary_predecessor_version_id"]
        similarity = continuity["cosine_similarity"]
        if relationship == "birth":
            if predecessor is not None or similarity is not None:
                _fail(f"{path}.continuity", "births cannot claim a predecessor or similarity")
        else:
            if previous_week is None:
                _fail(f"{path}.continuity", "a predecessor requires previous_model_week")
            predecessor = _version_id(predecessor, previous_week, f"{path}.continuity.primary_predecessor_version_id")
            similarity = _number(similarity, f"{path}.continuity.cosine_similarity", 0, 1)
            if relationship == "uncertain" and similarity >= continuity_threshold:
                _fail(f"{path}.continuity.cosine_similarity", "uncertain matches must fall below the threshold")
            if relationship != "uncertain" and similarity < continuity_threshold:
                _fail(f"{path}.continuity.cosine_similarity", "claimed continuity must meet the threshold")
            if relationship in {"continuation", "merger"}:
                predecessor_lineage = predecessor.partition("@")[0]
                if predecessor_lineage != lineage_id:
                    _fail(f"{path}.lineage_id", "must retain the primary predecessor's lineage")
            if relationship == "uncertain" and predecessor.partition("@")[0] == lineage_id:
                _fail(f"{path}.lineage_id", "uncertain matches must receive a new lineage")
        lineage_ids.append(lineage_id)
        version_ids.append(version_id)
        labels.append(label)
        factor_indexes.append(factor_index)
        network_by_version[version_id] = network
    _unique(lineage_ids, "document.networks[].lineage_id")
    _unique(version_ids, "document.networks[].version_id")
    _unique(labels, "document.networks[].label")
    if sorted(factor_indexes) != list(range(factor_count)):
        _fail("document.networks[].factor_index", "must be a permutation of 0 through factor_count - 1")

    membership_rows = _array(document["memberships"], "document.memberships", minimum=1)
    if len(membership_rows) != observer_count:
        _fail("document.memberships", "length must equal counts.observer_count")
    weights_by_observer: dict[str, dict[str, float]] = {}
    for index, membership in enumerate(membership_rows):
        path = f"document.memberships[{index}]"
        membership = _object(membership, {"observer_id", "weights"}, path=path)
        observer_id = _slug(membership["observer_id"], f"{path}.observer_id")
        if observer_id in weights_by_observer:
            _fail(f"{path}.observer_id", "duplicates another membership row")
        if registry_observers is not None and observer_id not in registry_observers:
            _fail(f"{path}.observer_id", "does not name a registered observer")
        weights = membership["weights"]
        if not isinstance(weights, dict) or set(weights) != set(lineage_ids):
            _fail(f"{path}.weights", "must contain exactly one weight for every current lineage")
        parsed_weights = {
            lineage_id: _number(weight, f"{path}.weights.{lineage_id}", 0, 1)
            for lineage_id, weight in weights.items()
        }
        if not _close(sum(parsed_weights.values()), 1):
            _fail(f"{path}.weights", "must sum to 1 within tolerance")
        weights_by_observer[observer_id] = parsed_weights

    active_ids = _array(
        document["previous_week_active_observer_ids"],
        "document.previous_week_active_observer_ids",
        minimum=1,
    )
    for index, observer_id in enumerate(active_ids):
        _slug(observer_id, f"document.previous_week_active_observer_ids[{index}]")
    _unique(active_ids, "document.previous_week_active_observer_ids")
    if not set(active_ids) <= set(weights_by_observer):
        _fail("document.previous_week_active_observer_ids", "must be a subset of modeled observers")

    for index, network in enumerate(networks):
        path = f"document.networks[{index}]"
        lineage_id = network["lineage_id"]
        weights = {observer_id: row[lineage_id] for observer_id, row in weights_by_observer.items()}
        mass = sum(weights.values())
        squared_mass = sum(weight * weight for weight in weights.values())
        if squared_mass <= 0:
            _fail(f"{path}.effective_membership", "a network must have positive membership mass")
        expected_effective = mass * mass / squared_mass
        if not _close(network["effective_membership"], expected_effective):
            _fail(f"{path}.effective_membership", "does not match the complete membership matrix")
        active_mass = sum(weights[observer_id] for observer_id in active_ids)
        if active_mass <= 0:
            _fail(f"{path}.participants", "cannot be computed from zero active membership mass")
        expected_participants = [
            (observer_id, weights[observer_id] / active_mass, weights[observer_id])
            for observer_id in active_ids
            if weights[observer_id] > 0
        ]
        expected_participants.sort(key=lambda item: (-item[1], item[0]))
        participants = _array(network["participants"], f"{path}.participants", minimum=1)
        if len(participants) != len(expected_participants):
            _fail(f"{path}.participants", "must contain every preceding-week participant with nonzero mass")
        for participant_index, (participant, expected) in enumerate(zip(participants, expected_participants)):
            participant_path = f"{path}.participants[{participant_index}]"
            participant = _object(
                participant,
                {"observer_id", "network_share", "observer_affinity"},
                path=participant_path,
            )
            observer_id = _slug(participant["observer_id"], f"{participant_path}.observer_id")
            share = _number(participant["network_share"], f"{participant_path}.network_share", 0, 1)
            affinity = _number(participant["observer_affinity"], f"{participant_path}.observer_affinity", 0, 1)
            if observer_id != expected[0] or not _close(share, expected[1]) or not _close(affinity, expected[2]):
                _fail(participant_path, "does not match the membership-derived participant table")

    event_targets: list[str] = []
    event_sources: list[str] = []
    event_ids: list[str] = []
    for index, event in enumerate(_array(document["lineage_events"], "document.lineage_events", minimum=1)):
        path = f"document.lineage_events[{index}]"
        event = _object(
            event,
            {"id", "type", "from_version_ids", "to_version_ids", "links"},
            path=path,
        )
        event_ids.append(_slug(event["id"], f"{path}.id"))
        event_type = _enum(event["type"], LINEAGE_EVENT_TYPES, f"{path}.type")
        from_ids = _array(event["from_version_ids"], f"{path}.from_version_ids")
        to_ids = _array(event["to_version_ids"], f"{path}.to_version_ids")
        if previous_week is None and from_ids:
            _fail(f"{path}.from_version_ids", "cannot be used without previous_model_week")
        for item_index, version_id in enumerate(from_ids):
            _version_id(version_id, previous_week, f"{path}.from_version_ids[{item_index}]")
        for item_index, version_id in enumerate(to_ids):
            _version_id(version_id, week, f"{path}.to_version_ids[{item_index}]")
            if version_id not in network_by_version:
                _fail(f"{path}.to_version_ids[{item_index}]", "does not name a current network")
        _unique(from_ids, f"{path}.from_version_ids")
        _unique(to_ids, f"{path}.to_version_ids")
        shapes = {
            "birth": (0, 1),
            "continuation": (1, 1),
            "retirement": (1, 0),
            "uncertain": (1, 1),
        }
        if event_type in shapes and (len(from_ids), len(to_ids)) != shapes[event_type]:
            _fail(path, f"{event_type} must have {shapes[event_type][0]} predecessor(s) and {shapes[event_type][1]} successor(s)")
        if event_type == "split" and not (len(from_ids) == 1 and len(to_ids) >= 2):
            _fail(path, "split must have one predecessor and at least two successors")
        if event_type == "merger" and not (len(from_ids) >= 2 and len(to_ids) == 1):
            _fail(path, "merger must have at least two predecessors and one successor")
        if event_type == "split":
            parent = from_ids[0].partition("@")[0]
            inheritors = [version for version in to_ids if version.partition("@")[0] == parent]
            if len(inheritors) != 1:
                _fail(path, "exactly one split successor must retain the parent lineage")
        links = _array(event["links"], f"{path}.links")
        if event_type in {"birth", "retirement"} and links:
            _fail(f"{path}.links", f"{event_type} cannot contain similarity links")
        if event_type not in {"birth", "retirement"} and not links:
            _fail(f"{path}.links", f"{event_type} requires similarity links")
        link_pairs = []
        for link_index, link in enumerate(links):
            link_path = f"{path}.links[{link_index}]"
            link = _object(
                link,
                {"from_version_id", "to_version_id", "cosine_similarity", "shared_membership_mass"},
                path=link_path,
            )
            if link["from_version_id"] not in from_ids or link["to_version_id"] not in to_ids:
                _fail(link_path, "must connect versions declared by the event")
            _number(link["cosine_similarity"], f"{link_path}.cosine_similarity", 0, 1)
            _number(link["shared_membership_mass"], f"{link_path}.shared_membership_mass", 0)
            link_pairs.append((link["from_version_id"], link["to_version_id"]))
        if event_type not in {"birth", "retirement"}:
            if set(link_pairs) != {(source, target) for source in from_ids for target in to_ids}:
                _fail(f"{path}.links", "must connect every declared predecessor and successor")
            _unique(link_pairs, f"{path}.links")
        for version_id in to_ids:
            network = network_by_version[version_id]
            if network["continuity"]["relationship"] != event_type:
                _fail(path, "event type must match each successor's continuity relationship")
            predecessor = network["continuity"]["primary_predecessor_version_id"]
            if predecessor is not None:
                matching_links = [
                    link for link in links
                    if link["from_version_id"] == predecessor and link["to_version_id"] == version_id
                ]
                if len(matching_links) != 1 or not _close(
                    matching_links[0]["cosine_similarity"],
                    network["continuity"]["cosine_similarity"],
                ):
                    _fail(path, "must contain the successor's primary similarity link")
        event_targets.extend(to_ids)
        event_sources.extend(from_ids)
    _unique(event_ids, "document.lineage_events[].id")
    _unique(event_sources, "document.lineage_events[].from_version_ids")
    if sorted(event_targets) != sorted(version_ids):
        _fail("document.lineage_events", "must account for each current network exactly once")

    diagnostics = _object(
        document["diagnostics"],
        {
            "held_out_reconstruction_error",
            "held_out_fraction",
            "bootstrap_stability",
            "bootstrap_runs",
            "observer_sensitivity",
            "brand_family_sensitivity",
            "network_story_overlap",
        },
        path="document.diagnostics",
    )
    _number(diagnostics["held_out_reconstruction_error"], "document.diagnostics.held_out_reconstruction_error", 0)
    _number(diagnostics["held_out_fraction"], "document.diagnostics.held_out_fraction", 0, 1)
    _number(diagnostics["bootstrap_stability"], "document.diagnostics.bootstrap_stability", 0, 1)
    _integer(diagnostics["bootstrap_runs"], "document.diagnostics.bootstrap_runs", 1)
    _number(diagnostics["observer_sensitivity"], "document.diagnostics.observer_sensitivity", 0, 1)
    _number(diagnostics["brand_family_sensitivity"], "document.diagnostics.brand_family_sensitivity", 0, 1)
    overlap_pairs: set[tuple[str, str]] = set()
    for index, overlap in enumerate(_array(diagnostics["network_story_overlap"], "document.diagnostics.network_story_overlap")):
        path = f"document.diagnostics.network_story_overlap[{index}]"
        overlap = _object(overlap, {"left_lineage_id", "right_lineage_id", "similarity"}, path=path)
        left = _lineage_id(overlap["left_lineage_id"], f"{path}.left_lineage_id")
        right = _lineage_id(overlap["right_lineage_id"], f"{path}.right_lineage_id")
        if left not in lineage_ids or right not in lineage_ids or left >= right:
            _fail(path, "must name two current lineages in lexical order")
        pair = (left, right)
        if pair in overlap_pairs:
            _fail(path, "duplicates another network pair")
        overlap_pairs.add(pair)
        _number(overlap["similarity"], f"{path}.similarity", 0, 1)
    expected_pairs = {
        (left, right)
        for left_index, left in enumerate(sorted(lineage_ids))
        for right in sorted(lineage_ids)[left_index + 1 :]
    }
    if overlap_pairs != expected_pairs:
        _fail("document.diagnostics.network_story_overlap", "must contain every current network pair exactly once")
    return document


def validate_current_snapshot(
    document: Any,
    registry: dict[str, Any] | None = None,
    weekly_model: dict[str, Any] | None = None,
    observation_manifest: dict[str, Any] | list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    document = _object(
        document,
        {
            "document_type",
            "schema_version",
            "scheduled_for",
            "generated_at",
            "timezone",
            "window",
            "weekly_model_week",
            "coverage",
            "networks",
        },
    )
    _header(document, "media-current-snapshot")
    if document["timezone"] != "America/New_York":
        _fail("document.timezone", "must be America/New_York")
    scheduled_for = _timestamp(document["scheduled_for"], "document.scheduled_for")
    generated_at = _timestamp(document["generated_at"], "document.generated_at")
    scheduled_local = scheduled_for.astimezone(EASTERN)
    if scheduled_local.minute or scheduled_local.second or scheduled_local.hour % 3:
        _fail("document.scheduled_for", "must be a three-hour boundary in America/New_York")
    if generated_at < scheduled_for:
        _fail("document.generated_at", "cannot precede scheduled_for")
    model_week = _week(document["weekly_model_week"], "document.weekly_model_week")
    window = _object(document["window"], {"start_at", "end_at"}, path="document.window")
    start_at = _timestamp(window["start_at"], "document.window.start_at")
    end_at = _timestamp(window["end_at"], "document.window.end_at")
    if end_at != scheduled_for:
        _fail("document.window.end_at", "must equal scheduled_for")
    if end_at - start_at != timedelta(hours=24):
        _fail("document.window", "must span exactly 24 elapsed hours")
    coverage = _validate_coverage(document["coverage"], "document.coverage", with_ids=True)
    successful_observers = set(coverage["successful_observer_ids"])

    registry_observers: dict[str, dict[str, Any]] = {}
    if registry is not None:
        validate_observer_registry(registry)
        registry_observers, _ = _registry_indexes(registry)
        attempted = set(coverage["attempted_observer_ids"])
        if not attempted <= set(registry_observers):
            _fail("document.coverage.attempted_observer_ids", "contains an unregistered observer")

    weekly_by_lineage: dict[str, dict[str, Any]] = {}
    if weekly_model is not None:
        validate_weekly_model(weekly_model, registry)
        if weekly_model["week"] != model_week:
            _fail("document.weekly_model_week", "does not match the supplied weekly model")
        weekly_by_lineage = {network["lineage_id"]: network for network in weekly_model["networks"]}
        modeled = {row["observer_id"] for row in weekly_model["memberships"]}
        if not successful_observers <= modeled:
            _fail("document.coverage.successful_observer_ids", "must contain only modeled observers")
        if _timestamp(weekly_model["generated_at"], "weekly_model.generated_at") > generated_at:
            _fail("document.weekly_model_week", "cannot use a model generated after this snapshot")

    observations: dict[str, dict[str, Any]] = {}
    if observation_manifest is not None:
        manifests = observation_manifest if isinstance(observation_manifest, list) else [observation_manifest]
        for manifest in manifests:
            validate_observation_manifest(manifest, registry)
            if _timestamp(manifest["generated_at"], "observation_manifest.generated_at") > generated_at:
                _fail("observation manifests", "cannot have been generated after the snapshot")
            for item in manifest["observations"]:
                if item["id"] in observations:
                    _fail("observation manifests", "duplicate observation across manifests")
                observations[item["id"]] = item

    lineage_ids: list[str] = []
    labels: list[str] = []
    for index, network in enumerate(_array(document["networks"], "document.networks", minimum=1)):
        path = f"document.networks[{index}]"
        network = _object(
            network,
            {
                "lineage_id",
                "version_id",
                "label",
                "continuity_similarity",
                "effective_membership",
                "participants",
                "stories",
            },
            path=path,
        )
        lineage_id = _lineage_id(network["lineage_id"], f"{path}.lineage_id")
        version_id = _version_id(network["version_id"], model_week, f"{path}.version_id")
        if not version_id.startswith(lineage_id + "@"):
            _fail(f"{path}.version_id", "must begin with lineage_id")
        label = _network_label(network["label"], f"{path}.label")
        if network["continuity_similarity"] is not None:
            _number(network["continuity_similarity"], f"{path}.continuity_similarity", 0, 1)
        _number(network["effective_membership"], f"{path}.effective_membership", 1)
        lineage_ids.append(lineage_id)
        labels.append(label)

        participants = _array(network["participants"], f"{path}.participants", maximum=3)
        prior_share = float("inf")
        participant_ids: list[str] = []
        for participant_index, participant in enumerate(participants):
            participant_path = f"{path}.participants[{participant_index}]"
            participant = _object(
                participant,
                {"observer_id", "network_share", "observer_affinity"},
                path=participant_path,
            )
            observer_id = _slug(participant["observer_id"], f"{participant_path}.observer_id")
            share = _number(participant["network_share"], f"{participant_path}.network_share", 0, 1)
            _number(participant["observer_affinity"], f"{participant_path}.observer_affinity", 0, 1)
            if share > prior_share + TOLERANCE:
                _fail(participant_path, "participants must be ordered by descending network_share")
            prior_share = share
            participant_ids.append(observer_id)
            if registry is not None and observer_id not in registry_observers:
                _fail(f"{participant_path}.observer_id", "does not name a registered observer")
        _unique(participant_ids, f"{path}.participants[].observer_id")

        if weekly_model is not None:
            if lineage_id not in weekly_by_lineage:
                _fail(f"{path}.lineage_id", "does not appear in the weekly model")
            weekly_network = weekly_by_lineage[lineage_id]
            expected_similarity = weekly_network["continuity"]["cosine_similarity"]
            if network["version_id"] != weekly_network["version_id"]:
                _fail(f"{path}.version_id", "does not match the weekly model")
            if network["label"] != weekly_network["label"]:
                _fail(f"{path}.label", "does not match the weekly model")
            if not _close(network["effective_membership"], weekly_network["effective_membership"]):
                _fail(f"{path}.effective_membership", "does not match the weekly model")
            if expected_similarity is None:
                if network["continuity_similarity"] is not None:
                    _fail(f"{path}.continuity_similarity", "must be null for a birth")
            elif network["continuity_similarity"] is None or not _close(network["continuity_similarity"], expected_similarity):
                _fail(f"{path}.continuity_similarity", "does not match the weekly model")
            if participants != weekly_network["participants"][:3]:
                _fail(f"{path}.participants", "must be the weekly model's first three participants")

        story_ids: list[str] = []
        prior_score = float("inf")
        for story_index, story in enumerate(_array(network["stories"], f"{path}.stories", maximum=3)):
            story_path = f"{path}.stories[{story_index}]"
            story = _object(
                story,
                {
                    "story_id",
                    "title",
                    "canonical_url",
                    "weighted_attention",
                    "panel_attention",
                    "over_index",
                    "independent_observer_count",
                    "independent_brand_family_count",
                    "latest_observed_at",
                    "representative_links",
                    "evidence_observation_ids",
                },
                path=story_path,
            )
            story_id = _slug(story["story_id"], f"{story_path}.story_id")
            story_ids.append(story_id)
            _text(story["title"], f"{story_path}.title", maximum=260)
            _url(story["canonical_url"], f"{story_path}.canonical_url")
            score = _number(story["weighted_attention"], f"{story_path}.weighted_attention", 0, 1)
            panel_score = _number(story["panel_attention"], f"{story_path}.panel_attention", TOLERANCE, 1)
            over_index = _number(story["over_index"], f"{story_path}.over_index", 0)
            if not _close(over_index, score / panel_score):
                _fail(f"{story_path}.over_index", "must equal weighted_attention / panel_attention")
            if score > prior_score + TOLERANCE:
                _fail(story_path, "stories must be ordered by descending weighted_attention")
            prior_score = score
            observer_breadth = _integer(
                story["independent_observer_count"],
                f"{story_path}.independent_observer_count",
                3,
            )
            brand_breadth = _integer(
                story["independent_brand_family_count"],
                f"{story_path}.independent_brand_family_count",
                3,
            )
            if brand_breadth > observer_breadth:
                _fail(f"{story_path}.independent_brand_family_count", "cannot exceed observer breadth")
            if observer_breadth > len(successful_observers):
                _fail(story_path, "observer breadth cannot exceed successfully checked observers")
            evidence_ids = _array(story["evidence_observation_ids"], f"{story_path}.evidence_observation_ids", minimum=3)
            for evidence_index, evidence_id in enumerate(evidence_ids):
                _slug(evidence_id, f"{story_path}.evidence_observation_ids[{evidence_index}]")
            _unique(evidence_ids, f"{story_path}.evidence_observation_ids")
            if observation_manifest is not None:
                evidence_observers = set()
                evidence_times = []
                for evidence_id in evidence_ids:
                    if evidence_id not in observations:
                        _fail(story_path, "evidence references an unknown observation")
                    evidence = observations[evidence_id]
                    evidence_at = _timestamp(evidence["attention_at"], story_path)
                    if evidence["story_id"] != story_id or not start_at <= evidence_at < end_at:
                        _fail(story_path, "evidence must concern this story inside the half-open window")
                    if evidence["observer_id"] not in successful_observers:
                        _fail(story_path, "evidence must come from a successfully checked observer")
                    evidence_observers.add(evidence["observer_id"])
                    evidence_times.append(evidence_at)
                if len(evidence_observers) != observer_breadth:
                    _fail(story_path, "observer breadth does not match deduplicated evidence")
                if registry is not None:
                    families = {registry_observers[observer]["brand_family_id"] for observer in evidence_observers}
                    if len(families) != brand_breadth:
                        _fail(story_path, "brand-family breadth does not match evidence")
                if _timestamp(story["latest_observed_at"], story_path) != max(evidence_times):
                    _fail(story_path, "latest observation does not match evidence")
                if weekly_model is not None:
                    weights = {row["observer_id"]: row["weights"][lineage_id] for row in weekly_model["memberships"]}
                    latest_by_observer: dict[str, datetime] = {}
                    for evidence_id in evidence_ids:
                        evidence = observations[evidence_id]
                        observer = evidence["observer_id"]
                        observed_at = _timestamp(evidence["attention_at"], story_path)
                        latest_by_observer[observer] = max(observed_at, latest_by_observer.get(observer, observed_at))
                    denominator = sum(weights[observer] for observer in successful_observers)
                    if denominator <= 0:
                        _fail(story_path, "cannot score a network with zero checked membership mass")
                    decay = {observer: 2 ** (-(end_at - observed_at).total_seconds() / 43200)
                             for observer, observed_at in latest_by_observer.items()}
                    expected_score = sum(weights[observer] * value for observer, value in decay.items()) / denominator
                    expected_panel = sum(decay.values()) / len(successful_observers)
                    if not _close(score, expected_score) or not _close(panel_score, expected_panel):
                        _fail(story_path, "attention scores do not match deduplicated evidence and checked membership")
            latest = _timestamp(story["latest_observed_at"], f"{story_path}.latest_observed_at")
            if not start_at <= latest < end_at:
                _fail(f"{story_path}.latest_observed_at", "must fall inside the trailing window")
            links = _array(story["representative_links"], f"{story_path}.representative_links", minimum=1, maximum=3)
            representative_observers: list[str] = []
            representative_observations: list[str] = []
            for link_index, link in enumerate(links):
                link_path = f"{story_path}.representative_links[{link_index}]"
                link = _object(
                    link,
                    {"observation_id", "observer_id", "label", "url", "medium", "attention_at"},
                    path=link_path,
                )
                observation_id = _slug(link["observation_id"], f"{link_path}.observation_id")
                if observation_id not in evidence_ids:
                    _fail(link_path, "representative links must be drawn from the evidence set")
                observer_id = _slug(link["observer_id"], f"{link_path}.observer_id")
                _text(link["label"], f"{link_path}.label", maximum=180)
                _url(link["url"], f"{link_path}.url")
                _enum(link["medium"], MEDIA, f"{link_path}.medium")
                attention_at = _timestamp(link["attention_at"], f"{link_path}.attention_at")
                if not start_at <= attention_at < end_at:
                    _fail(f"{link_path}.attention_at", "must fall inside the trailing window")
                if observer_id not in successful_observers:
                    _fail(f"{link_path}.observer_id", "must be a successfully checked observer")
                if observation_manifest is not None:
                    if observation_id not in observations:
                        _fail(f"{link_path}.observation_id", "does not name a supplied observation")
                    observation = observations[observation_id]
                    expected = {
                        "observer_id": observer_id,
                        "url": link["url"],
                        "medium": link["medium"],
                        "attention_at": link["attention_at"],
                        "story_id": story_id,
                    }
                    for field, value in expected.items():
                        if observation[field] != value:
                            _fail(link_path, f"does not match the observation's {field}")
                representative_observers.append(observer_id)
                representative_observations.append(observation_id)
            _unique(representative_observers, f"{story_path}.representative_links[].observer_id")
            _unique(representative_observations, f"{story_path}.representative_links[].observation_id")
        _unique(story_ids, f"{path}.stories[].story_id")
    _unique(lineage_ids, "document.networks[].lineage_id")
    _unique(labels, "document.networks[].label")
    if weekly_model is not None and set(lineage_ids) != set(weekly_by_lineage):
        _fail("document.networks", "must contain every weekly network exactly once")
    return document


def validate_model_order_diagnostics(document: Any) -> dict[str, Any]:
    document = _object(
        document,
        {
            "document_type",
            "schema_version",
            "evaluation_id",
            "generated_at",
            "review_type",
            "current_k",
            "weekly_cutoffs",
            "candidates",
            "decision",
        },
    )
    _header(document, "media-model-order-diagnostics")
    _slug(document["evaluation_id"], "document.evaluation_id")
    _timestamp(document["generated_at"], "document.generated_at")
    review_type = _enum(
        document["review_type"],
        {"prelaunch-exhaustive", "routine-local", "triggered-exhaustive"},
        "document.review_type",
    )
    current_k = document["current_k"]
    if current_k is not None:
        current_k = _integer(current_k, "document.current_k", 2)
    cutoffs = _array(document["weekly_cutoffs"], "document.weekly_cutoffs", minimum=1)
    for index, cutoff in enumerate(cutoffs):
        _week(cutoff, f"document.weekly_cutoffs[{index}]")
    _unique(cutoffs, "document.weekly_cutoffs")
    cutoff_dates = sorted(date.fromisocalendar(int(week[:4]), int(week[-2:]), 1) for week in cutoffs)
    if any((right - left).days != 7 for left, right in zip(cutoff_dates, cutoff_dates[1:])):
        _fail("document.weekly_cutoffs", "must be adjacent weekly endpoints")

    candidate_keys: set[tuple[str, int]] = set()
    k_values: set[int] = set()
    for index, candidate in enumerate(_array(document["candidates"], "document.candidates", minimum=1)):
        path = f"document.candidates[{index}]"
        candidate = _object(
            candidate,
            {
                "weekly_cutoff",
                "k",
                "held_out_reconstruction_error",
                "held_out_standard_error",
                "bootstrap_stability",
                "minimum_effective_membership",
                "median_effective_membership",
                "agenda_distinctiveness",
                "observer_sensitivity",
                "brand_family_sensitivity",
                "passes_quality_gates",
            },
            path=path,
        )
        cutoff = _week(candidate["weekly_cutoff"], f"{path}.weekly_cutoff")
        if cutoff not in cutoffs:
            _fail(f"{path}.weekly_cutoff", "does not appear in weekly_cutoffs")
        k = _integer(candidate["k"], f"{path}.k", 2)
        key = (cutoff, k)
        if key in candidate_keys:
            _fail(path, "duplicates another cutoff/k result")
        candidate_keys.add(key)
        k_values.add(k)
        for field in (
            "held_out_reconstruction_error",
            "held_out_standard_error",
            "minimum_effective_membership",
            "median_effective_membership",
        ):
            _number(candidate[field], f"{path}.{field}", 0)
        for field in (
            "bootstrap_stability",
            "agenda_distinctiveness",
            "observer_sensitivity",
            "brand_family_sensitivity",
        ):
            _number(candidate[field], f"{path}.{field}", 0, 1)
        if candidate["median_effective_membership"] < candidate["minimum_effective_membership"]:
            _fail(path, "median_effective_membership cannot be below the minimum")
        if not isinstance(candidate["passes_quality_gates"], bool):
            _fail(f"{path}.passes_quality_gates", "must be true or false")
    expected_keys = {(cutoff, k) for cutoff in cutoffs for k in k_values}
    if candidate_keys != expected_keys:
        _fail("document.candidates", "must evaluate the same k values at every weekly cutoff")
    ordered_k = sorted(k_values)
    if review_type in {"prelaunch-exhaustive", "triggered-exhaustive"}:
        if ordered_k != list(range(min(ordered_k), max(ordered_k) + 1)):
            _fail("document.candidates", "exhaustive reviews require a contiguous k range")
    if review_type == "prelaunch-exhaustive" and (min(ordered_k) > 5 or max(ordered_k) < 15):
        _fail("document.candidates", "the initial exhaustive review must include k=5 through k=15")
    if review_type == "routine-local":
        if current_k is None:
            _fail("document.current_k", "is required for a routine local review")
        expected_k = {k for k in range(current_k - 2, current_k + 3) if k >= 2}
        if k_values != expected_k:
            _fail("document.candidates", "routine local review must cover current k and its four nearest alternatives")

    decision = _object(
        document["decision"],
        {
            "best_k",
            "selected_k",
            "winner_by_cutoff",
            "change_authorized",
            "expanded_search_required",
            "rationale",
        },
        path="document.decision",
    )
    best_k = _integer(decision["best_k"], "document.decision.best_k", 2)
    selected_k = _integer(decision["selected_k"], "document.decision.selected_k", 2)
    if best_k not in k_values or selected_k not in k_values:
        _fail("document.decision", "best_k and selected_k must be evaluated candidates")
    winners = decision["winner_by_cutoff"]
    if not isinstance(winners, dict) or set(winners) != set(cutoffs):
        _fail("document.decision.winner_by_cutoff", "must name one winner for every weekly cutoff")
    for cutoff, winner in winners.items():
        if _integer(winner, f"document.decision.winner_by_cutoff.{cutoff}", 2) not in k_values:
            _fail(f"document.decision.winner_by_cutoff.{cutoff}", "must be an evaluated candidate")
    for field in ("change_authorized", "expanded_search_required"):
        if not isinstance(decision[field], bool):
            _fail(f"document.decision.{field}", "must be true or false")
    _text(decision["rationale"], "document.decision.rationale", maximum=800)
    selected_results = [candidate for candidate in document["candidates"] if candidate["k"] == selected_k]
    if not all(candidate["passes_quality_gates"] for candidate in selected_results):
        _fail("document.decision.selected_k", "must pass the absolute quality gates at every cutoff")
    if best_k in {min(k_values), max(k_values)} and not decision["expanded_search_required"]:
        _fail("document.decision.expanded_search_required", "must be true when the best result is on a search boundary")
    if decision["expanded_search_required"] and decision["change_authorized"]:
        _fail("document.decision.change_authorized", "cannot authorize a change before the expanded search")
    if current_k is not None and selected_k != current_k and decision["change_authorized"]:
        if len(cutoffs) < 2 or set(winners.values()) != {selected_k}:
            _fail("document.decision.change_authorized", "a changed k requires the same winner at two adjacent cutoffs")
    return document


def validate_lineage_scenarios(document: Any) -> dict[str, Any]:
    """Validate synthetic inputs and declared outcomes used by Stage 4 tests."""
    document = _object(
        document,
        {"document_type", "schema_version", "observer_ids", "scenarios"},
    )
    _header(document, "media-lineage-scenarios")
    observer_ids = _array(document["observer_ids"], "document.observer_ids", minimum=2)
    for index, observer_id in enumerate(observer_ids):
        _slug(observer_id, f"document.observer_ids[{index}]")
    _unique(observer_ids, "document.observer_ids")
    observer_set = set(observer_ids)

    scenario_names: set[str] = set()
    for index, scenario in enumerate(_array(document["scenarios"], "document.scenarios", minimum=1)):
        path = f"document.scenarios[{index}]"
        scenario = _object(
            scenario,
            {"name", "threshold", "previous", "current", "expected"},
            path=path,
        )
        name = _slug(scenario["name"], f"{path}.name")
        if name in scenario_names:
            _fail(f"{path}.name", "duplicates another scenario")
        scenario_names.add(name)
        _number(scenario["threshold"], f"{path}.threshold", 0, 1)

        previous_versions: set[str] = set()
        previous_lineages: set[str] = set()
        previous_labels: set[str] = set()
        for previous_index, previous in enumerate(_array(scenario["previous"], f"{path}.previous")):
            previous_path = f"{path}.previous[{previous_index}]"
            previous = _object(
                previous,
                {"version_id", "lineage_id", "label", "weights"},
                path=previous_path,
            )
            lineage_id = _lineage_id(previous["lineage_id"], f"{previous_path}.lineage_id")
            version_id = _text(previous["version_id"], f"{previous_path}.version_id", maximum=100)
            if not version_id.startswith(lineage_id + "@"):
                _fail(f"{previous_path}.version_id", "must begin with lineage_id")
            label = _network_label(previous["label"], f"{previous_path}.label")
            weights = previous["weights"]
            if not isinstance(weights, dict) or set(weights) != observer_set:
                _fail(f"{previous_path}.weights", "must contain every synthetic observer")
            parsed = [_number(value, f"{previous_path}.weights.{key}", 0) for key, value in weights.items()]
            if not _close(sum(parsed), 1):
                _fail(f"{previous_path}.weights", "must sum to 1")
            previous_versions.add(version_id)
            previous_lineages.add(lineage_id)
            previous_labels.add(label)
        if len(previous_versions) != len(scenario["previous"]) or len(previous_lineages) != len(scenario["previous"]) or len(previous_labels) != len(scenario["previous"]):
            _fail(f"{path}.previous", "version ids, lineage ids, and labels must be unique")

        factor_ids: set[str] = set()
        for current_index, current in enumerate(_array(scenario["current"], f"{path}.current", minimum=1)):
            current_path = f"{path}.current[{current_index}]"
            current = _object(current, {"factor_id", "weights"}, path=current_path)
            factor_id = _slug(current["factor_id"], f"{current_path}.factor_id")
            if factor_id in factor_ids:
                _fail(f"{current_path}.factor_id", "duplicates another factor")
            factor_ids.add(factor_id)
            weights = current["weights"]
            if not isinstance(weights, dict) or set(weights) != observer_set:
                _fail(f"{current_path}.weights", "must contain every synthetic observer")
            parsed = [_number(value, f"{current_path}.weights.{key}", 0) for key, value in weights.items()]
            if not _close(sum(parsed), 1):
                _fail(f"{current_path}.weights", "must sum to 1")

        expected = _object(scenario["expected"], {"assignments", "events"}, path=f"{path}.expected")
        assigned_factors: set[str] = set()
        assigned_lineages: set[str] = set()
        for assignment_index, assignment in enumerate(_array(expected["assignments"], f"{path}.expected.assignments", minimum=1)):
            assignment_path = f"{path}.expected.assignments[{assignment_index}]"
            assignment = _object(
                assignment,
                {"factor_id", "lineage_id", "label", "relationship", "primary_predecessor_version_id"},
                path=assignment_path,
            )
            factor_id = _slug(assignment["factor_id"], f"{assignment_path}.factor_id")
            if factor_id not in factor_ids:
                _fail(f"{assignment_path}.factor_id", "does not name a current factor")
            lineage_id = _lineage_id(assignment["lineage_id"], f"{assignment_path}.lineage_id")
            _network_label(assignment["label"], f"{assignment_path}.label")
            relationship = _enum(
                assignment["relationship"],
                LINEAGE_RELATIONSHIPS,
                f"{assignment_path}.relationship",
            )
            predecessor = assignment["primary_predecessor_version_id"]
            if relationship == "birth":
                if predecessor is not None:
                    _fail(f"{assignment_path}.primary_predecessor_version_id", "must be null for a birth")
            elif predecessor not in previous_versions:
                _fail(f"{assignment_path}.primary_predecessor_version_id", "does not name a previous version")
            if factor_id in assigned_factors or lineage_id in assigned_lineages:
                _fail(assignment_path, "factor and resulting lineage must each be unique")
            assigned_factors.add(factor_id)
            assigned_lineages.add(lineage_id)
        if assigned_factors != factor_ids:
            _fail(f"{path}.expected.assignments", "must assign every current factor")

        for event_index, event in enumerate(_array(expected["events"], f"{path}.expected.events", minimum=1)):
            event_path = f"{path}.expected.events[{event_index}]"
            event = _object(event, {"type", "from_version_ids", "to_factor_ids"}, path=event_path)
            _enum(event["type"], LINEAGE_EVENT_TYPES, f"{event_path}.type")
            from_ids = _array(event["from_version_ids"], f"{event_path}.from_version_ids")
            to_ids = _array(event["to_factor_ids"], f"{event_path}.to_factor_ids")
            if not set(from_ids) <= previous_versions:
                _fail(f"{event_path}.from_version_ids", "contains an unknown previous version")
            if not set(to_ids) <= factor_ids:
                _fail(f"{event_path}.to_factor_ids", "contains an unknown current factor")
            _unique(from_ids, f"{event_path}.from_version_ids")
            _unique(to_ids, f"{event_path}.to_factor_ids")
    return document


VALIDATORS = {
    "media-observer-registry": validate_observer_registry,
    "media-observation-manifest": validate_observation_manifest,
    "media-weekly-model": validate_weekly_model,
    "media-current-snapshot": validate_current_snapshot,
    "media-model-order-diagnostics": validate_model_order_diagnostics,
    "media-lineage-scenarios": validate_lineage_scenarios,
}


def load_document(path: Path) -> Any:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def validate_document(document: Any) -> dict[str, Any]:
    if not isinstance(document, dict):
        _fail("document", "must be an object")
    document_type = document.get("document_type")
    if not isinstance(document_type, str) or document_type not in DOCUMENT_TYPES:
        _fail("document.document_type", f"must be one of: {', '.join(sorted(DOCUMENT_TYPES))}")
    return VALIDATORS[document_type](document)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate Observatory media-attention JSON contracts.")
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--registry", type=Path, help="Frozen observer registry for cross-reference checks")
    parser.add_argument("--weekly-model", type=Path, help="Weekly model for snapshot consistency checks")
    parser.add_argument("--observations", type=Path, action="append", default=[], help="Evidence manifest; repeat across week boundaries")
    args = parser.parse_args()
    registry = load_document(args.registry) if args.registry else None
    model = load_document(args.weekly_model) if args.weekly_model else None
    manifests = [load_document(path) for path in args.observations] if args.observations else None
    for path in args.files:
        document = validate_document(load_document(path))
        if document["document_type"] == "media-observation-manifest":
            validate_observation_manifest(document, registry)
        elif document["document_type"] == "media-weekly-model":
            validate_weekly_model(document, registry)
        elif document["document_type"] == "media-current-snapshot":
            validate_current_snapshot(document, registry, model, manifests)
        print(f"Valid {path}")


if __name__ == "__main__":
    main()
