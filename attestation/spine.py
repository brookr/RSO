#!/usr/bin/env python3
"""Deep-history attestation spine (Phase B §6, Phase-1 offline build).

From the welded daily manifest (per-day contentHash) computes, with zero chain
writes and no network:
  1. the continuous daily blockHash chain 1957-10-04 -> 2025-12-31 (genesis
     parent = 0x00..00), replicating DocChain.sol _hashDocBlockFields exactly,
  2. the 819 monthly Merkle roots (leaves = that month's daily blockHashes, a
     sha256 sorted-pair tree via merkle.py),
  3. the month-root DocBlock spine (each root committed as docRef YYYYMM00000000,
     contentHash = root, parentHash = previous month-root's blockHash),
  4. the weld value (2025-12-31 daily blockHash) the live era will parent off.

blockHash is EIP-712-domain-independent, so these values are final regardless of
which network/contract later attests them. Validated against the real on-chain
Sepolia genesis blockHash.

Usage: python3 spine.py --manifest full_manifest.txt --out-dir spine/
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from keccak256 import keccak256  # noqa: E402
from merkle import merkle_root, merkle_proof, verify_proof, day_doc_ref, month_doc_ref  # noqa: E402

TYPEHASH = bytes.fromhex("b84212102d711af6fc7ae9fa3e37753befb8b25762a552631b0e9ff9e8d07894")
DOCCHAIN = bytes.fromhex("6011620b5a3faa23f8078c2af0bb1a87bb85a68f784abdf3dbae67939c399bea")
ZERO = b"\x00" * 32

# Real on-chain Sepolia genesis (2026-04-20) — the formula self-check.
_ONCHAIN_CHECK = dict(
    doc_ref=20260420000000, parent="00" * 32,
    content="1838a06696902f7397d78d906c4bd9f5e9d9e372725ccbf4629bbd377231a740",
    block="e651a58398fe906551be2f4d2ec39bace608e7aa60f3c8c30cd276aac96e103e",
)


def block_hash(doc_ref: int, parent: bytes, content: bytes) -> bytes:
    """EIP-712 hashStruct(DocBlock) — the on-chain blockHash, byte-for-byte."""
    payload = TYPEHASH + DOCCHAIN + doc_ref.to_bytes(32, "big") + parent + content
    return keccak256(payload)


def _self_check():
    got = block_hash(_ONCHAIN_CHECK["doc_ref"], bytes.fromhex(_ONCHAIN_CHECK["parent"]),
                     bytes.fromhex(_ONCHAIN_CHECK["content"])).hex()
    if got != _ONCHAIN_CHECK["block"]:
        sys.exit(f"blockHash self-check FAILED: {got} != {_ONCHAIN_CHECK['block']}")


def build(manifest_path, out_dir):
    _self_check()
    os.makedirs(out_dir, exist_ok=True)

    # 1) daily blockHash chain + group leaves by month
    parent = ZERO
    months = {}          # (y,m) -> list[bytes]  daily blockHash leaves, chronological
    month_day_index = {}  # (y,m) -> list[str] day strings (for proofs)
    genesis_bh = weld_bh = None
    genesis_day = None
    days = 0
    prev_date = None
    # Full SPEC §5 line grammar — a manifest the clean-room verifiers would
    # reject (CRLF, dotted dates, junk recordCount) must not build a spine here.
    line_re = __import__("re").compile(r"^(\d{4})-(\d{2})-(\d{2}) ([0-9a-f]{64}) (0|[1-9][0-9]*)\n?$")
    with open(manifest_path, newline="") as f:
        for line in f:
            if line in ("", "\n"):
                continue
            mo = line_re.match(line)
            if mo is None:
                raise ValueError(f"manifest line does not match the SPEC §5 grammar: {line!r}")
            day = f"{mo.group(1)}-{mo.group(2)}-{mo.group(3)}"
            content_hex = mo.group(4)
            y, m, d = int(mo.group(1)), int(mo.group(2)), int(mo.group(3))
            cur_date = datetime.date(y, m, d)  # also rejects impossible dates
            # B2: fail-closed structural validation BEFORE hashing — the spine is a
            # strict parentHash chain, so any gap/dup/misorder silently corrupts it.
            if prev_date is not None and (cur_date - prev_date).days != 1:
                raise ValueError(f"manifest not strictly contiguous at {day} (prev {prev_date})")
            prev_date = cur_date
            if genesis_day is None:
                genesis_day = day
            bh = block_hash(day_doc_ref(y, m, d), parent, bytes.fromhex(content_hex))
            months.setdefault((y, m), []).append(bh)
            month_day_index.setdefault((y, m), []).append(day)
            if genesis_bh is None:
                genesis_bh = bh.hex()
            weld_bh = bh.hex()
            parent = bh
            days += 1

    if days == 0:
        raise ValueError(f"empty manifest: {manifest_path}")

    # 2/3) month-root Merkle roots + the month-root DocBlock spine
    mparent = ZERO
    roots = []
    for ym in sorted(months):
        y, m = ym
        leaves = months[ym]
        root = bytes.fromhex(merkle_root(leaves))
        mref = month_doc_ref(y, m)
        mbh = block_hash(mref, mparent, root)
        roots.append({
            "year": y, "month": m, "docRef": mref, "dayCount": len(leaves),
            "monthRoot": "0x" + root.hex(), "blockHash": "0x" + mbh.hex(),
            "parentHash": "0x" + mparent.hex(),
        })
        mparent = mbh
    spine_head = roots[-1]["blockHash"] if roots else None

    # 4) inclusion-proof round-trip self-test on a few days across the span
    sample_days = ["1957-10-04", "1985-06-15", "2025-12-31"]
    proof_checks = []
    for sd in sample_days:
        y, m, d = int(sd[:4]), int(sd[5:7]), int(sd[8:10])
        # the sample MONTH may be in span while the sample DAY is not (partial /
        # weld-extension builds) — .index() on a missing day would crash build()
        if sd not in month_day_index.get((y, m), []):
            continue
        idx = month_day_index[(y, m)].index(sd)
        leaves = months[(y, m)]
        proof = merkle_proof(leaves, idx)
        root_hex = next(r["monthRoot"][2:] for r in roots if r["year"] == y and r["month"] == m)
        ok = verify_proof(leaves[idx], proof, root_hex)
        proof_checks.append({"day": sd, "leafIndex": idx, "proofLen": len(proof), "verified": ok})

    with open(os.path.join(out_dir, "month_roots.json"), "w") as f:
        json.dump(roots, f, indent=1)
    summary = {
        "schema": "rso-core-omm-v1", "docChainId": "0x" + DOCCHAIN.hex(),
        "days": days, "months": len(roots),
        "genesis_day": genesis_day, "genesis_blockHash": "0x" + genesis_bh,
        "weld_value_2025_12_31": "0x" + weld_bh,
        "spine_head_blockHash": spine_head,
        "blockhash_self_check": "passed (vs on-chain Sepolia genesis)",
        "proof_round_trips": proof_checks,
    }
    with open(os.path.join(out_dir, "spine_summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    print(json.dumps(summary, indent=2))
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    build(args.manifest, args.out_dir)
