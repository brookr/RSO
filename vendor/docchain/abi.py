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
