# Attestation Design

This document is the build source for the attestation dApp, relayer, and
Ethereum contract.

The goal is to let RSO Meme Card holders confirm archive publications without
paying gas, while preserving a direct paid path for anyone else and protecting
the OW treasury from repeated sponsored submissions.

## Roles

| Component | Responsibility |
|-----------|----------------|
| Static dApp | Reads archive data, checks wallet status, asks wallet to sign, submits to relayer or contract |
| Relayer | Verifies eligibility and artifact integrity, pays gas for approved sponsored submissions |
| Contract | Verifies signatures, prevents duplicates, emits append-only attestation events |
| OW treasury | Funds limited relayer hot wallets |

The contract is deployed on Ethereum L1 mainnet. All holder lookups, TDH
checks, and signature verification target mainnet directly. The protocol does
not span chains: there is no L2 deployment, no bridge, and no cross-chain
attestation.

The dApp should be static and hostable by every node operator fork. It does not
need a node-local backend. It submits directly from the browser to one or more
relayer endpoints.

## User Flow

```text
1. User opens attestation dApp from any node fork.
2. User connects wallet.
3. dApp checks current RSO Meme Card holdings.
4. dApp checks card-specific TDH through 6529 API or prenode data.
5. dApp verifies the selected archive artifact locally where practical.
6. User signs an EIP-712 archive attestation.
7. If signer is eligible for sponsorship, dApp POSTs to relayer.
8. If signer is not eligible, dApp offers direct contract submission.
```

The dApp holder/TDH checks are for UX and routing. The relayer must repeat all
sponsorship checks before paying gas.

## Sponsorship Eligibility

A relayer-sponsored submission requires:

- valid EIP-712 signature
- signer currently holds the RSO Meme Card
- signer has non-zero card-specific TDH for that card
- signer has remaining sponsored quota for the archive day
- attestation is not a duplicate
- URI resolves to bytes that match the signed hash claims
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

Quota is counted by signer and archive date:

```text
sponsoredCount[signer][archiveDate]
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

## Attestation Payload

Use EIP-712 typed data. The attestation should bind to the chain, verifying
contract, archive date, consensus hash, publication URI, and signature
deadline.

### Domain Separator

To prevent cross-chain replay attacks (e.g., testnet signatures replayed on mainnet), the EIP-712 domain separator must explicitly include the `chainId` and `verifyingContract`:

```solidity
struct EIP712Domain {
    string name;              // "Orbital Witness"
    string version;           // "1"
    uint256 chainId;          // 1 (Ethereum mainnet); Sepolia (11155111) only for staging
    address verifyingContract; // Address of the deployed attestation contract
}
```

*Note: The `version` field follows EIP-712 best practice for long-lived protocols. It costs nothing to include and gives the project a clean way to deploy a v2 of the attestation struct later — alongside or in place of v1 — without manual disambiguation. Worth keeping for a multi-decade project even if v2 never ships.*


Suggested payload fields:

```solidity
struct ArchiveAttestation {
    uint32 archiveDate;      // YYYYMMDD or days since Unix epoch
    bytes32 catalogHash;     // SHA-256 canonical catalog bytes
    bytes32 bundleHash;      // SHA-256 release bundle bytes
    bytes32 uriHash;         // keccak256(bytes(uri))
    string uri;              // emitted in event for discoverability
    uint256 deadline;        // timestamp after which the signature expires
}
```

Use both date and hash. Date preserves archive-day semantics. Hash preserves
the consensus object. URI identifies the specific publication being attested.

### Deadline Enforcement

Both the contract and every relayer MUST reject any attestation whose
`deadline` has passed. The contract is the authoritative check; the relayer
performs the same check earlier so it does not pay gas on transactions the
contract will reject:

- **Contract:** revert if `block.timestamp > deadline`. This makes signatures
  uncloggable — no relayer or third party can park a stale signature and
  replay it later.
- **Relayer:** reject before paying gas if the deadline is already past, and
  reject if the deadline is too close to the current head (e.g., less than
  60 seconds remaining) to avoid paying gas on a transaction that will
  expire between simulation and inclusion.

dApps should set `deadline` conservatively: long enough to survive a normal
relayer queue (minutes), short enough that a divested or compromised wallet
cannot be replayed days later (hours, not days).

## Contract Events

The contract must emit an append-only event for every successful attestation. This event provides enough data for indexers to reconstruct the state without heavy contract reads.

```solidity
event ArchiveAttested(
    address indexed attester,
    address indexed submitter,
    uint32 indexed date,
    bytes32 catalogHash,
    bytes32 bundleHash,
    bytes32 uriHash,
    string uri
);
```

### Event Sufficiency & Read Model

The `ArchiveAttested` event must be sufficient for the NFT, indexers, and operator tooling to reconstruct witness history without depending on heavy contract read APIs. 

From events alone, an indexer can reconstruct:
- Every archive date that has attestations
- Every attester for each date
- Who paid gas for each submission (`submitter`)
- Every candidate `catalogHash` for a given date
- Each bundle hash and URI attested for that candidate
- Hash-only attestations where `uri == ""` and `bundleHash == 0x0`
- Duplicate-prevention identity by recomputing the attestation key
- Fork/dispute state by grouping multiple hashes for the same date

The event is therefore enough to build an off-chain read model (index):

```text
date
  catalogHash candidate
    attestors[]
    submitters[]
    locations[uriHash]
      uri
      bundleHash
      attestors[]
