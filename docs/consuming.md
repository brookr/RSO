# Consuming the Archive

Machine-readable pointers let any consumer — an API partner, the
visualization layer, a mirror — fetch and verify archive data without ever
constructing asset URLs or parsing file names. Every node publishes the same
interface at the same paths, so you can source from any node (or several,
for failover) and verify the bytes regardless of which one served them.

## The latest day

```
https://raw.githubusercontent.com/{node}/RSO/node/latest.json
```

```json
{
  "schema": "rso-latest-v1",
  "date": "2026-06-10",
  "tag": "rso-archive-2026-06-10",
  "asset_name": "rso-archive-2026-06-10.tar.gz",
  "asset_url": "https://github.com/.../rso-archive-2026-06-10.tar.gz",
  "arweave_tx": "…",                  // present only once the Arweave tx is CONFIRMED (mined) — a pending tx's ar:// URL 404s, so it is never advertised
  "bundle_sha256": "…",               // fingerprint of the bundle bytes
  "sha256": "…",                      // raw catalog (artifact integrity)
  "content_schema": "rso-core-v1",
  "content_sha256": "…",              // the consensus hash, attested on-chain
  "annotations_sha256": "…",
  "conjunctions_sha256": "…",         // present once the day captures CDMs
  "adopted_from": "OMPub/RSO",        // present only on days this node mirrored from an upstream node (not its own capture)
  "object_count": 67917,
  "generated_at_utc": "…"
}
```

Download from `asset_url` (or `ar://{arweave_tx}`), then verify:
`sha256(bundle) == bundle_sha256`, and optionally re-derive
`content_sha256` from the catalog inside (see
[the chain profile](profile.md), §6) and compare it with the on-chain
attestation. You are then serving witnessed data, not just mirrored bytes.

When you consume the observation artifacts (`annotations.json`,
`conjunctions.json`) forensically — "what did the network know, when?" —
weigh each node's claims by its **commitment time**, not by the
`observed_at_utc` inside the files: the attestation index annotates every
event with `attestedAtUtc` and `attestationLagDays` from its block
timestamp, which is the unforgeable part. A day witnessed at lag 0 proves
the knowledge existed that day; a re-attestation of history proves only
that the bytes existed when it landed.

## Any past day

The full day list, with hashes and the same publication fields per entry:

```
https://raw.githubusercontent.com/{node}/RSO/node/ledger.json
```

Each entry carries `date`, `sha256`, `content_sha256`, `asset_url`,
`arweave_tx` (when uploaded), and `bundle_sha256` — enough to build a date
picker and verified downloads from one fetch. Per-day detail (storage
destinations, verification provenance) lives at the deterministic path:

```
https://raw.githubusercontent.com/{node}/RSO/node/data/YYYY/MM/DD/storage.json
```

## Why pointers instead of URLs

Asset names never carry versions and published bundles are never rewritten,
but storage can evolve (new locations, future hosts). The pointers absorb
all of it: your integration reads `latest.json`/`ledger.json` and never
changes. Trust comes from the hashes and the chain, availability from
whichever node you prefer.
