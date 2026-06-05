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
- Treasury sweeper with daily operator-backing snapshots, URI bundle validation,
  simulation, and funded submission path

## Next

- Deploy DocChain v1 to Ethereum mainnet and verify it on Etherscan
- Configure the RSO Meme Card token ID after issuance
- Add production TDH/backing snapshot generation and branch scoring
- Daily diff computation for objects added, updated, and carried forward
- TDH-weighted community confirmations
- Dynamic NFT artwork for 6529 The Memes
- Browser verification client and visualization layer
- Operator publication hooks for arbitrary upload destinations, so individual
  nodes can add self-hosted, R2, S3, GitLab, IPFS, or other mirrors without
  forking the upstream daily workflow
- Orbital Witness template for additional datasets
- NEO witness archive