```

The contract does not need to expose large paginated date reads for normal NFT rendering. The index can be generated purely from logs and published as JSON pages. The NFT can then use the index for browsing and make targeted RPC calls only when the user wants proof for a specific event or day.


## Smart Contract Wallet Support (EIP-1271)

To support smart contract wallets (e.g., Safe multisigs, account abstraction wallets), the signature verification must implement [EIP-1271](https://eips.ethereum.org/EIPS/eip-1271). Both the relayer off-chain and the verifying contract on-chain should call `isValidSignature(hash, signature)` alongside standard `ecrecover` logic.

## Duplicate Rule

Contract-level uniqueness:

```text
attested[signer][archiveDate][catalogHash][uriHash] = true
```

Implementation can store a packed key:

```solidity
bytes32 key = keccak256(
    abi.encode(signer, archiveDate, catalogHash, uriHash)
);
```

This allows a signer to attest distinct publications for the same day, such as
Arweave and IPFS copies, while blocking repeated attestations to the same
publication.

## Relayer URI Validation

Before paying gas, the relayer must validate that the submitted URI matches the
signed hash claims.

Bundle layout, file roles, and canonical hashing rules are defined in
[`snapshot-spec.md`](snapshot-spec.md) and [`verification.md`](verification.md).
The steps below are the relayer-side application of that spec; if the two
disagree, the snapshot/verification specs are authoritative for bundle format.

For each request:

1. Recover signer from EIP-712 signature. (Support EIP-1271 `isValidSignature` for smart contract wallets).
2. Check allowed URI scheme and host. **SSRF Protection:** Reject any URIs resolving to private/internal IP ranges (e.g., RFC1918 `10.0.0.0/8`, `192.168.0.0/16`, link-local `169.254.0.0/16`, IPv6 ULA, or loopback).
3. Fetch artifact with strict size, timeout, and redirect limits.
4. Compute `bundleHash` of the downloaded bytes. Halt immediately if it does not match the signature to prevent processing malicious or oversized payloads.
5. Extract contents if the artifact is a bundle.
6. **Strict Allowlist:** Reject the bundle immediately if it contains files outside the canonical bundle inventory defined in [`snapshot-spec.md`](snapshot-spec.md). Anything not in that spec — executables, HTML, surprise artifacts — is an immediate reject.
7. Verify `manifest.sha256 == catalogHash`.
8. Decompress `catalog.json.gz` using a streamed reader with a strict decompression byte limit (e.g., 150MB max) to prevent Zip-Bomb Denial-of-Service attacks.
9. Verify canonical catalog bytes hash to `catalogHash`.
10. Check duplicate and sponsorship quota.
11. Simulate the transaction with `eth_call` against the latest block to
    catch duplicates, expired deadlines, and other revert conditions before
    paying gas.

    *Residual race:* `eth_call` reads committed state, not the public mempool.
    If two relayers simulate in the same block window, both can pass and both
    can submit; only the first wins on-chain and the loser eats base fees.
    Public mempool inspection (`eth_subscribe newPendingTransactions`) is not
    a reliable mitigation on mainnet — a meaningful share of transactions
    arrive via private orderflow and never appear publicly. The collision
    rate should be low in practice given per-wallet quotas and short
    deadlines; if it turns out to be material, the answer is a shared
    coordination cache (e.g., Redis) across project-blessed relayers keyed on
    `(signer, archiveDate, catalogHash, uriHash)`, not mempool watching.
12. Submit transaction only after all checks pass.

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

- use the same EIP-712 payload
- are paid by the attestor
- are subject to the same contract duplicate rule
- should not require relayer-specific quota checks

The contract can remain neutral: it verifies signatures and rejects duplicates.
Sponsorship policy lives in relayers because relayers are the components that
spend treasury gas.

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
top-ups; its on-chain attestations remain visible like any other event, and
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
wallets made signed claims about a specific archive date, hash, and publication
URI, and that sponsored claims passed relayer integrity and eligibility checks.
