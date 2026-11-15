#!/usr/bin/env python3
"""Conservative, title-first story-event clustering for private Stage 3 trials.

The module deliberately treats retrieval and judgment as separate operations:
candidate pairs are bounded by time, while a merge requires stronger lexical,
entity, URL, or exact-title evidence.  Scores are deterministic audit scores,
not calibrated probabilities.  Nothing in this module publishes a page,
schedules collection, or infers media-attention networks.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import datetime, time, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from media_validate import (
    load_document,
    validate_collection_batch,
    validate_observation_manifest,
    validate_observer_registry,
)


EASTERN = ZoneInfo("America/New_York")
UTC = timezone.utc
DEFAULT_MAX_GAP_HOURS = 96
MERGE_THRESHOLD = 0.95

WORD_RE = re.compile(r"[^\W_]+(?:[’'][^\W_]+)?", re.UNICODE)
UPDATE_PREFIX_RE = re.compile(
    r"^(?:breaking|exclusive|live|watch|video|podcast|analysis|opinion|"
    r"update(?:\s+\d+)?|corrected)\s*[:\-–—|]\s*",
    re.IGNORECASE,
)

# Function words and headline boilerplate are excluded only from comparison;
# original titles are retained unchanged in every output manifest.
STOPWORDS = frozenset(
    "a an and are as at be been being but by can could did do does for from had "
    "has have he her hers him his how i if in into is it its may might more most "
    "new no not of on or our ours over says she should so than that the their "
    "theirs them then there these they this those through to under up us was we "
    "were what when where which who why will with would you your after amid before "
    "during latest live news report reports reporting update updates video watch"
    .split()
)


@dataclass(frozen=True)
class TitleFeatures:
    normalized: str
    tokens: tuple[str, ...]
    content: frozenset[str]
    entities: frozenset[str]
    numbers: frozenset[str]


@dataclass(frozen=True)
class PairDecision:
    merge: bool
    score: float
    basis: str
    token_jaccard: float
    token_containment: float
    sequence_ratio: float
    shared_entities: tuple[str, ...]
    shared_numbers: tuple[str, ...]


@dataclass(frozen=True)
class Unit:
    id: str
    observer_id: str
    canonical_item_id: str
    item_ids: tuple[str, ...]
    title: str
    url: str
    attention_at: datetime
    features: TitleFeatures


def parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _fold(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = "".join(character for character in value if not unicodedata.combining(character))
    return value.casefold()


def _token_key(token: str) -> str:
    token = _fold(token).replace("’", "'").strip("'")
    if len(token) > 5 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def title_features(title: str) -> TitleFeatures:
    stripped = title.strip()
    while True:
        updated = UPDATE_PREFIX_RE.sub("", stripped, count=1)
        if updated == stripped:
            break
        stripped = updated.strip()

    raw_tokens = WORD_RE.findall(stripped)
    tokens = tuple(filter(None, (_token_key(token) for token in raw_tokens)))
    content = frozenset(token for token in tokens if token not in STOPWORDS and len(token) > 1)
    numbers = frozenset(token for token in tokens if any(character.isdigit() for character in token))

    entity_phrases: set[str] = set()
    current: list[str] = []
    for index, raw in enumerate(raw_tokens):
        cleaned = _token_key(raw)
        is_capitalized = bool(raw[:1].isupper()) and cleaned not in STOPWORDS
        is_acronym = len(raw) > 1 and raw.isupper()
        if is_capitalized or is_acronym:
            # A sentence-initial ordinary word is not an entity unless it joins
            # another capitalized token.
            current.append(cleaned)
            continue
        if len(current) >= 2:
            entity_phrases.add(" ".join(current))
        elif len(current) == 1 and (index > 1 or raw_tokens[0].isupper()):
            entity_phrases.add(current[0])
        current = []
    if len(current) >= 2:
        entity_phrases.add(" ".join(current))
    elif len(current) == 1 and len(raw_tokens) > 1:
        entity_phrases.add(current[0])

    normalized = " ".join(tokens)
    return TitleFeatures(normalized, tokens, content, frozenset(entity_phrases), numbers)


def _ratio(numerator: int, denominator: int) -> float:
    return 0.0 if denominator <= 0 else numerator / denominator


def _url_identity(left: str, right: str) -> bool:
    def normalized(value: str) -> str:
        try:
            parsed = urlsplit(value)
        except ValueError:
            return ""
        host = (parsed.hostname or "").casefold()
        path = re.sub(r"/+", "/", parsed.path or "/").rstrip("/") or "/"
        return urlunsplit(("https", host, path, parsed.query, ""))

    first, second = normalized(left), normalized(right)
    return bool(first and first == second)


def classify_pair(
    left_title: str,
    right_title: str,
    *,
    left_at: datetime,
    right_at: datetime,
    left_url: str = "",
    right_url: str = "",
    max_gap_hours: int = DEFAULT_MAX_GAP_HOURS,
) -> PairDecision:
    """Classify one candidate pair under precision-first deterministic rules."""
    left = title_features(left_title)
    right = title_features(right_title)
    return _classify_features(
        left,
        right,
        left_at=left_at,
        right_at=right_at,
        left_url=left_url,
        right_url=right_url,
        max_gap_hours=max_gap_hours,
    )


def _classify_features(
    left: TitleFeatures,
    right: TitleFeatures,
    *,
    left_at: datetime,
    right_at: datetime,
    left_url: str = "",
    right_url: str = "",
    max_gap_hours: int = DEFAULT_MAX_GAP_HOURS,
) -> PairDecision:
    """Classify a pair whose normalized title features are already cached."""
    shared = left.content & right.content
    union = left.content | right.content
    jaccard = _ratio(len(shared), len(union))
    containment = _ratio(len(shared), min(len(left.content), len(right.content)))
    sequence = SequenceMatcher(None, left.normalized, right.normalized, autojunk=False).ratio()
    shared_entities = tuple(sorted(left.entities & right.entities))
    shared_numbers = tuple(sorted(left.numbers & right.numbers))
    conflicting_numbers = bool(left.numbers and right.numbers and left.numbers != right.numbers)
    gap_hours = abs((left_at - right_at).total_seconds()) / 3600
    in_window = gap_hours <= max_gap_hours

    basis = "insufficient-evidence"
    score = 0.0
    if (
        in_window
        and left.normalized == right.normalized
        and len(left.content) >= 3
        and not conflicting_numbers
    ):
        basis, score = "normalized-exact-title", 0.99
    else:
        # The continuous score orders review candidates.  It does not by
        # itself authorize a merge; one of the explicit evidence rules below
        # must also pass.
        score = min(
            0.949,
            0.42 * containment
            + 0.28 * jaccard
            + 0.20 * sequence
            + (0.06 if shared_entities else 0.0)
            + (0.04 if shared_numbers else 0.0),
        )
        if conflicting_numbers:
            score = max(0.0, score - 0.20)
        if not in_window:
            score = max(0.0, score - 0.25)

        enough_words = len(shared) >= 4 and min(len(left.content), len(right.content)) >= 4
        same_url_support = _url_identity(left_url, right_url) and containment >= 0.75 and len(shared) >= 3
        if in_window and not conflicting_numbers and same_url_support:
            basis, score = "canonical-url-with-title-support", 0.985
        if (
            gap_hours <= 24
            and not conflicting_numbers
            and enough_words
            and containment >= 0.75
            and jaccard >= 0.30
            and sequence >= 0.50
        ):
            basis, score = "calibrated-high-containment", 0.97
        elif (
            in_window
            and not conflicting_numbers
            and len(shared) >= 5
            and bool(shared_entities)
            and containment >= 0.76
            and jaccard >= 0.58
            and sequence >= 0.84
        ):
            basis, score = "entity-supported-paraphrase", 0.955

    return PairDecision(
        merge=score >= MERGE_THRESHOLD,
        score=round(score, 6),
        basis=basis,
        token_jaccard=round(jaccard, 6),
        token_containment=round(containment, 6),
        sequence_ratio=round(sequence, 6),
        shared_entities=shared_entities,
        shared_numbers=shared_numbers,
    )


def evaluate_hlgd(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Evaluate against the human-annotated HLGD pair format.

    The upstream release uses label 1 for same-event and 0 for different-event.
    Dates are treated as noon UTC because HLGD supplies calendar dates only.
    """
    true_positive = false_positive = true_negative = false_negative = 0
    by_timeline: dict[str, dict[str, int]] = {}
    for index, row in enumerate(rows):
        try:
            left_at = datetime.combine(datetime.fromisoformat(row["date_a"]).date(), time(12), UTC)
            right_at = datetime.combine(datetime.fromisoformat(row["date_b"]).date(), time(12), UTC)
            actual = int(row["label"]) == 1
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"invalid HLGD row {index}") from error
        decision = classify_pair(
            str(row["headline_a"]),
            str(row["headline_b"]),
            left_at=left_at,
            right_at=right_at,
            left_url=str(row.get("url_a", "")),
            right_url=str(row.get("url_b", "")),
        )
        bucket = by_timeline.setdefault(
            str(row.get("timeline_id", "unknown")),
            {"true_positive": 0, "false_positive": 0, "true_negative": 0, "false_negative": 0},
        )
        if decision.merge and actual:
            true_positive += 1
            bucket["true_positive"] += 1
        elif decision.merge and not actual:
            false_positive += 1
            bucket["false_positive"] += 1
        elif not decision.merge and actual:
            false_negative += 1
            bucket["false_negative"] += 1
        else:
            true_negative += 1
            bucket["true_negative"] += 1

    precision = _ratio(true_positive, true_positive + false_positive)
    recall = _ratio(true_positive, true_positive + false_negative)
    f1 = _ratio(2 * precision * recall, precision + recall)
    return {
        "row_count": true_positive + false_positive + true_negative + false_negative,
        "proposed_merge_count": true_positive + false_positive,
        "true_positive": true_positive,
        "false_positive": false_positive,
        "true_negative": true_negative,
        "false_negative": false_negative,
        "precision": round(precision, 6),
        "recall": round(recall, 6),
        "f1": round(f1, 6),
        "passes_precision_gate": precision >= 0.95 and true_positive + false_positive >= 100,
        "gate": {"minimum_precision": 0.95, "minimum_proposed_merges": 100},
        "by_timeline": by_timeline,
    }


