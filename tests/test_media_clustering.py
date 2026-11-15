"""Stage 3 story-identity, clustering, manifest, and audit tests."""
from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from media_cluster import (  # noqa: E402
    PairDecision,
    Unit,
    _story_id,
    audit_document,
    build_manifests,
    build_units,
    candidate_decisions,
    classify_pair,
    cluster_units,
    evaluate_hlgd,
    title_features,
    validate_cluster_audit,
    write_private_run,
)
from media_validate import load_document, validate_collection_batch, validate_observation_manifest  # noqa: E402


UTC = timezone.utc
AT = datetime(2026, 10, 14, 14, 0, tzinfo=UTC)
FIXTURES = ROOT / "tests" / "fixtures" / "media"


def unit(identifier: str, observer: str, title: str, hour: int = 14) -> Unit:
    return Unit(
        id=identifier,
        observer_id=observer,
        canonical_item_id=f"item-{identifier}",
        item_ids=(f"item-{identifier}",),
        title=title,
        url=f"https://{identifier}.example/story",
        attention_at=datetime(2026, 10, 14, hour, 0, tzinfo=UTC),
        features=title_features(title),
    )


def decision(merge: bool, score: float) -> PairDecision:
    return PairDecision(merge, score, "synthetic", 0.8, 0.9, 0.85, (), ())


def collection_batch() -> dict:
    items = [
        {
            "id": "item-atlas-web",
            "observer_id": "observer-atlas",
            "channel_id": "channel-atlas",
            "medium": "website",
            "normalized_title": "Court orders release of the complete audit report",
            "url": "https://atlas.example/audit-report",
            "external_id": "atlas-web",
            "attention_at": "2026-10-14T14:00:00Z",
            "timestamp_basis": "published",
            "collected_at": "2026-10-14T16:00:00Z",
            "mirror_group_id": "mirror-atlas",
        },
        {
            "id": "item-atlas-video",
            "observer_id": "observer-atlas",
            "channel_id": "channel-atlas-video",
            "medium": "youtube",
            "normalized_title": "Court orders release of the complete audit report",
            "url": "https://video.example/atlas-audit-report",
            "external_id": "atlas-video",
            "attention_at": "2026-10-14T14:10:00Z",
            "timestamp_basis": "published",
            "collected_at": "2026-10-14T16:00:00Z",
            "mirror_group_id": "mirror-atlas",
        },
        {
            "id": "item-beacon",
            "observer_id": "observer-beacon",
            "channel_id": "channel-beacon",
            "medium": "website",
            "normalized_title": "Court orders release of the complete audit report",
            "url": "https://beacon.example/audit-report",
            "external_id": "beacon",
            "attention_at": "2026-10-14T15:00:00Z",
            "timestamp_basis": "published",
            "collected_at": "2026-10-14T16:00:00Z",
            "mirror_group_id": "mirror-beacon",
        },
    ]
    return {
        "document_type": "media-collection-batch",
        "schema_version": 1,
        "run_id": "collect-stage-three-test",
        "registry_updated_at": "2026-10-12T08:05:00Z",
        "started_at": "2026-10-14T15:59:00Z",
        "completed_at": "2026-10-14T16:05:00Z",
        "timezone": "America/New_York",
        "lookback_started_at": "2026-10-01T15:59:00Z",
        "items": items,
        "mirror_groups": [
            {
                "id": "mirror-atlas",
                "observer_id": "observer-atlas",
                "canonical_item_id": "item-atlas-web",
                "match_basis": "exact-title",
                "item_ids": ["item-atlas-web", "item-atlas-video"],
            },
            {
                "id": "mirror-beacon",
                "observer_id": "observer-beacon",
                "canonical_item_id": "item-beacon",
                "match_basis": "singleton",
                "item_ids": ["item-beacon"],
            },
        ],
    }


