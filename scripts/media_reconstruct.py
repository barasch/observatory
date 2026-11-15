#!/usr/bin/env python3
"""Offline Stage 4 weekly inference. No collection, schedule, or publication path."""
from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from scipy.optimize import linear_sum_assignment

from media_validate import (load_document, validate_model_order_diagnostics,
                            validate_observation_manifest, validate_observer_registry, validate_weekly_model)

EASTERN = ZoneInfo("America/New_York")
MEDIA = {"website", "newsletter", "podcast", "youtube", "bluesky", "x", "tiktok", "instagram"}
EPS = 1e-10
LINEAGE_AMBIGUITY_MARGIN = 0.02  # provisional; requires empirical calibration


def bounds(week: str) -> tuple[datetime, datetime, datetime]:
    year, number = map(int, week.split("-W"))
    monday = date.fromisocalendar(year, number, 1)
    end = datetime.combine(monday, time(), EASTERN)
    start = datetime.combine(monday - timedelta(weeks=12), time(), EASTERN)
    effective = datetime.combine(monday, time(4), EASTERN)
    return start, end, effective


def stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _cosine(left: np.ndarray, right: np.ndarray) -> float:
    denom = float(np.linalg.norm(left) * np.linalg.norm(right))
    return float(np.dot(left, right) / denom) if denom > EPS else 0.0


def matrix(manifests: list[dict], week: str, *, daily_cap: int = 12):
    """One observer/story/day, daily activity cap, four-week calendar decay.

    An observer/story cell is the strongest (most recent) weekly attendance,
    not a sum of mirror channels or repeated items. A daily cap is applied to
    distinct events before the twelve-week collapse.
    """
    if daily_cap < 1:
        raise ValueError("daily_cap must be positive")
    start, end, _ = bounds(week)
    expected = {(start.date() + timedelta(weeks=i)).isocalendar() for i in range(12)}
    expected_weeks = {f"{value.year:04d}-W{value.week:02d}" for value in expected}
    supplied = [manifest["week"] for manifest in manifests]
    if len(supplied) != 12 or set(supplied) != expected_weeks:
        raise ValueError("require exactly twelve distinct preceding local ISO weeks")
    daily: dict[tuple[str, str, date], tuple[datetime, str]] = {}
    observation_ids: set[str] = set()
    for manifest in manifests:
        for row in manifest["observations"]:
            if row["id"] in observation_ids:
                raise ValueError("observation repeated across weekly manifests")
            observation_ids.add(row["id"])
            at = datetime.fromisoformat(row["attention_at"].replace("Z", "+00:00")).astimezone(EASTERN)
            if not start <= at < end:
                raise ValueError("observation outside twelve completed local weeks")
            key = (row["observer_id"], row["story_id"], at.date())
            if key not in daily or at > daily[key][0]:
                daily[key] = (at, row["id"])
    by_day: dict[tuple[str, date], list[tuple[str, datetime, str]]] = defaultdict(list)
    for (observer, story, day), (at, observation_id) in daily.items():
        by_day[(observer, day)].append((story, at, observation_id))
    capped = [
        (observer, story, at, observation_id)
        for (observer, _), rows in by_day.items()
        for story, at, observation_id in sorted(rows, key=lambda row: (-row[1].timestamp(), row[0]))[:daily_cap]
    ]
    observers = sorted({observer for observer, _, _, _ in capped})
    stories = sorted({story for _, story, _, _ in capped})
    if not observers or not stories:
        raise ValueError("no usable observer-story evidence")
    oi = {observer: i for i, observer in enumerate(observers)}
    si = {story: i for i, story in enumerate(stories)}
    raw = np.zeros((len(observers), len(stories)), dtype=float)
    for observer, story, at, _ in capped:
        age_weeks = (end.date() - at.date()).days / 7
        raw[oi[observer], si[story]] = max(raw[oi[observer], si[story]], 2 ** (-age_weeks / 4))
    frequency = np.count_nonzero(raw, axis=0)
    idf = 1 + np.log((len(observers) + 1) / (frequency + 1))
    weighted = raw * idf
    preceding = end.date() - timedelta(weeks=1)
    active = sorted({observer for observer, _, at, _ in capped if at.date() >= preceding})
    return weighted, observers, stories, active, len(observation_ids), len(capped)


