"""Generic Doc Chain event model helpers.

This module intentionally contains no project-specific scoring or validation.
Individual Doc Chain indexers and profilers should compose these neutral records with their own rules.
"""

from dataclasses import dataclass
from typing import Mapping

ZERO_ADDRESS = "0x" + "00" * 20


def _field(raw: Mapping[str, object], snake_name: str, camel_name: str) -> object:
    if snake_name in raw:
        return raw[snake_name]
    return raw[camel_name]


def _optional_field(raw: Mapping[str, object], snake_name: str, camel_name: str) -> object:
    if snake_name in raw:
        return raw[snake_name]
    return raw.get(camel_name, "")


@dataclass(frozen=True)
class DocBlock:
    doc_chain_id: str
    doc_ref: int
    parent_hash: str
    content_hash: str


@dataclass(frozen=True)
class DocAttestation:
    attester: str
    on_behalf_of: str
    doc_block: DocBlock
    uri: str
    deadline: int


@dataclass(frozen=True)
class DocAttested:
    doc_chain_id: str
    attester: str
    doc_ref: int
    on_behalf_of: str
    submitter: str
    parent_hash: str
    block_hash: str
    content_hash: str
    uri_hash: str
    uri: str
    block_number: int
    transaction_hash: str
    log_index: int
    ethereum_block_hash: str = ""


def normalize_doc_attested(raw: Mapping[str, object]) -> DocAttested:
    """Build a neutral event record from an already-decoded log mapping."""
    return DocAttested(
        doc_chain_id=str(_field(raw, "doc_chain_id", "docChainId")),
        attester=str(raw["attester"]),
        doc_ref=int(_field(raw, "doc_ref", "docRef")),
        on_behalf_of=str(_optional_field(raw, "on_behalf_of", "onBehalfOf") or ZERO_ADDRESS),
        submitter=str(raw["submitter"]),
        parent_hash=str(_field(raw, "parent_hash", "parentHash")),
        block_hash=str(_field(raw, "block_hash", "blockHash")),
        content_hash=str(_field(raw, "content_hash", "contentHash")),
        uri_hash=str(_field(raw, "uri_hash", "uriHash")),
        uri=str(raw.get("uri", "")),
        block_number=int(_field(raw, "block_number", "blockNumber")),
        transaction_hash=str(_field(raw, "transaction_hash", "transactionHash")),
        log_index=int(_field(raw, "log_index", "logIndex")),
        ethereum_block_hash=str(
            _optional_field(raw, "ethereum_block_hash", "ethereumBlockHash")
        ),
    )