def build_units(batch: dict[str, Any]) -> tuple[list[Unit], dict[str, dict[str, Any]]]:
    items = {item["id"]: item for item in batch["items"]}
    units: list[Unit] = []
    for group in batch["mirror_groups"]:
        canonical = items[group["canonical_item_id"]]
        group_items = [items[item_id] for item_id in group["item_ids"]]
        units.append(
            Unit(
                id=group["id"],
                observer_id=canonical["observer_id"],
                canonical_item_id=canonical["id"],
                item_ids=tuple(sorted(group["item_ids"])),
                title=canonical["normalized_title"],
                url=canonical["url"],
                attention_at=min(parse_utc(item["attention_at"]) for item in group_items),
                features=title_features(canonical["normalized_title"]),
            )
        )
    units.sort(key=lambda unit: (unit.attention_at, unit.id))
    return units, items


def candidate_decisions(
    units: list[Unit],
    *,
    max_gap_hours: int = DEFAULT_MAX_GAP_HOURS,
) -> dict[tuple[str, str], PairDecision]:
    decisions: dict[tuple[str, str], PairDecision] = {}
    for left_index, left in enumerate(units):
        for right in units[left_index + 1 :]:
            gap = (right.attention_at - left.attention_at).total_seconds() / 3600
            if gap > max_gap_hours:
                break
            # Every admitted merge rule requires at least three shared
            # informative tokens.  Apply that lossless block before the much
            # more expensive sequence comparison.
            if len(left.features.content & right.features.content) < 3:
                continue
            decision = _classify_features(
                left.features,
                right.features,
                left_at=left.attention_at,
                right_at=right.attention_at,
                left_url=left.url,
                right_url=right.url,
                max_gap_hours=max_gap_hours,
            )
            if left.observer_id == right.observer_id and decision.merge:
                gap_hours = abs((left.attention_at - right.attention_at).total_seconds()) / 3600
                exact_within_day = decision.basis == "normalized-exact-title" and gap_hours <= 24
                near_duplicate = decision.token_jaccard >= 0.75 and decision.sequence_ratio >= 0.70
                if not (exact_within_day or near_duplicate):
                    decision = PairDecision(
                        merge=False,
                        score=min(decision.score, 0.949),
                        basis="intra-observer-near-duplicate-gate",
                        token_jaccard=decision.token_jaccard,
                        token_containment=decision.token_containment,
                        sequence_ratio=decision.sequence_ratio,
                        shared_entities=decision.shared_entities,
                        shared_numbers=decision.shared_numbers,
                    )
            if decision.score >= 0.45:
                decisions[tuple(sorted((left.id, right.id)))] = decision
    return decisions


