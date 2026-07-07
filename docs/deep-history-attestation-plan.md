# RSO Deep-History Attestation Plan (Phase B §6)

**Status:** draft for human approval. No gas spent. Every on-chain action is gated on §5.
Researched 2026-06-25 against the live contract + reference lib + on-chain index.

## 0. Fixed constants (frozen)

| Thing | Value |
|---|---|
| `docChainId` | `0x6011620b…399bea` (= keccak256 of `https://om.pub/rso/doc-chain`) |
| `DOC_BLOCK_TYPEHASH` | `0xb8421210…07894` |
| Genesis day | 1957-10-04 (Sputnik), `parentHash = 0x00…00` |
| Days to chain | 24,926 (1957-10-04 → 2025-12-31) |
| Month-roots | **819** (1957-10 → 2025-12 inclusive) |
| Schema (history) | `rso-core-omm-v1`, 11-field core; live era stays `rso-core-v1` |

## 1. blockHash chain (offline, no chain writes)

Per the contract (`DocChain.sol:313-322`), each day:
```
blockHash[N] = keccak256( abi.encode(
    DOC_BLOCK_TYPEHASH, docChainId, uint64(docRef), parentHash, contentHash ))
```
- `docRef` = `YYYYMMDD000000`; `contentHash` = the manifest's per-day `content_sha256`;
  `parentHash` = `blockHash[N-1]`; genesis parent = `0x00…00`.
- **`recordCount` is NOT hashed** (the DocBlock has only 4 fields). It rides in the
  published per-day file, verified out-of-band against `contentHash`.
- **blockHash is domain-independent** — the EIP-712 domain (chainId/contract) enters only
  the *signature*, never the blockHash. So all 24,926 leaves precompute now with no
  network chosen.
- **Perf:** do NOT `cast keccak` 24,926× (subprocess each). Vendor a stdlib/pure-Python
  keccak256, and assert byte-identity vs `cast keccak` on sampled days (genesis, a mid
  day, 2025-12-31) before trusting the bulk.

## 2. Monthly Merkle roots

- Per month, leaves = that month's **daily blockHashes** (keccak, opaque 32 bytes), built
  into a **sha256 sorted-pair** tree (`merkle.py`). Two domains on purpose: leaves keccak,
  tree sha256, proofs checked client-side (no on-chain MerkleProof).
- Each month-root is its own DocBlock: `docRef = YYYYMM00000000` (DD=00, can't collide with
  a real day), `contentHash = monthRoot`, `parentHash = previous month-root's blockHash`.
  First month (1957-10) parent = `0x00…00`.
- **On-chain (history):** only the **819 month-root DocBlocks**. Individual 1957→2025 daily
  blockHashes are committed *only* via their month-root. Day inclusion = recompute the day's
  blockHash + a ~5-hash sibling path against the on-chain month-root.

## 3. The weld to the live chain — ⚠️ spec vs reality conflict

| | Spec §6 | Reality (on-chain) |
|---|---|---|
| Live genesis | 2026-01-01, parent = 2025-12-31 | **2026-04-20, parent = `0x00…00`** |
| Range attested | — | 52 days, 2026-04-20 → 2026-06-10 |
| Network | mainnet | **Sepolia only; mainnet undeployed** |

The spec's "2026-01-01 mainnet weld" predates the real 2026-04-20 Sepolia genesis. The weld
seam exists (`parent_hash_for_date(baseline_parent_hash=…)`): the live-genesis day stops being
zero-parented and points at the prior day's blockHash. **Two clean architectures (Decision D1):**

