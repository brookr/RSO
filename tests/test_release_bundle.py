import gzip
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_release_bundle_from_existing_rehashes_catalog_member(self):
        manifest = self.archive_day()
        output_dir = self.root / "bundle"
        bundle = snapshot.build_release_bundle("2026-04-18", output_dir=output_dir, min_count=1)

        tampered_catalog = b'[{"NORAD_CAT_ID":"999"}]'
        release_manifest = {
            "catalog_sha256": manifest["sha256"],
            "manifest_sha256": bundle["manifest_sha256"],
            "object_count": manifest["object_count"],
            "files": bundle["files"],
        }
        with open(bundle["path"], "wb") as raw_file:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw_file, mtime=0) as gz_file:
                with tarfile.open(fileobj=gz_file, mode="w") as tar:
                    add_tar_bytes(tar, "release-manifest.json", json.dumps(release_manifest).encode("utf-8"))
                    add_tar_bytes(tar, "catalog.json.gz", gzip.compress(tampered_catalog))

        with self.assertRaisesRegex(snapshot.SnapshotError, "catalog bytes"):
            snapshot.release_bundle_from_existing("2026-04-18", output_dir=output_dir)

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

    def test_github_download_url_rejects_untrusted_hosts(self):
        with self.assertRaisesRegex(snapshot.SnapshotError, "not allowed"):
            snapshot.validate_github_download_url("https://example.com/archive.tar.gz")
        with self.assertRaisesRegex(snapshot.SnapshotError, "HTTPS"):
            snapshot.validate_github_download_url("http://github.com/OMPub/RSO/releases/download/a/b")

    def test_arweave_records_pending_receipt_before_chunk_upload(self):
        # H1 double-spend guard: if the run dies during chunk upload (after the
        # tx is broadcast and AR is spent), the tx id must already be on disk so
        # the next run's skip-guard does not build and pay for a fresh tx.
        bundle_path = self.root / "bundle.tar.gz"
        bundle_path.write_bytes(b"bundle-bytes")
        bundle = {
            "date": "2026-04-18", "asset_name": "rso-archive-2026-04-18.tar.gz",
            "bytes": bundle_path.stat().st_size, "bundle_sha256": "a" * 64,
            "catalog_sha256": "b" * 64, "manifest_sha256": "c" * 64,
            "path": str(bundle_path),
        }
        original_wallet = snapshot.arweave_wallet_jwk
        original_build = snapshot.arweave_build_transaction
        original_request = snapshot.arweave_request
        try:
            snapshot.arweave_wallet_jwk = lambda: {"kty": "RSA"}
            snapshot.arweave_build_transaction = lambda b, jwk: {
                "transaction": {"id": "txDIE", "reward": "99", "last_tx": "anchor",
                                "data_root": "root", "data_size": "12"},
                "bundle_bytes": b"bundle-bytes",
                "chunk_plan": {"data_root": b"root",
                               "chunks": [{"min_byte_range": 0, "max_byte_range": 12}],
                               "proofs": [{"offset": 11, "proof": b"proof"}]},
                "inline_data": False, "wallet_address": "addr",
            }

            def fake_request(method, path, payload=None, headers=None,
                             allow_http_errors=False, allow_not_found=False):
                if method == "POST" and path == "/tx":
                    return 200, {}
                if method == "POST" and path == "/chunk":
                    raise snapshot.SnapshotError("runner died mid-upload")
                raise AssertionError((method, path))

            snapshot.arweave_request = fake_request
            with self.assertRaises(snapshot.SnapshotError):
                snapshot.publish_arweave_bundle(bundle, force=True)

            # The receipt exists with the broadcast tx id -> a re-run skips it.
            receipt = snapshot.load_storage_receipt("2026-04-18")
            arw = receipt["destinations"]["arweave"]
            self.assertEqual(arw["transaction_id"], "txDIE")
            self.assertEqual(arw["status"], "pending")
        finally:
            snapshot.arweave_wallet_jwk = original_wallet
            snapshot.arweave_build_transaction = original_build
            snapshot.arweave_request = original_request

    def test_arweave_tx_confirmation_mapping(self):
        self.assertEqual(
            snapshot.arweave_tx_confirmation(200, {"block_height": 100, "number_of_confirmations": 5}),
            ("confirmed", 5, 100),
        )
        self.assertEqual(snapshot.arweave_tx_confirmation(404, None), ("pending", None, None))
        # 200 with no block data yet is still pending, not confirmed
        self.assertEqual(snapshot.arweave_tx_confirmation(200, {}), ("pending", None, None))

    def test_reconcile_promotes_pending_to_confirmed(self):
        with patch.object(snapshot, "LEDGER_PATH", self.root / "ledger.json"), patch.object(
            snapshot, "LATEST_POINTER_PATH", self.root / "latest.json"
        ):
            self.archive_day()
            bundle = {
                "date": "2026-04-18",
                "asset_name": "rso-archive-2026-04-18.tar.gz",
                "bytes": 1,
                "bundle_sha256": "a" * 64,
                "catalog_sha256": "b" * 64,
                "manifest_sha256": "c" * 64,
                "path": str(self.root / "b.tar.gz"),
            }
            snapshot.record_storage_destination(
                bundle, "arweave",
                {"status": "pending", "transaction_id": "txP", "bundle_sha256": "a" * 64},
            )
            original = snapshot.arweave_request
            try:
                snapshot.arweave_request = lambda *a, **k: (200, {"block_height": 7, "number_of_confirmations": 3})
                settled = snapshot.reconcile_arweave_pending(["2026-04-18"])
            finally:
                snapshot.arweave_request = original
            self.assertEqual(settled, 1)
            receipt = snapshot.load_storage_receipt("2026-04-18")
            arw = receipt["destinations"]["arweave"]
            self.assertEqual(arw["status"], "confirmed")
            self.assertEqual(arw["confirmations"], 3)
            self.assertIn("last_checked_at", arw)

    def test_reconcile_leaves_still_pending_unconfirmed(self):
        with patch.object(snapshot, "LEDGER_PATH", self.root / "ledger.json"), patch.object(
            snapshot, "LATEST_POINTER_PATH", self.root / "latest.json"
        ):
            self.archive_day()
            bundle = {
                "date": "2026-04-18", "asset_name": "x.tar.gz", "bytes": 1,
                "bundle_sha256": "a" * 64, "catalog_sha256": "b" * 64,
                "manifest_sha256": "c" * 64, "path": str(self.root / "b.tar.gz"),
            }
            snapshot.record_storage_destination(
                bundle, "arweave",
                {"status": "pending", "transaction_id": "txP", "bundle_sha256": "a" * 64},
            )
            original = snapshot.arweave_request
            try:
                snapshot.arweave_request = lambda *a, **k: (404, None)
                settled = snapshot.reconcile_arweave_pending(["2026-04-18"])
            finally:
                snapshot.arweave_request = original
            self.assertEqual(settled, 0)
            self.assertEqual(
                snapshot.load_storage_receipt("2026-04-18")["destinations"]["arweave"]["status"],
                "pending",
            )

    def test_arweave_gateway_is_https_arweave_net(self):
        with patch("pipeline.snapshot.socket.getaddrinfo", return_value=[(None, None, None, None, ("95.216.149.139", 443))]):
            snapshot.validate_arweave_gateway("https://arweave.net")
            with self.assertRaisesRegex(snapshot.SnapshotError, "HTTPS"):
                snapshot.validate_arweave_gateway("http://arweave.net")
            with self.assertRaisesRegex(snapshot.SnapshotError, "arweave.net"):
                snapshot.validate_arweave_gateway("https://example.com")

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

    def test_publish_arweave_retries_failed_receipt_without_force(self):
        bundle_path = self.root / "bundle.tar.gz"
        bundle_path.write_bytes(b"bundle-bytes")
        bundle = {
            "date": "2026-04-18",
            "asset_name": "rso-archive-2026-04-18.tar.gz",
            "bytes": bundle_path.stat().st_size,
            "bundle_sha256": "a" * 64,
            "catalog_sha256": "b" * 64,
            "manifest_sha256": "c" * 64,
            "path": str(bundle_path),
        }
        snapshot.record_storage_destination(
            bundle,
            "arweave",
            {
                "status": "failed",
                "bundle_sha256": "a" * 64,
                "error": "previous failure",
            },
        )
        calls = []
        original_wallet = snapshot.arweave_wallet_jwk
        original_build = snapshot.arweave_build_transaction
        original_request = snapshot.arweave_request
        try:
            snapshot.arweave_wallet_jwk = lambda: {"kty": "RSA"}
            snapshot.arweave_build_transaction = lambda bundle, jwk: {
                "transaction": {
                    "id": "tx123",
                    "reward": "99",
                    "last_tx": "anchor123",
                    "data_root": "root123",
                    "data_size": str(bundle_path.stat().st_size),
                },
                "bundle_bytes": b"bundle-bytes",
                "chunk_plan": {
                    "data_root": b"root",
                    "chunks": [{"min_byte_range": 0, "max_byte_range": 12}],
                    "proofs": [{"offset": 11, "proof": b"proof"}],
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
                calls.append((method, path))
                if method == "POST" and path == "/tx":
                    return 200, {}
                if method == "POST" and path == "/chunk":
                    return 200, {}
                if method == "GET" and path == "/tx/tx123/status":
                    return 404, None
                raise AssertionError((method, path, payload))

            snapshot.arweave_request = fake_request
            result = snapshot.publish_arweave_bundle(bundle, upload_policy="if_missing", force=False)

            # 404 status query == not yet mined -> honestly "pending", not a
            # blanket "submitted" that implies durability.
            self.assertEqual(result["status"], "pending")
            self.assertEqual(result["transaction_id"], "tx123")
            self.assertEqual(calls, [("POST", "/tx"), ("POST", "/chunk"), ("GET", "/tx/tx123/status")])
            receipt = snapshot.load_storage_receipt("2026-04-18")
            self.assertEqual(receipt["destinations"]["arweave"]["status"], "pending")
        finally:
            snapshot.arweave_wallet_jwk = original_wallet
            snapshot.arweave_build_transaction = original_build
            snapshot.arweave_request = original_request

    def test_publish_arweave_uses_chunk_upload(self):
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

            self.assertEqual(result["status"], "pending")
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

    def test_arweave_build_transaction_uses_chunk_upload(self):
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

    def test_arweave_chunk_plan_splits_large_bundle(self):
        bundle_path = self.root / "bundle.tar.gz"
        bundle_path.write_bytes(b"x" * (snapshot.ARWEAVE_MAX_CHUNK_SIZE + 1))
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
            self.assertGreater(len(upload["chunk_plan"]["chunks"]), 1)
        finally:
            snapshot.arweave_request = original_request
            snapshot.rsa_pss_sign_sha256 = original_sign

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

    def test_arweave_transaction_submit_retries_transient_http_errors(self):
        calls = []
        upload = {
            "transaction": {
                "id": "tx123",
                "data_root": "root123",
                "data_size": "20",
            },
        }
        original_request = snapshot.arweave_request
        original_delay = snapshot.ARWEAVE_TRANSACTION_RETRY_DELAY
        try:
            snapshot.ARWEAVE_TRANSACTION_RETRY_DELAY = 0

            def fake_request(
                method,
                path,
                payload=None,
                headers=None,
                allow_http_errors=False,
                allow_not_found=False,
            ):
                calls.append((method, path, allow_http_errors))
                if len(calls) == 1:
                    return 503, {"error": "service unavailable"}
                return 200, {}

            snapshot.arweave_request = fake_request
            snapshot.arweave_submit_transaction(upload)
            self.assertEqual(len(calls), 2)
            self.assertTrue(calls[0][2])
        finally:
            snapshot.arweave_request = original_request
            snapshot.ARWEAVE_TRANSACTION_RETRY_DELAY = original_delay

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


def add_tar_bytes(tar, arcname, data):
    info = tarfile.TarInfo(arcname)
    info.size = len(data)
    info.mtime = 0
    info.mode = 0o644
    tar.addfile(info, io.BytesIO(data))



class RebuiltBundleTests(ReleaseBundleTests):
    def archive_v2_day(self, current_date_str="2026-04-18"):
        manifest = self.archive_day(current_date_str)
        records = [gp_record("1"), gp_record("2")]
        data = sorted(records, key=snapshot.catalog_id_sort_key)
        annotations = snapshot.build_annotations(
            current_date_str,
            data,
            None,
            observed_at_utc=manifest["archived_at"],
            baseline=True,
        )
        day_dir = snapshot.snapshot_dir(current_date_str)
        snapshot.write_json(day_dir / "annotations.json", annotations)
        manifest["annotations_sha256"] = snapshot.sha256_path(day_dir / "annotations.json")
        snapshot.write_json(day_dir / "manifest.json", manifest)
        return manifest

    def write_conjunctions(self, current_date_str, manifest):
        day_dir = snapshot.snapshot_dir(current_date_str)
        conjunctions = snapshot.build_conjunctions(
            current_date_str,
            [
                {
                    "CDM_ID": "7",
                    "CREATED": f"{current_date_str} 05:00:00.000000",
                    "EMERGENCY_REPORTABLE": "Y",
                    "PC": "0.001",
                    "TCA": f"{current_date_str}T12:00:00.000000",
                    "SAT_1_ID": "1",
                    "SAT_1_NAME": "A",
                    "SAT_2_ID": "2",
                    "SAT_2_NAME": "B",
                    "MIN_RNG": "100",
                }
            ],
            observed_at_utc=manifest["archived_at"],
            window_start_utc=f"{current_date_str}T00:00:00Z",
            window_end_utc=f"{current_date_str}T00:00:00Z",
        )
        snapshot.write_json(day_dir / "conjunctions.json", conjunctions)
        manifest["conjunctions_sha256"] = snapshot.sha256_path(day_dir / "conjunctions.json")
        snapshot.write_json(day_dir / "manifest.json", manifest)
        return manifest

    def test_rebuilt_bundle_uses_plain_asset_name_and_includes_annotations(self):
        self.archive_v2_day()

        bundle = snapshot.build_rebuilt_bundle(
            "2026-04-18", output_dir=self.root / "out", min_count=1
        )

        self.assertEqual(bundle["asset_name"], "rso-archive-2026-04-18.tar.gz")
        with tarfile.open(bundle["path"], "r:gz") as tar:
            names = set(tar.getnames())
        self.assertIn("annotations.json", names)
        self.assertIn("catalog.json.gz", names)
        self.assertIn("manifest.json", names)

    def test_bundle_includes_conjunctions_when_present(self):
        manifest = self.archive_v2_day()
        self.write_conjunctions("2026-04-18", manifest)

        bundle = snapshot.build_rebuilt_bundle(
            "2026-04-18", output_dir=self.root / "out", min_count=1
        )

        with tarfile.open(bundle["path"], "r:gz") as tar:
            names = set(tar.getnames())
        self.assertIn("conjunctions.json", names)
        self.assertIn("conjunctions.json", [f["path"] for f in bundle["files"]])

    def test_validate_artifacts_checks_conjunctions_fingerprint_both_ways(self):
        manifest = self.archive_v2_day()
        day_dir = snapshot.snapshot_dir("2026-04-18")

        # file present without a manifest fingerprint
        (day_dir / "conjunctions.json").write_text("{}", encoding="utf-8")
        errors, _ = snapshot.validate_snapshot_artifacts("2026-04-18", min_count=1)
        self.assertTrue(any("manifest has no conjunctions_sha256" in e for e in errors))

        # consistent fingerprint validates clean
        manifest = self.write_conjunctions("2026-04-18", manifest)
        errors, _ = snapshot.validate_snapshot_artifacts("2026-04-18", min_count=1)
        self.assertEqual(errors, [])

        # tampered bytes are caught
        (day_dir / "conjunctions.json").write_text("{\"tampered\": true}", encoding="utf-8")
        errors, _ = snapshot.validate_snapshot_artifacts("2026-04-18", min_count=1)
        self.assertTrue(any("does not match manifest conjunctions_sha256" in e for e in errors))

        # declared but missing is caught
        (day_dir / "conjunctions.json").unlink()
        errors, _ = snapshot.validate_snapshot_artifacts("2026-04-18", min_count=1)
        self.assertTrue(any("conjunctions.json is missing" in e for e in errors))

    def test_save_snapshot_records_conjunctions_fingerprint(self):
        records = [gp_record("1")]
        data = sorted(records, key=snapshot.catalog_id_sort_key)
        conjunctions = snapshot.build_conjunctions(
            "2026-04-19",
            [],
            observed_at_utc="2026-04-19T00:20:00Z",
            window_start_utc="2026-04-18T00:00:00Z",
            window_end_utc="2026-04-19T00:00:00Z",
        )
        manifest = snapshot.save_snapshot(
            "2026-04-19",
            snapshot.canonicalize(data),
            data,
            "genesis_from_gp",
            "current_gp_genesis",
            [],
            conjunctions=conjunctions,
        )
        day_dir = snapshot.snapshot_dir("2026-04-19")
        self.assertTrue((day_dir / "conjunctions.json").exists())
        self.assertEqual(
            manifest["conjunctions_sha256"],
            snapshot.sha256_path(day_dir / "conjunctions.json"),
        )

    def test_rebuilt_bundle_requires_content_fields(self):
        self.archive_day()
        manifest_path = snapshot.snapshot_dir("2026-04-18") / "manifest.json"
        stored = json.loads(manifest_path.read_text(encoding="utf-8"))
        for key in ("content_schema", "content_excluded_fields", "content_sha256"):
            stored.pop(key, None)
        manifest_path.write_text(json.dumps(stored, indent=2) + "\n", encoding="utf-8")

        with self.assertRaises(snapshot.SnapshotError):
            snapshot.build_rebuilt_bundle(
                "2026-04-18", output_dir=self.root / "out", min_count=1
            )

    def test_rebuilt_bundle_refuses_adopted_days(self):
        self.archive_v2_day()
        snapshot.write_json(
            snapshot.storage_receipt_path("2026-04-18"),
            {
                "date": "2026-04-18",
                "bundle_sha256": "aa" * 32,
                "verified_from_upstream": "OMPub/RSO",
                "destinations": {"github_release": {"status": "submitted"}},
            },
        )

        with self.assertRaises(snapshot.SnapshotError):
            snapshot.build_rebuilt_bundle(
                "2026-04-18", output_dir=self.root / "out", min_count=1
            )


class ConsumerPointerTests(ReleaseBundleTests):
    def receipt(self, date="2026-04-18", arweave=True):
        receipt = {
            "date": date,
            "asset_name": f"rso-archive-{date}.tar.gz",
            "bundle_sha256": "ab" * 32,
            "destinations": {
                "github_release": {
                    "status": "created",
                    "asset_url": f"https://github.com/o/r/releases/download/rso-archive-{date}/rso-archive-{date}.tar.gz",
                }
            },
        }
        if arweave:
            receipt["destinations"]["arweave"] = {
                "status": "confirmed",
                "transaction_id": "txABC",
                "bundle_sha256": "ab" * 32,
            }
        return receipt

    def test_publication_fields_extracts_locations_and_hashes(self):
        fields = snapshot.publication_fields_from_receipt(self.receipt())
        self.assertEqual(fields["bundle_sha256"], "ab" * 32)
        self.assertTrue(fields["asset_url"].endswith("rso-archive-2026-04-18.tar.gz"))
        self.assertEqual(fields["arweave_tx"], "txABC")

    def test_publication_fields_ignores_stale_arweave_upload(self):
        receipt = self.receipt()
        receipt["destinations"]["arweave"]["bundle_sha256"] = "cd" * 32
        fields = snapshot.publication_fields_from_receipt(receipt)
        self.assertNotIn("arweave_tx", fields)

    def test_publication_fields_does_not_advertise_pending_arweave(self):
        receipt = self.receipt()
        receipt["destinations"]["arweave"]["status"] = "pending"
        fields = snapshot.publication_fields_from_receipt(receipt)
        # a pending tx's ar:// URL 404s; advertising it would be a lie
        self.assertNotIn("arweave_tx", fields)

    def test_publication_fields_records_adopted_from(self):
        receipt = self.receipt()
        receipt["verified_from_upstream"] = "OMPub/RSO"
        fields = snapshot.publication_fields_from_receipt(receipt)
        self.assertEqual(fields["adopted_from"], "OMPub/RSO")
        self.assertNotIn("adopted_from", snapshot.publication_fields_from_receipt(self.receipt()))

    def test_ledger_carries_publication_fields_after_publish(self):
        with patch.object(snapshot, "LEDGER_PATH", self.root / "ledger.json"):
            manifest = self.archive_day()
            snapshot.write_json(
                snapshot.storage_receipt_path("2026-04-18"), self.receipt()
            )
            snapshot.update_ledger(manifest)
            ledger = json.loads((self.root / "ledger.json").read_text())
        entry = ledger[-1]
        self.assertEqual(entry["arweave_tx"], "txABC")
        self.assertTrue(entry["asset_url"].endswith(".tar.gz"))
        self.assertEqual(entry["bundle_sha256"], "ab" * 32)

    def test_latest_pointer_describes_newest_day(self):
        with patch.object(snapshot, "LEDGER_PATH", self.root / "ledger.json"), patch.object(
            snapshot, "LATEST_POINTER_PATH", self.root / "latest.json"
        ):
            manifest = self.archive_day()
            snapshot.write_json(
                snapshot.storage_receipt_path("2026-04-18"), self.receipt()
            )
            snapshot.update_ledger(manifest)
            pointer = snapshot.write_latest_pointer()
            stored = json.loads((self.root / "latest.json").read_text())

        self.assertEqual(pointer, stored)
        self.assertEqual(stored["schema"], "rso-latest-v1")
        self.assertEqual(stored["date"], "2026-04-18")
        self.assertEqual(stored["tag"], "rso-archive-2026-04-18")
        self.assertEqual(stored["content_schema"], "rso-core-v1")
        self.assertEqual(stored["sha256"], manifest["sha256"])
        self.assertEqual(stored["content_sha256"], manifest["content_sha256"])
        self.assertEqual(stored["arweave_tx"], "txABC")
        self.assertTrue(stored["asset_url"].endswith(".tar.gz"))

    def test_latest_pointer_without_receipt_still_points(self):
        with patch.object(snapshot, "LEDGER_PATH", self.root / "ledger.json"), patch.object(
            snapshot, "LATEST_POINTER_PATH", self.root / "latest.json"
        ):
            manifest = self.archive_day()
            snapshot.update_ledger(manifest)
            pointer = snapshot.write_latest_pointer()
        self.assertEqual(pointer["date"], "2026-04-18")
        self.assertNotIn("asset_url", pointer)

if __name__ == "__main__":
    unittest.main()
