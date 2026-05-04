# Development

The project intentionally uses only the Python standard library.

## Requirements

- Python 3.10+
- Free Space-Track account for live snapshot commands
- No pip dependencies

## Tests

```bash
python3 -m unittest discover -s tests
```

## Local Snapshot Commands

Set credentials first:

```bash
export SPACETRACK_USER="your@email.com"
export SPACETRACK_PASS="your-password"
```

Common commands:

```bash
# Capture a genesis snapshot
python3 pipeline/snapshot.py genesis --date 2026-04-20

# Build today's rolling snapshot
python3 pipeline/snapshot.py daily

# Build or rebuild a specific date
python3 pipeline/snapshot.py daily --date 2026-05-01
python3 pipeline/snapshot.py daily --date 2026-05-01 --force

# Verify one stored snapshot
python3 pipeline/snapshot.py verify --date 2026-05-01

# Validate all archived snapshots on a node branch
python3 pipeline/snapshot.py validate

# Show the next date this checkout should archive
python3 pipeline/snapshot.py next-date

# Build deterministic release bundles
python3 pipeline/snapshot.py publish --date 2026-05-01 --storage-backend none

# Keep only the newest two full catalogs in Git after bundles exist
python3 pipeline/snapshot.py prune-catalogs --all --keep-latest 2 --require-bundle

# Restore local catalogs from release bundles
python3 pipeline/snapshot.py hydrate-catalogs --latest 2 --repo OMPub/RSO
```

For long replay or roll-forward runs, increase the request delay:

```bash
RSO_REQUEST_DELAY=12.5 python3 pipeline/snapshot.py replay --start 2026-01-01
```

## Related Docs

- [Operator guide](../OPERATOR.md)
- [Roll-forward guide](../ROLL_FORWARD.md)
- [Replay findings](../REPLAY_FINDINGS.md)
- [Glossary](../GLOSSARY.md)
