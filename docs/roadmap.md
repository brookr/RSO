# Roadmap

## Done

- Daily rolling snapshot pipeline
- Roll forward from an existing prior-day base snapshot
- Local hash verification
- Midnight UTC rolling `gp_history` deltas
- Current `gp` visibility audit and missing/reappeared state
- Jan 1-to-current replay analysis against current `gp`
- Deterministic GitHub Release bundles
- Two-day Git bootstrap cache on `node`
- Optional Arweave upload during publish
- Per-day `storage.json` receipts
- `main` / `node` branch split for fork-safe operation
- Generic DocChain v1 helpers vendored from sibling `doc-chain`
- RSO static DocChain indexer with Sepolia seed support and custom deployment config
- Automatic signed attestation artifacts using `DISPOSABLE_NO_FUNDS_ETH_PRIVATE_KEY`
- Treasury sweeper with daily TDH support snapshots, signed node identity,
  public verification evidence, URI bundle validation, simulation, and funded
  submission path
- Static index weighting with separate direct-witness TDH and verified signed
  node backing; only net-positive usable backing contributes to branch weight
- Lean Tier-1 index: year-chunked per-day aggregates (on-orbit/re-entered split,
  band/type counts, daily delta + changes summary) with CORS catalog locators
- Card-parity aggregates carried in manifest, ledger, and index, with offline
  backfill (`rebuild-content`) and validator parity

## Next

- Deploy DocChain v1 to Ethereum mainnet and verify it on Etherscan
- Configure the RSO Meme Card token ID after issuance
- Add production TDH support snapshot generation, including public allocation
  evidence and enforcement that each participating identity allocates exactly
  its card-specific TDH across the absolute values of its signed allocations
- Publish and fingerprint daily TDH support snapshots and sweeper reports in
  durable storage so historical weighting inputs are independently reproducible
- Daily diff computation for objects added, updated, and carried forward
- TDH-weighted community confirmations
- Dynamic NFT artwork for 6529 The Memes
- Browser verification client and visualization layer
- Hardened discovery and signed-artifact fetching for self-hosted
  `domain:hostname` nodes
- Operator publication hooks for arbitrary upload destinations, so individual
  nodes can add self-hosted, R2, S3, GitLab, IPFS, or other mirrors without
  forking the upstream daily workflow
- Orbital Witness template for additional datasets
- NEO witness archive
