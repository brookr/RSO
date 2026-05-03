# RSO Operator Guide

This guide is for anyone who wants to operate RSO infrastructure. It covers
*why* you'd do it, the two roles available, and the operational expectations
attached to each. The mechanical first-time setup — fork the repo, enable
Actions, secrets, etc. — lives in [`setup.md`](setup.md).

Questions and requests for clarification are welcome.

## Why operate

The public space-object catalog is important, but fragile:

- One source publishes it: Space-Track (relies on government funding and staff).
- One person mirrors it publicly at CelesTrak.
- Without multiple independent archives, later edits or removals are hard
  to prove.

Operating RSO infrastructure strengthens the network's decentralization. If
many independent operators run the same daily snapshot logic and arrive at
the same hash chain, the archive is being *witnessed*, not merely hosted.

## Two roles, both called "operator"

There are two flavours of operator, and they're independent:

- **Node operator.** Runs the daily archive snapshot from a fork of this
  repo. Strengthens publication independence — every node is another
  party producing the same canonical archive bytes from the same source.
- **Relayer operator.** Runs an off-chain web service that pays gas for
  RSO Meme Card holders' attestations. Strengthens attestation
  accessibility — holders can confirm publications without paying for
  every transaction themselves.

Most operators run only one role. A node fork is a small daily GitHub
Actions job; a relayer is a long-running web service with a hot wallet.
They share no code.

For attestation protocol details, see
[`attestation-design.md`](attestation-design.md).

## What success looks like

### Node operator

A healthy node run produces five visible things:

1. A green workflow run in your fork's **Actions** tab
2. A `node` branch in your fork
3. A new daily metadata folder under `data/YYYY/MM/DD/` on `node`
4. An updated `ledger.json` on `node`
5. A release asset named `rso-archive-YYYY-MM-DD.tar.gz`

If you also add an Arweave wallet secret, the same publish step records an
extra `storage.json` receipt showing the GitHub Release location and Arweave
transaction ID for that day.

For a normal daily snapshot, the committed day folder should contain:

- `manifest.json`
- `delta.json`
- `audit.json`
- `visibility_state.json`

The latest two archived days also keep `catalog.json.gz` in Git on the `node`
branch. That small rolling cache lets the workflow read the prior full
catalog directly from the fork before it has published any of its own
release bundles.

The real success condition is matching hashes across forks for the same
date — same `ledger.json` hash, same `manifest.json` hash, same
`object_count`. Independent operators reaching the same numbers is the
witnessing.

### Relayer operator

A healthy relayer:

- accepts EIP-712-signed `ArchiveAttestation` payloads over HTTPS
- runs the full validation pipeline from
  [`attestation-design.md`](attestation-design.md): signature recovery,
  URI fetch with SSRF guards, hash matching, decompression caps, duplicate
  and quota checks
- submits accepted attestations to the mainnet contract from a hot wallet
- never charges the attestor for rejections; rejection reasons go back to
  the dApp

If validation fails, the relayer returns a structured error and pays no
gas. If validation passes, the on-chain `ArchiveAttested` event is the
public receipt.

## Branches (node operators)

The branch split on a node fork is intentional:

- `main`: code, docs, workflows, and the lightweight controller action
- `node`: the running node state, including `data/`, `ledger.json`,
  generated reports, release receipts, and the latest two retained full
  catalogs

By default, the daily workflow first updates `main` from upstream
`OMPub/RSO`, then merges the latest code into `node` while preserving
node-generated state. That gives normal operators daily code updates
without overwriting their own archive outputs. Standalone operators can
disable this by setting the repository variable
`RSO_AUTO_UPDATE_CODE=false`.

After creating the fork, do **NOT** use GitHub's "**Sync fork**" button as
a normal maintenance habit. The daily workflow already updates your fork's
`main` from upstream and applies that code to your `node` branch without
overwriting node-generated state. Manual fork syncs can be useful for rare
workflow-controller updates, but do them deliberately and only on `main`;
never use a sync or reset operation that overwrites your `node` branch.

## Funding models (relayer operators)

The contract is neutral and immutable: it has no admin role, no upgrade
path, and no concept of relayer identity. Anyone can run a relayer. The
only curated lever is who funds the hot wallet.

**Self-funded.** You fund your own hot wallet, set your own caps, and
decide who you'll sponsor (specific allowlist, all card holders, only your
community, only yourself). You can take your relayer down whenever you
want. No project obligations.

**Treasury-funded.** OW tops up your hot wallet on the agreed schedule. In
exchange, your relayer goes on the upstream-distributed list — so the
average dApp user sees it — and you accept the operational expectations
below. This is the curated subset; getting added is a conversation, not an
automatic process.

