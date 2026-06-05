"""RSO-specific Doc Chain attestation preparation and node state helpers."""

from __future__ import annotations

import json
import os
import urllib.parse
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

from indexer.rso_profile import RSO_DOC_CHAIN_ID, encode_publication_locator_uri
from vendor.docchain.attestation import (
    doc_block_hash_with_cast,
    normalize_address,
    normalize_bytes32,
    prepare_attestation,
    signed_attestation,
    sign_prepared_with_cast,
    write_json,
)
from vendor.docchain.model import ZERO_ADDRESS


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DEFAULT_STATE_PATH = DATA_DIR / "attestations" / "rso-docchain-state.json"
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


def signed_attestation_path(snapshot_date: str) -> Path:
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
    return normalize_bytes32("0x" + str(manifest["sha256"]))


def load_attestation_state(path: Path = DEFAULT_STATE_PATH) -> dict[str, object]:
    if not path.exists():
        return {"schema": "rso-docchain-node-state-v1", "attestations": []}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a JSON object")
    if raw.get("schema") != "rso-docchain-node-state-v1":
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


def state_attestation_for_date(
    state: Mapping[str, object],
    snapshot_date: str,
) -> dict[str, object] | None:
    raw = state.get("attestations", [])
    if not isinstance(raw, list):
        raise ValueError("state attestations must be an array")
    for item in raw:
        if isinstance(item, dict) and item.get("date") == snapshot_date:
            return item
    return None


def parent_hash_for_date(
    snapshot_date: str,
    state: Mapping[str, object],
    *,
    bootstrap_parent_hash: str | None = None,
    baseline_date: str = OFFICIAL_BASELINE_DATE,
) -> str:
    if snapshot_date == baseline_date:
        return ZERO_BYTES32
    latest = latest_state_attestation(state)
    if latest is None:
        if bootstrap_parent_hash:
            return normalize_bytes32(bootstrap_parent_hash)
        raise ValueError(
            f"{snapshot_date}: no prior DocChain blockHash in state. "
            "Set RSO_ATTESTATION_BOOTSTRAP_PARENT_HASH for a deliberate bootstrap."
        )
    expected_previous = previous_date(snapshot_date)
    if latest.get("date") != expected_previous:
        raise ValueError(
            f"{snapshot_date}: latest attested date is {latest.get('date')}, "
            f"expected {expected_previous}"
        )
    return normalize_bytes32(str(latest["blockHash"]))


def release_uri(snapshot_date: str, *, mode: str = "auto") -> str:
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
            )
        return locations[0]
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
    if mode in ("auto", "arweave") and isinstance(arweave, dict):
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
    ttl: int = 86_400,
    network: str = "",
) -> dict[str, object]:
    manifest = load_manifest(snapshot_date)
    return prepare_attestation(
        chain_id=chain_id,
        contract_address=contract_address,
        attester=attester,
        on_behalf_of=on_behalf_of,
        doc_chain_id=RSO_DOC_CHAIN_ID,
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
    return {
        "date": snapshot_date,
        "docRef": int(doc_block["docRef"]),
        "attester": normalize_address(str(attestation["attester"])),
        "onBehalfOf": normalize_address(str(attestation.get("onBehalfOf", ZERO_ADDRESS))),
        "parentHash": normalize_bytes32(str(doc_block["parentHash"])),
        "blockHash": normalize_bytes32(block_hash),
        "contentHash": normalize_bytes32(str(doc_block["contentHash"])),
        "uri": str(attestation.get("uri", "")),
        "signedPath": display_path(signed_attestation_path(snapshot_date)),
        "signature": str(signed["signature"]),
        "submissionStatus": "signed",
        "updatedAt": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
    }


def record_state_entry(path: Path, entry: Mapping[str, object]) -> dict[str, object]:
    state = load_attestation_state(path)
    attestations = state["attestations"]
    assert isinstance(attestations, list)
    entry_date = str(entry["date"])
    retained = [item for item in attestations if isinstance(item, dict) and item.get("date") != entry_date]
    retained.append(dict(entry))
    retained.sort(key=lambda item: str(item["date"]))
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
    ttl: int = 86_400,
    network: str = "",
    repository: str = "",
    workflow_run_id: str = "",
    cast: str | None = None,
) -> tuple[PreparedRsoAttestation, dict[str, object]]:
    parent_hash = parent_hash_for_date(
        snapshot_date,
        state,
        bootstrap_parent_hash=bootstrap_parent_hash,
    )
    uri = release_uri(snapshot_date, mode=uri_mode)
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
    )
    signature = sign_prepared_with_cast(prepared, private_key_env=private_key_env)
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
