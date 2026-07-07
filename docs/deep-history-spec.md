# Phase B — Deep-History Doc-Chain: Go-Live Spec

Genesis at Sputnik (1957-10-04) through the live present, as one verifiable
chain. Draft go-live plan; formalizes into `docs/profile.md` (a profile
revision) + `docs/deep-history.md` at execution. Grounded in the validated
findings: [[spacetrack-deep-history-data]], [[spacetrack-2004-migration]],
`.backpatch/1957_FEASIBILITY.md`, `.backpatch/inspect_tle.py`.

## 0. Decisions locked
- **Genesis:** 1957-10-04 (Sputnik 1, confirmed available from McDowell).
- **Attestation granularity:** monthly Merkle roots 1957-10 → 2025-12; per-day
  attestation from 2026-01-01 → present.
- **Core hash:** `rso-core-omm-v1` — numeric mean-elements as all-string tokens,
  float-free, 864-µs epoch grid (format/source-independent), stored as OMM.
  Supersedes the live string-based `rso-core-v1`. Spec frozen in §4.
- **Network:** ETH mainnet (rehearsed on Sepolia). Same DocChain contract,
  same unversioned docChainId (`keccak256("https://om.pub/rso/doc-chain")`).

## 1. Source map (all validated)
| Era | Source | Provenance / validation |
|---|---|---|
| 1957-10-04 → 1958 | McDowell `planet4589.org/space/elements/S<catnum>` | NASA GSFC/OIG via Allen Thomson, redistribution-unrestricted; Sputnik-1 launch-day elset confirmed |
| 1959 → 2004 | `tle2004_{1..8}of8` backlog zips | numeric-identical to live `gp_history` (Vanguard-1 1959 8/8) |
| 2005 → 2025 | annual `tleYYYY` zips | ISS 2025 31/31 exact; all 29 files verified |
| soft-spots (e.g. 1972) | live `gp_history` API | API complete (1972-06 = 14,408); §3 funnel |
| analyst T-band (≥270000, 2020+) | live `gp_history` API (OMM) | excluded from TLE zips; retrievable |
| 2026-04-20 → present | live OMM daily chain | already running (Sepolia → re-attested on mainnet) |

Raw source files are retained verbatim on the archive volume; provenance
(source, file, retrieval date) is recorded per elset in the build manifest.

## 2. Capture rule (EPOCH-keyed)
`CREATION_DATE` is meaningless pre-2004 (all bulk-stamped 2004-08-15), so the
deep build keys on **EPOCH**:
- Parse every elset from every source; bucket by `EPOCH`'s UTC day.
- `state(D)` = each object's latest elset with `epoch ≤ D` (carry-forward;
  `dedupe_latest_per_object` keyed on `(EPOCH, element_set_number)`).
- A day with no new elset for an object carries the prior elset — sparse early
  years (1959: ≈154 elsets) still yield well-defined daily catalogs.
- Genesis day 1957-10-04 = the McDowell graft; the chain is continuous daily
  from there.
- **Provenance boundary (segmented, §10.3):** the McDowell genesis set covers
  1957-10-04 → 1959-01-10; at the Space-Track floor (1959-01-11) the consensus
  catalog is Space-Track-only — McDowell objects do not carry across. Each day's
  hash is reproducible from a single source. (Card display may overlay genesis
  objects from the observation plane; that never enters the hash.)
- De-dup across overlapping sources by `(NORAD, EPOCH, mean-elements)`: the
  same elset from zip and API collapses to one (proven identical numerically).

## 3. Soft-spot detection (no full API walk)
1. **Internal anomaly scan** (zips only): per-(year, month) elset-count curve;
   flag windows deviating > Nσ from local trend. Implemented in
   `.backpatch/softspot_scan.py`.
2. **Targeted API count-check** on flagged windows only; materially-higher API
   count → backfill that window from `gp_history`.
3. **Random sampling** across unflagged periods for confidence.
Result + every flagged/backfilled window is logged in the build manifest.

