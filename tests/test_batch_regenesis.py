"""Tests for batch re-genesis tooling: submit_batch, hydrate_from_upstream,
sweeper v2 bundle validation, and indexer chain profiles."""

import argparse
import gzip
import hashlib
import io
import json
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "pipeline")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import snapshot  # noqa: E402
from attestation import hydrate_from_upstream, submit_batch  # noqa: E402
from sweeper import rso_sweeper  # noqa: E402
from tests.test_core_projection import gp_record  # noqa: E402


def build_v2_bundle_bytes(
    records,
    *,
    include_annotations=True,
    corrupt_core=False,
    include_conjunctions=False,
    corrupt_conjunctions=False,
    omit_conjunctions_member=False,
):
    """Build an in-memory v2 bundle the way the pipeline does."""
    data = sorted(records, key=snapshot.catalog_id_sort_key)
    catalog_bytes = snapshot.canonicalize(data)
    manifest = {
        "date": "2026-04-20",
        "sha256": snapshot.compute_hash(catalog_bytes),
        "content_schema": snapshot.CONTENT_SCHEMA,
        "content_excluded_fields": list(snapshot.CONTENT_EXCLUDED_FIELDS),
        "content_sha256": snapshot.core_content_sha256(data),
        "object_count": len(data),
    }
    if corrupt_core:
        manifest["content_sha256"] = "ab" * 32
    annotations = {"schema": "rso-annotations-v1", "date": "2026-04-20", "catalog_changes": []}
    annotations_bytes = json.dumps(annotations, indent=2, sort_keys=True).encode() + b"\n"
    if include_annotations:
        manifest["annotations_sha256"] = hashlib.sha256(annotations_bytes).hexdigest()
    conjunctions = {
        "schema": "rso-conjunctions-v1",
        "date": "2026-04-20",
        "messages": [],
        "summary": {"message_count": 0, "emergency_reportable_count": 0, "max_pc": None},
    }
    conjunctions_bytes = json.dumps(conjunctions, indent=2, sort_keys=True).encode() + b"\n"
    if include_conjunctions:
        manifest["conjunctions_sha256"] = (
            "cd" * 32 if corrupt_conjunctions else hashlib.sha256(conjunctions_bytes).hexdigest()
        )
    release_manifest = {"catalog_sha256": manifest["sha256"], "manifest_sha256": "ignored"}

    catalog_gz = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=catalog_gz, mtime=0) as gz:
        gz.write(catalog_bytes)

    out = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=out, mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w") as tar:
            def add(name, payload):
                info = tarfile.TarInfo(name=name)
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))

            add("catalog.json.gz", catalog_gz.getvalue())
            add("manifest.json", json.dumps(manifest).encode())
            if include_annotations:
                add("annotations.json", annotations_bytes)
            if include_conjunctions and not omit_conjunctions_member:
                add("conjunctions.json", conjunctions_bytes)
            add("release-manifest.json", json.dumps(release_manifest).encode())
    return out.getvalue(), manifest