def _decision_for(
    left: Unit,
    right: Unit,
    decisions: dict[tuple[str, str], PairDecision],
) -> PairDecision | None:
    key = tuple(sorted((left.id, right.id)))
    return decisions.get(key)


def cluster_units(
    units: list[Unit],
    decisions: dict[tuple[str, str], PairDecision],
) -> list[list[Unit]]:
    """Agglomerate only when every cross-cluster pair independently merges.

    This complete-link rule rejects transitive bridges.  It intentionally
    sacrifices recall because one false merge contaminates the observer-by-
    story matrix used by every later stage.
    """
    clusters: dict[str, list[Unit]] = {unit.id: [unit] for unit in units}
    owner = {unit.id: unit.id for unit in units}
    unit_index = {unit.id: unit for unit in units}
    edges = sorted(
        ((decision.score, left_id, right_id) for (left_id, right_id), decision in decisions.items() if decision.merge),
        key=lambda value: (-value[0], value[1], value[2]),
    )
    for _, left_id, right_id in edges:
        left_owner, right_owner = owner[left_id], owner[right_id]
        if left_owner == right_owner:
            continue
        left_cluster, right_cluster = clusters[left_owner], clusters[right_owner]
        compatible = True
        for left in left_cluster:
            for right in right_cluster:
                decision = _decision_for(left, right, decisions)
                if decision is None or not decision.merge:
                    compatible = False
                    break
            if not compatible:
                break
        if not compatible:
            continue
        combined = sorted(left_cluster + right_cluster, key=lambda unit: (unit.attention_at, unit.id))
        new_owner = min(left_owner, right_owner)
        old_owner = max(left_owner, right_owner)
        clusters[new_owner] = combined
        del clusters[old_owner]
        for unit in combined:
            owner[unit.id] = new_owner

    result = list(clusters.values())
    result.sort(key=lambda cluster: (cluster[0].attention_at, cluster[0].id))
    # Keep this assertion close to the algorithm: no unit may vanish or repeat.
    assert sorted(unit.id for cluster in result for unit in cluster) == sorted(unit_index)
    return result


