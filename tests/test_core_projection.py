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

        self.assertEqual(annotations["schema"], "rso-annotations-v2")
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
        self.assertEqual(annotations["tip_messages"], [])

    def test_satcat_decay_and_tip_sections_pass_through(self):
        satcat = [{"NORAD_CAT_ID": "69179", "PREVIOUS_DECAY": None, "CURRENT_DECAY": "2026-06-07"}]
        decay = [{"NORAD_CAT_ID": "69179", "MSG_EPOCH": "2026-06-07 03:14:00", "MSG_TYPE": "Historical"}]
        tip = [
            {
                "NORAD_CAT_ID": "53559",
                "MSG_EPOCH": "2026-06-09 01:48:00",
                "DECAY_EPOCH": "2026-06-09 03:37:00",
                "WINDOW": "50",
                "LAT": "-29.4",
                "LON": "11.9",
                "HIGH_INTEREST": "N",
            }
        ]
        annotations = snapshot.build_annotations(
            "2026-06-08",
            [gp_record()],
            [gp_record()],
            observed_at_utc="2026-06-08T01:00:00Z",
            satcat_changes=satcat,
            decay_messages=decay,
            tip_messages=tip,
            query_paths=["/class/satcat_change/...", "/class/decay/...", "/class/tip/..."],
        )
        self.assertEqual(annotations["satcat_changes"], satcat)
        self.assertEqual(annotations["decay_messages"], decay)
        self.assertEqual(annotations["tip_messages"], tip)
        self.assertEqual(len(annotations["api_query_paths"]), 3)

    def test_decay_feed_queries_historical_messages_only(self):
        captured = []

        class Client:
            def query(self, path):
                captured.append(path)
                return []

        snapshot.query_annotation_observations(
            Client(), "2026-06-09T00:00:00", "2026-06-10T00:00:00"
        )
        decay_paths = [p for p in captured if "/class/decay/" in p]
        self.assertEqual(len(decay_paths), 1)
        self.assertIn("/MSG_TYPE/Historical/", decay_paths[0])
        self.assertIn("/DECAY_EPOCH/2026-05-10--2026-06-17/", decay_paths[0])

    def test_tip_feed_is_windowed_and_epoch_bounded(self):
        captured = []

        class Client:
            def query(self, path):
                captured.append(path)
                return []

        satcat_rows, decay_rows, tip_rows, paths = snapshot.query_annotation_observations(
            Client(), "2026-06-09T00:00:00", "2026-06-10T00:00:00"
        )
        tip_paths = [p for p in captured if "/class/tip/" in p]
        self.assertEqual(len(tip_paths), 1)
        self.assertIn("/MSG_EPOCH/2026-06-09T00:00:00--2026-06-10T00:00:00/", tip_paths[0])
        # same flood floor as decay, 60-day forecast ceiling
        self.assertIn("/DECAY_EPOCH/2026-05-10--2026-08-09/", tip_paths[0])
        self.assertEqual((satcat_rows, decay_rows, tip_rows), ([], [], []))
        self.assertEqual(paths, captured)

    def test_validate_annotation_rows_rejects_non_string_values(self):
        with self.assertRaises(snapshot.SnapshotError):
            snapshot.validate_annotation_rows(
                [{"NORAD_CAT_ID": 69179}], context="satcat_change response"
            )


def cdm_row(cdm_id="1", created="2026-06-10 05:00:00.000000", pc="0.0001", emergency="N"):
    return {
        "CDM_ID": cdm_id,
        "CREATED": created,
        "EMERGENCY_REPORTABLE": emergency,
        "TCA": "2026-06-12T01:00:00.000000",
        "MIN_RNG": "401",
        "PC": pc,
        "SAT_1_ID": "50140",
        "SAT_1_NAME": "ORBCOMM FM 5 DEB",
        "SAT_2_ID": "60874",
        "SAT_2_NAME": "CZ-6A DEB",
    }


