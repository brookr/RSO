# Background — RSO Archive Project

> Full design context for coding agents. This document captures the complete architectural conversation behind this project. Read this before making any design decisions.

---

## One-Line Summary

A community-operated, permanently archived, cryptographically verified daily snapshot of every tracked artificial object in Earth orbit — funded by an NFT, visualized and verified through the NFT client, stored forever on Arweave, and attested on Ethereum.

---

## Origin & Motivation

This project lives at the intersection of the 6529 Open Metaverse (OM) community and space situational awareness (SSA). The idea: create a meme card on 6529's "The Memes" NFT collection that funds the permanent archival of the public space object catalog. The NFT artwork itself serves as the live verification client and visualization layer: it reads the archive chain, checks integrity commitments, and makes operator agreement visible.

### The Problem

The public catalog of every tracked artificial object in Earth orbit originates from a **single source**: the U.S. Space Force's 18th Space Defense Squadron, distributed via Space-Track.org. It is mirrored by **one person** (Dr. T.S. Kelso at CelesTrak.org). There is:

- No redundant historical archive with cryptographic proof of what the catalog said on any given date
- No public record of daily changes (objects added, removed, orbits altered)
- No tamper-evident chain of custody
- No guarantee of continued public access

If public GP availability is restricted, missing, or changed over time, nobody
can independently verify what changed without a daily historical record.
CelesTrak is already struggling with bandwidth (jumped from ~125 GB/day to
~330 GB/day in early 2026) and enforcing aggressive rate limiting.

### Why It Matters

- **Kessler Syndrome forensics**: Reconstructing trajectories after a collision requires the historical record
- **Accountability**: "What did we know, and when did we know it?" about orbital events
- **Institutional fragility**: CelesTrak is one person's mission. The MPC runs on 5-year NASA grants. Space-Track could restrict access at any time
- **The OMM transition**: The legacy TLE format runs out of 5-digit catalog numbers around July 2026 (~69999). Objects after that can only be represented in OMM format. This archive will be one of the few complete OMM-format historical records spanning the transition

---

## Scope

### Season 1 (Current)

**One dataset**: Space-Track GP catalog (artificial objects — satellites, rocket bodies, debris). ~50,000+ tracked Resident Space Objects (RSOs).

### Season 2 (Future, Separate NFT)

NEO (Near-Earth Object) data — CNEOS Sentry risk table, fireball/bolide data, MPC NEA orbital elements. Same architecture, separate contract, separate archive.

### Terminology Note

**Do not call artificial objects "NEOs."** NEO = Near-Earth Object = natural bodies (asteroids, comets). The Space Force catalog tracks RSOs = Resident Space Objects = artificial hardware. The project name avoids this confusion: "RSO Archive" for Season 1, potential "NEO Archive" for Season 2.

---

## Architecture

### Data Pipeline (GitHub Actions, daily)

```
Space-Track.org API
    │
    │  Bounded GP_HISTORY delta query by CREATION_DATE
    │
    ▼
Python script (zero dependencies, stdlib only)
    │
    ├── Merge against prior archived snapshot
    ├── Canonical JSON serialization (sorted keys, no whitespace)
    ├── SHA-256 hash computation
    ├── gzip compression
    ├── Current-GP visibility audit (non-consensus)
    │
    ▼
Git commit (Phase 1) → Arweave upload (Phase 2) → Ethereum event (Phase 3)
```

### Canonical Cutoff Time: 00:00:00 UTC

The daily snapshot is defined by a **fixed cutoff timestamp**, not by when the query runs. The refined design uses `00:00:00 UTC` as the canonical cutoff. A snapshot dated `2026-04-13` means "the catalog state as of `2026-04-13T00:00:00Z`."

The GitHub Action is scheduled for **00:15 UTC**, after the complete previous
UTC day has closed. The data boundary remains a clean UTC day. GitHub cron can
start late, but a near-midnight target keeps the archive closer to the cutoff
while preserving the fixed `00:00:00 UTC` consensus boundary.

**Why the change matters**: The original design tried to reconstruct each day with `gp_history CREATION_DATE/<cutoff` over the full catalog. That is mathematically clean but operationally impossible at current scale: without a lower bound, each range can ask Space-Track for decades of historical element sets and hit response limits. The refined design uses a stateful daily merge:

