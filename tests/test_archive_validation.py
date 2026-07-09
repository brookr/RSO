import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

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


class ArchiveValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_data_dir = snapshot.DATA_DIR
        self.original_ledger_path = snapshot.LEDGER_PATH
        root = Path(self.tmp.name)
        snapshot.DATA_DIR = root / "data"
        snapshot.LEDGER_PATH = root / "ledger.json"

    def tearDown(self):
        snapshot.DATA_DIR = self.original_data_dir
        snapshot.LEDGER_PATH = self.original_ledger_path
        self.tmp.cleanup()

    def archive_genesis(self, date="2026-04-12", records=None):
        if records is None:
            records = [gp_record("1"), gp_record("2")]
        data = sorted(records, key=snapshot.catalog_id_sort_key)
        manifest = snapshot.save_snapshot(
            date,
            snapshot.canonicalize(data),
            data,
            "genesis_from_gp",
            "current_gp_genesis",
            ["/class/gp/orderby/NORAD_CAT_ID%20asc/format/json"],
            observed_at_utc=f"{date}T00:15:00Z",
            state_as_of_utc=f"{date}T00:15:00Z",
        )
        snapshot.update_ledger(manifest)
        return manifest

    def test_validate_archive_accepts_valid_genesis(self):
        self.archive_genesis()

        snapshot.validate_archive(min_count=1)

    def test_validate_archive_rejects_manifest_hash_mismatch(self):
        self.archive_genesis()
        manifest_path = snapshot.snapshot_dir("2026-04-12") / "manifest.json"
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
        manifest["sha256"] = "0" * 64
        snapshot.write_json(manifest_path, manifest)

        with self.assertRaises(snapshot.SnapshotError) as raised:
            snapshot.validate_archive(min_count=1)

        self.assertIn("sha256 mismatch", str(raised.exception))

    def test_validate_archive_checks_audit_counts_when_present(self):
        self.archive_genesis()
        day_dir = snapshot.snapshot_dir("2026-04-12")
        snapshot.write_json(
            day_dir / "audit.json",
            {
                "date": "2026-04-12",
                "observed_at_utc": "2026-04-12T00:15:00Z",
                "archive_object_count": 2,
                "missing_from_current_gp_count": 1,
                "missing_from_current_gp": [],
                "present_in_current_gp_not_in_archive_count": 0,
                "present_in_current_gp_not_in_archive": [],
                "reappeared_in_current_gp_count": 0,
                "reappeared_in_current_gp": [],
            },
        )

        with self.assertRaises(snapshot.SnapshotError) as raised:
            snapshot.validate_archive(min_count=1)

        self.assertIn("missing_from_current_gp_count", str(raised.exception))

    def test_validate_archive_rejects_genesis_with_stale_delta(self):
        self.archive_genesis()
        snapshot.write_json(
            snapshot.snapshot_dir("2026-04-12") / "delta.json",
            {"date": "2026-04-12"},
        )

        with self.assertRaises(snapshot.SnapshotError) as raised:
            snapshot.validate_archive(min_count=1)

        self.assertIn("genesis snapshot must not include delta.json", str(raised.exception))

    def test_validate_archive_allows_missing_catalog_by_default(self):
        self.archive_genesis()
        (snapshot.snapshot_dir("2026-04-12") / "catalog.json.gz").unlink()

        snapshot.validate_archive(min_count=1)

    def test_validate_archive_can_require_local_catalog(self):
        self.archive_genesis()
        (snapshot.snapshot_dir("2026-04-12") / "catalog.json.gz").unlink()

        with self.assertRaises(snapshot.SnapshotError) as raised:
            snapshot.validate_archive(min_count=1, require_catalog=True)

        self.assertIn("missing catalog.json.gz", str(raised.exception))

    def test_validate_archive_can_require_latest_local_catalogs(self):
        self.archive_genesis("2026-04-12")
        self.archive_genesis("2026-04-13")
        (snapshot.snapshot_dir("2026-04-13") / "catalog.json.gz").unlink()

        with self.assertRaises(snapshot.SnapshotError) as raised:
            snapshot.validate_archive(min_count=1, require_latest_catalogs=2)

        self.assertIn("missing catalog.json.gz", str(raised.exception))

    def test_prune_catalogs_keeps_latest_selected_dates(self):
        self.archive_genesis("2026-04-12")
        self.archive_genesis("2026-04-13")
        self.archive_genesis("2026-04-14")

        snapshot.process_prune_catalogs(
            SimpleNamespace(
                all=True,
                date=None,
                start=None,
                end=None,
                require_bundle=False,
                output_dir=Path(self.tmp.name) / ".release",
                keep_latest=2,
            )
        )

        self.assertFalse((snapshot.snapshot_dir("2026-04-12") / "catalog.json.gz").exists())
        self.assertTrue((snapshot.snapshot_dir("2026-04-13") / "catalog.json.gz").exists())
        self.assertTrue((snapshot.snapshot_dir("2026-04-14") / "catalog.json.gz").exists())

    def test_prune_require_bundle_prunes_when_bundle_is_buildable(self):
        # --require-bundle prunes a day whose release bundle can be built (its
        # catalog re-verifies against the manifest) — this is the build -> prune
        # -> publish daily flow, where prune precedes the same run's publish.
        self.archive_genesis("2026-04-12")
        self.archive_genesis("2026-04-13")

        # the daily pre-builds bundles before pruning; only 2026-04-12 is pruned
        # (keep_latest=1 retains 2026-04-13).
        out = Path(self.tmp.name) / ".release"
        snapshot.build_release_bundle("2026-04-12", output_dir=out, min_count=1)

        snapshot.process_prune_catalogs(
            SimpleNamespace(
                all=True,
                date=None,
                start=None,
                end=None,
                require_bundle=True,
                output_dir=out,
                keep_latest=1,
            )
        )
        # the older day's catalog was pruned; the retained (latest) day kept
        self.assertFalse((snapshot.snapshot_dir("2026-04-12") / "catalog.json.gz").exists())
        self.assertTrue((snapshot.snapshot_dir("2026-04-13") / "catalog.json.gz").exists())

    def test_update_pointers_ignores_stray_non_date_dirs(self):
        self.archive_genesis("2026-04-12")
        stray = snapshot.DATA_DIR / "2026" / "04" / "notes"
        stray.mkdir(parents=True)
        (stray / "manifest.json").write_text("{}", encoding="utf-8")

        original_latest = snapshot.LATEST_POINTER_PATH
        try:
            snapshot.LATEST_POINTER_PATH = Path(self.tmp.name) / "latest.json"
            snapshot.process_update_pointers(SimpleNamespace(start=None, end=None))
        finally:
            snapshot.LATEST_POINTER_PATH = original_latest

        ledger = json.loads(Path(snapshot.LEDGER_PATH).read_text(encoding="utf-8"))
        self.assertEqual([entry["date"] for entry in ledger], ["2026-04-12"])

    def test_next_unarchived_date_returns_day_after_latest(self):
        self.archive_genesis("2026-04-12")

        self.assertEqual(snapshot.next_unarchived_date("2026-04-14"), "2026-04-13")

    def test_next_unarchived_date_is_capped_at_end(self):
        self.archive_genesis("2026-04-12")

        self.assertEqual(snapshot.next_unarchived_date("2026-04-12"), "2026-04-12")

    def test_validate_archive_rejects_rolling_snapshot_with_wrong_base_hash(self):
        self.archive_genesis()
        records = [gp_record("1"), gp_record("2")]
        manifest = snapshot.save_snapshot(
            "2026-04-13",
            snapshot.canonicalize(records),
            records,
            "rolling_gp_history_delta",
            "prior_snapshot_plus_bounded_gp_history_delta",
            ["/class/gp_history/format/json"],
            base_snapshot_date="2026-04-12",
            base_snapshot_sha256="0" * 64,
            delta_window_start_utc="2026-04-12T00:00:00Z",
            delta_window_end_utc="2026-04-13T00:00:00Z",
        )
        snapshot.update_ledger(manifest)
        snapshot.write_json(
            snapshot.snapshot_dir("2026-04-13") / "delta.json",
            {
                "date": "2026-04-13",
                "window_start_utc": "2026-04-12T00:00:00Z",
                "window_end_utc": "2026-04-13T00:00:00Z",
                "new_object_count": 0,
                "new_norad_cat_ids": [],
                "updated_object_count": 0,
                "updated_norad_cat_ids": [],
                "unchanged_update_count": 0,
                "unchanged_update_norad_cat_ids": [],
                "ignored_older_update_count": 0,
                "ignored_older_update_norad_cat_ids": [],
                "deduped_update_count": 0,
            },
        )

        with self.assertRaises(snapshot.SnapshotError) as raised:
            snapshot.validate_archive(min_count=1)

        self.assertIn("base_snapshot_sha256 does not match", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
