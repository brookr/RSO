# RSO Doc Chain Indexer

This indexer builds a static JSON view of RSO Doc Chain attestations. It is
stdlib-only and uses vendored generic helpers from `vendor/docchain`.

The first profile is Sepolia:

```text
network: sepolia
contract: 0x867FcC4f0339009043E9F6e554DD516Bcf1bcaa9
profileURI: https://om.pub/rso/doc-chain
docChainId: 0x8621c2851714436d60da45cf0e11253114a4f2002f73ddc159b4dc88fea5611d
fromBlock: 11007365
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

To include TDH support in the generated agreement groups, pass the directory of
daily TDH support snapshots and the public sweeper reports:

```bash
python3 indexer/index_rso_attestations.py \
  --tdh-support data/backing \
  --sweeper-reports reports/sweeper
```

Supported shape:

```json
{
  "schema": "rso-tdh-support-snapshot-v1",
  "date": "2026-06-01",
  "identities": {
    "example-6529-identity": {
      "cardSpecificTdh": 12345,
      "accounts": ["0x..."]
    }
  },
  "operators": {
    "github:owner/repo": {
      "positiveBackingTdh": 15000,
      "negativeBackingTdh": 2655,
      "netBackingTdh": 12345,
      "usableBackingTdh": 12345,
      "backerCount": 17,
      "positiveBackerCount": 15,
      "negativeBackerCount": 2,
      "rank": 1
    }
  }
}
```

The indexer applies each support snapshot only to that snapshot's date. Direct
witness TDH is assigned when the event attester is an account in a listed 6529
identity. Node-backing TDH is assigned only when a public sweeper report proves
that the selected node, artifact declaration, signed publication `nodeId`, and
signed attester all aligned and every listed bundle location verified.

Publication URLs never establish node identity. This prevents a signer from
claiming another node's backing by placing a victim-looking GitHub URL in an
attestation, and it allows Arweave-only nodes to receive backing after the
sweeper verifies them.

The index reports `directWitnessTdh`, the four signed node fields
`nodePositiveBackingTdh`, `nodeNegativeBackingTdh`, `nodeNetBackingTdh`, and
`nodeUsableBackingTdh`, plus `combinedSupportTdh`. Only usable node backing is
added to combined branch support. It counts each identity and node at most once
per agreement group. If an identity or node supports conflicting groups for one
day, that channel is reported as equivocating and counts for neither group.
Raw attestation count is descriptive only and never breaks a support tie; when
multiple groups have equal combined support, `leadingAgreementGroup` is `null`.
`onBehalfOf` remains visible as generic DocChain metadata, but RSO V1 does not
use it for TDH weighting.

Raw DocChain events are permissionless claims. An event can name any
`docChainId`, publish any URI string, or point at a repository-like location.
RSO consumers should treat unverified node claims as claims only. Verified
sweeper evidence plus the applicable daily TDH support snapshot determines
weighting, not event volume or repository count.
