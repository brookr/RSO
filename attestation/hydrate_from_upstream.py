#!/usr/bin/env python3
"""Hydrate archive days from an upstream node's published v2 bundles.

The late-join path: a node that was not capturing on a historical day fetches
the upstream publication, verifies it byte-for-byte (bundle sha256 against the
upstream storage receipt) and semantically (raw catalog sha256 against the
bundled manifest, then an independent re-derivation of the core projection
hash), and only then adopts the day into its own archive: day artifacts are
extracted from the verified bundle and storage.json records the SAME upload
locations. node_attest.py then signs the day with the node's OWN publication
locator (its own nodeId, the shared bundle fingerprint and locations), so both
nodes attest the same upload without a second copy, and submit_batch.py lands
every day since genesis in one transaction.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from attestation.rso_attestation import date_range, snapshot_dir, storage_receipt_path  # noqa: E402
from pipeline.snapshot import (  # noqa: E402
    CONTENT_SCHEMA,
    MAX_CATALOG_BYTES,
    canonicalize,
    compute_hash,
    core_content_sha256,
    fetch_url_bytes_with_redirects,
    gzip_decompress_limited,
    read_limited,
    update_ledger,
    utc_stamp,
    validate_github_download_url,
    write_json,
)

MAX_BUNDLE_BYTES = 100 * 1024 * 1024
MAX_RECEIPT_BYTES = 1024 * 1024
BUNDLE_MEMBERS = {
    "annotations.json",
    "audit.json",
    "catalog.json.gz",
    "delta.json",
    "manifest.json",
    "release-manifest.json",
    "visibility_state.json",
}


def main() -> int:
    try:
        args = parse_args()
        hydrated = 0
        kept = 0
        for snapshot_date in date_range(args.start, args.end):
            if keep_local_day(args, snapshot_date):
                # A node's own captures are its observation record; hydration
                # fills gaps and replaces stale inherited copies, it never
                # touches days the node captured or published itself.
                print(f"  {snapshot_date}: keeping own archive day")
                kept += 1
                continue
            hydrate_day(args, snapshot_date)
            hydrated += 1
        print(
            f"\nHydrated and verified {hydrated} days from {args.upstream}"
            + (f"; kept {kept} local days" if kept else "")
        )
        return 0
    except (OSError, KeyError, ValueError) as exc:
        print(f"hydrate_from_upstream.py: {exc}", file=sys.stderr)
        return 2


def keep_local_day(args: argparse.Namespace, snapshot_date: str) -> bool:
    """Decide whether a local archive day is the node's own.

    With --keep-from, the boundary is explicit: days on or after it are the
    node's own captures, everything earlier is adopted from upstream (forked
    nodes inherit upstream's old day directories, which are not their own
    observations). Without it, a day is kept only when it already carries v2
    content fields -- a true cold-start node has no days at all, and a node
    that has been running v2 dailies owns everything it produced.
    """
    if args.overwrite:
        return False
    if args.keep_from:
        return snapshot_date >= args.keep_from
    manifest_file = snapshot_dir(snapshot_date) / "manifest.json"
    if not manifest_file.exists():
        return False
    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except ValueError:
        return False
    return manifest.get("content_schema") == CONTENT_SCHEMA


def hydrate_day(args: argparse.Namespace, snapshot_date: str) -> None:
    receipt = fetch_upstream_receipt(args, snapshot_date)
    bundle_sha = receipt.get("bundle_sha256")
    if not isinstance(bundle_sha, str) or len(bundle_sha) != 64:
        raise ValueError(f"{snapshot_date}: upstream receipt has no usable bundle_sha256")

    locations = upstream_locations(receipt)
    if not locations:
        raise ValueError(f"{snapshot_date}: upstream receipt records no publication locations")

    bundle_bytes = fetch_bundle(args, snapshot_date, receipt)
    actual_sha = hashlib.sha256(bundle_bytes).hexdigest()
    if actual_sha != bundle_sha:
        raise ValueError(
            f"{snapshot_date}: bundle bytes {actual_sha} do not match upstream receipt {bundle_sha}"
        )

    members = extract_bundle(snapshot_date, bundle_bytes)
    manifest = json.loads(members["manifest.json"])
    if manifest.get("content_schema") != CONTENT_SCHEMA:
        raise ValueError(f"{snapshot_date}: upstream manifest is not {CONTENT_SCHEMA}")

    catalog_bytes = gzip_decompress_limited(
        members["catalog.json.gz"], MAX_CATALOG_BYTES, label="catalog.json.gz"
    )
    if compute_hash(catalog_bytes) != manifest["sha256"]:
        raise ValueError(f"{snapshot_date}: catalog bytes do not match the bundled manifest sha256")
    records = json.loads(catalog_bytes)
    if canonicalize(records) != catalog_bytes:
        raise ValueError(f"{snapshot_date}: catalog bytes are not in canonical form")
    derived_core = core_content_sha256(records)
    if derived_core != manifest["content_sha256"]:
        raise ValueError(
            f"{snapshot_date}: re-derived core {derived_core} does not match "
            f"manifest content_sha256 {manifest['content_sha256']}"
        )

    day_dir = snapshot_dir(snapshot_date)
    day_dir.mkdir(parents=True, exist_ok=True)
    for name, payload in members.items():
        if name == "release-manifest.json":
            continue
        (day_dir / name).write_bytes(payload)

    # the ledger mirrors committed manifests; adopted days must be reflected
    update_ledger(manifest)

    write_json(
        storage_receipt_path(snapshot_date),
        {
            "date": snapshot_date,
            "asset_name": receipt.get("asset_name"),
            "bundle_bytes": len(bundle_bytes),
            "bundle_sha256": bundle_sha,
            "catalog_sha256": manifest["sha256"],
            "manifest_sha256": receipt.get("manifest_sha256"),
            "destinations": receipt.get("destinations", {}),
            "verified_from_upstream": args.upstream,
            "verified_at": utc_stamp(),
            "updated_at": utc_stamp(),
        },
    )
    print(
        f"  {snapshot_date}: bundle {bundle_sha[:12]} verified, core {derived_core[:12]} matches; "
        f"{len(locations)} shared location(s) adopted"
    )


def fetch_upstream_receipt(args: argparse.Namespace, snapshot_date: str) -> dict[str, object]:
    year, month, day = snapshot_date.split("-")
    url = (
        f"https://raw.githubusercontent.com/{args.upstream}/{args.branch}/"
        f"data/{year}/{month}/{day}/storage.json"
    )
    payload = fetch_url_bytes_with_redirects(
        url,
        timeout=args.timeout,
        max_bytes=MAX_RECEIPT_BYTES,
        headers={},
        label=f"{snapshot_date} upstream storage receipt",
        validate_url=validate_raw_githubusercontent_url,
    )
    receipt = json.loads(payload)
    if not isinstance(receipt, dict):
        raise ValueError(f"{snapshot_date}: upstream storage receipt is not an object")
    return receipt


def upstream_locations(receipt: dict[str, object]) -> list[str]:
    destinations = receipt.get("destinations")
    if not isinstance(destinations, dict):
        return []
    locations = []
    arweave = destinations.get("arweave")
    if isinstance(arweave, dict) and arweave.get("status") == "submitted":
        tx_id = arweave.get("transaction_id")
        if isinstance(tx_id, str) and tx_id:
            locations.append("ar://" + tx_id)
    github_release = destinations.get("github_release")
    if isinstance(github_release, dict):
        asset_url = github_release.get("asset_url")
        if isinstance(asset_url, str) and asset_url:
            locations.append(asset_url)
    return locations


def fetch_bundle(
    args: argparse.Namespace, snapshot_date: str, receipt: dict[str, object]
) -> bytes:
    destinations = receipt.get("destinations", {})
    github_release = destinations.get("github_release") if isinstance(destinations, dict) else None
    asset_url = github_release.get("asset_url") if isinstance(github_release, dict) else None
    if not isinstance(asset_url, str) or not asset_url:
        raise ValueError(f"{snapshot_date}: upstream receipt has no github_release asset_url")
    return fetch_url_bytes_with_redirects(
        asset_url,
        timeout=args.timeout,
        max_bytes=MAX_BUNDLE_BYTES,
        headers={},
        label=f"{snapshot_date} upstream bundle",
        validate_url=validate_github_download_url,
    )


def extract_bundle(snapshot_date: str, bundle_bytes: bytes) -> dict[str, bytes]:
    members: dict[str, bytes] = {}
    with tarfile.open(fileobj=io.BytesIO(bundle_bytes), mode="r|gz") as tar:
        for member in tar:
            name = member.name
            if name not in BUNDLE_MEMBERS:
                raise ValueError(f"{snapshot_date}: bundle contains unexpected file {name}")
            if name in members:
                raise ValueError(f"{snapshot_date}: bundle contains duplicate file {name}")
            if not member.isfile():
                raise ValueError(f"{snapshot_date}: bundle member {name} is not a regular file")
            extracted = tar.extractfile(member)
            if extracted is None:
                raise ValueError(f"{snapshot_date}: bundle cannot read {name}")
            members[name] = read_limited(extracted, MAX_BUNDLE_BYTES, label=name)
    for required in ("manifest.json", "catalog.json.gz"):
        if required not in members:
            raise ValueError(f"{snapshot_date}: bundle is missing {required}")
    return members


def validate_raw_githubusercontent_url(url: str) -> None:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "raw.githubusercontent.com":
        raise ValueError(f"unexpected upstream receipt host: {url}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify and adopt archive days from an upstream node's v2 bundles."
    )
    parser.add_argument("--start", required=True, help="First archive date, YYYY-MM-DD.")
    parser.add_argument("--end", required=True, help="Last archive date, YYYY-MM-DD.")
    parser.add_argument(
        "--upstream",
        default=os.environ.get("RSO_UPSTREAM_REPO", "OMPub/RSO"),
        help="Upstream OWNER/REPO publishing the bundles (default: OMPub/RSO).",
    )
    parser.add_argument(
        "--branch",
        default=os.environ.get("RSO_UPSTREAM_NODE_BRANCH", "node"),
        help="Upstream branch carrying the archive data (default: node).",
    )
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace local archive days with the verified upstream copies.",
    )
    parser.add_argument(
        "--keep-from",
        default=os.environ.get("RSO_HYDRATE_KEEP_FROM", ""),
        help=(
            "Explicit ownership boundary, YYYY-MM-DD: days on or after this date "
            "are the node's own captures and are never hydrated; earlier days are "
            "adopted from upstream even if inherited copies exist."
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