```text
snapshot[D] = snapshot[D-1] + bounded_gp_history_delta[D]
```

For example, the `2026-04-13` snapshot uses:

```text
base:  snapshot at 2026-04-12T00:00:00Z
delta: gp_history CREATION_DATE/2026-04-12T00:00:00--2026-04-13T00:00:00
```

The delta query is closed and bounded. Operators running at 00:15, 01:00,
08:00, 11:00, or 22:00 UTC can get the same result if they start from the same
prior consensus snapshot and Space-Track returns the same bounded history rows.

### CREATION_DATE Semantics

In `gp` and `gp_history`, `CREATION_DATE` is the publication timestamp for a GP element set row. It does **not** mean the object was launched, cataloged, or physically created at that time. Existing objects receive new `CREATION_DATE` values whenever Space-Track publishes updated public elements.

This makes `CREATION_DATE` the right field for daily deltas: a bounded window returns the element sets published during that UTC day. It does **not** return every object that was active or visible during that day. If an object does not appear in a one-day `gp_history` window, that is normal; the rolling snapshot carries forward its previous row unchanged.

`gp_history` is best treated as a publication ledger. The rolling archive uses
`CREATION_DATE` to bound the daily publication window and to decide which public
row supersedes the previous archived row. `GP_ID` and `EPOCH` are deterministic
tie-breakers. Current `gp` is valuable as an observation, but testing showed it
is not exactly reproducible from a simple ordering over `gp_history` for every
object; it should not be used as a daily consensus input.

### Genesis and Rolling State

The rolling model needs an agreed starting point. The first live day was
captured as a `genesis_from_gp` snapshot from the current `gp` endpoint, with
the exact query time and query paths recorded. From that point forward, the
canonical archive is a deterministic state machine:

1. Load the prior archived snapshot.
2. Query `gp_history` for `previous_cutoff <= CREATION_DATE < current_cutoff`.
3. Deduplicate the delta by `NORAD_CAT_ID`, selecting by `CREATION_DATE`, then `GP_ID`, then `EPOCH`.
4. Apply a delta row only if it is newer than the stored row by that same publication ordering.
5. Carry forward objects that did not receive a new public element set.
6. Sort by `NORAD_CAT_ID`.
7. Canonicalize and hash.

Historical reconstructions before genesis can still be useful, but they should
be labeled as reconstructed history rather than treated as having the same
guarantee as the live rolling archive.

A genesis snapshot is the exception to the midnight boundary: it records
`state_as_of_utc` as the actual current-`gp` observation time. The first daily
snapshot after genesis starts its bounded `gp_history` window from that observed
timestamp, then later snapshots use normal midnight-to-midnight UTC windows.

The official archive baseline date is **2026-04-20**. The baseline catalog
download was run at exactly `2026-04-20T00:00:00Z`, making that current-`gp`
observation the first consensus state. The 2026-04-13 genesis snapshot captured
during development is a rehearsal baseline only. It is useful for practicing
daily roll-forward and audit behavior during the week before launch, but it
should not be described as the permanent archive baseline.

### Current GP Is Audit Input, Not Consensus Input

The current `gp` endpoint must not be used as part of the canonical daily merge. It is retrieval-time dependent: an object present at midnight might disappear before the operator pulls current `gp`, or an object added after midnight might already be present. Using current `gp` in the hash path would make hashes depend on when the operator ran.

Current `gp` is still valuable as a visibility audit. The pipeline should query current `gp` once at the official run time. The audit records a time-sampled observation:

```text
observed_at_utc
query_path
current_gp_object_count
present_ids_sha256
missing_from_current_gp
reappeared_in_current_gp
```

Every presence or absence claim from current `gp` must include `observed_at_utc`, because it is not a closed-window fact. It is an observation made at a specific retrieval time.

### Removals and Visibility State

The canonical catalog should not remove an object merely because it is absent from current `gp`. Absence from current `gp` is not proof of orbital decay; it may indicate classification or access changes, catalog publication policy changes, transient API behavior, object identification changes, catalog renumbering or merges, source-side corrections, or something else. Until there is a deterministic removal rule, removing rows based on current absence would let retrieval-time state mutate the consensus hash.

