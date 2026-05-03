# Verification

The daily hash is computed from canonical catalog bytes. Storage locations are
retrieval hints; they are not part of consensus.

## Quick Verify

On a checkout with the archive state available:

```bash
python3 pipeline/snapshot.py verify --date 2026-05-01
```

For the newest retained days, `catalog.json.gz` is present on `node`. For older
days, the verifier can use the matching release bundle.

## Manual Verify

1. Download the release bundle for a date.
2. Extract `catalog.json.gz`.
3. Decompress it.
4. Compute SHA-256 of the raw catalog bytes.
5. Compare the hash with `manifest.json`, `release-manifest.json`, or
   `ledger.json`.

Example:

```bash
tar -xzf rso-archive-2026-05-01.tar.gz catalog.json.gz
gunzip -k catalog.json.gz
sha256sum catalog.json
```

## What To Compare Across Operators

For the same date, independent operators should compare:

- `manifest.json` `sha256`
- `ledger.json` `sha256`
- `object_count`

Matching hashes across independently operated forks are the signal that the
public record is being witnessed, not merely hosted.

Some fields are expected to differ across operators, including retrieval time,
release URL, Arweave transaction ID, and storage receipt details.
