# Snapshot Specification

> **Note:** the attested contentHash is the core projection
> (`content_sha256`) — the canonical catalog minus the nine mutable
> object-directory fields. The raw-catalog `sha256` described below is
> unchanged and remains the artifact-integrity hash. See
> [chain.md](chain.md).

The RSO archive is a deterministic rolling state machine. Each daily snapshot
is derived from the previous archived catalog plus a bounded Space-Track
`gp_history` publication window.

## Daily Snapshot

| Field | Value |
|-------|-------|
| Source | Space-Track.org GP_HISTORY class |
| Format | OMM/JSON |
| Snapshot cutoff | 00:00:00 UTC daily |
| Operator run time | Scheduled for 00:15 UTC; GitHub may start later |
| Canonical source | Prior archived snapshot plus bounded `gp_history` delta |
| Sort | `NORAD_CAT_ID` ascending after merge |
| Hash | SHA-256 of canonical JSON, sorted keys, no whitespace |
| Compression | gzip level 9 |
| Provenance | `genesis_from_gp` or `rolling_gp_history_delta` |

A snapshot dated `2026-05-01` represents the catalog state as of
`2026-05-01T00:00:00Z`.

## Rolling Rule

```text
snapshot[D] = snapshot[D-1] + bounded_gp_history_delta[D]
```

For a normal daily run:

```text
previous_cutoff <= CREATION_DATE < current_cutoff
```

Within the delta, the pipeline selects the latest published row per
`NORAD_CAT_ID` by `CREATION_DATE`, then `GP_ID`, then `EPOCH`, and applies it to
the base snapshot only if it is newer than the stored row by that same
ordering.

Objects that do not appear in the bounded delta are carried forward unchanged.
Absence from a one-day `gp_history` window is normal; it only means Space-Track
did not publish a new public element set for that object during that UTC day.

## CREATION_DATE

`CREATION_DATE` is the publication timestamp for a GP element set row. It is
not the launch date or object creation date. Existing objects receive new
`CREATION_DATE` values whenever Space-Track publishes updated public elements.

The archive uses `CREATION_DATE` for two things:

- deciding whether a row falls inside the bounded daily publication window
- deciding which public row supersedes the previous archived row

## Genesis

The official archive baseline date is `2026-04-20`.

The first live day was captured as a `genesis_from_gp` snapshot from current
`gp`, with the exact query time and query paths recorded. From that point
forward, daily snapshots are deterministic bounded-delta transitions.

Historical reconstructions before genesis can be useful, but they should be
labeled as reconstructed history rather than treated as having the same
guarantee as the live rolling archive.

## Current-GP Audit

Current `gp` is not a consensus input for daily snapshots because it is
retrieval-time dependent. It is used as an audit observation.

The daily audit records:

```text
observed_at_utc
query_path
current_gp_object_count
present_ids_sha256
missing_from_current_gp
reappeared_in_current_gp
```

Every presence or absence claim from current `gp` must include the audit
timestamp. The archive keeps absent objects in the canonical snapshot and makes
the disappearance visible in audit artifacts instead of letting retrieval-time
absence mutate the consensus hash.

## Data Tree

Archive state lives on a `node` branch, not on the code-only `main` branch.

```text
data/
└── YYYY/
    └── MM/
        └── DD/
            ├── manifest.json
            ├── catalog.json.gz
            ├── delta.json
            ├── audit.json
            ├── visibility_state.json
            └── storage.json

ledger.json
```

Only the newest two full `catalog.json.gz` files are retained in Git. Older
full catalogs live in deterministic release bundles named
`rso-archive-YYYY-MM-DD.tar.gz`.

## Manifest Fields

Daily manifests include:

- `date`
- `cutoff_utc`
- `state_as_of_utc`
- `sha256`
- `object_count`
- `raw_bytes`
- `compressed_bytes`
- `provenance`
- `format`
- `source`
- `pipeline_version`
- `query_strategy`
- `base_snapshot_date`
- `base_snapshot_sha256`
- `delta_window_start_utc`
- `delta_window_end_utc`
- `api_query_base`
- `api_query_paths`
- `archived_at`

### Card-parity aggregates

Manifests (and the ledger entries derived from them) also carry a small
observation-plane aggregate so consumers can render the "what's up there" HUD
with **zero catalog download**. All are derived from the recorded catalog (plus
the day's `delta.json` for `updated`/`new`) and mirror `card/index.html` exactly:

- `on_orbit_count` — objects still on orbit on this date
- `reentered_count` — objects whose `DECAY_DATE` calendar day is `<=` this date
  (so `on_orbit_count + reentered_count == object_count`)
- `band_counts` — `{leo, meo, geo}` over the on-orbit objects, by `MEAN_MOTION`
  (GEO `0.9..1.1`, LEO `>=11.0`, else MEO)
- `type_counts` — `{payload, rocket, debris, unknown, tba}` over the on-orbit
  objects, by `OBJECT_TYPE` substring
- `delta` — `{updated, new, decayed}`; `updated`/`new` from the bounded
  `gp_history` delta, `decayed` = objects whose `DECAY_DATE` is exactly this date

These are observation-plane, like the full `sha256` (`DECAY_DATE` is excluded
from the consensus `content_sha256`): they may differ between nodes capturing the
same day at different times. They are **optional** — manifests archived before
the aggregates existed stay reproducible — and re-derive from the catalog under
`validate` (catalog↔manifest) and from the manifest under `validate_ledger`
(manifest↔ledger). `rebuild-content` backfills them onto already-archived days
without re-deriving annotations (so published bundle bytes stay identical).

## Tier-1 Index

A lean, year-chunked aggregate timeline, separate from the heavy `ledger.json`,
that a browser reads instantly. Built with `build-index` from the archived
manifests + storage receipts:

```
index/
  YYYY.json        # array of lean per-day entries for that year
  manifest.json    # { schema, generated_at_utc, repo, branch, day_count,
                   #   first, latest, chunks:[{year,path,sha256,count,first,last}],
                   #   latestEntry:{...full aggregate for the head day...} }
```

Each `index/YYYY.json` entry carries only `date`, `object_count`,
`on_orbit_count`, `reentered_count`, `band_counts`, `type_counts`, `delta`,
`sha256`, `content_sha256`, `provenance`, `compressed_bytes`, plus a
**CORS-fetchable catalog locator**: `catalog_url` (+ `catalog_url_kind`,
`bundle_tar` for a confirmed Arweave tx, else `catalog_gz` for the node-branch
`catalog.json.gz`). The GitHub release asset is never the locator — its redirect
target serves no CORS headers, so a browser cannot read it.

Mirrors: the node branch (GitHub-raw) is the convenience mirror, committed each
run; `build-index --arweave` (with `ARWEAVE_JWK`) additionally uploads the
chunks + manifest to Arweave for permanence (opt-in — it spends AR).

The card's embedded `BASELINE` is this index inlined as-of-mint (compact keys
`d,c,s,cs,sc,pv,by` + aggregates `oo,re,bc,tc,dl` + locator `cu,ck`), regenerated
with `build-baseline --inject card/index.html` so the piece boots fully offline
to its mint-day head with exact numbers.
