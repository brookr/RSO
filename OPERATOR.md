# RSO Operator Guide

This guide is for someone who wants to operate an independent Orbital Witness
node. It is written as a detailed, beginner-friendly resource for people who
want the full path explained clearly, and questions or requests for
clarification are welcome.

## Why do this?

The public space object catalog is important, but fragile:

- One source publishes it: Space-Track (relies on government funding and staff).
- One person mirrors it publicly at CelesTrak.
- Without multiple independent archives, later edits or removals are hard to prove.

Operating an independent Orbital Witness node strengthens the decentralization
of the network. It means you keep your own fork of the code, run the same daily
snapshot logic, and publish the same hash chain from your own GitHub account.
If many operators get the same result independently, the archive is being
witnessed, not merely hosted.

## What you need

Minimum:

- A GitHub account
- A free Space-Track account
- Permission to enable GitHub Actions on your fork

A fork is all you need. No local clone is necessary for normal operation,
because GitHub Actions can run the node entirely inside your fork.

The intended setup path is deliberately short: fork the repo, enable Actions,
add the two Space-Track secrets, and let the scheduled workflow run. The latest
full catalog state needed for the first daily roll-forward is already committed
in the repo.

## What success looks like

A healthy operator run produces four visible things:

1. A green workflow run in the **Actions** tab
2. A new daily metadata folder under `data/YYYY/MM/DD/`
3. An updated `ledger.json`
4. A release asset named `rso-archive-YYYY-MM-DD.tar.gz`

For a normal daily snapshot, the committed day folder should contain:

- `manifest.json`
- `delta.json`
- `audit.json`
- `visibility_state.json`

The latest two archived days also keep `catalog.json.gz` in Git. That small
rolling cache is what makes a fresh fork self-starting: the workflow can read
the prior full catalog directly from the fork before it has published any of
its own release bundles.

## First-time path

### 1. Fork the repository

On GitHub, open this repo and press **Fork**. You can accept the default fork
settings.

That creates your operator copy at:

```text
https://github.com/YOUR_USERNAME/RSO
```

### 2. Enable GitHub Actions

In your fork:

```text
Settings -> Actions -> General
```

Under **Actions permissions**, choose **Allow all actions and reusable
workflows**.

Then check workflow write access:

```text
Settings -> Actions -> General -> Workflow permissions
```

Choose read/write access if GitHub offers it. The daily workflow needs to commit
archive metadata back into your fork.

### 3. Add your Space-Track credentials

Create a free [Space-Track.org](https://www.space-track.org/auth/createAccount)
account. Space-Track will email you a link to confirm your account and set your
password.

In your fork:

```text
Settings -> Secrets and variables -> Actions -> Repository secrets
```

Create:

```text
SPACETRACK_USER
SPACETRACK_PASS
```

Use the email address you signed up with as `SPACETRACK_USER`. Use the password
you created during Space-Track signup as `SPACETRACK_PASS`. These are the only
required secrets for the current GitHub-release operator path.

### 4. Run the validator first

Before pulling live data, prove your fork can run the read-only checks. The
validator also confirms that the latest retained `catalog.json.gz` files are
present and match their manifests.

On GitHub:

```text
top navigation bar -> Actions -> Validate RSO Archive -> Run workflow
```

After clicking **Run workflow**, leave **Use workflow from** set to branch
`main`.

Expected result: green, usually complete in less than a minute.

### 5. Enable and run the daily snapshot

GitHub disables scheduled workflows by default in forks. This is easy to miss.
In your fork, go to:

```text
top navigation bar -> Actions -> Daily RSO Snapshot
```

If GitHub shows:

```text
This scheduled workflow is disabled because scheduled workflows are disabled by default in forks.
```

Click **Enable workflow**.

Then run it manually once. This is not optional; it proves the producer workflow
is enabled and can write archive data into your fork.

On GitHub:

```text
top navigation bar -> Actions -> Daily RSO Snapshot -> Run workflow
```

Use:

```text
Use workflow from = main
mode = auto
date = blank, unless you deliberately want one specific date
overwrite = checked for this first run
```

After that first successful run, leave **overwrite** unchecked for normal daily
operation. The workflow may complete in 30 seconds, or it may take 5 minutes or
more depending on Space-Track response time and whether your fork needs to catch
up.

Expected result:

- workflow succeeds
- a new or refreshed `data/YYYY/MM/DD/manifest.json` appears for the run date
- `ledger.json` updates
- `catalog.json.gz` remains committed for the two newest archived days
- a matching release asset appears

That proves your fork can:

- read the prior snapshot
- apply a bounded `gp_history` delta
- write the new manifest/audit files
- publish the release bundle

### 6. Understand the official genesis

The official chain already starts at `2026-04-20`. New operators normally do
not create a fresh genesis document. They validate the existing lineage and
then continue it.

If you want to inspect the first document in the chain, look at:

```text
data/2026/04/20/manifest.json
```

That document is the agreed `genesis_from_gp` baseline for the live archive.

### 7. Compare with another operator

For the same date, compare:

- `ledger.json` hash
- `manifest.json` hash
- `object_count`

Matching hashes across forks are the real success condition.

## Where to look when you are lost

- `README.md`: full technical walkthrough and command reference
- `GLOSSARY.md`: orbital-data terms and field definitions
- `data/YYYY/MM/DD/manifest.json`: the daily hash and provenance summary
- `ledger.json`: rolling public hash chain
- `Releases`: where the full daily bundle is published
- `reports/rehearsal/`: pre-baseline practice data, separate from the official lineage

## The outputs to remember

Every successful producer run writes to three places:

- Git metadata: `data/` and `ledger.json`
- Git bootstrap cache: `catalog.json.gz` for the two newest archived days
- Release bundle: `rso-archive-YYYY-MM-DD.tar.gz`

Older full catalogs are pruned from Git after their deterministic release
bundles are built. That split keeps the repo small while making new forks able
to continue the chain without manual bootstrapping.

The daily hash comes from the canonical snapshot bytes, not from the release
URL or storage location. Different operators can publish the same bytes in
different places and still agree on the same daily hash.

## If something fails

Most first-run failures are one of these:

- Actions not enabled
- Workflow permissions still read-only
- `SPACETRACK_USER` or `SPACETRACK_PASS` missing
- A date already exists and needs **overwrite** checked during a deliberate
  rebuild

If the daily workflow can read Space-Track but fails on `git push`, check
workflow write permissions first.

Your fork can also be forked by others as long as it preserves these
conventions: the archive metadata stays in `data/`, the two newest catalogs
remain available for bootstrap, and the workflows keep committing the daily
hash chain. Share it with friends, and star the upstream repo so other operators
can find the canonical project.
