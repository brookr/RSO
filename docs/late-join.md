# Late-Join Runbook: From Zero to a Fully Attested Node

A node that joins the network after genesis can build, verify, publish, and
attest the entire chain history — and land every day on chain in **one
transaction**. This path is exercised in CI by the project's second node.

## What you end up with

- Your `node` branch carries every archive day (verified upstream adoptions
  plus your own captures), all with content fields.
- Your storage receipts point at the shared canonical uploads for adopted days
  (no duplicate copies) and your own published bundles for your own days.
- Your signer has attested every day since genesis under your own publication
  locator, and a single `attestBatch` transaction landed all of them.
- The sweeper verifies your claims and the index shows you in every day's
  agreement group.

## Prerequisites

Complete the standard operator setup (`docs/setup.md`): fork with all
branches, Space-Track secrets, a disposable signer secret
(`DISPOSABLE_NO_FUNDS_ETH_PRIVATE_KEY` or keystore variants), and
`RSO_ATTESTER_ADDRESS`.

## One-dispatch path

Run the **Attest Archive Range (v2)** workflow on your fork (`node` branch):

| Input | Value |
|---|---|
| `start` / `end` | the full range, e.g. `2026-04-20` → today |
| `hydrate_upstream` | `true` |
| `keep_from` | the first date you captured yourself (omit for a pure cold start) |

The workflow then:

1. **Hydrates** every day before `keep_from` from the upstream node's
   published bundles — fetching the upstream storage receipt, verifying the
   bundle bytes against its `bundle_sha256`, re-deriving the core projection
   hash independently from the catalog bytes, and only then adopting the day
   artifacts and the *same upload locations* into your archive. Forked nodes
   inherit upstream's old day directories; those are stale copies, not your
   observations, and are replaced by verified ones.
2. **Rebuilds** content fields for your own days that lack them
   (idempotent; adopted days are skipped so their artifacts stay
   byte-identical to the published bundles).
3. **Publishes** your own days' bundles to your releases (adopted days are
   refused — they are already published upstream; you attest the shared
   locations instead of re-uploading).
4. **Signs** every day in the range with your repo-secret signer. Parent
   hashes chain from the genesis day (zero parentHash); your locator carries your
   own `nodeId` with the verified bundle fingerprint and locations.

## Submit everything in one transaction

From a checkout of your `node` branch, with a funded courier key (any key —
the courier only pays gas; your attestations are already signed):

```bash
export SUBMITTER_PRIVATE_KEY=0x...     # courier / gas payer
export RSO_DOCCHAIN_ADDRESS=0x867FcC4f0339009043E9F6e554DD516Bcf1bcaa9
export RSO_DOCCHAIN_RPC_URL=https://...

python3 attestation/submit_batch.py \
  --start 2026-04-20 --end 2026-06-09 --require-all --dry-run   # expect: store N, skip 0
python3 attestation/submit_batch.py \
  --start 2026-04-20 --end 2026-06-09 --require-all
```

The contract skips already-recorded attestations, so resubmitting after a
partial failure is always safe. A 51-day batch costs ~2.5M gas.

## Independent verification (optional but encouraged)

Don't trust the upstream bundles — re-derive the chain yourself from
Space-Track (see `docs/chain.md`, "Verifying the chain from nothing") and
compare your derived `content_sha256` values against the manifests you
adopted. The hydration step already re-derives each adopted day's core hash
from the catalog bytes; the full from-source replay is the strongest check.

## Costs and limits

- Hydration fetches each adopted day's bundle once (~11 MB/day).
- Signing is local to the workflow run; Space-Track is not queried.
- One `attestBatch` transaction per node, any number of days (gas-bounded;
  hundreds of days fit comfortably in a block).
