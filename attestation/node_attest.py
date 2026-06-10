#!/usr/bin/env python3
"""Automatically sign RSO DocChain attestations for archived days."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from attestation.rso_attestation import (  # noqa: E402
    DEFAULT_STATE_PATH,
    STATE_SCHEMA,
    content_hash_from_manifest,
    date_range,
    load_manifest,
    load_attestation_state,
    parent_hash_for_date,
    prepare_sign_one,
    record_state_entry,
    release_uri,
    signed_attestation_path,
    state_attestation_for_inputs,
    state_entry_from_signed_artifact,
    write_signed_artifact,
)
from indexer.rso_profile import normalize_node_id  # noqa: E402
from vendor.docchain.attestation import (  # noqa: E402
    has_cast_wallet_config,
    normalize_address,
    subprocess_error_detail,
)
from vendor.docchain.model import ZERO_ADDRESS  # noqa: E402

MAX_ATTESTATION_TTL = 7 * 24 * 60 * 60


def main() -> int:
    try:
        args = parse_args()
        if should_skip(args):
            print("Attestation signing skipped: disposable key, attester, or contract is not configured.")
            return 0
        state_path = Path(args.state)
        state = load_attestation_state(state_path, schema=STATE_SCHEMA)
        for snapshot_date in date_range(args.start, args.end):
            parent_hash = parent_hash_for_date(
                snapshot_date,
                state,
                bootstrap_parent_hash=args.bootstrap_parent_hash,
                baseline_parent_hash=args.baseline_parent_hash,
            )
            content_hash = content_hash_from_manifest(load_manifest(snapshot_date))
            uri = release_uri(snapshot_date, mode=args.uri_mode, node_id=args.node_id)
            existing = state_attestation_for_inputs(
                state,
                snapshot_date=snapshot_date,
                chain_id=args.chain_id,
                contract_address=args.contract_address,
                attester=args.attester,
                on_behalf_of=args.on_behalf_of,
                parent_hash=parent_hash,
                content_hash=content_hash,
                uri=uri,
            )
            if existing is not None:
                print(f"Attestation skipped for {snapshot_date}: exact attestation already recorded in node state.")
                continue
            prepared, artifact = prepare_sign_one(
                snapshot_date=snapshot_date,
                state=state,
                chain_id=args.chain_id,
                contract_address=args.contract_address,
                attester=args.attester,
                on_behalf_of=args.on_behalf_of,
                private_key_env=args.private_key_env,
                uri_mode=args.uri_mode,
                bootstrap_parent_hash=args.bootstrap_parent_hash,
                ttl=args.ttl,
                network=args.network,
                node_id=args.node_id,
                repository=args.repository,
                workflow_run_id=args.workflow_run_id,
                cast=args.cast,
                parent_hash=parent_hash,
                uri=uri,
            )
            entry = state_entry_from_signed_artifact(
                snapshot_date=snapshot_date,
                prepared=prepared.prepared,
                signed=prepared.signed,
                block_hash=prepared.block_hash,
            )
            artifact_path = signed_attestation_path(snapshot_date)
            historical_path = signed_attestation_path(snapshot_date, str(entry["artifactId"]))
            write_signed_artifact(historical_path, artifact)
            write_signed_artifact(artifact_path, artifact)
            state = record_state_entry(state_path, entry, schema=STATE_SCHEMA)
            print(
                f"Signed {snapshot_date}: docRef={prepared.doc_ref} "
                f"blockHash={entry['blockHash']} artifact={artifact_path.relative_to(ROOT)}"
            )
        return 0
    except subprocess.CalledProcessError as exc:
        print(f"node_attest.py: {subprocess_error_detail(exc)}", file=sys.stderr)
        return 2
    except (OSError, KeyError, ValueError) as exc:
        print(f"node_attest.py: {exc}", file=sys.stderr)
        return 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sign RSO DocChain attestations from a node archive.",
    )
    parser.add_argument("--start", required=True, help="First archive date, YYYY-MM-DD.")
    parser.add_argument("--end", required=True, help="Last archive date, YYYY-MM-DD.")
    parser.add_argument(
        "--network",
        default=os.environ.get("RSO_DOCCHAIN_NETWORK", os.environ.get("DOCCHAIN_NETWORK", "mainnet")),
    )
    parser.add_argument(
        "--chain-id",
        type=int,
        default=int(os.environ.get("RSO_DOCCHAIN_CHAIN_ID", os.environ.get("DOCCHAIN_CHAIN_ID", "1"))),
    )
    parser.add_argument(
        "--contract-address",
        default=os.environ.get("RSO_DOCCHAIN_ADDRESS") or os.environ.get("DOCCHAIN_ADDRESS"),
    )
    parser.add_argument(
        "--attester",
        default=os.environ.get("RSO_ATTESTER_ADDRESS") or os.environ.get("DOCCHAIN_ATTESTER"),
        help="Disposable EOA public address matching the configured private key.",
    )
    parser.add_argument(
        "--node-id",
        default=os.environ.get("RSO_NODE_ID") or default_node_id(),
        help="Stable node identity claimed inside the signed publication locator.",
    )
    parser.add_argument(
        "--on-behalf-of",
        default=os.environ.get("RSO_ON_BEHALF_OF_ADDRESS") or ZERO_ADDRESS,
        help="6529 identity/card-holding address represented by the disposable EOA.",
    )
    parser.add_argument(
        "--private-key-env",
        default="DISPOSABLE_NO_FUNDS_ETH_PRIVATE_KEY",
        help="Environment variable holding the disposable EOA private key.",
    )
    parser.add_argument(
        "--state",
        default=str(DEFAULT_STATE_PATH),
        help="Committed node attestation state path.",
    )
    parser.add_argument(
        "--uri-mode",
        choices=("auto", "arweave", "github_release", "empty"),
        default=os.environ.get("RSO_ATTESTATION_URI_MODE", "auto"),
    )
    parser.add_argument(
        "--bootstrap-parent-hash",
        default=os.environ.get("RSO_ATTESTATION_BOOTSTRAP_PARENT_HASH"),
        help="One-time parent blockHash for joining an already-started DocChain.",
    )
    parser.add_argument(
        "--baseline-parent-hash",
        default=os.environ.get("RSO_GENESIS_PARENT_HASH"),
        help="Optional parentHash override for the genesis (baseline) day; defaults to the zero hash.",
    )
    parser.add_argument(
        "--ttl",
        type=int,
        default=int(os.environ.get("RSO_ATTESTATION_TTL", str(MAX_ATTESTATION_TTL))),
    )
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--workflow-run-id", default=os.environ.get("GITHUB_RUN_ID", ""))
    parser.add_argument("--cast", default=os.environ.get("CAST"))
    return parser.parse_args()


def should_skip(args: argparse.Namespace) -> bool:
    if args.ttl <= 0 or args.ttl > MAX_ATTESTATION_TTL:
        raise ValueError(f"RSO_ATTESTATION_TTL must be between 1 and {MAX_ATTESTATION_TTL} seconds")
    if not has_cast_wallet_config(private_key_env=args.private_key_env):
        return True
    if not args.attester:
        return True
    if not args.contract_address:
        return True
    if not args.node_id:
        raise ValueError("RSO_NODE_ID or GITHUB_REPOSITORY is required for node attestations")
    args.node_id = normalize_node_id(args.node_id)
    normalize_address(args.attester)
    normalize_address(args.on_behalf_of)
    normalize_address(args.contract_address)
    return False


def default_node_id() -> str:
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    return "github:" + repository if repository else ""


if __name__ == "__main__":
    raise SystemExit(main())
