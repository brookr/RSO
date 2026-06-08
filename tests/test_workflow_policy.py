import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class WorkflowPolicyTest(unittest.TestCase):
    def test_node_branch_syncs_report_checker_but_not_treasury_sweeper(self):
        workflow = (ROOT / ".github/workflows/daily-snapshot.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "for path in data ledger.json reports indexer/cache indexer/generated; do",
            workflow,
        )
        self.assertNotIn("data ledger.json reports .github/workflows", workflow)
        self.assertIn('path=".github/workflows/sweep-attestations.yml"', workflow)
        self.assertIn('path=".github/workflows/check-sweeper-report.yml"', workflow)
        self.assertIn('git checkout "$DEFAULT_BRANCH" -- "$path"', workflow)
        self.assertTrue((ROOT / ".github/workflows/check-sweeper-report.yml").is_file())


if __name__ == "__main__":
    unittest.main()
