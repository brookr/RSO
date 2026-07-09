import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WorkflowPolicyTest(unittest.TestCase):
    def test_node_commit_carries_no_workflow_change(self):
        # The daily node-prep step MUST ensure a node commit never adds or
        # modifies a workflow file: a fork's default GITHUB_TOKEN cannot push
        # workflow changes without workflows scope, so an unfrozen workflow diff
        # fails the whole daily, and a smuggled scheduled workflow (e.g. the
        # treasury sweeper) would run with the operator's secrets.
        #
        # IMPORTANT: this test runs in TWO contexts with DIFFERENT copies of the
        # workflow file — against main (its current design) in PR CI, AND against
        # the *node branch's own frozen copy* during the daily "Run tests" step
        # (node-prep freezes .github/workflows to the node's HEAD). Those copies
        # legitimately differ, so this asserts the security INVARIANT, tolerant
        # of both the whole-tree freeze and the older per-file selective freeze —
        # not one design's exact strings. (A version that pinned exact strings
        # for only one design broke the production daily for two days.)
        workflow = (ROOT / ".github/workflows/daily-snapshot.yml").read_text(
            encoding="utf-8"
        )

        # Node's own generated state is preserved from HEAD (not overwritten by
        # the code merge from the default branch) — common to both designs.
        self.assertIn(
            "for path in data ledger.json reports indexer/cache indexer/generated; do",
            workflow,
        )
        # Workflows are frozen back to the node's own copy by ONE of the known
        # mechanisms: the whole-tree freeze (main's current design) or the older
        # per-file selective freeze (still carried, frozen, by some node copies).
        whole_tree_freeze = (
            "git rm -r --cached --quiet --ignore-unmatch -- .github/workflows" in workflow
        )
        selective_freeze = 'path=".github/workflows/sweep-attestations.yml"' in workflow
        self.assertTrue(
            whole_tree_freeze or selective_freeze,
            "node-prep must freeze .github/workflows by a known mechanism",
        )
        # Neither design ever stages the workflows tree from the merge into the
        # node commit.
        self.assertNotIn("data ledger.json reports .github/workflows", workflow)
        # Every node carries the public report checker.
        self.assertTrue((ROOT / ".github/workflows/check-sweeper-report.yml").is_file())


if __name__ == "__main__":
    unittest.main()
