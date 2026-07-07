import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WorkflowPolicyTest(unittest.TestCase):
    def test_node_commit_carries_no_workflow_change(self):
        # The daily node-prep step MUST freeze the entire .github/workflows tree
        # back to the node's own (HEAD) version so a node commit never adds or
        # modifies a workflow file. A fork's default GITHUB_TOKEN is forbidden
        # from pushing workflow changes without workflows scope, so an
        # unfrozen workflow diff would make the node push (and the whole daily)
        # fail. Freezing the whole tree also means no node commit can smuggle in
        # an altered scheduled workflow (e.g. the treasury sweeper) that would
        # then run with the operator's secrets.
        workflow = (ROOT / ".github/workflows/daily-snapshot.yml").read_text(
            encoding="utf-8"
        )

        # Node's own generated state is preserved from HEAD (not overwritten by
        # the code merge from the default branch).
        self.assertIn(
            "for path in data ledger.json reports indexer/cache indexer/generated; do",
            workflow,
        )
        # The whole workflows tree is reset to the node's HEAD version: dropped
        # from the index + working tree, then restored from HEAD if node had it.
        self.assertIn(
            'git rm -r --cached --quiet --ignore-unmatch -- .github/workflows',
            workflow,
        )
        self.assertIn("rm -rf -- .github/workflows", workflow)
        self.assertIn('git checkout HEAD -- .github/workflows', workflow)
        # The node commit must never stage the workflows tree from the merge.
        self.assertNotIn("data ledger.json reports .github/workflows", workflow)
        # The report checker exists in the tree (nodes carry whatever they had;
        # it is not selectively re-synced any more, the whole tree is frozen).
        self.assertTrue((ROOT / ".github/workflows/check-sweeper-report.yml").is_file())


if __name__ == "__main__":
    unittest.main()
