import json
import tempfile
import unittest
from pathlib import Path

from pipeline import snapshot


def gp_record(cat_id="1"):
    return {
        "NORAD_CAT_ID": cat_id,
        "CREATION_DATE": "2026-04-12T05:00:00",
        "EPOCH": "2026-04-12T04:00:00",
        "MEAN_MOTION": "15.0",
        "ECCENTRICITY": "0.0001",
        "INCLINATION": "51.6",
        "RA_OF_ASC_NODE": "10.0",
        "ARG_OF_PERICENTER": "20.0",
        "MEAN_ANOMALY": "30.0",
    }


class ManifestTests(unittest.TestCase):
    def test_manifest_fields_are_present_and_typed(self):
        original_data_dir = snapshot.DATA_DIR
        with tempfile.TemporaryDirectory() as tmp:
            try:
                snapshot.DATA_DIR = Path(tmp) / "data"
                data = [gp_record()]
                manifest = snapshot.save_snapshot(
                    "2026-04-12",
                    snapshot.canonicalize(data),
                    data,
                    "test_provenance",
                    "test_strategy",
                    ["/class/gp_history/format/json"],
                )

                self.assertEqual(manifest["date"], "2026-04-12")
                self.assertEqual(manifest["cutoff_utc"], "2026-04-12T00:00:00Z")
                self.assertEqual(manifest["state_as_of_utc"], "2026-04-12T00:00:00Z")
                self.assertIsInstance(manifest["sha256"], str)
                self.assertEqual(manifest["object_count"], 1)
                self.assertIsInstance(manifest["raw_bytes"], int)
                self.assertIsInstance(manifest["compressed_bytes"], int)
                self.assertEqual(manifest["provenance"], "test_provenance")
                self.assertEqual(manifest["format"], "OMM/JSON")
                self.assertEqual(manifest["source"], "space-track.org")
                self.assertEqual(manifest["pipeline_version"], "0.4.0")
                self.assertEqual(manifest["content_schema"], "rso-core-v2")
                self.assertEqual(
                    manifest["content_excluded_fields"],
                    list(snapshot.CONTENT_EXCLUDED_FIELDS),
                )
                self.assertEqual(
                    manifest["content_sha256"],
                    snapshot.core_content_sha256(data),
                )
                self.assertEqual(manifest["query_strategy"], "test_strategy")
                self.assertEqual(manifest["api_query_paths"], ["/class/gp_history/format/json"])

                manifest_path = snapshot.DATA_DIR / "2026" / "04" / "12" / "manifest.json"
                with open(manifest_path, encoding="utf-8") as f:
                    stored = json.load(f)
                self.assertEqual(stored["sha256"], manifest["sha256"])
            finally:
                snapshot.DATA_DIR = original_data_dir


if __name__ == "__main__":
    unittest.main()
