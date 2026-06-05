import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from attestation import rso_attestation as node
from indexer.rso_profile import describe_publication_uri


class RsoAttestationNodeTest(unittest.TestCase):
    def test_doc_ref_for_date(self):
        self.assertEqual(node.doc_ref_for_date("2026-05-28"), 20260528000000)

    def test_parent_hash_for_baseline_is_zero(self):
        self.assertEqual(
            node.parent_hash_for_date("2026-04-20", {"attestations": []}),
            node.ZERO_BYTES32,
        )

    def test_parent_hash_requires_prior_state_or_bootstrap(self):
        with self.assertRaisesRegex(ValueError, "no prior DocChain blockHash"):
            node.parent_hash_for_date("2026-05-28", {"attestations": []})

        self.assertEqual(
            node.parent_hash_for_date(
                "2026-05-28",
                {"attestations": []},
                bootstrap_parent_hash="0x" + "aa" * 32,
            ),
            "0x" + "aa" * 32,
        )

    def test_parent_hash_requires_previous_day(self):
        state = {
            "attestations": [
                {
                    "date": "2026-05-26",
                    "blockHash": "0x" + "aa" * 32,
                }
            ]
        }
        with self.assertRaisesRegex(ValueError, "expected 2026-05-27"):
            node.parent_hash_for_date("2026-05-28", state)

    def test_state_attestation_for_date_finds_existing_entries(self):
        state = {
            "attestations": [
                {"date": "2026-05-27", "blockHash": "0x" + "aa" * 32},
                {"date": "2026-05-28", "blockHash": "0x" + "bb" * 32},
            ]
        }

        self.assertEqual(
            node.state_attestation_for_date(state, "2026-05-28")["blockHash"],
            "0x" + "bb" * 32,
        )
        self.assertIsNone(node.state_attestation_for_date(state, "2026-05-29"))

    def test_release_uri_builds_locator_with_bundle_and_all_locations(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir) / "data"
            day_dir = data_dir / "2026" / "05" / "28"
            day_dir.mkdir(parents=True)
            (day_dir / "storage.json").write_text(
                json.dumps(
                    {
                        "bundle_sha256": "aa" * 32,
                        "destinations": {
                            "github_release": {
                                "release_url": "https://github.com/owner/repo/releases/tag/rso-archive-2026-05-28",
                                "asset_name": "rso-archive-2026-05-28.tar.gz",
                            },
                            "arweave": {"transaction_id": "abc123"},
                        }
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(node, "DATA_DIR", data_dir):
                publication = describe_publication_uri(node.release_uri("2026-05-28"))
                self.assertEqual(publication["bundleSha256"], "aa" * 32)
                self.assertEqual(
                    publication["locations"],
                    [
                        "ar://abc123",
                        "https://github.com/owner/repo/releases/download/rso-archive-2026-05-28/rso-archive-2026-05-28.tar.gz",
                    ],
                )
                github_only = describe_publication_uri(node.release_uri("2026-05-28", mode="github_release"))
                self.assertEqual(
                    github_only["locations"],
                    [
                        "https://github.com/owner/repo/releases/download/rso-archive-2026-05-28/rso-archive-2026-05-28.tar.gz",
                    ],
                )

    def test_release_uri_falls_back_to_direct_location_without_bundle_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir) / "data"
            day_dir = data_dir / "2026" / "05" / "28"
            day_dir.mkdir(parents=True)
            (day_dir / "storage.json").write_text(
                json.dumps(
                    {
                        "destinations": {
                            "arweave": {"transaction_id": "abc123"},
                        }
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(node, "DATA_DIR", data_dir):
                self.assertEqual(node.release_uri("2026-05-28"), "ar://abc123")

    def test_prepare_sign_records_signed_artifact_and_state_entry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir) / "data"
            day_dir = data_dir / "2026" / "04" / "20"
            day_dir.mkdir(parents=True)
            (day_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "date": "2026-04-20",
                        "sha256": "11" * 32,
                        "object_count": 1,
                    }
                ),
                encoding="utf-8",
            )
            (day_dir / "storage.json").write_text(
                json.dumps(
                    {
                        "bundle_sha256": "dd" * 32,
                        "destinations": {
                            "github_release": {
                                "release_url": "https://github.com/owner/repo/releases/tag/rso-archive-2026-04-20",
                                "asset_name": "rso-archive-2026-04-20.tar.gz",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(node, "DATA_DIR", data_dir):
                with patch.object(node, "sign_prepared_with_cast", return_value="0x1234"):
                    with patch.object(node, "doc_block_hash_with_cast", return_value="0x" + "22" * 32):
                        prepared, artifact = node.prepare_sign_one(
                            snapshot_date="2026-04-20",
                            state={"attestations": []},
                            chain_id=1,
                            contract_address="0x" + "aa" * 20,
                            attester="0x" + "bb" * 20,
                            on_behalf_of="0x" + "cc" * 20,
                        )

            self.assertEqual(prepared.parent_hash, node.ZERO_BYTES32)
            self.assertEqual(prepared.block_hash, "0x" + "22" * 32)
            self.assertEqual(prepared.content_hash, "0x" + "11" * 32)
            publication = describe_publication_uri(prepared.uri)
            self.assertEqual(publication["bundleSha256"], "dd" * 32)
            self.assertEqual(
                publication["locations"],
                [
                    "https://github.com/owner/repo/releases/download/rso-archive-2026-04-20/rso-archive-2026-04-20.tar.gz"
                ],
            )
            self.assertEqual(artifact["schema"], "rso-signed-attestation-v1")
            self.assertEqual(artifact["blockHash"], "0x" + "22" * 32)
            entry = node.state_entry_from_signed_artifact(
                snapshot_date="2026-04-20",
                prepared=prepared.prepared,
                signed=prepared.signed,
                block_hash=prepared.block_hash,
            )
            self.assertEqual(entry["blockHash"], "0x" + "22" * 32)
            self.assertEqual(entry["submissionStatus"], "signed")
            state_path = Path(tmpdir) / "state.json"
            state = node.record_state_entry(state_path, entry)
            self.assertEqual(state["latest"]["date"], "2026-04-20")
            self.assertEqual(len(state["attestations"]), 1)


if __name__ == "__main__":
    unittest.main()
