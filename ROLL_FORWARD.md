# Roll Forward

`roll-forward` builds missing daily snapshots from a known prior archive state.
It is the operational catch-up command for this repository.

It does not reconstruct history from nothing. It starts with the full catalog
from the day before `--start`, applies one bounded `gp_history` publication
window, writes that day's snapshot, then repeats until `--end`.

## When To Use It

Use `roll-forward` when a node has a valid archived base day but is missing one
or more later days.

Common cases:

- A fork was created after the upstream repo had already archived several days.
- A scheduled GitHub Actions run was disabled or failed for a day.
- A maintainer needs to repair a local checkout by rebuilding a contiguous range
  from a known good prior snapshot.

Do not use it to create the first archive day. The first archive day is
`genesis`. Do not use it for historical reconstruction before genesis unless
the output is clearly labeled as reconstructed history.

## How It Works

For this command:

```bash
python pipeline/snapshot.py roll-forward --start 2026-04-27 --end 2026-04-29
```

the repo must already have a valid 2026-04-26 snapshot:

```text
data/2026/04/26/manifest.json
```

It must also be able to read the 2026-04-26 full catalog bytes. That means
`data/2026/04/26/catalog.json.gz` is present locally, or the matching release
bundle can be fetched.

The command then performs deterministic state transitions:

```text
2026-04-27 = 2026-04-26 catalog
           + gp_history rows from 2026-04-26T00:00:00Z through 2026-04-27T00:00:00Z

2026-04-28 = 2026-04-27 catalog
           + gp_history rows from 2026-04-27T00:00:00Z through 2026-04-28T00:00:00Z

2026-04-29 = 2026-04-28 catalog
           + gp_history rows from 2026-04-28T00:00:00Z through 2026-04-29T00:00:00Z
```

Each step writes the same artifacts as a normal daily run:

- `manifest.json`
- `delta.json`
- `catalog.json.gz`

The daily workflow uses this same command internally when a fork needs to catch
up before producing the current day's snapshot.

## Commands

Roll forward a date range:

```bash
python pipeline/snapshot.py roll-forward --start 2026-04-27 --end 2026-04-29
```

Rebuild an existing range deliberately:

```bash
python pipeline/snapshot.py roll-forward --start 2026-04-27 --end 2026-04-29 --force
```

Use a slower Space-Track request delay for long ranges:

```bash
RSO_REQUEST_DELAY=12.5 python pipeline/snapshot.py roll-forward --start 2026-04-21 --end 2026-04-29
```

After rolling forward, validate:

```bash
python pipeline/snapshot.py validate
```

Build release bundles for the produced range:

```bash
python pipeline/snapshot.py publish --start 2026-04-27 --end 2026-04-29
```

Prune older local full catalogs while keeping the bootstrap cache:

```bash
python pipeline/snapshot.py prune-catalogs --all --keep-latest 2 --require-bundle
```

## Relationship To Replay

`roll-forward` starts from an existing prior full catalog and catches up from
there.

`replay` starts from an empty state and was used as a validation experiment for
bounded `gp_history` behavior. Replay is documented separately in
[REPLAY_FINDINGS.md](REPLAY_FINDINGS.md), and it is not a substitute for normal
roll-forward operation.
