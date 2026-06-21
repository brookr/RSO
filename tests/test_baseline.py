"""Re-cutting the card's embedded baseline from the Tier-1 index (as-of-mint)."""

import json
import tempfile
import types
import unittest
from pathlib import Path

from pipeline import snapshot

REAL_CARD = Path(__file__).resolve().parent.parent / "card" / "index.html"


def gp_record(cat_id="1", mean_motion="15.0", object_type="PAYLOAD", decay=None):
    record = {
        "NORAD_CAT_ID": cat_id, "CREATION_DATE": "2026-01-01T05:00:00",
        "EPOCH": "2026-01-01T04:00:00", "MEAN_MOTION": mean_motion,
        "ECCENTRICITY": "0.0001", "INCLINATION": "51.6", "RA_OF_ASC_NODE": "10.0",
        "ARG_OF_PERICENTER": "20.0", "MEAN_ANOMALY": "30.0", "OBJECT_TYPE": object_type,
    }
    record["DECAY_DATE"] = decay
    return record


SYNTHETIC_CARD = """<!doctype html><html><body><script>
    const CONFIG = { repo: "OMPub/RSO", branch: "node" };
    // BASELINE:BEGIN — generated; do not hand-edit
    const BASELINE = {"buildAt":"old","startDate":"old","days":[{"d":"2000-01-01","c":1}],"attest":{"2000-01-01":{"b":"0xabc","t":3}},"anno":{"decayNotices":5},"annoDate":"2000-01-01","chainMeta":{"contractAddress":"0xDEAD","chainId":1}};
    // BASELINE:END
    console.log(BASELINE.days.length);
</script></body></html>
"""


