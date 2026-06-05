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
    normalize_operator_backing,
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
        self.assertFalse(doc_ref["events"][0]["hasIdentityClaim"])
        self.assertEqual(doc_ref["events"][0]["identityAddress"], "")
        self.assertEqual(doc_ref["blockFingerprints"], doc_ref["blockHashes"])
        self.assertEqual(doc_ref["contentFingerprints"], doc_ref["contentHashes"])
        self.assertEqual(index["events"][0]["date"], "2026-05-19")
        self.assertEqual(index["events"][0]["onBehalfOf"], ZERO_ADDRESS)
        self.assertEqual(index["events"][0]["publication"], {"bundleSha256": "", "locations": []})

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
        self.assertTrue(indexed_event["hasIdentityClaim"])
        self.assertEqual(indexed_event["identityAddress"], "0x" + "2" * 40)
        self.assertEqual(indexed_event["operatorAttester"], "0x" + "a" * 40)
        self.assertEqual(indexed_event["backingAccount"], "0x" + "a" * 40)

    def test_build_static_index_reports_tdh_weighted_agreement_groups(self):
        locator = encode_publication_locator_uri(
            bundle_sha256="11" * 32,
            locations=["ar://abc123", "https://github.com/owner/repo/releases/download/tag/archive.tar.gz"],
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
            operator_backing={
                "0x" + "a" * 40: 100,
                "0x" + "b" * 40: 25,
            },
            indexed_at="2026-05-22T00:00:00Z",
        )

        groups = index["docRefs"]["20260519000000"]["agreementGroups"]
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0]["cardSpecificTdh"], 100)
        self.assertEqual(groups[0]["backingAccounts"], ["0x" + "a" * 40])
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
        )
        header, encoded = uri.split(",", 1)
        decoded_payload = json.loads(base64.b64decode(encoded).decode("utf-8"))

        publication = describe_publication_uri(uri)

        self.assertEqual(
            header,
            "data:application/vnd.ompub.rso.publication-locator.v1+json;base64",
        )
        self.assertNotIn("schema", decoded_payload)
        self.assertEqual(publication["bundleSha256"], "aa" * 32)
        self.assertEqual(publication["locations"], ["ar://abc123"])

    def test_publication_uri_accepts_direct_and_empty_forms(self):
        self.assertEqual(
            describe_publication_uri("ar://abc123"),
            {"bundleSha256": "", "locations": ["ar://abc123"]},
        )
        self.assertEqual(
            describe_publication_uri(""),
            {"bundleSha256": "", "locations": []},
        )

    def test_normalize_operator_backing_accepts_schema_records(self):
        self.assertEqual(
            normalize_operator_backing({"0X" + "AB" * 20: {"cardSpecificTdh": "42"}}),
            {"0x" + "ab" * 20: 42},
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
                            "0x" + "ab" * 20: {
                                "cardSpecificTdh": 99,
                                "backerCount": 3,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(cli.load_operator_backing(str(path)), {"0x" + "ab" * 20: 99})

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
        index = json.loads(
            (ROOT / "indexer/generated/sepolia/rso-docchain-index.json").read_text(
                encoding="utf-8"
            )
        )
        checkpoint = json.loads(
            (ROOT / "indexer/cache/sepolia/checkpoint.json").read_text(encoding="utf-8")
        )
        cache_lines = [
            line
            for line in (ROOT / "indexer/cache/sepolia/doc-attested.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]

        self.assertEqual(index["eventCount"], 5)
        self.assertEqual(index["docRefCount"], 5)
        self.assertEqual(len(cache_lines), 5)
        self.assertEqual(index["events"][0]["onBehalfOf"], ZERO_ADDRESS)
        self.assertFalse(index["events"][0]["hasIdentityClaim"])
        self.assertEqual(index["events"][0]["identityAddress"], "")
        self.assertIn('"on_behalf_of":"0x0000000000000000000000000000000000000000"', cache_lines[0])
        self.assertEqual(checkpoint["last_block"], index["toBlock"])
        self.assertEqual(index["chunkSize"], 10)
        self.assertEqual(
            list(index["docRefs"].keys()),
            [
                "20260515000000",
                "20260516000000",
                "20260517000000",
                "20260518000000",
                "20260519000000",
            ],
        )


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


if __name__ == "__main__":
    unittest.main()
