"""Card-parity aggregates carried in the manifest/ledger, and validator parity.

These cover the on-orbit/re-entered split plus band_counts/type_counts/delta that
let the card render the HUD with zero catalog download. The definitions mirror
card/index.html (recordMeta/applyHud, bandFromMM, klassFromType) exactly.
"""

import json
import tempfile
import unittest
from pathlib import Path

from pipeline import snapshot


def gp_record(cat_id="1", mean_motion="15.0", object_type="PAYLOAD", decay=None):
    record = {
        "NORAD_CAT_ID": cat_id,
        "CREATION_DATE": "2026-04-12T05:00:00",
        "EPOCH": "2026-04-12T04:00:00",
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


class ClassifyTests(unittest.TestCase):
    def test_band_from_mean_motion_mirrors_card(self):
        # GEO 0.9..1.1, LEO >=11.0, else MEO (incl. non-finite / non-positive)
        self.assertEqual(snapshot.classify_band("1.0"), "geo")
        self.assertEqual(snapshot.classify_band("0.9"), "geo")
        self.assertEqual(snapshot.classify_band("1.1"), "geo")
        self.assertEqual(snapshot.classify_band("15.5"), "leo")
        self.assertEqual(snapshot.classify_band("11.0"), "leo")
        self.assertEqual(snapshot.classify_band("5.0"), "meo")
        self.assertEqual(snapshot.classify_band("0.5"), "meo")
        self.assertEqual(snapshot.classify_band("not-a-number"), "meo")
        self.assertEqual(snapshot.classify_band(None), "meo")
        self.assertEqual(snapshot.classify_band("0"), "meo")

    def test_type_from_object_type_mirrors_card(self):
        self.assertEqual(snapshot.classify_type("PAYLOAD"), "payload")
        self.assertEqual(snapshot.classify_type("ROCKET BODY"), "rocket")
        self.assertEqual(snapshot.classify_type("DEBRIS"), "debris")
        self.assertEqual(snapshot.classify_type("TBA - TO BE ASSIGNED"), "unknown")
        self.assertEqual(snapshot.classify_type(None), "unknown")
        # substring precedence: DEBRIS before ROCKET before PAYLOAD
        self.assertEqual(snapshot.classify_type("ROCKET DEBRIS"), "debris")


class AggregateCountsTests(unittest.TestCase):
    def test_split_and_breakdowns_match_card_definition(self):
        records = [
            gp_record("1", "15.5", "PAYLOAD"),                      # leo payload, on orbit
            gp_record("2", "1.0", "ROCKET BODY"),                   # geo rocket, on orbit
            gp_record("3", "2.0", "DEBRIS"),                        # meo debris, on orbit
            gp_record("4", "bad", "TBA"),                           # meo unknown, on orbit
            gp_record("5", "15.0", "PAYLOAD", decay="2026-04-12"),  # decayed today
            gp_record("6", "15.0", "PAYLOAD", decay="2025-01-01"),  # decayed earlier
            gp_record("7", "15.0", "PAYLOAD", decay="2026-04-13"),  # future decay -> still on orbit
        ]
        agg = snapshot.aggregate_counts(
            records, "2026-04-12", delta={"updated_object_count": 7, "new_object_count": 3}
        )
        self.assertEqual(agg["reentered_count"], 2)  # ids 5,6 (decay <= date)
        self.assertEqual(agg["on_orbit_count"], 5)
        self.assertEqual(agg["band_counts"], {"leo": 2, "meo": 2, "geo": 1})
        self.assertEqual(
            agg["type_counts"],
            {"payload": 2, "rocket": 1, "debris": 1, "unknown": 1, "tba": 0},
        )
        self.assertEqual(agg["delta"], {"updated": 7, "new": 3, "decayed": 1})

    def test_totals_are_consistent(self):
        records = [gp_record(str(i), decay=("2025-01-01" if i % 3 == 0 else None)) for i in range(1, 31)]
        agg = snapshot.aggregate_counts(records, "2026-04-12")
        self.assertEqual(agg["on_orbit_count"] + agg["reentered_count"], len(records))
        self.assertEqual(sum(agg["band_counts"].values()), agg["on_orbit_count"])
        self.assertEqual(sum(agg["type_counts"].values()), agg["on_orbit_count"])

    def test_delta_falls_back_to_deduped_and_zero(self):
        records = [gp_record("1")]
        self.assertEqual(
            snapshot.aggregate_counts(records, "2026-04-12", delta={"deduped_update_count": 9})["delta"]["updated"],
            9,
        )
        self.assertEqual(
            snapshot.aggregate_counts(records, "2026-04-12")["delta"],
            {"updated": 0, "new": 0, "decayed": 0},
        )

    def test_decay_equal_to_date_counts_as_reentered(self):
        # The card's `reentered = decay <= date` includes the decay==date day.
        agg = snapshot.aggregate_counts([gp_record("1", decay="2026-04-12")], "2026-04-12")
        self.assertEqual(agg["reentered_count"], 1)
        self.assertEqual(agg["on_orbit_count"], 0)
        self.assertEqual(agg["delta"]["decayed"], 1)


class ManifestAndLedgerFlowTests(unittest.TestCase):
    def _save(self, tmp, date, records, delta=None):
        snapshot.DATA_DIR = Path(tmp) / "data"
        return snapshot.save_snapshot(
            date,
            snapshot.canonicalize(records),
            records,
            "rolling_gp_history_delta",
            "test",
            ["/class/gp_history/format/json"],
            delta=delta,
        )

    def test_manifest_carries_all_aggregate_fields(self):
        original = snapshot.DATA_DIR
        with tempfile.TemporaryDirectory() as tmp:
            try:
                records = [
                    gp_record("1", "15.0", "PAYLOAD"),
                    gp_record("2", "1.0", "ROCKET BODY"),
                    gp_record("3", "15.0", "DEBRIS", decay="2026-04-12"),
                ]
                manifest = self._save(
                    tmp, "2026-04-12", records,
                    delta={"updated_object_count": 4, "new_object_count": 1},
                )
                self.assertEqual(manifest["on_orbit_count"], 2)
                self.assertEqual(manifest["reentered_count"], 1)
                self.assertEqual(manifest["band_counts"], {"leo": 1, "meo": 0, "geo": 1})
                self.assertEqual(
                    manifest["type_counts"],
                    {"payload": 1, "rocket": 1, "debris": 0, "unknown": 0, "tba": 0},
                )
                self.assertEqual(manifest["delta"], {"updated": 4, "new": 1, "decayed": 1})
                self.assertEqual(
                    manifest["on_orbit_count"] + manifest["reentered_count"],
                    manifest["object_count"],
                )
            finally:
                snapshot.DATA_DIR = original

    def test_ledger_entry_mirrors_manifest_aggregates(self):
        original_data, original_ledger = snapshot.DATA_DIR, snapshot.LEDGER_PATH
        with tempfile.TemporaryDirectory() as tmp:
            try:
                snapshot.LEDGER_PATH = Path(tmp) / "ledger.json"
                records = [gp_record("1"), gp_record("2", "1.0", "ROCKET BODY")]
                manifest = self._save(tmp, "2026-04-12", records, delta={"new_object_count": 2})
                entry = snapshot.ledger_entry_from_manifest(manifest)
                for key in ("on_orbit_count", "reentered_count", "band_counts", "type_counts", "delta"):
                    self.assertEqual(entry[key], manifest[key], key)
                snapshot.update_ledger(manifest)
                ledger = json.loads(Path(snapshot.LEDGER_PATH).read_text())
                self.assertEqual(ledger[0]["band_counts"], manifest["band_counts"])
                self.assertEqual(ledger[0]["delta"], manifest["delta"])
            finally:
                snapshot.DATA_DIR, snapshot.LEDGER_PATH = original_data, original_ledger

    def test_legacy_manifest_without_aggregates_still_produces_entry(self):
        legacy = {
            "date": "2026-04-12", "sha256": "x", "object_count": 5, "compressed_bytes": 1,
            "provenance": "genesis_from_gp", "query_strategy": "q", "archived_at": "t",
        }
        entry = snapshot.ledger_entry_from_manifest(legacy)
        for key in ("on_orbit_count", "band_counts", "type_counts", "delta"):
            self.assertNotIn(key, entry)


class ValidatorParityTests(unittest.TestCase):
    def _archive_day(self, tmp, date, records, delta=None):
        snapshot.DATA_DIR = Path(tmp) / "data"
        snapshot.LEDGER_PATH = Path(tmp) / "ledger.json"
        manifest = snapshot.save_snapshot(
            date, snapshot.canonicalize(records), records,
            "genesis_from_gp" if delta is None else "rolling_gp_history_delta",
            "test", ["/x"], delta=delta,
        )
        if delta is not None:
            snapshot.save_artifacts(date, delta=delta)
        snapshot.update_ledger(manifest)
        return manifest

    def test_clean_day_validates(self):
        original = (snapshot.DATA_DIR, snapshot.LEDGER_PATH)
        with tempfile.TemporaryDirectory() as tmp:
            try:
                records = [gp_record(str(i)) for i in range(1, 6)]
                self._archive_day(tmp, "2026-04-12", records)
                errors, _ = snapshot.validate_snapshot_artifacts("2026-04-12", min_count=0)
                self.assertEqual(errors, [])
            finally:
                snapshot.DATA_DIR, snapshot.LEDGER_PATH = original

    def test_catalog_recompute_catches_tampered_aggregate(self):
        original = (snapshot.DATA_DIR, snapshot.LEDGER_PATH)
        with tempfile.TemporaryDirectory() as tmp:
            try:
                records = [gp_record("1", "15.0"), gp_record("2", "1.0", "ROCKET BODY")]
                manifest = self._archive_day(tmp, "2026-04-12", records)
                manifest_path = snapshot.snapshot_dir("2026-04-12") / "manifest.json"
                tampered = json.loads(manifest_path.read_text())
                tampered["band_counts"] = {"leo": 99, "meo": 0, "geo": 0}  # lie
                snapshot.write_json(manifest_path, tampered)
                errors, _ = snapshot.validate_snapshot_artifacts("2026-04-12", min_count=0)
                self.assertTrue(any("band_counts" in e and "catalog yields" in e for e in errors), errors)
            finally:
                snapshot.DATA_DIR, snapshot.LEDGER_PATH = original

    def test_validate_ledger_catches_manifest_ledger_mismatch(self):
        original = (snapshot.DATA_DIR, snapshot.LEDGER_PATH)
        with tempfile.TemporaryDirectory() as tmp:
            try:
                records = [gp_record("1"), gp_record("2", "1.0", "ROCKET BODY")]
                manifest = self._archive_day(tmp, "2026-04-12", records)
                ledger = json.loads(Path(snapshot.LEDGER_PATH).read_text())
                ledger[0]["on_orbit_count"] = ledger[0]["on_orbit_count"] + 1  # diverge from manifest
                snapshot.write_json(snapshot.LEDGER_PATH, ledger)
                errors = snapshot.validate_ledger({"2026-04-12": manifest})
                self.assertTrue(any("on_orbit_count" in e for e in errors), errors)
            finally:
                snapshot.DATA_DIR, snapshot.LEDGER_PATH = original


class BackfillTests(unittest.TestCase):
    """rebuild-content backfills aggregates onto an already-rebuilt day without
    re-deriving annotations (so published bundles stay byte-identical)."""

    def test_rebuild_content_tops_up_missing_aggregates(self):
        import types

        original = (snapshot.DATA_DIR, snapshot.LEDGER_PATH)
        with tempfile.TemporaryDirectory() as tmp:
            try:
                snapshot.DATA_DIR = Path(tmp) / "data"
                snapshot.LEDGER_PATH = Path(tmp) / "ledger.json"
                date = "2026-04-12"
                records = [
                    gp_record("1", "15.0", "PAYLOAD"),
                    gp_record("2", "1.0", "ROCKET BODY"),
                    gp_record("3", "15.0", "DEBRIS", decay="2025-01-01"),
                ]
                # Archive a day that already carries content fields + annotations,
                # as a pre-aggregate rebuild would have.
                snapshot.save_snapshot(
                    date, snapshot.canonicalize(records), records,
                    "genesis_from_gp", "test", ["/x"],
                    annotations={"schema": "rso-annotations-v2", "date": date, "catalog_changes": []},
                )
                manifest_path = snapshot.snapshot_dir(date) / "manifest.json"
                manifest = json.loads(manifest_path.read_text())
                annotations_sha_before = manifest["annotations_sha256"]
                # Strip the aggregate fields to simulate a manifest from before this change.
                for key in snapshot.AGGREGATE_MANIFEST_FIELDS:
                    manifest.pop(key, None)
                snapshot.write_json(manifest_path, manifest)

                args = types.SimpleNamespace(start=date, end=date, force=False)
                snapshot.process_rebuild_content(args)

                rebuilt = json.loads(manifest_path.read_text())
                self.assertEqual(rebuilt["on_orbit_count"], 2)
                self.assertEqual(rebuilt["reentered_count"], 1)
                self.assertEqual(rebuilt["band_counts"], {"leo": 1, "meo": 0, "geo": 1})
                self.assertIn("delta", rebuilt)
                # annotations untouched -> byte-identical bundle preserved
                self.assertEqual(rebuilt["annotations_sha256"], annotations_sha_before)
                # ledger gained the aggregates too
                ledger = json.loads(Path(snapshot.LEDGER_PATH).read_text())
                self.assertEqual(ledger[0]["on_orbit_count"], 2)

                # Idempotent: a second pass finds nothing to change.
                self.assertFalse(
                    snapshot.backfill_manifest_aggregates(rebuilt, date, records)
                )
            finally:
                snapshot.DATA_DIR, snapshot.LEDGER_PATH = original


class AnnotationSummaryTests(unittest.TestCase):
    """The lens-4 legend counts (directory / TIP / decay) precomputed into the manifest, ledger
    and index so the daily-changes legend is instant the whole timeline with no catalog download."""

    def _annotations(self):
        return {
            "catalog_changes": [
                {"norad_cat_id": "1", "field": "OBJECT_NAME"},
                {"norad_cat_id": "1", "field": "RCS_SIZE"},   # same object → one directory change
                {"norad_cat_id": "2", "field": "OBJECT_NAME"},
            ],
            "tip_messages": [{"NORAD_CAT_ID": "5"}, {"NORAD_CAT_ID": "6"}],
            "decay_messages": [{"NORAD_CAT_ID": "7"}],
        }

    def test_summary_counts_distinct_objects_and_feed_lengths(self):
        summary = snapshot.annotation_summary(self._annotations())
        self.assertEqual(summary, {"directory_changes": 2, "tip_count": 2, "decay_notices": 1})

    def test_summary_is_zero_for_missing_or_empty(self):
        self.assertEqual(
            snapshot.annotation_summary(None),
            {"directory_changes": 0, "tip_count": 0, "decay_notices": 0},
        )

    def test_manifest_ledger_and_index_carry_anno_summary(self):
        original = (snapshot.DATA_DIR, snapshot.INDEX_DIR)
        with tempfile.TemporaryDirectory() as tmp:
            try:
                snapshot.DATA_DIR = Path(tmp) / "data"
                snapshot.INDEX_DIR = Path(tmp) / "index"
                records = [gp_record("1"), gp_record("2", "1.0", "ROCKET BODY")]
                manifest = snapshot.save_snapshot(
                    "2026-04-12", snapshot.canonicalize(records), records,
                    "rolling_gp_history_delta", "test", ["/x"],
                    annotations=self._annotations(),
                )
                self.assertEqual(
                    manifest["anno_summary"],
                    {"directory_changes": 2, "tip_count": 2, "decay_notices": 1},
                )
                entry = snapshot.ledger_entry_from_manifest(manifest)
                self.assertEqual(entry["anno_summary"], manifest["anno_summary"])
                index = snapshot.build_index("OMPub/RSO", "node")
                self.assertEqual(index["latestEntry"]["anno_summary"], manifest["anno_summary"])
                # content_schema is now self-describing in the index entry too
                self.assertEqual(index["latestEntry"]["content_schema"], snapshot.CONTENT_SCHEMA)
            finally:
                snapshot.DATA_DIR, snapshot.INDEX_DIR = original

    def test_backfill_adds_anno_summary_from_annotations_file(self):
        original = snapshot.DATA_DIR
        with tempfile.TemporaryDirectory() as tmp:
            try:
                snapshot.DATA_DIR = Path(tmp) / "data"
                records = [gp_record("1")]
                day = "2026-04-12"
                snapshot.save_snapshot(
                    day, snapshot.canonicalize(records), records,
                    "rolling_gp_history_delta", "test", ["/x"],
                    annotations=self._annotations(),
                )
                # a manifest archived before anno_summary existed gets it back, byte-safe
                stale = snapshot.read_json_if_exists(snapshot.snapshot_dir(day) / "manifest.json")
                stale.pop("anno_summary")
                self.assertTrue(snapshot.backfill_manifest_aggregates(stale, day, records))
                self.assertEqual(
                    stale["anno_summary"],
                    {"directory_changes": 2, "tip_count": 2, "decay_notices": 1},
                )
            finally:
                snapshot.DATA_DIR = original

    def test_reentered_object_count_helper_is_gone(self):
        # deleted as dead code by the deep review; aggregate_counts is the single source
        self.assertFalse(hasattr(snapshot, "reentered_object_count"))


if __name__ == "__main__":
    unittest.main()