def fit(values: np.ndarray, factors: int, seed: int, *, mask: np.ndarray | None = None, iterations: int = 400):
    """Non-negative matrix factorization with a masked squared-error objective."""
    if factors < 1 or factors > min(values.shape):
        raise ValueError("factor count exceeds usable matrix dimensions")
    rng = np.random.default_rng(seed)
    weights = np.ones_like(values) if mask is None else mask.astype(float)
    left = rng.uniform(0.1, 1, size=(values.shape[0], factors))
    right = rng.uniform(0.1, 1, size=(factors, values.shape[1]))
    observed = weights * values
    for _ in range(iterations):
        right *= (left.T @ observed + EPS) / (left.T @ (weights * (left @ right)) + EPS)
        left *= (observed @ right.T + EPS) / ((weights * (left @ right)) @ right.T + EPS)
        # A zero row or factor is an explicit quality failure, not a reason to
        # invent uniform membership.
        if np.any(left.sum(axis=1) < EPS) or np.any(left.sum(axis=0) < EPS):
            raise ValueError("empty observer or factor in fitted matrix")
    return left, right


def affinities(left: np.ndarray) -> np.ndarray:
    totals = left.sum(axis=1, keepdims=True)
    if np.any(totals < EPS):
        raise ValueError("observer with no factor affinity")
    return left / totals


def align(reference: np.ndarray, other: np.ndarray) -> float:
    similarities = np.array([
        [_cosine(reference[:, i], other[:, j]) for j in range(other.shape[1])]
        for i in range(reference.shape[1])
    ])
    rows, cols = linear_sum_assignment(-similarities)
    return float(np.mean(similarities[rows, cols]))


def diagnostics(values: np.ndarray, observers: list[str], families: dict[str, str],
                left: np.ndarray, right: np.ndarray, seed: int, runs: int = 5) -> dict:
    rng = np.random.default_rng(seed + 991)
    mask = rng.random(values.shape) >= 0.2
    if not np.any(~mask):
        raise ValueError("held-out mask is empty")
    trained_left, trained_right = fit(values, left.shape[1], seed + 1, mask=mask)
    error = float(np.sqrt(np.mean((values[~mask] - (trained_left @ trained_right)[~mask]) ** 2)))
    reference = affinities(left)
    stability = []
    for index in range(runs):
        sampled = rng.choice(len(observers), len(observers), replace=True)
        if len(set(sampled)) < left.shape[1]:
            continue
        boot_left, _ = fit(values[sampled], left.shape[1], seed + 100 + index)
        stability.append(align(reference[sampled], affinities(boot_left)))
    if not stability:
        raise ValueError("no usable bootstrap draws")
    # Refit on each leave-one-out panel, then compare matched affinities for
    # observers common to both panels. Never label instability as a fact about
    # an individual observer or family.
    def sensitivity(groups: list[list[int]]) -> float:
        differences = []
        for group_index, removed in enumerate(groups):
            kept = [index for index in range(len(observers)) if index not in removed]
            if len(kept) < left.shape[1]:
                continue
            reduced_left, _ = fit(values[kept], left.shape[1], seed + 200 + group_index)
            differences.append(1 - align(reference[kept], affinities(reduced_left)))
        return max(differences, default=0.0)
    by_family: dict[str, list[int]] = defaultdict(list)
    for index, observer in enumerate(observers):
        by_family[families[observer]].append(index)
    profile_pairs = [(i, j, _cosine(right[i], right[j]))
                     for i in range(right.shape[0]) for j in range(i + 1, right.shape[0])]
    return {
        "held_out_reconstruction_error": error,
        "held_out_fraction": float(np.mean(~mask)),
        "bootstrap_stability": float(np.mean(stability)),
        "bootstrap_runs": len(stability),
        "observer_sensitivity": sensitivity([[i] for i in range(len(observers))]),
        "brand_family_sensitivity": sensitivity(list(by_family.values())),
        "profile_pairs": profile_pairs,
    }


def label(number: int) -> str:
    value = ""
    while number:
        number, digit = divmod(number - 1, 26)
        value = chr(ord("A") + digit) + value
    return "Network " + value


