"""Reusable storage helpers for dependency-free Doc Chain indexers."""

from __future__ import annotations

import json
import os
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .indexer import EthereumRpc, iter_doc_attested_chunks
from .model import DocAttested, normalize_doc_attested


ProgressCallback = Callable[[int, int, int, int], None]


@dataclass(frozen=True)
class ScanContext:
    """Stable context used to validate checkpoints and event caches."""

    address: str
    doc_chain_id: str = ""
    attester: str = ""
    doc_ref: int | None = None
    chain_id: int | None = None
    network: str = ""


@dataclass(frozen=True)
class ScanResult:
    """Summary of a cache update scan."""

    from_block: int
    to_block: int
    last_scanned_block: int
    chunk_count: int
    matched_event_count: int
    new_event_count: int


def update_event_cache(
    *,
    rpc: EthereumRpc,
    address: str,
    cache_path: str | Path,
    from_block: int,
    to_block: int,
    checkpoint_path: str | Path | None = None,
    chunk_size: int = 2_000,
    doc_chain_id: str | None = None,
    attester: str | None = None,
    doc_ref: int | None = None,
    chain_id: int | None = None,
    network: str = "",
    progress: ProgressCallback | None = None,
) -> ScanResult:
    """Scan DocAttested logs, append new events, then checkpoint each chunk.

    The checkpoint is written only after the chunk's matching events are safely
    appended to the raw cache. Failed runs can therefore resume without losing
    discovered logs or corrupting the generated static index.
    """
    context = ScanContext(
        address=address,
        doc_chain_id=doc_chain_id or "",
        attester=attester or "",
        doc_ref=doc_ref,
        chain_id=chain_id,
        network=network,
    )
    start_block = from_block
    if checkpoint_path is not None:
        checkpoint_block = checkpoint_from_block(checkpoint_path, context)
        if checkpoint_block is not None:
            start_block = max(start_block, checkpoint_block)

    known_keys = event_keys_from_cache(cache_path)
    chunk_count = 0
    matched_event_count = 0
    new_event_count = 0
    last_scanned_block = start_block - 1

    if to_block < start_block:
        return ScanResult(
            from_block=start_block,
            to_block=to_block,
            last_scanned_block=last_scanned_block,
            chunk_count=0,
            matched_event_count=0,
            new_event_count=0,
        )

    for chunk_start, chunk_end, events in iter_doc_attested_chunks(
        rpc,
        address,
        start_block,
        to_block,
        chunk_size=chunk_size,
        doc_chain_id=doc_chain_id,
        attester=attester,
        doc_ref=doc_ref,
    ):
        chunk_count += 1
        matched_event_count += len(events)
        new_event_count += append_event_cache(cache_path, events, known_keys=known_keys)
        last_scanned_block = chunk_end
        if checkpoint_path is not None:
            write_checkpoint(checkpoint_path, context, chunk_end)
        if progress is not None:
            progress(chunk_start, chunk_end, len(events), new_event_count)

    return ScanResult(
        from_block=start_block,
        to_block=to_block,
        last_scanned_block=last_scanned_block,
        chunk_count=chunk_count,
        matched_event_count=matched_event_count,
        new_event_count=new_event_count,
    )


def checkpoint_from_block(path: str | Path, context: ScanContext) -> int | None:
    """Return the next block to scan from a checkpoint, if one exists."""
    checkpoint_path = Path(path)
    if not checkpoint_path.exists():
        return None
    raw = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("checkpoint must be a JSON object")
    validate_checkpoint(raw, context)
    if "last_block" not in raw:
        return None
    return int(raw["last_block"]) + 1


def validate_checkpoint(raw: Mapping[str, object], context: ScanContext) -> None:
    """Raise if a checkpoint belongs to a different scan context."""
    expected = scan_context_record(context)
    actual = {
        "address": str(raw.get("address", "")).lower(),
        "doc_chain_id": str(raw.get("doc_chain_id", "")).lower(),
        "attester": str(raw.get("attester", "")).lower(),
        "doc_ref": raw.get("doc_ref"),
        "chain_id": raw.get("chain_id"),
        "network": str(raw.get("network", "")),
    }
    for field, expected_value in expected.items():
        if actual[field] != expected_value:
            raise ValueError(f"checkpoint {field} does not match current scan")


def write_checkpoint(path: str | Path, context: ScanContext, last_block: int) -> None:
    """Atomically write the latest completed scan block."""
    checkpoint = {
        **scan_context_record(context),
        "last_block": last_block,
        "updated_at": utc_now(),
    }
    write_json_file(Path(path), checkpoint)


