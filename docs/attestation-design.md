# RSO Attestation Design

This document is the RSO profile and integration design for the generic
Document Chain protocol.

Generic protocol concerns — `DocBlock`, EIP-712 attestation shape, contract
events, EIP-1271 support, duplicate prevention, and neutral reference event
models — live in the sibling [`doc-chain`](../../doc-chain) repo. This repo
owns the RSO-specific profile: archive validation, holder sponsorship, TDH
scoring, NFT/dApp flow, and relayer policy.

The RSO-specific goal is to let RSO Meme Card holders confirm archive
publications without paying gas, while preserving a direct paid path for
anyone else and protecting the OW treasury from repeated sponsored
submissions.

## Roles

| Component | Responsibility |
|-----------|----------------|
| Static dApp | Reads archive data, checks wallet status, asks wallet to sign, submits to relayer or contract |
| Relayer | Verifies eligibility and artifact integrity, pays gas for approved sponsored submissions |
| DocChain contract | Verifies signatures, prevents duplicates, emits append-only attestation events |
| OW treasury | Funds limited relayer hot wallets |

The DocChain contract should be deployed on Ethereum L1 mainnet. All holder
lookups, TDH checks, and signature verification target mainnet directly. The
protocol does not span chains: there is no L2 deployment, no bridge, and no
cross-chain attestation.

The dApp should be static and hostable by every node operator fork. It does not
need a node-local backend. It submits directly from the browser to one or more
relayer endpoints.

## User Flow

```text
1. User opens the NFT or verification viewer.
2. User scrolls to any archive day and asks the NFT/viewer to validate it.
3. NFT/viewer loads that day's document metadata and validates the chain edge.
4. NFT/viewer opens the attestation dApp with the exact values to sign.
5. User connects wallet in the dApp.
6. dApp checks current RSO Meme Card holdings.
7. dApp checks card-specific TDH through 6529 API or prenode data.
8. User signs an EIP-712 document attestation.
9. If signer is eligible for sponsorship, dApp POSTs to relayer.
10. If signer is not eligible, dApp offers direct contract submission.
```

The NFT/viewer is the archive browsing, verification, and candidate-selection
surface. It should let a user scroll back to any retained or indexed archive
day, load that day's metadata, validate the document hash and parent link, and
then prepare the corresponding chain-edge claim. Because NFT iframes cannot
reliably access wallet injection, the dApp is the signing and submission
surface. The dApp receives the `DocBlock` fields (`docChainId`, `docRef`,
`parentHash`, `contentHash`) and optional `uri` from the
NFT/viewer, displays the claim, and asks the wallet to sign it.

The dApp holder/TDH checks are for UX and routing. The relayer must repeat all
sponsorship checks before paying gas.

## Deployable Projects

Keep the RSO attestation system as four deployable projects, plus vendored
Document Chain reference code:

```text
nft/         static NFT/viewer, published to Arweave
dapp/        static wallet signing/submission app
indexer/     static attestation index generator
relayer/     long-running backend for sponsored gas
vendor/docchain/  pinned stdlib-only helpers from ../doc-chain
```

The NFT and dApp are separate static sites. The NFT is the read-only archive
viewer embedded by NFT platforms. The dApp is the wallet-enabled signing and
submission page opened from the NFT/viewer. GitHub Pages is acceptable for
development and operator forks; the canonical NFT/viewer should also be
published to Arweave.

The indexer should not be a required live service. It can run from GitHub
Actions, read contract logs, and publish compact JSON pages that the NFT uses
for normal browsing. A scheduled indexer run every five minutes is a reasonable
starting point; manual dispatch should also be available for deploys and
repairs.

The NFT should treat that static index as its durable browse cache, then add a
small **Recent** overlay from public RPC:

```text
1. Load the static index.
2. Read latestIndexedBlock from the index metadata.
3. Estimate the block for a short recent window, e.g. the last 10 minutes.
4. Query DocumentAttested logs from max(recentWindowStart, latestIndexedBlock + 1)
   to the current head.
5. Merge matching logs into an in-memory Recent set.
6. Repeat lightly while open, for example once per minute.
```

Recent logs are onchain events that have not yet been folded into the static
index. Because the static index is expected to refresh every five to ten
minutes, the NFT should only poll a small recent block range. If the static
index is stale enough that the bounded recent window would miss events, the NFT
should show the index as stale and wait for the next refresh rather than scan a
large public-RPC range. The dApp remains the immediate confirmation surface
after submission; the NFT does not rely on cross-window callbacks from the dApp.

The relayer is the only backend that must be continuously available because it
holds a hot-wallet key and submits sponsored transactions. It should start as a
small long-running service with explicit logs, quota state, monitoring, and key
rotation. A Cloudflare Worker can be useful later as a front door, cache, or
rate limiter, but the signer/hot-wallet service should remain an explicitly
operated backend.

