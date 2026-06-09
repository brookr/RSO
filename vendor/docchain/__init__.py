"""Stdlib-only reference helpers for Doc Chain indexers and attestations."""

from .attestation import (
    attest_batch_calldata,
    attest_doc_calldata,
    cast_wallet_args_from_env,
    doc_block_hash_payload,
    doc_block_hash_with_cast,
    has_cast_wallet_config,
    prepare_attestation,
    signed_attestation,
    sign_prepared_with_cast,
    typed_data_for_attestation,
)
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
    "attest_batch_calldata",
    "attest_doc_calldata",
    "build_docchain_index",
    "cast_wallet_args_from_env",
    "checkpoint_from_block",
    "decode_doc_attested_log",
    "doc_block_hash_payload",
    "doc_block_hash_with_cast",
    "has_cast_wallet_config",
    "load_event_cache",
    "normalize_doc_attested",
    "prepare_attestation",
    "signed_attestation",
    "sign_prepared_with_cast",
    "typed_data_for_attestation",
    "update_event_cache",
    "write_checkpoint",
]
