# The RSO Doc Chain: Consensus Core and Observation Log

Every archived day is hashed and attested on Ethereum. The protocol is built
around one principle:

> **Consensus covers only what is reproducible. Observations are recorded per
> node, with the time we learned them.**

## Why the hash is a projection

Hashing every field of every record would assume the source never edits
published rows. A field-by-field re-query of 50 archived windows (2026-06-09)
measured the opposite:

- Space-Track **mutates object-directory fields in place** on already-published
  `gp_history` rows. Across 1,371,193 record-observations, 152 records changed
  (0.011%) -- every one an in-place field mutation on the same `GP_ID`, never a
  selection change.
- Only the object-directory family mutates: `DECAY_DATE` (118 stamps, up to
  **7,224 days** after the fact) and the naming triplet
  `OBJECT_NAME`/`OBJECT_TYPE`/`TLE_LINE0` (35 each, TBA -> assigned), with the
  rest of the family (`RCS_SIZE`, `COUNTRY_CODE`, ...) churning through fresh
  elsets by the same mechanism.
- With all fields hashed, **0 of 50** archived day hashes could be reproduced
  from a fresh query. With the mutable family excluded, **50 of 50** reproduced
  exactly.
- No settling delay fixes this: decay stamps have no time horizon.
- The gp window capture alone is nearly blind to decay knowledge: of 118 decay
  stamps in the coverage window, the capture caught **2** (decayed objects
  publish no further elsets, so a stamp lands on a row no later window
  revisits).

Two independent nodes querying the same window hours apart forked on exactly
this mechanism during testing. The field partition removes the fork class
entirely; the observation log turns the churn into recorded knowledge.

## The field partition

Every raw record keeps all 39 OMM fields exactly as the API returned them.
The partition only affects what is hashed:

- **Core (30 fields, hashed):** elset identity and orbital state -- `GP_ID`,
  `NORAD_CAT_ID`, `CREATION_DATE`, `EPOCH`, mean elements, drag terms,
  `TLE_LINE1`, `TLE_LINE2`, format/provenance fields. Measured immutable once
  published.
- **Observation (9 fields, excluded):** the object-directory family --
  `COUNTRY_CODE`, `DECAY_DATE`, `LAUNCH_DATE`, `OBJECT_ID`, `OBJECT_NAME`,
  `OBJECT_TYPE`, `RCS_SIZE`, `SITE`, `TLE_LINE0`. Excluded by *mechanism*
  (Space-Track back-fills directory attributes), not just by observed incident.

```text
content_sha256 = SHA-256( canonical JSON of records minus the 9 excluded fields )
```

The raw catalog hash (`sha256`) stays in the manifest as artifact integrity;
the chain attests `content_sha256` (`manifest.content_schema: rso-core-v1`).

## The observation plane

Each day also publishes `annotations.json` -- what this node learned about the
mutable fields, with the time of recording:

- `catalog_changes`: per-object diffs of the 9 fields between consecutive raw
  catalogs (`previous` -> `current`, `first_observation` for new objects),
  stamped with `observed_at_utc`.
- `satcat_changes`: Space-Track's own catalog change log for the window
  (`satcat_change`, windowed on `CHANGE_MADE`).
- `decay_messages`: the `decay` class for the window (windowed on
  `MSG_EPOCH`). Reentries reach the archive the next day even though decayed
  objects publish no elsets.

Annotations are per-node and eventually consistent -- two honest nodes may
hold different observations for the same day. They are signed into each node's
publication locator (the attestation `uri`), so they are chain-committed
per-node without perturbing blockHash agreement. `manifest.annotations_sha256`
fingerprints the artifact.

## Chain identity

- Profile URI (permanent protocol id): `https://om.pub/rso/doc-chain`
- `docChainId = keccak256(profile URI)` =
  `0x6011620b5a3faa23f8078c2af0bb1a87bb85a68f784abdf3dbae67939c399bea`
- Contract: DocChain (supports single and batch attestation), Sepolia
  `0x867FcC4f0339009043E9F6e554DD516Bcf1bcaa9`
- Genesis: docRef `20260420000000`, zero parentHash.

The id is deliberately unversioned: protocol revisions are described in
metadata and documentation, never baked into the permanent identifier.

## Verifying the chain from nothing

Any party can re-derive every contentHash from the source:

1. Fetch the genesis raw catalog (anchor artifact; its bytes are attested and
   published).
2. For each day, query `gp_history` for `CREATION_DATE` in
   `[D-1 00:00Z, D 00:00Z]`, apply the published selection rule
   (`filter_creation_window` + `dedupe_latest_per_object`), and fold into the
   prior state.
3. Project out the 9 excluded fields, canonicalize, hash.

This full from-source replay has been executed against the complete chain:
every day reproduced, including days whose raw captures differed between
nodes. See [late-join.md](late-join.md) for doing this as a joining node.

## The guardrail

The weekly **drift audit** (`pipeline/drift_audit.py`) re-queries a sliding
sample of archived windows and classifies every difference. Excluded-field
drift is expected and logged. Any non-excluded-field mutation or selection
drift -- the signals that would threaten consensus -- fails the run and opens
a repository issue. The partition is an empirical claim; the audit is its
continuous test.
