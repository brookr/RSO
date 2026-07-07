import json
import tempfile
import unittest
from pathlib import Path

from pipeline import snapshot


def manifest(date, sha256):
    return {
        "date": date,
        "sha256": sha256,
        "object_count": 50000,
        "compressed_bytes": 123,
        "provenance": "test",
        "query_strategy": "test_strategy",
        "archived_at": f"{date}T08:00:00+00:00",
    }


class LedgerTests(unittest.TestCase):
    def test_insert_and_sort_by_date(self):
        original_ledger_path = snapshot.LEDGER_PATH
        with tempfile.TemporaryDirectory() as tmp:
            try:
                snapshot.LEDGER_PATH = Path(tmp) / "ledger.json"
                snapshot.update_ledger(manifest("2026-04-13", "b" * 64))
                snapshot.update_ledger(manifest("2026-04-12", "a" * 64))

                with open(snapshot.LEDGER_PATH, encoding="utf-8") as f:
                    ledger = json.load(f)

                self.assertEqual([entry["date"] for entry in ledger], ["2026-04-12", "2026-04-13"])
            finally:
                snapshot.LEDGER_PATH = original_ledger_path

    def test_upsert_existing_date_records_regeneration(self):
        original_ledger_path = snapshot.LEDGER_PATH
        with tempfile.TemporaryDirectory() as tmp:
            try:
                snapshot.LEDGER_PATH = Path(tmp) / "ledger.json"
                snapshot.update_ledger(manifest("2026-04-12", "a" * 64))
                snapshot.update_ledger(manifest("2026-04-12", "b" * 64))

                with open(snapshot.LEDGER_PATH, encoding="utf-8") as f:
                    ledger = json.load(f)

                self.assertEqual(len(ledger), 1)
                self.assertEqual(ledger[0]["sha256"], "b" * 64)
                self.assertEqual(ledger[0]["previous_sha256"], "a" * 64)
                self.assertIn("regenerated_at", ledger[0])
            finally:
                snapshot.LEDGER_PATH = original_ledger_path

    def test_corrupt_ledger_raises_instead_of_restarting_from_empty(self):
        # A truncated/corrupt ledger is a crash artifact, not a fresh start;
        # silently resetting it would erase the published chain of entries.
        original_ledger_path = snapshot.LEDGER_PATH
        with tempfile.TemporaryDirectory() as tmp:
            try:
                snapshot.LEDGER_PATH = Path(tmp) / "ledger.json"
                snapshot.LEDGER_PATH.write_text("{not-json", encoding="utf-8")
                with self.assertRaisesRegex(snapshot.SnapshotError, "corrupt"):
                    snapshot.update_ledger(manifest("2026-04-12", "a" * 64))
                # the corrupt bytes are left in place for the operator
                self.assertEqual(snapshot.LEDGER_PATH.read_text(encoding="utf-8"), "{not-json")

                snapshot.LEDGER_PATH.write_text("{}", encoding="utf-8")
                with self.assertRaisesRegex(snapshot.SnapshotError, "JSON list"):
                    snapshot.update_ledger(manifest("2026-04-12", "a" * 64))
            finally:
                snapshot.LEDGER_PATH = original_ledger_path

    def test_write_json_is_atomic_and_leaves_no_temp_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "nested" / "out.json"
            snapshot.write_json(target, {"a": 1})
            snapshot.write_json(target, {"a": 2})

            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"a": 2})
            self.assertEqual(
                [p.name for p in target.parent.iterdir()], ["out.json"]
            )


if __name__ == "__main__":
    unittest.main()
