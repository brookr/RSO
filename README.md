# Orbital Witness: RSO Archive

**A daily, independently witnessed, publicly verifiable archive of the public
space object catalog.**

Every day, decentralized operator nodes pull the public Resident Space Object catalog from
Space-Track, roll it forward from the prior snapshot, hash the canonical bytes,
and publish evidence anyone can verify.

## 10-Second Version

- **Why bother:** public orbital data should have durable, independently
  verifiable memory outside any single service
- **What's preserved:** historical snapshots of tracked artificial objects in Earth orbit
- **How:** many fork-based nodes run the same zero-dependency pipeline and
  compare SHA-256 hashes
- **Current output:** Git metadata on each `node` branch plus deterministic
  GitHub Release bundles
- **Permanent layers:** Arweave permanence, automatic DocChain attestations,
  and NFT-based verification/visualization

## Start Here

| Goal | Read |
|------|------|
| Understand the public value | [docs/faq.md](docs/faq.md) |
| Set up a node | [docs/setup.md](docs/setup.md) |
| Operator and sweeper model | [docs/operator.md](docs/operator.md) |
| Understand the architecture | [docs/architecture.md](docs/architecture.md) |
| Understand the snapshot rules | [docs/snapshot-spec.md](docs/snapshot-spec.md) |
| The chain profile (normative spec) | [docs/profile.md](docs/profile.md) |
| The chain: consensus core + observation log | [docs/chain.md](docs/chain.md) |
| Join late and attest from genesis in one tx | [docs/late-join.md](docs/late-join.md) |
| Build attestations | [docs/attestation-design.md](docs/attestation-design.md) |
| Verify a daily archive | [docs/verification.md](docs/verification.md) |
| Consume the archive (pointers, latest.json) | [docs/consuming.md](docs/consuming.md) |
| Develop locally | [docs/development.md](docs/development.md) |
| Catch up a node | [docs/roll-forward.md](docs/roll-forward.md) |
| Learn the Science | [docs/glossary.md](docs/glossary.md) |

## The Core Idea

The public catalog comes primarily from one source, Space-Track (US Space
Force), and is made more accessible by individual volunteer efforts like
[CelesTrak](https://celestrak.org/) and [KeepTrack.space](https://keeptrack.space/).
Those services are valuable. RSO Archive depends on Space-Track and does not
replace any of them.

Public access is not the same as durable public memory. A daily, reproducible
archive gives future users a stable artifact to cite, mirror, compare, and
verify, even if live APIs, formats, accounts, or institutional priorities
change.

Orbital Witness fixes that by making the archive a reproducible public process:

```text
prior snapshot + bounded Space-Track GP_HISTORY delta
        |
        v
canonical JSON catalog
        |
        v
SHA-256 hash + manifest + ledger + release bundle
```

If independent nodes produce the same daily hash, the record is being witnessed,
not merely hosted.

## Current Status

Live archive baseline:

```text
2026-04-20
```

Current implementation:

- zero runtime dependencies; Python stdlib only
- daily scheduled GitHub Actions workflow
- fork-safe `main` / `node` branch model
- rolling catalog snapshots from bounded `gp_history`
- current-GP visibility audits
- deterministic GitHub Release bundles
- optional Arweave upload with nonfatal failure receipts

## Branch Model

- `main`: code, docs, workflows, and controller logic
- `node`: generated archive state, including `data/`, `ledger.json`, retained
  bootstrap catalogs, generated reports, and storage receipts

Forks should include all branches so the upstream `node` bootstrap state is
copied. After that, do not use GitHub's **Sync fork** button as normal
maintenance; the daily workflow updates `main` from upstream and applies the
code to `node` without overwriting node-generated archive state.

See [docs/setup.md](docs/setup.md) for the exact setup path, and
[docs/operator.md](docs/operator.md) for the operator overview.

## What A Daily Run Publishes

On the operator's `node` branch:

- `data/YYYY/MM/DD/manifest.json`
- `data/YYYY/MM/DD/annotations.json`
- `data/YYYY/MM/DD/delta.json`
- `data/YYYY/MM/DD/audit.json`
- `data/YYYY/MM/DD/visibility_state.json`
- `data/YYYY/MM/DD/storage.json`
- `ledger.json`
- `catalog.json.gz` for the newest two archived days

As a release asset:

- `rso-archive-YYYY-MM-DD.tar.gz`

The consensus object is the **core projection hash** (`content_sha256`): the
SHA-256 of the canonical catalog with the nine mutable object-directory fields
excluded, because Space-Track back-patches those in place on published rows.
The raw catalog (all 39 fields, exactly as returned) stays the archival
artifact, and `annotations.json` records what the node learned about the
mutable fields and when. See [docs/chain.md](docs/chain.md) for the
measurements behind the split. The release URL, storage URI, and Arweave
transaction ID are never part of consensus; attestations sign the exact bundle
SHA-256 so mirrors can be checked byte-for-byte.

## Quick Operator Path

1. Create a free [Space-Track.org](https://www.space-track.org/auth/createAccount)
   account.
2. Fork this repo with all branches. Leave GitHub's **Copy the main branch
   only** option unchecked.
3. Enable GitHub Actions and workflow write access.
4. Add `SPACETRACK_USER` and `SPACETRACK_PASS` as Actions secrets.
5. Run **Validate RSO Archive**.
6. Enable and manually run **Daily RSO Snapshot** once.
7. Optional: add a disposable no-funds EOA secret so the same workflow
   publishes signed DocChain attestations for sweepers.

Detailed instructions: [docs/setup.md](docs/setup.md).

## Quick Verify

```bash
python3 pipeline/snapshot.py verify --date 2026-05-01
```

More verification options: [docs/verification.md](docs/verification.md).

## Project Docs

- [Architecture](docs/architecture.md)
- [Snapshot specification](docs/snapshot-spec.md)
- [Attestation design](docs/attestation-design.md)
- [Verification](docs/verification.md)
- [Development](docs/development.md)
- [Roadmap](docs/roadmap.md)
- [Prior art and acknowledgments](docs/acknowledgments.md)
- [Full design background](docs/background.md)

## License

CC0 1.0 Universal

*The community is the infrastructure. The art is the dashboard. The meme is the
message.*
