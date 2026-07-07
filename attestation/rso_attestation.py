"""RSO-specific Doc Chain attestation preparation and node state helpers."""

from __future__ import annotations

import json
import os
import hashlib
import urllib.parse
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

from indexer.rso_profile import (
    RSO_DOC_CHAIN_ID,
    encode_publication_locator_uri,
    normalize_node_id,
)
from vendor.docchain.attestation import (
    doc_block_hash_payload,
    doc_block_hash_with_cast,
    normalize_address,
    normalize_bytes32,
    prepare_attestation,
    signed_attestation,
    sign_prepared_with_cast,
    write_json,
)
from vendor.docchain.model import ZERO_ADDRESS

# In-process Keccak-256 so parentHash re-derivation needs no `cast` subprocess.
from attestation.keccak256 import keccak256


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DEFAULT_STATE_PATH = DATA_DIR / "attestations" / "rso-docchain-state.json"
STATE_SCHEMA = "rso-docchain-node-state-v1"
ACCEPTED_STATE_SCHEMAS = frozenset({STATE_SCHEMA})
DEFAULT_BUILD_DIR = ROOT / "build" / "attestations"
OFFICIAL_BASELINE_DATE = os.environ.get("OFFICIAL_BASELINE_DATE", "2026-04-20")
ZERO_BYTES32 = "0x" + "00" * 32


@dataclass(frozen=True)
class PreparedRsoAttestation:
    """Prepared and signed RSO attestation payload for sweeper submission."""

    date: str
    doc_ref: int
    parent_hash: str
    block_hash: str
    content_hash: str
    uri: str
    prepared: dict[str, object]
    signed: dict[str, object]


def snapshot_dir(snapshot_date: str) -> Path:
    parsed = parse_snapshot_date(snapshot_date)
    return DATA_DIR / f"{parsed.year:04d}" / f"{parsed.month:02d}" / f"{parsed.day:02d}"


def manifest_path(snapshot_date: str) -> Path:
    return snapshot_dir(snapshot_date) / "manifest.json"


def storage_receipt_path(snapshot_date: str) -> Path:
    return snapshot_dir(snapshot_date) / "storage.json"


def signed_attestation_path(snapshot_date: str, artifact_id: str | None = None) -> Path:
    if artifact_id:
        return DATA_DIR / "attestations" / "signed" / snapshot_date / f"{artifact_id}.json"
    return DATA_DIR / "attestations" / "signed" / f"{snapshot_date}.json"


def load_manifest(snapshot_date: str) -> dict[str, object]:
    path = manifest_path(snapshot_date)
    if not path.exists():
        raise ValueError(f"{snapshot_date}: missing {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a JSON object")
    if raw.get("date") != snapshot_date:
        raise ValueError(f"{path} date does not match {snapshot_date}")
    return raw


def load_storage_receipt(snapshot_date: str) -> dict[str, object]:
    path = storage_receipt_path(snapshot_date)
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return raw


def doc_ref_for_date(snapshot_date: str) -> int:
    parsed = parse_snapshot_date(snapshot_date)
    return int(parsed.strftime("%Y%m%d000000"))


def parse_snapshot_date(snapshot_date: str) -> date:
    try:
        return datetime.strptime(snapshot_date, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"invalid snapshot date {snapshot_date!r}; expected YYYY-MM-DD") from exc


def previous_date(snapshot_date: str) -> str:
    return (parse_snapshot_date(snapshot_date) - timedelta(days=1)).isoformat()


def date_range(start: str, end: str) -> list[str]:
    start_date = parse_snapshot_date(start)
    end_date = parse_snapshot_date(end)
    if end_date < start_date:
        raise ValueError("--end must be on or after --start")
    days = []
    current = start_date
    while current <= end_date:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def content_hash_from_manifest(manifest: Mapping[str, object]) -> str:
    """Return the attested contentHash for a day manifest.

    v2 manifests carry content_sha256 (the deterministic core projection) next
    to the raw-catalog sha256; the consensus chain attests the core hash. The
    raw hash remains in the manifest for artifact integrity.
    """
    if "content_sha256" in manifest:
        return normalize_bytes32("0x" + str(manifest["content_sha256"]))
    return normalize_bytes32("0x" + str(manifest["sha256"]))


