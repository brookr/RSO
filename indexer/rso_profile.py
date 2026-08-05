"""RSO profile rules for Doc Chain attestation indexing."""

from __future__ import annotations

import base64
import binascii
import json
import re
import urllib.parse
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone

from vendor.docchain.attestation import (
    attestation_claim_fingerprint,
    claim_fingerprint,
    event_claim_fingerprint,
    normalize_address,
    normalize_bytes32,
)
from vendor.docchain.model import DocAttested
from vendor.docchain.store import build_docchain_index
from support.tdh import SUPPORT_FIELDS, normalize_support_record

# The RSO chain identity. The profile URI is the permanent protocol id --
# docChainId = keccak256(profile URI) -- and is deliberately unversioned:
# protocol revisions are described in metadata and docs, never baked into the
# id. The contentHash covers only the elset-intrinsic core (see
# pipeline.snapshot.CONTENT_EXCLUDED_FIELDS); the mutable object-directory
# fields live in per-day annotations with the time each value was observed.
RSO_PROFILE_URI = "https://om.pub/rso/doc-chain"
RSO_DOC_CHAIN_ID = "0x6011620b5a3faa23f8078c2af0bb1a87bb85a68f784abdf3dbae67939c399bea"
RSO_LOCATOR_MEDIA_TYPE = "application/vnd.ompub.rso.publication-locator.v1+json"

SEPOLIA_CHAIN_ID = 11155111
SEPOLIA_DOCCHAIN_ADDRESS = "0x867FcC4f0339009043E9F6e554DD516Bcf1bcaa9"
SEPOLIA_DEPLOYMENT_BLOCK = 11026268

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
    operator_backing: Mapping[str, object] | None = None,
    operator_backing_by_date: Mapping[str, object] | None = None,
    verified_claims: Mapping[str, object] | None = None,
    direct_witnesses: Mapping[str, object] | None = None,
    direct_witnesses_by_date: Mapping[str, object] | None = None,
    indexed_at: str | None = None,
    doc_chain_id: str = RSO_DOC_CHAIN_ID,
    profile_uri: str = RSO_PROFILE_URI,
    chain_profile: Mapping[str, object] | None = None,
    block_timestamps: Mapping[int, int] | None = None,
) -> dict[str, object]:
    """Build the RSO static index by decorating the generic Doc Chain index."""
    index = build_docchain_index(
        events=events,
        network=network,
        chain_id=chain_id,
        contract_address=contract_address,
        doc_chain_id=doc_chain_id,
        profile_uri=profile_uri,
        from_block=from_block,
        to_block=to_block,
        latest_chain_block=latest_chain_block,
        confirmations=confirmations,
        indexed_at=indexed_at,
    )
    index["schema"] = "rso-docchain-index-v1"
    index["chunkSize"] = chunk_size
    if chain_profile is not None:
        index["chainProfile"] = dict(chain_profile)
    if operator_backing is not None:
        index["nodeSupport"] = dict(sorted(normalize_operator_backing(operator_backing).items()))
    index["sweeperVerifiedClaimCount"] = len(normalize_verified_claims(verified_claims))
    witness_accounts = set(normalize_direct_witnesses(direct_witnesses))
    for records in normalize_dated_support(
        direct_witnesses_by_date,
        normalize_direct_witnesses,
    ).values():
        witness_accounts.update(records)
    index["directWitnessAccountCount"] = len(witness_accounts)
    if operator_backing_by_date is not None or direct_witnesses_by_date is not None:
        index["tdhSupportDates"] = sorted(
            set((operator_backing_by_date or {}).keys())
            | set((direct_witnesses_by_date or {}).keys())
        )
    return decorate_rso_index(
        index,
        operator_backing=operator_backing,
        operator_backing_by_date=operator_backing_by_date,
        verified_claims=verified_claims,
        direct_witnesses=direct_witnesses,
        direct_witnesses_by_date=direct_witnesses_by_date,
        block_timestamps=block_timestamps,
    )


