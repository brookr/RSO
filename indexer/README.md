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

For a production or local deployment, pass an explicit custom network:

```bash
DOCCHAIN_RPC_URL=https://... python3 indexer/index_rso_attestations.py \
  --network custom \
  --chain-id 1 \
  --contract-address 0x... \
  --deployment-block 12345678
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

For `--network custom`, the same pattern is used under
`indexer/cache/custom/` and `indexer/generated/custom/` unless you pass
explicit paths.

The JSONL file is the append-only raw event cache. The checkpoint advances only
after a chunk's events have been written to that cache. The generated index is a
browse cache for static clients. It is not a live service and it is not an
authority separate from the underlying onchain events.

The vendored decoder accepts both the original `DocAttested` event shape and the
current shape with signed `onBehalfOf` metadata. Legacy events are indexed with
`onBehalfOf` set to the zero address.

To include card-specific backing in the generated agreement groups, pass a
daily operator-backing snapshot:

```bash
python3 indexer/index_rso_attestations.py --backing backing.json
```

Supported shape:

```json
{
  "schema": "rso-operator-backing-snapshot-v1",
  "date": "2026-06-01",
  "operators": {
    "0xoperatorAttester": { "cardSpecificTdh": 12345, "backerCount": 17, "rank": 1 }
  }
}
```

The indexer groups events by daily fingerprint and sums backing across the
operator attesters in each agreement group. `onBehalfOf` remains visible as
DocChain metadata, but RSO V1 does not use it for TDH weighting. The indexer
does not treat raw repository count as consensus.