Instead, removals and disappearances are made visible in audit artifacts. The daily audit should compare the canonical archive's `NORAD_CAT_ID` set with the current `gp` set and maintain visibility state for currently missing objects:

```text
last_gp_creation_date
last_seen_in_current_gp_audit
first_missing_in_current_gp_audit
consecutive_missing_audits
```

If an archived object is absent from current `gp`, report it as `missing_from_current_gp`. Continue checking it every day. If the object later appears again, report `reappeared_in_current_gp` and preserve the missing interval. This lets users see both the first missing date and the duration of the absence without changing the canonical snapshot rule.

Orbital decay is not treated as an explanation for disappearance from current `gp`. Space-Track can include objects with `DECAY_DATE` in current `gp`, and `gp_history` can publish `DECAY_DATE` updates as normal element-set rows. Current-`gp` absence is a separate signal. Possible causes include temporary API/catalog behavior, publication policy changes, classification or access changes, object identification changes, catalog renumbering or merges, or source-side data corrections. The archive keeps the last public row and makes the absence visible so operators can investigate it without changing the canonical hash.

The split is intentional:

| Artifact | Role | Determinism |
|----------|------|-------------|
| `catalog.json.gz` | Consensus snapshot | Deterministic from prior snapshot plus bounded delta |
| `manifest.json` | Hash/provenance metadata | Deterministic fields plus archive timestamp |
| `delta.json` | Closed-window GP_HISTORY changes | Replayable for the UTC day |
| `audit.json` | Current-GP visibility observation | Time-sampled; includes observation timestamp |
| `visibility_state.json` | Derived currently-missing state | Rebuildable from prior audits |

### The Hash IS the Consensus Object

Multiple independent operators pull the same data, serialize it canonically (sorted keys, no whitespace, ASCII-only), and compute SHA-256. If they pulled the same data, their hashes match automatically. The hash — not an Arweave URI, not a transaction ID — is what operators submit to the Ethereum contract.

**Important**: Arweave transaction IDs are NOT content-addressed. Two people uploading identical bytes get different TX IDs (the ID includes the signer's key and a nonce). IPFS CIDs are content-addressed but IPFS doesn't guarantee persistence. So: Arweave for storage, SHA-256 for consensus, Ethereum for attestation.

### Storage Layers

| Layer | Purpose | Cost | Permanence |
|-------|---------|------|------------|
| Git repo | Phase 1 working storage, code, ledger | Free (public repo) | As long as GitHub exists |
| Arweave | Permanent data archive | ~$0.12/day ($44/year) one-time | 200+ years (endowment model) |
| Ethereum L1 | Append-only hash/location attestations | Gas-dependent | Forever |
| The NFT | Verification dashboard | Hosted on Arweave | Permanent |

### Ethereum Contract Design

The contract is intentionally minimal: an append-only attestation log. It does
not publish archives, validate source data, resolve disputes, calculate TDH, or
choose the canonical hash. Verification and weighting happen in clients,
indexers, and the NFT artwork.

The current design uses one relayer-friendly write function:

```solidity
attestArchive(
    ArchiveAttestation calldata attestation,
    bytes calldata signature
)
```

Operators sign an EIP-712 `ArchiveAttestation` offchain. Anyone can submit the
signed attestation onchain: the signer, a relayer, a GitHub Action, another
operator, or a future archival service. The contract recovers the signer from
the signature and records that signer as the attester; `msg.sender` is only the
gas payer / courier.

An attestation says:

```text
For archive date D, I attest to catalog hash H, previous hash P,
manifest hash M, code version C, and optionally location U whose bundle hashes
to B.
```

Hash-only attestations use an empty URI and zero bundle hash. Hash-plus-location
attestations use the same function with a non-empty URI and nonzero bundle
hash.

The event should contain enough data for an indexer or NFT to reconstruct the
witness history:

```solidity
event ArchiveAttested(
    address indexed attester,
    address indexed submitter,
    uint32 indexed date,
    bytes32 catalogHash,
    bytes32 previousCatalogHash,
    bytes32 manifestHash,
    bytes32 codeVersionHash,
    bytes32 bundleHash,
    bytes32 uriHash,
    string uri
);
```

There is intentionally no `WeekSummary`, `finalizeWeek`, contract-level winning
hash, or weekly Merkle-root storage in v1. The contract is the raw witness log;
clients and indexers group attestations by date and hash.

**Why no on-chain validation**: Smart contracts cannot reach the internet. They can't fetch from Arweave, can't hash external data, can't verify anything outside the EVM. An oracle (like Chainlink) would reintroduce centralized trust. The verification belongs in the viewer's browser.

### Sybil Resistance: TDH Weighting

The 6529 ecosystem has **TDH (Total Days Held)** — a reputation metric computed as token holdings × days held. It's already used for governance (SZN11's first meme card was selected by TDH plurality). TDH can't be manufactured quickly because the time dimension is the Sybil defense.