def order_decision(candidates: list[dict], cutoffs: list[str], *, current_k: int | None = None,
                   review_type: str = "prelaunch-exhaustive", generated_at: str | None = None) -> dict:
    """Apply the constrained one-standard-error rule to measured diagnostics.

    Absolute quality gates are inputs established by a separate empirical
    protocol; this function never invents thresholds or fabricates metrics.
    """
    if not cutoffs or len(set(cutoffs)) != len(cutoffs):
        raise ValueError("weekly cutoffs must be distinct and nonempty")
    cutoffs = sorted(cutoffs)
    rows = {(row["weekly_cutoff"], row["k"]): row for row in candidates}
    ks = sorted({row["k"] for row in candidates})
    if set(rows) != {(week, k) for week in cutoffs for k in ks} or len(rows) != len(candidates):
        raise ValueError("every cutoff must have exactly one result for every k")
    winners: dict[str, int] = {}
    for cutoff in cutoffs:
        results = [rows[(cutoff, k)] for k in ks]
        best = min(results, key=lambda row: (row["held_out_reconstruction_error"], row["k"]))
        ceiling = best["held_out_reconstruction_error"] + best["held_out_standard_error"]
        eligible = [row["k"] for row in results if row["passes_quality_gates"]
                    and row["held_out_reconstruction_error"] <= ceiling]
        if not eligible:
            raise ValueError(f"no quality-gated model within one standard error at {cutoff}")
        winners[cutoff] = min(eligible)
    latest = cutoffs[-1]
    best_k = min(ks, key=lambda k: (rows[(latest, k)]["held_out_reconstruction_error"], k))
    ceiling = (rows[(latest, best_k)]["held_out_reconstruction_error"]
               + rows[(latest, best_k)]["held_out_standard_error"])
    viable = [k for k in ks if all(rows[(week, k)]["passes_quality_gates"] for week in cutoffs)
              and rows[(latest, k)]["held_out_reconstruction_error"] <= ceiling]
    if not viable:
        raise ValueError("no quality-gated model across all cutoffs within one standard error")
    selected = min(viable)
    boundary = best_k in {ks[0], ks[-1]}
    confirmed = len(cutoffs) >= 2 and len(set(winners.values())) == 1
    authorized = not boundary and (current_k is None or selected == current_k or confirmed)
    if current_k is None:
        authorized = not boundary and confirmed
    document = {
        "document_type": "media-model-order-diagnostics", "schema_version": 1,
        "evaluation_id": "order-" + latest.casefold(),
        "generated_at": generated_at or stamp(datetime.now(timezone.utc)),
        "review_type": review_type, "current_k": current_k,
        "weekly_cutoffs": cutoffs, "candidates": candidates,
        "decision": {"best_k": best_k, "selected_k": selected,
                     "winner_by_cutoff": winners, "change_authorized": authorized,
                     "expanded_search_required": boundary,
                     "rationale": "Smallest quality-gated model within one standard error at each cutoff; "
                                  "a boundary winner requires expansion and a change requires adjacent confirmation."},
    }
    validate_model_order_diagnostics(document)
    return document


