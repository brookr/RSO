"""Tests for EPOCH-keyed daily assembly with carry-forward (Phase B §2 / §5)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "pipeline")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import tle_normalize as tn  # noqa: E402
from assemble import ObjectHistory  # noqa: E402


def rec(norad, epoch, mean_motion="15.5"):
    """A minimal valid canonical 11-key core record."""
    return {
        "NORAD_CAT_ID": str(norad), "EPOCH": epoch,
        "INCLINATION": "51.6", "RA_OF_ASC_NODE": "100.0", "ECCENTRICITY": "0.001",
        "ARG_OF_PERICENTER": "90.0", "MEAN_ANOMALY": "270.0",
        "MEAN_MOTION": mean_motion, "MEAN_MOTION_DOT": "0", "MEAN_MOTION_DDOT": "0",
        "BSTAR": "0",
    }


class CarryForwardTests(unittest.TestCase):
    def test_object_carries_forward(self):
        h = ObjectHistory()
        h.add(rec(5, "1959-05-23T00:00:00.000000"))
        # present on its epoch day and every later day
        self.assertEqual(h.object_count_for_day("1959-05-23"), 1)
        self.assertEqual(h.object_count_for_day("1959-06-01"), 1)
        self.assertEqual(h.object_count_for_day("1960-01-01"), 1)

    def test_object_absent_before_first_epoch(self):
        h = ObjectHistory()
        h.add(rec(5, "1959-05-23T12:00:00.000000"))
        self.assertEqual(h.object_count_for_day("1959-05-22"), 0)
        self.assertEqual(h.object_count_for_day("1959-05-23"), 1)

    def test_latest_epoch_wins(self):
        h = ObjectHistory()
        h.add(rec(5, "1959-05-23T00:00:00.000000", mean_motion="15.5"))
        h.add(rec(5, "1959-06-10T00:00:00.000000", mean_motion="15.6"))
        # before the update -> old elset; on/after -> new elset
        self.assertEqual(
            h.catalog_for_day("1959-06-01")[0]["MEAN_MOTION"], "15.5")
        self.assertEqual(
            h.catalog_for_day("1959-06-10")[0]["MEAN_MOTION"], "15.6")

    def test_end_of_day_cutoff_boundary(self):
        h = ObjectHistory()
        # epoch late on the 23rd is in the 23rd's catalog; just past midnight is not
        h.add(rec(5, "1959-05-23T23:59:59.999999"))
        self.assertEqual(h.object_count_for_day("1959-05-23"), 1)
        h2 = ObjectHistory()
        h2.add(rec(5, "1959-05-24T00:00:00.000000"))
        self.assertEqual(h2.object_count_for_day("1959-05-23"), 0)

    def test_tie_break_highest_element_set_no(self):
        h = ObjectHistory()
        # element_set_no is selection metadata passed to add(), never in the core.
        h.add(rec(5, "1959-05-23T00:00:00.000000", mean_motion="15.7"), element_set_no="7")
        h.add(rec(5, "1959-05-23T00:00:00.000000", mean_motion="15.12"), element_set_no="12")
        # same epoch -> higher ELEMENT_SET_NO (12) wins
        self.assertEqual(
            h.catalog_for_day("1959-05-23")[0]["MEAN_MOTION"], "15.12")


class CatalogShapeTests(unittest.TestCase):
    def test_multiple_objects_sorted_and_hashable(self):
        h = ObjectHistory()
        h.add(rec(270000, "1959-05-23T00:00:00.000000"))
        h.add(rec(5, "1959-05-23T00:00:00.000000"))
        h.add(rec(100, "1959-05-23T00:00:00.000000"))
        cat = h.catalog_for_day("1959-05-23")
        self.assertEqual(len(cat), 3)
        # content_sha256 sorts numerically + is deterministic
        self.assertEqual(h.content_sha256_for_day("1959-05-23"),
                         tn.content_sha256(cat))

    def test_empty_before_any_data(self):
        import hashlib
        h = ObjectHistory()
        h.add(rec(5, "1959-05-23T00:00:00.000000"))
        self.assertEqual(h.content_sha256_for_day("1957-10-04"),
                         hashlib.sha256(b"[]").hexdigest())

    def test_hash_stable_while_catalog_unchanged(self):
        h = ObjectHistory()
        h.add(rec(5, "1959-05-23T00:00:00.000000"))
        h.add(rec(8, "1959-07-09T00:00:00.000000"))
        # between the two epochs the catalog is just object 5, unchanging
        a = h.content_sha256_for_day("1959-06-01")
        b = h.content_sha256_for_day("1959-06-15")
        self.assertEqual(a, b)
        # once object 8 appears, the hash changes
        self.assertNotEqual(a, h.content_sha256_for_day("1959-07-10"))

    def test_first_epoch_day(self):
        h = ObjectHistory()
        h.add(rec(5, "1959-05-23T00:00:00.000000"))
        h.add(rec(8, "1959-07-09T00:00:00.000000"))
        self.assertEqual(h.first_epoch_day(), "1959-05-23")


if __name__ == "__main__":
    unittest.main()
