# Orbital Witness: RSO Archive

**Permanent, decentralized, tamper-evident archive of the public space object catalog.**

Orbital Witness is a public-good mission to preserve important public datasets
through independently operated, cryptographically verifiable archives. The goal
is a durable historical record that does not depend on any single maintainer,
platform, institution, or storage provider.

This repository is the first witness: the daily
Resident Space Object (RSO) catalog. The same shape can later be reused for
near-Earth objects (NEO), conjunction events, launch records, reentry
observations, or any other public space dataset where provenance matters.

The project values simple and auditable infrastructure: zero runtime code
dependencies, reproducible hashes over authority, fork-based operation,
pseudonymous operators, and community confirmation through the 6529 network's
TDH reputation signal for Sybil resistance.

Every day, operator nodes pull the public source data, roll the catalog forward
from the prior snapshot, hash the canonical result, and publish matching
evidence for anyone to verify.

Verification can be done through an interactive digital artwork (NFT). Attestations 
from invididual verifications are recorded to Ethereum as a permanent public record 
of the state of the archive.

---

## What this does

Builds a daily General Perturbations (GP) catalog snapshot from [Space-Track.org](https://www.space-track.org), computes a canonical SHA-256 hash, and stores the snapshot permanently.

The refined snapshot model is stateful: each daily catalog is derived from the
prior archived catalog plus a bounded `gp_history` delta window. This avoids
unbounded historical queries while keeping the snapshot deterministic for
operators who start from the same prior consensus snapshot.

The catalog currently tracks **50,000+** resident space objects (RSOs): active satellites, defunct spacecraft, rocket bodies, and debris.

For terminology and field definitions, see [GLOSSARY.md](GLOSSARY.md).

If you want to operate an independent Orbital Witness node to strengthen the
decentralization of the network, start with [OPERATOR.md](OPERATOR.md). It
explains what you need and what a first successful run should look like.

## Why

The public space object catalog originates from a single source (U.S. Space Force, 18th Space Defense Squadron) and is mirrored by a single individual (at [CelesTrak](https://celestrak.org)). No decentralized source provides cryptographic proof of what the catalog said on any given date.

If public GP availability is restricted, missing, or changed over time, nobody
can independently verify what changed.

Orbital Witness fixes that.

## Architecture

```
Dataset Source ──────────────→ Witness Pipeline ──→ SHA-256 Hash
     │                                  │
     ▼                                  ▼
Space-Track GP_HISTORY            RSO Archive
                         │                  │
                         ▼                  ▼
                    Arweave (permanent)  Ethereum (attestation)
                         │                  │
                         └────────┬─────────┘
                                  ▼
                         NFT Client (verify + visualize)
```

**Phase 1 (current):** Daily metadata is archived to Git with SHA-256 hashes on
each operator's `node` branch. The default `main` branch stays focused on code,
docs, and workflow controllers so forks can keep pulling upstream code without
overwriting their own generated archive state. The two most recent full
catalogs are kept on `node`; older full catalog bytes are published as
deterministic GitHub Release bundles.

**Phase 2:** Optional Arweave permanent storage during publish, with Ethereum
on-chain attestation next.

**Phase 3:** Dynamic NFT artwork on 6529 The Memes that acts as the
verification client and visualization layer: it reads archive commitments from
Arweave and Ethereum, checks integrity in the browser where practical, and
shows the archive chain, operator agreement, and community witness status.
TDH-weighted community confirmations make participation measurable without
turning the NFT iframe into a wallet dApp.

**Phase 4:** Orbital Witness expansion to other datasets: NEO, conjunction events, fireball observations, etc.

Future Orbital Witness archives should keep the same operator experience:
bounded source queries, canonical JSON, reproducible hashes, public manifests,
read-only validation, and eventually permanent storage plus onchain
attestation. RSO comes first because the catalog is public, important, and
fragile enough to justify the machinery.

The NFT is not just a badge for the project. It is the public verification
client and visualization layer: an HTML/JS artwork that can read the permanent
archive, compare published commitments, and make the health of the witness
network visible. The heavier wallet-confirmation flow can live in a separate
page so the NFT stays portable across sandboxed marketplaces and collection
sites.

## Join the Operators

An operator is the person or group running an independent Orbital Witness node
to strengthen the decentralization of the network. The fork plus workflows are
the node itself. The goal is simple: start from the same agreed baseline, run
the same code, publish the same daily hashes, and make drift visible.

The branch layout keeps code sync separate from generated archive output:

- `main`: upstream-tracking code, docs, workflows, and controller logic
- `node`: the running node state, including `data/`, `ledger.json`, generated
  reports, release receipts, and retained bootstrap catalogs

The daily workflow runs from `main`, updates `main` from upstream by default,
merges current code into `node` while preserving generated node state, and then
processes the daily catalog on `node`.

The resilience comes from many independent operators, not from one blessed
server. If one GitHub account, one workflow, one maintainer, or one future
storage provider disappears, other operators still have the code, the data, the
ledger, and the hashes. Matching hashes across independent forks are the signal
that the public record is being witnessed, not merely hosted.

For a detailed beginner-friendly setup path, read [OPERATOR.md](OPERATOR.md).

The short version:

- Create a free [Space-Track.org](https://www.space-track.org/auth/createAccount)
  account. You'll be emailed a link to confirm your account and set your
  password.
- Fork this repo into your own GitHub account or organization, including all
  branches. In GitHub's fork form, leave **Copy the main branch only**
  unchecked so your fork receives the `node` bootstrap branch.
- Enable GitHub Actions and workflow write access on your fork.
- Add `SPACETRACK_USER` and `SPACETRACK_PASS` as repository secrets.
- Run **Validate RSO Archive** first. Then enable and run **Daily RSO Snapshot**
  once manually to prove the scheduled producer workflow is active in your
  fork.

The live archive already has an agreed genesis day:

```text
2026-04-20
```

New operators normally do not create a fresh genesis snapshot. They validate
the existing lineage and then continue it.

There are two GitHub workflows:

- **Validate RSO Archive** — tests on `main`; full archive validation on `node`
- **Daily RSO Snapshot** — scheduled producer workflow with code sync, node
  branch preparation, and automatic catch-up

Roll-forward remains available as a repository script for maintainers who need
to catch up a node from an existing prior snapshot. See
[ROLL_FORWARD.md](ROLL_FORWARD.md) for details:

```bash
python pipeline/snapshot.py roll-forward --start 2026-04-21 --end 2026-04-23
```

Each successful producer run writes to four places:

- Git metadata on `node`: `data/YYYY/MM/DD/` plus `ledger.json`
- Git bootstrap cache on `node`: `catalog.json.gz` for the two newest archived days
- Release bundle in that fork: `rso-archive-YYYY-MM-DD.tar.gz`
- Publish receipt on `node`: `data/YYYY/MM/DD/storage.json`

The daily `sha256` is computed from the canonical snapshot bytes, not from a
release URL, storage URI, or upload location. Different operators can publish
the same snapshot bytes at different locations and still agree on the same
daily hash.

The default producer settings are:

```text
STORAGE_BACKEND=github_release
UPLOAD_POLICY=if_missing
RSO_NODE_BRANCH=node
RSO_UPSTREAM_REPO=OMPub/RSO
RSO_AUTO_UPDATE_CODE=true
```

Standalone operators can set `RSO_AUTO_UPDATE_CODE=false` as a repository
variable if they want their node to run only the code they maintain locally.
Operators who want workflow-controller files to self-update can add a
fine-grained `RSO_WORKFLOW_UPDATE_TOKEN` secret with Contents write and
Workflows write. Without that token, normal pipeline code updates still work,
but upstream changes under `.github/workflows/` may produce a warning and
require clicking GitHub's **Sync fork** button on `main`.

If `ARWEAVE_JWK` is present in the environment, the publish step also submits
the same deterministic bundle to Arweave and records the resulting transaction
ID in `storage.json`. The canonical daily hash remains the snapshot `sha256`,
not the Arweave transaction ID. Bundles at or below `12 MiB` go as one inline
transaction; larger bundles automatically switch to Arweave chunk upload. Set
`ARWEAVE_FORCE_CHUNK_UPLOAD=true` to force chunk mode for testing.

The workflow schedule target is:

```text
00:15 UTC
```

GitHub schedules may start late. The canonical data cutoff remains midnight
UTC.

## Archive Schedule

The official archive baseline is **2026-04-20**. The baseline catalog download
was run at exactly `2026-04-20T00:00:00Z` and recorded current `gp` as the
first agreed full catalog state. Daily consensus snapshots after that are built
from bounded `gp_history` deltas.

The `2026-04-13` through `2026-04-19` snapshots were rehearsal artifacts. On a
node branch, they live under `reports/rehearsal/` so `data/` and `ledger.json`
represent only the official archive lineage.

## Snapshot Specification

| Field | Value |
|-------|-------|
| Source | Space-Track.org GP_HISTORY class |
| Format | OMM/JSON |
| Snapshot cutoff | 00:00:00 UTC daily |
| Operator run time | Scheduled for 00:15 UTC; GitHub may start later |
| Canonical source | Prior archived snapshot plus bounded `gp_history` delta |
| Sort | NORAD_CAT_ID ascending after merge |
| Hash | SHA-256 of canonical JSON (sorted keys, no whitespace) |
| Compression | gzip level 9 |
| Provenance | `genesis_from_gp` or `rolling_gp_history_delta` |

The snapshot cutoff is fixed at midnight UTC. A snapshot dated `2026-04-13`
represents the catalog state as of `2026-04-13T00:00:00Z`. The producer must run
after that cutoff so the complete previous 24-hour UTC publication window can
be queried.

For daily operation, the pipeline starts from the previous archived snapshot and
queries only the bounded history interval:

```text
previous_cutoff <= CREATION_DATE < current_cutoff
```

For example, the `2026-04-13` snapshot uses:

```text
base:  snapshot at 2026-04-12T00:00:00Z
delta: gp_history CREATION_DATE/2026-04-12T00:00:00--2026-04-13T00:00:00
```

Within the delta, the pipeline selects the latest published row per
`NORAD_CAT_ID` by `CREATION_DATE`, then `GP_ID`, then `EPOCH`, and applies it to
the base snapshot only if it is newer than the stored row by that same ordering.
`CREATION_DATE` controls both whether a row is inside the bounded publication
window and which public row supersedes the previous archived row. Objects that do not appear in the
bounded delta are carried forward unchanged. Absence from a one-day
`gp_history` window is normal; it only means Space-Track did not publish a new
public element set for that object during that UTC day.

`CREATION_DATE` is the publication timestamp for a GP element set row. It is not
the launch date or object creation date. Existing objects receive new
`CREATION_DATE` values whenever Space-Track publishes updated public elements.

The canonical snapshot must not use current `gp` as an input to the daily merge,
because current `gp` is retrieval-time dependent and is not exactly
reconstructible from a simple public ordering over `gp_history` in all cases.
Current `gp` is useful as a genesis input and as an audit observation.

### Genesis Snapshot

The rolling model needs an agreed starting point. The first live day was
captured as a `genesis_from_gp` snapshot from current `gp`, with the exact query
time and query paths recorded. From that point forward, daily snapshots are
deterministic state transitions:

```text
snapshot[D] = snapshot[D-1] + bounded_gp_history_delta[D]
```

Historical reconstructions before genesis can still be useful, but they should
be labeled as reconstructed history rather than treated as having the same
guarantee as the live rolling archive.

A genesis snapshot records `state_as_of_utc` as the actual current-`gp`
observation time. The first daily snapshot after genesis starts its bounded
`gp_history` window from that observed timestamp, then later snapshots use
normal midnight-to-midnight UTC windows.

### Visibility Audit

Removals and disappearances are intentionally not allowed to mutate the
canonical catalog unless a deterministic removal rule is later defined. Instead,
the pipeline stores observation-time audit artifacts beside the consensus
snapshot.

The daily audit queries current `gp` once at the official run time. It records:

```text
observed_at_utc
query_path
current_gp_object_count
present_ids_sha256
missing_from_current_gp
reappeared_in_current_gp
```

Every presence or absence claim from current `gp` must include the audit
timestamp, because it is a time-sampled observation rather than a closed-window
fact.

The audit should also keep visibility state for currently missing objects:

```text
last_gp_creation_date
last_seen_in_current_gp_audit
first_missing_in_current_gp_audit
consecutive_missing_audits
```

If an archived object is absent from current `gp`, the audit reports it as
`missing_from_current_gp`. The object remains in the canonical snapshot, making
the disappearance visible without making the hash depend on retrieval-time
absence. If the object later appears in current `gp` again, the audit records a
`reappeared_in_current_gp` event and preserves the missing interval.

Orbital decay is not treated as an explanation for disappearance from current
`gp`. Space-Track can and does include objects with `DECAY_DATE` in current
`gp`, and `gp_history` can publish `DECAY_DATE` updates as normal element-set
rows. A missing current-`gp` row is therefore a separate signal. Possible
causes include temporary API/catalog behavior, publication policy changes,
classification or access changes, object identification changes, catalog
renumbering or merges, or source-side data corrections. The archive keeps the
last public row and makes the absence visible so operators can investigate it
without changing the canonical hash.

## Local Developer Quick Start

### Prerequisites

- Python 3.10+
- Free [Space-Track.org](https://www.space-track.org/auth/createAccount) account

### Local Usage

```bash
# Set credentials in your CLI, or source from .env
export SPACETRACK_USER="your@email.com"
export SPACETRACK_PASS="your-password"

# Capture the first agreed rolling snapshot from current gp
python pipeline/snapshot.py genesis

# Official baseline day, captured on 2026-04-20
python pipeline/snapshot.py genesis --date 2026-04-20

# Build today's rolling snapshot from yesterday's archived snapshot
python pipeline/snapshot.py daily

# Build or rebuild a specific date
python pipeline/snapshot.py daily --date 2026-04-13
python pipeline/snapshot.py daily --date 2026-04-12 --force

# Validation experiment; findings are documented in REPLAY_FINDINGS.md.
python pipeline/snapshot.py replay --start 2026-01-01

# Verify a stored snapshot
python pipeline/snapshot.py verify --date 2026-04-12

# On a node branch, validate every archived snapshot, manifest, ledger entry,
# delta, and audit.
python pipeline/snapshot.py validate

# Show the next date this checkout should archive
python pipeline/snapshot.py next-date

# Build and publish deterministic release bundles for archived days
python pipeline/snapshot.py publish --date 2026-04-18
python pipeline/snapshot.py publish --start 2026-04-13 --end 2026-04-18

# Keep only the newest two full catalogs in Git after bundles exist
python pipeline/snapshot.py prune-catalogs --all --keep-latest 2 --require-bundle

# Repair a checkout by restoring the newest two catalogs from release bundles
python pipeline/snapshot.py hydrate-catalogs --latest 2 --repo OMPub/RSO
```

Useful operational knobs:

```bash
# Defaults: range size 10000, max catalog id 339999, minimum objects 40000.
# For long replay/roll-forward runs, use a larger delay to stay well below API caps.
# Satcat metadata for missing objects is queried in batches of 500 by default.
RSO_REQUEST_DELAY=12.5 python pipeline/snapshot.py replay --start 2026-01-01
```

## Data Structure

The archive tree below lives on a node branch, not on the code-only `main`
branch.

```
data/
├── 2026/
│   ├── 01/
│   │   ├── 01/
│   │   │   ├── manifest.json      # Hash, object count, metadata
│   │   │   ├── catalog.json.gz    # Kept only for the newest two archive days
│   │   │   ├── delta.json         # Bounded gp_history changes applied
│   │   │   ├── audit.json         # Time-sampled current-gp visibility audit
│   │   │   └── visibility_state.json
│   │   ├── 02/
│   │   │   └── manifest.json
│   │   └── ...
│   └── ...
└── ledger.json                     # Running hash ledger (all dates)
```

The two newest full catalogs live directly in `data/` on `node` so a fresh fork
can read the previous snapshot without first owning any release assets. Older
full catalog bytes live in release assets named `rso-archive-YYYY-MM-DD.tar.gz`.
Each bundle contains `catalog.json.gz`, `manifest.json`, any daily audit/delta
artifacts, and a deterministic `release-manifest.json`.

### Manifest Example

```json
{
  "date": "2026-04-12",
  "cutoff_utc": "2026-04-12T00:00:00Z",
  "state_as_of_utc": "2026-04-12T00:00:00Z",
  "sha256": "a1b2c3d4e5f6...",
  "object_count": 50847,
  "raw_bytes": 48293847,
  "compressed_bytes": 8234561,
  "provenance": "rolling_gp_history_delta",
  "format": "OMM/JSON",
  "source": "space-track.org",
  "pipeline_version": "0.3.0",
  "query_strategy": "prior_snapshot_plus_bounded_gp_history_delta",
  "base_snapshot_date": "2026-04-11",
  "base_snapshot_sha256": "9f8e7d6c5b4a...",
  "delta_window_start_utc": "2026-04-11T00:00:00Z",
  "delta_window_end_utc": "2026-04-12T00:00:00Z",
  "api_query_base": "https://www.space-track.org/basicspacedata/query",
  "api_query_paths": [
    "/class/gp_history/CREATION_DATE/2026-04-11T00:00:00--2026-04-12T00:00:00/orderby/NORAD_CAT_ID%20asc,CREATION_DATE%20desc/format/json"
  ],
  "archived_at": "2026-04-12T00:15:12.456789+00:00"
}
```

## Verification

Anyone can verify a snapshot independently:

1. Download the release bundle for a given date
2. Extract and decompress `catalog.json.gz`
3. Compute SHA-256 of the raw bytes
4. Compare against `manifest.json`, `release-manifest.json`, or `ledger.json`

For the newest retained days, step 1 is optional because `catalog.json.gz` is
already present in Git.

The storage location is not part of the consensus hash. The point of the bundle
or later Arweave URI is to retrieve the bytes; the bytes themselves are what
hash to the daily `sha256`.

```bash
# Quick verify. If catalog.json.gz is not local, this fetches the release bundle.
python pipeline/snapshot.py verify --date 2026-04-23
```

```bash
# Manual verify
tar -xzf rso-archive-2026-04-23.tar.gz catalog.json.gz
gunzip -k catalog.json.gz
sha256sum catalog.json
```

## Tests

The project intentionally has no external dependencies.

```bash
python -m unittest discover -s tests
```

## Roadmap

- [x] Daily rolling snapshot pipeline
- [x] Roll forward from an existing prior-day base snapshot
- [x] Local hash verification
- [x] Refactor daily snapshots to midnight UTC rolling `gp_history` deltas
- [x] Add current `gp` visibility audit and missing/reappeared state
- [x] Analyze Jan 1-to-current replay results against current `gp`
- [x] Publish deterministic GitHub Release bundles and retain a two-day Git bootstrap cache
- [x] Optional Arweave upload during publish, with per-day storage receipts
- [ ] Ethereum contract for hash attestation
- [ ] Daily diff computation (objects added/updated/carried-forward)
- [ ] TDH-weighted community confirmations
- [ ] Dynamic NFT artwork (6529 The Memes): verification client and visualization layer
- [ ] Orbital Witness template for additional datasets
- [ ] NEO witness archive

## Prior Art & Acknowledgments

- **MITRE BESTA** (Dailey, Reed, Bryson, 2019–2020) — Established the conceptual case that blockchain and space situational awareness belong together.
- **Matt Scobel / [Eyes on Earth](https://foundation.app/mint/eth/0x3B3ee1931Dc30C1957379FAc9aba94D1C48a5405/95439)** — Beautiful cryptoart visualizing true orbits of satellites, of a 24h time period.
- **Dr. T.S. Kelso / CelesTrak** — Decades of making GP data accessible when nobody else would.
- **Jonathan McDowell / GCAT** — Proving one person's determination can preserve the space record. "My audience is the historian 1,000 years from now."
- **18th Space Defense Squadron / Space-Track** — Making the data public in the first place.
- **6529 / The Memes / Open Metaverse** — Building the community infrastructure (TDH, delegation, the open metaverse thesis) that makes this approach possible.

## License

CC0 1.0 Universal

---

*The community is the infrastructure. The art is the dashboard. The meme is the message.*
