import unittest

from indexer.rso_profile import (
    RSO_DOC_CHAIN_ID,
    ZERO_BYTES32,
    build_static_index,
    doc_ref_to_date,
    filter_rso_events,
)
from vendor.docchain.model import DocAttested


class RsoIndexerTest(unittest.TestCase):
    def test_doc_ref_to_date_accepts_midnight_utc(self):
        self.assertEqual(doc_ref_to_date("20260519000000"), "2026-05-19")

    def test_doc_ref_to_date_rejects_non_midnight_refs(self):
        with self.assertRaises(ValueError):
            doc_ref_to_date("20260519000100")

    def test_filter_rso_events_matches_profile(self):
        rso_event = make_event(doc_ref=20260519000000)
        other_event = make_event(
            doc_chain_id="0x" + "1" * 64,
            doc_ref=20260519000000,
        )

        self.assertEqual(filter_rso_events([other_event, rso_event]), [rso_event])

    def test_build_static_index_groups_events_by_doc_ref(self):
        event = make_event(doc_ref=20260519000000, block_number=10861426)

        index = build_static_index(
            network="sepolia",
            chain_id=11155111,
            contract_address="0xace3a26fe2f993e351a0ef74fb727cfe1029884b",
            from_block=10849363,
            to_block=10861426,
            latest_chain_block=10861440,
            confirmations=12,
            chunk_size=2000,
            events=[event],
            indexed_at="2026-05-22T00:00:00Z",
        )

        self.assertEqual(index["eventCount"], 1)
        self.assertEqual(index["docRefCount"], 1)
        self.assertIn("20260519000000", index["docRefs"])
        doc_ref = index["docRefs"]["20260519000000"]
        self.assertEqual(doc_ref["date"], "2026-05-19")
        self.assertEqual(doc_ref["candidateCount"], 1)
        self.assertEqual(doc_ref["events"][0]["ethereumBlock"], 10861426)


def make_event(
    *,
    doc_chain_id=RSO_DOC_CHAIN_ID,
    doc_ref=20260519000000,
    block_number=10861426,
):
    return DocAttested(
        doc_chain_id=doc_chain_id,
        attester="0x" + "a" * 40,
        doc_ref=doc_ref,
        submitter="0x" + "b" * 40,
        parent_hash=ZERO_BYTES32,
        block_hash="0x" + "c" * 64,
        content_hash="0x" + "d" * 64,
        uri_hash="0x" + "e" * 64,
        uri="",
        block_number=block_number,
        transaction_hash="0x" + "f" * 64,
        log_index=3,
        ethereum_block_hash="0x" + "9" * 64,
    )


if __name__ == "__main__":
    unittest.main()