**How it works**:
1. Operators or holders sign archive attestations.
2. Anyone can submit a signed attestation to the contract.
3. The contract records the recovered signer as the attester.
4. Indexers and the NFT group attestations by archive date and catalog hash.
5. The NFT looks up each attester's TDH (via seize.io API) and sums TDH per unique hash.
6. The highest-TDH-backed hash wins for display purposes.

**Attack resistance**: An attacker submitting a fake hash has low/zero TDH. The legitimate hash from established community members always outweighs it. Even if an attacker buys cards to inflate TDH, the *days held* component means they'd need months before their TDH matters. By then, the community notices.

### The NFT Verification Client

**Two components, clean separation**:

1. **The NFT itself** (HTML/JS on Arweave, rendered in iframe on 6529.io/OpenSea): Read-only verification client and visualization layer. No wallet needed. Fetches commitments from Ethereum via public RPC, fetches compact archive metadata from Arweave, verifies lightweight hashes and checkpoints in the browser, and renders the witness chain as an orbital status view. Full catalog hashing can be offered as an on-demand path because the compressed catalog is large enough that every iframe render should not decompress and hash it by default.

2. **The attestation dApp** (separate HTML page, also on Arweave): Linked from the NFT. This is where holders connect their wallet, sign an EIP-712 archive attestation, and either submit it directly or hand it to a relayer. Shows the current day's hash, lets them verify against Arweave data, one-button attest.

**Why the split**: NFT iframes on platforms like 6529.io and OpenSea are sandboxed — they can't access `window.ethereum` (wallet injection). The art is read-only by design. The dApp page lives at a separate URL (e.g., `om.pub/rso`) where wallet connection works normally.

**Viewing IS verification**: Every time someone opens the NFT, their browser independently checks the public commitments it can safely verify in that context. No wallet, no account, no trust. The more people view the art, the more eyes are on the archive chain; deeper full-data verification remains available as an intentional action.

### Daily Delta and Audit

Implemented. The daily pipeline writes two related artifacts:

1. A deterministic `delta.json` from the closed `gp_history` window.
2. A time-sampled `audit.json` from current `gp`.

`delta.json` records objects added or updated during the UTC day. Objects absent from the one-day `gp_history` window are simply carried forward; that absence is not suspicious.

`audit.json` records visibility observations: objects in the archive that are missing from current `gp` and objects that reappeared after being missing. This catches scenarios that hash-only verification misses without letting retrieval-time absence change the canonical catalog.

Future work can add richer field-level diffs and NFT visualization for daily
catalog volatility, missing objects, and reappearances.

---

## Prior Art

### Directly Relevant

- **MITRE BESTA / SNARE** (Dailey, Reed, Bryson, 2019–2020): Proposed a permissioned blockchain for international SSA data sharing. Top-down institutional framework — governments as node operators, international governing body. Research papers only, no live deployment found. Our project is bottom-up, permissionless, community-operated.

- **Jonathan McDowell / GCAT** (2020–present): The General Catalog of Artificial Space Objects. One person's decades-long effort to catalog every artificial object ever launched, under CC-BY. Manually maintained, periodically updated. McDowell said his audience is "the historian 1,000 years from now." Closest in spirit to our project. GCAT does curation; we do preservation. Complementary, not competitive.

