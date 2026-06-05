"""RSO profile rules for Doc Chain attestation indexing."""

from __future__ import annotations

import base64
import binascii
import json
import urllib.parse
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime, timezone

from vendor.docchain.model import DocAttested
from vendor.docchain.store import build_docchain_index

RSO_PROFILE_URI = "https://om.pub/rso/doc-chain/v1"
RSO_DOC_CHAIN_ID = "0x8621c2851714436d60da45cf0e11253114a4f2002f73ddc159b4dc88fea5611d"
RSO_LOCATOR_MEDIA_TYPE = "application/vnd.ompub.rso.publication-locator.v1+json"

SEPOLIA_CHAIN_ID = 11155111
SEPOLIA_DOCCHAIN_ADDRESS = "0xaCE3a26Fe2F993e351a0eF74fb727Cfe1029884b"
SEPOLIA_DEPLOYMENT_BLOCK = 10849363

ZERO_BYTES32 = "0x" + "0" * 64
ZERO_ADDRESS = "0x" + "0" * 40


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
    operator_backing: dict[str, int] | None = None,
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
    if operator_backing is not None:
        index["operatorBacking"] = dict(sorted(operator_backing.items()))
    return decorate_rso_index(index, operator_backing=operator_backing)


def decorate_rso_index(
    index: dict[str, object],
    *,
    operator_backing: dict[str, int] | None = None,
) -> dict[str, object]:
    """Add RSO-specific date and fingerprint aliases to a generic index."""
    backing = normalize_operator_backing(
        operator_backing
        if operator_backing is not None
        else index.get("operatorBacking", index.get("identityBacking", {}))
    )
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
            decorate_publication(event)
            decorate_operator_backing(event, backing)
        group["agreementGroups"] = agreement_groups(events, backing)
        group["leadingAgreementGroup"] = group["agreementGroups"][0] if group["agreementGroups"] else None

    events = index.get("events", [])
    if not isinstance(events, list):
        raise ValueError("events must be a JSON array")
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("event records must be JSON objects")
        event["date"] = doc_ref_to_date(str(event["docRef"]))
        decorate_publication(event)
        decorate_operator_backing(event, backing)
    return index


def decorate_operator_backing(event: dict[str, object], backing: dict[str, int] | None = None) -> None:
    """Add RSO V1 operator-backing fields while preserving onBehalfOf metadata."""
    backing = backing or {}
    on_behalf_of = normalize_address(event.get("onBehalfOf", ZERO_ADDRESS))
    attester = normalize_address(event["attester"])
    event["onBehalfOf"] = on_behalf_of
    event["hasIdentityClaim"] = on_behalf_of != ZERO_ADDRESS
    event["identityAddress"] = "" if on_behalf_of == ZERO_ADDRESS else on_behalf_of
    event["operatorAttester"] = attester
    event["backingAccount"] = attester
    event["cardSpecificTdh"] = backing.get(attester, 0)


def decorate_publication(event: dict[str, object]) -> None:
    """Expose RSO publication locations from the generic DocChain URI field."""
    uri = str(event.get("uri", ""))
    try:
        event["publication"] = describe_publication_uri(uri)
    except ValueError as exc:
        event["publication"] = {
            "bundleSha256": "",
            "locations": [],
            "error": str(exc),
        }


