"""Tests for the v2 consensus/observation split.

The consensus contentHash must be invariant to every field Space-Track
back-patches in place (measured 2026-06-09 across 50 archived windows), while
the observation plane records exactly what changed and when we learned it.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "pipeline")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import snapshot  # noqa: E402
from attestation import rso_attestation  # noqa: E402
from indexer import rso_profile  # noqa: E402


def gp_record(**overrides):
    record = {
        "NORAD_CAT_ID": "69179",
        "GP_ID": "327741899",
        "CREATION_DATE": "2026-06-07T13:26:16",
        "EPOCH": "2026-06-07T12:15:59.925600",
        "MEAN_MOTION": "16.40545372",
        "ECCENTRICITY": "0.00050000",
        "INCLINATION": "97.50000000",
        "RA_OF_ASC_NODE": "10.00000000",
        "ARG_OF_PERICENTER": "20.00000000",
        "MEAN_ANOMALY": "30.00000000",
        "ELEMENT_SET_NO": "999",
        "REV_AT_EPOCH": "255",
        "BSTAR": "0.00100000000000",
        "TLE_LINE1": "1 69179U 26112C   26158.51111025  .00000000  00000-0  10000-3 0  9991",
        "TLE_LINE2": "2 69179  97.5000  10.0000 0005000  20.0000  30.0000 16.40545372  2550",
        # the mutable object-directory family
        "OBJECT_NAME": "ELECTRON R/B",
        "OBJECT_TYPE": "ROCKET BODY",
        "OBJECT_ID": "2026-112C",
        "COUNTRY_CODE": "US",
        "LAUNCH_DATE": "2026-05-22",
        "SITE": "RLLB",
        "RCS_SIZE": "LARGE",
        "DECAY_DATE": None,
        "TLE_LINE0": "0 ELECTRON R/B",
    }
    record.update(overrides)
    return record


class CoreProjectionTest(unittest.TestCase):
    def test_excluded_fields_are_exactly_the_object_directory_family(self):
        self.assertEqual(
            snapshot.CONTENT_EXCLUDED_FIELDS,
            (
                "COUNTRY_CODE",
                "DECAY_DATE",
                "LAUNCH_DATE",
                "OBJECT_ID",
                "OBJECT_NAME",
                "OBJECT_TYPE",
                "RCS_SIZE",
                "SITE",
                "TLE_LINE0",
            ),
        )

    def test_core_record_drops_only_excluded_fields(self):
        record = gp_record()
        core = snapshot.core_record(record)

        for field in snapshot.CONTENT_EXCLUDED_FIELDS:
            self.assertNotIn(field, core)
        for field in record:
            if field not in snapshot.CONTENT_EXCLUDED_FIELDS:
                self.assertEqual(core[field], record[field])

    def test_core_hash_invariant_to_back_patched_fields(self):
        # The 06-08 two-node divergence in miniature: same elsets, one capture
        # taken before and one after Space-Track stamped DECAY_DATE and named a
        # TBA object. The consensus hash must not see the difference.
        early = [
            gp_record(),
            gp_record(
                NORAD_CAT_ID="68826",
                GP_ID="327000001",
                OBJECT_NAME="TBA - TO BE ASSIGNED",
                OBJECT_TYPE="UNKNOWN",
                TLE_LINE0="0 TBA - TO BE ASSIGNED",
            ),
        ]
        late = [
            gp_record(DECAY_DATE="2026-06-07"),
            gp_record(
                NORAD_CAT_ID="68826",
                GP_ID="327000001",
                OBJECT_NAME="COSMOS 2615",
                OBJECT_TYPE="PAYLOAD",
                TLE_LINE0="0 COSMOS 2615",
                COUNTRY_CODE="CIS",
                RCS_SIZE="MEDIUM",
            ),
        ]

        self.assertNotEqual(
            snapshot.compute_hash(snapshot.canonicalize(early)),
            snapshot.compute_hash(snapshot.canonicalize(late)),
        )
        self.assertEqual(
            snapshot.core_content_sha256(early),
            snapshot.core_content_sha256(late),
        )

    def test_core_hash_sensitive_to_elset_intrinsic_fields(self):
        baseline = [gp_record()]
        for field, value in (
            ("EPOCH", "2026-06-07T13:00:00.000000"),
            ("MEAN_MOTION", "16.50000000"),
            ("GP_ID", "327741900"),
            ("CREATION_DATE", "2026-06-07T14:00:00"),
            ("TLE_LINE1", "1 69179U 26112C   26158.51111025  .00000001  00000-0  10000-3 0  9992"),
        ):
            with self.subTest(field=field):
                self.assertNotEqual(
                    snapshot.core_content_sha256(baseline),
                    snapshot.core_content_sha256([gp_record(**{field: value})]),
                )

    def test_core_projection_preserves_null_values(self):
        record = gp_record(MEAN_MOTION_DDOT=None)
        core = snapshot.core_record(record)
        self.assertIsNone(core["MEAN_MOTION_DDOT"])


class AnnotationsTest(unittest.TestCase):
    def test_catalog_changes_record_previous_and_current(self):
        previous = [gp_record()]
        current = [gp_record(DECAY_DATE="2026-06-07", GP_ID="327741899")]

        annotations = snapshot.build_annotations(
            "2026-06-08",
            current,
            previous,
            observed_at_utc="2026-06-08T01:00:00Z",
            window_start_utc="2026-06-07T00:00:00Z",
            window_end_utc="2026-06-08T00:00:00Z",
        )

        self.assertEqual(annotations["schema"], "rso-annotations-v1")
        self.assertEqual(annotations["date"], "2026-06-08")
        self.assertEqual(annotations["observed_at_utc"], "2026-06-08T01:00:00Z")
        self.assertEqual(annotations["fields"], list(snapshot.CONTENT_EXCLUDED_FIELDS))
        self.assertFalse(annotations["baseline"])
        self.assertEqual(
            annotations["catalog_changes"],
            [
                {
                    "norad_cat_id": "69179",
                    "field": "DECAY_DATE",
                    "previous": None,
                    "current": "2026-06-07",
                    "first_observation": False,
                }
            ],
        )

    def test_new_object_records_first_observations(self):
        annotations = snapshot.build_annotations(
            "2026-06-08",
            [gp_record()],
            [],
            observed_at_utc="2026-06-08T01:00:00Z",
        )

        changes = annotations["catalog_changes"]
        self.assertTrue(changes)
        self.assertTrue(all(item["first_observation"] for item in changes))
        self.assertTrue(all(item["previous"] is None for item in changes))
        # null current values on a new object are not observations
        self.assertNotIn("DECAY_DATE", [item["field"] for item in changes])

    def test_unchanged_records_produce_no_changes(self):
        records = [gp_record()]
        annotations = snapshot.build_annotations(
            "2026-06-08",
            records,
            [dict(r) for r in records],
            observed_at_utc="2026-06-08T01:00:00Z",
        )
        self.assertEqual(annotations["catalog_changes"], [])

    def test_changes_sorted_by_norad_then_field(self):
        previous = [
            gp_record(NORAD_CAT_ID="100", GP_ID="1"),
            gp_record(NORAD_CAT_ID="99", GP_ID="2"),
        ]
        current = [
            gp_record(NORAD_CAT_ID="100", GP_ID="1", OBJECT_NAME="B", OBJECT_TYPE="PAYLOAD"),
            gp_record(NORAD_CAT_ID="99", GP_ID="2", DECAY_DATE="2026-06-01"),
        ]
        annotations = snapshot.build_annotations(
            "2026-06-08", current, previous, observed_at_utc="2026-06-08T01:00:00Z"
        )
        keys = [(item["norad_cat_id"], item["field"]) for item in annotations["catalog_changes"]]
        self.assertEqual(
            keys, [("99", "DECAY_DATE"), ("100", "OBJECT_NAME"), ("100", "OBJECT_TYPE")]
        )

    def test_baseline_mode_emits_no_changes(self):
        annotations = snapshot.build_annotations(
            "2026-04-20",
            [gp_record()],
            None,
            observed_at_utc="2026-04-20T03:49:18Z",
            baseline=True,
        )
        self.assertTrue(annotations["baseline"])
        self.assertEqual(annotations["catalog_changes"], [])

    def test_satcat_and_decay_sections_pass_through(self):
        satcat = [{"NORAD_CAT_ID": "69179", "PREVIOUS_DECAY": None, "CURRENT_DECAY": "2026-06-07"}]
        decay = [{"NORAD_CAT_ID": "69179", "MSG_EPOCH": "2026-06-07 03:14:00", "MSG_TYPE": "Historical"}]
        annotations = snapshot.build_annotations(
            "2026-06-08",
            [gp_record()],
            [gp_record()],
            observed_at_utc="2026-06-08T01:00:00Z",
            satcat_changes=satcat,
            decay_messages=decay,
            query_paths=["/class/satcat_change/...", "/class/decay/..."],
        )
        self.assertEqual(annotations["satcat_changes"], satcat)
        self.assertEqual(annotations["decay_messages"], decay)
        self.assertEqual(len(annotations["api_query_paths"]), 2)

    def test_validate_annotation_rows_rejects_non_string_values(self):
        with self.assertRaises(snapshot.SnapshotError):
            snapshot.validate_annotation_rows(
                [{"NORAD_CAT_ID": 69179}], context="satcat_change response"
            )
        with self.assertRaises(snapshot.SnapshotError):
            snapshot.validate_annotation_rows({"error": "x"}, context="satcat_change response")
        rows = [{"NORAD_CAT_ID": "69179", "CURRENT_DECAY": None}]
        self.assertEqual(
            snapshot.validate_annotation_rows(rows, context="satcat_change response"), rows
        )


class RebuildV2Test(unittest.TestCase):
    def _archive_day(self, day, records):
        manifest = snapshot.save_snapshot(
            day,
            snapshot.canonicalize(records),
            records,
            "test_provenance",
            "test_strategy",
            [],
        )
        # simulate a v1-era manifest: strip the v2 fields save_snapshot now adds
        manifest_path = snapshot.snapshot_dir(day) / "manifest.json"
        import json as json_module

        stored = json_module.loads(manifest_path.read_text(encoding="utf-8"))
        for key in ("content_schema", "content_excluded_fields", "content_sha256"):
            stored.pop(key, None)
        manifest_path.write_text(json_module.dumps(stored, indent=2) + "\n", encoding="utf-8")
        return manifest

    def test_rebuild_adds_content_fields_and_annotations(self):
        import argparse
        import json as json_module
        import tempfile
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with patch.object(snapshot, "DATA_DIR", tmp_path / "data"), patch.object(
                snapshot, "LEDGER_PATH", tmp_path / "ledger.json"
            ):
                day_one = [gp_record()]
                day_two = [gp_record(DECAY_DATE="2026-06-07")]
                self._archive_day("2026-04-20", day_one)
                self._archive_day("2026-04-21", day_two)

                snapshot.process_rebuild_v2(
                    argparse.Namespace(start="2026-04-20", end="2026-04-21")
                )

                for day, records in (("2026-04-20", day_one), ("2026-04-21", day_two)):
                    manifest = json_module.loads(
                        (snapshot.snapshot_dir(day) / "manifest.json").read_text()
                    )
                    self.assertEqual(manifest["content_schema"], "rso-core-v2")
                    self.assertEqual(
                        manifest["content_sha256"], snapshot.core_content_sha256(records)
                    )
                    self.assertIn("annotations_sha256", manifest)

                genesis_annotations = json_module.loads(
                    (snapshot.snapshot_dir("2026-04-20") / "annotations.json").read_text()
                )
                self.assertTrue(genesis_annotations["baseline"])
                self.assertTrue(genesis_annotations["rebuilt"])
                self.assertEqual(genesis_annotations["catalog_changes"], [])

                day_two_annotations = json_module.loads(
                    (snapshot.snapshot_dir("2026-04-21") / "annotations.json").read_text()
                )
                self.assertFalse(day_two_annotations["baseline"])
                self.assertEqual(
                    [
                        (item["field"], item["previous"], item["current"])
                        for item in day_two_annotations["catalog_changes"]
                    ],
                    [("DECAY_DATE", None, "2026-06-07")],
                )

                ledger = json_module.loads((tmp_path / "ledger.json").read_text())
                self.assertTrue(all("content_sha256" in entry for entry in ledger))

    def test_rebuild_refuses_tampered_catalog(self):
        import argparse
        import gzip as gzip_module
        import tempfile
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with patch.object(snapshot, "DATA_DIR", tmp_path / "data"), patch.object(
                snapshot, "LEDGER_PATH", tmp_path / "ledger.json"
            ):
                self._archive_day("2026-04-20", [gp_record()])
                gz_path = snapshot.snapshot_dir("2026-04-20") / "catalog.json.gz"
                tampered = snapshot.canonicalize([gp_record(EPOCH="1999-01-01T00:00:00")])
                with gzip_module.open(gz_path, "wb") as handle:
                    handle.write(tampered)

                with self.assertRaises(snapshot.SnapshotError):
                    snapshot.process_rebuild_v2(
                        argparse.Namespace(start="2026-04-20", end="2026-04-20")
                    )


class AttestationV2Test(unittest.TestCase):
    def test_content_hash_prefers_core_projection(self):
        manifest = {"sha256": "aa" * 32, "content_sha256": "bb" * 32}
        self.assertEqual(
            rso_attestation.content_hash_from_manifest(manifest), "0x" + "bb" * 32
        )

    def test_content_hash_falls_back_to_raw_for_v1_manifests(self):
        manifest = {"sha256": "aa" * 32}
        self.assertEqual(
            rso_attestation.content_hash_from_manifest(manifest), "0x" + "aa" * 32
        )

    def test_v2_doc_chain_id_derivation_is_pinned(self):
        # keccak256("https://om.pub/rso/doc-chain/v2"); same derivation as v1.
        self.assertEqual(
            rso_profile.RSO_DOC_CHAIN_ID_V2,
            "0x7c5d6ad47ba584ce3f34ec8f94b08d17d4828c1d5ee6fbaecb4dfcb986efbc40",
        )
        self.assertNotEqual(rso_profile.RSO_DOC_CHAIN_ID_V2, rso_profile.RSO_DOC_CHAIN_ID)

    def test_baseline_parent_links_v1_head(self):
        parent = rso_attestation.parent_hash_for_date(
            "2026-04-20",
            {"attestations": []},
            baseline_parent_hash=rso_profile.RSO_V1_HEAD_BLOCK_HASH,
        )
        self.assertEqual(parent, rso_profile.RSO_V1_HEAD_BLOCK_HASH)

    def test_baseline_without_parent_stays_zero(self):
        parent = rso_attestation.parent_hash_for_date("2026-04-20", {"attestations": []})
        self.assertEqual(parent, rso_attestation.ZERO_BYTES32)

    def test_record_state_entry_preserves_v2_schema(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state-v2.json"
            entry = {
                "date": "2026-04-20",
                "updatedAt": "2026-06-09T00:00:00Z",
                "artifactId": "abc",
                "blockHash": "0x" + "11" * 32,
            }
            state = rso_attestation.record_state_entry(
                path, entry, schema=rso_attestation.STATE_SCHEMA_V2
            )
            self.assertEqual(state["schema"], "rso-docchain-node-state-v2")
            reloaded = rso_attestation.load_attestation_state(
                path, schema=rso_attestation.STATE_SCHEMA_V2
            )
            self.assertEqual(len(reloaded["attestations"]), 1)
            with self.assertRaises(ValueError):
                rso_attestation.load_attestation_state(
                    path, schema=rso_attestation.STATE_SCHEMA_V1
                )
            any_schema = rso_attestation.load_attestation_state(path, schema=None)
            self.assertEqual(any_schema["schema"], "rso-docchain-node-state-v2")

    def test_state_schema_v2_accepted_and_v1_rejected_for_v2_path(self):
        state = rso_attestation.load_attestation_state(
            Path("/nonexistent/state.json"), schema=rso_attestation.STATE_SCHEMA_V2
        )
        self.assertEqual(state["schema"], "rso-docchain-node-state-v2")
        with self.assertRaises(ValueError):
            rso_attestation.load_attestation_state(
                Path("/nonexistent/state.json"), schema="rso-docchain-node-state-v3"
            )


if __name__ == "__main__":
    unittest.main()