def _story_id(cluster: list[Unit]) -> str:
    anchor = min(cluster, key=lambda unit: (unit.attention_at, unit.canonical_item_id))
    digest = hashlib.sha256(anchor.canonical_item_id.encode("utf-8")).hexdigest()[:24]
    return f"story-{digest}"


def _canonical_unit(
    cluster: list[Unit],
    decisions: dict[tuple[str, str], PairDecision],
) -> Unit:
    def rank(candidate: Unit) -> tuple[float, int, datetime, str]:
        similarity = 0.0
        for other in cluster:
            if other.id == candidate.id:
                continue
            decision = _decision_for(candidate, other, decisions)
            similarity += 0.0 if decision is None else decision.score
        return (-similarity, -len(candidate.features.content), candidate.attention_at, candidate.id)

    return min(cluster, key=rank)


def _unit_confidence(
    unit: Unit,
    cluster: list[Unit],
    decisions: dict[tuple[str, str], PairDecision],
) -> float:
    if len(cluster) > 1:
        scores = [
            decision.score
            for other in cluster
            if other.id != unit.id
            if (decision := _decision_for(unit, other, decisions)) is not None
        ]
        return round(min(scores), 6) if scores else MERGE_THRESHOLD
    alternatives = [
        decision.score
        for (left_id, right_id), decision in decisions.items()
        if unit.id in {left_id, right_id}
    ]
    strongest = max(alternatives, default=0.0)
    # Singleton confidence reports separation margin, not a probability.
    return round(max(0.5, min(0.99, 1.0 - strongest / 2)), 6)


