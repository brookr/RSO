import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


# A minimal representation of the OLDER, per-file selective-freeze node-prep
# design. The node branch freezes its .github/workflows to its own HEAD, so a
# node can legitimately still carry this older workflow while main has evolved.
# The daily's "Run tests" step reads THIS copy (from the node tree), so the
# policy check MUST accept it — a version that only accepted main's current
# whole-tree freeze broke the production daily for two days.
OLD_DESIGN_NODE_WORKFLOW = """\
      - name: Prepare node branch
        run: |
          for path in data ledger.json reports indexer/cache indexer/generated; do
            git rm -r --cached --quiet --ignore-unmatch -- "$path" || true
            git checkout HEAD -- "$path"
          done
          path=".github/workflows/sweep-attestations.yml"
          git rm --cached --quiet --ignore-unmatch -- "$path" || true
          git checkout HEAD -- "$path"
          path=".github/workflows/check-sweeper-report.yml"
          git checkout "$DEFAULT_BRANCH" -- "$path"
"""


def assert_node_commit_carries_no_workflow_change(test, workflow):
    """The security invariant, tolerant of BOTH freeze designs. Asserted against
    the live workflow AND the old-design fixture below, so it can never be
    narrowed to one design (which is exactly the regression that broke the
    daily)."""
    # Node's own generated state is preserved from HEAD — common to both designs.
    test.assertIn(
        "for path in data ledger.json reports indexer/cache indexer/generated; do",
        workflow,
    )
    # Workflows are frozen to the node's own copy by ONE of the known mechanisms:
    # the whole-tree freeze (main's current design) or the older per-file
    # selective freeze (still carried, frozen, by some node copies).
    whole_tree_freeze = (
        "git rm -r --cached --quiet --ignore-unmatch -- .github/workflows" in workflow
    )
    selective_freeze = 'path=".github/workflows/sweep-attestations.yml"' in workflow
    test.assertTrue(
        whole_tree_freeze or selective_freeze,
        "node-prep must freeze .github/workflows by a known mechanism",
    )
    # Neither design ever stages the workflows tree from the merge into the node
    # commit.
    test.assertNotIn("data ledger.json reports .github/workflows", workflow)


class WorkflowPolicyTest(unittest.TestCase):
    def test_node_commit_carries_no_workflow_change_on_current_workflow(self):
        workflow = (ROOT / ".github/workflows/daily-snapshot.yml").read_text(encoding="utf-8")
        assert_node_commit_carries_no_workflow_change(self, workflow)
        # Every node carries the public report checker.
        self.assertTrue((ROOT / ".github/workflows/check-sweeper-report.yml").is_file())

    def test_policy_also_accepts_the_old_selective_freeze_design(self):
        # The daily's "Run tests" step runs against the node branch's FROZEN
        # workflow, which may still be this older design. If this fails, the
        # policy check has been narrowed to only the current design and will
        # break the live daily against older node copies.
        assert_node_commit_carries_no_workflow_change(self, OLD_DESIGN_NODE_WORKFLOW)


if __name__ == "__main__":
    unittest.main()