The generic contract source and deployment metadata live in `../doc-chain`.
When RSO needs generic log decoding or `DocBlock` helpers, copy a pinned
stdlib-only reference module from `../doc-chain/reference/docchain/` into
`vendor/docchain/` by reviewed PR. Do not install it with pip or fetch it at
runtime.

## Sponsorship Eligibility

A relayer-sponsored submission requires:

- valid EIP-712 signature
- signer currently holds the RSO Meme Card
- signer has non-zero card-specific TDH for that card
- signer has remaining sponsored quota for the archive day
- attestation is not a duplicate
- if `uri != ""`, URI resolves to bytes that match the signed DocBlock claims
- if `uri == ""`, the profile metadata validates the signed chain edge
- signer is not blacklisted
- hourly and daily relayer budgets are not exhausted

Current card holdings are acceptable for the holder check. Card-specific TDH
still needs to come from the 6529 API or prenode data. Because TDH is calculated
on a schedule, it resists same-day card shuffling into fresh wallets: a wallet
that just received a card should not immediately gain sponsored quota unless it
already has non-zero card-specific TDH.

## Sponsored Quota

Base rule:

```text
sponsored submissions per signer per archive day <= current RSO Meme Card count
```

Recommended production rule:

```text
quota = min(
  current RSO Meme Card count,
  configured max per wallet per day
)

eligible only if card_specific_TDH > 0
```

Quota is counted by signer, document chain, and UTC archive day:

```text
sponsoredCount[signer][docChainId][quotaDay]
```

Do not count quota by URI or hash. Otherwise a signer could drain gas by
attesting many valid URIs for the same day.

The archive date and the quota window both follow the project's UTC day
boundary. A signer's sponsored quota resets at 00:00 UTC, which matches the
boundary used for archive publication and TDH calculation throughout the
project. There is no per-signer or per-region time-zone interpretation.

## Hourly Budget

Use hourly or rolling-window relayer budgets in addition to a daily cap.

A single daily budget can be exhausted by early time zones before later
operators and holders are awake. Hourly buckets make the treasury support more
globally fair and reduce blast radius if an abuse pattern appears.

Recommended budget controls:

- global daily gas cap
- rolling hourly gas cap
- per-wallet daily sponsored quota
- per-wallet short-window rate limit
- hot wallet funded only up to a limited operating budget
- emergency pause switch per relayer

The hot wallet should not hold more than the amount the project is willing to
lose before the next monitoring or refill cycle.

Hot wallets may be topped up in the hour before 00:00 UTC so the next day's
sponsored capacity is available immediately at the boundary. Refills must
still respect the loss-acceptance ceiling above; the goal is to land at the
ceiling at 00:00 UTC, not to exceed it.

## RSO DocChain Profile

The generic payload, EIP-712 domain, event shape, EIP-1271 behavior, deadline
checks, URI size cap, duplicate rule, and neutral event read model are defined
in the sibling [`doc-chain`](../../doc-chain) repo. RSO uses that generic
protocol with one profile:

```text
docChainId = keccak256("https://om.pub/rso/doc-chain/v1")
```

The profile URI is the human-readable source of the RSO profile rules. The
onchain `docChainId` is only the `bytes32` hash of that URI, so RSO clients,
indexers, relayers, and viewers must carry the profile URI in config or docs.

RSO fills the generic `DocBlock` as follows:

```solidity
struct DocBlock {
    bytes32 docChainId;     // keccak256("https://om.pub/rso/doc-chain/v1")
    uint64 docRef;          // UTC snapshot boundary as YYYYMMDDHHMMSS
    bytes32 parentHash;     // prior RSO DocBlock blockHash; 0x0 for baseline
    bytes32 contentHash;    // SHA-256 of canonical catalog JSON bytes
}
```

The DocChain contract computes and emits:

```text
blockHash = hashStruct(DocBlock)
```

The important linkage is `parentHash -> blockHash`. Because `parentHash` is
inside the block being hashed, changing a historical RSO document changes that
block hash and every descendant block hash.

For RSO v1:

- `docRef` is the snapshot boundary encoded as `YYYYMMDDHHMMSS` in UTC. It is
  a profile-defined reference, not a Unix timestamp.
- RSO daily snapshots must use `00:00:00Z`, so their `docRef` values end in
  `000000`.
- `docRef` must be exactly 14 decimal digits and decode to a valid Gregorian
  UTC instant with seconds `00` through `59`; leap seconds are not accepted.
- The decoded UTC instant must match the snapshot's `state_as_of_utc` /
  `cutoff_utc`.
- `contentHash` is SHA-256 of the canonical catalog JSON bytes defined by
  [`snapshot-spec.md`](snapshot-spec.md) and [`verification.md`](verification.md).
