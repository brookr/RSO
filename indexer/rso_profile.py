"""RSO profile rules for Doc Chain attestation indexing."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

from vendor.docchain.model import DocAttested
from vendor.docchain.store import build_docchain_index

RSO_PROFILE_URI = "https://om.pub/rso/doc-chain/v1"
RSO_DOC_CHAIN_ID = "0x8621c2851714436d60da45cf0e11253114a4f2002f73ddc159b4dc88fea5611d"

SEPOLIA_CHAIN_ID = 11155111
SEPOLIA_DOCCHAIN_ADDRESS = "0xaCE3a26Fe2F993e351a0eF74fb727Cfe1029884b"
SEPOLIA_DEPLOYMENT_BLOCK = 10849363

ZERO_BYTES32 = "0x" + "0" * 64


def build_static_index(
    *,
    network: str,
    chain_id: int,
    contract_address: str,
    from_block: int,
    to_block: int,
    latest_chain_block: int,
    confirmations: int,
    chunk_size: int,
    events: Iterable[DocAttested],
    indexed_at: str | None = None,
) -> dict[str, object]:
    """Build the RSO static index by decorating the generic Doc Chain index."""
    index = build_docchain_index(
        events=events,
        network=network,
        chain_id=chain_id,
        contract_address=contract_address,
        doc_chain_id=RSO_DOC_CHAIN_ID,
        profile_uri=RSO_PROFILE_URI,
        from_block=from_block,
        to_block=to_block,
        latest_chain_block=latest_chain_block,
        confirmations=confirmations,
        indexed_at=indexed_at,
    )
    index["schema"] = "rso-docchain-index-v1"
    index["chunkSize"] = chunk_size
    return decorate_rso_index(index)


def decorate_rso_index(index: dict[str, object]) -> dict[str, object]:
    """Add RSO-specific date and fingerprint aliases to a generic index."""
    doc_refs = index.get("docRefs", {})
    if not isinstance(doc_refs, dict):
        raise ValueError("docRefs must be a JSON object")

    for doc_ref, group in doc_refs.items():
        if not isinstance(group, dict):
            raise ValueError("docRef groups must be JSON objects")
        date = doc_ref_to_date(str(doc_ref))
        group["date"] = date
        group["blockFingerprints"] = group.get("blockHashes", [])
        group["contentFingerprints"] = group.get("contentHashes", [])
        events = group.get("events", [])
        if not isinstance(events, list):
            raise ValueError("docRef events must be a JSON array")
        for event in events:
            if not isinstance(event, dict):
                raise ValueError("event records must be JSON objects")
            event["date"] = date

    events = index.get("events", [])
    if not isinstance(events, list):
        raise ValueError("events must be a JSON array")
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("event records must be JSON objects")
        event["date"] = doc_ref_to_date(str(event["docRef"]))
    return index


def filter_rso_events(events: Iterable[DocAttested]) -> list[DocAttested]:
    """Return only events for the RSO document chain."""
    return [
        event
        for event in events
        if normalize_hex(event.doc_chain_id) == RSO_DOC_CHAIN_ID.lower()
    ]


def doc_ref_to_date(doc_ref: str) -> str:
    """Convert an RSO YYYYMMDDHHMMSS docRef into a UTC date string."""
    if len(doc_ref) != 14 or not doc_ref.isdecimal():
        raise ValueError("RSO docRef must be a 14 digit YYYYMMDDHHMMSS value")
    parsed = datetime.strptime(doc_ref, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    if parsed.hour != 0 or parsed.minute != 0 or parsed.second != 0:
        raise ValueError("RSO daily docRef must be at 00:00:00 UTC")
    return parsed.date().isoformat()


def normalize_hex(value: object) -> str:
    """Normalize a 0x-prefixed hex string without changing its length."""
    text = str(value)
    if not text:
        return ""
    if not text.startswith("0x"):
        raise ValueError("hex values must be 0x-prefixed")
    int(text[2:] or "0", 16)
    return "0x" + text[2:].lower()
