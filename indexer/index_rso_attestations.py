#!/usr/bin/env python3
"""Build a static RSO Doc Chain attestation index from Ethereum logs."""

from __future__ import annotations

import argparse
import json
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
    },
    "custom": {
        "chain_id": None,
        "address": None,
        "from_block": None,
    },
}


def main() -> int:
    args = parse_args()
    try:
        config = network_config(args)
        rpc = EthereumRpc(args.rpc_url, timeout=args.timeout)
        latest_block = rpc.block_number()
        from_block = parse_block(args.from_block) if args.from_block else config["from_block"]
        to_block = resolve_to_block(args.to_block, latest_block, args.confirmations)
        cache_path = args.cache or f"indexer/cache/{args.network}/doc-attested.jsonl"
        checkpoint_path = args.checkpoint or f"indexer/cache/{args.network}/checkpoint.json"
        out_path = args.out or f"indexer/generated/{args.network}/rso-docchain-index.json"
        operator_backing = load_operator_backing(args.backing) if args.backing else None
        result = update_event_cache(
            rpc=rpc,
            address=config["address"],
            cache_path=cache_path,
            checkpoint_path=checkpoint_path,
            from_block=from_block,
            to_block=to_block,
            chunk_size=args.chunk_size,
            doc_chain_id=RSO_DOC_CHAIN_ID,
            chain_id=config["chain_id"],
            network=args.network,
            progress=progress_callback(args),
        )
        events = filter_rso_events(load_event_cache(cache_path))

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
            operator_backing=operator_backing,
        )
        write_json_file(Path(out_path), index)
        if not args.quiet:
            print(
                f"scanned {result.chunk_count} chunks; cached "
                f"{result.new_event_count} new RSO attestations; wrote "
                f"{index['eventCount']} events across {index['docRefCount']} docRefs "
                f"into {out_path}"
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
        default=None,
        help="Append-only raw event cache.",
    )
    parser.add_argument(
        "--checkpoint",
        default=None,
        help="Scan checkpoint path.",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output JSON path.",
    )
    parser.add_argument("--chain-id", type=int, help="Chain ID for --network custom.")
    parser.add_argument("--contract-address", help="DocChain contract address for --network custom.")
    parser.add_argument("--deployment-block", type=int, help="First block for --network custom.")
    parser.add_argument(
        "--backing",
        help="Optional JSON backing snapshot mapping operator attesters to card-specific TDH.",
    )
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


def load_operator_backing(path: str) -> dict[str, int]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("backing snapshot must be a JSON object")
    if raw.get("schema") == "rso-operator-backing-snapshot-v1":
        operators = raw.get("operators", {})
        if not isinstance(operators, dict):
            raise ValueError("backing snapshot operators must be a JSON object")
        return {
            str(operator): int(value.get("cardSpecificTdh", 0) if isinstance(value, dict) else value)
            for operator, value in operators.items()
        }
    if raw.get("schema") == "rso-operator-backing-v1":
        operators = raw.get("operators", raw.get("identities", {}))
        if not isinstance(operators, dict):
            raise ValueError("backing snapshot operators must be a JSON object")
        return {
            str(operator): int(value.get("cardSpecificTdh", 0) if isinstance(value, dict) else value)
            for operator, value in operators.items()
        }
    return {
        str(operator): int(value.get("cardSpecificTdh", 0) if isinstance(value, dict) else value)
        for operator, value in raw.items()
    }


def load_identity_backing(path: str) -> dict[str, int]:
    """Backward-compatible alias for older callers."""
    return load_operator_backing(path)


def network_config(args: argparse.Namespace) -> dict[str, object]:
    if args.confirmations < 0:
        raise ValueError("--confirmations must not be negative")
    if args.chunk_size < 1:
        raise ValueError("--chunk-size must be at least 1")
    config = dict(NETWORKS[args.network])
    if args.network == "custom":
        chain_id = args.chain_id or int_env("RSO_DOCCHAIN_CHAIN_ID") or int_env("DOCCHAIN_CHAIN_ID")
        address = args.contract_address or os.environ.get("RSO_DOCCHAIN_ADDRESS") or os.environ.get("DOCCHAIN_ADDRESS")
        from_block = (
            args.deployment_block
            if args.deployment_block is not None
            else int_env("RSO_DOCCHAIN_DEPLOYMENT_BLOCK")
        )
        if chain_id is None:
            raise ValueError("--chain-id or RSO_DOCCHAIN_CHAIN_ID is required for custom network")
        if not address:
            raise ValueError("--contract-address or RSO_DOCCHAIN_ADDRESS is required for custom network")
        if from_block is None:
            raise ValueError("--deployment-block or RSO_DOCCHAIN_DEPLOYMENT_BLOCK is required for custom network")
        config.update({"chain_id": chain_id, "address": address, "from_block": from_block})
    return config


def int_env(name: str) -> int | None:
    raw = os.environ.get(name)
    return int(raw) if raw else None


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
