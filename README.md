# Orbital Witness: RSO Archive

**A daily, independently witnessed, tamper-evident archive of the public space
object catalog.**

Every day, operator nodes pull the public Resident Space Object catalog from
Space-Track, roll it forward from the prior snapshot, hash the canonical bytes,
and publish evidence anyone can verify.

## 10-Second Version

- **What:** historical snapshots of tracked artificial objects in Earth orbit
- **Why:** today's public catalog has fragile centralized access and weak public
  history
- **How:** many fork-based nodes run the same zero-dependency pipeline and
  compare SHA-256 hashes
- **Current output:** Git metadata on each `node` branch plus deterministic
  GitHub Release bundles
- **Next layers:** Arweave permanence, Ethereum attestations, and NFT-based
  verification/visualization

## Start Here

| Goal | Read |
|------|------|
| Run your own node | [OPERATOR.md](OPERATOR.md) |
| Understand the architecture | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| Understand the snapshot rules | [docs/SNAPSHOT_SPEC.md](docs/SNAPSHOT_SPEC.md) |
| Verify a daily archive | [docs/VERIFICATION.md](docs/VERIFICATION.md) |
| Develop locally | [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) |
| Catch up a node | [ROLL_FORWARD.md](ROLL_FORWARD.md) |
| Learn the terms | [GLOSSARY.md](GLOSSARY.md) |

## The Core Idea

The public catalog comes from one source, Space-Track, and is mirrored publicly
by one long-running individual effort, CelesTrak. If public GP availability is
restricted, edited, missing, or changed over time, independent historical proof
is hard.

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

See [OPERATOR.md](OPERATOR.md) for the exact setup path.

## What A Daily Run Publishes

On the operator's `node` branch:

- `data/YYYY/MM/DD/manifest.json`
- `data/YYYY/MM/DD/delta.json`
- `data/YYYY/MM/DD/audit.json`
- `data/YYYY/MM/DD/visibility_state.json`
- `data/YYYY/MM/DD/storage.json`
- `ledger.json`
- `catalog.json.gz` for the newest two archived days

As a release asset:

- `rso-archive-YYYY-MM-DD.tar.gz`

The consensus object is the catalog SHA-256, not the release URL, storage URI,
or Arweave transaction ID.

## Quick Operator Path

1. Create a free [Space-Track.org](https://www.space-track.org/auth/createAccount)
   account.
2. Fork this repo with all branches. Leave GitHub's **Copy the main branch
   only** option unchecked.
3. Enable GitHub Actions and workflow write access.
4. Add `SPACETRACK_USER` and `SPACETRACK_PASS` as Actions secrets.
5. Run **Validate RSO Archive**.
6. Enable and manually run **Daily RSO Snapshot** once.

Detailed instructions: [OPERATOR.md](OPERATOR.md).

## Quick Verify

```bash
python3 pipeline/snapshot.py verify --date 2026-05-01
```

More verification options: [docs/VERIFICATION.md](docs/VERIFICATION.md).

## Project Docs

- [Architecture](docs/ARCHITECTURE.md)
- [Snapshot specification](docs/SNAPSHOT_SPEC.md)
- [Verification](docs/VERIFICATION.md)
- [Development](docs/DEVELOPMENT.md)
- [Roadmap](docs/ROADMAP.md)
- [Prior art and acknowledgments](docs/ACKNOWLEDGMENTS.md)
- [Full design background](background.md)

## License

CC0 1.0 Universal

*The community is the infrastructure. The art is the dashboard. The meme is the
message.*
