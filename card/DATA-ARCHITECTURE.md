# RSO viewer — data architecture (built to last 5–20+ years)

The viewer needs two very different things:

1. **Per-day "card metadata"** for the *whole* timeline — date, object count, LEO/MEO/GEO
   split, content fingerprint, attestation status — cheap enough to scrub instantly.
2. **The full daily catalog** (~11 MB) — only for the day you actually stop on (click-to-inspect).

…and it must keep working long after any given web host is gone.

## The three tiers

### Tier 0 — Permanence (the source of truth, forever)
- **Ethereum (DocChain contract).** Every daily snapshot is a `DocAttested` event keyed by
  `docRef` (the UTC day), carrying `contentHash`, `blockHash`, attester(s), and a publication
  URI. This is the canonical, censorship-resistant, decades-proof index: anyone can rebuild the
  entire timeline (every day + its content hash + how attested it is) by reading the contract's
  event log through *any* Ethereum RPC / archive node / subgraph. As long as Ethereum exists, the
  index exists.
- **Arweave.** Each day's bundle (and a tiny manifest) is content-addressed and stored
  pay-once-store-forever. The `contentHash`/locator in the on-chain event resolves to it. No
  dependency on GitHub or any single server.

Everything else is *derivable* from Tier 0. This is what makes the piece "forever."

### Tier 1 — Fast metadata index (for scrolling)
- A compact, **chunked-by-year** index of per-day card metadata:
  `{date, objectCount, bandCounts:{leo,meo,geo}, sha256, blockHash, attested, tdh}`
  ≈ 100–150 bytes/day → ~50 KB/year → trivial for centuries.
- Stored on **Arweave** (permanent) with a tiny top-level `manifest` listing the chunks + the
  latest day. Its current head is pinned via the contract (latest attestation URI) or a mutable
  name (ENS / ArNS) so the viewer can always find "now."
- The viewer loads the manifest + only the year-chunk(s) covering the **visible** scroll range
  (lazy). Boot is O(1), not O(timeline).
- This replaces today's single growing `ledger.json` **and** the faked band proportions with
  real per-day band counts.

### Tier 2 — Heavy catalog (on demand)
- The full daily catalog bundle from Arweave, fetched only when you stop on a day. Powers
  click-to-inspect of real objects. (Already implemented.)

## The NFT must be self-sufficient
For an on-chain artwork, **embed a baseline index snapshot at mint time** (the chunked metadata
as-of-mint, inlined) so the piece renders the full archive-to-date even if every gateway is down.
Live data (Tier 1 head + Tier 2 bundles) layers on top when reachable. Config = the **contract
address + a list of fallback RPCs + Arweave gateways**, so no single provider's death breaks it.
The viewer HTML and Three.js are already inlined/vendored for this reason.

## Where the prism faces come from (and what to precompute)

The bottom-left info prisms surface per-day metadata. The goal: **every face loads instantly
for the whole timeline**, with the heavy catalog reserved purely for click-to-inspect.

| Prism face | Source today | Should live in the index? |
|---|---|---|
| Objects · total | `ledger.object_count` ✅ already up-front | yes (already there) |
| Objects · by altitude (LEO/MEO/GEO) | computed from the 11 MB catalog on stop | **add `band_counts`** |
| Objects · by type (payload/RB/debris/unknown) | computed from the catalog on stop | **add `type_counts`** |
| Objects · updated this doc-day | `delta.json` (separate per-day fetch) on stop | **add `delta` summary** |
| Fingerprint · SHA-256 | `ledger.sha256` ✅ | yes (already there) |
| Fingerprint · provenance / source | `ledger.provenance` / `ledger.source` ✅ | yes (already there) |
| Fingerprint · bundle size / format | `ledger.compressed_bytes` / `ledger.format` ✅ | yes (already there) |
| Fingerprint · on-chain block / TDH | doc-chain index (loaded up-front) ✅ | yes (already there) |

**Verdict:** the fingerprint prism is already fully index-backed. Only **three small
aggregates** are missing for the objects prism — and they're a dozen integers per day. They
should **all** go in the index, not be gated behind the 11 MB download. Don't do "first face from
the index, rest from the catalog" — the catalog is for inspecting individual objects, nothing
else.

### Index entry schema (per day)
The pipeline already has the full catalog in hand when it builds each snapshot, so emitting these
is essentially free:

