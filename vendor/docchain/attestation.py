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
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from .abi import MAX_URI_BYTES
from .model import ZERO_ADDRESS


ATTEST_DOC_SELECTOR = "d2b85e96"
ATTEST_BATCH_SELECTOR = "7fba5650"
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
    keystore_json_env: str | None = None,
    keystore_path_env: str | None = None,
    account_env: str | None = None,
    password_env: str | None = None,
    password_file_env: str | None = None,
    cast: str | None = None,
    run=subprocess.run,
) -> str:
    """Sign a prepared attestation using Foundry `cast wallet sign`.

    The signer can be supplied as a temporary keystore, an existing keystore, an
    account name, or a raw private key. Keystore inputs avoid placing the raw
    private key on the process command line. Raw private-key mode is retained
    for simple disposable signer setups and should never be used with funded
    keys.
    """
    typed_data = prepared["typedData"]
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as tmp:
        json.dump(typed_data, tmp, separators=(",", ":"), sort_keys=True)
        tmp_path = tmp.name
    try:
        with cast_wallet_args_from_env(
            private_key_env=private_key_env,
            keystore_json_env=keystore_json_env or private_key_env.replace("PRIVATE_KEY", "KEYSTORE_JSON"),
            keystore_path_env=keystore_path_env or private_key_env.replace("PRIVATE_KEY", "KEYSTORE"),
            account_env=account_env or private_key_env.replace("PRIVATE_KEY", "ACCOUNT"),
            password_env=password_env or private_key_env.replace("PRIVATE_KEY", "KEYSTORE_PASSWORD"),
            password_file_env=password_file_env or private_key_env.replace("PRIVATE_KEY", "KEYSTORE_PASSWORD_FILE"),
        ) as wallet_args:
            expected_attester = prepared_attester(prepared)
            if expected_attester is not None:
                actual_attester = cast_wallet_address(
                    wallet_args=wallet_args,
                    cast=cast,
                    run=run,
                )
                if actual_attester != expected_attester:
                    raise ValueError(
                        f"configured attester {expected_attester} does not match "
                        f"configured signing wallet address {actual_attester}"
                    )
            command = [
                cast_path(cast),
                "wallet",
                "sign",
                "--data",
                "--from-file",
                tmp_path,
                *wallet_args,
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
    *,
    private_key: str | None = None,
    wallet_args: list[str] | None = None,
    cast: str | None = None,
    run=subprocess.run,
) -> str:
    if wallet_args is None:
        if not private_key:
            raise ValueError("private_key or wallet_args is required")
        wallet_args = ["--private-key", private_key]
    command = [cast_path(cast), "wallet", "address", *wallet_args]
    result = run(command, check=True, capture_output=True, text=True)
    address = result.stdout.strip().splitlines()[-1].strip()
    return normalize_address(address)


def has_cast_wallet_config(
    *,
    private_key_env: str = "PRIVATE_KEY",
    keystore_json_env: str | None = None,
    keystore_path_env: str | None = None,
    account_env: str | None = None,
    allow_raw_private_key: bool = True,
) -> bool:
    names = [
        keystore_json_env or private_key_env.replace("PRIVATE_KEY", "KEYSTORE_JSON"),
        keystore_path_env or private_key_env.replace("PRIVATE_KEY", "KEYSTORE"),
        account_env or private_key_env.replace("PRIVATE_KEY", "ACCOUNT"),
    ]
    if allow_raw_private_key:
        names.append(private_key_env)
    return any(os.environ.get(name) for name in names)