Both models share the same protocol checks. The only thing that differs is
who's paying for gas and who's setting the policy.

## Operational expectations (treasury-funded relayers)

These apply to relayers on the treasury-funded list. Self-funded relayers
can ignore them, but most are good practice anyway.

- **Reachability.** A monitoring URL the project can poll, and a contact
  channel for incidents (Discord handle, email, anything that gets
  answered).
- **Uptime.** No formal SLA, but sustained outages mean the dApp routes
  around you and treasury support follows.
- **Log retention.** Keep your own logs of accepted and rejected requests
  long enough to debug incidents. The on-chain event stream is the public
  record; your private logs are for ops.
- **Capacity caps.** Configure the daily/hourly/per-wallet caps per the
  attestation design, and don't quietly raise them. If you need more
  headroom, coordinate first.
- **Pause readiness.** An emergency pause switch that takes the relayer
  out of service quickly. Rare, but useful when something is clearly
  wrong.
- **Public-facing identity.** A short page describing who runs the relayer
  (handle, project, contact) so users know who they're trusting. Optional
  but appreciated.
- **Canonical code.** Treasury-funded relayers should run the project's
  canonical relayer codebase rather than a private reimplementation;
  that's how the project verifies the validation pipeline is intact.

## Hot wallet sizing and rotation

Treat the hot wallet as a daily allowance, not a permanent balance:

- The ceiling is whatever amount the funder is willing to lose if the key
  is compromised between top-ups. For treasury-funded wallets this is set
  by OW and reviewed periodically.
- Top-ups happen in the hour before 00:00 UTC so the next day's sponsored
  capacity is available right at the boundary.
- The wallet should never exceed the ceiling. If you find yourself
  manually adding more during the day, your caps are wrong, not your
  ceiling.

Roughly:

```text
daily ceiling  ≈  daily attestation cap  ×  gas per attestation  ×  gas-price ceiling
```

If gas spikes above your assumed ceiling, your hourly cap should pause
sponsorship until the spike subsides — preferable to draining the wallet
early in the day.

Plan for rotation from the start. Treasury-funded relayers rotate on a
documented schedule (TBD); self-funded relayers should still rotate
periodically and after any signal of compromise:

- generate the new key offline
- pre-fund the new wallet before the swap
- update the relayer config, drain the old wallet
- let any in-flight transactions settle before disabling the old key

Rotation is also the response to small anomalies: an unexpected balance
dip, an unrecognised outbound transaction, an unplanned config change. The
cost of rotating is low; the cost of running on a maybe-compromised key is
the whole ceiling.

## What gets a relayer defunded

Treasury funding can be paused or revoked if the relayer:

- pays gas on attestations that fail post-hoc validation (i.e., your
  validation pipeline is broken)
- censors valid sponsored requests without cause
- shows signs of key compromise or unauthorized hot-wallet drain
- exceeds budget caps repeatedly without coordinating
- becomes unreachable to project operators

Defunding is reversible: fix the underlying issue, propose re-inclusion,
return to the curated set. Defunding does not remove your relayer from the
dApp's published list — that's a separate upstream code-update question.

## Getting added to the upstream relayer list

The published relayer list lives in the dApp source. Adding a new entry is
an upstream PR like any other code change, plus two extras:

- evidence the relayer is running (live URL, monitoring page)
- agreement on who's funding the hot wallet (self vs. treasury) and, if
  treasury, alignment on caps and rotation schedule

Once merged, the new endpoint propagates to operator forks on the next
daily upstream sync — no manual action required from existing operators.

A relayer can also exist *off* the upstream list: an operator can add
their own relayer to their fork's copy and serve their own users without
any upstream coordination. Blessed-list inclusion is not a precondition
for operating.

## Where to look when you are lost

- [`setup.md`](setup.md): mechanical first-time path
- [`../README.md`](../README.md): full technical walkthrough and command
  reference
- [`glossary.md`](glossary.md): orbital-data terms and field definitions
- [`attestation-design.md`](attestation-design.md): the attestation
  protocol — authoritative for relayer behaviour
- [`snapshot-spec.md`](snapshot-spec.md): bundle layout and canonical
  hashing rules
- [`architecture.md`](architecture.md): how the pieces fit together
- `node` branch on your fork: your generated archive state
- `data/YYYY/MM/DD/manifest.json`: the daily hash and provenance summary
- `ledger.json`: rolling public hash chain
- `Releases`: where your fork publishes full daily bundles

If anything in this guide disagrees with the protocol design docs, the
design docs win.
