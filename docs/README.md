# RSO Documentation

Canonical project documentation. The repo [`README.md`](../README.md) has
the high-level pitch and quick-start; this index is the deeper map.

## Start here

- [How it all fits together](overview.md) — one-page tour of the archive, the
  lean index, and the NFT card, and how they connect. The doc to hand a
  newcomer or collector before the deeper specs below.

## Operating

For anyone running RSO infrastructure — an archive node, the sweeper, or both.

- [Operator guide](operator.md) — why operate, what to expect, the two
  roles (node and sweeper), funding models, operational expectations
- [Setup walkthrough](setup.md) — mechanical first-time path: fork,
  Actions, secrets, first run, optional attestations, and sweeper setup
- [Roll-forward](roll-forward.md) — catching a stale node back up to
  current
- [Replay findings](replay-findings.md) — notes from historical replay
  runs

## Protocol and architecture

For anyone building tools that produce, consume, or verify the archive.

- [Architecture](architecture.md) — how the pieces fit together
- [Snapshot specification](snapshot-spec.md) — bundle layout and
  canonical hashing rules; authoritative for bundle format
- [Attestation design](attestation-design.md) — RSO's DocChain profile,
  signed operator artifacts, sweeper submission, and indexer behaviour
- [Verification](verification.md) — how to verify a daily archive

## Reference

- [Glossary](glossary.md) — orbital-data terms and field definitions
- [Roadmap](roadmap.md) — what's planned next
- [Development](development.md) — local dev, replay, contributing
- [Background](background.md) — full design background and motivation
- [Acknowledgments](acknowledgments.md) — prior art and credits

## Conventions

- Files in `docs/` use lowercase kebab-case (`attestation-design.md`,
  `snapshot-spec.md`). Root-level metadata files (`README.md`,
  `AGENTS.md`) keep the conventional ALLCAPS.
- Cross-references inside `docs/` use sibling-relative paths
  (`operator.md`, not `docs/operator.md`).
- If a guide and a design doc disagree, the design doc wins.
