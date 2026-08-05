# How Orbital Witness Fits Together

*A one-page tour of the whole system — the archive, the index, and the artwork —
and why each piece exists. Start here, then follow the links at the bottom to go
deeper.*

**In one sentence:** Orbital Witness turns the public catalog of objects in Earth
orbit into durable, independently verifiable public memory — captured daily,
hashed and attested on Ethereum, mirrored to permanent storage, and rendered by
an NFT "dashboard" card that keeps working even if every server disappears.

## The one-paragraph version

Every day, volunteer **operator nodes** pull the public Resident Space Object
(RSO) catalog from Space-Track, roll it forward from yesterday's snapshot, and
hash the canonical bytes. That hash is the **consensus object**: if independent
nodes produce the same hash for a day, the day is *witnessed*, not merely hosted.
Each day is attested on **Ethereum** (the DocChain contract) and its bundle is
stored on **GitHub Releases + Arweave**. A lean **index** of per-day aggregates
lets anything browse the entire timeline instantly — without downloading
gigabytes — and the **NFT card** reads that index, loads a single day's full
catalog only when you stop to inspect it, and embeds a **baseline** so the art
renders the whole archive-to-date with zero network.

## The flow

```text
  Space-Track public catalog  (US Space Force)
            │  daily pull
            ▼
  ┌─────────────────────────────────────────────────┐
  │ PIPELINE  (pipeline/snapshot.py — Python stdlib)  │
  │   prior snapshot + bounded gp_history delta       │
  │     → canonical JSON catalog                      │
  │     → SHA-256 consensus hash (content_sha256)     │
  └─────────────────────────────────────────────────┘
            │  publishes to the `node` branch
            ▼
   ledger.json · manifest.json · catalog.json.gz · index/
            │
   ┌────────┴─────────────────────────────────┐
   ▼                                           ▼
  TIER 0 · PERMANENCE                  TIER 1 · LEAN INDEX
  Ethereum (hash + attesters +         index/manifest.json +
  block + locator) and Arweave         index/YYYY.json year-chunks
  (pay-once bundles). The source       per-day aggregates, ~340 B/day,
  of truth, forever.                   instant to scrub.
   │                                           │
   │                                           ▼
   │                                   TIER 2 · HEAVY CATALOG
   │                                   the ~11 MB day, fetched only
   │                                   when you stop to inspect it.
   │                                           │
   └──────────────────┬────────────────────────┘
                      ▼
            THE NFT CARD  (the dashboard)
      boots from the index (instant, exact HUD) · loads a
      day's catalog on demand via a CORS locator · embeds a
      baseline so it renders fully offline, forever.
```

Verification ties it together: download a bundle from anywhere, re-hash it, and
compare against the on-chain attestation. **Same hash → witnessed, not just
hosted.**

## The four moving parts (and why each exists)

1. **The witnessed archive.** The daily snapshot + its consensus hash. The hash
   covers a *core projection* of the catalog (the elset-intrinsic fields), not
   the raw bytes, because Space-Track back-patches nine mutable directory fields
   (decay date, names, country, RCS size…) on already-published rows. So two
   honest nodes can agree on the science even as the directory churns; what each
   node *learned and when* is recorded separately in `annotations.json`.
   *Why:* independent agreement is the whole trust model.

2. **The lean index (Tier 1).** A compact, year-chunked timeline of per-day
   aggregates — object count, on-orbit vs re-entered split, LEO/MEO/GEO bands,
   object types, the day's delta, a daily-changes summary, fingerprints, and a
   browser-fetchable catalog locator. ~340 bytes/day.
   *Why:* you can browse decades of the catalog instantly without ever pulling an
   11 MB day. It replaces the older single, bloated `ledger.json` for viewers.

3. **The NFT card (the dashboard).** A single self-contained HTML artwork that
   boots from the index through a ranked, fall-through list of backing **nodes**,
   shows exact numbers on the first frame, and only downloads a full day's
   catalog when you stop to inspect individual objects. It embeds a **baseline**
   (the index inlined as-of-mint) so it renders the full archive even if every
   gateway is dark.
   *Why:* the art is the public face, and an on-chain artwork must outlive any
   single host. See [card/DATA-ARCHITECTURE.md](../card/DATA-ARCHITECTURE.md).

4. **The permanence layers (Tier 0).** Ethereum carries the censorship-resistant
   index of every day (content hash, block, attesters, locator); Arweave stores
   the bundles and index pay-once-store-forever. Everything else is *derivable*
   from these.
   *Why:* "forever" can't depend on GitHub, or on us.

## Who does what

- **Operator nodes** run the pipeline daily and publish the archive, index, and
  pointers to their `node` branch. Anyone can run one — see
  [setup.md](setup.md).
- **Sweepers** (optional, funded) submit the signed daily attestations on-chain.
- **Consumers** (API partners, mirrors, the card) read `latest.json` /
  `ledger.json` / the `index/` and verify the bytes against the hashes — see
  [consuming.md](consuming.md).
- **The card / NFT** is just a consumer with a face: it reads the index and
  renders it.

## How you know it's real

- **Reproducible:** anyone can rebuild a day from `prior snapshot + bounded
  gp_history delta` and get the same canonical bytes.
- **Witnessed:** the consensus hash (`content_sha256`) is attested on Ethereum;
  independent nodes that publish the same hash are agreeing, not copying.
- **Address-independent:** trust comes from the hash and the chain, availability
  from whichever mirror you prefer. A bundle proves itself byte-for-byte
  regardless of where you fetched it. (Arweave tx ids are *not* content
  addresses — always verify fetched bytes against the recorded hash.)

## Where to go deeper

| You want to… | Read |
|---|---|
| Get the public-value pitch | [faq.md](faq.md) |
| Run a node | [setup.md](setup.md) · [operator.md](operator.md) |
| Understand the daily snapshot + index format | [snapshot-spec.md](snapshot-spec.md) |
| Understand the consensus core vs. observation log | [chain.md](chain.md) · [profile.md](profile.md) |
| Consume the archive (pointers, index) | [consuming.md](consuming.md) |
| Understand the card / NFT data architecture | [card/DATA-ARCHITECTURE.md](../card/DATA-ARCHITECTURE.md) |
| Verify a day yourself | [verification.md](verification.md) |
| See the full design rationale | [background.md](background.md) |