def build_manifests(
    batch: dict[str, Any],
    clusters: list[list[Unit]],
    decisions: dict[tuple[str, str], PairDecision],
) -> list[dict[str, Any]]:
    _, items = build_units(batch)
    generated_at = batch["completed_at"]
    by_week: dict[str, list[dict[str, Any]]] = {}
    story_metadata: dict[str, dict[str, Any]] = {}

    for cluster in clusters:
        story_id = _story_id(cluster)
        canonical = _canonical_unit(cluster, decisions)
        story_metadata[story_id] = {
            "canonical_title": canonical.title[:260].rstrip(),
            "canonical_url": canonical.url,
        }
        for unit in cluster:
            confidence = _unit_confidence(unit, cluster, decisions)
            for item_id in unit.item_ids:
                item = items[item_id]
                attention = parse_utc(item["attention_at"])
                local_week = attention.astimezone(EASTERN).date().isocalendar()
                week = f"{local_week.year:04d}-W{local_week.week:02d}"
                by_week.setdefault(week, []).append(
                    {
                        "id": f"observation-{item['id'].removeprefix('item-')}",
                        "observer_id": item["observer_id"],
                        "channel_id": item["channel_id"],
                        "medium": item["medium"],
                        "normalized_title": item["normalized_title"],
                        "url": item["url"],
                        "attention_at": item["attention_at"],
                        "timestamp_basis": item["timestamp_basis"],
                        "story_id": story_id,
                        "cluster_confidence": confidence,
                    }
                )

    manifests: list[dict[str, Any]] = []
    for week, observations in sorted(by_week.items()):
        observations.sort(key=lambda item: (item["attention_at"], item["id"]))
        stories: list[dict[str, Any]] = []
        for story_id in sorted({item["story_id"] for item in observations}):
            members = [item for item in observations if item["story_id"] == story_id]
            metadata = story_metadata[story_id]
            story = {
                "id": story_id,
                "canonical_title": metadata["canonical_title"],
                "canonical_url": metadata["canonical_url"],
                "first_attention_at": min(item["attention_at"] for item in members),
                "last_attention_at": max(item["attention_at"] for item in members),
                "observation_ids": [item["id"] for item in members],
            }
            stories.append(story)
        manifests.append(
            {
                "document_type": "media-observation-manifest",
                "schema_version": 1,
                "week": week,
                "generated_at": generated_at,
                "observations": observations,
                "stories": stories,
            }
        )
    return manifests