def decorate_rso_index(
    index: dict[str, object],
    *,
    operator_backing: Mapping[str, object] | None = None,
    operator_backing_by_date: Mapping[str, object] | None = None,
    verified_claims: Mapping[str, object] | None = None,
    direct_witnesses: Mapping[str, object] | None = None,
    direct_witnesses_by_date: Mapping[str, object] | None = None,
    block_timestamps: Mapping[int, int] | None = None,
) -> dict[str, object]:
    """Add RSO-specific date and fingerprint aliases to a generic index."""
    backing = normalize_operator_backing(
        operator_backing
        if operator_backing is not None
        else index.get("nodeSupport", {})
    )
    claims = normalize_verified_claims(verified_claims)
    witnesses = normalize_direct_witnesses(direct_witnesses)
    dated_backing = normalize_dated_support(operator_backing_by_date, normalize_operator_backing)
    dated_witnesses = normalize_dated_support(direct_witnesses_by_date, normalize_direct_witnesses)
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
            decorate_attestation_timing(event, block_timestamps)
            decorate_tdh_support(
                event,
                dated_backing.get(date, backing),
                claims,
                dated_witnesses.get(date, witnesses),
            )
        group["agreementGroups"] = agreement_groups(events)
        group["leadingAgreementGroup"] = leading_agreement_group(group["agreementGroups"])
        timed = [event for event in events if event.get("attestedAtUtc")]
        if timed:
            group["firstAttestedAtUtc"] = min(str(event["attestedAtUtc"]) for event in timed)
            group["minAttestationLagDays"] = min(
                int(event["attestationLagDays"]) for event in timed
            )

    events = index.get("events", [])
    if not isinstance(events, list):
        raise ValueError("events must be a JSON array")
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("event records must be JSON objects")
        event["date"] = doc_ref_to_date(str(event["docRef"]))
        decorate_publication(event)
        decorate_attestation_timing(event, block_timestamps)
        event_date = str(event["date"])
        decorate_tdh_support(
            event,
            dated_backing.get(event_date, backing),
            claims,
            dated_witnesses.get(event_date, witnesses),
        )
    return index


