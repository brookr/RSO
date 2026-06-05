"""Generic Doc Chain attestation preparation and calldata helpers.

These helpers are stdlib-only and profile-neutral. Project-specific code should
choose the DocBlock values, URI, identity policy, and submission rules.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from .abi import MAX_URI_BYTES
from .model import ZERO_ADDRESS


ATTEST_DOC_SELECTOR = "d2b85e96"
DOC_BLOCK_TYPEHASH = "0xb84212102d711af6fc7ae9fa3e37753befb8b25762a552631b0e9ff9e8d07894"
DOCCHAIN_DOMAIN_NAME = "Doc Chain"
DOCCHAIN_DOMAIN_VERSION = "1"


def typed_data_for_attestation(
    *,
    chain_id: int,
    contract_address: str,
    attestation: Mapping[str, object],
) -> dict[str, object]:
    """Return the EIP-712 typed-data object for a Doc Chain attestation."""
    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "DocBlock": [
                {"name": "docChainId", "type": "bytes32"},
                {"name": "docRef", "type": "uint64"},
                {"name": "parentHash", "type": "bytes32"},
                {"name": "contentHash", "type": "bytes32"},
            ],
            "DocAttestation": [
                {"name": "attester", "type": "address"},
                {"name": "onBehalfOf", "type": "address"},
                {"name": "docBlock", "type": "DocBlock"},
                {"name": "uri", "type": "string"},
                {"name": "deadline", "type": "uint256"},
            ],
        },
        "primaryType": "DocAttestation",
        "domain": {
            "name": DOCCHAIN_DOMAIN_NAME,
            "version": DOCCHAIN_DOMAIN_VERSION,
            "chainId": positive_uint(chain_id, "chainId"),
            "verifyingContract": normalize_address(contract_address),
        },
        "message": dict(attestation),
    }


def prepare_attestation(
    *,
    chain_id: int,
    contract_address: str,
    attester: str,
    doc_chain_id: str,
    doc_ref: int,
    parent_hash: str,
    content_hash: str,
    uri: str = "",
    deadline: int | None = None,
    ttl: int = 86_400,
    on_behalf_of: str = ZERO_ADDRESS,
    network: str = "",
) -> dict[str, object]:
    """Build a prepared Doc Chain attestation artifact."""
    if deadline is None:
        if ttl <= 0:
            raise ValueError("ttl must be positive")
        deadline = int(time.time()) + ttl
    if len(uri.encode("utf-8")) > MAX_URI_BYTES:
        raise ValueError(f"uri exceeds {MAX_URI_BYTES} UTF-8 bytes")
    attestation = {
        "attester": normalize_address(attester),
        "onBehalfOf": normalize_address(on_behalf_of),
        "docBlock": {
            "docChainId": normalize_bytes32(doc_chain_id),
            "docRef": uint64(doc_ref, "docRef"),
            "parentHash": normalize_bytes32(parent_hash),
            "contentHash": normalize_bytes32(content_hash),
        },
        "uri": uri,
        "deadline": positive_uint(deadline, "deadline"),
    }
    return {
        "schema": "doc-chain-prepared-attestation-v1",
        "contract": "DocChain",
        "contractAddress": normalize_address(contract_address),
        "network": network,
        "chainId": positive_uint(chain_id, "chainId"),
        "attestation": attestation,
        "typedData": typed_data_for_attestation(
            chain_id=chain_id,
            contract_address=contract_address,
            attestation=attestation,
        ),
        "preparedAt": utc_now(),
    }


def signed_attestation(prepared: Mapping[str, object], signature: str) -> dict[str, object]:
    return {
        "schema": "doc-chain-signed-attestation-v1",
        "prepared": dict(prepared),
        "signature": normalize_hex_bytes(signature),
        "signedAt": utc_now(),
    }


def sign_prepared_with_cast(
    prepared: Mapping[str, object],
    *,
    private_key_env: str = "PRIVATE_KEY",
    cast: str | None = None,
    run=subprocess.run,
) -> str:
    """Sign a prepared attestation using Foundry `cast wallet sign`.

    The private key is read from `private_key_env`. Callers should use a
    disposable signing key and should never fund that key.
    """
    typed_data = prepared["typedData"]
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as tmp:
        json.dump(typed_data, tmp, separators=(",", ":"), sort_keys=True)
        tmp_path = tmp.name
    try:
        private_key = os.environ.get(private_key_env)
        if not private_key:
            raise ValueError(f"set {private_key_env}")
        expected_attester = prepared_attester(prepared)
        if expected_attester is not None:
            actual_attester = cast_wallet_address(
                private_key,
                cast=cast,
                run=run,
            )
            if actual_attester != expected_attester:
                raise ValueError(
                    f"configured attester {expected_attester} does not match "
                    f"{private_key_env} address {actual_attester}"
                )
        command = [
            cast_path(cast),
            "wallet",
            "sign",
            "--data",
            "--from-file",
            tmp_path,
            "--private-key",
            private_key,
        ]
        result = run(command, check=True, capture_output=True, text=True)
        signature = result.stdout.strip().splitlines()[-1].strip()
        return normalize_hex_bytes(signature)
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def prepared_attester(prepared: Mapping[str, object]) -> str | None:
    attestation = prepared.get("attestation")
    if not isinstance(attestation, Mapping):
        return None
    attester = attestation.get("attester")
    return normalize_address(attester) if attester else None


def cast_wallet_address(
    private_key: str,
    *,
    cast: str | None = None,
    run=subprocess.run,
) -> str:
    command = [cast_path(cast), "wallet", "address", "--private-key", private_key]
    result = run(command, check=True, capture_output=True, text=True)
    address = result.stdout.strip().splitlines()[-1].strip()
    return normalize_address(address)


def attest_doc_calldata(attestation: Mapping[str, object], signature: str) -> str:
    """ABI-encode `attestDoc(DocAttestation,bytes)` call data."""
    doc_block = attestation["docBlock"]
    if not isinstance(doc_block, Mapping):
        raise ValueError("docBlock must be an object")
    attestation_encoded = _encode_attestation(attestation, doc_block)
    signature_encoded = _encode_bytes(bytes.fromhex(normalize_hex_bytes(signature)[2:]))
    signature_offset = 64 + len(attestation_encoded) // 2
    payload = _word_uint(64) + _word_uint(signature_offset) + attestation_encoded + signature_encoded
    return "0x" + ATTEST_DOC_SELECTOR + payload


def doc_block_hash_with_cast(
    doc_block: Mapping[str, object],
    *,
    cast: str | None = None,
    run=subprocess.run,
) -> str:
    """Compute the contract's `hashDocBlock` value with Foundry `cast keccak`."""
    payload = doc_block_hash_payload(doc_block)
    command = [cast_path(cast), "keccak", payload]
    result = run(command, check=True, capture_output=True, text=True)
    return normalize_bytes32(result.stdout.strip().splitlines()[-1].strip())