def audit_document(
    batch: dict[str, Any],
    units: list[Unit],
    clusters: list[list[Unit]],
    decisions: dict[tuple[str, str], PairDecision],
    *,
    max_gap_hours: int = DEFAULT_MAX_GAP_HOURS,
) -> dict[str, Any]:
    proposed = [
        (left_id, right_id, decision)
        for (left_id, right_id), decision in decisions.items()
        if decision.merge
    ]
    review = sorted(
        (
            {
                "left_unit_id": left_id,
                "right_unit_id": right_id,
                "score": decision.score,
                "basis": decision.basis,
                "token_jaccard": decision.token_jaccard,
                "token_containment": decision.token_containment,
                "sequence_ratio": decision.sequence_ratio,
                "shared_entities": list(decision.shared_entities),
                "shared_numbers": list(decision.shared_numbers),
            }
            for (left_id, right_id), decision in decisions.items()
        ),
        key=lambda row: (-row["score"], row["left_unit_id"], row["right_unit_id"]),
    )
    stories: list[dict[str, Any]] = []
    for cluster in clusters:
        story_id = _story_id(cluster)
        canonical = _canonical_unit(cluster, decisions)
        ranked = [canonical] + [
            unit
            for unit in sorted(cluster, key=lambda value: (value.attention_at, value.id))
            if unit.id != canonical.id
        ]
        representative_links: list[dict[str, str]] = []
        represented_observers: set[str] = set()
        for unit in ranked:
            if unit.observer_id in represented_observers:
                continue
            represented_observers.add(unit.observer_id)
            representative_links.append(
                {
                    "unit_id": unit.id,
                    "observer_id": unit.observer_id,
                    "title": unit.title,
                    "url": unit.url,
                }
            )
            if len(representative_links) == 3:
                break
        stories.append(
            {
                "story_id": story_id,
                "canonical_title": canonical.title[:260].rstrip(),
                "canonical_url": canonical.url,
                "unit_ids": [unit.id for unit in cluster],
                "representative_links": representative_links,
            }
        )
    return {
        "document_type": "media-story-cluster-audit",
        "schema_version": 1,
        "run_id": batch["run_id"],
        "generated_at": batch["completed_at"],
        "parameters": {
            "algorithm": "precision-first-complete-link-title-v1",
            "max_gap_hours": max_gap_hours,
            "merge_threshold": MERGE_THRESHOLD,
            "score_interpretation": "deterministic-audit-score-not-probability",
        },
        "counts": {
            "item_count": len(batch["items"]),
            "mirror_unit_count": len(units),
            "candidate_pair_count": len(decisions),
            "proposed_merge_pair_count": len(proposed),
            "story_count": len(clusters),
            "merged_story_count": sum(len(cluster) > 1 for cluster in clusters),
        },
        "units": [
            {
                "id": unit.id,
                "observer_id": unit.observer_id,
                "canonical_item_id": unit.canonical_item_id,
                "title": unit.title,
                "url": unit.url,
                "attention_at": iso_utc(unit.attention_at),
            }
            for unit in units
        ],
        "stories": stories,
        "review_pairs": review,
    }