def lineage(current: np.ndarray, observers: list[str], week: str, previous: dict | None,
            threshold: float = 0.65, reserved_ids: set[str] | None = None):
    count = current.shape[1]
    result: dict[int, dict] = {}
    events: list[dict] = []
    prior_networks = [] if previous is None else previous["networks"]
    previous_weights = {} if previous is None else {
        row["observer_id"]: row["weights"] for row in previous["memberships"]
    }
    similarity = np.zeros((len(prior_networks), count))
    shared = np.zeros_like(similarity)
    for p, network in enumerate(prior_networks):
        previous_vector = np.array([previous_weights.get(observer, {}).get(network["lineage_id"], 0)
                                    for observer in observers])
        for c in range(count):
            similarity[p, c] = _cosine(previous_vector, current[:, c])
            shared[p, c] = float(np.minimum(previous_vector, current[:, c]).sum())
    # Only a strong, isolated one-to-one match inherits lineage. Components
    # with two parents and two children are ambiguous and must be reviewed.
    edges = {(p, c) for p in range(len(prior_networks)) for c in range(count)
             if similarity[p, c] >= threshold}
    used_p: set[int] = set()
    used_c: set[int] = set()
    components = []
    for p, c in sorted(edges):
        if p in used_p or c in used_c:
            continue
        ps, cs = {p}, {c}
        while True:
            expanded_ps = ps | {i for i, j in edges if j in cs}
            expanded_cs = cs | {j for i, j in edges if i in expanded_ps}
            if (expanded_ps, expanded_cs) == (ps, cs):
                break
            ps, cs = expanded_ps, expanded_cs
        if len(ps) > 1 and len(cs) > 1:
            raise ValueError("many-to-many lineage ambiguity requires human review")
        used_p |= ps
        used_c |= cs
        components.append((sorted(ps), sorted(cs)))
    if reserved_ids is not None and not {n["lineage_id"] for n in prior_networks} <= reserved_ids:
        raise ValueError("lineage ledger omits a current predecessor")
    used_numbers = [int(value.split("-")[1]) for value in
                    (reserved_ids if reserved_ids is not None else {n["lineage_id"] for n in prior_networks})]
    next_number = max(used_numbers, default=0) + 1

    def new_identity():
        nonlocal next_number
        if previous is not None and reserved_ids is None:
            raise ValueError("new lineage requires the complete reserved-lineage ledger")
        number = next_number
        next_number += 1
        return f"lineage-{number:04d}", label(number)

    def version(p):
        return prior_networks[p]["version_id"]

    def link(p, c):
        return {"from_version_id": version(p), "to_version_id": result[c]["version_id"],
                "cosine_similarity": float(similarity[p, c]),
                "shared_membership_mass": float(shared[p, c])}

    for ps, cs in components:
        kind = "split" if len(cs) > 1 else "merger" if len(ps) > 1 else "continuation"
        if kind == "split":
            ranked = sorted((similarity[ps[0], c] for c in cs), reverse=True)
        elif kind == "merger":
            ranked = sorted((similarity[p, cs[0]] for p in ps), reverse=True)
        else:
            ranked = []
        if len(ranked) > 1 and ranked[0] - ranked[1] < LINEAGE_AMBIGUITY_MARGIN:
            raise ValueError("lineage inheritor is ambiguous; human review required")
        parent = max(ps, key=lambda p: (similarity[p, cs[0]], -p)) if len(ps) > 1 else ps[0]
        child = max(cs, key=lambda c: (similarity[parent, c], -c))
        for c in cs:
            primary = parent
            if kind == "split" and c != child:
                identity, name = new_identity()
            else:
                identity, name = prior_networks[primary]["lineage_id"], prior_networks[primary]["label"]
            result[c] = {"lineage_id": identity, "label": name, "version_id": f"{identity}@{week}",
                         "continuity": {"relationship": kind, "primary_predecessor_version_id": version(primary),
                                        "cosine_similarity": float(similarity[primary, c])}}
        events.append({"id": f"event-{kind}-{len(events)+1}", "type": kind,
                       "from_version_ids": [version(p) for p in ps],
                       "to_version_ids": [result[c]["version_id"] for c in cs],
                       "links": [link(p, c) for p in ps for c in cs]})
    for c in range(count):
        if c in used_c:
            continue
        identity, name = new_identity()
        result[c] = {"lineage_id": identity, "label": name, "version_id": f"{identity}@{week}",
                     "continuity": {"relationship": "birth", "primary_predecessor_version_id": None,
                                    "cosine_similarity": None}}
        events.append({"id": f"event-birth-{len(events)+1}", "type": "birth",
                       "from_version_ids": [], "to_version_ids": [result[c]["version_id"]], "links": []})
    for p in range(len(prior_networks)):
        if p not in used_p:
            events.append({"id": f"event-retirement-{len(events)+1}", "type": "retirement",
                           "from_version_ids": [version(p)], "to_version_ids": [], "links": []})
    return result, events


