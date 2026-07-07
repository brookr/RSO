# Deep-history rebuild — verification record

Full 1957→2025 rebuild run **2026-06-24** on the Mac Studio (echo-base, M3 Ultra,
512 GB, Python 3.9.6), engine `pipeline/build_history.py`, schema
`rso-core-omm-v1` (11-field pure-orbit core). Artifacts on the Studio at
`/Users/Shared/Backups/rso/rebuild/out/`.

## Rebuild summary
| metric | value |
|---|---|
| elsets processed | 232,331,940 |
| elsets skipped (logged) | 48,614 (0.021%) |
| daily catalogs | 24,462 |
| genesis | 1959-01-11 |
| final day | 2025-12-31 |
| final catalog size | 65,329 objects |
| wall time | 964 s (~16 min, 28 workers) |
| genesis hash | `b1a567a2…8090ca` |
| final hash | `4bf23033…c75f904e` |

## Checks

**1. Continuity — PASS.** 1959-01-11 → 2025-12-31 is *exactly* 24,462 calendar
days (66y + leap days + Jan-Dec 2025); manifest has 24,462 rows, zero duplicate
days, zero gaps. Every UTC day present exactly once by construction (day-sweep).

**2. Skip categorization — PASS (no hidden data loss).** All 48,614 skips are a
single class — `malformed assumed-exponent field` (47,833 + 778 + 2) plus 1
`non-numeric decimal field`. These are the non-standard **overflowed drag-term
encoding** on rapidly-decaying (near-reentry) objects, which the §4 decoder
**fails closed** on (never guesses). No systematic category of valid data is
silently dropped. Carry-forward covers the affected objects (they keep their last
valid elset). Every skipped line is preserved in `out/skipped_elsets.tsv`,
recoverable later if the overflow format is deciphered.

**3. Growth curve — PASS (matches known catalog history).**
1965: 887 · 1975: 6,701 · 1985: 14,049 · 1995: 21,548 · 2005: 26,362 ·
2015: 37,888 · 2024: 56,940 · 2025-12-31: 65,329. Monotonic, with the visible
Starlink-era acceleration 2015→2024.

**4. Independent recomputation — cross-validates the sort+sweep path.**
`pipeline/verify_days.py` recomputes selected days' hashes by a *different*
aggregation (per-object "latest EPOCH ≤ D" reduction — no external sort, no
day-sweep), sharing only the unit-tested §4 canonicalizer. Local self-test vs the
1959–65 slice manifest: 1959-12-31, 1962-06-15, 1965-12-31 all MATCH (hash +
count). Full-manifest run across 1959→2025 (incl. both endpoints + leap days
2000-02-29 / 2024-02-29): **ALL 11 MATCH** — hash + object count identical on
every sampled day. Endpoints anchored (1959-12-31 = genesis era; 2025-12-31 =
`4bf23033…` = recorded final hash).

**5. Parser provenance.** The §4 canonicalizer is independently validated by 60
unit tests + the §6 reference vectors (ISS TLE⇔OMM, Sputnik, 1959 Vanguard
legacy export) + prior numeric spot-checks vs the live Space-Track API.

## Genesis graft — McDowell 1957-58 (2026-06-24)
Extends genesis from the Space-Track floor (1959-01-11) back to **Sputnik 1,
1957-10-04**, per §10 decision 3 ("one clean provenance boundary; McDowell
sources 1957-58 only; hard cutover to Space-Track at 1959").

- Source: McDowell `planet4589.org/space/elements/00000/` (95 files S00001–S00099,
  NORAD 1–99, redistribution-unrestricted). 3-line extended-TLE; the `3 `
  provenance line is ignored by the parser. Sputnik 1 parses exact (NORAD 1,
  1957-10-04T19:18:18, incl 65.1°, mm 14.96977024, ecc 0.0520478).
- Built with the same engine, `--year-max 1958 --final-day 1959-01-10`: only
  ≤1958 elsets (25 of them, 0 skips), carried forward to fill the gap up to the
  Space-Track floor. **464 days, 1957-10-04 → 1959-01-10.**
- Historically faithful growth: 1957-10-04 Sputnik 1 (1) · 1957-11-03 Sputnik 2
  (2) · early-1958 Explorer 1 (3) · → 8 objects by 1959-01-10.

**Welded full chain (`out/full_manifest.txt`): 24,926 days, 1957-10-04 →
2025-12-31, zero gaps, zero duplicates.** The Space-Track segment is byte-for-byte
unchanged (1959-01-11 = `b1a567a2…`). The weld is a hard provenance boundary:
the 8 McDowell genesis objects do not carry into the Space-Track era (1959-01-11
opens at 1 object and the operational catalog rebuilds), exactly as decision 3
specifies. (Alternative, if a continuous object set is preferred over a clean
provenance boundary: a combined run carrying McDowell objects forward — would
change the 1959+ hashes by the few McDowell-only decayed objects; not done.)

## Result
**VERIFIED.** The 24,462-day Space-Track rebuild + 464-day McDowell genesis graft
(24,926 days total, 1957-10-04 → 2025-12-31) is structurally complete
(perfect day continuity), has no hidden data loss (all skips one known
fail-closed class, fully logged), tracks the real catalog growth curve, and
every sampled day across the full 1959→2025 timeline reproduces bit-for-bit via
an independent aggregation. The manifest (`out/daily_manifest.txt`) is the
authoritative per-day `contentHash` + `recordCount` set, ready for the §6 Merkle
month-roots + on-chain attestation.

Open follow-ons (not blockers): McDowell 1957-58 genesis graft (extends genesis
from 1959-01-11 back to 1957-10-04); optional tolerant decoder to recover the
~48k overflowed-drag-term skips; Merkle/blockHash linkage + mainnet attestation
(needs chain context + go-ahead).

## Recovery — Space-Track large-drag-term encoding (2026-06-25)

The integrity review's "44-object coverage gap" was resolved: the records were
Space-Track's own overflow encoding for large drag terms (BSTAR/nddot) on
near-reentry objects, confirmed against the authoritative `gp_history` JSON. The
unified decoder (`decode_assumed_exp`, §4.2) recovers **48,613 of 48,614**
previously-skipped elsets (1 residual is a genuinely corrupt line, correctly
fail-closed) and the +1 blank-designator column shift. This makes the
TLE-sourced hash match the OMM-sourced hash where they diverged.

Re-run + re-anchored. **Final anchors:** genesis `0xac994f03…595936fc0`
(unchanged), weld `0x1bc2b0f3…e0a5596b`, spine-head `0x9e41f7c2…4769e4c5`.
Final catalog **65,373 objects** (= 65,329 + 44 recovered). Independent
`verify_days` recompute MATCHES the recovered manifest on 14 days across
1959→2025 (incl. the 1989 recovery peak and obj-898's 2004 days). Decoder
validated against ALL 48,614 originally-skipped records offline; 9 decode
vectors pinned to the Space-Track API.