- **Dr. T.S. Kelso / CelesTrak** (1985–present): Primary public mirror of Space-Track data. Now struggling with bandwidth and implementing aggressive rate limiting. One-person operation. The fragility of CelesTrak is part of our motivation.

### Tangentially Related

- **SpaceChain**: Launched blockchain nodes on actual satellites. Different problem — putting blockchain in space, not putting space data on blockchain.
- **Academic papers** (2020–2025): Various proposals for blockchain-enabled satellite swarms for debris tracking. All about building new observation networks, not preserving existing public data.
- **Arch Mission Foundation**: Storing Wikipedia on a satellite. Data preservation in space, not preservation of space data.

### What Doesn't Exist (Our Gap)

Nobody (we could find) is doing:
- Daily automated archival of the GP catalog to permanent decentralized storage
- Computing/publishing the daily diff of the space object catalog
- Using an NFT as both verification client and community coordination mechanism
- Using an existing NFT community's reputation (TDH) as Sybil resistance for a scientific data oracle

---

## Implementation Status

### Done (Phase 1 — Git Metadata + Bootstrap Catalogs + Release Bundles)

- [x] `pipeline/snapshot.py` — Zero-dependency Python script (stdlib only)
  - `genesis` command: captures the first agreed rolling snapshot from current `gp`
  - `daily` command: builds a rolling midnight-UTC snapshot from the prior archived snapshot plus bounded `gp_history` deltas
  - `roll-forward` command: builds rolling snapshots from an existing prior-day base snapshot
  - `replay` command: replays bounded `gp_history` from an empty state and compares the result to current `gp`
  - `verify` command: re-hashes stored snapshot and compares to manifest
  - `next-date` command: reports the next missing archive date for automated catch-up
  - `publish` command: builds deterministic release bundles and uploads them through the GitHub API with stdlib only
  - `prune-catalogs` command: keeps the newest local full catalogs and removes older ones after matching release bundles exist
  - `hydrate-catalogs` command: restores local full catalogs from release bundles when repairing or migrating a checkout
  - Canonical JSON serialization for deterministic hashing
  - gzip compression, manifest generation, running ledger, `delta.json`, `audit.json`, and `visibility_state.json`
- [x] `.github/workflows/daily-snapshot.yml` — Runs at 00:15 UTC daily and catches up missing dates
- [x] `.github/workflows/validate-archive.yml` — Read-only tests and archive validation
- [x] `README.md` with architecture overview and setup instructions
- [x] Zero external dependencies (no pip install, no requirements.txt, no required GitHub CLI)

The canonical Git tree keeps manifests, deltas, audits, visibility state,
`ledger.json`, and a rolling two-day cache of `catalog.json.gz`. Older full
catalog bytes are pruned from Git after deterministic
`rso-archive-YYYY-MM-DD.tar.gz` release bundles are built. This keeps normal
Git history small while making a fresh fork self-starting.

### Done (Phase 1.1 — Deterministic Rolling Snapshot)

- [x] Change canonical cutoff from `06:52:09 UTC` to `00:00:00 UTC`
- [x] Change GitHub Action schedule to run at `00:15 UTC`
- [x] Add explicit `genesis_from_gp` mode for the first agreed live snapshot
- [x] Change daily snapshots to `prior_snapshot_plus_bounded_gp_history_delta`
- [x] Write `delta.json` with bounded `gp_history` counts, updated IDs, new IDs, and query paths
- [x] Add current `gp` visibility audit as non-consensus `audit.json`
- [x] Add `visibility_state.json` for missing/reappeared tracking across days
- [x] Record `base_snapshot_date` and `base_snapshot_sha256` in each rolling manifest
- [x] Update tests for rolling merge, manifest metadata, URL construction, and audit state

### Done (Phase 1.2 — Replay Validation)

- [x] Capture current `gp` as an end-state reference
- [x] Replay bounded 24-hour `gp_history` windows from `2026-01-01T00:00:00Z` to the current observation time
- [x] Compare replayed state to current `gp`
- [x] Quantify objects missing from replay, missing from current `gp`, and record-level mismatches
- [x] Decide whether a Jan 1 delta-only replay is sufficient for historical baseline analysis