- **Option A — single continuous chain (re-root on mainnet).** Treat Sepolia as a throwaway
  rehearsal. Build ONE daily chain from 1957 (parent=0); the first live day takes
  `baseline_parent_hash = 2025-12-31's blockHash`. Live days reuse their existing contentHashes
  verbatim but get new parentHashes → new blockHashes on mainnet. Deep history is a true
  parent-prefix of live. (Matches the `rso-chain-v2-plan` reset + spec's "re-attested on mainnet.")
- **Option B — separate referenced spine.** Live chain keeps its 2026-04-20 zero-parent genesis;
  the spine is additive and referenced (not hash-linked across the boundary). No live blockHash
  changes.

## 4. On-chain submission

- **Submit:** 819 month-root DocBlocks (history) + the live daily run, via `attestBatch`
  (selector `7fba5650`), all-or-nothing on validity, **idempotent on duplicates** (safe to
  resubmit). One tx can't hold 819 — chunk ~200-300/tx → **≈3-4 history txs** + 1 live tx.
  `submit_batch.py` has no chunking loop yet (thin chunker = build gap).
- **Gas:** ≈ **0.1-0.3 ETH** single node at 2-5 gwei. Confirm via `--dry-run` + `eth_estimateGas`
  on one chunk (no gas) before scheduling.
- **Keys:** disposable no-funds **signer** (EIP-712, needs the domain → network fixed first) +
  separate funded **courier** (keystore, not raw key, for mainnet). A **month-root signer**
  (sibling of `node_attest.py`) does not exist yet — build gap.
- **Timing:** weekends ≈ 02:00-04:00 UTC (§10.2). Idempotent skip makes a resumed window safe.

## 5. Decisions needing you

| # | Decision | Default |
|---|---|---|
| **D1** | Weld architecture: **A** (single chain, re-root on mainnet) vs **B** (separate spine) | A, if Sepolia is throwaway |
| **D2** | Weld date: 2026-01-01 (backfill Jan-Apr 2026) vs 2026-04-20 (real genesis) | decide with D1 |
| **D3** | Weld parent: 2025-12-31 daily leaf vs Dec-2025 month-root | daily leaf |
| **D4** | Signer key + funded courier (keystore vs raw) | keystore courier |
| **D5** | Network: mainnet now vs **Sepolia rehearsal of the full spine first** | rehearse first |
| **D6** | First month-root (1957-10) parent = `0x00…00` | zero |
| **D7** | Gap-month policy (empty months — `merkle_root` raises on 0 leaves) | skip empty months, document |
| **D8** | Inclusion-proof publication schema (per-month proof files) | fingerprinted JSON bundle |
| **D9** | Single-node vs community attestation | single node + late-join witnesses |
| **D10** | Confirm manifest contentHash field == `content_sha256`; live stays rso-core-v1 | confirm before compute |
| **D11** | Gas budget cap + max-gwei abort | 0.3 ETH ceiling |

## 6. Build order

**Phase 1 — implementable NOW, zero chain writes, only needs D10 confirmed:**
1. Pure-Python keccak256 + `attestation/spine.py`: compute the 24,926 daily blockHashes
   (genesis parent=0), assert byte-identity vs `cast keccak` on sampled days.
2. Month-root computation: 819 sha256 Merkle roots over daily leaves (via `merkle.py`); emit
   the month-root DocBlock tuples (chained parentHashes, first parent=0).
3. Inclusion proofs for all 24,926 days (leaf index, ~5-hash path, monthRoot).
4. Validation: 819 months, no docRef collisions, no gap-month crash (D7), 2025-12-31 blockHash
   deterministic (the weld value).
5. Indexer read-side: parse both docRef regimes, expose per-day monthRoot+proof.

**Phase 2 — gated on decisions:** month-root signer (D4/D5) · chunker around `submit_batch.py`
(D1/D2/D3) · weld wiring (D1/D2/D3) · Sepolia full-spine rehearsal (D5) · mainnet submission
(D5/D11 + funded courier + mainnet deploy).

## 7. Reconciliation flags
- **F1** Option A re-roots the live genesis (parent 0 → non-zero) → live blockHashes change on
  mainnet; OK only because Sepolia is a throwaway rehearsal, not because hashes are preserved.
- **F2** Spec weld date (2026-01-01) ≠ real genesis (2026-04-20); Jan-Apr 2026 is unattested
  anywhere (backfill or absorb into history months — D2).
- **F3** Sepolia blockHashes are not portable to mainnet for the *signature*; blockHashes
  themselves are domain-independent — only re-parenting changes them.
- **F4** Schema split: history = rso-core-omm-v1, live = rso-core-v1. A verifier must dispatch
  on schema per docRef; the omm-v1 hash does NOT reproduce the live v1 days.
- **F5** recordCount is outside the hash — never commit it on-chain.
