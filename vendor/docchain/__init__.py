"""Stdlib-only reference helpers for Doc Chain indexers."""

from .indexer import BlockRangeLimitError, EthereumRpc, RateLimitError, RpcError
from .logs import decode_doc_attested_log
from .model import DocAttestation, DocAttested, DocBlock, normalize_doc_attested
from .store import (
    ScanContext,
    ScanResult,
    append_event_cache,
    build_docchain_index,
    checkpoint_from_block,
    load_event_cache,
    update_event_cache,
    write_checkpoint,
)

__all__ = [
    "DocBlock",
    "DocAttestation",
    "DocAttested",
    "BlockRangeLimitError",
    "EthereumRpc",
    "RateLimitError",
    "RpcError",
    "ScanContext",
    "ScanResult",
    "append_event_cache",
    "build_docchain_index",
    "checkpoint_from_block",
    "decode_doc_attested_log",
    "load_event_cache",
    "normalize_doc_attested",
    "update_event_cache",
    "write_checkpoint",
]