def validate_cluster_audit(audit: Any, batch: dict[str, Any]) -> dict[str, Any]:
    """Validate the complete private audit contract against its Stage 2 batch."""
    if not isinstance(audit, dict):
        raise ValueError("cluster audit must be an object")
    expected_top = {
        "document_type",
        "schema_version",
        "run_id",
        "generated_at",
        "parameters",
        "counts",
        "units",
        "stories",
        "review_pairs",
    }
    if set(audit) != expected_top:
        raise ValueError("cluster audit has missing or unknown top-level fields")
    if audit["document_type"] != "media-story-cluster-audit" or audit["schema_version"] != 1:
        raise ValueError("cluster audit header is invalid")
    if audit["run_id"] != batch["run_id"] or audit["generated_at"] != batch["completed_at"]:
        raise ValueError("cluster audit does not identify its source batch")
    parameters = audit["parameters"]
    if set(parameters) != {"algorithm", "max_gap_hours", "merge_threshold", "score_interpretation"}:
        raise ValueError("cluster audit parameters have missing or unknown fields")
    if parameters["max_gap_hours"] <= 0 or parameters["merge_threshold"] != MERGE_THRESHOLD:
        raise ValueError("cluster audit parameters are invalid")

    batch_items = {item["id"]: item for item in batch["items"]}
    batch_groups = {group["id"]: group for group in batch["mirror_groups"]}
    unit_index: dict[str, dict[str, Any]] = {}
    for unit_row in audit["units"]:
        if set(unit_row) != {"id", "observer_id", "canonical_item_id", "title", "url", "attention_at"}:
            raise ValueError("cluster audit unit has missing or unknown fields")
        unit_id = unit_row["id"]
        if unit_id in unit_index or unit_id not in batch_groups:
            raise ValueError("cluster audit unit is duplicate or unknown")
        group = batch_groups[unit_id]
        canonical = batch_items[group["canonical_item_id"]]
        if (
            unit_row["observer_id"] != group["observer_id"]
            or unit_row["canonical_item_id"] != canonical["id"]
            or unit_row["title"] != canonical["normalized_title"]
            or unit_row["url"] != canonical["url"]
        ):
            raise ValueError("cluster audit unit does not match its mirror group")
        unit_index[unit_id] = unit_row
    if set(unit_index) != set(batch_groups):
        raise ValueError("cluster audit units do not cover the collection mirror groups")

    story_ids: set[str] = set()
    assigned_units: set[str] = set()
    merged_story_count = 0
    for story in audit["stories"]:
        if set(story) != {
            "story_id",
            "canonical_title",
            "canonical_url",
            "unit_ids",
            "representative_links",
        }:
            raise ValueError("cluster audit story has missing or unknown fields")
        if story["story_id"] in story_ids:
            raise ValueError("cluster audit story id is duplicated")
        story_ids.add(story["story_id"])
        if not story["unit_ids"] or len(story["unit_ids"]) != len(set(story["unit_ids"])):
            raise ValueError("cluster audit story unit list is empty or duplicated")
        for unit_id in story["unit_ids"]:
            if unit_id not in unit_index or unit_id in assigned_units:
                raise ValueError("cluster audit story unit is unknown or assigned twice")
            assigned_units.add(unit_id)
        merged_story_count += len(story["unit_ids"]) > 1
        links = story["representative_links"]
        if not 1 <= len(links) <= 3:
            raise ValueError("cluster audit story must have one to three representative links")
        represented: set[str] = set()
        for link in links:
            if set(link) != {"unit_id", "observer_id", "title", "url"}:
                raise ValueError("cluster audit representative link has missing or unknown fields")
            if link["unit_id"] not in story["unit_ids"]:
                raise ValueError("cluster audit representative link is outside its story")
            unit_row = unit_index[link["unit_id"]]
            if any(link[field] != unit_row[field] for field in ("observer_id", "title", "url")):
                raise ValueError("cluster audit representative link does not match its unit")
            if link["observer_id"] in represented:
                raise ValueError("cluster audit representative links repeat an observer")
            represented.add(link["observer_id"])
    if assigned_units != set(unit_index):
        raise ValueError("cluster audit stories do not partition the comparison units")

    pair_keys: set[tuple[str, str]] = set()
    proposed_count = 0
    pair_fields = {
        "left_unit_id",
        "right_unit_id",
        "score",
        "basis",
        "token_jaccard",
        "token_containment",
        "sequence_ratio",
        "shared_entities",
        "shared_numbers",
    }
    for pair in audit["review_pairs"]:
        if set(pair) != pair_fields:
            raise ValueError("cluster audit review pair has missing or unknown fields")
        left_id, right_id = pair["left_unit_id"], pair["right_unit_id"]
        if left_id not in unit_index or right_id not in unit_index or left_id == right_id:
            raise ValueError("cluster audit review pair names invalid units")
        key = tuple(sorted((left_id, right_id)))
        if key in pair_keys:
            raise ValueError("cluster audit review pair is duplicated")
        pair_keys.add(key)
        for field in ("score", "token_jaccard", "token_containment", "sequence_ratio"):
            value = pair[field]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
                raise ValueError("cluster audit review pair has an invalid score")
        if not isinstance(pair["basis"], str) or not pair["basis"]:
            raise ValueError("cluster audit review pair basis is invalid")
        proposed_count += pair["score"] >= MERGE_THRESHOLD

    counts = audit["counts"]
    if set(counts) != {
        "item_count",
        "mirror_unit_count",
        "candidate_pair_count",
        "proposed_merge_pair_count",
        "story_count",
        "merged_story_count",
    }:
        raise ValueError("cluster audit counts have missing or unknown fields")
    expected_counts = {
        "item_count": len(batch["items"]),
        "mirror_unit_count": len(unit_index),
        "candidate_pair_count": len(pair_keys),
        "proposed_merge_pair_count": proposed_count,
        "story_count": len(story_ids),
        "merged_story_count": merged_story_count,
    }
    if counts != expected_counts:
        raise ValueError("cluster audit counts do not match its contents")
    return audit


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def write_private_run(
    output_dir: Path,
    batch: dict[str, Any],
    manifests: list[dict[str, Any]],
    audit: dict[str, Any],
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    run_name = batch["run_id"].replace("collect-", "cluster-", 1)
    destination = output_dir / run_name
    if destination.exists():
        raise FileExistsError(f"refusing to replace existing Stage 3 run: {destination}")
    temporary = Path(tempfile.mkdtemp(prefix=f".{run_name}.", dir=output_dir))
    try:
        for manifest in manifests:
            _write_json(temporary / "observations" / f"{manifest['week']}.json", manifest)
        _write_json(temporary / "audit.json", audit)
        temporary.replace(destination)
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return destination


def run_collection(args: argparse.Namespace) -> int:
    registry = load_document(args.registry)
    batch = load_document(args.input)
    validate_observer_registry(registry)
    validate_collection_batch(batch, registry)
    units, _ = build_units(batch)
    decisions = candidate_decisions(units, max_gap_hours=args.max_gap_hours)
    clusters = cluster_units(units, decisions)
    manifests = build_manifests(batch, clusters, decisions)
    for manifest in manifests:
        validate_observation_manifest(manifest, registry)
    audit = audit_document(batch, units, clusters, decisions, max_gap_hours=args.max_gap_hours)
    validate_cluster_audit(audit, batch)
    destination = write_private_run(args.output_dir, batch, manifests, audit)
    print(
        json.dumps(
            {
                "output": str(destination),
                "weeks": len(manifests),
                **audit["counts"],
            },
            indent=2,
        )
    )
    return 0


def run_benchmark(args: argparse.Namespace) -> int:
    with args.benchmark.open(encoding="utf-8") as handle:
        rows = json.load(handle)
    if not isinstance(rows, list):
        raise ValueError("HLGD benchmark must be a JSON array")
    result = evaluate_hlgd(rows)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passes_precision_gate"] or not args.require_gate else 1


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Run private Stage 3 story clustering or the external HLGD precision audit."
    )
    mode = result.add_mutually_exclusive_group(required=True)
    mode.add_argument("--input", type=Path, help="validated Stage 2 collection batch")
    mode.add_argument("--benchmark", type=Path, help="HLGD classification JSON array")
    result.add_argument("--registry", type=Path, default=Path("config/media-observers.json"))
    result.add_argument("--output-dir", type=Path, default=Path(".work/media-networks-stage3"))
    result.add_argument("--max-gap-hours", type=int, default=DEFAULT_MAX_GAP_HOURS)
    result.add_argument(
        "--require-gate",
        action="store_true",
        help="exit nonzero when a benchmark has fewer than 100 proposed merges or precision below 0.95",
    )
    return result


def main() -> int:
    args = parser().parse_args()
    if args.max_gap_hours <= 0:
        raise SystemExit("--max-gap-hours must be positive")
    if args.input:
        return run_collection(args)
    return run_benchmark(args)


if __name__ == "__main__":
    raise SystemExit(main())