**Scan run 2026-06-23 (232,380,037 elsets, 1959–2025, 519 skips):** flagged
year-holes 1959 (154) & 1960 (737), month-holes 1966-03/09/10 & 1986-07. Full
results in `.backpatch/SOFTSPOTS.md`.

**Key interpretation — the scan measures element *cadence*, not catalog
*coverage*.** Coverage is guaranteed by **carry-forward**: every object alive in
year N−1 is present in every daily catalog of year N regardless of how many
*fresh* elsets year N carries. (Worked example: the historical file has only 438
raw / 85 distinct fresh 1972 elsets, yet 1972 daily catalogs are full — every
1971 object carries forward. The whole-corpus 1972 raw count of 180k is
re-export duplication from later annual files, harmless because the rebuild
dedups by (NORAD, EPOCH) with the §5 tie-break.) Therefore:
- The flagged dips are **cadence** dips (less-frequent element updates), not
  gaps. They are acceptable for the archive; an optional API supplement only
  improves freshness, never coverage.
- Raw counts are **dup-inflated for heavily re-exported years** (1972+), so the
  scan can *under-flag* those — but that is moot, since carry-forward covers them.
- The **only true coverage concerns** are at the genesis edge before
  carry-forward has anything to carry: 1957-10→1958 (the McDowell graft) and the
  1959 ramp. Those are sourced explicitly (§1), not detected here.
- The rebuild **ingests every elset from every file** (deduped), so annual
  re-exports automatically raise old-year cadence above the historical file alone.

**1959 slice validation (`.backpatch/slice_1959.py`, end-to-end):** 154 legacy-
format elsets parsed with **0 skips**; 15 distinct objects by year-end; carry-
forward daily object counts grow monotonically 6→7→9→11→13→15 across 1959;
contentHashes deterministic. The 154 matches the scan's 1959 bucket exactly
(old sparse years are *not* re-export-inflated). Proves canonicalizer + assembler
work on the oldest, hardest-format data.

## 4. Core projection — `rso-core-omm-v1` (normative, frozen at genesis)
Three serializations exist (McDowell 1957, old spaced-designator export,
modern standard TLE) plus live OMM JSON; all byte-differ but carry identical
orbits. The contentHash is over the **canonical numeric mean-elements as
all-string tokens**, never raw source strings. This supersedes the live
exclusion-based `rso-core-v1` (which hashed Space-Track's raw OMM strings and so
could not reproduce from a TLE). It is a new schema-registry entry; already-
attested `rso-core-v1` days are untouched (each manifest names its own schema).

`contentHash = SHA-256(canonical_bytes)`, committed on-chain as the raw 32-byte
EIP-712 value (never a hex string — dodges hex-case ambiguity).

**Hard rule: no IEEE-754 float anywhere in the pipeline** (parse *or* serialize).
A conforming verifier in Python/JS/Go/Rust MUST produce byte-identical
`canonical_bytes`. Designed + adversarially verified 2026-06-22 (3 independent
designs → adversarial panel → synthesis); the panel's three blocking findings
(epoch off-grid divergence, core-only tie-break, mandatory Alpha-5 decode) are
all closed below.

### 4.1 `core_record` — 11 keys (the pure orbit), fixed-arity, every value a JSON **string**
```
ARG_OF_PERICENTER, BSTAR, ECCENTRICITY, EPOCH, INCLINATION,
MEAN_ANOMALY, MEAN_MOTION, MEAN_MOTION_DDOT, MEAN_MOTION_DOT,
NORAD_CAT_ID, RA_OF_ASC_NODE
```
All 11 keys mandatory in every record (absence vs null is a divergence vector).
The three **bookkeeping** fields are **excluded** from the hash — `REV_AT_EPOCH`,
`EPHEMERIS_TYPE`, and `ELEMENT_SET_NO` are provider-assigned counters, not the
orbit. Excluding them makes the hash a pure, source-independent statement about
the orbit (decision 2026-06-24, maximizing third-party reproducibility — see
§4.6). They live in the observation plane; `ELEMENT_SET_NO` is still read for the
§5 carry-forward tie-break (selection only, never hashed).

