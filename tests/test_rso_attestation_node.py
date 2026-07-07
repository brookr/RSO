import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from attestation import node_attest as node_cli
from attestation import rso_attestation as node
from indexer.rso_profile import describe_publication_uri


class RsoAttestationNodeTest(unittest.TestCase):
    def test_doc_ref_for_date(self):
        self.assertEqual(node.doc_ref_for_date("2026-05-28"), 20260528000000)

    def test_node_cli_default_ttl_allows_multiple_sweeper_retries(self):
        with patch.dict("os.environ", {}, clear=True), patch(
            "sys.argv",
            ["node_attest.py", "--start", "2026-05-28", "--end", "2026-05-28"],
        ):
            args = node_cli.parse_args()
        self.assertEqual(args.ttl, node_cli.MAX_ATTESTATION_TTL)

    def test_node_cli_treats_empty_on_behalf_of_environment_as_zero_address(self):
        with patch.dict("os.environ", {"RSO_ON_BEHALF_OF_ADDRESS": ""}, clear=True), patch(
            "sys.argv",
            ["node_attest.py", "--start", "2026-05-28", "--end", "2026-05-28"],
        ):
            args = node_cli.parse_args()
        self.assertEqual(args.on_behalf_of, node.ZERO_ADDRESS)

    def test_node_cli_allow_content_change_flag(self):
        with patch.dict("os.environ", {}, clear=True), patch(
            "sys.argv",
            ["node_attest.py", "--start", "2026-05-28", "--end", "2026-05-28"],
        ):
            self.assertFalse(node_cli.parse_args().allow_content_change)
        with patch.dict("os.environ", {}, clear=True), patch(
            "sys.argv",
            ["node_attest.py", "--start", "2026-05-28", "--end", "2026-05-28", "--allow-content-change"],
        ):
            self.assertTrue(node_cli.parse_args().allow_content_change)

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
        with self.assertRaisesRegex(ValueError, "no prior DocChain blockHash"):
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
                            "arweave": {
                                "status": "submitted",
                                "bundle_sha256": "aa" * 32,
                                "transaction_id": "abc123",
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(node, "DATA_DIR", data_dir):
                publication = describe_publication_uri(
                    node.release_uri("2026-05-28", node_id="github:owner/repo")
                )
                self.assertEqual(publication["nodeId"], "github:owner/repo")
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

    def test_release_uri_ignores_failed_or_mismatched_arweave_receipts(self):
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
                            "arweave": {
                                "status": "failed",
                                "bundle_sha256": "aa" * 32,
                                "transaction_id": "abc123",
                            },
                        },
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(node, "DATA_DIR", data_dir):
                publication = describe_publication_uri(node.release_uri("2026-05-28"))
                self.assertEqual(
                    publication["locations"],
                    [
                        "https://github.com/owner/repo/releases/download/rso-archive-2026-05-28/rso-archive-2026-05-28.tar.gz",
                    ],
                )

            receipt = json.loads((day_dir / "storage.json").read_text(encoding="utf-8"))
            receipt["destinations"]["arweave"]["status"] = "submitted"
            receipt["destinations"]["arweave"]["bundle_sha256"] = "bb" * 32
            (day_dir / "storage.json").write_text(json.dumps(receipt), encoding="utf-8")

            with patch.object(node, "DATA_DIR", data_dir):
                publication = describe_publication_uri(node.release_uri("2026-05-28"))
                self.assertEqual(len(publication["locations"]), 1)

    def test_release_uri_falls_back_to_direct_location_without_bundle_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir) / "data"
            day_dir = data_dir / "2026" / "05" / "28"
            day_dir.mkdir(parents=True)
            (day_dir / "storage.json").write_text(
                json.dumps(
                    {
                        "destinations": {
                            "arweave": {
                                "status": "submitted",
                                "transaction_id": "abc123",
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(node, "DATA_DIR", data_dir):
                # auto mode falls back to the plain location, with or without node_id
                self.assertEqual(node.release_uri("2026-05-28"), "ar://abc123")
                self.assertEqual(
                    node.release_uri("2026-05-28", node_id="github:owner/repo"), "ar://abc123"
                )
                # an explicitly requested destination mode still requires a fingerprint
                with self.assertRaisesRegex(ValueError, "bundle fingerprint"):
                    node.release_uri("2026-05-28", mode="arweave", node_id="github:owner/repo")

    def test_release_uri_hash_only_when_no_publication_location(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(node, "DATA_DIR", Path(tmpdir) / "data"):
                # auto mode degrades to a hash-only attestation (empty uri) rather
                # than halting the snapshot, with or without node_id set.
                self.assertEqual(node.release_uri("2026-05-28"), "")
                self.assertEqual(
                    node.release_uri("2026-05-28", node_id="github:owner/repo"), ""
                )
                # an explicitly requested destination mode still errors.
                with self.assertRaisesRegex(ValueError, "destination"):
                    node.release_uri("2026-05-28", mode="github_release", node_id="github:owner/repo")

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
                            node_id="github:owner/repo",
                        )

            self.assertEqual(prepared.parent_hash, node.ZERO_BYTES32)
            self.assertEqual(prepared.block_hash, "0x" + "22" * 32)
            self.assertEqual(prepared.content_hash, "0x" + "11" * 32)
            publication = describe_publication_uri(prepared.uri)
            self.assertEqual(publication["bundleSha256"], "dd" * 32)
            self.assertEqual(publication["nodeId"], "github:owner/repo")
            self.assertEqual(
                publication["locations"],
                [
                    "https://github.com/owner/repo/releases/download/rso-archive-2026-04-20/rso-archive-2026-04-20.tar.gz"
                ],
            )
            self.assertEqual(artifact["schema"], "rso-signed-attestation-v1")
            self.assertEqual(artifact["blockHash"], "0x" + "22" * 32)
            self.assertEqual(artifact["node"]["nodeId"], "github:owner/repo")
            self.assertEqual(artifact["node"]["attester"], "0x" + "bb" * 20)
            entry = node.state_entry_from_signed_artifact(
                snapshot_date="2026-04-20",
                prepared=prepared.prepared,
                signed=prepared.signed,
                block_hash=prepared.block_hash,
            )
            self.assertEqual(entry["blockHash"], "0x" + "22" * 32)
            self.assertEqual(entry["chainId"], 1)
            self.assertEqual(entry["contractAddress"], "0x" + "aa" * 20)
            self.assertTrue(entry["signedPath"].startswith("data/attestations/signed/2026-04-20/"))
            self.assertEqual(entry["latestSignedPath"], "data/attestations/signed/2026-04-20.json")
            self.assertEqual(entry["submissionStatus"], "signed")
            state_path = Path(tmpdir) / "state.json"
            state = node.record_state_entry(state_path, entry)
            self.assertEqual(state["latest"]["date"], "2026-04-20")
            self.assertEqual(len(state["attestations"]), 1)

    def test_state_allows_multiple_same_day_attestations_for_different_uris(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            base_entry = {
                "date": "2026-04-20",
                "artifactId": "one",
                "chainId": 1,
                "contractAddress": "0x" + "aa" * 20,
                "docRef": 20260420000000,
                "attester": "0x" + "bb" * 20,
                "onBehalfOf": node.ZERO_ADDRESS,
                "parentHash": node.ZERO_BYTES32,
                "blockHash": "0x" + "11" * 32,
                "contentHash": "0x" + "22" * 32,
                "uri": "ar://one",
                "signedPath": "one.json",
                "signature": "0x1234",
                "submissionStatus": "signed",
                "updatedAt": "2026-06-01T00:00:00Z",
            }
            second_entry = dict(base_entry)
            second_entry["artifactId"] = "two"
            second_entry["uri"] = "ar://two"
            second_entry["updatedAt"] = "2026-06-01T00:01:00Z"

            state = node.record_state_entry(state_path, base_entry)
            state = node.record_state_entry(state_path, second_entry)

            self.assertEqual(len(state["attestations"]), 2)
            self.assertEqual(node.state_attestation_for_date(state, "2026-04-20")["uri"], "ar://two")
            self.assertIsNotNone(
                node.state_attestation_for_inputs(
                    state,
                    snapshot_date="2026-04-20",
                    chain_id=1,
                    contract_address="0x" + "aa" * 20,
                    attester="0x" + "bb" * 20,
                    on_behalf_of=node.ZERO_ADDRESS,
                    parent_hash=node.ZERO_BYTES32,
                    content_hash="0x" + "22" * 32,
                    uri="ar://one",
                )
            )

    def test_state_keeps_attestations_for_different_contract_domains(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            state_path = Path(tmpdir) / "state.json"
            base_entry = {
                "date": "2026-04-20",
                "artifactId": "one",
                "chainId": 11155111,
                "contractAddress": "0x" + "aa" * 20,
                "docRef": 20260420000000,
                "attester": "0x" + "bb" * 20,
                "onBehalfOf": node.ZERO_ADDRESS,
                "parentHash": node.ZERO_BYTES32,
                "blockHash": "0x" + "11" * 32,
                "contentHash": "0x" + "22" * 32,
                "uri": "ar://one",
                "signedPath": "one.json",
                "signature": "0x1234",
                "submissionStatus": "signed",
                "updatedAt": "2026-06-01T00:00:00Z",
            }
            replacement = dict(base_entry)
            replacement["artifactId"] = "two"
            replacement["contractAddress"] = "0x" + "cc" * 20
            replacement["updatedAt"] = "2026-06-01T00:01:00Z"

            state = node.record_state_entry(state_path, base_entry)
            state = node.record_state_entry(state_path, replacement)

            self.assertEqual(len(state["attestations"]), 2)
            self.assertIsNone(
                node.state_attestation_for_inputs(
                    state,
                    snapshot_date="2026-04-20",
                    chain_id=11155111,
                    contract_address="0x" + "dd" * 20,
                    attester="0x" + "bb" * 20,
                    on_behalf_of=node.ZERO_ADDRESS,
                    parent_hash=node.ZERO_BYTES32,
                    content_hash="0x" + "22" * 32,
                    uri="ar://one",
                )
            )


    # F2: parentHash is re-derived from the prior entry's own DocBlock fields.
    _GENESIS_ENTRY = {
        "date": "2026-04-20",
        "docRef": 20260420000000,
        "chainId": 11155111,
        "contractAddress": "0x" + "cc" * 20,
        "attester": "0x" + "bb" * 20,
        "parentHash": "0x" + "00" * 32,
        "contentHash": "0x1838a06696902f7397d78d906c4bd9f5e9d9e372725ccbf4629bbd377231a740",
        # Real on-chain Sepolia genesis (2026-04-20) blockHash.
        "blockHash": "0xe651a58398fe906551be2f4d2ec39bace608e7aa60f3c8c30cd276aac96e103e",
    }

    def test_block_hash_recompute_matches_onchain_vector(self):
        self.assertEqual(
            node.recompute_state_entry_block_hash(self._GENESIS_ENTRY),
            self._GENESIS_ENTRY["blockHash"],
        )
        self.assertEqual(
            node.verified_state_entry_block_hash(self._GENESIS_ENTRY),
            self._GENESIS_ENTRY["blockHash"],
        )

    def test_parent_hash_reverifies_prior_entry_block_hash(self):
        state = {"attestations": [self._GENESIS_ENTRY]}
        parent = node.parent_hash_for_date(
            "2026-04-21",
            state,
            chain_id=11155111,
            contract_address="0x" + "cc" * 20,
        )
        self.assertEqual(parent, self._GENESIS_ENTRY["blockHash"])

    def test_parent_hash_fails_closed_on_tampered_content_hash(self):
        tampered = dict(self._GENESIS_ENTRY)
        tampered["contentHash"] = "0x" + "ff" * 32
        state = {"attestations": [tampered]}
        with self.assertRaisesRegex(ValueError, "does not match blockHash re-derived"):
            node.parent_hash_for_date(
                "2026-04-21",
                state,
                chain_id=11155111,
                contract_address="0x" + "cc" * 20,
            )

    def test_parent_hash_does_not_cross_link_chains(self):
        state = {"attestations": [self._GENESIS_ENTRY]}
        # A different chainId must NOT follow the Sepolia entry as its parent.
        with self.assertRaisesRegex(ValueError, "no prior DocChain blockHash"):
            node.parent_hash_for_date(
                "2026-04-21",
                state,
                chain_id=1,
                contract_address="0x" + "cc" * 20,
            )

    def test_latest_state_attestation_filters_by_domain(self):
        state = {
            "attestations": [
                {"date": "2026-04-20", "chainId": 1, "contractAddress": "0x" + "aa" * 20, "blockHash": "0x" + "11" * 32},
                {"date": "2026-04-20", "chainId": 11155111, "contractAddress": "0x" + "cc" * 20, "blockHash": "0x" + "22" * 32},
            ]
        }
        found = node.latest_state_attestation_for_date(
            state, "2026-04-20", chain_id=1, contract_address="0x" + "aa" * 20
        )
        self.assertEqual(found["blockHash"], "0x" + "11" * 32)
        self.assertIsNone(
            node.latest_state_attestation_for_date(
                state, "2026-04-20", chain_id=1, contract_address="0x" + "cc" * 20
            )
        )

    # F3: refuse a second, contradictory attestation for a docRef already signed.
    def test_conflicting_content_state_attestation_detects_equivocation(self):
        state = {"attestations": [self._GENESIS_ENTRY]}
        conflict = node.conflicting_content_state_attestation(
            state,
            chain_id=11155111,
            contract_address="0x" + "cc" * 20,
            attester="0x" + "bb" * 20,
            doc_ref=20260420000000,
            content_hash="0x" + "ff" * 32,
        )
        self.assertIsNotNone(conflict)
        # Same contentHash is not a conflict.
        self.assertIsNone(
            node.conflicting_content_state_attestation(
                state,
                chain_id=11155111,
                contract_address="0x" + "cc" * 20,
                attester="0x" + "bb" * 20,
                doc_ref=20260420000000,
                content_hash=self._GENESIS_ENTRY["contentHash"],
            )
        )
        # A different attester is a separate identity, not equivocation.
        self.assertIsNone(
            node.conflicting_content_state_attestation(
                state,
                chain_id=11155111,
                contract_address="0x" + "cc" * 20,
                attester="0x" + "dd" * 20,
                doc_ref=20260420000000,
                content_hash="0x" + "ff" * 32,
            )
        )


if __name__ == "__main__":
    unittest.main()