class SweeperV2ValidationTest(unittest.TestCase):
    def config(self):
        return rso_sweeper.SweeperConfig(
            rpc_url="http://localhost",
            docchain_address="0x" + "aa" * 20,
        )

    def test_accepts_v2_bundle_against_core_hash(self):
        records = [gp_record(), gp_record(NORAD_CAT_ID="69181", GP_ID="2")]
        bundle, manifest = build_v2_bundle_bytes(records)

        rso_sweeper.validate_release_bundle(
            bundle, "0x" + manifest["content_sha256"], self.config()
        )

    def test_rejects_v2_bundle_with_mismatched_core(self):
        records = [gp_record()]
        bundle, manifest = build_v2_bundle_bytes(records, corrupt_core=True)

        with self.assertRaises(rso_sweeper.SweeperError):
            rso_sweeper.validate_release_bundle(
                bundle, "0x" + manifest["content_sha256"], self.config()
            )

    def test_accepts_v2_bundle_with_conjunctions(self):
        records = [gp_record()]
        bundle, manifest = build_v2_bundle_bytes(records, include_conjunctions=True)

        rso_sweeper.validate_release_bundle(
            bundle, "0x" + manifest["content_sha256"], self.config()
        )

    def test_rejects_v2_bundle_with_mismatched_conjunctions(self):
        records = [gp_record()]
        bundle, manifest = build_v2_bundle_bytes(
            records, include_conjunctions=True, corrupt_conjunctions=True
        )

        with self.assertRaisesRegex(rso_sweeper.SweeperError, "conjunctions.json"):
            rso_sweeper.validate_release_bundle(
                bundle, "0x" + manifest["content_sha256"], self.config()
            )

    def test_rejects_v2_bundle_with_declared_but_missing_conjunctions(self):
        records = [gp_record()]
        bundle, manifest = build_v2_bundle_bytes(
            records, include_conjunctions=True, omit_conjunctions_member=True
        )

        with self.assertRaisesRegex(rso_sweeper.SweeperError, "no conjunctions.json"):
            rso_sweeper.validate_release_bundle(
                bundle, "0x" + manifest["content_sha256"], self.config()
            )

    def test_rejects_v2_bundle_with_tampered_annotations(self):
        records = [gp_record()]
        bundle, manifest = build_v2_bundle_bytes(records)
        # rebuild the bundle with different annotations bytes but the same manifest
        raw = io.BytesIO(bundle)
        members = {}
        with tarfile.open(fileobj=raw, mode="r:gz") as tar:
            for member in tar:
                members[member.name] = tar.extractfile(member).read()
        members["annotations.json"] = b'{"schema": "tampered"}\n'
        out = io.BytesIO()
        with gzip.GzipFile(filename="", mode="wb", fileobj=out, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w") as tar:
                for name, payload in members.items():
                    info = tarfile.TarInfo(name=name)
                    info.size = len(payload)
                    tar.addfile(info, io.BytesIO(payload))

        with self.assertRaises(rso_sweeper.SweeperError):
            rso_sweeper.validate_release_bundle(
                out.getvalue(), "0x" + manifest["content_sha256"], self.config()
            )

    def test_legacy_v1_bundle_path_still_validates_raw_hash(self):
        records = [gp_record()]
        data = sorted(records, key=snapshot.catalog_id_sort_key)
        catalog_bytes = snapshot.canonicalize(data)
        raw_sha = snapshot.compute_hash(catalog_bytes)
        manifest = {"date": "2026-04-20", "sha256": raw_sha}
        release_manifest = {"catalog_sha256": raw_sha}
        catalog_gz = io.BytesIO()
        with gzip.GzipFile(filename="", mode="wb", fileobj=catalog_gz, mtime=0) as gz:
            gz.write(catalog_bytes)
        out = io.BytesIO()
        with gzip.GzipFile(filename="", mode="wb", fileobj=out, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w") as tar:
                for name, payload in (
                    ("catalog.json.gz", catalog_gz.getvalue()),
                    ("manifest.json", json.dumps(manifest).encode()),
                    ("release-manifest.json", json.dumps(release_manifest).encode()),
                ):
                    info = tarfile.TarInfo(name=name)
                    info.size = len(payload)
                    tar.addfile(info, io.BytesIO(payload))

        rso_sweeper.validate_release_bundle(out.getvalue(), "0x" + raw_sha, self.config())


class SubmitBatchTest(unittest.TestCase):
    def test_collects_signed_artifacts_in_date_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with patch.object(
                submit_batch, "signed_attestation_path",
                lambda date, artifact_id=None: tmp_path / f"{date}.json",
            ):
                for day, ref in (("2026-04-20", 20260420000000), ("2026-04-21", 20260421000000)):
                    artifact = {
                        "schema": "rso-signed-attestation-v1",
                        "date": day,
                        "docRef": ref,
                        "signed": {
                            "prepared": {"attestation": {"docBlock": {"docRef": ref}}},
                            "signature": "0x" + "ab" * 65,
                        },
                    }
                    (tmp_path / f"{day}.json").write_text(json.dumps(artifact))

                args = argparse.Namespace(start="2026-04-20", end="2026-04-21", require_all=True)
                items, dates = submit_batch.collect_signed_items(args)

        self.assertEqual(dates, ["2026-04-20", "2026-04-21"])
        self.assertEqual(items[0][0]["docBlock"]["docRef"], 20260420000000)
        self.assertEqual(items[1][1], "0x" + "ab" * 65)

    def test_require_all_raises_on_gap(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            with patch.object(
                submit_batch, "signed_attestation_path",
                lambda date, artifact_id=None: tmp_path / f"{date}.json",
            ):
                args = argparse.Namespace(start="2026-04-20", end="2026-04-20", require_all=True)
                with self.assertRaises(ValueError):
                    submit_batch.collect_signed_items(args)

    def test_decode_batch_result(self):
        body = format(3, "064x") + format(2, "064x")
        self.assertEqual(submit_batch.decode_batch_result("0x" + body), (3, 2))


class HydrateFromUpstreamTest(unittest.TestCase):
    def test_upstream_locations_prefers_arweave_then_github(self):
        receipt = {
            "destinations": {
                "arweave": {"status": "submitted", "transaction_id": "txabc"},
                "github_release": {"asset_url": "https://github.com/o/r/releases/download/t/a.tar.gz"},
            }
        }
        self.assertEqual(
            hydrate_from_upstream.upstream_locations(receipt),
            ["ar://txabc", "https://github.com/o/r/releases/download/t/a.tar.gz"],
        )

    def test_extract_bundle_rejects_unexpected_member(self):
        out = io.BytesIO()
        with gzip.GzipFile(filename="", mode="wb", fileobj=out, mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w") as tar:
                info = tarfile.TarInfo(name="evil.sh")
                payload = b"#!/bin/sh"
                info.size = len(payload)
                tar.addfile(info, io.BytesIO(payload))

        with self.assertRaises(ValueError):
            hydrate_from_upstream.extract_bundle("2026-04-20", out.getvalue())

    def test_hydrate_day_verifies_and_adopts(self):
        records = [gp_record()]
        bundle, manifest = build_v2_bundle_bytes(records)
        receipt = {
            "bundle_sha256": hashlib.sha256(bundle).hexdigest(),
            "asset_name": "rso-archive-2026-04-20-v2.tar.gz",
            "destinations": {
                "arweave": {"status": "submitted", "transaction_id": "txabc"},
                "github_release": {
                    "asset_url": "https://github.com/OMPub/RSO/releases/download/t/a.tar.gz"
                },
            },
        }

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            args = argparse.Namespace(
                start="2026-04-20",
                end="2026-04-20",
                upstream="OMPub/RSO",
                branch="node",
                timeout=10.0,
            )
            ledger_calls = []
            with patch.object(
                hydrate_from_upstream, "fetch_upstream_receipt", return_value=receipt
            ), patch.object(
                hydrate_from_upstream, "fetch_bundle", return_value=bundle
            ), patch.object(
                hydrate_from_upstream, "snapshot_dir", lambda d: tmp_path / d
            ), patch.object(
                hydrate_from_upstream,
                "storage_receipt_path",
                lambda d: tmp_path / d / "storage.json",
            ), patch.object(
                hydrate_from_upstream, "update_ledger", ledger_calls.append
            ):
                hydrate_from_upstream.hydrate_day(args, "2026-04-20")
            self.assertEqual(len(ledger_calls), 1)
            self.assertEqual(ledger_calls[0]["content_schema"], snapshot.CONTENT_SCHEMA)

            day_dir = tmp_path / "2026-04-20"
            self.assertTrue((day_dir / "manifest.json").exists())
            self.assertTrue((day_dir / "catalog.json.gz").exists())
            adopted = json.loads((day_dir / "storage.json").read_text())
            self.assertEqual(adopted["bundle_sha256"], receipt["bundle_sha256"])
            self.assertEqual(adopted["verified_from_upstream"], "OMPub/RSO")
            self.assertIn("arweave", adopted["destinations"])

    def test_hydrate_day_adopts_conjunctions(self):
        records = [gp_record()]
        bundle, _ = build_v2_bundle_bytes(records, include_conjunctions=True)
        receipt = {
            "bundle_sha256": hashlib.sha256(bundle).hexdigest(),
            "destinations": {
                "github_release": {
                    "asset_url": "https://github.com/OMPub/RSO/releases/download/t/a.tar.gz"
                },
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            args = argparse.Namespace(
                start="2026-04-20", end="2026-04-20", upstream="OMPub/RSO",
                branch="node", timeout=10.0,
            )
            with patch.object(
                hydrate_from_upstream, "fetch_upstream_receipt", return_value=receipt
            ), patch.object(
                hydrate_from_upstream, "fetch_bundle", return_value=bundle
            ), patch.object(
                hydrate_from_upstream, "snapshot_dir", lambda d: tmp_path / d
            ), patch.object(
                hydrate_from_upstream,
                "storage_receipt_path",
                lambda d: tmp_path / d / "storage.json",
            ), patch.object(
                hydrate_from_upstream, "update_ledger", lambda manifest: None
            ):
                hydrate_from_upstream.hydrate_day(args, "2026-04-20")
            self.assertTrue((tmp_path / "2026-04-20" / "conjunctions.json").exists())

    def test_hydrate_day_rejects_mismatched_conjunctions(self):
        records = [gp_record()]
        bundle, _ = build_v2_bundle_bytes(
            records, include_conjunctions=True, corrupt_conjunctions=True
        )
        receipt = {
            "bundle_sha256": hashlib.sha256(bundle).hexdigest(),
            "destinations": {
                "github_release": {
                    "asset_url": "https://github.com/OMPub/RSO/releases/download/t/a.tar.gz"
                },
            },
        }
        args = argparse.Namespace(
            start="2026-04-20", end="2026-04-20", upstream="OMPub/RSO",
            branch="node", timeout=10.0,
        )
        with patch.object(
            hydrate_from_upstream, "fetch_upstream_receipt", return_value=receipt
        ), patch.object(hydrate_from_upstream, "fetch_bundle", return_value=bundle):
            with self.assertRaisesRegex(ValueError, "conjunctions.json"):
                hydrate_from_upstream.hydrate_day(args, "2026-04-20")

    def test_hydrate_day_rejects_bundle_hash_mismatch(self):
        records = [gp_record()]
        bundle, _ = build_v2_bundle_bytes(records)
        receipt = {
            "bundle_sha256": "00" * 32,
            "destinations": {
                "github_release": {"asset_url": "https://github.com/o/r/releases/download/t/a.tar.gz"}
            },
        }
        args = argparse.Namespace(
            start="2026-04-20", end="2026-04-20", upstream="OMPub/RSO", branch="node", timeout=10.0
        )
        with patch.object(
            hydrate_from_upstream, "fetch_upstream_receipt", return_value=receipt
        ), patch.object(hydrate_from_upstream, "fetch_bundle", return_value=bundle):
            with self.assertRaises(ValueError):
                hydrate_from_upstream.hydrate_day(args, "2026-04-20")


if __name__ == "__main__":
    unittest.main()