def decorate_attestation_timing(
    event: dict[str, object],
    block_timestamps: Mapping[int, int] | None,
) -> None:
    """Stamp the event with its unforgeable commitment time.

    An attestation's verifiable claim is "these bytes were committed no later
    than this block": the block timestamp, not any observed_at value inside
    the published artifacts, is what forensic consumers should weigh. Lag is
    measured from the docRef day's state boundary (00:00 UTC); a node
    witnessing the day as it closes attests at lag 0, a re-attestation of
    history wears its distance openly.
    """
    if not block_timestamps:
        return
    timestamp = block_timestamps.get(int(event["ethereumBlock"]))
    if timestamp is None:
        return
    attested_at = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
    day_start = datetime.strptime(str(event["date"]), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    event["attestedAtUtc"] = attested_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    event["attestationLagDays"] = max(
        0, int((attested_at - day_start).total_seconds() // 86400)
    )


def decorate_tdh_support(
    event: dict[str, object],
    backing: Mapping[str, object] | None = None,
    verified_claims: Mapping[str, Mapping[str, object]] | None = None,
    direct_witnesses: Mapping[str, Mapping[str, object]] | None = None,
) -> None:
    """Add direct-witness and verified node-backing TDH fields."""
    backing = backing or {}
    verified_claims = verified_claims or {}
    direct_witnesses = direct_witnesses or {}
    on_behalf_of = normalize_address(event.get("onBehalfOf", ZERO_ADDRESS))
    attester = normalize_address(event["attester"])
    claimed_node_id = event_claimed_node_id(event)
    claim_fingerprint = event_claim_fingerprint(event)
    verification = verified_claims.get(claim_fingerprint)
    node_id = verified_node_id(
        verification,
        claimed_node_id=claimed_node_id,
        attester=attester,
    )
    witness = direct_witnesses.get(attester, {})
    direct_identity = str(witness.get("identity", ""))
    direct_witness_tdh = int(witness.get("cardSpecificTdh", 0)) if direct_identity else 0
    node_support = (
        normalize_support_record(backing[node_id])
        if node_id and node_id in backing
        else {field: 0 for field in SUPPORT_FIELDS}
    )
    event["onBehalfOf"] = on_behalf_of
    event["claimFingerprint"] = claim_fingerprint
    event["claimedNodeId"] = claimed_node_id
    event["nodeId"] = node_id
    event["nodeAuthorizationStatus"] = (
        "verified" if node_id else "unverified" if claimed_node_id else "not_claimed"
    )
    event["publicationVerification"] = (
        str((verification or {}).get("publicationStatus", "")) if node_id else ""
    )
    event["directWitnessIdentity"] = direct_identity
    event["directWitnessTdh"] = direct_witness_tdh
    for field in SUPPORT_FIELDS:
        event["node" + field[0].upper() + field[1:]] = node_support[field]
    event["combinedSupportTdh"] = direct_witness_tdh + node_support["usableBackingTdh"]


def event_claimed_node_id(event: Mapping[str, object]) -> str:
    publication = event.get("publication", {})
    if not isinstance(publication, dict):
        return ""
    node_id = publication.get("nodeId", "")
    if not isinstance(node_id, str) or not node_id:
        return ""
    return normalize_node_id(node_id)


def github_node_id(owner: str, repo: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner):
        return ""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", repo):
        return ""
    if owner in (".", "..") or repo in (".", ".."):
        return ""
    return f"github:{owner.lower()}/{repo.lower()}"


def decorate_publication(event: dict[str, object]) -> None:
    """Expose RSO publication locations from the generic DocChain URI field."""
    uri = str(event.get("uri", ""))
    try:
        event["publication"] = describe_publication_uri(uri)
    except ValueError as exc:
        event["publication"] = {
            "nodeId": "",
            "bundleSha256": "",
            "locations": [],
            "error": str(exc),
        }


def encode_publication_locator_uri(
    *,
    bundle_sha256: str,
    locations: Iterable[str],
    node_id: str = "",
) -> str:
    """Build a signed data URI for one bundle fingerprint and many locations."""
    payload = {
        "bundleSha256": normalize_sha256(bundle_sha256),
        "locations": normalize_locations(locations),
    }
    if node_id:
        payload["nodeId"] = normalize_node_id(node_id)
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    encoded = base64.b64encode(raw).decode("ascii")
    return f"data:{RSO_LOCATOR_MEDIA_TYPE};base64,{encoded}"


def describe_publication_uri(uri: str) -> dict[str, object]:
    """Return a stable publication description for direct and data URI forms."""
    if not uri:
        return {"nodeId": "", "bundleSha256": "", "locations": []}
    if uri.startswith("data:"):
        payload = decode_publication_locator_uri(uri)
        return {
            "nodeId": str(payload["nodeId"]),
            "bundleSha256": str(payload["bundleSha256"]),
            "locations": list(payload["locations"]),
        }
    return {"nodeId": "", "bundleSha256": "", "locations": [uri]}


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
    node_id = payload.get("nodeId", "")
    if not isinstance(node_id, str):
        raise ValueError("publication locator nodeId must be a string")
    node_id = normalize_node_id(node_id) if node_id else ""
    bundle_sha256 = normalize_sha256(payload.get("bundleSha256", ""))
    locations = normalize_locations(payload.get("locations"))
    return {
        "nodeId": node_id,
        "bundleSha256": bundle_sha256,
        "locations": locations,
    }


def normalize_sha256(value: object) -> str:
    text = str(value).lower()
    if text.startswith("0x"):
        text = text[2:]
    if len(text) != 64:
        raise ValueError("SHA-256 values must be 32 bytes")
    if not _HEX_DIGITS.issuperset(text):
        raise ValueError("SHA-256 values contain non-hex characters")
    return text


def normalize_locations(raw: object) -> list[str]:
    if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes, bytearray, dict)):
        raise ValueError("publication locator locations must be an array")
    locations = []
    seen = set()
    for item in raw:
        if not isinstance(item, str) or not item:
            raise ValueError("publication locator locations must be non-empty strings")
        if item in seen:
            raise ValueError("publication locator locations must be unique")
        seen.add(item)
        locations.append(item)
    if not locations:
        raise ValueError("publication locator requires at least one location")
    return locations


