"""Offline contract tests. No claim here tests an unimplemented inference algorithm."""
import copy
import gzip
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from media_validate import (
    MediaValidationError, load_document, validate_document,
    validate_observer_registry, validate_observation_manifest,
    validate_weekly_model, validate_current_snapshot,
    validate_model_order_diagnostics,
)

FIXTURES = ROOT / "tests/fixtures/media"


class MediaContractTests(unittest.TestCase):
    def setUp(self):
        self.registry = load_document(FIXTURES / "observer-registry.json")
        self.manifest = load_document(FIXTURES / "observations.json")
        self.weekly = load_document(FIXTURES / "weekly-model.json")
        self.snapshot = load_document(FIXTURES / "current-snapshot.json")
        self.order = load_document(FIXTURES / "model-order-diagnostics.json")

    def bundle(self):
        return validate_current_snapshot(self.snapshot, self.registry, self.weekly, self.manifest)

    def test_all_checked_in_fixtures_validate(self):
        for path in FIXTURES.glob("*.json"):
            with self.subTest(path=path.name):
                validate_document(load_document(path))
        self.bundle()

    def test_production_registry_is_empty_and_valid_at_stage_one(self):
        registry = load_document(ROOT / "config/media-observers.json")
        validate_observer_registry(registry)
        self.assertEqual(registry["observers"], [])

    def test_compressed_manifests_are_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "observations.json.gz"
            with gzip.open(path, "wt", encoding="utf-8") as handle:
                import json
                json.dump(self.manifest, handle)
            validate_observation_manifest(load_document(path), self.registry)

    def test_unknown_fields_rejected_at_each_public_level(self):
        for document in (self.registry, self.manifest, self.weekly, self.snapshot, self.order):
            for target in (document, next(value[0] for value in document.values()
                                           if isinstance(value, list) and value and isinstance(value[0], dict))):
                with self.subTest(type=document["document_type"], target=target.keys()):
                    target["private_feedback"] = "never publish"
                    with self.assertRaises(MediaValidationError):
                        validate_document(document)
                    del target["private_feedback"]

    def test_rejects_full_text_in_observation(self):
        self.manifest["observations"][0]["full_text"] = "Not retained"
        with self.assertRaises(MediaValidationError):
            validate_observation_manifest(self.manifest)

    def test_rejects_malformed_header_and_nonfinite_numbers(self):
        for version in (True, 2, "1"):
            value = copy.deepcopy(self.weekly)
            value["schema_version"] = version
            with self.assertRaises(MediaValidationError):
                validate_document(value)
        for value in (float("nan"), float("inf"), True, -0.1, 1.1):
            document = copy.deepcopy(self.weekly)
            document["memberships"][0]["weights"]["lineage-0001"] = value
            with self.assertRaises(MediaValidationError):
                validate_weekly_model(document)
        with self.assertRaises(MediaValidationError):
            validate_document({"document_type": []})

    def test_unsafe_and_credential_bearing_urls_rejected(self):
        for url in ("javascript:alert(1)", "http://example.org/", "https://user:password@example.org/",
                    "https://example.org/a\npath", "https://[broken", "https://example.org:bad/"):
            self.registry["observers"][0]["channels"][0]["endpoint_url"] = url
            with self.subTest(url=url), self.assertRaises(MediaValidationError):
                validate_observer_registry(self.registry)

    def test_mirror_channels_cannot_belong_to_two_observers(self):
        self.registry["observers"][1]["channels"].append(copy.deepcopy(self.registry["observers"][0]["channels"][1]))
        with self.assertRaises(MediaValidationError):
            validate_observer_registry(self.registry)

    def test_retired_observer_channels_are_disabled(self):
        observer = self.registry["observers"][0]
        observer.update({"panel_status": "retired", "retired_at": "2026-10-01"})
        with self.assertRaises(MediaValidationError):
            validate_observer_registry(self.registry)

    def test_observation_must_match_channel_owner_and_medium(self):
        for field, value in (("observer_id", "observer-beacon"), ("medium", "podcast")):
            manifest = copy.deepcopy(self.manifest)
            manifest["observations"][0][field] = value
            with self.assertRaises(MediaValidationError):
                validate_observation_manifest(manifest, self.registry)

    def test_observation_story_assignment_and_bounds_are_consistent(self):
        self.manifest["stories"][0]["observation_ids"].pop()
        with self.assertRaises(MediaValidationError):
            validate_observation_manifest(self.manifest)

    def test_unknown_and_duplicate_story_evidence_rejected(self):
        for bad_id in ("observation-unknown", self.manifest["stories"][0]["observation_ids"][0]):
            manifest = copy.deepcopy(self.manifest)
            manifest["stories"][0]["observation_ids"].append(bad_id)
            with self.assertRaises(MediaValidationError):
                validate_observation_manifest(manifest)

    def test_manifest_uses_local_iso_week(self):
        self.manifest["observations"][0]["attention_at"] = "2026-10-12T02:00:00Z"
        with self.assertRaisesRegex(MediaValidationError, "does not fall"):
            validate_observation_manifest(self.manifest)

    def test_membership_rows_are_complete_normalized_and_unique(self):
        for change in ("sum", "missing", "duplicate"):
            document = copy.deepcopy(self.weekly)
            if change == "sum":
                document["memberships"][0]["weights"]["lineage-0001"] = .5
            elif change == "missing":
                del document["memberships"][0]["weights"]["lineage-0002"]
            else:
                document["memberships"][1] = document["memberships"][0]
            with self.assertRaises(MediaValidationError):
                validate_weekly_model(document)

    def test_empty_factor_is_rejected_without_division_by_zero(self):
        for row in self.weekly["memberships"]:
            row["weights"] = {"lineage-0001": 1, "lineage-0002": 0}
        # First network's derived values must be refreshed to reach empty-factor check.
        self.weekly["networks"][0]["effective_membership"] = 6
        self.weekly["networks"][0]["participants"] = [
            {"observer_id": observer, "network_share": .2, "observer_affinity": 1}
            for observer in self.weekly["previous_week_active_observer_ids"]
        ]
        with self.assertRaisesRegex(MediaValidationError, "positive membership mass"):
            validate_weekly_model(self.weekly)

    def test_network_share_is_not_observer_affinity(self):
        participant = self.weekly["networks"][0]["participants"][0]
        self.assertNotAlmostEqual(participant["network_share"], participant["observer_affinity"])
        participant["network_share"] = participant["observer_affinity"]
        with self.assertRaises(MediaValidationError):
            validate_weekly_model(self.weekly)

    def test_effective_membership_is_recomputed(self):
        self.weekly["networks"][0]["effective_membership"] += 1
        with self.assertRaises(MediaValidationError):
            validate_weekly_model(self.weekly)

    def test_factor_permutation_does_not_require_relabeling(self):
        self.weekly["networks"][0]["factor_index"] = 1
        self.weekly["networks"][1]["factor_index"] = 0
        validate_weekly_model(self.weekly)
        self.assertEqual([n["label"] for n in self.weekly["networks"]], ["Network A", "Network B"])

    def make_split(self):
        self.weekly["previous_model_week"] = "2026-W41"
        predecessor = "lineage-0001@2026-W41"
        for network in self.weekly["networks"]:
            network["continuity"] = {"relationship": "split", "primary_predecessor_version_id": predecessor,
                                     "cosine_similarity": .8}
        self.weekly["lineage_events"] = [{"id": "event-split", "type": "split", "from_version_ids": [predecessor],
            "to_version_ids": [n["version_id"] for n in self.weekly["networks"]],
            "links": [{"from_version_id": predecessor, "to_version_id": n["version_id"],
                       "cosine_similarity": .8, "shared_membership_mass": 1} for n in self.weekly["networks"]]}]

    def test_split_can_have_one_inheriting_child_and_one_new_child(self):
        self.make_split()
        validate_weekly_model(self.weekly)

    def test_split_requires_exactly_one_inheriting_child(self):
        self.make_split()
        event = self.weekly["lineage_events"][0]
        event["from_version_ids"] = ["lineage-0003@2026-W41"]
        for link in event["links"]:
            link["from_version_id"] = "lineage-0003@2026-W41"
        for network in self.weekly["networks"]:
            network["continuity"]["primary_predecessor_version_id"] = "lineage-0003@2026-W41"
        with self.assertRaisesRegex(MediaValidationError, "exactly one split"):
            validate_weekly_model(self.weekly)

    def test_merger_retains_one_parent_and_records_all_links(self):
        self.weekly["previous_model_week"] = "2026-W41"
        net = self.weekly["networks"][0]
        net["continuity"] = {"relationship": "merger", "primary_predecessor_version_id": "lineage-0001@2026-W41",
                             "cosine_similarity": .9}
        self.weekly["lineage_events"][0] = {"id": "event-merger", "type": "merger",
            "from_version_ids": ["lineage-0001@2026-W41", "lineage-0003@2026-W41"],
            "to_version_ids": [net["version_id"]], "links": [
                {"from_version_id": f"lineage-000{n}@2026-W41", "to_version_id": net["version_id"],
                 "cosine_similarity": .9 if n == 1 else .7, "shared_membership_mass": 1} for n in (1, 3)]}
        validate_weekly_model(self.weekly)
        self.weekly["lineage_events"][0]["links"].pop()
        with self.assertRaises(MediaValidationError):
            validate_weekly_model(self.weekly)

    def test_snapshots_allow_zero_through_three_not_four_stories(self):
        for count in (0, 1, 2, 3):
            document = copy.deepcopy(self.snapshot)
            stories = document["networks"][0]["stories"]
            if count == 3:
                extra = copy.deepcopy(stories[-1]); extra["story_id"] = "story-third"
                stories.append(extra)
            document["networks"][0]["stories"] = stories[:count]
            validate_current_snapshot(document)
        self.snapshot["networks"][0]["stories"] *= 2
        with self.assertRaises(MediaValidationError):
            validate_current_snapshot(self.snapshot)

    def test_same_story_can_appear_in_both_networks(self):
        self.bundle()
        self.assertEqual(self.snapshot["networks"][0]["stories"][0]["story_id"],
                         self.snapshot["networks"][1]["stories"][0]["story_id"])

    def test_mirror_and_siblings_cannot_inflate_evidence_breadth(self):
        for field, value in (("independent_observer_count", 6), ("independent_brand_family_count", 5)):
            original = self.snapshot["networks"][0]["stories"][0][field]
            self.snapshot["networks"][0]["stories"][0][field] = value
            with self.assertRaises(MediaValidationError):
                self.bundle()
            self.snapshot["networks"][0]["stories"][0][field] = original

    def test_failed_feed_excluded_from_denominator(self):
        self.bundle()
        self.snapshot["coverage"]["successful_observer_ids"].append("observer-foxtrot")
        with self.assertRaisesRegex(MediaValidationError, "attention scores"):
            self.bundle()

    def test_snapshot_requires_a_nonempty_successful_denominator(self):
        self.snapshot["coverage"]["successful_observer_ids"] = []
        with self.assertRaises(MediaValidationError):
            validate_current_snapshot(self.snapshot)

    def test_score_and_overindex_must_match_evidence(self):
        story = self.snapshot["networks"][0]["stories"][0]
        story["weighted_attention"] *= .9
        with self.assertRaises(MediaValidationError):
            self.bundle()
        story["over_index"] = story["weighted_attention"] / story["panel_attention"]
        with self.assertRaisesRegex(MediaValidationError, "attention scores"):
            self.bundle()

    def test_unknown_evidence_and_unobserved_representative_links_rejected(self):
        self.snapshot["networks"][0]["stories"][0]["evidence_observation_ids"].append("observation-invented")
        with self.assertRaises(MediaValidationError):
            self.bundle()

    def test_top_three_participants_must_match_weekly_model(self):
        self.snapshot["networks"][0]["participants"][0]["network_share"] += .01
        with self.assertRaises(MediaValidationError):
            self.bundle()

    def test_evidence_manifest_list_supports_cross_week_interface(self):
        validate_current_snapshot(self.snapshot, self.registry, self.weekly, [self.manifest])
        with self.assertRaises(MediaValidationError):
            validate_current_snapshot(self.snapshot, self.registry, self.weekly, [self.manifest, self.manifest])

    def test_three_hour_schedule_and_elapsed_day_across_dst(self):
        for end, start in (("2026-03-08T07:00:00Z", "2026-03-07T07:00:00Z"),
                           ("2026-11-01T08:00:00Z", "2026-10-31T08:00:00Z")):
            document = copy.deepcopy(self.snapshot)
            document["scheduled_for"] = end
            document["generated_at"] = end
            document["window"] = {"start_at": start, "end_at": end}
            for network in document["networks"]:
                network["stories"] = []
            validate_current_snapshot(document)
        self.snapshot["scheduled_for"] = "2026-10-14T18:00:00Z"  # 2 p.m., not a slot
        with self.assertRaises(MediaValidationError):
            validate_current_snapshot(self.snapshot)

    def test_weekly_calendar_window_crosses_dst_not_fixed_utc_hours(self):
        document = copy.deepcopy(self.weekly)
        document["week"] = "2026-W45"
        document["generated_at"] = "2026-11-02T09:05:00Z"
        document["effective_at"] = "2026-11-02T09:00:00Z"
        document["inference_window"]["start_at"] = "2026-08-10T04:00:00Z"
        document["inference_window"]["end_at"] = "2026-11-02T05:00:00Z"
        for network, event in zip(document["networks"], document["lineage_events"]):
            network["version_id"] = network["version_id"].replace("W42", "W45")
            event["to_version_ids"] = [network["version_id"]]
        validate_weekly_model(document)

    def test_model_order_need_not_be_ten(self):
        validate_model_order_diagnostics(self.order)
        self.assertEqual(self.order["decision"]["selected_k"], 9)

    def test_model_order_rejects_coarse_prelaunch_and_endpoint_winner(self):
        coarse = copy.deepcopy(self.order)
        coarse["candidates"] = [c for c in coarse["candidates"] if c["k"] in (7, 10, 13)]
        coarse["decision"].update({"best_k": 10, "selected_k": 10,
                                   "winner_by_cutoff": {week: 10 for week in coarse["weekly_cutoffs"]}})
        with self.assertRaises(MediaValidationError):
            validate_model_order_diagnostics(coarse)
        self.order["decision"]["best_k"] = 15
        with self.assertRaises(MediaValidationError):
            validate_model_order_diagnostics(self.order)

    def test_model_order_selected_value_must_pass_quality_gates(self):
        next(candidate for candidate in self.order["candidates"] if candidate["k"] == 9)["passes_quality_gates"] = False
        with self.assertRaises(MediaValidationError):
            validate_model_order_diagnostics(self.order)

    def test_model_order_requires_adjacent_confirming_endpoints(self):
        self.order["decision"]["winner_by_cutoff"]["2026-W41"] = 10
        with self.assertRaises(MediaValidationError):
            validate_model_order_diagnostics(self.order)
        self.order["weekly_cutoffs"][0] = "2026-W39"
        with self.assertRaises(MediaValidationError):
            validate_model_order_diagnostics(self.order)

    def test_fixture_scenarios_cover_planned_lineage_cases(self):
        scenarios = load_document(FIXTURES / "lineage-scenarios.json")
        validate_document(scenarios)
        kinds = {event["type"] for s in scenarios["scenarios"] for event in s["expected"]["events"]}
        self.assertEqual(kinds, {"birth", "retirement", "continuation", "split", "merger", "uncertain"})


if __name__ == "__main__":
    unittest.main()
