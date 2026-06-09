#!/usr/bin/env python3
"""Submit a range of signed RSO attestations in one attestBatch transaction.

Reads the per-day signed artifacts node_attest.py wrote, encodes them with the
vendored doc-chain batch encoder, and broadcasts a single transaction through
Foundry `cast` with a funded courier key. The contract (release 2) skips any
attestation that already landed, so re-running after a partial failure is
safe. Mixed attesters are allowed: a courier may submit several nodes' days.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from attestation.rso_attestation import (  # noqa: E402
    date_range,
    signed_attestation_path,
)
from vendor.docchain.attestation import (  # noqa: E402
    attest_batch_calldata,
    cast_path,
    normalize_address,
    subprocess_error_detail,
)


def main() -> int:
    try:
        args = parse_args()
        items, dates = collect_signed_items(args)
        if not items:
            print("No signed artifacts found for the requested range; nothing to submit.")
            return 0
        calldata = attest_batch_calldata(items)
        print(
            f"attestBatch: {len(items)} attestations "
            f"({dates[0]}..{dates[-1]}), calldata {len(calldata) // 2 - 1} bytes"
        )
        if args.calldata_out:
            Path(args.calldata_out).parent.mkdir(parents=True, exist_ok=True)
            Path(args.calldata_out).write_text(calldata + "\n", encoding="utf-8")
        if args.no_send and not args.dry_run:
            return 0

        contract_address = normalize_address(args.contract_address)
        if args.dry_run:
            output = run_cast(
                args,
                ["call", contract_address, "--data", calldata, "--rpc-url", args.rpc_url],
            )
            stored, skipped = decode_batch_result(output)
            print(f"dry run: would store {stored}, skip {skipped}")
            return 0

        command = [
            "send",
            contract_address,
            "--data",
            calldata,
            "--rpc-url",
            args.rpc_url,
            "--confirmations",
            str(args.confirmations),
            "--json",
        ]
        private_key = os.environ.get(args.private_key_env)
        if not private_key:
            raise ValueError(f"set {args.private_key_env} with the courier private key")
        command.extend(["--private-key", private_key])
        receipt = json.loads(run_cast(args, command).strip().splitlines()[-1])
        print(f"tx: {receipt['transactionHash']}")
        print(f"status: {receipt['status']}")
        print(f"gasUsed: {int(receipt['gasUsed'], 16)}")
        print(f"DocAttested events: {len(receipt.get('logs', []))}")
        if receipt.get("status") not in ("0x1", 1, "1"):
            raise ValueError("attestBatch transaction reverted")
        return 0
    except subprocess.CalledProcessError as exc:
        print(f"submit_batch.py: {subprocess_error_detail(exc)}", file=sys.stderr)
        return 2
    except (OSError, KeyError, ValueError) as exc:
        print(f"submit_batch.py: {exc}", file=sys.stderr)
        return 2


def collect_signed_items(args: argparse.Namespace):
    items = []
    dates = []
    for snapshot_date in date_range(args.start, args.end):
        path = signed_attestation_path(snapshot_date)
        if not path.exists():
            if args.require_all:
                raise ValueError(f"{snapshot_date}: missing signed artifact {path}")
            continue
        artifact = json.loads(path.read_text(encoding="utf-8"))
        if artifact.get("schema") != "rso-signed-attestation-v1":
            raise ValueError(f"{path} has unsupported schema")
        signed = artifact["signed"]
        prepared = signed["prepared"]
        items.append((prepared["attestation"], str(signed["signature"])))
        dates.append(snapshot_date)
    return items, dates


def run_cast(args: argparse.Namespace, command: list[str]) -> str:
    result = subprocess.run(
        [cast_path(args.cast), *command], check=True, capture_output=True, text=True
    )
    return result.stdout


def decode_batch_result(output: str) -> tuple[int, int]:
    raw = output.strip().splitlines()[-1].strip()
    body = raw[2:] if raw.startswith("0x") else raw
    if len(body) < 128:
        raise ValueError(f"unexpected attestBatch return data: {raw!r}")
    return int(body[0:64], 16), int(body[64:128], 16)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Submit signed RSO attestations in one attestBatch transaction."
    )
    parser.add_argument("--start", required=True, help="First archive date, YYYY-MM-DD.")
    parser.add_argument("--end", required=True, help="Last archive date, YYYY-MM-DD.")
    parser.add_argument(
        "--contract-address",
        default=os.environ.get("RSO_DOCCHAIN_ADDRESS") or os.environ.get("DOCCHAIN_ADDRESS"),
        help="DocChain release 2 contract address.",
    )
    parser.add_argument(
        "--rpc-url",
        default=os.environ.get("RSO_DOCCHAIN_RPC_URL")
        or os.environ.get("DOCCHAIN_RPC_URL")
        or os.environ.get("RPC_URL")
        or os.environ.get("SEPOLIA_RPC_URL"),
    )
    parser.add_argument(
        "--private-key-env",
        default="SUBMITTER_PRIVATE_KEY",
        help="Environment variable holding the courier (gas payer) private key.",
    )
    parser.add_argument(
        "--require-all",
        action="store_true",
        help="Fail if any day in the range lacks a signed artifact.",
    )
    parser.add_argument("--dry-run", action="store_true", help="eth_call instead of broadcasting.")
    parser.add_argument("--no-send", action="store_true", help="Only build calldata.")
    parser.add_argument("--calldata-out", help="Optional path for the raw calldata.")
    parser.add_argument("--confirmations", type=int, default=1)
    parser.add_argument("--cast", default=os.environ.get("CAST"))
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
