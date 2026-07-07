import contextlib
import io
import unittest

from pipeline import snapshot


def gp_record(cat_id="1", created="2026-04-12T05:00:00", name=None):
    record = {
        "NORAD_CAT_ID": cat_id,
        "CREATION_DATE": created,
        "EPOCH": "2026-04-12T04:00:00",
        "MEAN_MOTION": "15.0",
        "ECCENTRICITY": "0.0001",
        "INCLINATION": "51.6",
        "RA_OF_ASC_NODE": "10.0",
        "ARG_OF_PERICENTER": "20.0",
        "MEAN_ANOMALY": "30.0",
    }
    if name is not None:
        record["OBJECT_NAME"] = name
    return record


class RollingSnapshotTests(unittest.TestCase):
    def test_dedupe_latest_per_object_keeps_newest_creation_date(self):
        old = gp_record("10", "2026-04-12T01:00:00")
        new = gp_record("10", "2026-04-12T02:00:00")

        self.assertEqual(snapshot.dedupe_latest_per_object([old, new]), [new])

    def test_filter_creation_window_is_lower_inclusive_upper_exclusive(self):
        before = gp_record("1", "2026-04-11T23:59:59")
        lower = gp_record("2", "2026-04-12T00:00:00")
        inside = gp_record("3", "2026-04-12T12:00:00")
        upper = gp_record("4", "2026-04-13T00:00:00")

        filtered = snapshot.filter_creation_window(
            [before, lower, inside, upper],
            lower_inclusive="2026-04-12T00:00:00",
            upper_exclusive="2026-04-13T00:00:00",
        )

        self.assertEqual([record["NORAD_CAT_ID"] for record in filtered], ["2", "3"])

    def test_dedupe_latest_per_object_uses_gp_id_tie_breaker(self):
        older_gp_id = dict(gp_record("10", "2026-04-12T01:00:00"), GP_ID="10")
        newer_gp_id = dict(gp_record("10", "2026-04-12T01:00:00"), GP_ID="11")

        self.assertEqual(
            snapshot.dedupe_latest_per_object([older_gp_id, newer_gp_id]),
            [newer_gp_id],
        )

    def test_apply_updates_adds_changes_and_carries_forward_absent_objects(self):
        base = [
            gp_record("1", "2026-04-11T01:00:00"),
            gp_record("2", "2026-04-11T02:00:00"),
        ]
        updates = [
            gp_record("2", "2026-04-12T02:00:00"),
            gp_record("3", "2026-04-12T03:00:00"),
        ]

        data, summary = snapshot.apply_updates(base, updates)

        self.assertEqual([record["NORAD_CAT_ID"] for record in data], ["1", "2", "3"])
        self.assertEqual(summary["new_norad_cat_ids"], ["3"])
        self.assertEqual(summary["updated_norad_cat_ids"], ["2"])
        self.assertEqual(summary["carried_forward_count"], 1)

    def test_apply_updates_replaces_newer_epoch_with_later_publication(self):
        base = [
            dict(
                gp_record("1", "2026-04-11T01:00:00"),
                EPOCH="2026-04-14T00:00:00",
                GP_ID="10",
            )
        ]
        updates = [
            dict(
                gp_record("1", "2026-04-12T01:00:00"),
                EPOCH="2026-04-13T00:00:00",
                GP_ID="11",
            )
        ]

        data, summary = snapshot.apply_updates(base, updates)

        self.assertEqual(data[0]["EPOCH"], "2026-04-13T00:00:00")
        self.assertEqual(summary["updated_norad_cat_ids"], ["1"])

    def test_apply_updates_to_state_matches_publication_selection_rule(self):
        state = {
            "1": dict(
                gp_record("1", "2026-04-11T01:00:00"),
                EPOCH="2026-04-14T00:00:00",
                GP_ID="10",
            )
        }
        updates = [
            dict(
                gp_record("1", "2026-04-12T01:00:00"),
                EPOCH="2026-04-13T00:00:00",
                GP_ID="11",
            ),
            dict(
                gp_record("2", "2026-04-12T01:00:00"),
                EPOCH="2026-04-12T00:00:00",
                GP_ID="12",
            ),
        ]

        summary = snapshot.apply_updates_to_state(state, updates)

        self.assertEqual(state["1"]["EPOCH"], "2026-04-13T00:00:00")
        self.assertEqual(state["2"]["NORAD_CAT_ID"], "2")
        self.assertEqual(summary["applied_update_count"], 2)
        self.assertEqual(summary["ignored_older_update_count"], 0)

    def test_build_snapshot_from_base_rejects_non_advancing_base_state(self):
        class FakeClient:
            pass

        with self.assertRaises(snapshot.SnapshotError):
            snapshot.build_snapshot_from_base(
                FakeClient(),
                "2026-04-12",
                [gp_record("1")],
                "2026-04-12T00:00:00Z",
            )

    def test_decay_date_update_is_a_normal_canonical_update(self):
        base = [dict(gp_record("1", "2026-04-11T01:00:00"), DECAY_DATE=None)]
        updates = [
            dict(
                gp_record("1", "2026-04-12T01:00:00"),
                DECAY_DATE="2026-04-12",
            )
        ]

        data, summary = snapshot.apply_updates(base, updates)

        self.assertEqual(data[0]["DECAY_DATE"], "2026-04-12")
        self.assertEqual(summary["updated_norad_cat_ids"], ["1"])
        self.assertEqual(summary["carried_forward_count"], 0)

    def test_decayed_object_absent_from_delta_is_carried_forward(self):
        decayed = dict(
            gp_record("1", "2026-04-11T01:00:00"),
            DECAY_DATE="2026-04-11",
        )

        data, summary = snapshot.apply_updates([decayed], [])

        self.assertEqual(data, [decayed])
        self.assertEqual(summary["carried_forward_count"], 1)
        self.assertEqual(summary["new_norad_cat_ids"], [])
        self.assertEqual(summary["updated_norad_cat_ids"], [])

    def test_empty_delta_window_warns_loudly(self):
        # an HTTP-200-empty Space-Track outage is indistinguishable from a
        # quiet day; the operator must see it immediately, not at consensus
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            snapshot.warn_if_empty_delta_window("2026-04-12", {"raw_row_count": 0})
        self.assertIn("WARNING", buf.getvalue())
        self.assertIn("0 rows", buf.getvalue())

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            snapshot.warn_if_empty_delta_window("2026-04-12", {"raw_row_count": 5})
        self.assertEqual(buf.getvalue(), "")

    def test_visibility_audit_tracks_missing_and_reappeared_objects(self):
        archived = [
            gp_record("1", "2026-04-11T01:00:00", "OBJECT 1"),
            gp_record("2", "2026-04-11T02:00:00", "OBJECT 2"),
        ]
        previous_visibility = {
            "2": {
                "currently_missing_from_current_gp": True,
                "first_missing_in_current_gp_audit": "2026-04-12T00:15:00Z",
                "consecutive_missing_audits": 1,
            }
        }

        audit, state = snapshot.build_visibility_audit(
            "2026-04-13",
            archived,
            [archived[1]],
            "2026-04-13T00:15:00Z",
            ["/class/gp/format/json"],
            previous_visibility=previous_visibility,
        )

        self.assertEqual(audit["missing_from_current_gp_count"], 1)
        self.assertEqual(audit["missing_from_current_gp"][0]["norad_cat_id"], "1")
        self.assertEqual(audit["reappeared_in_current_gp_count"], 1)
        self.assertEqual(audit["reappeared_in_current_gp"][0]["norad_cat_id"], "2")
        self.assertIn("1", state["missing_objects"])
        self.assertNotIn("2", state["missing_objects"])

    def test_visibility_audit_does_not_treat_decay_as_missing(self):
        decayed = dict(
            gp_record("1", "2026-04-11T01:00:00", "DECAYED OBJECT"),
            DECAY_DATE="2026-04-11",
        )

        audit, state = snapshot.build_visibility_audit(
            "2026-04-13",
            [decayed],
            [decayed],
            "2026-04-13T00:15:00Z",
            ["/class/gp/format/json"],
        )

        self.assertEqual(audit["missing_from_current_gp_count"], 0)
        self.assertEqual(audit["missing_from_current_gp"], [])
        self.assertEqual(state["missing_objects"], {})

    def test_visibility_audit_missing_record_has_no_decay_explanation(self):
        decayed = dict(
            gp_record("1", "2026-04-11T01:00:00", "DECAYED OBJECT"),
            DECAY_DATE="2026-04-11",
        )

        audit, state = snapshot.build_visibility_audit(
            "2026-04-13",
            [decayed],
            [],
            "2026-04-13T00:15:00Z",
            ["/class/gp/format/json"],
        )

        self.assertEqual(audit["missing_from_current_gp_count"], 1)
        self.assertEqual(audit["missing_from_current_gp"][0]["norad_cat_id"], "1")
        self.assertNotIn("satcat_decay", audit["missing_from_current_gp"][0])
        self.assertNotIn("decay_explanation", audit["missing_from_current_gp"][0])
        self.assertNotIn("satcat_decay_explained_missing_count", audit)
        self.assertIn("1", state["missing_objects"])

    def test_compare_record_sets_summarizes_id_and_record_differences(self):
        replay = [gp_record("1"), gp_record("2", "2026-04-12T02:00:00")]
        current = [gp_record("2", "2026-04-12T03:00:00"), gp_record("3")]

        comparison = snapshot.compare_record_sets(replay, current)

        self.assertEqual(comparison["shared_object_count"], 1)
        self.assertEqual(comparison["matched_record_count"], 0)
        self.assertEqual(comparison["mismatched_record_count"], 1)
        self.assertEqual(comparison["missing_from_replay_sample"], ["3"])
        self.assertEqual(comparison["missing_from_current_gp_sample"], ["1"])

    def test_mismatch_sample_details_names_differing_fields(self):
        replay = [dict(gp_record("1"), EPOCH="2026-04-12T04:00:00")]
        current = [dict(gp_record("1"), EPOCH="2026-04-13T04:00:00")]

        details = snapshot.mismatch_sample_details(replay, current, ["1"])

        self.assertEqual(details[0]["norad_cat_id"], "1")
        self.assertIn("EPOCH", details[0]["differing_fields"])
        self.assertEqual(details[0]["replay"]["epoch"], "2026-04-12T04:00:00")
        self.assertEqual(details[0]["current_gp"]["epoch"], "2026-04-13T04:00:00")


if __name__ == "__main__":
    unittest.main()
