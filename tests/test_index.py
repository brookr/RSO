"""Lean Tier-1 index: year chunks, manifest, CORS catalog locators, Arweave mirror."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from pipeline import snapshot


def gp_record(cat_id="1", mean_motion="15.0", object_type="PAYLOAD", decay=None):
    record = {
        "NORAD_CAT_ID": cat_id,
        "CREATION_DATE": "2026-01-01T05:00:00",
        "EPOCH": "2026-01-01T04:00:00",
        "MEAN_MOTION": mean_motion,
        "ECCENTRICITY": "0.0001",
        "INCLINATION": "51.6",
        "RA_OF_ASC_NODE": "10.0",
        "ARG_OF_PERICENTER": "20.0",
        "MEAN_ANOMALY": "30.0",
        "OBJECT_TYPE": object_type,
    }
    record["DECAY_DATE"] = decay
    return record


class IndexBuildBase(unittest.TestCase):
    def setUp(self):
        self._original = (snapshot.DATA_DIR, snapshot.LEDGER_PATH, snapshot.INDEX_DIR)
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        snapshot.DATA_DIR = tmp / "data"
        snapshot.LEDGER_PATH = tmp / "ledger.json"
        snapshot.INDEX_DIR = tmp / "index"

    def tearDown(self):
        snapshot.DATA_DIR, snapshot.LEDGER_PATH, snapshot.INDEX_DIR = self._original
        self._tmp.cleanup()

    def archive(self, date, records, provenance="genesis_from_gp", delta=None):
        manifest = snapshot.save_snapshot(
            date, snapshot.canonicalize(records), records, provenance, "test", ["/x"], delta=delta
        )
        snapshot.update_ledger(manifest)
        return manifest

    def write_arweave_receipt(self, date, tx="TX123", bundle_sha="abc"):
        snapshot.write_json(
            snapshot.snapshot_dir(date) / "storage.json",
            {
                "date": date,
                "bundle_sha256": bundle_sha,
                "destinations": {
                    "arweave": {
                        "status": "confirmed",
                        "transaction_id": tx,
                        "transaction_url": f"https://arweave.net/{tx}",
                        "bundle_sha256": bundle_sha,
                    }
                },
            },
        )


class BuildIndexTests(IndexBuildBase):
    def test_chunks_manifest_and_latest_entry(self):
        records = [gp_record("1"), gp_record("2", "1.0", "ROCKET BODY"), gp_record("3", "15.0", "DEBRIS", decay="2025-06-01")]
        for date in ("2025-12-31", "2026-01-01", "2026-01-02"):
            self.archive(date, records)
        manifest = snapshot.build_index("OMPub/RSO", "node")

        self.assertEqual(manifest["schema"], "rso-index-v1")
        self.assertEqual(manifest["latest"], "2026-01-02")
        self.assertEqual(manifest["first"], "2025-12-31")
        self.assertEqual(manifest["day_count"], 3)
        self.assertEqual([c["year"] for c in manifest["chunks"]], ["2025", "2026"])

        for chunk in manifest["chunks"]:
            chunk_path = snapshot.INDEX_DIR / f"{chunk['year']}.json"
            self.assertTrue(chunk_path.exists())
            self.assertEqual(snapshot.sha256_path(chunk_path), chunk["sha256"])
            entries = json.loads(chunk_path.read_text())
            self.assertEqual(len(entries), chunk["count"])
            self.assertEqual(entries[0]["date"], chunk["first"])
            self.assertEqual(entries[-1]["date"], chunk["last"])

        latest = manifest["latestEntry"]
        self.assertEqual(latest["date"], "2026-01-02")
        self.assertEqual(latest["on_orbit_count"], 2)
        self.assertEqual(latest["reentered_count"], 1)
        self.assertEqual(latest["band_counts"], {"leo": 1, "meo": 0, "geo": 1})

    def test_entries_are_lean(self):
        self.archive("2026-01-01", [gp_record("1")])
        manifest = snapshot.build_index("OMPub/RSO", "node")
        entry = json.loads((snapshot.INDEX_DIR / "2026.json").read_text())[0]
        allowed = set(snapshot.INDEX_ENTRY_FIELDS) | {"catalog_url", "catalog_url_kind"}
        self.assertEqual(set(entry).difference(allowed), set())
        # heavy ledger/manifest fields must not leak into the lean index
        for heavy in ("raw_bytes", "archived_at", "query_strategy", "cutoff_utc", "asset_url"):
            self.assertNotIn(heavy, entry)

    def test_locator_prefers_confirmed_arweave_then_node_branch(self):
        self.archive("2026-01-01", [gp_record("1")])  # no receipt -> node-branch
        self.archive("2026-01-02", [gp_record("1")])
        self.write_arweave_receipt("2026-01-02", tx="TXPERM")
        snapshot.build_index("OMPub/RSO", "node")
        entries = {e["date"]: e for e in json.loads((snapshot.INDEX_DIR / "2026.json").read_text())}

        self.assertEqual(entries["2026-01-02"]["catalog_url"], "https://arweave.net/TXPERM")
        self.assertEqual(entries["2026-01-02"]["catalog_url_kind"], "bundle_tar")

        self.assertEqual(
            entries["2026-01-01"]["catalog_url"],
            "https://raw.githubusercontent.com/OMPub/RSO/node/data/2026/01/01/catalog.json.gz",
        )
        self.assertEqual(entries["2026-01-01"]["catalog_url_kind"], "catalog_gz")
        # never the release asset
        self.assertNotIn("releases/download", entries["2026-01-02"]["catalog_url"])

    def test_stale_year_chunk_removed(self):
        self.archive("2025-01-01", [gp_record("1")])
        self.archive("2026-01-01", [gp_record("1")])
        snapshot.build_index("OMPub/RSO", "node")
        self.assertTrue((snapshot.INDEX_DIR / "2025.json").exists())
        # drop the 2025 day, rebuild -> 2025 chunk should be pruned
        import shutil
        shutil.rmtree(snapshot.snapshot_dir("2025-01-01"))
        snapshot.build_index("OMPub/RSO", "node")
        self.assertFalse((snapshot.INDEX_DIR / "2025.json").exists())
        self.assertTrue((snapshot.INDEX_DIR / "2026.json").exists())

    def test_empty_archive_raises(self):
        with self.assertRaises(snapshot.SnapshotError):
            snapshot.build_index("OMPub/RSO", "node")


class ArweaveMirrorTests(IndexBuildBase):
    def test_mirror_records_tx_per_chunk_and_manifest(self):
        self.archive("2025-06-01", [gp_record("1")])
        self.archive("2026-01-01", [gp_record("1")])
        snapshot.build_index("OMPub/RSO", "node")

        uploaded = []

        def fake_upload(path, **kwargs):
            stem = Path(path).stem
            uploaded.append(stem)
            return {"status": "confirmed", "transaction_id": f"TX-{stem}",
                    "transaction_url": f"https://arweave.net/TX-{stem}"}

        result = snapshot.mirror_index_to_arweave(snapshot.INDEX_DIR, upload=fake_upload)
        self.assertIsNotNone(result)
        chunks = result["arweave"]["chunks"]
        self.assertEqual(chunks["index/2025.json"]["transaction_id"], "TX-2025")
        self.assertEqual(chunks["index/2026.json"]["transaction_id"], "TX-2026")
        self.assertIn("manifest", result["arweave"])
        # persisted to disk
        on_disk = json.loads((snapshot.INDEX_DIR / "manifest.json").read_text())
        self.assertEqual(on_disk["arweave"]["chunks"]["index/2026.json"]["transaction_id"], "TX-2026")

    def test_mirror_without_wallet_is_noop(self):
        self.archive("2026-01-01", [gp_record("1")])
        snapshot.build_index("OMPub/RSO", "node")
        self.assertIsNone(
            snapshot.mirror_index_to_arweave(
                snapshot.INDEX_DIR, upload=lambda p, **k: {"status": "skipped", "reason": "missing_wallet"}
            )
        )

    def test_arweave_upload_file_with_injected_primitives(self):
        path = snapshot.INDEX_DIR
        path.mkdir(parents=True, exist_ok=True)
        target = path / "manifest.json"
        target.write_text("{}")

        captured = {}

        def fake_build(bundle, jwk, tags=None):
            captured["tags"] = tags
            captured["path"] = bundle["path"]
            return {"transaction": {"id": "TXabc"}, "inline_data": True,
                    "chunk_plan": {"chunks": [b"x"]}, "wallet_address": "addr"}

        result = snapshot.arweave_upload_file(
            target,
            content_type="application/json",
            extra_tags=[("RSO-Index", "manifest")],
            jwk={"kty": "RSA"},
            build=fake_build,
            submit_transaction=lambda upload: None,
            submit_chunks=lambda upload: None,
            query_status=lambda tx_id: (200, {"block_height": 7}),
        )
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(result["transaction_id"], "TXabc")
        self.assertTrue(result["transaction_url"].endswith("/TXabc"))
        # tags were JSON/index appropriate (base64url-encoded objects present)
        self.assertTrue(captured["tags"] and all("name" in t and "value" in t for t in captured["tags"]))

    def test_arweave_upload_file_without_wallet_skips(self):
        saved = {k: os.environ.pop(k) for k in ("ARWEAVE_JWK", "ARWEAVE_WALLET_JSON") if k in os.environ}
        try:
            target = snapshot.INDEX_DIR
            target.mkdir(parents=True, exist_ok=True)
            f = target / "manifest.json"
            f.write_text("{}")
            self.assertEqual(snapshot.arweave_upload_file(f)["status"], "skipped")
        finally:
            os.environ.update(saved)


if __name__ == "__main__":
    unittest.main()