class BaselineBuildTests(unittest.TestCase):
    def setUp(self):
        self._original = (snapshot.DATA_DIR, snapshot.LEDGER_PATH, snapshot.INDEX_DIR)
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        snapshot.DATA_DIR = tmp / "data"
        snapshot.LEDGER_PATH = tmp / "ledger.json"
        snapshot.INDEX_DIR = tmp / "index"
        self.tmp = tmp
        records = [gp_record("1"), gp_record("2", "1.0", "ROCKET BODY"), gp_record("3", "15.0", "DEBRIS", decay="2025-06-01")]
        for date in ("2025-12-31", "2026-01-01"):
            m = snapshot.save_snapshot(date, snapshot.canonicalize(records), records, "genesis_from_gp", "test", ["/x"])
            snapshot.update_ledger(m)
        self.write_arweave_receipt("2026-01-01", "TXPERM")
        self.index_manifest = snapshot.build_index("OMPub/RSO", "node")

    def tearDown(self):
        snapshot.DATA_DIR, snapshot.LEDGER_PATH, snapshot.INDEX_DIR = self._original
        self._tmp.cleanup()

    def write_arweave_receipt(self, date, tx):
        snapshot.write_json(
            snapshot.snapshot_dir(date) / "storage.json",
            {"date": date, "bundle_sha256": "abc", "destinations": {"arweave": {
                "status": "confirmed", "transaction_id": tx,
                "transaction_url": f"https://arweave.net/{tx}", "bundle_sha256": "abc"}}},
        )

    def test_compact_day_keys_and_aggregates(self):
        carry = {"attest": {"x": 1}, "anno": {"y": 2}, "annoDate": "2026-01-01", "chainMeta": {"contractAddress": "0xC"}}
        baseline = snapshot.build_baseline_object(
            self.index_manifest, snapshot.INDEX_DIR, start_date="2026-01-01",
            build_at="2026-01-01T00:00:00Z", carry=carry,
        )
        self.assertEqual(len(baseline["days"]), 2)
        self.assertEqual(baseline["startDate"], "2026-01-01")
        self.assertEqual(baseline["buildAt"], "2026-01-01T00:00:00Z")
        day = baseline["days"][0]
        self.assertEqual(day["d"], "2025-12-31")
        # base keys + aggregates + locator
        for key in ("d", "c", "s", "cs", "sc", "pv", "by", "oo", "re", "bc", "tc", "dl"):
            self.assertIn(key, day)
        self.assertEqual(day["oo"], 2)
        self.assertEqual(day["re"], 1)
        self.assertEqual(day["bc"], {"leo": 1, "meo": 0, "geo": 1})
        self.assertEqual(day["sc"], snapshot.CONTENT_SCHEMA)
        # latest day carries the permanent Arweave locator
        latest = baseline["days"][1]
        self.assertEqual(latest["cu"], "https://arweave.net/TXPERM")
        self.assertEqual(latest["ck"], "bundle_tar")
        # carry preserved verbatim
        self.assertEqual(baseline["attest"], carry["attest"])
        self.assertEqual(baseline["anno"], carry["anno"])
        self.assertEqual(baseline["chainMeta"], carry["chainMeta"])

    def test_default_start_date_is_index_head(self):
        baseline = snapshot.build_baseline_object(self.index_manifest, snapshot.INDEX_DIR)
        self.assertEqual(baseline["startDate"], "2026-01-01")

    def test_inject_and_extract_round_trip(self):
        card = self.tmp / "card.html"
        card.write_text(SYNTHETIC_CARD)
        carry = snapshot.extract_baseline_from_card(card)
        self.assertEqual(carry["chainMeta"]["contractAddress"], "0xDEAD")

        baseline = snapshot.build_baseline_object(
            self.index_manifest, snapshot.INDEX_DIR, start_date="2026-01-01", carry=carry
        )
        snapshot.inject_baseline_into_card(card, json.dumps(baseline, ensure_ascii=True, separators=(",", ":")))

        text = card.read_text()
        self.assertEqual(text.count("BASELINE:BEGIN"), 1)
        self.assertEqual(text.count("BASELINE:END"), 1)
        self.assertEqual(text.count("const BASELINE = "), 1)
        # surrounding script untouched
        self.assertIn('const CONFIG = { repo: "OMPub/RSO"', text)
        self.assertIn("console.log(BASELINE.days.length);", text)

        roundtrip = snapshot.extract_baseline_from_card(card)
        self.assertEqual(roundtrip["days"], baseline["days"])
        self.assertEqual(roundtrip["startDate"], "2026-01-01")
        # carried planes preserved through the re-cut
        self.assertEqual(roundtrip["chainMeta"]["contractAddress"], "0xDEAD")
        self.assertEqual(roundtrip["attest"], carry["attest"])

    def test_process_build_baseline_refuses_empty(self):
        empty_dir = self.tmp / "empty"
        empty_dir.mkdir()
        snapshot.write_json(empty_dir / "manifest.json", {"schema": "rso-index-v1", "chunks": [], "latest": None})
        card = self.tmp / "card.html"
        card.write_text(SYNTHETIC_CARD)
        args = types.SimpleNamespace(
            index_dir=str(empty_dir), start_date=None, build_at=None,
            carry_from=None, inject=str(card), output=None,
        )
        with self.assertRaises(snapshot.SnapshotError):
            snapshot.process_build_baseline(args)
        # untouched original baseline still present
        self.assertIn('"startDate":"old"', card.read_text())

    def test_inject_without_markers_raises(self):
        card = self.tmp / "nomarkers.html"
        card.write_text("<html><script>const BASELINE = {};</script></html>")
        with self.assertRaises(snapshot.SnapshotError):
            snapshot.inject_baseline_into_card(card, "{}")


class RealCardMarkerTests(unittest.TestCase):
    def test_real_card_has_markers_and_extractable_baseline(self):
        baseline = snapshot.extract_baseline_from_card(REAL_CARD)
        self.assertIsNotNone(baseline, "real card must carry the BASELINE markers")
        self.assertIsInstance(baseline.get("days"), list)
        self.assertGreaterEqual(len(baseline["days"]), 50)
        # the planes a re-cut must carry forward
        for key in ("attest", "anno", "chainMeta", "startDate"):
            self.assertIn(key, baseline)


if __name__ == "__main__":
    unittest.main()
