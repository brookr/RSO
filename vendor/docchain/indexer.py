"""Dependency-free JSON-RPC helpers for generic Doc Chain event indexing."""

from __future__ import annotations

import json
import random
import re
import time
import urllib.error
import urllib.request
from collections.abc import Iterator

from .abi import DOC_ATTESTED_EVENT_TOPIC0S
from .logs import decode_doc_attested_log
from .model import DocAttested


class RpcError(RuntimeError):
    """Raised when an Ethereum JSON-RPC request fails."""


class RateLimitError(RpcError):
    """Raised after exhausting JSON-RPC rate-limit retries."""


class BlockRangeLimitError(RpcError):
    """Raised when a provider rejects an eth_getLogs block range."""

    def __init__(self, message: str, max_block_range: int | None = None) -> None:
        super().__init__(message)
        self.max_block_range = max_block_range


RPC_MAX_RETRIES = 6
RPC_RETRY_BASE_DELAY_SECONDS = 0.75
RPC_RETRY_MAX_DELAY_SECONDS = 30.0
GET_LOGS_REQUEST_DELAY_SECONDS = 0.2
RPC_MAX_RESPONSE_BYTES = 10 * 1024 * 1024


class EthereumRpc:
    """Small JSON-RPC client for the methods needed by a log indexer."""

    def __init__(self, rpc_url: str, timeout: float = 30.0) -> None:
        self.rpc_url = rpc_url
        self.timeout = timeout
        self._next_id = 1
        self._last_get_logs_at = 0.0

    def call(self, method: str, params: list[object]) -> object:
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": method,
            "params": params,
        }
        self._next_id += 1
        request_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        for attempt in range(RPC_MAX_RETRIES + 1):
            request = urllib.request.Request(
                self.rpc_url,
                data=request_body,
                headers={"content-type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    body = json.loads(_read_limited(response, RPC_MAX_RESPONSE_BYTES).decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = _read_limited(exc, RPC_MAX_RESPONSE_BYTES).decode("utf-8", errors="replace")
                if exc.code == 429:
                    if attempt < RPC_MAX_RETRIES:
                        _sleep_for_retry(attempt, _retry_after_seconds(exc))
                        continue
                    raise RateLimitError(
                        f"{method} request failed after retries: HTTP {exc.code}: {detail}"
                    ) from exc
                block_limit = _block_range_limit_from_http_detail(method, detail)
                if block_limit is not None:
                    raise BlockRangeLimitError(
                        f"{method} request failed: HTTP {exc.code}: {detail}",
                        max_block_range=block_limit,
                    ) from exc
                raise RpcError(f"{method} request failed: HTTP {exc.code}: {detail}") from exc
            except urllib.error.URLError as exc:
                if attempt < RPC_MAX_RETRIES:
                    _sleep_for_retry(attempt, None)
                    continue
                raise RpcError(f"{method} request failed after retries: {exc}") from exc
            if "error" in body:
                error = body["error"]
                if _is_rate_limit_error(error):
                    if attempt < RPC_MAX_RETRIES:
                        _sleep_for_retry(attempt, None)
                        continue
                    raise RateLimitError(f"{method} returned rate-limit error after retries: {error}")
                block_limit = _block_range_limit_from_error(method, error)
                if block_limit is not None:
                    raise BlockRangeLimitError(
                        f"{method} returned block-range limit error: {error}",
                        max_block_range=block_limit,
                    )
                raise RpcError(f"{method} returned error: {error}")
            return body["result"]
        raise RpcError(f"{method} request failed")

    def block_number(self) -> int:
        result = self.call("eth_blockNumber", [])
        if not isinstance(result, str):
            raise RpcError("eth_blockNumber returned a non-string result")
        return int(result, 16)

    def get_logs(
        self,
        address: str,
        from_block: int,
        to_block: int,
        topics: list[str | list[str] | None],
    ) -> list[object]:
        self._throttle_get_logs()
        result = self.call(
            "eth_getLogs",
            [
                {
                    "address": normalize_address(address),
                    "fromBlock": hex(from_block),
                    "toBlock": hex(to_block),
                    "topics": topics,
                }
            ],
        )
        if not isinstance(result, list):
            raise RpcError("eth_getLogs returned a non-list result")
        return result

    def _throttle_get_logs(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_get_logs_at
        if self._last_get_logs_at > 0 and elapsed < GET_LOGS_REQUEST_DELAY_SECONDS:
            time.sleep(GET_LOGS_REQUEST_DELAY_SECONDS - elapsed)
        self._last_get_logs_at = time.monotonic()


def iter_doc_attested_chunks(
    rpc: EthereumRpc,
    address: str,
    from_block: int,
    to_block: int,
    *,
    chunk_size: int = 2_000,
    doc_chain_id: str | None = None,
    attester: str | None = None,
    doc_ref: int | None = None,
) -> Iterator[tuple[int, int, list[DocAttested]]]:
    """Yield decoded `DocAttested` events in inclusive block-range chunks."""
    topics = doc_attested_topics(
        doc_chain_id=doc_chain_id,
        attester=attester,
        doc_ref=doc_ref,
    )
    for start, end in block_ranges(from_block, to_block, chunk_size):
        yield from _iter_doc_attested_range(rpc, address, start, end, topics)


def block_ranges(from_block: int, to_block: int, chunk_size: int) -> Iterator[tuple[int, int]]:
    """Split an inclusive block range into provider-friendly chunks."""
    if from_block < 0 or to_block < 0:
        raise ValueError("block numbers must not be negative")
    if chunk_size < 1:
        raise ValueError("chunk_size must be at least 1")
    start = from_block
    while start <= to_block:
        end = min(start + chunk_size - 1, to_block)
        yield start, end
        start = end + 1


def _iter_doc_attested_range(
    rpc: EthereumRpc,
    address: str,
    from_block: int,
    to_block: int,
    topics: list[str | list[str] | None],
) -> Iterator[tuple[int, int, list[DocAttested]]]:
    try:
        logs = rpc.get_logs(address, from_block, to_block, topics)
    except RateLimitError:
        raise
    except BlockRangeLimitError as exc:
        if exc.max_block_range is None or from_block >= to_block:
            raise
        for start, end in block_ranges(from_block, to_block, exc.max_block_range):
            yield from _iter_doc_attested_range(rpc, address, start, end, topics)
        return
    except RpcError:
        if from_block >= to_block:
            raise
        mid_block = (from_block + to_block) // 2
        yield from _iter_doc_attested_range(rpc, address, from_block, mid_block, topics)
        yield from _iter_doc_attested_range(rpc, address, mid_block + 1, to_block, topics)
        return
    yield from_block, to_block, [decode_doc_attested_log(log) for log in logs]


def doc_attested_topics(
    *,
    doc_chain_id: str | None = None,
    attester: str | None = None,
    doc_ref: int | None = None,
) -> list[str | list[str] | None]:
    """Build an `eth_getLogs` topic filter for `DocAttested`."""
    return [
        list(DOC_ATTESTED_EVENT_TOPIC0S),
        normalize_bytes32(doc_chain_id) if doc_chain_id is not None else None,
        topic_address(attester) if attester is not None else None,
        topic_uint64(doc_ref) if doc_ref is not None else None,
    ]


def normalize_address(address: str) -> str:
    body = _hex_body(address, "address")
    if len(body) != 40:
        raise ValueError("address must be 20 bytes")
    return "0x" + body


def normalize_bytes32(value: str) -> str:
    body = _hex_body(value, "bytes32")
    if len(body) != 64:
        raise ValueError("bytes32 value must be 32 bytes")
    return "0x" + body


def topic_address(address: str) -> str:
    return "0x" + ("0" * 24) + normalize_address(address)[2:]


def topic_uint64(value: int) -> str:
    if value < 0 or value > 2**64 - 1:
        raise ValueError("doc_ref must fit uint64")
    return "0x" + format(value, "064x")


def _hex_body(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.lower().startswith("0x"):
        raise ValueError(f"{field} must be a 0x-prefixed hex string")
    body = value[2:].lower()
    try:
        int(body or "0", 16)
    except ValueError as exc:
        raise ValueError(f"{field} contains non-hex characters") from exc
    return body


def _is_rate_limit_error(error: object) -> bool:
    if isinstance(error, dict):
        code = error.get("code")
        message = str(error.get("message", ""))
        return code == 429 or _mentions_rate_limit(message)
    return _mentions_rate_limit(str(error))


def _block_range_limit_from_http_detail(method: str, detail: str) -> int | None:
    if method != "eth_getLogs":
        return None
    try:
        raw = json.loads(detail)
    except json.JSONDecodeError:
        return _block_range_limit_from_message(detail)
    if isinstance(raw, dict) and "error" in raw:
        return _block_range_limit_from_error(method, raw["error"])
    return _block_range_limit_from_message(detail)


def _block_range_limit_from_error(method: str, error: object) -> int | None:
    if method != "eth_getLogs":
        return None
    if isinstance(error, dict):
        message = str(error.get("message", ""))
    else:
        message = str(error)
    return _block_range_limit_from_message(message)


def _block_range_limit_from_message(message: str) -> int | None:
    normalized = message.lower()
    if "block range" not in normalized:
        return None
    match = re.search(r"up to a? ([0-9][0-9_,]*) block range", normalized)
    if match is None:
        return None
    limit = int(match.group(1).replace("_", "").replace(",", ""))
    if limit < 1:
        return None
    return limit


def _mentions_rate_limit(message: str) -> bool:
    normalized = message.lower()
    return (
        "rate limit" in normalized
        or "rate-limit" in normalized
        or "too many requests" in normalized
        or "compute units per second" in normalized
        or "throughput" in normalized
    )


def _retry_after_seconds(exc: urllib.error.HTTPError) -> float | None:
    value = exc.headers.get("Retry-After")
    if value is None:
        return None
    try:
        delay = float(value)
    except ValueError:
        return None
    if delay < 0:
        return None
    return delay


def _read_limited(stream, limit: int) -> bytes:
    if limit < 1:
        raise RpcError("JSON-RPC response size limit must be positive")
    chunks = []
    total = 0
    while True:
        chunk = stream.read(min(1024 * 1024, limit - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise RpcError("JSON-RPC response exceeds size limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _sleep_for_retry(attempt: int, retry_after: float | None) -> None:
    if retry_after is not None:
        time.sleep(min(retry_after, RPC_RETRY_MAX_DELAY_SECONDS))
        return
    cap = min(
        RPC_RETRY_BASE_DELAY_SECONDS * (2**attempt),
        RPC_RETRY_MAX_DELAY_SECONDS,
    )
    time.sleep(cap + random.uniform(0, cap / 4))