def agreement_groups(
    events: object,
) -> list[dict[str, object]]:
    if not isinstance(events, list):
        raise ValueError("events must be a JSON array")
    groups: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    for event in events:
        if not isinstance(event, dict):
            raise ValueError("event records must be JSON objects")
        groups[(str(event["blockHash"]), str(event["contentHash"]))].append(event)
    node_groups = support_groups(groups, "nodeId")
    identity_groups = support_groups(groups, "directWitnessIdentity")
    records = []
    for (block_hash, content_hash), group_events in groups.items():
        agreement_key = (block_hash, content_hash)
        backed_node_ids = sorted(
            {
                str(event.get("nodeId", ""))
                for event in group_events
                if event.get("nodeId") and len(node_groups[str(event["nodeId"])]) == 1
            }
        )
        direct_identities = sorted(
            {
                str(event.get("directWitnessIdentity", ""))
                for event in group_events
                if event.get("directWitnessIdentity")
                and len(identity_groups[str(event["directWitnessIdentity"])]) == 1
            }
        )
        direct_witness_tdh = sum(
            max(
                int(event.get("directWitnessTdh", 0))
                for event in group_events
                if event.get("directWitnessIdentity") == identity
            )
            for identity in direct_identities
        )
        node_support = {
            field: sum(
                max(
                    int(event.get("node" + field[0].upper() + field[1:], 0))
                    for event in group_events
                    if event.get("nodeId") == node_id
                )
                for node_id in backed_node_ids
            )
            for field in SUPPORT_FIELDS
        }
        records.append(
            {
                "blockHash": block_hash,
                "contentHash": content_hash,
                "attestationCount": len(group_events),
                "attesters": sorted({normalize_address(event["attester"]) for event in group_events}),
                "directWitnessIdentities": direct_identities,
                "backedNodeIds": backed_node_ids,
                "equivocatingDirectWitnessIdentities": sorted(
                    identity for identity, keys in identity_groups.items() if len(keys) > 1 and agreement_key in keys
                ),
                "equivocatingNodes": sorted(
                    node_id for node_id, keys in node_groups.items() if len(keys) > 1 and agreement_key in keys
                ),
                "directWitnessTdh": direct_witness_tdh,
                **{
                    "node" + field[0].upper() + field[1:]: amount
                    for field, amount in node_support.items()
                },
                "combinedSupportTdh": direct_witness_tdh + node_support["usableBackingTdh"],
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
            -int(record["combinedSupportTdh"]),
            str(record["blockHash"]),
        )
    )
    return records


def leading_agreement_group(records: list[dict[str, object]]) -> dict[str, object] | None:
    if not records:
        return None
    if len(records) == 1:
        return records[0]
    leading_support = int(records[0]["combinedSupportTdh"])
    if int(records[1]["combinedSupportTdh"]) == leading_support:
        return None
    return records[0]


def support_groups(
    groups: Mapping[tuple[str, str], list[dict[str, object]]],
    field: str,
) -> dict[str, set[tuple[str, str]]]:
    result: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for agreement_key, events in groups.items():
        for event in events:
            value = str(event.get(field, ""))
            if value:
                result[value].add(agreement_key)
    return result


def verified_node_id(
    verification: Mapping[str, object] | None,
    *,
    claimed_node_id: str,
    attester: str,
) -> str:
    if not verification:
        return ""
    if verification.get("authorizationStatus") != "verified":
        return ""
    publication_status = verification.get("publicationStatus")
    if publication_status == "verified":
        if not claimed_node_id:
            return ""
    elif publication_status == "hash_only":
        # No locator exists, so there is no locator nodeId to match; identity
        # rests on the sweeper-checked node declaration alone.
        if claimed_node_id:
            return ""
    else:
        return ""
    try:
        node_id = normalize_node_id(str(verification.get("nodeId", "")))
        evidence_claimed_node_id = normalize_node_id(
            str(verification.get("claimedNodeId", ""))
        )
        declared_attester = normalize_address(
            verification.get("declaredAttester", ZERO_ADDRESS)
        )
        attestation_attester = normalize_address(
            verification.get("attestationAttester", ZERO_ADDRESS)
        )
    except ValueError:
        return ""
    if publication_status == "verified" and node_id != claimed_node_id:
        return ""
    if evidence_claimed_node_id != node_id:
        return ""
    if declared_attester != attester or attestation_attester != attester:
        return ""
    return node_id


def normalize_verified_claims(
    raw: Mapping[str, object] | None,
) -> dict[str, dict[str, object]]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError("verified claims must be a JSON object")
    normalized: dict[str, dict[str, object]] = {}
    for fingerprint, record in raw.items():
        if not isinstance(record, Mapping):
            raise ValueError("verified claim records must be JSON objects")
        text = normalize_sha256(fingerprint)
        if text in normalized:
            raise ValueError("verified claims contain duplicate normalized fingerprints")
        normalized[text] = dict(record)
    return normalized


