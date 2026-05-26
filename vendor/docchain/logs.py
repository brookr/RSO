"""Raw Ethereum log decoding helpers for Doc Chain events.

The helpers here decode the single `DocAttested` event without web3.py or
eth-abi. They intentionally stay narrow: generic indexers can fetch raw logs
with JSON-RPC, pass them through this decoder, and apply profile rules later.
"""

from collections.abc import Mapping, Sequence

from .abi import DOC_ATTESTED_EVENT_TOPIC0
from .model import DocAttested


def decode_doc_attested_log(log: Mapping[str, object]) -> DocAttested:
    """Decode a raw JSON-RPC log object into a neutral `DocAttested` record."""
    topics = _topics(log)
    if _word(topics[0], "topics[0]") != _word(DOC_ATTESTED_EVENT_TOPIC0, "topic0"):
        raise ValueError("log is not a DocAttested event")
    if len(topics) != 4:
        raise ValueError(f"DocAttested log requires 4 topics, got {len(topics)}")

    doc_chain_id = _bytes32(topics[1], "docChainId")
    attester = _address_from_word(_word(topics[2], "attester"), "attester")
    doc_ref = _uint64_from_word(_word(topics[3], "docRef"), "docRef")

    data = _data(log["data"], "data")
    submitter = _address_from_word(_data_word(data, 0), "submitter")
    parent_hash = _bytes32_word(_data_word(data, 1))
    block_hash = _bytes32_word(_data_word(data, 2))
    content_hash = _bytes32_word(_data_word(data, 3))
    uri_hash = _bytes32_word(_data_word(data, 4))
    uri_offset = _uint_from_word(_data_word(data, 5))
    uri = _string_from_data(data, uri_offset)

    return DocAttested(
        doc_chain_id=doc_chain_id,
        attester=attester,
        doc_ref=doc_ref,
        submitter=submitter,
        parent_hash=parent_hash,
        block_hash=block_hash,
        content_hash=content_hash,
        uri_hash=uri_hash,
        uri=uri,
        block_number=_quantity(log["blockNumber"], "blockNumber"),
        transaction_hash=_bytes32(log["transactionHash"], "transactionHash"),
        log_index=_quantity(log["logIndex"], "logIndex"),
        ethereum_block_hash=_optional_bytes32(log.get("blockHash"), "blockHash"),
    )


def _topics(log: Mapping[str, object]) -> list[str]:
    raw_topics = log.get("topics")
    if not isinstance(raw_topics, Sequence) or isinstance(raw_topics, (str, bytes)):
        raise ValueError("log topics must be a sequence")
    return [str(topic) for topic in raw_topics]


def _hex(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise ValueError(f"{field} must be a 0x-prefixed hex string")
    body = value[2:].lower()
    try:
        int(body or "0", 16)
    except ValueError as exc:
        raise ValueError(f"{field} contains non-hex characters") from exc
    return body


def _word(value: object, field: str) -> str:
    body = _hex(value, field)
    if len(body) != 64:
        raise ValueError(f"{field} must be 32 bytes")
    return body


def _data(value: object, field: str) -> str:
    body = _hex(value, field)
    if len(body) % 2 != 0:
        raise ValueError(f"{field} has an odd-length hex payload")
    return body


def _data_word(data: str, index: int) -> str:
    start = index * 64
    end = start + 64
    if len(data) < end:
        raise ValueError(f"data is too short for ABI word {index}")
    return data[start:end]


def _bytes32(value: object, field: str) -> str:
    return "0x" + _word(value, field)


def _optional_bytes32(value: object, field: str) -> str:
    if value is None:
        return ""
    return _bytes32(value, field)


def _bytes32_word(word: str) -> str:
    return "0x" + word


def _address_from_word(word: str, field: str) -> str:
    if int(word[:24], 16) != 0:
        raise ValueError(f"{field} has non-zero address padding")
    return "0x" + word[24:]


def _uint_from_word(word: str) -> int:
    return int(word, 16)


def _uint64_from_word(word: str, field: str) -> int:
    value = _uint_from_word(word)
    if value > 2**64 - 1:
        raise ValueError(f"{field} does not fit uint64")
    return value


def _quantity(value: object, field: str) -> int:
    if isinstance(value, int):
        if value < 0:
            raise ValueError(f"{field} must not be negative")
        return value
    return int(_hex(value, field) or "0", 16)


def _string_from_data(data: str, offset: int) -> str:
    if offset % 32 != 0:
        raise ValueError("ABI string offset is not word-aligned")
    length_word_start = offset * 2
    length_word_end = length_word_start + 64
    if len(data) < length_word_end:
        raise ValueError("data is too short for ABI string length")
    byte_length = int(data[length_word_start:length_word_end], 16)
    payload_start = length_word_end
    payload_end = payload_start + byte_length * 2
    if len(data) < payload_end:
        raise ValueError("data is too short for ABI string payload")
    return bytes.fromhex(data[payload_start:payload_end]).decode("utf-8")
