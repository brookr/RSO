"""Guards for the daily-snapshot WORKFLOW itself — the class of regression that
unit-testing individual functions cannot catch.

Three production outages happened when audit fixes were merged to the live
`main` the daily runs from. Every one passed the existing unit tests because
those tested functions in isolation, never the workflow's command SEQUENCE,
step ORDERING, or the daily's real external-service reality. These tests model
the workflow so the same class of break is caught in a non-live env:

  * test_publish_precedes_prune          — reorder that lets a catalog be pruned
                                            before its bytes are published
  * test_daily_publish_prune_sequence... — the real publish(github ok / Arweave
                                            fails)->prune sequence end-to-end
"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pipeline import snapshot

ROOT = Path(__file__).resolve().parents[1]
DAILY_WORKFLOW = ROOT / ".github/workflows/daily-snapshot.yml"


# The whole-.github/workflows-tree freeze marker is present ONLY in the current
# (default-branch) workflow design that GitHub Actions actually executes. The
# node branch's frozen copy uses the older per-file selective freeze and is
# NEVER executed on a schedule — so asserting its step ordering is meaningless.
# This matters because the daily's OWN "Run tests" step runs after "Prepare node
# branch" has frozen .github/workflows back to the node's copy, so the workflow
# file in the working tree there is the node's legacy copy, not the executable
# one. (A version of this test that asserted ordering unconditionally read that
# frozen copy and blocked the daily for days — the very bug it was meant to
# prevent, inverted.)
_WHOLE_TREE_FREEZE_MARKER = "git rm -r --cached --quiet --ignore-unmatch -- .github/workflows"


def assert_daily_step_ordering(test, wf):
    """Assert the daily's step-ordering invariants against a workflow string."""
    pos = {}
    for label, needle in (
        ("build_bundles", "--storage-backend none"),
        ("commit_archive", '"Archive RSO snapshot"'),
        ("publish", "--use-existing-bundle"),
        ("prune", "prune-catalogs"),
        ("build_index", "snapshot.py build-index"),
        ("commit_receipts", '"Record archive publish destinations"'),
    ):
        i = wf.find(needle)
        test.assertNotEqual(i, -1, f"daily workflow no longer contains the {label} operation ({needle!r})")
        test.assertEqual(
            wf.find(needle, i + 1), -1,
            f"{label} operation ({needle!r}) appears more than once; ordering assertions are ambiguous",
        )
        pos[label] = i
    # A captured day must be committed to git AND uploaded to github_release
    # BEFORE its raw catalog is eligible for pruning; prune's deletions are
    # committed afterward. Any order that lets prune run before publish
    # reintroduces the data-loss window that took the daily down.
    test.assertLess(pos["build_bundles"], pos["publish"], "bundles must be built before publish")
    test.assertLess(
        pos["commit_archive"], pos["publish"],
        "the captured catalog must be committed to git before publish (so it survives even if publish fails)",
    )
    test.assertLess(
        pos["publish"], pos["prune"],
        "PUBLISH must precede PRUNE — never delete a raw catalog before its bytes are durably published",
    )
    test.assertLess(pos["prune"], pos["commit_receipts"], "prune deletions must be committed after prune")
    test.assertLess(pos["publish"], pos["build_index"], "build-index needs the storage receipts that publish records")
    test.assertLess(pos["prune"], pos["build_index"], "build-index must run after prune so a pruned day gets no dead locator")


class DailyWorkflowOrderingTest(unittest.TestCase):
    """The daily broke when steps were reordered so `prune-catalogs` ran before
    `publish`, letting a day's raw catalog be deleted + committed before its
    bytes were durably published. Assert the ordering invariants on the workflow
    that ACTUALLY executes (the default-branch, whole-tree-freeze design), so a
    reorder fails here instead of in production — while never false-failing
    against the node branch's never-executed frozen copy."""

    def setUp(self):
        self.wf = DAILY_WORKFLOW.read_text(encoding="utf-8")

    def test_publish_precedes_prune(self):
        if _WHOLE_TREE_FREEZE_MARKER not in self.wf:
            self.skipTest(
                "the daily-snapshot.yml in the working tree is the node branch's "
                "frozen legacy copy (per-file selective freeze), which GitHub "
                "Actions never executes; step ordering is only asserted on the "
                "current whole-tree-freeze design that runs from the default branch"
            )
        assert_daily_step_ordering(self, self.wf)