class MediaClusteringTests(unittest.TestCase):
    def test_cross_observer_same_event_merges(self):
        result = classify_pair(
            "Walter Koenig, Ensign Chekov on Original Star Trek Series, Dies at 90",
            "Walter Koenig, Ensign Chekov on the original Star Trek series, dies at 90",
            left_at=AT,
            right_at=AT,
        )
        self.assertTrue(result.merge)
        self.assertGreaterEqual(result.score, 0.95)

    def test_changed_calendar_number_is_not_a_match(self):
        result = classify_pair(
            "Early Edition: October 26, 2026",
            "Early Edition: October 27, 2026",
            left_at=AT,
            right_at=AT,
        )
        self.assertFalse(result.merge)

    def test_same_url_without_title_support_is_not_a_match(self):
        result = classify_pair(
            "Company chief resigns after the security breach",
            "Interim chief outlines plans for overseas expansion",
            left_at=AT,
            right_at=AT,
            left_url="https://publisher.example/live-file",
            right_url="https://publisher.example/live-file",
        )
        self.assertFalse(result.merge)

    def test_same_observer_template_is_blocked(self):
        units = [
            unit("first", "observer-atlas", "CNN IS FINISHED | The Kyle Sample Show"),
            unit("second", "observer-atlas", "ASMONGOLD IS FINISHED | The Kyle Sample Show", 15),
        ]
        results = candidate_decisions(units)
        result = results[("first", "second")]
        self.assertFalse(result.merge)
        self.assertEqual(result.basis, "intra-observer-near-duplicate-gate")

    def test_same_observer_near_duplicate_is_retained(self):
        units = [
            unit("first", "observer-atlas", "Transcript: Court Orders Release of Complete Audit Report"),
            unit("second", "observer-atlas", "Court Orders Release of the Complete Audit Report", 15),
        ]
        self.assertTrue(candidate_decisions(units)[("first", "second")].merge)

    def test_complete_link_rejects_a_transitive_bridge(self):
        units = [
            unit("a", "observer-atlas", "Synthetic event alpha report"),
            unit("b", "observer-beacon", "Synthetic event beta report"),
            unit("c", "observer-cedar", "Synthetic event gamma report"),
        ]
        decisions = {
            ("a", "b"): decision(True, 0.98),
            ("b", "c"): decision(True, 0.97),
            ("a", "c"): decision(False, 0.80),
        }
        clusters = cluster_units(units, decisions)
        self.assertEqual(sorted(len(cluster) for cluster in clusters), [1, 2])

    def test_candidate_keys_are_canonical_when_time_and_id_orders_differ(self):
        units = [
            unit("z-earlier", "observer-atlas", "Court orders release of complete audit report", 12),
            unit("a-later", "observer-beacon", "Court orders release of complete audit report", 14),
        ]
        decisions = candidate_decisions(units)
        self.assertIn(("a-later", "z-earlier"), decisions)
        self.assertEqual([len(cluster) for cluster in cluster_units(units, decisions)], [2])

    def test_mirror_items_share_one_story_and_manifest_validates(self):
        registry = load_document(FIXTURES / "observer-registry.json")
        batch = collection_batch()
        validate_collection_batch(batch, registry)
        units, _ = build_units(batch)
        decisions = candidate_decisions(units)
        clusters = cluster_units(units, decisions)
        manifests = build_manifests(batch, clusters, decisions)
        self.assertEqual(len(manifests), 1)
        self.assertEqual(len(manifests[0]["observations"]), 3)
        self.assertEqual(len({row["story_id"] for row in manifests[0]["observations"]}), 1)
        validate_observation_manifest(manifests[0], registry)

    def test_story_anchor_is_stable_when_a_later_item_arrives(self):
        first = unit("first", "observer-atlas", "Court orders release of audit report", 12)
        second = unit("second", "observer-beacon", "Court orders release of audit report", 14)
        self.assertEqual(_story_id([first]), _story_id([first, second]))

    def test_representative_links_use_distinct_observers(self):
        batch = collection_batch()
        units, _ = build_units(batch)
        decisions = candidate_decisions(units)
        clusters = cluster_units(units, decisions)
        audit = audit_document(batch, units, clusters, decisions)
        links = audit["stories"][0]["representative_links"]
        self.assertEqual(len(links), 2)
        self.assertEqual(len({link["observer_id"] for link in links}), 2)

    def test_cluster_audit_recomputes_counts_and_rejects_unknown_fields(self):
        batch = collection_batch()
        units, _ = build_units(batch)
        decisions = candidate_decisions(units)
        clusters = cluster_units(units, decisions)
        audit = audit_document(batch, units, clusters, decisions)
        validate_cluster_audit(audit, batch)
        audit["counts"]["story_count"] += 1
        with self.assertRaises(ValueError):
            validate_cluster_audit(audit, batch)

    def test_hlgd_label_one_means_same_event(self):
        row = {
            "timeline_id": 1,
            "headline_a": "Court orders release of the complete audit report",
            "headline_b": "Court orders release of the complete audit report",
            "date_a": "2026-10-14",
            "date_b": "2026-10-14",
            "url_a": "https://atlas.example/report",
            "url_b": "https://beacon.example/report",
            "label": 1,
        }
        result = evaluate_hlgd([copy.deepcopy(row) for _ in range(100)])
        self.assertTrue(result["passes_precision_gate"])
        self.assertEqual(result["true_positive"], 100)

    def test_private_run_refuses_to_replace_an_existing_run(self):
        batch = collection_batch()
        units, _ = build_units(batch)
        decisions = candidate_decisions(units)
        clusters = cluster_units(units, decisions)
        manifests = build_manifests(batch, clusters, decisions)
        audit = audit_document(batch, units, clusters, decisions)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            write_private_run(output, batch, manifests, audit)
            with self.assertRaises(FileExistsError):
                write_private_run(output, batch, manifests, audit)


if __name__ == "__main__":
    unittest.main()
