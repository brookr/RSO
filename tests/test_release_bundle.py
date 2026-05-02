import json
import os
import tarfile
import tempfile
import unittest
from pathlib import Path

from pipeline import snapshot


def gp_record(cat_id="1"):
    return {
        "NORAD_CAT_ID": cat_id,
        "CREATION_DATE": "2026-04-18T05:00:00",
        "EPOCH": "2026-04-18T04:00:00",
        "MEAN_MOTION": "15.0",
        "ECCENTRICITY": "0.0001",
        "INCLINATION": "51.6",
        "RA_OF_ASC_NODE": "10.0",
        "ARG_OF_PERICENTER": "20.0",
        "MEAN_ANOMALY": "30.0",
    }


class ReleaseBundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.original_data_dir = snapshot.DATA_DIR
        snapshot.DATA_DIR = self.root / "data"

    def tearDown(self):
        snapshot.DATA_DIR = self.original_data_dir
        self.tmp.cleanup()

    def archive_day(self, current_date_str="2026-04-18"):
        records = [gp_record("1"), gp_record("2")]
        data = sorted(records, key=snapshot.catalog_id_sort_key)
        return snapshot.save_snapshot(
            current_date_str,
            snapshot.canonicalize(data),
            data,
            "genesis_from_gp",
            "current_gp_genesis",
            ["/class/gp/orderby/NORAD_CAT_ID%20asc/format/json"],
            observed_at_utc=f"{current_date_str}T00:15:00Z",
            state_as_of_utc=f"{current_date_str}T00:00:00Z",
        )

    def test_release_bundle_is_deterministic(self):
        self.archive_day()

        first = snapshot.build_release_bundle(
            "2026-04-18", output_dir=self.root / "first", min_count=1
        )
        second = snapshot.build_release_bundle(
            "2026-04-18", output_dir=self.root / "second", min_count=1
        )

        self.assertEqual(first["bundle_sha256"], second["bundle_sha256"])
        self.assertEqual(
            Path(first["path"]).read_bytes(),
            Path(second["path"]).read_bytes(),
        )

    def test_release_bundle_contains_expected_files_and_manifest(self):
        manifest = self.archive_day()

        bundle = snapshot.build_release_bundle(
            "2026-04-18", output_dir=self.root / "bundle", min_count=1
        )

        with tarfile.open(bundle["path"], mode="r:gz") as tar:
            names = sorted(tar.getnames())
            self.assertEqual(names, ["catalog.json.gz", "manifest.json", "release-manifest.json"])
            release_manifest = json.load(tar.extractfile("release-manifest.json"))

        self.assertEqual(release_manifest["date"], "2026-04-18")
        self.assertEqual(release_manifest["catalog_sha256"], manifest["sha256"])
        self.assertEqual(release_manifest["object_count"], 2)
        self.assertEqual(snapshot.release_tag("2026-04-18"), "rso-archive-2026-04-18")
        self.assertEqual(
            snapshot.release_asset_name("2026-04-18"),
            "rso-archive-2026-04-18.tar.gz",
        )

    def test_github_release_publish_skips_existing_asset_without_force(self):
        calls = []
        original_release_payload = snapshot.github_release_payload
        original_resolve_repo = snapshot.resolve_github_repo
        original_upload = snapshot.github_upload_release_asset
        try:
            snapshot.resolve_github_repo = lambda repo=None: "OMPub/RSO"
            snapshot.github_release_payload = lambda tag, repo=None, allow_missing=False: {
                "id": 1,
                "upload_url": "https://uploads.github.com/repos/OMPub/RSO/releases/1/assets{?name,label}",
                "assets": [
                    {
                        "id": 2,
                        "name": "rso-archive-2026-04-18.tar.gz",
                    }
                ],
            }
            snapshot.github_upload_release_asset = lambda release, bundle: calls.append(
                ("upload", release, bundle)
            )
            bundle = {
                "date": "2026-04-18",
                "tag": "rso-archive-2026-04-18",
                "asset_name": "rso-archive-2026-04-18.tar.gz",
                "bytes": 123,
                "bundle_sha256": "a" * 64,
                "catalog_sha256": "b" * 64,
                "manifest_sha256": "c" * 64,
            }

            result = snapshot.publish_github_release(
                bundle,
                upload_policy="always_mirror",
                force=False,
            )

            self.assertEqual(result["status"], "skipped")
            self.assertEqual(result["reason"], "asset_exists")
            self.assertEqual(calls, [])
            receipt_path = snapshot.storage_receipt_path("2026-04-18")
            self.assertTrue(receipt_path.exists())
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(
                receipt["destinations"]["github_release"]["asset_name"],
                "rso-archive-2026-04-18.tar.gz",
            )
        finally:
            snapshot.github_release_payload = original_release_payload
            snapshot.resolve_github_repo = original_resolve_repo
            snapshot.github_upload_release_asset = original_upload

    def test_record_storage_destination_merges_destinations(self):
        bundle = {
            "date": "2026-04-18",
            "asset_name": "rso-archive-2026-04-18.tar.gz",
            "bytes": 123,
            "bundle_sha256": "a" * 64,
            "catalog_sha256": "b" * 64,
            "manifest_sha256": "c" * 64,
        }
        snapshot.record_storage_destination(
            bundle,
            "github_release",
            {"status": "created", "release_url": "https://example.invalid/release"},
        )
        snapshot.record_storage_destination(
            bundle,
            "arweave",
            {"status": "submitted", "transaction_id": "tx123"},
        )

        receipt = json.loads(
            snapshot.storage_receipt_path("2026-04-18").read_text(encoding="utf-8")
        )
        self.assertEqual(receipt["bundle_sha256"], "a" * 64)
        self.assertEqual(receipt["destinations"]["github_release"]["status"], "created")
        self.assertEqual(receipt["destinations"]["arweave"]["transaction_id"], "tx123")

    def test_github_create_release_targets_node_commit(self):
        calls = []
        original_request = snapshot.github_request
        try:
            snapshot.github_request = lambda method, url, payload=None, token_required=False, **kwargs: calls.append(
                {
                    "method": method,
                    "url": url,
                    "payload": payload,
                    "token_required": token_required,
                }
            ) or {"id": 1}
            snapshot.github_create_release(
                {
                    "tag": "rso-archive-2026-04-18",
                    "title": "RSO Archive 2026-04-18",
                },
                "OMPub/RSO",
                "notes",
                target_commitish="abc123",
            )

            self.assertEqual(calls[0]["payload"]["target_commitish"], "abc123")
        finally:
            snapshot.github_request = original_request

    def test_publish_arweave_skips_existing_receipt_without_force(self):
        bundle = {
            "date": "2026-04-18",
            "asset_name": "rso-archive-2026-04-18.tar.gz",
            "bytes": 123,
            "bundle_sha256": "a" * 64,
            "catalog_sha256": "b" * 64,
            "manifest_sha256": "c" * 64,
            "path": str(self.root / "bundle.tar.gz"),
        }
        snapshot.record_storage_destination(
            bundle,
            "arweave",
            {
                "status": "submitted",
                "bundle_sha256": "a" * 64,
                "transaction_id": "existingtx",
            },
        )
        original_wallet = snapshot.arweave_wallet_jwk
        try:
            snapshot.arweave_wallet_jwk = lambda: {"kty": "RSA"}
            result = snapshot.publish_arweave_bundle(bundle, upload_policy="if_missing", force=False)
            self.assertEqual(result["status"], "skipped")
            self.assertEqual(result["reason"], "receipt_exists")
            self.assertEqual(result["transaction_id"], "existingtx")
        finally:
            snapshot.arweave_wallet_jwk = original_wallet

    def test_publish_arweave_uses_chunk_upload_when_not_inline(self):
        bundle = {
            "date": "2026-04-18",
            "asset_name": "rso-archive-2026-04-18.tar.gz",
            "bytes": 20,
            "bundle_sha256": "a" * 64,
            "catalog_sha256": "b" * 64,
            "manifest_sha256": "c" * 64,
            "path": str(self.root / "bundle.tar.gz"),
        }
        calls = []
        original_wallet = snapshot.arweave_wallet_jwk
        original_wallet_address = snapshot.arweave_wallet_address
        original_build = snapshot.arweave_build_transaction
        original_request = snapshot.arweave_request
        try:
            snapshot.arweave_wallet_jwk = lambda: {"kty": "RSA"}
            snapshot.arweave_wallet_address = lambda jwk: "addr123"
            snapshot.arweave_build_transaction = lambda bundle, jwk: {
                "transaction": {
                    "id": "tx123",
                    "reward": "99",
                    "last_tx": "anchor123",
                    "data_root": "root123",
                    "data_size": "20",
                },
                "bundle_bytes": b"01234567890123456789",
                "chunk_plan": {
                    "data_root": b"root",
                    "chunks": [
                        {"min_byte_range": 0, "max_byte_range": 10},
                        {"min_byte_range": 10, "max_byte_range": 20},
                    ],
                    "proofs": [
                        {"offset": 9, "proof": b"proof1"},
                        {"offset": 19, "proof": b"proof2"},
                    ],
                },
                "inline_data": False,
                "wallet_address": "addr123",
            }

            def fake_request(
                method,
                path,
                payload=None,
                headers=None,
                allow_http_errors=False,
                allow_not_found=False,
            ):
                calls.append((method, path, payload))
                if method == "POST" and path == "/tx":
                    return 200, {}
                if method == "POST" and path == "/chunk":
                    return 200, {}
                if method == "GET" and path == "/tx/tx123/status":
                    return 404, None
                raise AssertionError((method, path, payload))

            snapshot.arweave_request = fake_request
            result = snapshot.publish_arweave_bundle(bundle, upload_policy="if_missing", force=True)

            self.assertEqual(result["status"], "submitted")
            self.assertEqual([call[1] for call in calls], ["/tx", "/chunk", "/chunk", "/tx/tx123/status"])
            receipt = json.loads(
                snapshot.storage_receipt_path("2026-04-18").read_text(encoding="utf-8")
            )
            self.assertEqual(receipt["destinations"]["arweave"]["upload_mode"], "chunked")
            self.assertEqual(receipt["destinations"]["arweave"]["chunk_count"], 2)
        finally:
            snapshot.arweave_wallet_jwk = original_wallet
            snapshot.arweave_wallet_address = original_wallet_address
            snapshot.arweave_build_transaction = original_build
            snapshot.arweave_request = original_request

    def test_arweave_build_transaction_checks_wallet_balance(self):
        bundle_path = self.root / "bundle.tar.gz"
        bundle_path.write_bytes(b"bundle-bytes")
        bundle = {
            "date": "2026-04-18",
            "asset_name": "rso-archive-2026-04-18.tar.gz",
            "path": str(bundle_path),
            "bytes": bundle_path.stat().st_size,
            "bundle_sha256": "a" * 64,
            "catalog_sha256": "b" * 64,
            "manifest_sha256": "c" * 64,
        }
        original_request = snapshot.arweave_request
        original_sign = snapshot.rsa_pss_sign_sha256
        try:
            def fake_request(
                method,
                path,
                payload=None,
                headers=None,
                allow_http_errors=False,
                allow_not_found=False,
            ):
                if method == "GET" and path == f"/price/{bundle_path.stat().st_size}":
                    return 200, "100"
                if method == "GET" and path == "/tx_anchor":
                    return 200, "anchor123"
                if method == "GET" and path.startswith("/wallet/"):
                    return 200, "99"
                raise AssertionError((method, path, payload))

            snapshot.arweave_request = fake_request
            snapshot.rsa_pss_sign_sha256 = lambda jwk, message, salt_length=32: b"sig"
            with self.assertRaises(snapshot.SnapshotError) as raised:
                snapshot.arweave_build_transaction(
                    bundle,
                    {
                        "kty": "RSA",
                        "n": "AQAB",
                        "e": "AQAB",
                        "d": "AQAB",
                        "p": "AQAB",
                        "q": "AQAB",
                        "dp": "AQAB",
                        "dq": "AQAB",
                        "qi": "AQAB",
                    },
                )
            self.assertIn("below required reward", str(raised.exception))
        finally:
            snapshot.arweave_request = original_request
            snapshot.rsa_pss_sign_sha256 = original_sign

    def test_arweave_force_chunk_upload_env_disables_inline_data(self):
        bundle_path = self.root / "bundle.tar.gz"
        bundle_path.write_bytes(b"small-bundle")
        bundle = {
            "date": "2026-04-18",
            "asset_name": "rso-archive-2026-04-18.tar.gz",
            "path": str(bundle_path),
            "bytes": bundle_path.stat().st_size,
            "bundle_sha256": "a" * 64,
            "catalog_sha256": "b" * 64,
            "manifest_sha256": "c" * 64,
        }
        original_request = snapshot.arweave_request
        original_sign = snapshot.rsa_pss_sign_sha256
        original_force = os.environ.get("ARWEAVE_FORCE_CHUNK_UPLOAD")
        try:
            os.environ["ARWEAVE_FORCE_CHUNK_UPLOAD"] = "true"

            def fake_request(
                method,
                path,
                payload=None,
                headers=None,
                allow_http_errors=False,
                allow_not_found=False,
            ):
                if method == "GET" and path == f"/price/{bundle_path.stat().st_size}":
                    return 200, "100"
                if method == "GET" and path == "/tx_anchor":
                    return 200, "AQAB"
                if method == "GET" and path.startswith("/wallet/"):
                    return 200, "100"
                raise AssertionError((method, path, payload))

            snapshot.arweave_request = fake_request
            snapshot.rsa_pss_sign_sha256 = lambda jwk, message, salt_length=32: b"sig"
            upload = snapshot.arweave_build_transaction(
                bundle,
                {
                    "kty": "RSA",
                    "n": "AQAB",
                    "e": "AQAB",
                    "d": "AQAB",
                    "p": "AQAB",
                    "q": "AQAB",
                    "dp": "AQAB",
                    "dq": "AQAB",
                    "qi": "AQAB",
                },
            )
            self.assertFalse(upload["inline_data"])
            self.assertEqual(upload["transaction"]["data"], "")
        finally:
            snapshot.arweave_request = original_request
            snapshot.rsa_pss_sign_sha256 = original_sign
            if original_force is None:
                os.environ.pop("ARWEAVE_FORCE_CHUNK_UPLOAD", None)
            else:
                os.environ["ARWEAVE_FORCE_CHUNK_UPLOAD"] = original_force

    def test_arweave_chunk_upload_retries_transient_errors(self):
        calls = []
        upload = {
            "transaction": {
                "id": "tx123",
                "data_root": "root123",
                "data_size": "20",
            },
            "bundle_bytes": b"01234567890123456789",
            "chunk_plan": {
                "chunks": [{"min_byte_range": 0, "max_byte_range": 20}],
                "proofs": [{"offset": 19, "proof": b"proof"}],
            },
        }
        original_request = snapshot.arweave_request
        original_delay = snapshot.ARWEAVE_CHUNK_UPLOAD_RETRY_DELAY
        try:
            snapshot.ARWEAVE_CHUNK_UPLOAD_RETRY_DELAY = 0

            def fake_request(
                method,
                path,
                payload=None,
                headers=None,
                allow_http_errors=False,
                allow_not_found=False,
            ):
                calls.append((method, path, payload, allow_http_errors))
                if len(calls) == 1:
                    return 400, {"error": "data_root_not_found"}
                return 200, {}

            snapshot.arweave_request = fake_request
            snapshot.arweave_submit_chunks(upload)
            self.assertEqual(len(calls), 2)
            self.assertTrue(calls[0][3])
        finally:
            snapshot.arweave_request = original_request
            snapshot.ARWEAVE_CHUNK_UPLOAD_RETRY_DELAY = original_delay

    def test_arweave_nonfatal_failure_records_failed_receipt(self):
        bundle = {
            "date": "2026-04-18",
            "asset_name": "rso-archive-2026-04-18.tar.gz",
            "bytes": 123,
            "bundle_sha256": "a" * 64,
            "catalog_sha256": "b" * 64,
            "manifest_sha256": "c" * 64,
            "path": str(self.root / "bundle.tar.gz"),
        }
        original_publish = snapshot.publish_arweave_bundle
        try:
            snapshot.publish_arweave_bundle = lambda *args, **kwargs: (_ for _ in ()).throw(
                snapshot.SnapshotError("Arweave wallet addr has 0 winston")
            )
            result = snapshot.publish_arweave_bundle_nonfatal(bundle)

            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["reason"], "arweave_upload_failed")
            receipt = json.loads(
                snapshot.storage_receipt_path("2026-04-18").read_text(encoding="utf-8")
            )
            self.assertEqual(receipt["destinations"]["arweave"]["status"], "failed")
            self.assertIn("0 winston", receipt["destinations"]["arweave"]["error"])
        finally:
            snapshot.publish_arweave_bundle = original_publish


if __name__ == "__main__":
    unittest.main()
