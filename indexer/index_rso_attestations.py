#!/usr/bin/env python3
"""Build a static RSO Doc Chain attestation index from Ethereum logs."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from indexer.rso_profile import (  # noqa: E402
    RSO_DOC_CHAIN_ID,
    SEPOLIA_CHAIN_ID,
    SEPOLIA_DEPLOYMENT_BLOCK,
    SEPOLIA_DOCCHAIN_ADDRESS,
    build_static_index,
    filter_rso_events,
)
from vendor.docchain.indexer import EthereumRpc, RpcError  # noqa: E402
from vendor.docchain.store import load_event_cache, update_event_cache, write_json_file  # noqa: E402


NETWORKS = {
    "sepolia": {
        "chain_id": SEPOLIA_CHAIN_ID,
        "address": SEPOLIA_DOCCHAIN_ADDRESS,
        "from_block": SEPOLIA_DEPLOYMENT_BLOCK,
    }
}


def main() -> int:
    args = parse_args()
    try:
        config = network_config(args)
        rpc = EthereumRpc(args.rpc_url, timeout=args.timeout)
        latest_block = rpc.block_number()
        from_block = parse_block(args.from_block) if args.from_block else config["from_block"]
        to_block = resolve_to_block(args.to_block, latest_block, args.confirmations)
        result = update_event_cache(
            rpc=rpc,
            address=config["address"],
            cache_path=args.cache,
            checkpoint_path=args.checkpoint,
            from_block=from_block,
            to_block=to_block,
            chunk_size=args.chunk_size,
            doc_chain_id=RSO_DOC_CHAIN_ID,
            chain_id=config["chain_id"],
            network=args.network,
            progress=progress_callback(args),
        )
        events = filter_rso_events(load_event_cache(args.cache))

        index = build_static_index(
            network=args.network,
            chain_id=config["chain_id"],
            contract_address=config["address"],
            from_block=from_block,
            to_block=to_block,
            latest_chain_block=latest_block,
            confirmations=args.confirmations,
            chunk_size=args.chunk_size,
            events=events,
        )
        write_json_file(Path(args.out), index)
        if not args.quiet:
            print(
                f"scanned {result.chunk_count} chunks; cached "
                f"{result.new_event_count} new RSO attestations; wrote "
                f"{index['eventCount']} events across {index['docRefCount']} docRefs "
                f"into {args.out}"
            )
        return 0
    except (OSError, RpcError, ValueError) as exc:
        print(f"index_rso_attestations.py: {exc}", file=sys.stderr)
        return 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the RSO static Doc Chain attestation index.",
    )
    parser.add_argument("--network", choices=sorted(NETWORKS), default="sepolia")
    parser.add_argument(
        "--rpc-url",
        default=(
            os.environ.get("DOCCHAIN_RPC_URL")
            or os.environ.get("SEPOLIA_RPC_URL")
            or os.environ.get("RPC_URL")
        ),
        required=not (
            os.environ.get("DOCCHAIN_RPC_URL")
            or os.environ.get("SEPOLIA_RPC_URL")
            or os.environ.get("RPC_URL")
        ),
    )
    parser.add_argument("--from-block", help="First Ethereum block to scan.")
    parser.add_argument("--to-block", default="latest", help="Last Ethereum block to scan.")
    parser.add_argument("--confirmations", type=int, default=12)
    parser.add_argument("--chunk-size", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--progress-every-chunks", type=int, default=25)
    parser.add_argument(
        "--cache",
        default="indexer/cache/sepolia/doc-attested.jsonl",
        help="Append-only raw event cache.",
    )
    parser.add_argument(
        "--checkpoint",
        default="indexer/cache/sepolia/checkpoint.json",
        help="Scan checkpoint path.",
    )
    parser.add_argument(
        "--out",
        default="indexer/generated/sepolia/rso-docchain-index.json",
        help="Output JSON path.",
    )
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


def network_config(args: argparse.Namespace) -> dict[str, object]:
    if args.confirmations < 0:
        raise ValueError("--confirmations must not be negative")
    if args.chunk_size < 1:
        raise ValueError("--chunk-size must be at least 1")
    return NETWORKS[args.network]


def progress_callback(args: argparse.Namespace):
    if args.quiet or args.progress_every_chunks <= 0:
        return None
    chunks = {"count": 0}

    def progress(_chunk_start: int, chunk_end: int, _matched: int, new_total: int) -> None:
        chunks["count"] += 1
        if chunks["count"] % args.progress_every_chunks == 0:
            print(
                f"scanned through block {chunk_end}; "
                f"{new_total} new cached RSO events so far",
                file=sys.stderr,
            )

    return progress


def resolve_to_block(value: str, latest_block: int, confirmations: int) -> int:
    if value == "latest":
        return max(0, latest_block - confirmations)
    return parse_block(value)


def parse_block(value: str) -> int:
    parsed = int(value, 16) if value.startswith("0x") else int(value)
    if parsed < 0:
        raise ValueError("block numbers must not be negative")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
