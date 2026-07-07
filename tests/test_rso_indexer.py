import base64
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from indexer import index_rso_attestations as cli
from indexer.rso_profile import (
    RSO_DOC_CHAIN_ID,
    SEPOLIA_DEPLOYMENT_BLOCK,
    ZERO_ADDRESS,
    ZERO_BYTES32,
    build_static_index,
    decorate_rso_index,
    describe_publication_uri,
    encode_publication_locator_uri,
    doc_ref_to_date,
    filter_rso_events,
    normalize_address,
    normalize_hex,
    normalize_node_id,
    normalize_operator_backing,
    normalize_direct_witnesses,
    normalize_verified_claims,
)
from vendor.docchain.model import DocAttested

ROOT = Path(__file__).resolve().parents[1]


class RsoIndexerTest(unittest.TestCase):
    def test_doc_ref_to_date_accepts_midnight_utc(self):
        self.assertEqual(doc_ref_to_date("20260519000000"), "2026-05-19")

    def test_doc_ref_to_date_rejects_bad_shape_and_invalid_dates(self):
        for bad_ref in ["20260519", "2026051900000x", "20260230000000"]:
            with self.subTest(bad_ref=bad_ref):
                with self.assertRaises(ValueError):
                    doc_ref_to_date(bad_ref)

    def test_doc_ref_to_date_rejects_non_midnight_refs(self):
        with self.assertRaises(ValueError):
            doc_ref_to_date("20260519000100")

    def test_normalize_hex_accepts_uppercase_prefix_and_body(self):
        self.assertEqual(normalize_hex("0XABCDEF"), "0xabcdef")

    def test_normalize_address_requires_twenty_bytes(self):
        self.assertEqual(normalize_address("0X" + "AB" * 20), "0x" + "ab" * 20)
        with self.assertRaises(ValueError):
            normalize_address("0x1234")

    def test_filter_rso_events_matches_profile(self):
        rso_event = make_event(doc_chain_id="0X" + RSO_DOC_CHAIN_ID[2:].upper())
        other_event = make_event(
            doc_chain_id="0x" + "1" * 64,
            doc_ref=20260519000000,
        )

        self.assertEqual(filter_rso_events([other_event, rso_event]), [rso_event])

    def test_build_static_index_groups_events_by_doc_ref(self):
        event = make_event(doc_ref=20260519000000, block_number=10861426)

        index = build_static_index(
            network="sepolia",
            chain_id=11155111,
            contract_address="0xace3a26fe2f993e351a0ef74fb727cfe1029884b",
            from_block=10849363,
            to_block=10861426,
            latest_chain_block=10861440,
            confirmations=12,
            chunk_size=2000,
            events=[event],
            indexed_at="2026-05-22T00:00:00Z",
        )

        self.assertEqual(index["eventCount"], 1)
        self.assertEqual(index["docRefCount"], 1)
        self.assertIn("20260519000000", index["docRefs"])
        doc_ref = index["docRefs"]["20260519000000"]
        self.assertEqual(doc_ref["date"], "2026-05-19")
        self.assertEqual(doc_ref["candidateCount"], 1)
        self.assertEqual(doc_ref["events"][0]["ethereumBlock"], 10861426)
        self.assertEqual(doc_ref["events"][0]["onBehalfOf"], ZERO_ADDRESS)
        self.assertEqual(doc_ref["events"][0]["onBehalfOf"], ZERO_ADDRESS)
        self.assertEqual(doc_ref["events"][0]["nodeAuthorizationStatus"], "not_claimed")
        self.assertEqual(doc_ref["blockFingerprints"], doc_ref["blockHashes"])
        self.assertEqual(doc_ref["contentFingerprints"], doc_ref["contentHashes"])
        self.assertEqual(index["events"][0]["date"], "2026-05-19")
        self.assertEqual(index["events"][0]["onBehalfOf"], ZERO_ADDRESS)
        self.assertEqual(index["events"][0]["publication"], {"nodeId": "", "bundleSha256": "", "locations": []})

    def test_build_static_index_stamps_attestation_timing(self):
        from datetime import datetime, timezone

        day_start = int(datetime(2026, 5, 19, tzinfo=timezone.utc).timestamp())
        same_day = make_event(doc_ref=20260519000000, block_number=100)
        late = make_event(
            doc_ref=20260519000000,
            block_number=200,
            attester="0x" + "b" * 40,
            transaction_hash="0x" + "9" * 64,
        )

        index = build_static_index(
            network="sepolia",
            chain_id=11155111,
            contract_address="0xace3a26fe2f993e351a0ef74fb727cfe1029884b",
            from_block=1,
            to_block=200,
            latest_chain_block=240,
            confirmations=12,
            chunk_size=2000,
            events=[same_day, late],
            indexed_at="2026-06-22T00:00:00Z",
            block_timestamps={100: day_start + 2 * 3600, 200: day_start + 31 * 86400},
        )

        doc_ref = index["docRefs"]["20260519000000"]
        by_block = {event["ethereumBlock"]: event for event in doc_ref["events"]}
        self.assertEqual(by_block[100]["attestedAtUtc"], "2026-05-19T02:00:00Z")
        self.assertEqual(by_block[100]["attestationLagDays"], 0)
        self.assertEqual(by_block[200]["attestationLagDays"], 31)
        self.assertEqual(doc_ref["firstAttestedAtUtc"], "2026-05-19T02:00:00Z")
        self.assertEqual(doc_ref["minAttestationLagDays"], 0)
        self.assertEqual(index["events"][0]["attestationLagDays"], 0)

    def test_build_static_index_without_timestamps_adds_no_timing(self):
        event = make_event(doc_ref=20260519000000, block_number=100)
        index = build_static_index(
            network="sepolia",
            chain_id=11155111,
            contract_address="0xace3a26fe2f993e351a0ef74fb727cfe1029884b",
            from_block=1,
            to_block=200,
            latest_chain_block=240,
            confirmations=12,
            chunk_size=2000,
            events=[event],
            indexed_at="2026-06-22T00:00:00Z",
        )
        doc_ref = index["docRefs"]["20260519000000"]
        self.assertNotIn("attestedAtUtc", doc_ref["events"][0])
        self.assertNotIn("firstAttestedAtUtc", doc_ref)

    def test_fetch_block_timestamps_caches_and_reuses(self):
        calls = []

        class FakeRpc:
            def call(self, method, params):
                calls.append((method, params))
                number = int(params[0], 16)
                return {"timestamp": hex(number * 12)}

        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "block-timestamps.json"
            # latest_block far above the queries: all blocks are finality-deep,
            # so caching behaves as before
            first = cli.fetch_block_timestamps(FakeRpc(), [100, 200, 100], cache_path, latest_block=10_000)
            self.assertEqual(first, {100: 1200, 200: 2400})
            self.assertEqual(len(calls), 2)

            second = cli.fetch_block_timestamps(FakeRpc(), [100, 200, 300], cache_path, latest_block=10_000)
            self.assertEqual(second[300], 3600)
            self.assertEqual(len(calls), 3)  # only the new block was fetched

    def test_fetch_block_timestamps_does_not_persist_shallow_blocks(self):
        calls = []

        class FakeRpc:
            def call(self, method, params):
                calls.append((method, params))
                number = int(params[0], 16)
                return {"timestamp": hex(number * 12)}

        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "block-timestamps.json"
            # block 200 is within TIMESTAMP_FINALITY_DEPTH of the tip: it must be
            # resolved but NOT persisted (it could still reorg)
            out = cli.fetch_block_timestamps(FakeRpc(), [100, 200], cache_path, latest_block=220)
            self.assertEqual(out, {100: 1200, 200: 2400})
            import json as _json
            cached = _json.loads(cache_path.read_text()) if cache_path.exists() else {}
            self.assertIn("100", cached)
            self.assertNotIn("200", cached)
            # a second run refetches the shallow block, reuses the deep one
            cli.fetch_block_timestamps(FakeRpc(), [100, 200], cache_path, latest_block=220)
            shallow_fetches = [c for c in calls if int(c[1][0], 16) == 200]
            self.assertEqual(len(shallow_fetches), 2)

    def test_build_static_index_exposes_identity_claims(self):
        event = make_event(
            doc_ref=20260519000000,
            block_number=10861426,
            on_behalf_of="0x" + "2" * 40,
        )

        index = build_static_index(
            network="sepolia",
            chain_id=11155111,
            contract_address="0xace3a26fe2f993e351a0ef74fb727cfe1029884b",
            from_block=10849363,
            to_block=10861426,
            latest_chain_block=10861440,
            confirmations=12,
            chunk_size=2000,
            events=[event],
            indexed_at="2026-05-22T00:00:00Z",
        )

        indexed_event = index["events"][0]
        self.assertEqual(indexed_event["onBehalfOf"], "0x" + "2" * 40)
        self.assertEqual(indexed_event["onBehalfOf"], "0x" + "2" * 40)
        self.assertEqual(indexed_event["attester"], "0x" + "a" * 40)
        self.assertEqual(indexed_event["nodeId"], "")
        self.assertEqual(indexed_event["directWitnessTdh"], 0)
        self.assertEqual(indexed_event["nodeBackingTdh"], 0)
        self.assertEqual(indexed_event["combinedSupportTdh"], 0)

    def test_build_static_index_reports_tdh_weighted_agreement_groups(self):
        locator = encode_publication_locator_uri(
            bundle_sha256="11" * 32,
            locations=["ar://abc123", "https://github.com/owner/repo/releases/download/tag/archive.tar.gz"],
            node_id="github:owner/repo",
        )
        first = make_event(
            doc_ref=20260519000000,
            block_number=10861426,
            transaction_hash="0x" + "1" * 64,
            attester="0x" + "a" * 40,
            on_behalf_of="0x" + "2" * 40,
            block_hash="0x" + "c" * 64,
            content_hash="0x" + "d" * 64,
            uri=locator,
        )
        second = make_event(
            doc_ref=20260519000000,
            block_number=10861427,
            transaction_hash="0x" + "2" * 64,
            attester="0x" + "b" * 40,
            on_behalf_of="0x" + "3" * 40,
            block_hash="0x" + "e" * 64,
            content_hash="0x" + "f" * 64,
        )

        unverified = build_static_index(
            network="sepolia",
            chain_id=11155111,
            contract_address="0xace3a26fe2f993e351a0ef74fb727cfe1029884b",
            from_block=1,
            to_block=2,
            latest_chain_block=3,
            confirmations=0,
            chunk_size=10,
            events=[first, second],
            operator_backing={
                "github:owner/repo": 100,
                "github:other/repo": 25,
            },
            indexed_at="2026-05-22T00:00:00Z",
        )
        first_claim = unverified["events"][0]["claimFingerprint"]
        index = build_static_index(
            network="sepolia",
            chain_id=11155111,
            contract_address="0xace3a26fe2f993e351a0ef74fb727cfe1029884b",
            from_block=1,
            to_block=2,
            latest_chain_block=3,
            confirmations=0,
            chunk_size=10,
            events=[first, second],
            operator_backing={"github:owner/repo": 100},
            verified_claims={
                first_claim: {
                    "authorizationStatus": "verified",
                    "publicationStatus": "verified",
                    "nodeId": "github:owner/repo",
                    "claimedNodeId": "github:owner/repo",
                    "declaredAttester": "0x" + "a" * 40,
                    "attestationAttester": "0x" + "a" * 40,
                }
            },
            indexed_at="2026-05-22T00:00:00Z",
        )

        groups = index["docRefs"]["20260519000000"]["agreementGroups"]
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0]["nodeBackingTdh"], 100)
        self.assertEqual(groups[0]["combinedSupportTdh"], 100)
        self.assertEqual(groups[0]["backedNodeIds"], ["github:owner/repo"])
        self.assertEqual(groups[0]["bundleFingerprints"], ["11" * 32])
        self.assertEqual(
            groups[0]["locations"],
            ["ar://abc123", "https://github.com/owner/repo/releases/download/tag/archive.tar.gz"],
        )
        self.assertEqual(index["docRefs"]["20260519000000"]["leadingAgreementGroup"], groups[0])

    def test_publication_locator_uri_round_trips(self):
        uri = encode_publication_locator_uri(
            bundle_sha256="AA" * 32,
            locations=["ar://abc123"],
            node_id="github:owner/repo",
        )
        header, encoded = uri.split(",", 1)
        decoded_payload = json.loads(base64.b64decode(encoded).decode("utf-8"))

        publication = describe_publication_uri(uri)

        self.assertEqual(
            header,
            "data:application/vnd.ompub.rso.publication-locator.v1+json;base64",
        )
        self.assertNotIn("schema", decoded_payload)
        self.assertEqual(publication["nodeId"], "github:owner/repo")
        self.assertEqual(publication["bundleSha256"], "aa" * 32)
        self.assertEqual(publication["locations"], ["ar://abc123"])

    def test_publication_uri_accepts_direct_and_empty_forms(self):
        self.assertEqual(
            describe_publication_uri("ar://abc123"),
            {"nodeId": "", "bundleSha256": "", "locations": ["ar://abc123"]},
        )
        self.assertEqual(
            describe_publication_uri(""),
            {"nodeId": "", "bundleSha256": "", "locations": []},
        )

    def test_publication_locator_rejects_duplicate_locations(self):
        with self.assertRaisesRegex(ValueError, "unique"):
            encode_publication_locator_uri(
                bundle_sha256="aa" * 32,
                locations=["ar://same", "ar://same"],
                node_id="github:owner/repo",
            )

    def test_normalize_operator_backing_accepts_schema_records(self):
        self.assertEqual(
            normalize_operator_backing({"GitHub:Owner/Repo": {"cardSpecificTdh": "42"}}),
            {"github:owner/repo": 42},
        )
        self.assertEqual(
            normalize_operator_backing({"GitHub:Owner/Repo": {"cardSpecificTdhBacking": "43"}}),
            {"github:owner/repo": 43},
        )

    def test_normalize_node_id_accepts_github_short_form(self):
        self.assertEqual(normalize_node_id("Owner/Repo"), "github:owner/repo")

    def test_normalize_node_id_accepts_valid_domains_and_rejects_malformed_hosts(self):
        self.assertEqual(normalize_node_id("domain:Node.Example.com"), "domain:node.example.com")
        for node_id in (
            "domain:localhost",
            "domain:-node.example.com",
            "domain:node..example.com",
            "domain:node_example.com",
        ):
            with self.subTest(node_id=node_id):
                with self.assertRaisesRegex(ValueError, "domain:hostname"):
                    normalize_node_id(node_id)
        with self.assertRaisesRegex(ValueError, "GitHub node id"):
            normalize_node_id("github:../repo")

    def test_normalized_support_and_evidence_duplicates_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "duplicate normalized node"):
            normalize_operator_backing(
                {
                    "github:owner/repo": 100,
                    "GitHub:Owner/Repo": 200,
                }
            )
        with self.assertRaisesRegex(ValueError, "duplicate normalized fingerprints"):
            normalize_verified_claims(
                {
                    "aa" * 32: {"nodeId": "github:owner/repo"},
                    "AA" * 32: {"nodeId": "github:other/repo"},
                }
            )

    def test_direct_witness_normalization_rejects_conflicting_identity_data(self):
        with self.assertRaisesRegex(ValueError, "conflicting TDH"):
            normalize_direct_witnesses(
                {
                    "first": {
                        "identity": "alice",
                        "cardSpecificTdh": 100,
                        "accounts": ["0x" + "aa" * 20],
                    },
                    "second": {
                        "identity": "alice",
                        "cardSpecificTdh": 200,
                        "accounts": ["0x" + "bb" * 20],
                    },
                }
            )
        with self.assertRaisesRegex(ValueError, "conflicting direct witness"):
            normalize_direct_witnesses(
                {
                    "alice": {
                        "cardSpecificTdh": 100,
                        "accounts": ["0x" + "aa" * 20],
                    },
                    "bob": {
                        "cardSpecificTdh": 100,
                        "accounts": ["0x" + "aa" * 20],
                    },
                }
            )

    def test_cli_load_operator_backing_accepts_snapshot_schema(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "backing.json"
            path.write_text(
                json.dumps(
                    {
                        "schema": "rso-operator-backing-snapshot-v1",
                        "date": "2026-06-01",
                        "operators": {
                            "github:owner/repo": {
                                "cardSpecificTdhBacking": 99,
                                "backerCount": 3,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(cli.load_operator_backing(str(path)), {"github:owner/repo": 99})

    def test_github_looking_location_never_establishes_node_identity(self):
        locator = encode_publication_locator_uri(
            bundle_sha256="11" * 32,
            locations=["https://github.com/victim/repo/releases/download/tag/archive.tar.gz"],
        )
        index = build_test_index(
            [make_event(uri=locator)],
            operator_backing={"github:victim/repo": 999},
        )

        event = index["events"][0]
        self.assertEqual(event["claimedNodeId"], "")
        self.assertEqual(event["nodeId"], "")
        self.assertEqual(event["nodeBackingTdh"], 0)

    def test_verified_arweave_only_node_claim_receives_backing(self):
        locator = encode_publication_locator_uri(
            bundle_sha256="11" * 32,
            locations=["ar://abc123"],
            node_id="github:owner/repo",
        )
        event = make_event(uri=locator)
        unverified = build_test_index([event])
        fingerprint = unverified["events"][0]["claimFingerprint"]
        index = build_test_index(
            [event],
            operator_backing={"github:owner/repo": 250},
            verified_claims={
                fingerprint: verified_claim(
                    node_id="github:owner/repo",
                    attester=event.attester,
                )
            },
        )

        indexed = index["events"][0]
        self.assertEqual(indexed["nodeId"], "github:owner/repo")
        self.assertEqual(indexed["nodeBackingTdh"], 250)

    def test_unverified_signed_node_claim_receives_no_backing(self):
        event = make_event(
            uri=encode_publication_locator_uri(
                bundle_sha256="11" * 32,
                locations=["ar://abc123"],
                node_id="github:owner/repo",
            )
        )
        index = build_test_index(
            [event],
            operator_backing={"github:owner/repo": 250},
        )

        self.assertEqual(index["events"][0]["claimedNodeId"], "github:owner/repo")
        self.assertEqual(index["events"][0]["nodeAuthorizationStatus"], "unverified")
        self.assertEqual(index["events"][0]["nodeBackingTdh"], 0)

    def test_direct_witness_and_node_backing_are_separate_and_additive(self):
        locator = encode_publication_locator_uri(
            bundle_sha256="11" * 32,
            locations=["ar://abc123"],
            node_id="github:owner/repo",
        )
        event = make_event(uri=locator, attester="0x" + "a" * 40)
        fingerprint = build_test_index([event])["events"][0]["claimFingerprint"]
        index = build_test_index(
            [event],
            operator_backing={"github:owner/repo": 200},
            verified_claims={
                fingerprint: verified_claim(
                    node_id="github:owner/repo",
                    attester=event.attester,
                )
            },
            direct_witnesses={
                "alice": {
                    "cardSpecificTdh": 100,
                    "accounts": [event.attester, "0x" + "b" * 40],
                }
            },
        )

        indexed = index["events"][0]
        group = index["docRefs"]["20260519000000"]["agreementGroups"][0]
        self.assertEqual(indexed["directWitnessIdentity"], "alice")
        self.assertEqual(indexed["directWitnessTdh"], 100)
        self.assertEqual(indexed["nodeBackingTdh"], 200)
        self.assertEqual(group["combinedSupportTdh"], 300)

    def test_raw_attestation_count_cannot_break_a_support_tie(self):
        first = make_event(
            attester="0x" + "a" * 40,
            transaction_hash="0x" + "1" * 64,
        )
        second = make_event(
            attester="0x" + "b" * 40,
            transaction_hash="0x" + "2" * 64,
        )
        conflicting = make_event(
            attester="0x" + "c" * 40,
            transaction_hash="0x" + "3" * 64,
            block_hash="0x" + "e" * 64,
            content_hash="0x" + "f" * 64,
        )
        index = build_test_index([first, second, conflicting])

        groups = index["docRefs"]["20260519000000"]["agreementGroups"]
        self.assertEqual(sorted(group["attestationCount"] for group in groups), [1, 2])
        self.assertTrue(all(group["combinedSupportTdh"] == 0 for group in groups))
        self.assertIsNone(index["docRefs"]["20260519000000"]["leadingAgreementGroup"])

    def test_multiple_identity_accounts_and_node_keys_count_each_channel_once(self):
        locator = encode_publication_locator_uri(
            bundle_sha256="11" * 32,
            locations=["ar://abc123"],
            node_id="github:owner/repo",
        )
        first = make_event(
            uri=locator,
            attester="0x" + "a" * 40,
            transaction_hash="0x" + "1" * 64,
        )
        second = make_event(
            uri=locator,
            attester="0x" + "b" * 40,
            transaction_hash="0x" + "2" * 64,
        )
        initial = build_test_index([first, second])
        verified = {
            event["claimFingerprint"]: verified_claim(
                node_id="github:owner/repo",
                attester=event["attester"],
            )
            for event in initial["events"]
        }
        index = build_test_index(
            [first, second],
            operator_backing={"github:owner/repo": 200},
            verified_claims=verified,
            direct_witnesses={
                "alice": {
                    "cardSpecificTdh": 100,
                    "accounts": [first.attester, second.attester],
                }
            },
        )

        group = index["docRefs"]["20260519000000"]["agreementGroups"][0]
        self.assertEqual(group["attestationCount"], 2)
        self.assertEqual(group["directWitnessIdentities"], ["alice"])
        self.assertEqual(group["backedNodeIds"], ["github:owner/repo"])
        self.assertEqual(group["directWitnessTdh"], 100)
        self.assertEqual(group["nodeBackingTdh"], 200)
        self.assertEqual(group["combinedSupportTdh"], 300)

    def test_equivocating_identity_and_node_do_not_multiply_or_count(self):
        locator = encode_publication_locator_uri(
            bundle_sha256="11" * 32,
            locations=["ar://abc123"],
            node_id="github:owner/repo",
        )
        first = make_event(uri=locator, transaction_hash="0x" + "1" * 64)
        second = make_event(
            uri=locator,
            transaction_hash="0x" + "2" * 64,
            block_hash="0x" + "e" * 64,
            content_hash="0x" + "f" * 64,
        )
        initial = build_test_index([first, second])
        verified = {
            event["claimFingerprint"]: verified_claim(
                node_id="github:owner/repo",
                attester=event["attester"],
            )
            for event in initial["events"]
        }
        index = build_test_index(
            [first, second],
            operator_backing={"github:owner/repo": 200},
            verified_claims=verified,
            direct_witnesses={
                "alice": {
                    "cardSpecificTdh": 100,
                    "accounts": [first.attester],
                }
            },
        )

        for group in index["docRefs"]["20260519000000"]["agreementGroups"]:
            self.assertEqual(group["directWitnessTdh"], 0)
            self.assertEqual(group["nodeBackingTdh"], 0)
            self.assertEqual(group["equivocatingDirectWitnessIdentities"], ["alice"])
            self.assertEqual(group["equivocatingNodes"], ["github:owner/repo"])

    def test_cli_loads_tdh_support_and_only_verified_sweeper_records(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            support_path = Path(tmpdir) / "support.json"
            report_path = Path(tmpdir) / "report.json"
            support_path.write_text(
                json.dumps(
                    {
                        "schema": "rso-tdh-support-snapshot-v1",
                        "identities": {
                            "alice": {
                                "cardSpecificTdh": 100,
                                "accounts": ["0x" + "aa" * 20],
                            }
                        },
                        "operators": {
                            "github:owner/repo": {"cardSpecificTdhBacking": 200}
                        },
                    }
                ),
                encoding="utf-8",
            )
            report_path.write_text(
                json.dumps(
                    {
                        "schema": "rso-sweeper-date-report-v1",
                        "operators": [
                            {
                                "status": "submitted",
                                "authorizationStatus": "verified",
                                "publicationStatus": "verified",
                                "claimFingerprint": "11" * 32,
                                "nodeId": "github:owner/repo",
                                "claimedNodeId": "github:owner/repo",
                                "declaredAttester": "0x" + "aa" * 20,
                                "attestationAttester": "0x" + "aa" * 20,
                            },
                            {
                                "status": "candidate",
                                "authorizationStatus": "verified",
                                "publicationStatus": "pending",
                                "claimFingerprint": "22" * 32,
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(cli.load_operator_backing(str(support_path)), {"github:owner/repo": 200})
            self.assertIn("alice", cli.load_direct_witnesses(str(support_path)))
            self.assertEqual(list(cli.load_verified_claims(str(report_path))), ["11" * 32])

    def test_tdh_support_is_applied_only_to_matching_archive_date(self):
        first = make_event(
            doc_ref=20260519000000,
            transaction_hash="0x" + "1" * 64,
        )
        second = make_event(
            doc_ref=20260520000000,
            transaction_hash="0x" + "2" * 64,
        )
        index = build_test_index(
            [first, second],
            direct_witnesses_by_date={
                "2026-05-19": {
                    "alice": {
                        "cardSpecificTdh": 100,
                        "accounts": [first.attester],
                    }
                },
                "2026-05-20": {
                    "alice": {
                        "cardSpecificTdh": 250,
                        "accounts": [second.attester],
                    }
                },
            },
        )

        by_date = {event["date"]: event for event in index["events"]}
        self.assertEqual(by_date["2026-05-19"]["directWitnessTdh"], 100)
        self.assertEqual(by_date["2026-05-20"]["directWitnessTdh"], 250)
        self.assertEqual(index["tdhSupportDates"], ["2026-05-19", "2026-05-20"])
        self.assertEqual(index["directWitnessAccountCount"], 1)

    def test_cli_allows_repeated_equivalent_sweeper_evidence(self):
        record = {
            "status": "submitted",
            "authorizationStatus": "verified",
            "publicationStatus": "verified",
            "claimFingerprint": "11" * 32,
            "nodeId": "github:owner/repo",
            "claimedNodeId": "github:owner/repo",
            "declaredAttester": "0x" + "aa" * 20,
            "attestationAttester": "0x" + "aa" * 20,
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            for name, observed_at in (
                ("one.json", "2026-06-01T01:00:00Z"),
                ("two.json", "2026-06-01T02:00:00Z"),
            ):
                payload = {
                    "schema": "rso-sweeper-date-report-v1",
                    "operators": [{**record, "observedAt": observed_at}],
                }
                (Path(tmpdir) / name).write_text(json.dumps(payload), encoding="utf-8")

            claims = cli.load_verified_claims(tmpdir)

        self.assertEqual(list(claims), ["11" * 32])

    def test_build_static_index_dedupes_duplicate_events(self):
        event = make_event(doc_ref=20260519000000, transaction_hash="0x" + "1" * 64)

        index = build_static_index(
            network="sepolia",
            chain_id=11155111,
            contract_address="0xace3a26fe2f993e351a0ef74fb727cfe1029884b",
            from_block=1,
            to_block=2,
            latest_chain_block=3,
            confirmations=0,
            chunk_size=10,
            events=[event, event],
            indexed_at="2026-05-22T00:00:00Z",
        )

        self.assertEqual(index["eventCount"], 1)
        self.assertEqual(index["docRefs"]["20260519000000"]["candidateCount"], 1)

    def test_decorate_rso_index_rejects_malformed_doc_refs(self):
        with self.assertRaises(ValueError):
            decorate_rso_index({"docRefs": {"not-a-ref": {"events": []}}, "events": []})

    def test_decorate_rso_index_rejects_malformed_events(self):
        with self.assertRaisesRegex(ValueError, "events"):
            decorate_rso_index({"docRefs": {"20260519000000": {"events": {}}}, "events": []})

    def test_decorate_rso_index_rejects_malformed_on_behalf_of(self):
        with self.assertRaisesRegex(ValueError, "20 bytes"):
            decorate_rso_index(
                {
                    "docRefs": {
                        "20260519000000": {
                            "events": [
                                {"docRef": 20260519000000, "onBehalfOf": "0x1234"}
                            ]
                        }
                    },
                    "events": [],
                }
            )

    def test_cli_parse_block_accepts_decimal_and_hex(self):
        self.assertEqual(cli.parse_block("123"), 123)
        self.assertEqual(cli.parse_block("0x7b"), 123)
        with self.assertRaises(ValueError):
            cli.parse_block("-1")

    def test_cli_resolve_to_block_applies_confirmations(self):
        self.assertEqual(cli.resolve_to_block("latest", latest_block=100, confirmations=12), 88)
        self.assertEqual(cli.resolve_to_block("0x64", latest_block=200, confirmations=12), 100)

    def test_cli_network_config_validates_chunk_and_confirmation_inputs(self):
        args = SimpleNamespace(network="sepolia", confirmations=0, chunk_size=10)
        self.assertEqual(cli.network_config(args)["from_block"], SEPOLIA_DEPLOYMENT_BLOCK)

        with self.assertRaises(ValueError):
            cli.network_config(SimpleNamespace(network="sepolia", confirmations=-1, chunk_size=10))
        with self.assertRaises(ValueError):
            cli.network_config(SimpleNamespace(network="sepolia", confirmations=0, chunk_size=0))

    def test_cli_network_config_supports_custom_network_from_args(self):
        args = SimpleNamespace(
            network="custom",
            confirmations=0,
            chunk_size=10,
            chain_id=1,
            contract_address="0x" + "aa" * 20,
            deployment_block=123,
        )

        config = cli.network_config(args)

        self.assertEqual(config["chain_id"], 1)
        self.assertEqual(config["address"], "0x" + "aa" * 20)
        self.assertEqual(config["from_block"], 123)

    def test_cli_network_config_requires_custom_inputs(self):
        args = SimpleNamespace(
            network="custom",
            confirmations=0,
            chunk_size=10,
            chain_id=None,
            contract_address=None,
            deployment_block=None,
        )

        with patch.dict(
            "os.environ",
            {
                "RSO_DOCCHAIN_CHAIN_ID": "",
                "DOCCHAIN_CHAIN_ID": "",
                "RSO_DOCCHAIN_ADDRESS": "",
                "DOCCHAIN_ADDRESS": "",
                "RSO_DOCCHAIN_DEPLOYMENT_BLOCK": "",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(ValueError, "chain-id"):
                cli.network_config(args)

    def test_cli_progress_callback_respects_interval(self):
        args = SimpleNamespace(quiet=False, progress_every_chunks=2)
        callback = cli.progress_callback(args)
        stream = io.StringIO()

        with patch("sys.stderr", stream):
            callback(1, 10, 0, 0)
            callback(11, 20, 1, 1)

        self.assertIn("scanned through block 20", stream.getvalue())

    def test_cli_progress_callback_can_be_disabled(self):
        self.assertIsNone(cli.progress_callback(SimpleNamespace(quiet=True, progress_every_chunks=1)))
        self.assertIsNone(cli.progress_callback(SimpleNamespace(quiet=False, progress_every_chunks=0)))

    def test_cli_main_uses_generic_cache_pipeline(self):
        captured_index = {}

        class FakeRpc:
            def __init__(self, rpc_url, timeout):
                self.rpc_url = rpc_url
                self.timeout = timeout

            def block_number(self):
                return 10861440

            def call(self, method, params):
                assert method == "eth_getBlockByNumber"
                # canonical hash matches make_event's ethereum_block_hash, so
                # reorg reconciliation sees a healthy chain and purges nothing
                return {"timestamp": hex(1750000000), "hash": "0x" + "9" * 64}

        def fake_write_json(path, payload):
            captured_index["path"] = path
            captured_index["payload"] = payload

        with tempfile.TemporaryDirectory() as tmpdir:
            args = [
                "index_rso_attestations.py",
                "--rpc-url",
                "https://example.invalid",
                "--from-block",
                "10861420",
                "--to-block",
                "10861440",
                "--cache",
                str(Path(tmpdir) / "events.jsonl"),
                "--checkpoint",
                str(Path(tmpdir) / "checkpoint.json"),
                "--out",
                str(Path(tmpdir) / "index.json"),
                "--quiet",
            ]
            with patch.object(sys, "argv", args):
                with patch("indexer.index_rso_attestations.EthereumRpc", FakeRpc):
                    with patch(
                        "indexer.index_rso_attestations.update_event_cache",
                        return_value=SimpleNamespace(chunk_count=1, new_event_count=1),
                    ) as update:
                        with patch(
                            "indexer.index_rso_attestations.load_event_cache",
                            return_value=[make_event(doc_ref=20260519000000)],
                        ):
                            with patch(
                                "indexer.index_rso_attestations.write_json_file",
                                side_effect=fake_write_json,
                            ):
                                self.assertEqual(cli.main(), 0)

        update.assert_called_once()
        self.assertEqual(captured_index["payload"]["eventCount"], 1)
        self.assertEqual(captured_index["payload"]["chunkSize"], 10)

    def test_committed_sepolia_seed_artifacts_are_consistent(self):
        index_path = ROOT / "indexer/generated/sepolia/rso-docchain-index.json"
        checkpoint_path = ROOT / "indexer/cache/sepolia/checkpoint.json"
        cache_path = ROOT / "indexer/cache/sepolia/doc-attested.jsonl"
        existing_paths = [
            path for path in (index_path, checkpoint_path, cache_path) if path.exists()
        ]
        if not existing_paths:
            self.skipTest("Sepolia index has not been generated on this node yet")
        self.assertEqual(len(existing_paths), 3)

        index = json.loads(index_path.read_text(encoding="utf-8"))
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        cache_lines = [
            line
            for line in cache_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        cached_events = [json.loads(line) for line in cache_lines]
        indexed_events = index["events"]
        doc_refs = sorted({str(event["docRef"]) for event in indexed_events})

        self.assertGreater(index["eventCount"], 0)
        self.assertEqual(index["eventCount"], len(indexed_events))
        self.assertEqual(index["eventCount"], len(cached_events))
        self.assertEqual(index["docRefCount"], len(index["docRefs"]))
        self.assertEqual(list(index["docRefs"]), doc_refs)
        self.assertTrue(all("on_behalf_of" in event for event in cached_events))
        for event in indexed_events:
            self.assertNotIn("hasIdentityClaim", event)
            self.assertNotIn("identityAddress", event)
            self.assertNotIn("operatorAttester", event)
            self.assertNotIn("cardSpecificTdh", event)
        for doc_ref in index["docRefs"].values():
            for group in doc_ref["agreementGroups"]:
                self.assertIn("attesters", group)
                self.assertNotIn("operators", group)
        self.assertEqual(checkpoint["last_block"], index["toBlock"])
        self.assertEqual(checkpoint["address"], index["contractAddress"].lower())
        self.assertEqual(checkpoint["chain_id"], index["chainId"])
        self.assertEqual(index["chunkSize"], 10)


def make_event(
    *,
    doc_chain_id=RSO_DOC_CHAIN_ID,
    doc_ref=20260519000000,
    block_number=10861426,
    transaction_hash="0x" + "f" * 64,
    attester="0x" + "a" * 40,
    on_behalf_of=ZERO_ADDRESS,
    block_hash="0x" + "c" * 64,
    content_hash="0x" + "d" * 64,
    uri="",
):
    return DocAttested(
        doc_chain_id=doc_chain_id,
        attester=attester,
        doc_ref=doc_ref,
        on_behalf_of=on_behalf_of,
        submitter="0x" + "b" * 40,
        parent_hash=ZERO_BYTES32,
        block_hash=block_hash,
        content_hash=content_hash,
        uri_hash="0x" + "e" * 64,
        uri=uri,
        block_number=block_number,
        transaction_hash=transaction_hash,
        log_index=3,
        ethereum_block_hash="0x" + "9" * 64,
    )


def build_test_index(events, **support):
    return build_static_index(
        network="sepolia",
        chain_id=11155111,
        contract_address="0xace3a26fe2f993e351a0ef74fb727cfe1029884b",
        from_block=1,
        to_block=2,
        latest_chain_block=3,
        confirmations=0,
        chunk_size=10,
        events=events,
        indexed_at="2026-05-22T00:00:00Z",
        **support,
    )


def verified_claim(*, node_id, attester):
    return {
        "authorizationStatus": "verified",
        "publicationStatus": "verified",
        "nodeId": node_id,
        "claimedNodeId": node_id,
        "declaredAttester": attester,
        "attestationAttester": attester,
    }


if __name__ == "__main__":
    unittest.main()
