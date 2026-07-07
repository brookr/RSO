"""Tests for the monthly Merkle commitment (Phase B §6).

Covers root determinism, inclusion proofs for every leaf at several tree sizes
(including the odd-leaf promotion path), tamper detection, hex/bytes input
parity, and the docRef month/day sentinel encoding.
"""
import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from attestation import merkle  # noqa: E402


def leaf(i):
    """A deterministic 32-byte leaf (stands in for a daily blockHash)."""
    return hashlib.sha256(f"day-{i}".encode()).hexdigest()


class MerkleRootTests(unittest.TestCase):
    def test_single_leaf_root_is_the_leaf(self):
        only = leaf(0)
        self.assertEqual(merkle.merkle_root([only]), only)

    def test_root_is_deterministic_and_order_sensitive_in_pairs_only(self):
        leaves = [leaf(i) for i in range(8)]
        self.assertEqual(merkle.merkle_root(leaves), merkle.merkle_root(leaves))

    def test_two_leaves_match_hand_computed_sorted_pair(self):
        a, b = leaf(0), leaf(1)
        lo, hi = sorted([bytes.fromhex(a), bytes.fromhex(b)])
        expected = hashlib.sha256(lo + hi).hexdigest()
        self.assertEqual(merkle.merkle_root([a, b]), expected)

    def test_bytes_and_hex_inputs_agree(self):
        hexes = [leaf(i) for i in range(5)]
        as_bytes = [bytes.fromhex(h) for h in hexes]
        self.assertEqual(merkle.merkle_root(hexes), merkle.merkle_root(as_bytes))

    def test_0x_prefix_tolerated(self):
        hexes = [leaf(i) for i in range(4)]
        prefixed = ["0x" + h for h in hexes]
        self.assertEqual(merkle.merkle_root(hexes), merkle.merkle_root(prefixed))

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            merkle.merkle_root([])

    def test_bad_length_raises(self):
        with self.assertRaises(ValueError):
            merkle.merkle_root(["abcd"])


class MerkleProofTests(unittest.TestCase):
    def test_every_leaf_proves_at_many_sizes(self):
        # 31 covers powers of two, odd levels, and the lone-promotion path.
        for n in range(1, 32):
            leaves = [leaf(i) for i in range(n)]
            root = merkle.merkle_root(leaves)
            for idx in range(n):
                proof = merkle.merkle_proof(leaves, idx)
                self.assertTrue(
                    merkle.verify_proof(leaves[idx], proof, root),
                    f"n={n} idx={idx} failed to verify",
                )

    def test_wrong_leaf_fails(self):
        leaves = [leaf(i) for i in range(10)]
        root = merkle.merkle_root(leaves)
        proof = merkle.merkle_proof(leaves, 3)
        self.assertFalse(merkle.verify_proof(leaf(99), proof, root))

    def test_tampered_proof_fails(self):
        leaves = [leaf(i) for i in range(10)]
        root = merkle.merkle_root(leaves)
        proof = merkle.merkle_proof(leaves, 3)
        if proof:
            proof[0] = leaf(123)  # swap a sibling
            self.assertFalse(merkle.verify_proof(leaves[3], proof, root))

    def test_proof_against_wrong_root_fails(self):
        leaves = [leaf(i) for i in range(10)]
        other = merkle.merkle_root([leaf(i) for i in range(10, 20)])
        proof = merkle.merkle_proof(leaves, 0)
        self.assertFalse(merkle.verify_proof(leaves[0], proof, other))

    def test_proof_root_accepts_0x_prefixed_root(self):
        leaves = [leaf(i) for i in range(6)]
        root = merkle.merkle_root(leaves)
        proof = merkle.merkle_proof(leaves, 2)
        self.assertTrue(merkle.verify_proof(leaves[2], proof, "0x" + root))

    def test_out_of_range_index_raises(self):
        leaves = [leaf(i) for i in range(4)]
        with self.assertRaises(IndexError):
            merkle.merkle_proof(leaves, 4)


class DocRefTests(unittest.TestCase):
    def test_day_ref_encoding(self):
        self.assertEqual(merkle.day_doc_ref(2026, 6, 23), 20260623000000)

    def test_month_ref_has_zero_day(self):
        self.assertEqual(merkle.month_doc_ref(1957, 10), 19571000000000)

    def test_kind_classification(self):
        self.assertEqual(merkle.doc_ref_kind(20260623000000), "day")
        self.assertEqual(merkle.doc_ref_kind(19571000000000), "month-root")

    def test_decode_roundtrip(self):
        self.assertEqual(merkle.decode_doc_ref(merkle.day_doc_ref(1959, 5, 23)),
                         (1959, 5, 23))
        self.assertEqual(merkle.decode_doc_ref(merkle.month_doc_ref(2025, 12)),
                         (2025, 12, 0))

    def test_genesis_day_and_its_month(self):
        # Sputnik genesis 1957-10-04 and the October-1957 month-root.
        self.assertEqual(merkle.day_doc_ref(1957, 10, 4), 19571004000000)
        self.assertEqual(merkle.doc_ref_kind(19571004000000), "day")
        self.assertEqual(merkle.month_doc_ref(1957, 10), 19571000000000)


if __name__ == "__main__":
    unittest.main()