def encode_publication_locator_uri(*, bundle_sha256: str, locations: Iterable[str]) -> str:
    """Build a signed data URI for one bundle fingerprint and many locations."""
    payload = {
        "bundleSha256": normalize_sha256(bundle_sha256),
        "locations": normalize_locations(locations),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{RSO_LOCATOR_MEDIA_TYPE};base64,{encoded}"


def describe_publication_uri(uri: str) -> dict[str, object]:
    """Return a stable publication description for direct and data URI forms."""
    if not uri:
        return {"bundleSha256": "", "locations": []}
    if uri.startswith("data:"):
        payload = decode_publication_locator_uri(uri)
        return {
            "bundleSha256": str(payload["bundleSha256"]),
            "locations": list(payload["locations"]),
        }
    return {"bundleSha256": "", "locations": [uri]}


def decode_publication_locator_uri(uri: str) -> dict[str, object]:
    """Decode and validate an RSO publication-locator data URI."""
    if not uri.startswith("data:"):
        raise ValueError("publication locator must be a data URI")
    header, separator, data = uri[5:].partition(",")
    if not separator:
        raise ValueError("publication locator data URI is missing a comma")
    parts = header.split(";") if header else []
    media_type = parts[0] if parts and "/" in parts[0] else "text/plain;charset=US-ASCII"
    is_base64 = bool(parts and parts[-1].lower() == "base64")
    if media_type != RSO_LOCATOR_MEDIA_TYPE:
        raise ValueError("unsupported publication locator media type")
    try:
        if is_base64:
            raw = base64.b64decode(data, validate=True)
        else:
            raw = urllib.parse.unquote_to_bytes(data)
        payload = json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("publication locator data URI is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("publication locator must decode to a JSON object")
    bundle_sha256 = normalize_sha256(payload.get("bundleSha256", ""))
    locations = normalize_locations(payload.get("locations"))
    return {
        "bundleSha256": bundle_sha256,
        "locations": locations,
    }


def normalize_sha256(value: object) -> str:
    text = str(value).lower()
    if text.startswith("0x"):
        text = text[2:]
    if len(text) != 64:
        raise ValueError("SHA-256 values must be 32 bytes")
    int(text, 16)
    return text


def normalize_locations(raw: object) -> list[str]:
    if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes, bytearray, dict)):
        raise ValueError("publication locator locations must be an array")
    locations = []
    for item in raw:
        if not isinstance(item, str) or not item:
            raise ValueError("publication locator locations must be non-empty strings")
        locations.append(item)
    if not locations:
        raise ValueError("publication locator requires at least one location")
    return locations


def event_backing_account(event: dict[str, object]) -> str:
    return normalize_address(event["attester"])


def agreement_groups(
    events: object,
    backing: dict[str, int] | None = None,
) -> list[dict[str, object]]:
    if not isinstance(events, list):
        raise ValueError("events must be a JSON array")
    backing = backing or {}
    groups: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("event records must be JSON objects")
        groups[(str(event["blockHash"]), str(event["contentHash"]))].append(event)
    records = []
    for (block_hash, content_hash), group_events in groups.items():
        backing_accounts = sorted({event_backing_account(event) for event in group_events})
        records.append(
            {
                "blockHash": block_hash,
                "contentHash": content_hash,
                "attestationCount": len(group_events),
                "operators": sorted({normalize_address(event["attester"]) for event in group_events}),
                "backingAccounts": backing_accounts,
                "cardSpecificTdh": sum(backing.get(account, 0) for account in backing_accounts),
                "bundleFingerprints": sorted(
                    {
                        str(event.get("publication", {}).get("bundleSha256", ""))
                        for event in group_events
                        if isinstance(event.get("publication"), dict)
                        and event.get("publication", {}).get("bundleSha256")
                    }
                ),
                "locations": sorted(
                    {
                        str(location)
                        for event in group_events
                        if isinstance(event.get("publication"), dict)
                        for location in event.get("publication", {}).get("locations", [])
                    }
                ),
            }
        )
    records.sort(
        key=lambda record: (
            -int(record["cardSpecificTdh"]),
            -int(record["attestationCount"]),
            str(record["blockHash"]),
        )
    )
    return records


def normalize_operator_backing(raw: object) -> dict[str, int]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("operator backing must be a JSON object")
    normalized: dict[str, int] = {}
    for operator, value in raw.items():
        if isinstance(value, dict):
            amount = value.get("cardSpecificTdh", 0)
        else:
            amount = value
        amount_int = int(amount)
        if amount_int < 0:
            raise ValueError("cardSpecificTdh must not be negative")
        normalized[normalize_address(operator)] = amount_int
    return normalized


def normalize_identity_backing(raw: object) -> dict[str, int]:
    """Backward-compatible alias for older callers."""
    return normalize_operator_backing(raw)


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
    if not text.lower().startswith("0x"):
        raise ValueError("hex values must be 0x-prefixed")
    body = text[2:]
    int(body or "0", 16)
    return "0x" + body.lower()


def normalize_address(value: object) -> str:
    normalized = normalize_hex(value)
    if len(normalized) != 42:
        raise ValueError("address values must be 20 bytes")
    return normalized
