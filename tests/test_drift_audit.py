"""Tests for the drift-audit classification logic."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "pipeline")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import snapshot  # noqa: E402
from pipeline import drift_audit  # noqa: E402
from tests.test_core_projection import gp_record  # noqa: E402


class FakeClient:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def query(self, path):
        self.queries.append(path)
        # return everything on the first range, nothing on the rest
        if len(self.queries) == 1:
            return list(self.rows)
        return []


def run_audit(fresh_rows, recorded, previous):
    manifest = {
        "date": "2026-06-08",
        "delta_window_start_utc": "2026-06-07T00:00:00Z",
        "delta_window_end_utc": "2026-06-08T00:00:00Z",
    }
    with patch.object(drift_audit.snapshot, "load_manifest", return_value=manifest), patch.object(
        drift_audit.snapshot,
        "read_catalog_bytes",
        return_value=snapshot.canonicalize(previous),
    ):
        return drift_audit.audit_window(FakeClient(fresh_rows), "2026-06-08", recorded)


class DriftAuditTest(unittest.TestCase):
    def base_day(self):
        previous = [gp_record(GP_ID="100", CREATION_DATE="2026-06-01T00:00:00")]
        recorded_update = gp_record(GP_ID="200", CREATION_DATE="2026-06-07T13:26:16")
        return previous, [recorded_update], recorded_update

    def test_excluded_field_mutation_is_observation_drift(self):
        previous, recorded, update = self.base_day()
        fresh = dict(update)
        fresh["DECAY_DATE"] = "2026-06-07"

        result = run_audit([fresh], recorded, previous)

        self.assertEqual(result["observation_drift_count"], 1)
        self.assertEqual(result["core_field_mutations"], [])
        self.assertEqual(result["selection_drift"], [])

    def test_core_field_mutation_alerts(self):
        previous, recorded, update = self.base_day()
        fresh = dict(update)
        fresh["MEAN_MOTION"] = "99.0"

        result = run_audit([fresh], recorded, previous)

        self.assertEqual(len(result["core_field_mutations"]), 1)
        self.assertEqual(result["core_field_mutations"][0]["field"], "MEAN_MOTION")

    def test_missing_recorded_elset_is_selection_drift(self):
        previous, recorded, _ = self.base_day()

        result = run_audit([], recorded, previous)

        self.assertEqual(result["selection_drift"][0]["kind"], "recorded_elset_absent")

    def test_superseding_gp_id_is_selection_drift(self):
        previous, recorded, update = self.base_day()
        fresh = dict(update)
        fresh["GP_ID"] = "999"

        result = run_audit([fresh], recorded, previous)

        self.assertEqual(result["selection_drift"][0]["kind"], "selection_superseded")

    def test_new_window_selection_is_selection_drift(self):
        previous, recorded, update = self.base_day()
        extra = gp_record(
            NORAD_CAT_ID="55555", GP_ID="300", CREATION_DATE="2026-06-07T10:00:00"
        )

        result = run_audit([dict(update), extra], recorded, previous)

        kinds = [item["kind"] for item in result["selection_drift"]]
        self.assertEqual(kinds, ["selection_appeared"])

    def test_identical_window_is_clean(self):
        previous, recorded, update = self.base_day()

        result = run_audit([dict(update)], recorded, previous)

        self.assertEqual(result["observation_drift_count"], 0)
        self.assertEqual(result["core_field_mutations"], [])
        self.assertEqual(result["selection_drift"], [])

    def test_sample_picker_combines_recent_and_older(self):
        days = [f"2026-05-{day:02d}" for day in range(1, 21)]
        sample = drift_audit.pick_sample_days(days, recent=3, older=4, seed="x")
        self.assertEqual(len(sample), 7)
        self.assertTrue(set(days[-3:]).issubset(sample))


if __name__ == "__main__":
    unittest.main()