- `parentHash` is the previous RSO `blockHash`, not the previous catalog hash.
- the official baseline snapshot dated `2026-04-20` uses `bytes32(0)` as
  `parentHash`.
- `uri == ""` is the default holder confirmation path: it attests the RSO
  block without endorsing one storage location.
- `uri != ""` additionally claims that the location resolves to bytes matching
  the RSO profile rules for `contentHash`.

Examples:

```text
2026-04-20T00:00:00Z -> docRef = 20260420000000
2026-05-14T00:00:00Z -> docRef = 20260514000000
```

The NFT/viewer prepares these exact `DocBlock` values after validating a
selected archive day. The dApp should display and sign those values; it should
not ask the attestor to pick an arbitrary bundle before it can produce a
signature. If a URI is present, the dApp can display it and the relayer must
validate it before paying gas.

## RSO Canonicality

DocChain records signed claims; it does not decide which branch is canonical.
For RSO, canonicality is an RSO profile rule:

```text
canonical branch = branch with the most eligible card-specific TDH
                   measured at each attestation's Ethereum block time
```

Indexers group `DocumentAttested` events by `docChainId`, `docRef`, and
`blockHash`, then walk parent links to build branches. For each attestation,
the RSO resolver looks up the attester's card-specific TDH at the time of the
Ethereum block that included the event. Historical TDH can come from 6529's
published Arweave snapshots or the 6529 node today; a future composable TDH
oracle can replace that source without changing the DocChain contract.

Current holdings and current TDH are still useful for sponsorship UX and
relayer eligibility. They are not the durable branch weight. Durable branch
weight uses historical TDH at attestation block time, so a later sale or
transfer does not rewrite old votes.

The RSO static index should publish the resolved RSO view, not just generic
events:

```text
docRef
  blockHash
    parentHash
    contentHash
    attestors[]
    historicalTdhWeight
    locations[uriHash]
```

The vendored `vendor/docchain/` code should only handle generic event models
and decoding helpers. RSO-specific parent validation, TDH lookup, branch
scoring, sponsorship, and archive validation stay in this repository.

## Relayer URI Validation

Before paying gas, the relayer must validate that the submitted URI matches the
signed DocBlock claims.

For hash-only attestations where `uri == ""`, skip URI fetching and continue
with profile metadata validation, duplicate, quota, and transaction-simulation
checks. A hash-only attestation signs the chain edge without endorsing any
specific publication location.

Bundle layout, file roles, and canonical hashing rules are defined in
[`snapshot-spec.md`](snapshot-spec.md) and [`verification.md`](verification.md).
The steps below are the relayer-side application of that spec; if the two
disagree, the snapshot/verification specs are authoritative for bundle format.

For `keccak256("https://om.pub/rso/doc-chain/v1")`, the validation profile is:

- `docRef` is the RSO snapshot boundary encoded as `YYYYMMDDHHMMSS` in UTC
- `parentHash` is the previous RSO `blockHash`, or `bytes32(0)` for the
  baseline snapshot
- `contentHash` is SHA-256 of the canonical catalog JSON bytes
- snapshot metadata must bind the current `blockHash`, `parentHash`, and
  `contentHash`
- direct-document URIs must resolve to canonical catalog bytes whose SHA-256 is
  `contentHash`
- release-bundle URIs must contain the canonical bundle inventory from
  [`snapshot-spec.md`](snapshot-spec.md)
- `manifest.json` `sha256` must equal `contentHash`
- decompressed `catalog.json.gz` bytes must hash to `contentHash`

For each request:

1. Apply the generic DocChain preflight checks from
   [`doc-chain/docs/protocol.md`](../../doc-chain/docs/protocol.md): signature
   verification for EOAs and EIP-1271 wallets, deadline enforcement, URI byte
   cap, and duplicate simulation.
2. If `uri != ""`, validate the location: check allowed URI scheme and host,
   reject any URI resolving to private/internal IP ranges, fetch the artifact
   with strict size, timeout, and redirect limits, determine whether it is a
   direct document or release bundle, and verify the artifact bytes against
   `contentHash`.
3. If the fetched artifact is a bundle, extract contents and apply the strict
   allowlist. Reject the bundle immediately if it contains files outside the
   canonical bundle inventory defined in [`snapshot-spec.md`](snapshot-spec.md).
   Anything not in that spec — executables, HTML, surprise artifacts — is an
   immediate reject.
4. For a fetched RSO release bundle, verify `manifest.sha256 == contentHash`,
   decompress `catalog.json.gz` using a streamed reader with a strict
   decompression byte limit (e.g., 150MB max), and verify the canonical catalog
   bytes hash to `contentHash`.