```jsonc
{
  "date": "2026-06-08",
  "object_count": 67915,
  "sha256": "96a0…fb39",
  "provenance": "rolling_gp_history_delta",
  "source": "space-track.org",
  "format": "OMM/JSON",
  "compressed_bytes": 11430000,
  "on_orbit_count": 34055,   // ADD — still up there (no decay date, or decay in the future)
  "reentered_count": 33860,  // ADD — already came down (decay date in the past)
  "band_counts": { "leo": 61593, "meo": 4462, "geo": 1860 },          // of the on-orbit set
  "type_counts": { "payload": 24805, "rocket": 6539, "debris": 34374, "unknown": 1900, "tba": 200 }, // on-orbit
  "delta": { "updated": 29240, "new": 4, "decayed": 1 },
  "anno_summary": { "directory_changes": 68, "tip_count": 4, "decay_notices": 4 }, // lens-4 legend, instant
  "content_schema": "rso-core-v1",          // self-describing consensus-hash schema
  "catalog_url": "https://…/catalog.json.gz", "catalog_url_kind": "catalog_gz"  // CORS bundle locator
}
```

> Note: `band_counts` / `type_counts` describe **what's on orbit** (decayed records are recorded
> in the catalog but counted separately as `reentered_count`). The viewer ghosts re-entered
> objects in the field and headlines the on-orbit number, since that's what people care about.

The viewer consumes `on_orbit_count`/`reentered_count`, `band_counts`, `type_counts`, `delta`, and
`anno_summary` straight off the index entry — every prism face **and** the daily-changes
decay/directory/forecast counts are instant for the whole timeline with **zero** catalog downloads.
For any day the index hasn't backfilled yet it falls back to catalog-computed (exact on stop) and a
proportional estimate (while scrubbing). `content_schema` is carried so a card booting from the
index alone still labels the consensus-hash face correctly.

### Chunk it by year
Put these entries in **year-chunk files** (`index/2026.json`, `index/2027.json`, …) on Arweave,
with a tiny top-level manifest listing the chunks + the latest day. The viewer lazy-loads only the
year(s) in view. ~150 bytes/day → ~55 KB/year → trivial for centuries; boot stays O(1).

## Recommended pipeline changes
1. Emit per-day **band counts** (and any card metadata) into the index, so the LEO/MEO/GEO split
   is exact for every day **without** downloading the bundle.
2. Publish the index as **year-chunks on Arweave** (not just GitHub raw), with a manifest + a
   mutable head pointer.
3. Resolve the timeline from **Arweave / the contract first**; treat GitHub raw as a dev
   convenience only (it is not permanent and has CORS limits).

## What the viewer does today (implemented)
- **Boot:** fetches the lean Tier-1 index — `index/manifest.json` (whose `latestEntry` inlines the
  newest day's full aggregate) + the current year-chunk `index/YYYY.json` — through a ranked,
  fall-through list of backing **nodes**, then **boots to the latest witnessed day** with exact
  numbers on the first frame. Year-chunks are cached in `localStorage`, validated by the manifest's
  content hash, so a return trip is instant and offline. `ledger.json` remains only as a legacy
  fallback for a node that hasn't published the lean index; the doc-chain index still layers on
  attestations.
- **Aggregates:** counts, on-orbit/re-entered split, `band_counts`, `type_counts`, `delta` and
  `anno_summary` all come straight from the index entry — every prism face and the daily-changes
  decay/directory/forecast counts are exact for the whole timeline with **zero** catalog downloads.
  Un-backfilled days fall back to a proportional estimate that becomes exact on stop.
- **Source rank:** the settings "index source priority" is a draggable stack-rank of nodes — the top
  one serves every fetch, any failure falls through to the next. It is **ranked by on-chain TDH
  backing by default** (derived from the attestation index's per-node `nodeBackingTdh`); the visitor
  can drag to override (persisted), **paste their own node** (`owner/repo`) to take an arbitrary one,
  or reset to TDH. The list is extended by the network's published roster
  (`indexer/generated/nodes.json`, carrying each node's `tdh`, sorted by TDH).
- **Source safety:** every node — from config, the roster, a paste, or `localStorage` — passes one
  gate (`sanitizeNode`): `owner/repo` and branch names are regex-validated (no `..`, one slash), so
  the only reachable host is `raw.githubusercontent.com`. All fetched payloads are read with
  `JSON.parse` only (never `eval`/`Function`), labels are escaped before they touch the DOM, and the
  list is capped — a hostile roster can at worst add more GitHub-raw fetch targets, nothing else.
- **Status:** honest — *live · <node>* when the index is serving, *aggregates live · bundle offline*
  when the per-object catalog can't be reached (the HUD numbers are still real), *embedded archive*
  when every node is dark.
- **Bundles (Tier 2, click-to-inspect only):** the index entry's `catalog_url` first (Arweave bundle
  tar when permanent, else the node-branch `catalog.json.gz`), then any witness's signed Arweave
  mirrors, then the ranked nodes' raw catalog — never the GitHub release asset (no CORS).