@contextmanager
def cast_wallet_args_from_env(
    *,
    private_key_env: str = "PRIVATE_KEY",
    keystore_json_env: str | None = None,
    keystore_path_env: str | None = None,
    account_env: str | None = None,
    password_env: str | None = None,
    password_file_env: str | None = None,
    allow_raw_private_key: bool = True,
):
    """Yield Foundry wallet arguments from environment-backed signer config."""
    keystore_json_env = keystore_json_env or private_key_env.replace("PRIVATE_KEY", "KEYSTORE_JSON")
    keystore_path_env = keystore_path_env or private_key_env.replace("PRIVATE_KEY", "KEYSTORE")
    account_env = account_env or private_key_env.replace("PRIVATE_KEY", "ACCOUNT")
    password_env = password_env or private_key_env.replace("PRIVATE_KEY", "KEYSTORE_PASSWORD")
    password_file_env = password_file_env or private_key_env.replace("PRIVATE_KEY", "KEYSTORE_PASSWORD_FILE")

    with tempfile.TemporaryDirectory(prefix="docchain-wallet-") as tmpdir:
        tmp = Path(tmpdir)
        args: list[str]
        keystore_json = os.environ.get(keystore_json_env)
        keystore_path = os.environ.get(keystore_path_env)
        account = os.environ.get(account_env)
        raw_private_key = os.environ.get(private_key_env)

        if keystore_json:
            key_path = tmp / "keystore.json"
            key_path.write_text(keystore_json, encoding="utf-8")
            args = ["--keystore", str(key_path)]
        elif keystore_path:
            args = ["--keystore", keystore_path]
        elif account:
            args = ["--account", account]
        elif raw_private_key and allow_raw_private_key:
            args = ["--private-key", raw_private_key]
        else:
            choices = f"{keystore_json_env}, {keystore_path_env}, or {account_env}"
            if allow_raw_private_key:
                choices += f", or {private_key_env}"
            raise ValueError(f"set {choices}")

        password_file = os.environ.get(password_file_env)
        password = os.environ.get(password_env)
        if password_file:
            args.extend(["--password-file", password_file])
        elif password:
            password_path = tmp / "password.txt"
            password_path.write_text(password, encoding="utf-8")
            args.extend(["--password-file", str(password_path)])

        yield args


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


def attest_batch_calldata(items) -> str:
    """ABI-encode `attestBatch(DocAttestation[],bytes[])` call data.

    `items` is a sequence of `(attestation, signature)` pairs. Requires contract
    release 2 (`CONTRACT_VERSION >= "2"`). The batch may mix attesters; the
    contract skips already-recorded attestations, so resubmitting a partially
    landed batch is safe.
    """
    items = list(items)
    if not items:
        raise ValueError("attestBatch requires at least one attestation")
    attestation_tails = []
    signature_tails = []
    for attestation, signature in items:
        doc_block = attestation["docBlock"]
        if not isinstance(doc_block, Mapping):
            raise ValueError("docBlock must be an object")
        attestation_tails.append(_encode_attestation(attestation, doc_block))
        signature_tails.append(
            _encode_bytes(bytes.fromhex(normalize_hex_bytes(signature)[2:]))
        )
    attestations_encoded = _encode_dynamic_array(attestation_tails)
    signatures_encoded = _encode_dynamic_array(signature_tails)
    attestations_offset = 64
    signatures_offset = attestations_offset + len(attestations_encoded) // 2
    payload = (
        _word_uint(attestations_offset)
        + _word_uint(signatures_offset)
        + attestations_encoded
        + signatures_encoded
    )
    return "0x" + ATTEST_BATCH_SELECTOR + payload


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
        "DISPOSABLE_NO_FUNDS_ETH_KEYSTORE_JSON",
        "DISPOSABLE_NO_FUNDS_ETH_KEYSTORE_PASSWORD",
        "RSO_SWEEPER_KEYSTORE_JSON",
        "RSO_SWEEPER_KEYSTORE_PASSWORD",
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


def _encode_dynamic_array(element_tails: list[str]) -> str:
    """Encode a dynamic array of dynamic elements from pre-encoded tails.

    Element offsets are relative to the first byte after the length word, per
    the ABI spec for arrays of dynamic types.
    """
    heads = []
    offset = len(element_tails) * 32
    for tail in element_tails:
        heads.append(_word_uint(offset))
        offset += len(tail) // 2
    return _word_uint(len(element_tails)) + "".join(heads) + "".join(element_tails)


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