def scan_context_record(context: ScanContext) -> dict[str, object]:
    return {
        "address": context.address.lower(),
        "doc_chain_id": context.doc_chain_id.lower(),
        "attester": context.attester.lower(),
        "doc_ref": context.doc_ref,
        "chain_id": context.chain_id,
        "network": context.network,
    }


def append_event_cache(
    path: str | Path,
    events: Iterable[DocAttested],
    *,
    known_keys: set[str] | None = None,
) -> int:
    """Append new events to a JSONL cache and return the number written."""
    cache_path = Path(path)
    if known_keys is None:
        known_keys = event_keys_from_cache(cache_path)

    new_events = []
    for event in sort_events(events):
        key = event_key(event)
        if key in known_keys:
            continue
        known_keys.add(key)
        new_events.append(event)

    if not new_events:
        return 0

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open("a", encoding="utf-8") as stream:
        for event in new_events:
            stream.write(json.dumps(event_cache_record(event), sort_keys=True, separators=(",", ":")))
            stream.write("\n")
    return len(new_events)


def event_keys_from_cache(path: str | Path) -> set[str]:
    return {event_key(event) for event in load_event_cache(path)}


def load_event_cache(path: str | Path) -> list[DocAttested]:
    """Load JSONL cached events, returning an empty list if the cache is absent."""
    cache_path = Path(path)
    if not cache_path.exists():
        return []
    events = []
    with cache_path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"cache line {line_number} is invalid JSON: {exc.msg}") from exc
            if not isinstance(raw, dict):
                raise ValueError(f"cache line {line_number} must be a JSON object")
            try:
                events.append(normalize_doc_attested(raw))
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"cache line {line_number} is invalid: {exc}") from exc
    return dedupe_events(events)


def dedupe_events(events: Iterable[DocAttested]) -> list[DocAttested]:
    """Return events sorted by chain order with duplicate tx/log entries removed."""
    seen: set[str] = set()
    deduped = []
    for event in sort_events(events):
        key = event_key(event)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(event)
    return deduped


def sort_events(events: Iterable[DocAttested]) -> list[DocAttested]:
    return sorted(
        events,
        key=lambda event: (
            event.block_number,
            event.log_index,
            event.transaction_hash.lower(),
        ),
    )


def event_key(event: DocAttested) -> str:
    return f"{event.transaction_hash.lower()}:{event.log_index}"


def event_cache_record(event: DocAttested) -> dict[str, object]:
    return asdict(event)


def build_docchain_index(
    *,
    events: Iterable[DocAttested],
    network: str = "",
    chain_id: int | None = None,
    contract_address: str = "",
    doc_chain_id: str = "",
    profile_uri: str = "",
    from_block: int | None = None,
    to_block: int | None = None,
    latest_chain_block: int | None = None,
    confirmations: int = 0,
    indexed_at: str | None = None,
) -> dict[str, object]:
    """Build a deterministic generic static index from cached events."""
    records = [event_index_record(event) for event in dedupe_events(events)]
    records.sort(
        key=lambda record: (
            int(record["docRef"]),
            int(record["ethereumBlock"]),
            int(record["logIndex"]),
            str(record["transactionHash"]),
        )
    )

    by_ref: dict[str, list[dict[str, object]]] = defaultdict(list)
    for record in records:
        by_ref[str(record["docRef"])].append(record)

    doc_refs = {
        doc_ref: {
            "candidateCount": len(group),
            "blockHashes": sorted({str(record["blockHash"]) for record in group}),
            "contentHashes": sorted({str(record["contentHash"]) for record in group}),
            "events": group,
        }
        for doc_ref, group in sorted(by_ref.items())
    }

    return {
        "schema": "docchain-index-v1",
        "network": network,
        "chainId": chain_id,
        "contractAddress": contract_address,
        "docChainId": doc_chain_id,
        "profileURI": profile_uri,
        "fromBlock": from_block,
        "toBlock": to_block,
        "latestChainBlock": latest_chain_block,
        "confirmations": confirmations,
        "indexedAt": indexed_at or utc_now(),
        "eventCount": len(records),
        "docRefCount": len(doc_refs),
        "docRefs": doc_refs,
        "events": records,
    }


def event_index_record(event: DocAttested) -> dict[str, object]:
    return {
        "docChainId": event.doc_chain_id,
        "docRef": event.doc_ref,
        "attester": event.attester,
        "onBehalfOf": event.on_behalf_of,
        "submitter": event.submitter,
        "parentHash": event.parent_hash,
        "blockHash": event.block_hash,
        "contentHash": event.content_hash,
        "uriHash": event.uri_hash,
        "uri": event.uri,
        "ethereumBlock": event.block_number,
        "ethereumBlockHash": event.ethereum_block_hash,
        "transactionHash": event.transaction_hash,
        "logIndex": event.log_index,
    }


def write_json_file(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp_path, path)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