def reconstruct(manifests: list[dict], registry: dict, week: str, *, factors: int,
                seed: int = 42, previous: dict | None = None, code_version: str = "0000000",
                reserved_ids: set[str] | None = None) -> dict:
    validate_observer_registry(registry)
    for manifest in manifests:
        validate_observation_manifest(manifest, registry)
    if previous is not None:
        validate_weekly_model(previous, registry)
        prev_date = date.fromisocalendar(*map(int, previous["week"].replace("-W", " ").split()), 1)
        current_date = date.fromisocalendar(*map(int, week.replace("-W", " ").split()), 1)
        if (current_date - prev_date).days != 7:
            raise ValueError("previous model must be the immediately preceding ISO week")
    values, observers, stories, active, observations, capped = matrix(manifests, week)
    if not active:
        raise ValueError("no observers active in preceding week")
    left, right = fit(values, factors, seed)
    member = affinities(left)
    if np.any(member.sum(axis=0) < EPS):
        raise ValueError("empty factor")
    families = {row["id"]: row["brand_family_id"] for row in registry["observers"]}
    measures = diagnostics(values, observers, families, left, right, seed)
    identities, events = lineage(member, observers, week, previous, reserved_ids=reserved_ids)
    memberships = [{"observer_id": observer, "weights": {
        identities[i]["lineage_id"]: float(member[index, i]) for i in range(factors)
    }} for index, observer in enumerate(observers)]
    networks = []
    for i in range(factors):
        weights = member[:, i]
        mass = float(weights.sum())
        active_rows = [(observer, float(weights[index])) for index, observer in enumerate(observers)
                       if observer in active and weights[index] > 0]
        active_mass = sum(weight for _, weight in active_rows)
        if active_mass < EPS:
            raise ValueError("factor has no active membership mass")
        participants = [{"observer_id": observer, "network_share": weight / active_mass,
                         "observer_affinity": weight}
                        for observer, weight in active_rows]
        participants.sort(key=lambda row: (-row["network_share"], row["observer_id"]))
        networks.append({**identities[i], "factor_index": i,
                         "effective_membership": mass * mass / float(np.sum(weights ** 2)),
                         "participants": participants})
    included = sorted({row["medium"] for manifest in manifests for row in manifest["observations"]})
    start, end, effective = bounds(week)
    overlap = [{"left_lineage_id": min(identities[i]["lineage_id"], identities[j]["lineage_id"]),
                "right_lineage_id": max(identities[i]["lineage_id"], identities[j]["lineage_id"]),
                "similarity": similarity} for i, j, similarity in measures.pop("profile_pairs")]
    generated = max(effective, datetime.now(timezone.utc).astimezone(EASTERN))
    result = {"document_type": "media-weekly-model", "schema_version": 1,
              "week": week, "previous_model_week": None if previous is None else previous["week"],
              "generated_at": stamp(generated), "effective_at": stamp(effective),
              "timezone": "America/New_York",
              "inference_window": {"start_at": stamp(start), "end_at": stamp(end),
                                   "completed_weeks": 12, "half_life_weeks": 4},
              "model": {"algorithm": "non-negative-matrix-factorization",
                        "implementation_version": "stage4-shadow-v1", "code_version": code_version,
                        "factor_count": factors, "random_seed": seed, "continuity_threshold": 0.65},
              "counts": {"observer_count": len(observers), "story_count": len(stories),
                         "observation_count": observations},
              "coverage": {"included_media": included, "excluded_media": sorted(MEDIA - set(included)),
                           "attempted_observer_count": len(observers),
                           "successful_observer_count": len(observers),
                           "material_limitations": ["Offline shadow reconstruction; event audit not complete.",
                                                    f"Daily activity cap is 12 distinct stories; {capped} retained observer-story-days.",
                                                    "Collection health unavailable here; no model is authorized for publication."]},
              "previous_week_active_observer_ids": active, "memberships": memberships,
              "networks": networks, "lineage_events": events,
              "diagnostics": {**measures, "network_story_overlap": overlap}}
    validate_weekly_model(result, registry)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline Stage 4 shadow reconstruction from synthetic manifests only")
    parser.add_argument("--fixture-manifests", type=Path, required=True,
                        help="JSON array of twelve invented weekly manifests; real data is blocked")
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--week", required=True)
    parser.add_argument("--factors", type=int, required=True)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--lineage-ledger", type=Path,
                        help="complete JSON array of all assigned lineage IDs, including retired ones")
    parser.add_argument("--output-dir", type=Path, default=Path(".work/media-networks-stage4"))
    args = parser.parse_args()
    manifests = load_document(args.fixture_manifests)
    registry = load_document(args.registry)
    # A fixture-only entry point prevents accidental use of the unaudited
    # Stage 3 collection. It is not an adequate production provenance check.
    if not all("synthetic" in row["normalized_title"].casefold()
               for manifest in manifests for row in manifest["observations"]):
        parser.error("only synthetic observation fixtures are admitted at Stage 4")
    previous = load_document(args.previous) if args.previous else None
    reserved_ids = None if not args.lineage_ledger else set(load_document(args.lineage_ledger))
    code_version = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    result = reconstruct(manifests, registry, args.week, factors=args.factors,
                         seed=args.seed, previous=previous, code_version=code_version,
                         reserved_ids=reserved_ids)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    target = args.output_dir / f"{args.week}.json"
    if target.exists():
        parser.error("refusing to overwrite an existing shadow result")
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=args.output_dir,
                                     prefix=".stage4-", delete=False) as handle:
        json.dump(result, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(target)
    print(f"Validated private synthetic model: {target}")


if __name__ == "__main__":
    main()
