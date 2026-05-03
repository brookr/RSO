# RSO Documentation

Canonical project documentation. The repo [`README.md`](../README.md) has
the high-level pitch and quick-start; this index is the deeper map.

## Operating

For anyone running RSO infrastructure — an archive node, a relayer, or
both.

- [Operator guide](operator.md) — why operate, what to expect, the two
  roles (node and relayer), funding models, operational expectations
- [Setup walkthrough](setup.md) — mechanical first-time path: fork,
  Actions, secrets, first run; optional relayer activation (TBD)
- [Roll-forward](roll-forward.md) — catching a stale node back up to
  current
- [Replay findings](replay-findings.md) — notes from historical replay
  runs

## Protocol and architecture

For anyone building tools that produce, consume, or verify the archive.

- [Architecture](architecture.md) — how the pieces fit together
- [Snapshot specification](snapshot-spec.md) — bundle layout and
  canonical hashing rules; authoritative for bundle format
- [Attestation design](attestation-design.md) — EIP-712 contract,
  relayer rules, dApp behaviour
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
