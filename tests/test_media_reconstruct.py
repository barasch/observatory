"""Synthetic-only Stage 4 inference tests."""
from __future__ import annotations

import copy
import importlib.util
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
if importlib.util.find_spec("numpy") and importlib.util.find_spec("scipy"):
    import numpy as np
    from media_reconstruct import bounds, matrix, reconstruct, lineage, fit, affinities, order_decision
else:  # Pages keeps its existing editorial-only dependency set.
    np = None
from media_validate import load_document, validate_weekly_model  # noqa: E402

FIXTURES = ROOT / "tests/fixtures/media"


def manifests(week: str = "2026-W42") -> list[dict]:
    template = load_document(FIXTURES / "observations.json")
    end = bounds(week)[1]
    result = []
    for offset in range(12, 0, -1):
        manifest = copy.deepcopy(template)
        shift = (end.date() - timedelta(days=7 * offset - 2)) - datetime(2026, 10, 14).date()
        local_week = (end.date() - timedelta(days=7 * offset)).isocalendar()
        manifest["week"] = f"{local_week.year:04d}-W{local_week.week:02d}"
        manifest["generated_at"] = (datetime(2026, 10, 14, 16, 5, tzinfo=timezone.utc)
                                    + timedelta(days=shift.days)).isoformat().replace("+00:00", "Z")
        for observation in manifest["observations"]:
            observation["id"] += f"-{offset}"
            timestamp = datetime.fromisoformat(observation["attention_at"].replace("Z", "+00:00"))
            observation["attention_at"] = (timestamp + timedelta(days=shift.days)).isoformat().replace("+00:00", "Z")
        for story in manifest["stories"]:
            story["observation_ids"] = [value + f"-{offset}" for value in story["observation_ids"]]
            for key in ("first_attention_at", "last_attention_at"):
                timestamp = datetime.fromisoformat(story[key].replace("Z", "+00:00"))
                story[key] = (timestamp + timedelta(days=shift.days)).isoformat().replace("+00:00", "Z")
        result.append(manifest)
    return result