### 4.2 `canon_decimal(s)` — shared numeric tokenizer (integer/string ops only)
1. Strip ASCII whitespace and a trailing `\` (legacy artifact).
2. Capture sign: one leading `-` → negative; leading `+` dropped; no other signs.
3. If `e`/`E` present, expand to plain decimal by shifting digits per the integer
   exponent (no float). TLE assumed-exponent fields are pre-expanded to `e`-form.
4. Ensure one `.`; split int/frac. `frac = frac.rstrip('0')`,
   `int = int.lstrip('0') or '0'`.
5. Recombine: `int`, or `int + '.' + frac` if frac non-empty.
6. **Zero guard:** all-zero → `"0"`, sign dropped (never `-0`, never `0.0`).
7. Re-apply `-` iff negative and result ≠ `"0"`.

One shortest plain-decimal form of a terminating rational ⇒ all implementations
converge. **No rounding** here — every field is a terminating decimal.

Per-field: INCLINATION / RAAN / ARG_OF_PERICENTER / MEAN_ANOMALY / MEAN_MOTION →
`canon_decimal` as published, **no quantization** (4-dp TLE and any OMM string
collapse only because they encode the *same digits*; a genuinely finer future
OMM value is a *different physical value*, resolved by the §5 source-of-record,
never by rounding). ECCENTRICITY: TLE 7-digit field has implied `0.` → build
`"0." + field` first (`0004499` → `0.0004499` = OMM `0.00044990`). MEAN_MOTION_DOT
(ndot/2): insert `0` before a leading/post-sign `.` then `canon_decimal`.

**Assumed-exponent decode (BSTAR, MEAN_MOTION_DDOT) — one unified rule** covers the
standard form and every Space-Track historical overflow form for large drag terms
on near-reentry objects: (1) a leading `+`/`-`/space is the mantissa sign (`-`
negative, else positive); a leading **digit** means no sign. (2) the **exponent**
is the trailing signed integer — the substring from the last interior `+`/`-`
(e.g. `-3`, `+1`, `-10`), or, if none, the last 2 digits as a positive exponent
(`00`, `01`). (3) the **mantissa** is the digits before the exponent with an
implied decimal point **5 places from the right**: 5 digits → `0.MMMMM`, 6 digits
→ `M.MMMMM`. All values pinned byte-for-byte against the authoritative Space-Track
`gp_history` JSON (the OMM emits the same number → cross-source equivalence):
`17028-3`→`0.00017028`, **`+2083500`→`0.20835`, `-2979601`→`-2.9796`,
`+1582202`→`15.822`, `49000-10`→`4.9e-11`, `973196+1`→`97.3196`**. All-zero
mantissa → `"0"`; fail-closed otherwise.
**Column-shift tolerance:** a subset of legacy records have a blank international
designator + one extra space, shifting every line-1 field +1; detect via the
epoch decimal point (index 23 standard vs 24 shifted — never false-positives on a
standard line) and read line-1 fields at that offset (line 2 is never shifted).
Absent-field defaults (pinned, irreversible):
`BSTAR/MEAN_MOTION_DOT/MEAN_MOTION_DDOT = "0"`, injected explicitly (silent
omission would change the hash). ELEMENT_SET_NO (selection-only, not hashed) is
read from the line tail by **whitespace-tokenize, never fixed-slice** (legacy
5-wide widths + trailing backslash shift the columns); `00041` → `41`.

### 4.3 EPOCH — fixed-width `YYYY-MM-DDThh:mm:ss.ffffff`, integer-only, 864-µs grid
The one canonical epoch token; a deterministic rendering of an integer-µs instant.
**Anti-divergence rule (load-bearing): the TLE 8-digit day-fraction grid.**
`1e-8 day = 864 µs exactly`, so every TLE epoch's µs-of-day is an exact multiple
of 864 and the TLE path **never rounds**. Space-Track OMM epochs are re-derived
from that same truncated fraction and also land on the 864-grid (verified ISS
`66452932224 = 864 × 76913116`). **Any ingested OMM epoch whose µs-of-day is not
a multiple of 864 is an ingest error → reject (or re-derive from its TLE).** This
forecloses the only proven cross-language divergence (the `86 400 000 000` denom
has a `3³` factor → off-grid numerators are non-terminating → precision/rounding
becomes language-dependent). A genuine future sub-864-µs source needs a **new
schema id** (`rso-core-omm-v2`), never an in-place change.

- **TLE `YYDDD.FFFFFFFF`:** year pivot `YY≥57 ⇒ 1900+YY`, `YY≤56 ⇒ 2000+YY`;
  `usec = (int(F)*86400_000000 + 10**L//2)//10**L`; assert `usec % 864 == 0`;
  carry days; leap-correct DOY→(month,day); `divmod` to h/mi/s/us; render.
- **OMM ISO:** strip `Z`; parse ints; round-half-up the seconds-fraction to
  exactly 6 digits (same integer carry machinery); assert on 864-grid; render.
- Rounding is normatively **round-half-up** on the 6-digit grid, both paths.

Worked: ISS `26172.76913116` → `2026-06-21T18:27:32.932224` (= OMM string).
Sputnik `57277.80437500` → `1957-10-04T19:18:18.000000`.

### 4.4 NORAD_CAT_ID — base-10 integer string `^(0|[1-9][0-9]*)$`
No leading zeros/sign/width/prefix; its `int()` drives sort + dedup. Plain numeric
(TLE `<100000`, any all-digit OMM incl. future 9-digit) → `int()`. **Alpha-5**
(5-char satnum, letter first) → `ALPHA5.index(first)*10000 + int(last4)`,
`ALPHA5="0123456789ABCDEFGHJKLMNPQRSTUVWXYZ"` (I/O skipped; `T0000=270000`,
`Z9999=339999`). **Mandatory:** a non-numeric OMM `NORAD_CAT_ID` MUST be Alpha-5-
decoded, never treated as opaque text (so 5-char TLE and 9-digit OMM of one object
agree). Validate with an **ASCII-only** digit test (not Unicode `isdigit`).

### 4.5 Serialization → hash
`core_record` bytes = `json.dumps(obj, sort_keys=True, separators=(",",":"),
ensure_ascii=True, allow_nan=False)`. All-string values + sorted ASCII keys +
pinned separators ⇒ zero degrees of freedom; values are `[0-9.\-T:]` so no JSON
escaping ever fires. Catalog: dedup (one record per NORAD, duplicate = hard
error) → **sort ascending by `int(NORAD_CAT_ID)`** (numeric, so `99999 < 100000`,
`25544 < 270000`) → `canonicalize([rec_0,…])` = `"[" + rec_0 + "," + … + "]"`,
no whitespace; empty day → `"[]"`. `canonical_bytes` is the *sole* hash input (no
newline/BOM/length prefix). `recordCount = len(array)` is published in the
manifest (not hashed) so verifiers detect truncation. Records are **stored as
OMM/JSON** — the keys are CCSDS keywords; observation metadata (OBJECT_NAME,
COUNTRY_CODE, GP_ID, CREATION_DATE, REV_AT_EPOCH, …) lives in a *separate*
observation file, never in the hashed `core_record`.

Byte-exact ISS example:
```json
{"ARG_OF_PERICENTER":"89.579","BSTAR":"0.00017028","ECCENTRICITY":"0.0004499","EPOCH":"2026-06-21T18:27:32.932224","INCLINATION":"51.6326","MEAN_ANOMALY":"270.663","MEAN_MOTION":"15.4935758","MEAN_MOTION_DDOT":"0","MEAN_MOTION_DOT":"0.00016717","NORAD_CAT_ID":"25544","RA_OF_ASC_NODE":"277.4139"}
```

### 4.6 Reproducibility caveats (surfaced, not buried)
- **Set selection is the larger reproducibility surface.** The hash only
  guarantees *same set → same bytes*; §2/§5 pin the carry-forward set (membership
  by latest EPOCH ≤ end-of-UTC-day; window on **EPOCH, never CREATION_DATE**;
  tie-break = latest EPOCH → highest `int(ELEMENT_SET_NO)` (from the source elset,
  selection-only) → lexicographically-largest core record bytes; **never**
  CREATION_DATE/GP_ID, which are excluded). All tie-break inputs come from the
  source elset itself, so two builders on the same pinned source reproduce it.
- **Bookkeeping fields are NOT hashed (resolved 2026-06-24).** `EPHEMERIS_TYPE`,
  `ELEMENT_SET_NO`, and `REV_AT_EPOCH` are excluded from the core so the hash
  depends only on the orbit — no dependence on McDowell's hand-corrected
  ephemeris types or a provider's element-set renumbering. They are preserved in
  the observation plane; `ELEMENT_SET_NO` is still used for the §5 tie-break
  (selection only). This removes the prior cross-mirror fragility entirely.
- Golden vectors shipped with the verifier (frozen at genesis): ISS (TLE⇔OMM),
  Sputnik McDowell, 1959 Vanguard export line, a leap-year DOY=366 epoch, a
  negative-mantissa BSTAR (`-11606-4`), an Alpha-5 id (`T0000`→`270000`), and
  full `canonical_bytes`/`contentHash` vectors.

## 5. Parser / normalizer
`pipeline/tle_normalize.py` (formalize `inspect_tle.py`):
- Alpha-5 catalog decode (`T0000→270000`, skip I/O, ≤339,999).
- Tolerate format variants: spaced intl designators, explicit `+` signs,
  5-wide element-set numbers, trailing `\`, non-standard classification chars,
  McDowell XTLE quirks; 1962-64 Brouwer ephemeris-type corrections per McDowell.
- Validate TLE checksums; log + skip malformed lines (rate recorded).
- Emit canonical numeric core + OMM record. Unit-tested against the known ISS
  elset and the Sputnik-1 / Vanguard-1 samples already verified.

## 6. Attestation structure
One continuous daily chain (parentHash links blockHashes); two on-chain
commitment regimes.

**docRef conventions** (all distinct, no collision):
- `YYYYMMDD000000` (MM 01-12, DD 01-31) — a daily catalog contentHash.
- `YYYYMM00000000` (MM 01-12, **DD=00**) — a monthly Merkle root.

**History (1957-10 → 2025-12):** daily blockHashes are the Merkle leaves;
one root per month, `contentHash = monthRoot`, `parentHash = previous month's
root blockHash`. Month-roots form an 819-link spine (1957-10 → 2025-12 inclusive
is exactly 819 months; the first root's parent = `0x00…00`). Any day is provable
by a ≈5-hash Merkle proof against its on-chain month-root.

### 6.1 Normative definitions (frozen — a third party reproduces every value)

**Day boundary.** A "day" is a civil UTC calendar day of a **fixed 86 400-second
length** (proleptic Gregorian; **no leap seconds** — a UTC day carrying a leap
second still buckets on the 86 400 000 000-µs grid). EPOCH lies on the 864-µs
grid (§4.3). Carry-forward cutoff = that day's `T23:59:59.999999Z`.

**blockHash (the leaf, and the on-chain commitment).** Replicates
`DocChain.sol` `_hashDocBlockFields` exactly:
```
blockHash = keccak256(
    DOC_BLOCK_TYPEHASH                              # 0xb8421210…07894 (32B)
  ‖ docChainId                                     # 0x6011620b…399bea (32B)
  ‖ uint64(docRef) left-zero-padded to 32 bytes
  ‖ parentHash (32B)                               # genesis = 0x00…00
  ‖ contentHash (32B) )                            # the day's content_sha256
```
160 bytes, Ethereum **keccak256** (NOT NIST SHA3-256). `recordCount` is **not**
hashed (the DocBlock has only 4 fields); it is published per-day and checked
out-of-band against `contentHash`. blockHash is **EIP-712-domain-independent** —
the domain (chainId/contract) enters only the *signature*, never the blockHash —
so all leaves are final regardless of which network later attests them.

**Monthly Merkle tree.** Leaves = that month's daily blockHashes as **opaque
32-byte values, in chronological day order**. Tree hash is a **separate sha256
domain** (NOT keccak): `combine(a,b) = sha256(min(a,b) ‖ max(a,b))` — sorted-pair,
so it is **commutative**. On an odd level the lone node is **promoted unchanged**
(carried up — NOT the Bitcoin/OpenZeppelin duplicate-last-node rule). `monthRoot`
= the fold to one 32-byte value. An inclusion proof is the **flat list of sibling
hashes** (no left/right flags, since combine is commutative); verify by folding
the leaf with each sibling via the same sorted-pair sha256 and comparing to
`monthRoot`. Leaves and nodes interchange as lowercase hex, no `0x`.
*(Reference: `attestation/merkle.py`, `attestation/spine.py`,
`attestation/keccak256.py`.)*

**Reference vector (self-test).** keccak256 must reproduce
`DOC_BLOCK_TYPEHASH = keccak256("DocBlock(bytes32 docChainId,uint64 docRef,bytes32
parentHash,bytes32 contentHash)")` and `docChainId =
keccak256("https://om.pub/rso/doc-chain")`. The genesis-era on-chain block
(2026-04-20, parent `0`, contentHash `0x1838a066…231a740`) → blockHash
`0xe651a583…96e103e` (matches the live Sepolia genesis). Deep-history genesis
(1957-10-04) blockHash, the 2025-12-31 weld value, and the Dec-2025 spine head
are pinned in `docs/deep-history-spine-anchors.json`.

**Live (2026-01-01 → present):** per-day attestation as today;
`docRef = YYYYMMDD000000`, `parentHash = previous day's blockHash`. Day
2026-01-01's parent = 2025-12-31's blockHash (a leaf under the 2025-12 root) →
the daily chain welds to the history spine with no seam.

**Submission:** offline-compute all blockHashes + month-roots (deterministic),
then `attestBatch` in low-gas windows (§10.2: weekend ≈02:00–04:00 UTC) —
819 month-roots + the 2026 daily run ≈ a few hundred txs total. At today's
≈2–5 gwei that is roughly **0.1–0.3 ETH single node** (the 1–6 ETH figure was
a high-gwei worst case). One upstream node attests the spine; the community
adds independent witnesses via late-join if they choose (§10.1).

## 7. Storage / publication (tiered — consensus is hash-only; the rest is UX)
Measured sizes: source corpus = **12 GB** (the 29 zips); a modern day =
**11.4 MB** OMM-gzip / **≈3.7 MB** TLE-gzip (68,165 objects, 2026-06-23);
deep days are KB. **Full materialized every-day set ≈ 15 GB as TLE,
≈45 GB as OMM.** Per-day file verifies against its contentHash + on-chain
month-root, so any cheap copy is provably the witnessed truth.

**Tier 0 — consensus (required, ≈free):** hash-only attestation for the bulk
deep history; monthly Merkle roots on-chain. Anyone re-derives from the
published sources + capture rule. No per-day bundle needed for consensus.

**Tier 1 — permanence (Arweave, one-time):** the 12 GB source corpus +
≈830 monthly anchor bundles, immutable, gateway-CORS, and the exact bytes the
on-chain locator commits to. Arweave is dynamic-priced (≈$0.5–10/GB; check
ar-fees.arweave.net): **12 GB source ≈ $6–120 one-time**, +15 GB materialized
per-day TLE ≈ $8–150 if we also pin the daily files permanently.

**Tier 2 — hot per-day UX (Cloudflare R2 or any CORS object store):**
materialized **per-day TLE** (`tle/YYYY-MM-DD.txt`) and OMM files for the
card scrubber + KeepTrack's history tool — load any day's real positions in
≈1s. ≈15 GB TLE → **≈$0.23/month on R2, zero egress.** Self-healing: a build
job materializes from the source + daily index; every file carries its day's
sha256 so consumers verify against Tier-0/1.

**Tier 3 — free mirror (GitHub):** source corpus + manifests + monthly anchors
on the node branch / releases for reproducibility (release assets lack CORS,
so not the hot path).

KeepTrack integration: it already ingests the RSO archive; expose a stable
`…/tle/YYYY-MM-DD.txt` (R2 + Arweave fallback) and its history tool renders any
day. The card scrubber reads the same per-day files via the full-history
Tier-1 index (§8).

## 8. Indexer updates (so the card supports full history)
`indexer/` changes:
1. **Parse both docRef regimes** — recognize `YYYYMM00000000` month-root events
   and `YYYYMMDD000000` daily events; group the deep history under its
   month-roots, the live era as daily.
2. **Merkle service** — store each month's leaf list + generate inclusion
   proofs; the index exposes, per historical day, its `monthRoot`, leaf index,
   and proof so a client verifies any day against the on-chain root.
3. **Full-history Tier-1 index** — extend the card's lean per-day index
   (`index/manifest.json` + `index/YYYY.json`) back to **1957**: per-day
   object count, on-orbit/decayed split, fingerprints, `catalog_url`, and the
   `monthRoot`+proof. Year-chunked so the card lazy-loads only displayed years.
4. **`attestedAtUtc` / lag** — deep-history days carry large
   `attestationLagDays` (honest: reconstructed/late-attested), the live era
   lag-0. The card already renders this; it now spans 1957→present.
5. **Coverage/provenance per day** — source tag (mcdowell / tle-zip / api),
   so the card can show where each day's data came from and the
   analyst-attribution lifecycle.
The card reads the extended index unchanged in shape — just more years — and
gains "verify this day" via the published Merkle proof + on-chain month-root.

### 8.1 Metadata directory + card data model (orbit ⋈ identity)
Identity (name/country/type/rcs/launch/decay/op-status) is NOT baked into
per-day files — that would be anachronistic, redundant across 25k files, and
violate the consensus/observation split. Instead:
- **Per-day file = orbit.** NORAD + elset + intl-designator (designator is in
  the TLE lines, so it's free per-day). Consensus, hash-attested.
- **Object directory = identity.** `NORAD → {name, country, type, rcs, launch,
  decay, op_status, first_seen, …}` where each mutable fact carries a
  `learned_utc`, ≈70k rows ≈ **2–3 MB gzipped**, range-chunked
  (`directory/NNNNN-NNNNN.json`). Indexer-maintained, refreshed daily,
  observation-plane (never in contentHash). This is the `rso-current` fold of
  all nodes' annotations.
- **Render = client-side join, in-memory first.** Card fetches the directory
  ONCE into a plain in-memory `Map` (it's only 2–3 MB — always works, even in
  a sandboxed NFT iframe), and *optionally* persists it to **IndexedDB when
  available** (a session-cache optimization, not a dependency — sandboxed
  iframes may block storage). Per scrub it fetches only the day's small orbit
  file and joins NORAD→identity locally.
- **Time-aware rendering (your question: yes).** Each identity fact has a
  `learned_utc`; the card surfaces a fact only when **scrub-date ≥ learned_utc**,
  so an object shows its then-state (e.g. `TBA`/unnamed) before its attribution
  date and gains the name/country/type on the exact day the network learned it
  — the analyst-attribution lifecycle, animated in the scrubber. This is
  precise for the **live era** (annotations record the real learn-date). For
  **deep history** there is no historical learn-date (we only have today's
  satcat), so those facts use `learned_utc = the object's first archived day`
  and simply show throughout — honest framing: position = witnessed then,
  identity = best known now.
- KeepTrack parallels this exactly (TLEs + its enrichment DB); it can consume
  our per-day TLE files and either its own or our directory.

A separate **card-agent instruction doc** (written once §10 + canonicalization
are locked) tells the card session how to: load/cache the directory, fetch
per-day orbit files, join + render, verify against month-roots, and extend the
scrubber to 1957.

## 9. Build order
1. `tle_normalize.py` + tests (parser/normalizer). 
2. Internal anomaly scan + soft-spot funnel (§3); backfill flagged windows.
3. EPOCH-keyed daily assembly 1957→2025 on the Studio (data is local there);
   emit daily core hashes + OMM records + manifest. **Module `pipeline/assemble.py`
   (`ObjectHistory`) is built + validated at slice scale (in-memory, all elsets).
   The full 232M-elset rebuild must NOT use it as-is** — it holds every elset in
   RAM. Productionize with a bounded-memory day-sweep: sort/merge elsets by EPOCH
   (external sort or per-object-shard), maintain a `current elset per object` map
   (~70k entries), and emit each day's catalog as the epoch cursor crosses the
   day boundary — O(elsets) time, O(objects) memory. The §4 canonicalizer + §5
   selection rules are unchanged; only the iteration strategy scales.
4. McDowell 1957-58 graft; de-dup across sources.
5. Merkle month-roots + blockHash linkage (offline, deterministic).
6. Indexer updates (§8) + full-history Tier-1 index.
7. Mainnet deploy + batched attestation (history month-roots, then 2026 daily);
   weld to live chain. Single upstream node attests the deep spine.
8. Profile revision (numeric schema, month-root convention, deep-history
   capture note) + `docs/deep-history.md`.

## 10. Decisions (resolved)
1. **Deep-spine attestation: single upstream node only.** brookr/forks do NOT
   re-attest the deep history (no 2× gas). The community fills in independent
   witnesses as desired via the late-join path — optional, not required.
2. **Gas timing: batch on weekends, early UTC.** Gas is structurally low now
   (≈2–5 gwei, down ≈95% from 2024) so cost is minor regardless, but timing
   shaves another 50–70%: lowest is **weekends (Sunday) around 02:00–04:00
   UTC** (late-US / early-EU off-hours, both markets offline). Schedule the
   ≈few-hundred-tx history batches in that window; verify live via
   ar-fees-style gas oracle before each batch. (Mainnet go-live still gated on
   meme-card mint economics, but no longer on gas.)
3. **Hard cutover to Space-Track at 1959 — SEGMENTED, resolved 2026-06-25.**
   McDowell sources **1957-10-04 → 1959-01-10 only** (≤1958 elsets, carried
   forward to the Space-Track floor 1959-01-11). At 1959-01-11 the catalog is
   exactly Space-Track; **the 8 McDowell genesis objects do NOT carry into the
   Space-Track era** (consensus hash, single-source-per-day). Consequence,
   accepted: the 4 genesis survivors (NORAD 4, 5=Vanguard 1, 8, 9) are absent
   for 103–179 days in early-1959 until Space-Track first lists them; the 4
   decayed (1=Sputnik 1, 3, 6, 10) drop permanently — both faithfully reflect
   Space-Track's real coverage. **Why segmented:** every operational-era day
   stays reproducible from Space-Track ALONE; a combined/bridged hash would
   permanently entangle the 4 never-superseded McDowell objects into every
   1959→2025 day (no day reproducible from Space-Track alone). **Display
   continuity (genesis + decayed/survivor objects on the card) is handled at the
   card layer via an observation-plane overlay — a separate thread, never in the
   hash.** McDowell may still cross-validate the 1959 overlap for confidence.
4. **Harvest the analyst band + metadata.** Include the analyst T-band (API
   harvest 2020–2025) and build the object directory from current satcat +
   annotations — the fullest "what we knew, when (as reported now)" picture.

Remaining to lock before code: the exact numeric canonicalization (§4) — the
per-field decimal forms that define the core hash and per-day file contents.
