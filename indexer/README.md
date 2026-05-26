# RSO Doc Chain Indexer

This indexer builds a static JSON view of RSO Doc Chain attestations. It is
stdlib-only and uses vendored generic helpers from `vendor/docchain`.

The first profile is Sepolia:

```text
network: sepolia
contract: 0xaCE3a26Fe2F993e351a0eF74fb727Cfe1029884b
profileURI: https://om.pub/rso/doc-chain/v1
docChainId: 0x8621c2851714436d60da45cf0e11253114a4f2002f73ddc159b4dc88fea5611d
fromBlock: 10849363
```

Run it with any Sepolia JSON-RPC endpoint:

```bash
DOCCHAIN_RPC_URL=https://... python3 indexer/index_rso_attestations.py
```

The default chunk size is `10` blocks to fit Alchemy's free-tier
`eth_getLogs` range limit. Larger ranges are safe with providers that allow
them; the vendored reference indexer can also detect provider range-limit
errors and split the scan into the advertised block window.

By default it writes:

```text
indexer/cache/sepolia/doc-attested.jsonl
indexer/cache/sepolia/checkpoint.json
indexer/generated/sepolia/rso-docchain-index.json
```

The JSONL file is the append-only raw event cache. The checkpoint advances only
after a chunk's events have been written to that cache. The generated index is a
browse cache for static clients. It is not a live service and it is not an
authority separate from the underlying onchain events.
