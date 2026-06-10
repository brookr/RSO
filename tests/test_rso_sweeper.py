import gzip
import hashlib
import io
import json
import subprocess
import tarfile
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from sweeper.rso_sweeper import (
    SweeperConfig,
    SweeperError,
    augment_operators_with_backed_github_nodes,
    backing_snapshot_location,
    candidate_operator_payloads,
    eligible_operators,
    fetch_url_bytes_with_redirects,
    github_fork_operator_registry,
    handle_signed_attestation,
    host_allowed,
    is_duplicate_error,
    merge_date_report,
    normalize_backing_snapshot,
    parse_attest_doc_return,
    signed_attestation_url,
    transaction_hash_from_cast_output,
    validate_bundle_sha256,
    validate_fetch_url,
    validate_node_artifact_url,
    validate_uri,
    validate_release_bundle,
    read_limited,
    write_date_reports,
)
from indexer.rso_profile import RSO_DOC_CHAIN_ID, encode_publication_locator_uri
from vendor.docchain.attestation import prepare_attestation, signed_attestation
from vendor.docchain.indexer import RpcError


class RsoSweeperTest(unittest.TestCase):
    def test_parse_attest_doc_return(self):
        result = "0x" + "11" * 32 + "22" * 32 + "33" * 32
        parsed = parse_attest_doc_return(result)
        self.assertEqual(parsed["blockHash"], "0x" + "11" * 32)
        self.assertEqual(parsed["uriHash"], "0x" + "22" * 32)
        self.assertEqual(parsed["attestationKey"], "0x" + "33" * 32)

    def test_duplicate_error_recognizes_text_and_custom_error_selector(self):
        self.assertTrue(is_duplicate_error("execution reverted: DuplicateAttestation"))
        self.assertTrue(
            is_duplicate_error(
                "eth_call returned error: {'data': "
                "'0xdd65d744372d0e8c6cbfdd3949b08650c67c1dce34a1b43a'}"
            )
        )
        self.assertFalse(is_duplicate_error("execution reverted: expired"))

    def test_operator_url_defaults_to_raw_github_signed_artifact(self):
        self.assertEqual(
            signed_attestation_url(
                {"repository": "owner/repo", "branch": "node"},
                "2026-06-01",
            ),
            "https://raw.githubusercontent.com/owner/repo/node/data/attestations/signed/2026-06-01.json",
        )

    def test_signed_node_artifact_url_must_belong_to_selected_node(self):
        validate_node_artifact_url(
            "https://raw.githubusercontent.com/owner/repo/node/data/attestation.json",
            "github:owner/repo",
        )
        validate_node_artifact_url(
            "https://node.example.com/.well-known/rso/attestation.json",
            "domain:node.example.com",
        )
        with self.assertRaisesRegex(SweeperError, "selected GitHub node"):
            validate_node_artifact_url(
                "https://raw.githubusercontent.com/attacker/repo/node/data/attestation.json",
                "github:victim/repo",
            )
        with self.assertRaisesRegex(SweeperError, "selected domain node"):
            validate_node_artifact_url(
                "https://attacker.example.com/attestation.json",
                "domain:node.example.com",
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
                    "github:owner/repo": {
                        "cardSpecificTdh": "100",
                        "backerCount": 3,
                        "rank": 1,
                    }
                },
            },
            expected_date="2026-06-01",
        )

        self.assertEqual(snapshot["github:owner/repo"]["nodeId"], "github:owner/repo")
        self.assertEqual(snapshot["github:owner/repo"]["cardSpecificTdhBacking"], 100)
        self.assertEqual(snapshot["github:owner/repo"]["backerCount"], 3)

    def test_normalize_backing_snapshot_rejects_duplicate_normalized_node_ids(self):
        with self.assertRaisesRegex(SweeperError, "duplicate normalized node"):
            normalize_backing_snapshot(
                {
                    "operators": {
                        "github:owner/repo": {"cardSpecificTdhBacking": 100},
                        "GitHub:Owner/Repo": {"cardSpecificTdhBacking": 200},
                    }
                }
            )

    def test_tdh_support_snapshot_requires_and_matches_date(self):
        with self.assertRaisesRegex(SweeperError, "requires date"):
            normalize_backing_snapshot(
                {
                    "schema": "rso-tdh-support-snapshot-v1",
                    "operators": {},
                },
                expected_date="2026-06-01",
            )
        with self.assertRaisesRegex(SweeperError, "does not match"):
            normalize_backing_snapshot(
                {
                    "schema": "rso-tdh-support-snapshot-v1",
                    "date": "2026-05-31",
                    "operators": {},
                },
                expected_date="2026-06-01",
            )

    def test_eligible_operators_ranks_by_card_specific_tdh_and_caps(self):
        operators = [
            {"name": "low", "repository": "owner/low"},
            {"name": "high", "repository": "owner/high"},
            {"name": "zero", "repository": "owner/zero"},
        ]
        backing = normalize_backing_snapshot(
            {
                "operators": {
                    "github:owner/low": {"cardSpecificTdh": 25},
                    "github:owner/high": {"cardSpecificTdh": 100},
                    "github:owner/zero": {"cardSpecificTdh": 0},
                }
            }
        )

        selected = eligible_operators(operators, backing, limit=1, min_card_specific_tdh=1)

        self.assertEqual([operator["name"] for operator in selected], ["high"])
        self.assertEqual(selected[0]["_backing"]["cardSpecificTdhBacking"], 100)

    def test_backed_github_nodes_augment_discovery_in_tdh_order(self):
        backing = normalize_backing_snapshot(
            {
                "operators": {
                    "github:existing/repo": {"cardSpecificTdhBacking": 300},
                    "github:independent/high": {"cardSpecificTdhBacking": 200},
                    "github:independent/low": {"cardSpecificTdhBacking": 100},
                    "domain:node.example.com": {"cardSpecificTdhBacking": 400},
                }
            }
        )
        operators = augment_operators_with_backed_github_nodes(
            [{"repository": "existing/repo", "branch": "node"}],
            backing,
            limit=1,
        )

        self.assertEqual(
            [operator["nodeId"] if "nodeId" in operator else "github:existing/repo" for operator in operators],
            ["github:existing/repo", "github:independent/high"],
        )
        self.assertEqual(operators[1]["source"], "tdh-support-snapshot")

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

    def test_validate_release_bundle_bounds_manifest_members(self):
        catalog = b'{"catalog":true}\n'
        content_hash = "0x" + __import__("hashlib").sha256(catalog).hexdigest()
        bundle = make_release_bundle(catalog, content_hash[2:])
        config = config_for_test(require_uri=True, max_json_bytes=8)

        with self.assertRaisesRegex(SweeperError, "release-manifest.json exceeds"):
            validate_release_bundle(bundle, content_hash, config)

    def test_read_limited_rejects_non_positive_limit(self):
        with self.assertRaisesRegex(SweeperError, "positive"):
            read_limited(io.BytesIO(b"x"), 0)

    def test_read_limited_rejects_oversized_stream(self):
        with self.assertRaisesRegex(SweeperError, "exceeds"):
            read_limited(io.BytesIO(b"x" * 64), 16)

    def test_validate_release_bundle_bounds_catalog_decompression(self):
        # A small-compressed catalog.json.gz that decompresses past the cap is a
        # gzip bomb. It passes the per-member compressed read but must be rejected
        # at gzip_decompress_limited before it is materialized for hashing.
        catalog = b"x" * 200_000  # ~250 bytes compressed, 200 KB decompressed
        content_hash = "0x" + hashlib.sha256(catalog).hexdigest()
        bundle = make_release_bundle(catalog, content_hash[2:])
        config = config_for_test(require_uri=True, max_catalog_bytes=1024)

        with self.assertRaisesRegex(SweeperError, "exceeds"):
            validate_release_bundle(bundle, content_hash, config)

    def test_reject_private_host_rejects_rebound_addresses(self):
        # An allowlisted host that resolves (via DNS rebinding / split horizon)
        # to a private, loopback, metadata, or CGNAT address must be rejected.
        for addr in ("127.0.0.1", "169.254.169.254", "10.0.0.1", "100.64.0.1"):
            with patch(
                "sweeper.rso_sweeper.socket.getaddrinfo",
                return_value=[(2, 1, 6, "", (addr, 443))],
            ):
                with self.assertRaisesRegex(SweeperError, "non-public"):
                    validate_fetch_url("https://raw.githubusercontent.com/o/r/node/x.json")

    def test_validate_fetch_url_allows_public_resolved_host(self):
        with patch(
            "sweeper.rso_sweeper.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("185.199.108.133", 443))],
        ):
            validate_fetch_url("https://raw.githubusercontent.com/o/r/node/x.json")

    def test_fetch_url_redirect_strips_authorization_cross_host(self):
        captured = []

        class _Resp:
            def __init__(self, body):
                self._b = body

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return None

            def read(self, amt=None):
                if amt is None:
                    body, self._b = self._b, b""
                    return body
                chunk, self._b = self._b[:amt], self._b[amt:]
                return chunk

        class _Opener:
            def open(self, request, timeout=None):
                captured.append(request)
                if len(captured) == 1:
                    raise urllib.error.HTTPError(
                        request.full_url,
                        302,
                        "redirect",
                        {"location": "https://arweave.net/elsewhere"},
                        io.BytesIO(b""),
                    )
                return _Resp(b"ok-bytes")

        with patch("sweeper.rso_sweeper.urllib.request.build_opener", return_value=_Opener()), patch(
            "sweeper.rso_sweeper.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("185.199.108.133", 443))],
        ):
            body = fetch_url_bytes_with_redirects(
                "https://raw.githubusercontent.com/o/r/node/x",
                timeout=1,
                max_bytes=1024,
                headers={"user-agent": "t", "AUTHORIZATION": "Bearer SECRET"},
                label="test",
                allow_authorized_redirects=False,
            )

        self.assertEqual(body, b"ok-bytes")
        self.assertIn("authorization", {k.lower() for k in captured[0].headers})
        self.assertNotIn("authorization", {k.lower() for k in captured[1].headers})

    def test_fetch_url_rejects_excessive_redirects(self):
        class _Opener:
            def open(self, request, timeout=None):
                raise urllib.error.HTTPError(
                    request.full_url,
                    302,
                    "redirect",
                    {"location": "https://raw.githubusercontent.com/o/r/loop"},
                    io.BytesIO(b""),
                )

        with patch("sweeper.rso_sweeper.urllib.request.build_opener", return_value=_Opener()), patch(
            "sweeper.rso_sweeper.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("185.199.108.133", 443))],
        ):
            with self.assertRaisesRegex(SweeperError, "redirects too many times"):
                fetch_url_bytes_with_redirects(
                    "https://raw.githubusercontent.com/o/r/start",
                    timeout=1,
                    max_bytes=1024,
                    headers={"user-agent": "t"},
                    label="test",
                    allow_authorized_redirects=False,
                )

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
            node_id="github:owner/repo",
        )
        fetched = []

        def fake_fetch_uri_bytes(location, config):
            fetched.append(location)
            return bundle

        with patch("sweeper.rso_sweeper.fetch_uri_bytes", side_effect=fake_fetch_uri_bytes):
            response = handle_signed_attestation(
                make_artifact(uri=uri, content_hash=content_hash),
                operator={"repository": "owner/repo", "attester": "0x" + "bb" * 20, "_backing": backed_operator()},
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
            node_id="github:owner/repo",
        )

        with patch("sweeper.rso_sweeper.fetch_uri_bytes", return_value=bundle):
            with self.assertRaisesRegex(SweeperError, "bundle fingerprint"):
                handle_signed_attestation(
                    make_artifact(uri=uri, content_hash=content_hash),
                    operator={"repository": "owner/repo", "attester": "0x" + "bb" * 20, "_backing": backed_operator()},
                    config=config_for_test(require_uri=True),
                    rpc=FakeRpc(),
                    expected_date="2026-06-01",
                )

    def test_validate_uri_rejects_too_many_publication_locations(self):
        uri = encode_publication_locator_uri(
            bundle_sha256="aa" * 32,
            locations=[
                "ar://one",
                "ar://two",
                "ar://three",
                "ar://four",
                "ar://five",
            ],
            node_id="github:owner/repo",
        )
        payload = make_artifact(uri=uri)
        attestation = payload["signed"]["prepared"]["attestation"]

        with self.assertRaisesRegex(SweeperError, "too many locations"):
            validate_uri(attestation, config_for_test(require_uri=True, max_publication_locations=4))

    def test_validate_uri_rejects_uri_over_contract_size_limit_before_fetch(self):
        uri = encode_publication_locator_uri(
            bundle_sha256="aa" * 32,
            locations=["https://example.com/" + ("a" * 8192)],
            node_id="github:owner/repo",
        )
        payload = make_artifact()
        attestation = payload["signed"]["prepared"]["attestation"]
        attestation["uri"] = uri

        with patch("sweeper.rso_sweeper.fetch_uri_bytes") as fetch:
            with self.assertRaisesRegex(SweeperError, "contract size limit"):
                validate_uri(attestation, config_for_test(require_uri=True))

        fetch.assert_not_called()

    def test_candidate_operator_payloads_discovers_attester_from_fork_artifact(self):
        artifact = make_artifact(node_id="github:owner/fork")
        operators = [{"name": "fork", "repository": "owner/fork", "branch": "node"}]
        backing = normalize_backing_snapshot(
            {
                "operators": {
                    "github:owner/fork": {"cardSpecificTdh": 100},
                }
            }
        )

        with patch("sweeper.rso_sweeper.fetch_json_url", return_value=artifact):
            candidates, records = candidate_operator_payloads(
                operators,
                backing,
                snapshot_date="2026-06-01",
                config=config_for_test(),
            )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["nodeId"], "github:owner/fork")
        self.assertEqual(candidates[0]["attester"], "0x" + "bb" * 20)
        self.assertEqual(candidates[0]["_backing"]["cardSpecificTdhBacking"], 100)
        self.assertEqual(records[0]["status"], "candidate")
        self.assertEqual(records[0]["authorizationStatus"], "pending_signature")
        self.assertEqual(records[0]["nodeBindingStatus"], "aligned")
        self.assertEqual(records[0]["publicationStatus"], "pending")
        self.assertEqual(records[0]["declaredAttester"], "0x" + "bb" * 20)
        self.assertEqual(records[0]["backing"]["cardSpecificTdhBacking"], 100)
        self.assertEqual(len(records[0]["declarationSha256"]), 64)

    def test_candidate_operator_payloads_rejects_copied_victim_artifact(self):
        victim_artifact = make_artifact(node_id="github:victim/rso")
        operators = [{"name": "attacker", "repository": "attacker/rso", "branch": "node"}]
        backing = normalize_backing_snapshot(
            {"operators": {"github:attacker/rso": {"cardSpecificTdhBacking": 100}}}
        )

        with patch("sweeper.rso_sweeper.fetch_json_url", return_value=victim_artifact):
            candidates, records = candidate_operator_payloads(
                operators,
                backing,
                snapshot_date="2026-06-01",
                config=config_for_test(),
            )

        self.assertEqual(candidates, [])
        self.assertEqual(records[0]["status"], "deferred")
        self.assertIn("selected nodeId", records[0]["error"])

    def test_candidate_operator_payloads_rejects_artifact_url_from_another_node(self):
        operators = [
            {
                "name": "victim",
                "nodeId": "github:victim/rso",
                "signedAttestationUrlTemplate": (
                    "https://raw.githubusercontent.com/attacker/rso/node/"
                    "data/attestations/signed/{date}.json"
                ),
            }
        ]
        backing = normalize_backing_snapshot(
            {"operators": {"github:victim/rso": {"cardSpecificTdhBacking": 100}}}
        )

        with patch("sweeper.rso_sweeper.fetch_json_url") as fetch:
            candidates, records = candidate_operator_payloads(
                operators,
                backing,
                snapshot_date="2026-06-01",
                config=config_for_test(),
            )

        self.assertEqual(candidates, [])
        self.assertEqual(records[0]["status"], "deferred")
        self.assertIn("selected GitHub node", records[0]["error"])
        fetch.assert_not_called()

    def test_handle_rejects_locator_node_id_spoof(self):
        artifact = make_artifact(
            node_id="github:victim/rso",
            declared_node_id="github:owner/repo",
        )
        with self.assertRaisesRegex(SweeperError, "signed publication nodeId"):
            handle_signed_attestation(
                artifact,
                operator={"repository": "owner/repo", "_backing": backed_operator()},
                config=config_for_test(),
                rpc=FakeRpc(),
            )

    def test_handle_rejects_node_declaration_attester_mismatch(self):
        artifact = make_artifact(declared_attester="0x" + "cc" * 20)
        with self.assertRaisesRegex(SweeperError, "declaration attester"):
            handle_signed_attestation(
                artifact,
                operator={"repository": "owner/repo", "_backing": backed_operator()},
                config=config_for_test(),
                rpc=FakeRpc(),
            )

    def test_github_fork_operator_registry_includes_root_and_bounded_forks(self):
        forks = [
            {"full_name": "alice/RSO"},
            {"full_name": "bob/RSO"},
        ]

        with patch("sweeper.rso_sweeper.fetch_github_json_array", return_value=forks):
            with patch.dict("os.environ", {"RSO_SWEEPER_MAX_FORKS": "2"}, clear=False):
                operators = github_fork_operator_registry("OMPub/RSO", timeout=1)

        self.assertEqual(
            [operator["repository"] for operator in operators],
            ["OMPub/RSO", "alice/RSO", "bob/RSO"],
        )
        self.assertEqual(
            [operator["nodeId"] for operator in operators],
            ["github:ompub/rso", "github:alice/rso", "github:bob/rso"],
        )

    def test_write_date_reports_publishes_one_json_per_date(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            write_date_reports(
                Path(tmpdir),
                {
                    "schema": "rso-sweeper-report-v1",
                    "operatorSource": "github-forks:OMPub/RSO",
                    "startedAt": "2026-06-05T01:15:00Z",
                    "finishedAt": "2026-06-05T01:16:00Z",
                    "dates": [
                        {
                            "date": "2026-06-04",
                            "status": "checked",
                            "operators": [{"nodeId": "github:owner/rso", "status": "submitted"}],
                        }
                    ],
                },
            )

            report = json.loads((Path(tmpdir) / "2026-06-04.json").read_text(encoding="utf-8"))
            self.assertEqual(report["schema"], "rso-sweeper-date-report-v1")
            self.assertEqual(report["date"], "2026-06-04")
            self.assertEqual(report["operators"][0]["nodeId"], "github:owner/rso")

    def test_write_date_reports_preserves_verified_history_across_retries(self):
        verified = {
            "nodeId": "github:owner/rso",
            "status": "submitted",
            "authorizationStatus": "verified",
            "publicationStatus": "verified",
            "claimFingerprint": "11" * 32,
        }
        deferred = {
            "nodeId": "github:owner/rso",
            "status": "deferred",
            "error": "temporary fetch failure",
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            report_dir = Path(tmpdir)
            write_date_reports(
                report_dir,
                {
                    "startedAt": "2026-06-05T01:15:00Z",
                    "finishedAt": "2026-06-05T01:16:00Z",
                    "dates": [{"date": "2026-06-04", "operators": [verified]}],
                },
            )
            write_date_reports(
                report_dir,
                {
                    "startedAt": "2026-06-06T01:15:00Z",
                    "finishedAt": "2026-06-06T01:16:00Z",
                    "dates": [{"date": "2026-06-04", "operators": [deferred]}],
                },
            )
            report = json.loads((report_dir / "2026-06-04.json").read_text(encoding="utf-8"))

        self.assertEqual(report["firstStartedAt"], "2026-06-05T01:15:00Z")
        self.assertIn(verified, report["operators"])
        self.assertIn(deferred, report["operators"])

    def test_merge_date_report_caps_nonfinal_history_but_preserves_verified_records(self):
        verified = {
            "status": "duplicate",
            "authorizationStatus": "verified",
            "publicationStatus": "verified",
        }
        merged = merge_date_report(
            {
                "schema": "rso-sweeper-date-report-v1",
                "date": "2026-06-04",
                "operators": [verified, {"status": "deferred", "attempt": 1}],
            },
            {
                "schema": "rso-sweeper-date-report-v1",
                "date": "2026-06-04",
                "operators": [
                    {"status": "deferred", "attempt": 2},
                    {"status": "deferred", "attempt": 3},
                ],
            },
            max_records=2,
        )

        self.assertEqual(merged["operators"], [verified, {"status": "deferred", "attempt": 3}])

    def test_handle_signed_attestation_rejects_direct_uri_for_backed_node(self):
        catalog = b'{"catalog":true}\n'
        content_hash = "0x" + __import__("hashlib").sha256(catalog).hexdigest()
        bundle = make_release_bundle(catalog, content_hash[2:])

        with patch("sweeper.rso_sweeper.fetch_uri_bytes", return_value=bundle):
            with self.assertRaisesRegex(SweeperError, "signed publication nodeId"):
                handle_signed_attestation(
                    make_artifact(uri="ar://abc123", content_hash=content_hash),
                    operator={"repository": "owner/repo", "attester": "0x" + "bb" * 20, "_backing": backed_operator()},
                    config=config_for_test(require_uri=True),
                    rpc=FakeRpc(),
                    expected_date="2026-06-01",
                )

    def test_handle_signed_attestation_checks_operator_authorization_and_simulates(self):
        config = config_for_test()
        rpc = FakeRpc()
        with patch("sweeper.rso_sweeper.validate_uri", return_value=verified_publication()):
            response = handle_signed_attestation(
                make_artifact(),
                operator={
                    "attester": "0x" + "bb" * 20,
                    "repository": "owner/repo",
                    "_backing": {
                        "nodeId": "github:owner/repo",
                        "cardSpecificTdhBacking": 100,
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
        self.assertEqual(response["sponsorship"]["scheme"], "rso-tdh-support-snapshot")
        self.assertEqual(response["sponsorship"]["nodeId"], "github:owner/repo")
        self.assertEqual(response["sponsorship"]["cardSpecificTdhBacking"], 100)
        self.assertEqual(response["authorizationStatus"], "verified")
        self.assertEqual(response["nodeBindingStatus"], "verified")
        self.assertEqual(response["publicationStatus"], "verified")
        self.assertEqual(response["claimedNodeId"], "github:owner/repo")
        self.assertEqual(response["declaredAttester"], "0x" + "bb" * 20)
        self.assertEqual(response["attestationAttester"], "0x" + "bb" * 20)
        self.assertEqual(len(response["claimFingerprint"]), 64)
        self.assertTrue(any(call.startswith("0xd2b85e96") for call in rpc.calls))

    def test_handle_signed_attestation_rejects_unregistered_attester(self):
        config = config_for_test()
        rpc = FakeRpc()

        with self.assertRaisesRegex(SweeperError, "registered operator attester"):
            handle_signed_attestation(
                make_artifact(),
                operator={"repository": "owner/repo", "attester": "0x" + "aa" * 20, "_backing": backed_operator()},
                config=config,
                rpc=rpc,
            )

    def test_handle_signed_attestation_rejects_operator_without_backing(self):
        config = config_for_test()
        rpc = FakeRpc()

        with self.assertRaisesRegex(SweeperError, "daily backing snapshot"):
            handle_signed_attestation(
                make_artifact(),
                operator={"repository": "owner/repo", "attester": "0x" + "bb" * 20},
                config=config,
                rpc=rpc,
            )

    def test_handle_signed_attestation_rejects_wrong_swept_date(self):
        config = config_for_test()
        rpc = FakeRpc()

        with self.assertRaisesRegex(SweeperError, "swept date"):
            handle_signed_attestation(
                make_artifact(),
                operator={"repository": "owner/repo", "attester": "0x" + "bb" * 20, "_backing": backed_operator()},
                config=config,
                rpc=rpc,
                expected_date="2026-06-02",
            )

    def test_handle_signed_attestation_can_submit_with_cast(self):
        config = config_for_test(dry_run=False)
        rpc = FakeRpc()
        tx = "0x" + "44" * 32

        def fake_run(command, check, capture_output, text):
            command_text = " ".join(command)
            self.assertIn("--keystore", command)
            self.assertIn("--password-file", command)
            self.assertNotIn("treasury-keystore-json", command_text)
            self.assertNotIn("treasury-keystore-password", command_text)
            return subprocess.CompletedProcess(command, 0, stdout=f"transactionHash {tx}\n", stderr="")

        with patch.dict(
            "os.environ",
            {
                "RSO_SWEEPER_KEYSTORE_JSON": "treasury-keystore-json",
                "RSO_SWEEPER_KEYSTORE_PASSWORD": "treasury-keystore-password",
            },
            clear=False,
        ):
            with patch("sweeper.rso_sweeper.validate_uri", return_value=verified_publication()):
                response = handle_signed_attestation(
                    make_artifact(),
                    operator={"repository": "owner/repo", "attester": "0x" + "bb" * 20, "_backing": backed_operator()},
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

    def test_handle_signed_attestation_reports_contract_duplicate(self):
        rpc = FakeRpc(error=RpcError("execution reverted: 0xdd65d744" + "33" * 32))
        with patch("sweeper.rso_sweeper.validate_uri", return_value=verified_publication()):
            response = handle_signed_attestation(
                make_artifact(),
                operator={
                    "repository": "owner/repo",
                    "attester": "0x" + "bb" * 20,
                    "_backing": backed_operator(),
                },
                config=config_for_test(),
                rpc=rpc,
            )

        self.assertEqual(response["status"], "duplicate")
        self.assertEqual(response["authorizationStatus"], "verified")
        self.assertEqual(response["publicationStatus"], "verified")


class FakeRpc:
    def __init__(self, *, simulation=None, error=None):
        self.simulation = simulation or "0x" + "11" * 32 + "22" * 32 + "33" * 32
        self.error = error
        self.calls = []

    def call(self, method, params):
        if method != "eth_call":
            raise AssertionError(f"unexpected method {method}")
        data = params[0]["data"]
        self.calls.append(data)
        if data.startswith("0xd2b85e96"):
            if self.error is not None:
                raise self.error
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
        "nodeId": "github:owner/repo",
        "cardSpecificTdhBacking": 100,
        "backerCount": 2,
        "rank": 1,
        "snapshotDate": "2026-06-01",
    }


def verified_publication():
    return {
        "publicationStatus": "verified",
        "nodeId": "github:owner/repo",
        "bundleSha256": "aa" * 32,
        "contentHash": "0x" + "22" * 32,
        "locations": ["ar://abc123"],
    }


def make_artifact(
    uri=None,
    content_hash="0x" + "22" * 32,
    *,
    node_id="github:owner/repo",
    attester="0x" + "bb" * 20,
    declared_node_id=None,
    declared_attester=None,
):
    if uri is None:
        uri = encode_publication_locator_uri(
            bundle_sha256="aa" * 32,
            locations=["ar://abc123"],
            node_id=node_id,
        )
    prepared = prepare_attestation(
        chain_id=1,
        contract_address="0x" + "aa" * 20,
        attester=attester,
        on_behalf_of="0x" + "cc" * 20,
        doc_chain_id=RSO_DOC_CHAIN_ID,
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
        "node": {
            "nodeId": declared_node_id if declared_node_id is not None else node_id,
            "attester": declared_attester if declared_attester is not None else attester,
            "repository": "owner/repo",
            "workflowRunId": "123",
        },
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