def load_attestation_state(
    path: Path = DEFAULT_STATE_PATH,
    *,
    schema: str | None = STATE_SCHEMA,
) -> dict[str, object]:
    """Load a node attestation state file.

    `schema` pins the expected schema; pass None to accept any known schema
    (used by helpers that must follow whatever chain the file belongs to).
    A missing file yields a fresh state carrying the requested schema.
    """
    if schema is not None and schema not in ACCEPTED_STATE_SCHEMAS:
        raise ValueError(f"unsupported attestation state schema {schema!r}")
    if not path.exists():
        return {"schema": schema or STATE_SCHEMA, "attestations": []}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a JSON object")
    found = raw.get("schema")
    if schema is None:
        if found not in ACCEPTED_STATE_SCHEMAS:
            raise ValueError(f"{path} has unsupported schema")
    elif found != schema:
        raise ValueError(f"{path} has unsupported schema")
    attestations = raw.get("attestations")
    if not isinstance(attestations, list):
        raise ValueError(f"{path} attestations must be an array")
    return raw


def latest_state_attestation(state: Mapping[str, object]) -> dict[str, object] | None:
    raw = state.get("attestations", [])
    if not isinstance(raw, list) or not raw:
        return None
    latest = raw[-1]
    if not isinstance(latest, dict):
        raise ValueError("state attestations must contain objects")
    return latest


def latest_state_attestation_for_date(
    state: Mapping[str, object],
    snapshot_date: str,
    *,
    chain_id: int | None = None,
    contract_address: str | None = None,
) -> dict[str, object] | None:
    raw = state.get("attestations", [])
    if not isinstance(raw, list):
        raise ValueError("state attestations must be an array")
    want_contract = normalize_address(contract_address) if contract_address else None
    for item in reversed(raw):
        if not isinstance(item, dict) or item.get("date") != snapshot_date:
            continue
        # When a submission domain is supplied, only follow an entry from the
        # same chain and contract so testnet and mainnet entries cannot
        # cross-link into one parentHash chain.
        if chain_id is not None and int(item.get("chainId", 0)) != int(chain_id):
            continue
        if want_contract is not None:
            entry_contract = item.get("contractAddress")
            if not entry_contract or normalize_address(str(entry_contract)) != want_contract:
                continue
        return item
    return None


def state_attestation_for_date(
    state: Mapping[str, object],
    snapshot_date: str,
) -> dict[str, object] | None:
    return latest_state_attestation_for_date(state, snapshot_date)


def state_attestation_for_inputs(
    state: Mapping[str, object],
    *,
    snapshot_date: str,
    chain_id: int,
    contract_address: str,
    attester: str,
    on_behalf_of: str,
    parent_hash: str,
    content_hash: str,
    uri: str,
) -> dict[str, object] | None:
    raw = state.get("attestations", [])
    if not isinstance(raw, list):
        raise ValueError("state attestations must be an array")
    expected = {
        "date": snapshot_date,
        "chainId": int(chain_id),
        "contractAddress": normalize_address(contract_address),
        "attester": normalize_address(attester),
        "onBehalfOf": normalize_address(on_behalf_of),
        "parentHash": normalize_bytes32(parent_hash),
        "contentHash": normalize_bytes32(content_hash),
        "uri": uri,
    }
    for item in raw:
        if not isinstance(item, dict):
            continue
        if all(item.get(key) == value for key, value in expected.items()):
            return item
    return None


def recompute_state_entry_block_hash(
    entry: Mapping[str, object],
    *,
    doc_chain_id: str = RSO_DOC_CHAIN_ID,
) -> str:
    """Recompute an entry's blockHash from its OWN recorded DocBlock fields.

    blockHash = keccak256(TYPEHASH || docChainId || uint64(docRef) as 32-byte BE
    || parentHash || contentHash). The state file never stores docChainId (it is
    fixed per profile), so it is supplied from the profile constant.
    """
    doc_block = {
        "docChainId": normalize_bytes32(doc_chain_id),
        "docRef": int(entry["docRef"]),
        "parentHash": normalize_bytes32(str(entry["parentHash"])),
        "contentHash": normalize_bytes32(str(entry["contentHash"])),
    }
    # doc_block_hash_payload returns the hashStruct input as a 0x-prefixed hex
    # string (TYPEHASH || docChainId || docRef || parentHash || contentHash).
    payload_hex = doc_block_hash_payload(doc_block)
    payload = bytes.fromhex(payload_hex[2:] if payload_hex.startswith("0x") else payload_hex)
    return normalize_bytes32("0x" + keccak256(payload).hex())