def doc_block_hash_payload(doc_block: Mapping[str, object]) -> str:
    """Return ABI-encoded `hashStruct(DocBlock)` input bytes."""
    return (
        DOC_BLOCK_TYPEHASH
        + _word_bytes32(str(doc_block["docChainId"]))
        + _word_uint(uint64(int(doc_block["docRef"]), "docRef"))
        + _word_bytes32(str(doc_block["parentHash"]))
        + _word_bytes32(str(doc_block["contentHash"]))
    )


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_json(path: Path) -> dict[str, object]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return raw


def normalize_address(value: object) -> str:
    body = _hex_body(value, "address")
    if len(body) != 40:
        raise ValueError("address must be 20 bytes")
    return "0x" + body


def normalize_bytes32(value: object) -> str:
    body = _hex_body(value, "bytes32")
    if len(body) != 64:
        raise ValueError("bytes32 value must be 32 bytes")
    return "0x" + body


def normalize_hex_bytes(value: object) -> str:
    body = _hex_body(value, "bytes")
    if len(body) % 2 != 0:
        raise ValueError("bytes value must have even hex length")
    return "0x" + body


def positive_uint(value: int, field: str) -> int:
    if value < 0:
        raise ValueError(f"{field} must not be negative")
    return value


def uint64(value: int, field: str) -> int:
    positive_uint(value, field)
    if value > 2**64 - 1:
        raise ValueError(f"{field} must fit uint64")
    return value


def cast_path(value: str | None = None) -> str:
    if value:
        return value
    foundry_cast = Path.home() / ".foundry" / "bin" / "cast"
    if foundry_cast.exists():
        return str(foundry_cast)
    return os.environ.get("CAST", "cast")


def subprocess_error_detail(exc: subprocess.CalledProcessError) -> str:
    detail = (exc.stderr or "").strip() or (exc.stdout or "").strip()
    if not detail:
        detail = f"command exited with {exc.returncode}"
    return redact_secret_values(detail)


def redact_secret_values(text: str) -> str:
    redacted = text
    for name in (
        "DISPOSABLE_NO_FUNDS_ETH_PRIVATE_KEY",
        "PRIVATE_KEY",
        "RSO_SWEEPER_PRIVATE_KEY",
        "SUBMITTER_PRIVATE_KEY",
    ):
        value = os.environ.get(name)
        if value:
            redacted = redacted.replace(value, "<redacted>")
    return redacted


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _encode_attestation(
    attestation: Mapping[str, object],
    doc_block: Mapping[str, object],
) -> str:
    uri = str(attestation.get("uri", "")).encode("utf-8")
    uri_offset = 8 * 32
    return (
        _word_address(str(attestation["attester"]))
        + _word_address(str(attestation.get("onBehalfOf", ZERO_ADDRESS)))
        + _word_bytes32(str(doc_block["docChainId"]))
        + _word_uint(uint64(int(doc_block["docRef"]), "docRef"))
        + _word_bytes32(str(doc_block["parentHash"]))
        + _word_bytes32(str(doc_block["contentHash"]))
        + _word_uint(uri_offset)
        + _word_uint(positive_uint(int(attestation["deadline"]), "deadline"))
        + _encode_bytes(uri)
    )


def _encode_bytes(payload: bytes) -> str:
    padding = b"\x00" * ((32 - len(payload) % 32) % 32)
    return _word_uint(len(payload)) + (payload + padding).hex()


def _word_uint(value: int) -> str:
    return format(positive_uint(value, "uint"), "064x")


def _word_address(value: str) -> str:
    return normalize_address(value)[2:].rjust(64, "0")


def _word_bytes32(value: str) -> str:
    return normalize_bytes32(value)[2:]


def _hex_body(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.lower().startswith("0x"):
        raise ValueError(f"{field} must be a 0x-prefixed hex string")
    body = value[2:].lower()
    try:
        int(body or "0", 16)
    except ValueError as exc:
        raise ValueError(f"{field} contains non-hex characters") from exc
    return body
