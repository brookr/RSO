import hashlib
import io
import math
import unittest

from pipeline import snapshot


class CanonicalizeTests(unittest.TestCase):
    def test_canonical_json_is_stable_and_hashed(self):
        data = [{"b": 2, "a": 1}]
        canonical = snapshot.canonicalize(data)

        self.assertEqual(canonical, b'[{"a":1,"b":2}]')
        self.assertEqual(
            snapshot.compute_hash(canonical),
            "44c7deead2ed8313d29655e45c0d1469419213c93d9f44d66da7c7afe46e74e3",
        )
        self.assertEqual(hashlib.sha256(canonical).hexdigest(), snapshot.compute_hash(canonical))

    def test_key_order_and_whitespace_do_not_change_output(self):
        left = [{"z": 3, "a": {"y": 2, "x": 1}}]
        right = [{"a": {"x": 1, "y": 2}, "z": 3}]

        self.assertEqual(snapshot.canonicalize(left), snapshot.canonicalize(right))
        self.assertNotIn(b" ", snapshot.canonicalize(left))

    def test_canonical_json_rejects_non_finite_numbers(self):
        with self.assertRaises(ValueError):
            snapshot.canonicalize([{"value": math.nan}])

    def test_gp_records_require_string_values(self):
        record = {
            "NORAD_CAT_ID": "1",
            "CREATION_DATE": "2026-04-18T05:00:00",
            "EPOCH": "2026-04-18T04:00:00",
            "MEAN_MOTION": 15.0,
            "ECCENTRICITY": "0.0001",
            "INCLINATION": "51.6",
            "RA_OF_ASC_NODE": "10.0",
            "ARG_OF_PERICENTER": "20.0",
            "MEAN_ANOMALY": "30.0",
        }

        with self.assertRaisesRegex(snapshot.SnapshotError, "expected string"):
            snapshot.validate_gp_records([record], min_count=0, context="test")

    def test_records_by_cat_id_rejects_duplicate_ids(self):
        left = {
            "NORAD_CAT_ID": "1",
        }
        right = {
            "NORAD_CAT_ID": "1",
        }

        with self.assertRaisesRegex(snapshot.SnapshotError, "duplicate NORAD_CAT_ID"):
            snapshot.records_by_cat_id([left, right])

    def test_read_limited_rejects_non_positive_limit(self):
        with self.assertRaisesRegex(snapshot.SnapshotError, "positive"):
            snapshot.read_limited(io.BytesIO(b"x"), 0)

    def test_norad_cat_id_canonical_form(self):
        for good in ("0", "4", "5", "25544", "270438"):
            self.assertTrue(snapshot.is_canonical_norad_cat_id(good), good)
        # Leading-zero aliases ("05" int-equals "5"), Unicode / non-decimal
        # digits that str.isdigit() accepts, and non-strings must all be rejected
        # so the string form stays a bijection with the int used for sort/dedup.
        for bad in ("05", "00005", "", "5a", "-1", " 5", "٥", "\U0001d7d3", "²", "①"):
            self.assertFalse(snapshot.is_canonical_norad_cat_id(bad), repr(bad))
        self.assertFalse(snapshot.is_canonical_norad_cat_id(5))

    def test_records_by_cat_id_rejects_noncanonical_ids(self):
        for bad in ("05", "٥", "²"):
            with self.assertRaisesRegex(snapshot.SnapshotError, "invalid NORAD_CAT_ID"):
                snapshot.records_by_cat_id([{"NORAD_CAT_ID": bad}])


if __name__ == "__main__":
    unittest.main()