def verified_state_entry_block_hash(
    entry: Mapping[str, object],
    *,
    doc_chain_id: str = RSO_DOC_CHAIN_ID,
) -> str:
    """Return the entry's blockHash after re-deriving it from its own fields.

    A tampered blockHash/contentHash/parentHash/docRef in the committed state
    file fails closed here (at sign time) rather than silently parenting a new
    day off a forged hash.
    """
    recorded = normalize_bytes32(str(entry["blockHash"]))
    recomputed = recompute_state_entry_block_hash(entry, doc_chain_id=doc_chain_id)
    if recomputed != recorded:
        raise ValueError(
            f"{entry.get('date', '?')}: recorded blockHash {recorded} does not match "
            f"blockHash re-derived from its own DocBlock fields {recomputed}; "
            "the committed attestation state file may be corrupt or tampered"
        )
    return recorded


def conflicting_content_state_attestation(
    state: Mapping[str, object],
    *,
    chain_id: int,
    contract_address: str,
    attester: str,
    doc_ref: int,
    content_hash: str,
) -> dict[str, object] | None:
    """Find an already-signed entry for the same (chain, contract, attester,
    docRef) that recorded a DIFFERENT contentHash.

    Signing a second attestation over the same docRef with a different
    contentHash is on-chain equivocation (one attester, two contentHashes for
    one document ref), so callers must refuse it unless the operator explicitly
    opts into a content change.
    """
    raw = state.get("attestations", [])
    if not isinstance(raw, list):
        raise ValueError("state attestations must be an array")
    want = {
        "chainId": int(chain_id),
        "contractAddress": normalize_address(contract_address),
        "attester": normalize_address(attester),
        "docRef": int(doc_ref),
    }
    want_content = normalize_bytes32(content_hash)
    for item in raw:
        if not isinstance(item, dict):
            continue
        if int(item.get("chainId", 0)) != want["chainId"]:
            continue
        entry_contract = item.get("contractAddress")
        if not entry_contract or normalize_address(str(entry_contract)) != want["contractAddress"]:
            continue
        if normalize_address(str(item.get("attester", ZERO_ADDRESS))) != want["attester"]:
            continue
        if int(item.get("docRef", 0)) != want["docRef"]:
            continue
        if normalize_bytes32(str(item.get("contentHash", ZERO_BYTES32))) != want_content:
            return item
    return None


def parent_hash_for_date(
    snapshot_date: str,
    state: Mapping[str, object],
    *,
    bootstrap_parent_hash: str | None = None,
    baseline_date: str = OFFICIAL_BASELINE_DATE,
    baseline_parent_hash: str | None = None,
    chain_id: int | None = None,
    contract_address: str | None = None,
    doc_chain_id: str = RSO_DOC_CHAIN_ID,
) -> str:
    """Derive the parentHash for a day from the node's attestation state.

    The baseline (genesis) day takes `baseline_parent_hash` when given and the
    zero hash otherwise (a fresh chain).
    """
    if snapshot_date == baseline_date:
        if baseline_parent_hash:
            return normalize_bytes32(baseline_parent_hash)
        return ZERO_BYTES32
    expected_previous = previous_date(snapshot_date)
    previous = latest_state_attestation_for_date(
        state,
        expected_previous,
        chain_id=chain_id,
        contract_address=contract_address,
    )
    if previous is None:
        if bootstrap_parent_hash:
            return normalize_bytes32(bootstrap_parent_hash)
        raise ValueError(
            f"{snapshot_date}: no prior DocChain blockHash in state. "
            "Set RSO_ATTESTATION_BOOTSTRAP_PARENT_HASH for a deliberate bootstrap."
        )
    # The lookup already pins date == previous_date(snapshot_date); guard the
    # entry's own recorded date as well so a mislabeled entry cannot slip in.
    if str(previous.get("date")) != expected_previous:
        raise ValueError(
            f"{snapshot_date}: prior state entry date {previous.get('date')!r} "
            f"is not the expected previous day {expected_previous}"
        )
    return verified_state_entry_block_hash(previous, doc_chain_id=doc_chain_id)


