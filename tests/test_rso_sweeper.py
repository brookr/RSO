import gzip
import io
import json
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sweeper.rso_sweeper import (
    SweeperConfig,
    SweeperError,
    backing_snapshot_location,
    eligible_operators,
    handle_signed_attestation,
    host_allowed,
    normalize_backing_snapshot,
    parse_attest_doc_return,
    signed_attestation_url,
    transaction_hash_from_cast_output,
    validate_bundle_sha256,
    validate_fetch_url,
    validate_release_bundle,
)
from indexer.rso_profile import encode_publication_locator_uri
from vendor.docchain.attestation import prepare_attestation, signed_attestation


class RsoSweeperTest(unittest.TestCase):
    def test_parse_attest_doc_return(self):
        result = "0x" + "11" * 32 + "22" * 32 + "33" * 32
        parsed = parse_attest_doc_return(result)
        self.assertEqual(parsed["blockHash"], "0x" + "11" * 32)
        self.assertEqual(parsed["uriHash"], "0x" + "22" * 32)
        self.assertEqual(parsed["attestationKey"], "0x" + "33" * 32)

    def test_operator_url_defaults_to_raw_github_signed_artifact(self):
        self.assertEqual(
            signed_attestation_url(
                {"repository": "owner/repo", "branch": "node"},
                "2026-06-01",
            ),
            "https://raw.githubusercontent.com/owner/repo/node/data/attestations/signed/2026-06-01.json",
        )

    def test_backing_snapshot_location_supports_templates_and_directories(self):
        self.assertEqual(
            backing_snapshot_location("data/backing/{date}.json", "2026-06-01"),
            "data/backing/2026-06-01.json",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            self.assertEqual(
                backing_snapshot_location(tmpdir, "2026-06-01"),
                str(Path(tmpdir) / "2026-06-01.json"),
            )

    def test_normalize_backing_snapshot_accepts_operator_records(self):
        snapshot = normalize_backing_snapshot(
            {
                "schema": "rso-operator-backing-snapshot-v1",
                "date": "2026-06-01",
                "operators": {
                    "0X" + "BB" * 20: {
                        "cardSpecificTdh": "100",
                        "backerCount": 3,
                        "rank": 1,
                    }
                },
            },
            expected_date="2026-06-01",
        )

        self.assertEqual(snapshot["0x" + "bb" * 20]["cardSpecificTdh"], 100)
        self.assertEqual(snapshot["0x" + "bb" * 20]["backerCount"], 3)

    def test_eligible_operators_ranks_by_card_specific_tdh_and_caps(self):
        operators = [
            {"name": "low", "attester": "0x" + "11" * 20},
            {"name": "high", "attester": "0x" + "22" * 20},
            {"name": "zero", "attester": "0x" + "33" * 20},
        ]
        backing = normalize_backing_snapshot(
            {
                "operators": {
                    "0x" + "11" * 20: {"cardSpecificTdh": 25},
                    "0x" + "22" * 20: {"cardSpecificTdh": 100},
                    "0x" + "33" * 20: {"cardSpecificTdh": 0},
                }
            }
        )

        selected = eligible_operators(operators, backing, limit=1, min_card_specific_tdh=1)

        self.assertEqual([operator["name"] for operator in selected], ["high"])
        self.assertEqual(selected[0]["_backing"]["cardSpecificTdh"], 100)

    def test_publication_host_guards(self):
        self.assertTrue(host_allowed("github.com"))
        self.assertTrue(host_allowed("raw.githubusercontent.com"))
        self.assertTrue(host_allowed("arweave.net"))
        self.assertFalse(host_allowed("example.com"))

        with self.assertRaisesRegex(SweeperError, "HTTPS"):
            validate_fetch_url("http://github.com/owner/repo")
        with self.assertRaisesRegex(SweeperError, "not allowed"):
            validate_fetch_url("https://example.com/archive.tar.gz")

    def test_validate_release_bundle_checks_catalog_fingerprint(self):
        catalog = b'{"catalog":true}\n'
        content_hash = "0x" + __import__("hashlib").sha256(catalog).hexdigest()
        bundle = make_release_bundle(catalog, content_hash[2:])
        config = config_for_test(require_uri=True)

        validate_release_bundle(bundle, content_hash, config)

        with self.assertRaisesRegex(SweeperError, "fingerprint"):
            validate_release_bundle(bundle, "0x" + "00" * 32, config)

    def test_validate_bundle_sha256_checks_exact_bundle_bytes(self):
        bundle = b"bundle"
        bundle_hash = __import__("hashlib").sha256(bundle).hexdigest()

        validate_bundle_sha256(bundle, bundle_hash)
        validate_bundle_sha256(bundle, "0x" + bundle_hash)

        with self.assertRaisesRegex(SweeperError, "bundle fingerprint"):
            validate_bundle_sha256(bundle, "00" * 32)

    def test_handle_signed_attestation_validates_locator_locations_and_bundle_fingerprint(self):
        catalog = b'{"catalog":true}\n'
        content_hash = "0x" + __import__("hashlib").sha256(catalog).hexdigest()
        bundle = make_release_bundle(catalog, content_hash[2:])
        bundle_hash = __import__("hashlib").sha256(bundle).hexdigest()
        uri = encode_publication_locator_uri(
            bundle_sha256=bundle_hash,
            locations=[
                "ar://abc123",
                "https://github.com/owner/repo/releases/download/rso-archive-2026-06-01/rso-archive-2026-06-01.tar.gz",
            ],
        )
        fetched = []

        def fake_fetch_uri_bytes(location, config):
            fetched.append(location)
            return bundle

        with patch("sweeper.rso_sweeper.fetch_uri_bytes", side_effect=fake_fetch_uri_bytes):
            response = handle_signed_attestation(
                make_artifact(uri=uri, content_hash=content_hash),
                operator={"attester": "0x" + "bb" * 20, "_backing": backed_operator()},
                config=config_for_test(require_uri=True),
                rpc=FakeRpc(),
                expected_date="2026-06-01",
            )

        self.assertEqual(response["status"], "simulated")
        self.assertEqual(
            fetched,
            [
                "ar://abc123",
                "https://github.com/owner/repo/releases/download/rso-archive-2026-06-01/rso-archive-2026-06-01.tar.gz",
            ],
        )

    def test_handle_signed_attestation_rejects_bad_locator_bundle_fingerprint(self):
        catalog = b'{"catalog":true}\n'
        content_hash = "0x" + __import__("hashlib").sha256(catalog).hexdigest()
        bundle = make_release_bundle(catalog, content_hash[2:])
        uri = encode_publication_locator_uri(
            bundle_sha256="00" * 32,
            locations=["ar://abc123"],
        )

        with patch("sweeper.rso_sweeper.fetch_uri_bytes", return_value=bundle):
            with self.assertRaisesRegex(SweeperError, "bundle fingerprint"):
                handle_signed_attestation(
                    make_artifact(uri=uri, content_hash=content_hash),
                    operator={"attester": "0x" + "bb" * 20, "_backing": backed_operator()},
                    config=config_for_test(require_uri=True),
                    rpc=FakeRpc(),
                    expected_date="2026-06-01",
                )

    def test_handle_signed_attestation_keeps_direct_uri_compatibility(self):
        catalog = b'{"catalog":true}\n'
        content_hash = "0x" + __import__("hashlib").sha256(catalog).hexdigest()
        bundle = make_release_bundle(catalog, content_hash[2:])

        with patch("sweeper.rso_sweeper.fetch_uri_bytes", return_value=bundle) as fetch:
            response = handle_signed_attestation(
                make_artifact(uri="ar://abc123", content_hash=content_hash),
                operator={"attester": "0x" + "bb" * 20, "_backing": backed_operator()},
                config=config_for_test(require_uri=True),
                rpc=FakeRpc(),
                expected_date="2026-06-01",
            )

        self.assertEqual(response["status"], "simulated")
        fetch.assert_called_once()

    def test_handle_signed_attestation_checks_operator_authorization_and_simulates(self):
        config = config_for_test()
        rpc = FakeRpc()
        response = handle_signed_attestation(
            make_artifact(),
            operator={
                "attester": "0x" + "bb" * 20,
                "_backing": {
                    "attester": "0x" + "bb" * 20,
                    "cardSpecificTdh": 100,
                    "backerCount": 2,
                    "rank": 1,
                    "snapshotDate": "2026-06-01",
                },
            },
            config=config,
            rpc=rpc,
            expected_date="2026-06-01",
        )

        self.assertEqual(response["status"], "simulated")
        self.assertEqual(response["blockHash"], "0x" + "11" * 32)
        self.assertEqual(response["operatorAttester"], "0x" + "bb" * 20)
        self.assertEqual(response["sponsorship"]["scheme"], "rso-operator-backing-snapshot")
        self.assertEqual(response["sponsorship"]["cardSpecificTdh"], 100)
        self.assertTrue(any(call.startswith("0xd2b85e96") for call in rpc.calls))

    def test_handle_signed_attestation_rejects_unregistered_attester(self):
        config = config_for_test()
        rpc = FakeRpc()

        with self.assertRaisesRegex(SweeperError, "registered operator attester"):
            handle_signed_attestation(
                make_artifact(),
                operator={"attester": "0x" + "aa" * 20, "_backing": backed_operator()},
                config=config,
                rpc=rpc,
            )

    def test_handle_signed_attestation_rejects_operator_without_backing(self):
        config = config_for_test()
        rpc = FakeRpc()

        with self.assertRaisesRegex(SweeperError, "daily backing snapshot"):
            handle_signed_attestation(
                make_artifact(),
                operator={"attester": "0x" + "bb" * 20},
                config=config,
                rpc=rpc,
            )

    def test_handle_signed_attestation_rejects_wrong_swept_date(self):
        config = config_for_test()
        rpc = FakeRpc()

        with self.assertRaisesRegex(SweeperError, "swept date"):
            handle_signed_attestation(
                make_artifact(),
                operator={"attester": "0x" + "bb" * 20, "_backing": backed_operator()},
                config=config,
                rpc=rpc,
                expected_date="2026-06-02",
            )

    def test_handle_signed_attestation_can_submit_with_cast(self):
        config = config_for_test(dry_run=False)
        rpc = FakeRpc()
        tx = "0x" + "44" * 32

        def fake_run(command, check, capture_output, text):
            self.assertIn("--private-key", command)
            return subprocess.CompletedProcess(command, 0, stdout=f"transactionHash {tx}\n", stderr="")

        with patch.dict("os.environ", {"RSO_SWEEPER_PRIVATE_KEY": "0xabc"}, clear=False):
            response = handle_signed_attestation(
                make_artifact(),
                operator={"attester": "0x" + "bb" * 20, "_backing": backed_operator()},
                config=config,
                rpc=rpc,
                run=fake_run,
            )

        self.assertEqual(response["status"], "submitted")
        self.assertEqual(response["transactionHash"], tx)

    def test_transaction_hash_from_cast_output(self):
        tx = "0x" + "aa" * 32
        self.assertEqual(transaction_hash_from_cast_output(f"transactionHash {tx}\n"), tx)
        self.assertEqual(transaction_hash_from_cast_output(f"sent {tx}\n"), tx)


class FakeRpc:
    def __init__(self, *, simulation=None):
        self.simulation = simulation or "0x" + "11" * 32 + "22" * 32 + "33" * 32
        self.calls = []

    def call(self, method, params):
        if method != "eth_call":
            raise AssertionError(f"unexpected method {method}")
        data = params[0]["data"]
        self.calls.append(data)
        if data.startswith("0xd2b85e96"):
            return self.simulation
        raise AssertionError(f"unexpected calldata {data}")


def config_for_test(**overrides):
    values = {
        "rpc_url": "https://rpc.example",
        "docchain_address": "0x" + "aa" * 20,
        "dry_run": True,
        "require_uri": False,
    }
    values.update(overrides)
    return SweeperConfig(**values)


def backed_operator():
    return {
        "attester": "0x" + "bb" * 20,
        "cardSpecificTdh": 100,
        "backerCount": 2,
        "rank": 1,
        "snapshotDate": "2026-06-01",
    }


def make_artifact(uri="", content_hash="0x" + "22" * 32):
    prepared = prepare_attestation(
        chain_id=1,
        contract_address="0x" + "aa" * 20,
        attester="0x" + "bb" * 20,
        on_behalf_of="0x" + "cc" * 20,
        doc_chain_id="0x8621c2851714436d60da45cf0e11253114a4f2002f73ddc159b4dc88fea5611d",
        doc_ref=20260601000000,
        parent_hash="0x" + "11" * 32,
        content_hash=content_hash,
        uri=uri,
        deadline=4_000_000_000,
    )
    return {
        "schema": "rso-signed-attestation-v1",
        "date": "2026-06-01",
        "docRef": 20260601000000,
        "blockHash": "0x" + "33" * 32,
        "signed": signed_attestation(prepared, "0x1234"),
    }


def make_release_bundle(catalog: bytes, catalog_sha256: str) -> bytes:
    output = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as gz_file:
        with tarfile.open(fileobj=gz_file, mode="w") as tar:
            add_tar_bytes(tar, "release-manifest.json", json.dumps({"catalog_sha256": catalog_sha256}).encode("utf-8"))
            add_tar_bytes(tar, "manifest.json", json.dumps({"sha256": catalog_sha256}).encode("utf-8"))
            add_tar_bytes(tar, "catalog.json.gz", gzip.compress(catalog))
    return output.getvalue()


def add_tar_bytes(tar: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mtime = 0
    info.mode = 0o644
    tar.addfile(info, io.BytesIO(data))


if __name__ == "__main__":
    unittest.main()