class ConjunctionsTest(unittest.TestCase):
    def test_feed_is_created_windowed_and_tca_bounded(self):
        captured = []

        class Client:
            def query(self, path):
                captured.append(path)
                return []

        rows, paths = snapshot.query_conjunction_messages(
            Client(), "2026-06-09T00:00:00", "2026-06-10T00:00:00"
        )
        self.assertEqual(rows, [])
        self.assertEqual(len(captured), 1)
        self.assertIn("/class/cdm_public/", captured[0])
        self.assertIn("/CREATED/2026-06-09T00:00:00--2026-06-10T00:00:00/", captured[0])
        # 1-day floor, 10-day forecast ceiling
        self.assertIn("/TCA/2026-06-08--2026-06-20/", captured[0])
        self.assertEqual(paths, captured)

    def test_build_conjunctions_sorts_and_summarizes(self):
        rows = [
            cdm_row(cdm_id="30", created="2026-06-10 06:00:00.000000", pc="0.002", emergency="Y"),
            cdm_row(cdm_id="9", created="2026-06-10 05:00:00.000000", pc=None),
            cdm_row(cdm_id="10", created="2026-06-10 05:00:00.000000", pc="0.0005", emergency="Y"),
        ]
        conjunctions = snapshot.build_conjunctions(
            "2026-06-10",
            rows,
            observed_at_utc="2026-06-10T00:20:00Z",
            window_start_utc="2026-06-09T00:00:00Z",
            window_end_utc="2026-06-10T00:00:00Z",
            query_paths=["/class/cdm_public/..."],
        )

        self.assertEqual(conjunctions["schema"], "rso-conjunctions-v1")
        self.assertEqual(conjunctions["date"], "2026-06-10")
        self.assertEqual(conjunctions["window_start_utc"], "2026-06-09T00:00:00Z")
        self.assertEqual(
            [row["CDM_ID"] for row in conjunctions["messages"]], ["9", "10", "30"]
        )
        summary = conjunctions["summary"]
        self.assertEqual(summary["message_count"], 3)
        self.assertEqual(summary["emergency_reportable_count"], 2)
        self.assertEqual(summary["max_pc"], "0.002")
        self.assertEqual(summary["max_pc_event"]["sat_1_name"], "ORBCOMM FM 5 DEB")
        self.assertEqual(len(conjunctions["api_query_paths"]), 1)

    def test_build_conjunctions_empty_window(self):
        conjunctions = snapshot.build_conjunctions(
            "2026-06-10",
            [],
            observed_at_utc="2026-06-10T00:20:00Z",
            window_start_utc="2026-06-09T00:00:00Z",
            window_end_utc="2026-06-10T00:00:00Z",
        )
        self.assertEqual(conjunctions["messages"], [])
        summary = conjunctions["summary"]
        self.assertEqual(summary["message_count"], 0)
        self.assertEqual(summary["emergency_reportable_count"], 0)
        self.assertIsNone(summary["max_pc"])
        self.assertNotIn("max_pc_event", summary)
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

                snapshot.process_rebuild_content(
                    argparse.Namespace(start="2026-04-20", end="2026-04-21", force=False)
                )

                for day, records in (("2026-04-20", day_one), ("2026-04-21", day_two)):
                    manifest = json_module.loads(
                        (snapshot.snapshot_dir(day) / "manifest.json").read_text()
                    )
                    self.assertEqual(manifest["content_schema"], "rso-core-v1")
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
                    snapshot.process_rebuild_content(
                        argparse.Namespace(start="2026-04-20", end="2026-04-20", force=False)
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

    def test_doc_chain_id_derivation_is_pinned(self):
        # keccak256("https://om.pub/rso/doc-chain") -- the unversioned protocol id.
        self.assertEqual(
            rso_profile.RSO_DOC_CHAIN_ID,
            "0x6011620b5a3faa23f8078c2af0bb1a87bb85a68f784abdf3dbae67939c399bea",
        )
        self.assertEqual(rso_profile.RSO_PROFILE_URI, "https://om.pub/rso/doc-chain")

    def test_baseline_parent_override_is_honored(self):
        override = "0x" + "ab" * 32
        parent = rso_attestation.parent_hash_for_date(
            "2026-04-20",
            {"attestations": []},
            baseline_parent_hash=override,
        )
        self.assertEqual(parent, override)

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
                path, entry, schema=rso_attestation.STATE_SCHEMA
            )
            self.assertEqual(state["schema"], "rso-docchain-node-state-v1")
            reloaded = rso_attestation.load_attestation_state(
                path, schema=rso_attestation.STATE_SCHEMA
            )
            self.assertEqual(len(reloaded["attestations"]), 1)
            any_schema = rso_attestation.load_attestation_state(path, schema=None)
            self.assertEqual(any_schema["schema"], "rso-docchain-node-state-v1")

    def test_state_schema_v2_accepted_and_v1_rejected_for_v2_path(self):
        state = rso_attestation.load_attestation_state(
            Path("/nonexistent/state.json"), schema=rso_attestation.STATE_SCHEMA
        )
        self.assertEqual(state["schema"], "rso-docchain-node-state-v1")
        with self.assertRaises(ValueError):
            rso_attestation.load_attestation_state(
                Path("/nonexistent/state.json"), schema="rso-docchain-node-state-v3"
            )



class HashOnlyTierProfileTest(unittest.TestCase):
    def verification(self, publication_status, claimed="github:owner/repo"):
        return {
            "authorizationStatus": "verified",
            "publicationStatus": publication_status,
            "nodeId": "github:owner/repo",
            "claimedNodeId": claimed,
            "declaredAttester": "0x" + "bb" * 20,
            "attestationAttester": "0x" + "bb" * 20,
        }

    def test_hash_only_claim_yields_verified_node_id_without_locator(self):
        node_id = rso_profile.verified_node_id(
            self.verification("hash_only"),
            claimed_node_id="",
            attester="0x" + "bb" * 20,
        )
        self.assertEqual(node_id, "github:owner/repo")

    def test_hash_only_claim_rejected_when_event_carries_a_locator(self):
        node_id = rso_profile.verified_node_id(
            self.verification("hash_only"),
            claimed_node_id="github:owner/repo",
            attester="0x" + "bb" * 20,
        )
        self.assertEqual(node_id, "")

    def test_publication_verified_path_still_requires_locator_match(self):
        node_id = rso_profile.verified_node_id(
            self.verification("verified"),
            claimed_node_id="",
            attester="0x" + "bb" * 20,
        )
        self.assertEqual(node_id, "")

    def test_decorate_attaches_backing_and_tier_to_hash_only_witness(self):
        verification = self.verification("hash_only")
        fingerprint = "ff" * 32
        event = {
            "attester": "0x" + "bb" * 20,
            "onBehalfOf": "0x" + "00" * 20,
            "uri": "",
            "claimFingerprint": fingerprint,
        }
        with unittest.mock.patch.object(
            rso_profile, "event_claim_fingerprint", return_value=fingerprint
        ):
            rso_profile.decorate_tdh_support(
                event,
                {"github:owner/repo": 7},
                {fingerprint: verification},
                {},
            )
        self.assertEqual(event["nodeId"], "github:owner/repo")
        self.assertEqual(event["nodeAuthorizationStatus"], "verified")
        self.assertEqual(event["publicationVerification"], "hash_only")
        self.assertEqual(event["nodeBackingTdh"], 7)
        self.assertEqual(event["combinedSupportTdh"], 7)

if __name__ == "__main__":
    unittest.main()
