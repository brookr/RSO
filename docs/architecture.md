# Architecture

Orbital Witness preserves public datasets through independently operated,
cryptographically verifiable archive nodes. This repository is the first
witness: the daily Resident Space Object (RSO) catalog from Space-Track.

## Pipeline

```text
Space-Track GP_HISTORY
        |
        v
Witness pipeline
        |
        +--> canonical JSON catalog
        +--> SHA-256 hash
        +--> manifest, delta, audit, ledger
        |
        +--> GitHub Release bundle
        +--> optional Arweave upload
        +--> future Ethereum attestation
```

Every day, operator nodes pull bounded public source data, roll the catalog
forward from the prior snapshot, hash the canonical result, and publish
evidence that anyone can verify.

## Branch Model

The branch split keeps code sync separate from generated archive output:

- `main`: code, docs, workflows, and controller logic
- `node`: the running archive node state, including `data/`, `ledger.json`,
  generated reports, release receipts, and retained bootstrap catalogs

The daily workflow runs from `main`, updates `main` from upstream by default,
merges current code into `node` while preserving generated node state, and then
processes the daily catalog on `node`.

This lets forks keep receiving upstream code updates without overwriting their
own archive history.

## Storage Layers

| Layer | Purpose |
|-------|---------|
| Git `node` branch | Public metadata, ledger, latest bootstrap catalogs |
| GitHub Releases | Deterministic full daily bundles |
| Arweave | Optional permanent bundle storage |
| Ethereum | Planned append-only hash attestation log |
| NFT client | Planned verification and visualization dashboard |

The daily consensus object is the SHA-256 of the canonical catalog bytes, not a
release URL, Arweave transaction ID, or storage location. Different operators
can publish identical bytes in different places and still agree on the same
daily hash.

## Current Phase

Phase 1 is live:

- daily rolling snapshot pipeline
- Git metadata and ledger
- newest two full catalogs retained on `node`
- deterministic GitHub Release bundles
- optional Arweave upload with per-day `storage.json`

Planned phases:

- Ethereum attestation contract
- dynamic NFT verification artwork
- richer daily diff and audit visualization
- reusable witness template for other datasets

## Self-Starting Forks

A rolling archive needs the prior full catalog bytes, not only the prior hash.
Each node branch keeps the newest two `catalog.json.gz` files:

- the newest retained catalog lets the next daily run start from local state
- the second retained catalog gives cushion for delayed or retried runs
- older full catalogs move to release bundles and later permanent storage

For the normal operator path, fork the repo with all branches so the upstream
`node` branch is copied. If a fork copies only `main`, the daily workflow can
still create `node` and import archive state from upstream `node` on its first
run.