def normalize_direct_witnesses(
    raw: Mapping[str, object] | None,
) -> dict[str, dict[str, object]]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError("direct witness TDH must be a JSON object")
    normalized: dict[str, dict[str, object]] = {}
    identity_amounts: dict[str, int] = {}
    for key, record in raw.items():
        if not isinstance(record, Mapping):
            record = {"cardSpecificTdh": record}
        identity = str(record.get("identity", key))
        amount = int(record.get("cardSpecificTdh", 0))
        if amount < 0:
            raise ValueError("cardSpecificTdh must not be negative")
        existing_amount = identity_amounts.get(identity)
        if existing_amount is not None and existing_amount != amount:
            raise ValueError("one direct witness identity cannot have conflicting TDH")
        identity_amounts[identity] = amount
        accounts = record.get("accounts")
        if accounts is None:
            accounts = [key]
        if not isinstance(accounts, Iterable) or isinstance(accounts, (str, bytes, bytearray, dict)):
            raise ValueError("direct witness accounts must be an array")
        for account in accounts:
            address = normalize_address(account)
            existing = normalized.get(address)
            if existing and (
                existing["identity"] != identity
                or int(existing["cardSpecificTdh"]) != amount
            ):
                raise ValueError("one account cannot have conflicting direct witness identity data")
            normalized[address] = {
                "identity": identity,
                "cardSpecificTdh": amount,
            }
    return normalized


def normalize_operator_backing(raw: object) -> dict[str, dict[str, int]]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("operator backing must be a JSON object")
    normalized: dict[str, dict[str, int]] = {}
    for node_id, value in raw.items():
        normalized_node_id = normalize_node_id(str(node_id))
        if normalized_node_id in normalized:
            raise ValueError("operator backing contains duplicate normalized node ids")
        normalized[normalized_node_id] = normalize_support_record(value)
    return normalized


def normalize_dated_support(
    raw: Mapping[str, object] | None,
    normalizer,
) -> dict[str, object]:
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError("dated TDH support must be a JSON object")
    normalized = {}
    for snapshot_date, support in raw.items():
        date_text = str(snapshot_date)
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_text):
            raise ValueError("dated TDH support keys must be YYYY-MM-DD")
        doc_ref_to_date(date_text.replace("-", "") + "000000")
        normalized[date_text] = normalizer(support)
    return normalized


def normalize_node_id(value: str) -> str:
    text = value.strip().lower()
    if text.startswith("github:"):
        body = text.removeprefix("github:")
    elif "/" in text and ":" not in text:
        body = text
    elif text.startswith("domain:"):
        host = text.removeprefix("domain:")
        labels = host.split(".")
        if (
            len(host) > 253
            or len(labels) < 2
            or any(
                len(label) > 63
                or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
                for label in labels
            )
        ):
            raise ValueError("domain node ids must be domain:hostname")
        return "domain:" + host
    else:
        raise ValueError("node ids must use a supported scheme such as github:owner/repo")
    owner_repo = body.split("/", 1)
    if len(owner_repo) != 2:
        raise ValueError("GitHub node ids must be github:owner/repo")
    node_id = github_node_id(owner_repo[0], owner_repo[1])
    if not node_id:
        raise ValueError("GitHub node id contains invalid owner or repo")
    return node_id


def normalize_identity_backing(raw: object) -> dict[str, int]:
    """Backward-compatible alias for older callers."""
    return normalize_operator_backing(raw)


def filter_rso_events(
    events: Iterable[DocAttested],
    doc_chain_id: str = RSO_DOC_CHAIN_ID,
) -> list[DocAttested]:
    """Return only events for the requested RSO document chain."""
    return [
        event
        for event in events
        if normalize_hex(event.doc_chain_id) == doc_chain_id.lower()
    ]


def doc_ref_to_date(doc_ref: str) -> str:
    """Convert an RSO YYYYMMDDHHMMSS docRef into a UTC date string."""
    if len(doc_ref) != 14 or not doc_ref.isdecimal():
        raise ValueError("RSO docRef must be a 14 digit YYYYMMDDHHMMSS value")
    parsed = datetime.strptime(doc_ref, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    if parsed.hour != 0 or parsed.minute != 0 or parsed.second != 0:
        raise ValueError("RSO daily docRef must be at 00:00:00 UTC")
    return parsed.date().isoformat()


_HEX_DIGITS = frozenset("0123456789abcdef")


def normalize_hex(value: object) -> str:
    """Normalize a 0x-prefixed hex string without changing its length."""
    text = str(value)
    if not text:
        return ""
    if not text.lower().startswith("0x"):
        raise ValueError("hex values must be 0x-prefixed")
    body = text[2:].lower()
    # validate digits directly: int(body, 16) silently accepts "_", a leading
    # sign, and whitespace, which would corrupt topic filters downstream
    if body and not _HEX_DIGITS.issuperset(body):
        raise ValueError("hex values contain non-hex characters")
    return "0x" + body