@unittest.skipIf(np is None, "optional offline media inference dependencies are not installed")
class ReconstructionTests(unittest.TestCase):
    def setUp(self):
        self.registry = load_document(FIXTURES / "observer-registry.json")
        self.manifests = manifests()

    def test_twelve_week_matrix_and_mirror_cap(self):
        values, observers, stories, active, observation_count, capped = matrix(self.manifests, "2026-W42")
        self.assertEqual(values.shape, (5, 3))
        self.assertEqual(len(active), 5)
        self.assertEqual(observation_count, 132)
        self.assertEqual(capped, 120)  # Atlas's video mirror does not add mass.
        self.assertLessEqual(float(values.max()), 3.0)

    def test_missing_week_rejected(self):
        with self.assertRaisesRegex(ValueError, "twelve distinct"):
            matrix(self.manifests[:-1], "2026-W42")

    def test_seeded_nonnegative_factorization_and_normalized_rows(self):
        values, *_ = matrix(self.manifests, "2026-W42")
        left, right = fit(values, 2, 42)
        same_left, same_right = fit(values, 2, 42)
        np.testing.assert_allclose(left, same_left)
        np.testing.assert_allclose(right, same_right)
        np.testing.assert_allclose(affinities(left).sum(axis=1), 1)
        self.assertTrue(np.all(left >= 0))
        self.assertTrue(np.all(right >= 0))

    def test_weekly_model_validates_with_synthetic_data(self):
        model = reconstruct(self.manifests, self.registry, "2026-W42", factors=2,
                            code_version="abcdef0")
        validate_weekly_model(model, self.registry)
        self.assertEqual(model["counts"]["observer_count"], 5)
        self.assertEqual(len(model["lineage_events"]), 2)
        self.assertTrue(all(event["type"] == "birth" for event in model["lineage_events"]))

    def test_factor_permutation_keeps_lineage(self):
        prior = reconstruct(self.manifests, self.registry, "2026-W42", factors=2,
                            code_version="abcdef0")
        observers = [row["observer_id"] for row in prior["memberships"]]
        old = np.array([[row["weights"][n["lineage_id"]] for n in prior["networks"]]
                        for row in prior["memberships"]])
        identities, events = lineage(old[:, ::-1], observers, "2026-W43", prior)
        self.assertEqual(identities[0]["lineage_id"], prior["networks"][1]["lineage_id"])
        self.assertEqual(identities[1]["lineage_id"], prior["networks"][0]["lineage_id"])
        self.assertEqual({event["type"] for event in events}, {"continuation"})

    def test_dst_window_is_calendar_bounded(self):
        start, end, _ = bounds("2026-W45")
        self.assertEqual((end.date() - start.date()).days, 84)
        elapsed = end.astimezone(timezone.utc) - start.astimezone(timezone.utc)
        self.assertEqual(elapsed.total_seconds(), 84 * 86400 + 3600)

    def test_new_lineage_needs_complete_reserved_history(self):
        prior = reconstruct(self.manifests, self.registry, "2026-W42", factors=2,
                            code_version="abcdef0")
        observers = [row["observer_id"] for row in prior["memberships"]]
        novel = np.array([[1., 0., 0.], [0., 1., 0.], [0., 0., 1.],
                          [0.5, 0.5, 0.], [0., 0.5, 0.5]])
        with self.assertRaisesRegex(ValueError, "ledger"):
            lineage(novel, observers, "2026-W43", prior, threshold=1.0)
        reserved = {"lineage-0001", "lineage-0002", "lineage-0008"}
        identities, _ = lineage(novel, observers, "2026-W43", prior,
                                threshold=1.0, reserved_ids=reserved)
        self.assertTrue(all(int(value["lineage_id"].split("-")[1]) > 8
                            for value in identities.values()))

    def test_split_and_merger_have_one_inheriting_lineage(self):
        observers = [f"observer-{value}" for value in "abcde"]
        old = np.array([[1., 0.], [1., 0.], [0., 1.], [0., 1.], [0., 1.]])
        previous = {
            "networks": [
                {"lineage_id": "lineage-0001", "version_id": "lineage-0001@2026-W42", "label": "Network A"},
                {"lineage_id": "lineage-0002", "version_id": "lineage-0002@2026-W42", "label": "Network B"},
            ],
            "memberships": [{"observer_id": observer, "weights": {
                "lineage-0001": float(old[i, 0]), "lineage-0002": float(old[i, 1])}}
                for i, observer in enumerate(observers)],
        }
        split = np.array([[1., 0., 0.], [0.4, 0.6, 0.], [0., 0., 1.],
                          [0., 0., 1.], [0., 0., 1.]])
        identities, events = lineage(split, observers, "2026-W43", previous,
                                     reserved_ids={"lineage-0001", "lineage-0002"})
        self.assertEqual({event["type"] for event in events}, {"split", "continuation"})
        self.assertEqual(sum(value["lineage_id"] == "lineage-0001"
                             for value in identities.values()), 1)
        tied = np.array([[1., 0., 0.], [0., 1., 0.], [0., 0., 1.],
                         [0., 0., 1.], [0., 0., 1.]])
        with self.assertRaisesRegex(ValueError, "inheritor is ambiguous"):
            lineage(tied, observers, "2026-W43", previous,
                    reserved_ids={"lineage-0001", "lineage-0002"})
        merged = np.array([[1.], [1.], [1.], [1.], [1.]])
        merged_ids, merged_events = lineage(merged, observers, "2026-W43", previous,
                                            threshold=0.6, reserved_ids={"lineage-0001", "lineage-0002"})
        self.assertEqual(merged_events[0]["type"], "merger")
        self.assertEqual(merged_ids[0]["lineage_id"], "lineage-0002")

    def test_model_order_requires_one_se_and_adjacent_confirmation(self):
        rows = []
        for week in ("2026-W42", "2026-W43"):
            for k in range(5, 16):
                rows.append({"weekly_cutoff": week, "k": k,
                             "held_out_reconstruction_error": 0.10 + abs(k - 8) * 0.01,
                             "held_out_standard_error": 0.011,
                             "bootstrap_stability": 0.8,
                             "minimum_effective_membership": 3.0,
                             "median_effective_membership": 4.0,
                             "agenda_distinctiveness": 0.4,
                             "observer_sensitivity": 0.1,
                             "brand_family_sensitivity": 0.1,
                             "passes_quality_gates": k >= 7})
        report = order_decision(rows, ["2026-W42", "2026-W43"],
                                current_k=10, generated_at="2026-10-19T10:00:00Z")
        self.assertEqual(report["decision"]["best_k"], 8)
        self.assertEqual(report["decision"]["selected_k"], 7)
        self.assertTrue(report["decision"]["change_authorized"])
        modified = copy.deepcopy(rows)
        for row in modified:
            if row["weekly_cutoff"] == "2026-W42" and row["k"] == 7:
                row["passes_quality_gates"] = False
        report = order_decision(modified, ["2026-W42", "2026-W43"], current_k=10)
        self.assertFalse(report["decision"]["change_authorized"])


if __name__ == "__main__":
    unittest.main()