Replay result: bounded 24-hour `gp_history` queries worked without the
unbounded-query Space-Track failure. The Jan 1-to-current empty-state replay
processed 8.35M history rows across 103 windows and reconstructed 31,412
objects, all of which were still present in current `gp`. Of those shared
objects, 31,310 byte-matched current `gp` and 102 had record-level differences.
The replay did not reconstruct 35,640 current `gp` objects, because many
long-lived objects had no public `gp_history` row in the 2026 replay window.
Therefore a delta-only replay from Jan 1 is useful validation, but it is not a
complete historical baseline. The live archive needs a `genesis_from_gp`
snapshot, then deterministic bounded deltas from that point forward.

The replay also showed that current `gp` does not always byte-match a simple
latest-publication reconstruction from `gp_history`. The canonical archive
therefore preserves its own deterministic rule: latest public `gp_history`
publication by `CREATION_DATE`, not retrieval-time current `gp` behavior.

### To Build (Phase 2 — Arweave)

- [ ] Arweave upload step in pipeline (via Irys/Bundlr CLI or arweave-js)
- [ ] Arweave TX ID recorded in manifest and ledger
- [ ] Pipeline uploads compressed snapshot to Arweave after git commit

### To Build (Phase 3 — Ethereum)

- [ ] Solidity contract: one `attestArchive` function plus `ArchiveAttested` events
- [ ] Contract deployment to Ethereum mainnet
- [ ] Pipeline step: generate EIP-712 attestation payloads after archive publication
- [ ] Optional relayer path for sponsored submission of signed attestations
- [ ] Contract verified on Etherscan, immutable, no owner functions

### To Build (Phase 4 — NFT)

- [ ] NFT artwork (HTML/JS): orbital ring visualization, 30-day rolling view
- [ ] Reads attestation index generated from contract events
- [ ] Verifies selected `ArchiveAttested` events with targeted RPC calls
- [ ] Fetches data from Arweave, re-hashes client-side
- [ ] Four-source cross-check display per day (Space-Track, CelesTrak, Arweave, Ethereum)
- [ ] TDH lookup for weighting (via seize.io API)
- [ ] Verification sequence animation on load
- [ ] Detail panel on click (hash, Arweave TX, confirmer count, TDH backing)

### To Build (Phase 5 — Confirmation dApp)

- [ ] Standalone HTML page (hosted on Arweave or om.pub/rso)
- [ ] Wallet connect (MetaMask/Rabby/WalletConnect)
- [ ] Shows current day's hash computed from Arweave data
- [ ] EIP-712 attestation signing and submission or relay handoff
- [ ] Displays confirmer leaderboard (TDH-weighted)

### To Build (Phase 6 — Rich Diff and Audit Visualization)

- [x] Delta summary: objects added/updated/carried-forward vs previous day
- [x] Audit computation: missing from current `gp`, reappeared in current `gp`
- [ ] Rich field-level diff computation for changed objects
- [ ] Diagnostic metadata for unusual missing-object cases, if practice data shows it is needed
- [ ] Include diff/audit summary in offchain index and NFT view
- [ ] NFT visualization layer for diff/audit activity

---

## Technical Decisions & Rationale

### Why Ethereum mainnet, not an L2

The project's thesis is "no single point of failure." Base L2 is operated by Coinbase — a corporate dependency. Ethereum mainnet has no single operator. Attestation costs depend on gas and participation, but relayed signatures let the signer and gas payer be different addresses. Philosophical consistency matters for credibility.

### Why not ZK proofs

ZK is for proving things without revealing underlying data. This data is public by design — transparency is the point. A Merkle chain (each day's record includes the previous day's hash) provides tamper evidence more simply and cheaply. ZK adds circuit compilation complexity, proof generation infrastructure, and trusted setup for zero benefit here.

### Why Arweave, not IPFS

IPFS is content-addressed (same data = same CID) but doesn't guarantee persistence — data disappears when no one pins it. Arweave is pay-once-store-forever with an endowment model that funds storage for 200+ years. For a permanent archive, Arweave's persistence guarantee is essential.

### Why the hash is the consensus object, not the Arweave TX ID

Arweave TX IDs include the signer's key and nonce — two people uploading identical data get different IDs. SHA-256 of the canonical JSON is deterministic — everyone who pulls the same data gets the same hash. Operators submit hashes to the contract; the Arweave TX ID is just a pointer to where the data lives.