5. Validate `parentHash -> blockHash` against the `docChainId` profile. For
    RSO v1, this means the selected snapshot metadata must identify
    `parentHash` as the prior RSO block hash, except for the baseline snapshot.
6. Check sponsorship quota.
7. Simulate the transaction with `eth_call` against the latest block to
    catch any remaining revert conditions before paying gas.

    *Residual race:* `eth_call` reads committed state, not the public mempool.
    If two relayers simulate in the same block window, both can pass and both
    can submit; only the first wins onchain and the loser eats base fees.
    Public mempool inspection (`eth_subscribe newPendingTransactions`) is not
    a reliable mitigation on mainnet — a meaningful share of transactions
    arrive via private orderflow and never appear publicly. The collision
    rate should be low in practice given per-wallet quotas and short
    deadlines; if it turns out to be material, the answer is a shared
    coordination cache (e.g., Redis) across project-blessed relayers keyed on
    `(attester, blockHash, uriHash)`, not
    mempool watching.
8. Submit transaction only after all checks pass.

Allowed URI types should start narrow:

- `ar://<tx-id>`
- `https://arweave.net/<tx-id>`
- `ipfs://<cid>`
- known GitHub Release asset URLs, if still needed

GitHub URLs are mutable by repository maintainers, so Arweave/IPFS publications
should be preferred for durable attestations.

## Direct Submission

If the signer is not eligible for sponsorship, or the relayer budget is
exhausted, the dApp should offer direct contract submission.

Direct submissions:

- use the same generic DocChain EIP-712 payload
- are paid by the attestor
- should not require relayer-specific quota checks

Sponsorship policy lives in relayers because relayers are the components that
spend treasury gas. Direct submissions remain ordinary DocChain claims; off-chain
RSO readers still decide whether the artifact, URI, and digest satisfy the RSO
profile. Sponsored submissions carry the extra signal that a relayer already
performed those RSO checks before paying gas.

## Relayer Decentralization

The dApp is published from this repository and any node operator can host it
alongside their archive. The list of relayer endpoints ships in the dApp
source and propagates through the same daily upstream-sync the node already
runs (see [`daily-snapshot.yml`](../.github/workflows/daily-snapshot.yml)):
upstream changes — including new or removed relayers — land in operator
forks automatically on the next scheduled run, no manual pull required.

```text
[
  "https://relayer-1.example",
  "https://relayer-2.example",
  "https://operator-node.example/relayer"
]
```

An operator who runs their own relayer can add it to their fork's list
manually, even before — or instead of — it being blessed upstream. Their
fork's users then route through their relayer; everyone else continues with
the upstream-blessed list. There is no central registry to gate inclusion.

Treasury sponsorship is a separate question from list inclusion. Only
relayers that meet the funding criteria below receive treasury-funded hot
wallets; anyone else can still operate a relayer, but it must be self-funded.

If one relayer censors, fails, or exhausts its hourly budget, the dApp tries
the next entry in the list and finally falls back to direct submission.

## Abuse Response

Relayer operators should maintain:

- blacklist for abusive wallets
- logs of rejected and sponsored requests
- per-signer pending locks to prevent concurrent quota races
- configurable hourly and daily budgets
- emergency pause

If a bad actor abuses sponsorship, the project can:

- stop sponsoring that wallet
- give negative reputation in the 6529 context
- lower per-wallet caps
- lower hourly budget
- move to stricter TDH thresholds

## Relayer Funding

Anyone can operate a relayer: a node operator funding their own hot wallet,
a third party sponsoring a wallet they care about, or an OW-treasury-funded
wallet on the curated list. The contract is neutral and immutable — it has
no admin role, no upgrade path, and no concept of relayer identity.

OW treasury sponsorship is the curated subset. The treasury funds a relayer
when it runs the project's canonical relayer code, stays within agreed gas
caps, and remains reachable for incident response. Detailed funding criteria,
hot-wallet rotation, and day-to-day operator obligations live in
[`operator.md`](operator.md) (relayer sections) rather than here, since most
readers of this design will never run a relayer.

A funded relayer that misbehaves can be defunded by stopping treasury
top-ups; its onchain attestations remain visible like any other event, and
defunding is reversible. Defunding does not remove the relayer from the
dApp's published list — that is a code-update question handled upstream as
described under "Relayer Decentralization."

## Threats Addressed

This design reduces:

- fake repo-count consensus
- repeated sponsored duplicate attestations
- same-day card shuffling to drain gas
- bogus URI attestations
- early-day budget exhaustion by one time zone
- relayer censorship as a single point of failure

It does not by itself prove Space-Track source truth. It proves that identified
wallets made signed claims about a specific document chain, timestamp, hash,
and publication URI, and that sponsored claims passed relayer integrity and
eligibility checks.
