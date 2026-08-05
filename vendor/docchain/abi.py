"""Doc Chain ABI constants used by stdlib indexers."""

MAX_URI_BYTES = 8192

EIP712_DOMAIN_TYPE = (
    "EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"
)

DOC_BLOCK_TYPE = (
    "DocBlock(bytes32 docChainId,uint64 docRef,bytes32 parentHash,bytes32 contentHash)"
)

DOC_ATTESTATION_TYPE = (
    "DocAttestation(address attester,address onBehalfOf,DocBlock docBlock,string uri,uint256 deadline)"
    + DOC_BLOCK_TYPE
)

DOC_ATTESTED_EVENT = (
    "DocAttested(bytes32,address,uint64,address,address,bytes32,bytes32,bytes32,bytes32,string)"
)

DOC_ATTESTED_EVENT_TOPIC0 = (
    "0xa5a9ded978a618be6783ec1af88ba95dd6e0ca4c344c2bd8893bed6aa92bb199"
)

DOC_ATTESTED_EVENT_LEGACY_TOPIC0 = (
    "0x003c1eb39369e9f39930ebf222b333215cb95fdc00894d4fe215b8659c452858"
)

DOC_ATTESTED_EVENT_TOPIC0S = (
    DOC_ATTESTED_EVENT_TOPIC0,
    DOC_ATTESTED_EVENT_LEGACY_TOPIC0,
)

# Custom error selectors: bytes4(keccak256(signature)). Couriers need these to
# classify reverts surfaced through eth_call / eth_estimateGas error messages.
DOCCHAIN_ERROR_SELECTORS = {
    "InvalidAttester()": "0xb8daf542",
    "DeadlineExpired(uint256,uint256)": "0x1503f5f8",
    "UriTooLong(uint256,uint256)": "0xa99e90bb",
    "DuplicateAttestation(bytes32)": "0xdd65d744",
    "InvalidSignature(address)": "0xd855c4f4",
    "InvalidSignatureLength(uint256)": "0x2c33b568",
    "EmptyBatch()": "0xc2e5347d",
    "BatchLengthMismatch(uint256,uint256)": "0x81b5b207",
}

DUPLICATE_ATTESTATION_ERROR_SELECTOR = DOCCHAIN_ERROR_SELECTORS[
    "DuplicateAttestation(bytes32)"
]


def is_duplicate_attestation_error(message: str) -> bool:
    """Return True when an RPC error message reports `DuplicateAttestation`.

    Providers surface custom errors inconsistently: some echo the raw selector
    data, others a decoded name. A duplicate revert means the claim is already
    witnessed on chain, so couriers should treat it as success, not failure.
    """
    normalized = message.lower()
    return (
        DUPLICATE_ATTESTATION_ERROR_SELECTOR in normalized
        or "duplicateattestation" in normalized
        or "duplicate attestation" in normalized
    )