def release_uri(snapshot_date: str, *, mode: str = "auto", node_id: str = "") -> str:
    """Choose the publication URI to include in a node attestation."""
    if mode == "empty":
        return ""
    receipt = load_storage_receipt(snapshot_date)
    locations = publication_locations_from_receipt(receipt, mode=mode)
    if locations:
        bundle_sha256 = receipt.get("bundle_sha256")
        if isinstance(bundle_sha256, str) and bundle_sha256:
            return encode_publication_locator_uri(
                bundle_sha256=bundle_sha256,
                locations=locations,
                node_id=node_id,
            )
        # A location without a bundle fingerprint cannot form a sweeper-verifiable
        # locator. For an explicitly requested destination mode that is a real
        # error; in auto mode fall back to the plain location URI (the sweeper
        # will simply defer an unverifiable claim).
        if mode != "auto" and node_id:
            raise ValueError(
                f"{snapshot_date}: node attestations require a bundle fingerprint"
            )
        return locations[0]
    # No publication destination. In auto mode this is a valid hash-only
    # attestation (uri == ""): the snapshot must not halt just because there is
    # nothing to publish (e.g. STORAGE_BACKEND=none), and the sweeper will not
    # sponsor an unverifiable claim. Only an explicitly requested destination
    # mode is an error here.
    if mode == "auto":
        return ""
    raise ValueError(f"{snapshot_date}: no {mode} destination in storage receipt")


def publication_locations_from_receipt(receipt: Mapping[str, object], *, mode: str = "auto") -> list[str]:
    """Return publication locations from a storage receipt in attestation order."""
    if mode not in ("auto", "arweave", "github_release"):
        raise ValueError(f"unsupported uri mode {mode!r}")
    destinations = receipt.get("destinations", {})
    if not isinstance(destinations, dict):
        destinations = {}
    locations = []
    arweave = destinations.get("arweave")
    github_release = destinations.get("github_release")
    if (
        mode in ("auto", "arweave")
        and isinstance(arweave, dict)
        and destination_matches_bundle(receipt, arweave, required_statuses={"submitted"})
    ):
        tx_id = arweave.get("transaction_id")
        if isinstance(tx_id, str) and tx_id:
            locations.append("ar://" + tx_id)
        else:
            tx_url = arweave.get("transaction_url")
            if isinstance(tx_url, str) and tx_url.startswith("https://arweave.net/"):
                locations.append(tx_url)
    if mode in ("auto", "github_release") and isinstance(github_release, dict):
        asset_url = github_release.get("asset_url")
        if isinstance(asset_url, str) and asset_url:
            locations.append(asset_url)
        else:
            release_url = github_release.get("release_url")
            asset_name = github_release.get("asset_name")
            if isinstance(release_url, str) and isinstance(asset_name, str):
                locations.append(github_release_asset_url(release_url, asset_name))
    return locations


def destination_matches_bundle(
    receipt: Mapping[str, object],
    destination: Mapping[str, object],
    *,
    required_statuses: set[str],
) -> bool:
    status = destination.get("status")
    if not isinstance(status, str) or status not in required_statuses:
        return False
    receipt_bundle_sha = receipt.get("bundle_sha256")
    destination_bundle_sha = destination.get("bundle_sha256")
    if isinstance(receipt_bundle_sha, str) and receipt_bundle_sha:
        return destination_bundle_sha == receipt_bundle_sha
    if isinstance(destination_bundle_sha, str) and destination_bundle_sha:
        return destination_bundle_sha == receipt_bundle_sha
    return True


def github_release_asset_url(release_url: str, asset_name: str) -> str:
    marker = "/releases/tag/"
    if marker in release_url:
        prefix, tag = release_url.rstrip("/").split(marker, 1)
        return (
            prefix
            + "/releases/download/"
            + urllib.parse.quote(tag, safe="")
            + "/"
            + urllib.parse.quote(asset_name, safe="")
        )
    return release_url.rstrip("/") + "/" + urllib.parse.quote(asset_name, safe="")


