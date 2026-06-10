# RSO Doc Chain Profile

**Profile URI (permanent protocol id):** `https://om.pub/rso/doc-chain`
**Profile revision:** 2 — 2026-06-10
**Canonical source:** this file (`docs/profile.md`) in the OMPub/RSO repository;
the page served at the profile URI mirrors the current revision.

This document is the source of truth for everything the RSO chain defines on
top of the generic [Doc Chain](https://github.com/OMPub/doc-chain) protocol.
The profile URI is deliberately unversioned: `docChainId = keccak256(profile
URI)` is the chain's permanent identity, and **every versioned rule in this
profile lives in metadata** (this document plus per-day manifests), never in
the identifier. Protocol revisions update this document and the metadata; they
never mint a new chain.

## 1. Identity

| | |
|---|---|
| docChainId | `keccak256("https://om.pub/rso/doc-chain")` = `0x6011620b5a3faa23f8078c2af0bb1a87bb85a68f784abdf3dbae67939c399bea` |
| docRef | the archive day as a uint64, `YYYYMMDD000000` (UTC) |
| Genesis | docRef `20260420000000`, `parentHash = 0x00…00` |
| parentHash | the previous day's blockHash (EIP-712 `hashStruct(DocBlock)`) |
| Contract binding | recorded in the doc-chain [deployments registry](https://github.com/OMPub/doc-chain/tree/main/deployments); currently Sepolia `0x867FcC4f0339009043E9F6e554DD516Bcf1bcaa9`. The contract and network are *bindings*, not identity: the chain may be re-attested on another contract or network under the same docChainId. |

## 2. The daily document

Each docRef commits to one canonical catalog: the public Resident Space
Object catalog as captured for that UTC day.

**Capture rule.** `state(D) = state(D-1) + gp_history delta` where the delta is
every elset with `CREATION_DATE` in `[D-1T00:00:00Z, DT00:00:00Z)`, and per
object the kept elset is selected by `(CREATION_DATE, GP_ID, EPOCH)` descending
(`dedupe_latest_per_object` in `pipeline/snapshot.py`). Records are never
removed; all 40 OMM fields are stored exactly as returned.

**Canonical form.** A catalog is the JSON array of records sorted by integer
`NORAD_CAT_ID`, serialized with sorted keys, separators `(",", ":")`,
`ensure_ascii`, and `allow_nan=False` (`canonicalize`). The raw catalog file is
these exact bytes; `manifest.sha256` fingerprints them (artifact integrity).

**contentHash (consensus).**

```
content_sha256 = SHA-256( canonicalize( [ record − excluded_fields  for record in catalog ] ) )
```

The excluded fields are defined by the **content schema** named in
`manifest.content_schema`. Verification is therefore: load the raw catalog,
delete the schema's excluded keys from each record, re-serialize canonically,
hash, compare — both to the manifest and to the on-chain attestation.

## 3. Content schema registry

| Schema | Effective docRefs | Excluded fields | Rationale |
|---|---|---|---|
| `rso-core-v1` | genesis → present | `COUNTRY_CODE, DECAY_DATE, LAUNCH_DATE, OBJECT_ID, OBJECT_NAME, OBJECT_TYPE, RCS_SIZE, SITE, TLE_LINE0` | Space-Track back-patches the object-directory family in place on published rows (measured 2026-06-09: 0/50 archived day hashes reproduced with all fields hashed; 50/50 with these excluded; decay stamps arrive up to 7,224 days late). Consensus covers only the elset-intrinsic fields, which the same measurement showed are immutable once published. |

The implementation mirrors this table in
`pipeline.snapshot.CONTENT_PROJECTIONS`; verifiers (sweeper, hydration)
dispatch on the manifest's declared schema and reject unknown schemas.

## 4. Protocol evolution

The procedure when a consensus assumption breaks (for example the weekly drift
audit detects a non-excluded field mutating):

1. Add a row to the registry above: new schema name (`rso-core-v2`), the new
   projection, and its **effective boundary** (the first docRef hashed under
   it). Add the matching entry to `CONTENT_PROJECTIONS`.
2. New days are archived and attested under the new schema from the boundary
   onward. **Nothing else changes**: same docChainId, same contract, and the
   parent chain continues unbroken because parentHash links blockHashes, not
   schemas.
3. Historical days are left exactly as attested. Their manifests name the
   schema their contentHash used, so they remain verifiable forever against
   their preserved raw catalogs. (Raw catalogs always retain every field —
   this is what makes any future projection computable over the original
   bytes.)
4. If the triggering mutation also broke from-source reproduction of old days
   under their original schema, the registry row for the *old* schema gains a
   note recording the measured break, and bundle-based verification (section
   6) remains the historical guarantee.

No re-genesis, no new chain id, no renamed artifacts. A revision is one
registry row, one constants entry, and one effective date.

## 5. Attestations

- **blockHash** = EIP-712 `hashStruct(DocBlock{docChainId, docRef, parentHash,
  contentHash})`. Two nodes agree on a day exactly when their blockHashes are
  equal.
- **uri** = a publication locator: a `data:` URI of media type
  `application/vnd.ompub.rso.publication-locator.v1+json` whose payload holds
  `bundleSha256` (the exact release-bundle fingerprint), `locations` (URLs
  where those bytes are served — GitHub release asset, `ar://` transaction),
  and `nodeId` (the attesting node's identity, e.g. `github:ompub/rso`). The
  uri is inside the signature; locations are commitments.
- **Hash-only attestations** (empty uri) are valid and sweeper-sponsored when
  the operator holds card-specific TDH backing for the date and its
  attester-to-node binding verifies against the node declaration. They carry
  no publication locator, so data custody cannot be checked: the index counts
  them in agreement and attaches their backing, but marks them
  `publicationVerification: "hash_only"` so consumers can distinguish
  custody-verified witnesses (`"verified"`) from signature-only agreement.
  Operators that also publish bundles get the stronger tier automatically.
- **Observation plane.** Each day each node also publishes `annotations.json`
  (schema `rso-annotations-v1`): per-object changes of the excluded fields
  between consecutive captures, plus the window's `satcat_change` rows and
  recent-reentry `decay` messages (`MSG_TYPE=Historical`, `DECAY_EPOCH` within
  window −30d…+7d), each with `observed_at_utc`. Annotations are per-node,
  eventually consistent, fingerprinted by `manifest.annotations_sha256`, and
  chain-committed through the node's locator — never part of contentHash.

## 6. Verification

**Of a published day:** fetch the bundle from any locator location; check
`sha256(bundle) == bundleSha256`; extract; check the catalog bytes against
`manifest.sha256`; re-derive `content_sha256` under `manifest.content_schema`;
compare with the on-chain attestation.

**From nothing:** start from the genesis catalog (an attested, published
anchor artifact) and replay the capture rule against Space-Track for every
window; project and hash each day. Executed in full on 2026-06-09: every day
of the chain reproduced from a fresh capture. Runbook: `docs/late-join.md`.

**Continuously:** the weekly drift audit re-queries archived windows and fails
loudly on any non-excluded-field mutation or selection drift — the empirical
claims in section 3 are under permanent test.

---

**Revisions.** r1 (2026-06-10): initial profile. r2 (2026-06-10): hash-only
attestations from TDH-backed, authorization-verified nodes are sponsored and
indexed with the `hash_only` publication tier.