### Why CelesTrak can't be used for live confirmation

CelesTrak's GP data updates roughly every 2 hours. A confirming holder opening the NFT hours after the operator's pull would get different data from CelesTrak (updated since capture). This breaks hash comparison. Instead, confirmers verify the Arweave copy (immutable, identical bytes every time) against the on-chain hash. Operators verify source-to-archive; confirmers verify archive integrity.

### Why the contract doesn't validate on-chain

Smart contracts can't reach the internet. They can't fetch Arweave data or compute hashes of external content. An oracle would reintroduce centralized trust. The contract is a dumb append-only ledger. The NFT's JavaScript is the smart verification layer — thousands of independent browsers are a better oracle than any single service.

### Why append-only attestation events

Events are permanent on Ethereum and are the natural shape for an append-only
witness log. Querying them over large block ranges requires pagination, so
indexers can publish compact JSON read models for the NFT. The NFT can browse
the index and use targeted RPC calls only when a viewer wants to verify a
specific attestation event.

---

## File Structure

```
rso-archive/
├── .github/
│   └── workflows/
│       ├── daily-snapshot.yml    # Cron: 00:15 UTC daily
│       └── validate-archive.yml  # Read-only validation
├── pipeline/
│   └── snapshot.py               # The entire pipeline (zero deps)
├── data/
│   └── {YYYY}/{MM}/{DD}/
│       ├── manifest.json         # Hash, object count, metadata
│       ├── catalog.json.gz       # Retained only for the newest two archive days
│       ├── delta.json            # Closed-window GP_HISTORY changes applied
│       ├── audit.json            # Current-GP visibility observation
│       └── visibility_state.json # Missing/reappeared state for this day
├── ledger.json                   # Running hash ledger (all dates)
├── reports/
│   └── rehearsal/                # Pre-baseline rehearsal artifacts
├── README.md
├── CONTEXT.md                    # This file
└── .gitignore
```

Full daily catalogs are published in GitHub Release bundles and later move to
Arweave permanent storage. The newest two full catalogs are also retained in
Git as a bootstrap cache.

### Design Lesson: Self-Starting Forks

A rolling archive needs the prior full catalog bytes, not only the prior hash.
If all full catalogs are pruned from Git, a new fork inherits the metadata
chain but may need an external release bundle before its first daily run.

The current design keeps the newest two `catalog.json.gz` files in the repo:

- `ledger.json` and `manifest.json` provide the public hash chain.
- The newest retained catalog lets the next daily run start from local state.
- The second retained catalog gives one extra day of cushion for delayed or
  retried runs.
- Older full catalogs move to release bundles and later permanent storage.

This makes the normal operator path self-starting: fork the repo, enable
Actions, add Space-Track secrets, and the scheduled workflow can catch up and
continue the chain without manual bootstrapping.

---

## Key URLs & Resources

- **Space-Track.org**: https://www.space-track.org (requires free account)
- **Space-Track API docs**: https://www.space-track.org/documentation
- **CelesTrak**: https://celestrak.org
- **6529 The Memes**: https://6529.io/the-memes
- **6529 FAQ**: https://6529.io/about/faq
- **6529 Delegation Contract**: https://github.com/6529-Collections/nftdelegation
- **MITRE BESTA paper**: https://www.mitre.org/sites/default/files/2021-11/prs-20-2645-blockchain-enabled-space-traffic-awareness-BESTA-discovery-of-anomalous-behavior-supporting-automated-space-traffic-management.pdf
- **GCAT**: https://planet4589.org/space/gcat/
- **Arweave fees**: https://ar-fees.arweave.net/
- **OMM format spec**: CCSDS 502.0-B-3

---

## Mantras

- The community is the infrastructure
- The art is the dashboard
- The meme is the message
- Every UTC day, the record is cut at midnight
- Every day after midnight UTC, the operators make the record visible
- Ship the pipeline this week — every day not archived is a day lost forever

---

*Last updated: 2026-04-13. Generated from conversation between Brook and Claude (Opus 4.6) designing the RSO Archive project for 6529 The Memes, then refined with Codex for the rolling snapshot and visibility audit model.*