def build_prepared_attestation(
    *,
    snapshot_date: str,
    chain_id: int,
    contract_address: str,
    attester: str,
    on_behalf_of: str,
    parent_hash: str,
    uri: str,
    deadline: int | None = None,
    ttl: int = 7 * 24 * 60 * 60,
    network: str = "",
    doc_chain_id: str = RSO_DOC_CHAIN_ID,
) -> dict[str, object]:
    manifest = load_manifest(snapshot_date)
    return prepare_attestation(
        chain_id=chain_id,
        contract_address=contract_address,
        attester=attester,
        on_behalf_of=on_behalf_of,
        doc_chain_id=doc_chain_id,
        doc_ref=doc_ref_for_date(snapshot_date),
        parent_hash=parent_hash,
        content_hash=content_hash_from_manifest(manifest),
        uri=uri,
        deadline=deadline,
        ttl=ttl,
        network=network,
    )


def state_entry_from_signed_artifact(
    *,
    snapshot_date: str,
    prepared: Mapping[str, object],
    signed: Mapping[str, object],
    block_hash: str,
) -> dict[str, object]:
    attestation = prepared["attestation"]
    if not isinstance(attestation, Mapping):
        raise ValueError("prepared attestation is malformed")
    doc_block = attestation["docBlock"]
    if not isinstance(doc_block, Mapping):
        raise ValueError("prepared docBlock is malformed")
    artifact_id = signed_attestation_artifact_id(
        chain_id=int(prepared["chainId"]),
        contract_address=str(prepared["contractAddress"]),
        attester=str(attestation["attester"]),
        on_behalf_of=str(attestation.get("onBehalfOf", ZERO_ADDRESS)),
        parent_hash=str(doc_block["parentHash"]),
        content_hash=str(doc_block["contentHash"]),
        uri=str(attestation.get("uri", "")),
    )
    return {
        "date": snapshot_date,
        "artifactId": artifact_id,
        "chainId": int(prepared["chainId"]),
        "contractAddress": normalize_address(str(prepared["contractAddress"])),
        "docRef": int(doc_block["docRef"]),
        "attester": normalize_address(str(attestation["attester"])),
        "onBehalfOf": normalize_address(str(attestation.get("onBehalfOf", ZERO_ADDRESS))),
        "parentHash": normalize_bytes32(str(doc_block["parentHash"])),
        "blockHash": normalize_bytes32(block_hash),
        "contentHash": normalize_bytes32(str(doc_block["contentHash"])),
        "uri": str(attestation.get("uri", "")),
        "signedPath": display_path(signed_attestation_path(snapshot_date, artifact_id)),
        "latestSignedPath": display_path(signed_attestation_path(snapshot_date)),
        "signature": str(signed["signature"]),
        "submissionStatus": "signed",
        "updatedAt": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
    }