def _gp_record(cat_id, date):
    return {
        "NORAD_CAT_ID": cat_id,
        "CREATION_DATE": f"{date}T05:00:00",
        "EPOCH": f"{date}T04:00:00",
        "MEAN_MOTION": "15.0",
        "ECCENTRICITY": "0.0001",
        "INCLINATION": "51.6",
        "RA_OF_ASC_NODE": "10.0",
        "ARG_OF_PERICENTER": "20.0",
        "MEAN_ANOMALY": "30.0",
    }


class DailySequenceTest(unittest.TestCase):
    """Run the daily's publish -> prune sub-sequence in order against a fixture,
    with github_release succeeding and the (intentionally unfunded) Arweave
    upload FAILING — the exact real-world scenario. Catches both a fatal-Arweave
    regression AND a publish/prune ordering or receipt-format mismatch."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self._orig = {name: getattr(snapshot, name) for name in ("DATA_DIR", "LEDGER_PATH", "LATEST_POINTER_PATH")}
        snapshot.DATA_DIR = self.root / "data"
        snapshot.LEDGER_PATH = self.root / "ledger.json"
        snapshot.LATEST_POINTER_PATH = self.root / "latest.json"

    def tearDown(self):
        for name, value in self._orig.items():
            setattr(snapshot, name, value)
        self.tmp.cleanup()

    def _archive(self, date):
        data = sorted([_gp_record("1", date), _gp_record("2", date)], key=snapshot.catalog_id_sort_key)
        return snapshot.save_snapshot(
            date, snapshot.canonicalize(data), data,
            "genesis_from_gp", "current_gp_genesis", ["/x"],
            observed_at_utc=f"{date}T00:15:00Z", state_as_of_utc=f"{date}T00:00:00Z",
        )

    def _publish_args(self, start, end):
        return SimpleNamespace(
            date=None, start=start, end=end,
            storage_backend="github_release", upload_policy="if_missing",
            target_commitish=None, rebuild=False, use_existing_bundle=False,
            output_dir=self.root / "out", min_objects=1, repo=None,
            force=False, prerelease=False, require_arweave=False,
        )

    def test_daily_publish_prune_sequence_survives_failing_arweave(self):
        for d in ("2026-04-12", "2026-04-13"):
            self._archive(d)

        def github_ok(bundle, **kwargs):
            snapshot.record_storage_destination(
                bundle, "github_release",
                {"status": "created", "asset_url": f"https://github.com/OMPub/RSO/releases/download/{bundle['date']}/a.tar.gz"},
            )
            return {"status": "created", "destination": "github_release", **bundle}

        def arweave_fails(bundle, **kwargs):
            return {"status": "failed", "reason": "arweave_upload_failed", "destination": "arweave", **bundle}

        with patch.object(snapshot, "publish_github_release", github_ok), patch.object(
            snapshot, "publish_arweave_bundle_nonfatal", arweave_fails
        ):
            # Publish (github ok, Arweave FAILS) must NOT raise — Arweave is opt-in
            # permanence and its wallet may be unfunded.
            snapshot.process_publish(self._publish_args("2026-04-12", "2026-04-13"))

        # Both days ended up durably published on github_release.
        self.assertTrue(snapshot.day_has_published_destination("2026-04-12"))
        self.assertTrue(snapshot.day_has_published_destination("2026-04-13"))

        # Prune (keep_latest=1 retains the newest, 2026-04-13) now removes the
        # published older day — its bytes are safe on github_release.
        snapshot.process_prune_catalogs(
            SimpleNamespace(
                all=True, date=None, start=None, end=None,
                require_bundle=True, output_dir=self.root / "out", keep_latest=1,
            )
        )
        self.assertFalse((snapshot.snapshot_dir("2026-04-12") / "catalog.json.gz").exists())
        self.assertTrue((snapshot.snapshot_dir("2026-04-13") / "catalog.json.gz").exists())

    def test_prune_keeps_a_day_that_never_published(self):
        # If publish never recorded a destination for a day (a legacy failure),
        # prune must KEEP its catalog rather than delete it or fail the run.
        for d in ("2026-04-12", "2026-04-13"):
            self._archive(d)
        snapshot.process_prune_catalogs(
            SimpleNamespace(
                all=True, date=None, start=None, end=None,
                require_bundle=True, output_dir=self.root / "out", keep_latest=1,
            )
        )
        # 2026-04-12 is old and unpublished -> kept; nothing raised.
        self.assertTrue((snapshot.snapshot_dir("2026-04-12") / "catalog.json.gz").exists())


if __name__ == "__main__":
    unittest.main()
