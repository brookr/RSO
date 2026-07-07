"""Monthly Merkle commitment for the deep-history doc-chain (Phase B §6).

Daily blockHashes are the leaves; one Merkle root per month is attested
on-chain as the month's contentHash (docRef ``YYYYMM00000000``). Any day is
provable by an inclusion proof against its month-root, checked client-side.

SHA-256 throughout: stdlib-only (the project's zero-dependency rule), fast
enough to build a 25k-leaf tree, and consistent with the catalog contentHash
domain. (The chain's blockHash is EIP-712/keccak; that is the *leaf value*,
an opaque 32 bytes here — the Merkle hash over leaves is a separate, sha256
domain, and proofs are verified off-chain so no on-chain MerkleProof.verify is
required.)

Tree shape: **sorted-pair** hashing (``sha256(min(a,b) + max(a,b))``) so the
combine step is commutative and a proof is just a list of sibling hashes with
no left/right flags. A lone node on an odd level is **promoted unchanged**
(no duplication). All node values interchange as lowercase hex, no ``0x``.
"""
from __future__ import annotations

import hashlib


def _to_bytes(value) -> bytes:
    if isinstance(value, (bytes, bytearray)):
        b = bytes(value)
    elif isinstance(value, str):
        s = value[2:] if value.startswith("0x") else value
        b = bytes.fromhex(s)
    else:
        # fail-closed with the CONTRACTUAL exception type, not AttributeError
        raise ValueError(f"Merkle node must be bytes or hex str, got {type(value).__name__}")
    if len(b) != 32:
        raise ValueError(f"Merkle node must be 32 bytes, got {len(b)}")
    return b


def _combine(a: bytes, b: bytes) -> bytes:
    lo, hi = (a, b) if a <= b else (b, a)
    return hashlib.sha256(lo + hi).digest()


def merkle_levels(leaves) -> list[list[bytes]]:
    nodes = [_to_bytes(leaf) for leaf in leaves]
    if not nodes:
        raise ValueError("cannot build a Merkle tree over zero leaves")
    levels = [nodes]
    while len(nodes) > 1:
        nxt: list[bytes] = []
        for i in range(0, len(nodes), 2):
            if i + 1 < len(nodes):
                nxt.append(_combine(nodes[i], nodes[i + 1]))
            else:
                nxt.append(nodes[i])  # promote the lone node unchanged
        levels.append(nxt)
        nodes = nxt
    return levels


def merkle_root(leaves) -> str:
    """The root as lowercase hex (no 0x)."""
    return merkle_levels(leaves)[-1][0].hex()


def merkle_proof(leaves, index: int) -> list[str]:
    """Sibling path proving leaf ``index`` is in ``merkle_root(leaves)``."""
    levels = merkle_levels(leaves)
    if not 0 <= index < len(levels[0]):
        raise IndexError(f"leaf index {index} out of range ({len(levels[0])} leaves)")
    proof: list[str] = []
    for level in levels[:-1]:
        sib = index ^ 1
        if sib < len(level):
            proof.append(level[sib].hex())
        # else: this node was promoted (no sibling) -> nothing to add
        index //= 2
    return proof


def verify_proof(leaf, proof, root) -> bool:
    """Recompute the root from a leaf + sibling path; compare to ``root``."""
    node = _to_bytes(leaf)
    for sibling in proof:
        node = _combine(node, _to_bytes(sibling))
    want = root[2:] if isinstance(root, str) and root.startswith("0x") else root
    return node.hex() == (want.lower() if isinstance(want, str) else want.hex())


# --- docRef sentinels (uint64 YYYYMMDD000000; DD=00 marks a month-root) ---

def day_doc_ref(year: int, month: int, day: int) -> int:
    return (year * 10000 + month * 100 + day) * 1_000_000


def month_doc_ref(year: int, month: int) -> int:
    return (year * 10000 + month * 100) * 1_000_000  # DD == 00


def doc_ref_kind(doc_ref: int) -> str:
    day = (doc_ref // 1_000_000) % 100
    return "month-root" if day == 0 else "day"


def decode_doc_ref(doc_ref: int) -> tuple[int, int, int]:
    head = doc_ref // 1_000_000
    return head // 10000, (head // 100) % 100, head % 100