def signed_attestation_artifact_id(
    *,
    chain_id: int,
    contract_address: str,
    attester: str,
    on_behalf_of: str,
    parent_hash: str,
    content_hash: str,
    uri: str,
) -> str:
    payload = {
        "chainId": int(chain_id),
        "contractAddress": normalize_address(contract_address),
        "attester": normalize_address(attester),
        "onBehalfOf": normalize_address(on_behalf_of),
        "parentHash": normalize_bytes32(parent_hash),
        "contentHash": normalize_bytes32(content_hash),
        "uri": uri,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def state_entry_key(
    entry: Mapping[str, object],
) -> tuple[str, int, str, str, str, str, str, str]:
    return (
        str(entry.get("date", "")),
        int(entry.get("chainId", 0)),
        normalize_address(str(entry.get("contractAddress", ZERO_ADDRESS))),
        normalize_address(str(entry.get("attester", ZERO_ADDRESS))),
        normalize_address(str(entry.get("onBehalfOf", ZERO_ADDRESS))),
        normalize_bytes32(str(entry.get("parentHash", ZERO_BYTES32))),
        normalize_bytes32(str(entry.get("contentHash", ZERO_BYTES32))),
        str(entry.get("uri", "")),
    )


def record_state_entry(
    path: Path,
    entry: Mapping[str, object],
    *,
    schema: str = STATE_SCHEMA,
) -> dict[str, object]:
    state = load_attestation_state(path, schema=schema)
    attestations = state["attestations"]
    assert isinstance(attestations, list)
    entry_key = state_entry_key(entry)
    retained = [
        item
        for item in attestations
        if isinstance(item, dict) and state_entry_key(item) != entry_key
    ]
    retained.append(dict(entry))
    retained.sort(
        key=lambda item: (
            str(item["date"]),
            str(item.get("updatedAt", "")),
            str(item.get("artifactId", "")),
        )
    )
    state["attestations"] = retained
    state["latest"] = retained[-1] if retained else None
    state["updatedAt"] = entry["updatedAt"]
    write_json(path, state)
    return state


def signed_artifact(
    *,
    snapshot_date: str,
    prepared: Mapping[str, object],
    signed: Mapping[str, object],
    block_hash: str,
    node_id: str = "",
    repository: str = "",
    workflow_run_id: str = "",
) -> dict[str, object]:
    attestation = prepared["attestation"]
    if not isinstance(attestation, Mapping):
        raise ValueError("prepared attestation is malformed")
    doc_block = attestation["docBlock"]
    if not isinstance(doc_block, Mapping):
        raise ValueError("prepared docBlock is malformed")
    return {
        "schema": "rso-signed-attestation-v1",
        "date": snapshot_date,
        "docRef": int(doc_block["docRef"]),
        "blockHash": normalize_bytes32(block_hash),
        "signed": dict(signed),
        "node": {
            "nodeId": normalize_node_id(node_id) if node_id else "",
            "attester": normalize_address(attestation["attester"]),
            "repository": repository,
            "workflowRunId": workflow_run_id,
        },
        "publishedForSweeperAt": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
    }


def write_signed_artifact(path: Path, artifact: Mapping[str, object]) -> None:
    write_json(path, dict(artifact))


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def prepare_sign_one(
    *,
    snapshot_date: str,
    state: Mapping[str, object],
    chain_id: int,
    contract_address: str,
    attester: str,
    on_behalf_of: str = ZERO_ADDRESS,
    private_key_env: str = "DISPOSABLE_NO_FUNDS_ETH_PRIVATE_KEY",
    uri_mode: str = "auto",
    bootstrap_parent_hash: str | None = None,
    ttl: int = 7 * 24 * 60 * 60,
    network: str = "",
    node_id: str = "",
    repository: str = "",
    workflow_run_id: str = "",
    cast: str | None = None,
    parent_hash: str | None = None,
    uri: str | None = None,
    doc_chain_id: str = RSO_DOC_CHAIN_ID,
    baseline_parent_hash: str | None = None,
) -> tuple[PreparedRsoAttestation, dict[str, object]]:
    if parent_hash is None:
        parent_hash = parent_hash_for_date(
            snapshot_date,
            state,
            bootstrap_parent_hash=bootstrap_parent_hash,
            baseline_parent_hash=baseline_parent_hash,
            chain_id=chain_id,
            contract_address=contract_address,
            doc_chain_id=doc_chain_id,
        )
    else:
        parent_hash = normalize_bytes32(parent_hash)
    if uri is None:
        uri = release_uri(snapshot_date, mode=uri_mode, node_id=node_id)
    prepared = build_prepared_attestation(
        snapshot_date=snapshot_date,
        chain_id=chain_id,
        contract_address=contract_address,
        attester=attester,
        on_behalf_of=on_behalf_of,
        parent_hash=parent_hash,
        uri=uri,
        ttl=ttl,
        network=network,
        doc_chain_id=doc_chain_id,
    )
    signature = sign_prepared_with_cast(prepared, private_key_env=private_key_env, cast=cast)
    signed = signed_attestation(prepared, signature)
    attestation = prepared["attestation"]
    assert isinstance(attestation, Mapping)
    doc_block = attestation["docBlock"]
    assert isinstance(doc_block, Mapping)
    block_hash = doc_block_hash_with_cast(doc_block, cast=cast)
    artifact = signed_artifact(
        snapshot_date=snapshot_date,
        prepared=prepared,
        signed=signed,
        block_hash=block_hash,
        node_id=node_id,
        repository=repository,
        workflow_run_id=workflow_run_id,
    )
    return (
        PreparedRsoAttestation(
            date=snapshot_date,
            doc_ref=int(doc_block["docRef"]),
            parent_hash=parent_hash,
            block_hash=block_hash,
            content_hash=str(doc_block["contentHash"]),
            uri=uri,
            prepared=prepared,
            signed=signed,
        ),
        artifact,
    )
