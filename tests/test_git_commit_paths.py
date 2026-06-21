"""Tests for scripts/git-commit-paths.sh — the idempotent commit+push helper.

The script must be safe to re-run: it stages each path independently (so an
absent optional path never aborts staging of a path that does have changes),
no-ops cleanly when nothing changed, and never crashes on an unborn HEAD.
"""

import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "git-commit-paths.sh"


def git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )


def run_script(cwd, *args):
    return subprocess.run(
        ["bash", str(SCRIPT), *args], cwd=cwd, capture_output=True, text=True
    )


class GitCommitPathsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.remote = root / "remote.git"
        self.work = root / "work"
        git(root, "init", "-q", "--bare", str(self.remote))
        git(root, "clone", "-q", str(self.remote), str(self.work))
        git(self.work, "config", "user.email", "t@t")
        git(self.work, "config", "user.name", "t")
        git(self.work, "checkout", "-q", "-b", "node")
        (self.work / "data").mkdir()
        (self.work / "data" / "seed.json").write_text("seed\n")
        git(self.work, "add", "-A")
        git(self.work, "commit", "-qm", "seed")
        git(self.work, "push", "-q", "origin", "node")

    def head_subject(self):
        return git(self.work, "log", "-1", "--format=%s").stdout.strip()

    def test_valid_plus_missing_path_commits_the_valid_change(self):
        # P1 regression: a missing pathspec must not abort staging of a path
        # that does have changes.
        (self.work / "data" / "seed.json").write_text("seed\nCHANGED\n")
        result = run_script(self.work, "node", "real update", "data", "indexer/generated")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.head_subject(), "real update")
        # and it reached the remote (the bare repo's default HEAD is an empty
        # main, so name the branch we actually pushed)
        remote_head = git(self.remote, "log", "-1", "--format=%s", "node").stdout.strip()
        self.assertEqual(remote_head, "real update")

    def test_no_changes_is_clean_noop(self):
        before = git(self.work, "rev-parse", "HEAD").stdout.strip()
        result = run_script(self.work, "node", "nothing here", "data", "indexer/generated")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(git(self.work, "rev-parse", "HEAD").stdout.strip(), before)

    def test_all_paths_missing_is_clean_noop(self):
        before = git(self.work, "rev-parse", "HEAD").stdout.strip()
        result = run_script(self.work, "node", "absent", "nope/one", "nope/two")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(git(self.work, "rev-parse", "HEAD").stdout.strip(), before)

    def test_staged_deletion_is_committed(self):
        (self.work / "data" / "seed.json").unlink()
        result = run_script(self.work, "node", "remove seed", "data")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.head_subject(), "remove seed")


if __name__ == "__main__":
    unittest.main()
