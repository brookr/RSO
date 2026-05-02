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
of the network. It means your fork runs the same daily snapshot logic and
publishes its own hash chain from your own GitHub account.
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
add the two Space-Track secrets, and let the scheduled workflow run. The
default `main` branch is the code/controller branch. The running archive state
lives on a `node` branch created automatically by the daily workflow if your
fork does not already have one.

## What success looks like

A healthy operator run produces five visible things:

1. A green workflow run in the **Actions** tab
2. A `node` branch in your fork
3. A new daily metadata folder under `data/YYYY/MM/DD/` on `node`
4. An updated `ledger.json` on `node`
5. A release asset named `rso-archive-YYYY-MM-DD.tar.gz`

If you also add an Arweave wallet secret, the same publish step records an
extra `storage.json` receipt showing the GitHub Release location and Arweave
transaction ID for that day.

For a normal daily snapshot, the committed day folder should contain:

- `manifest.json`
- `delta.json`
- `audit.json`
- `visibility_state.json`

The latest two archived days also keep `catalog.json.gz` in Git on the `node`
branch. That small rolling cache lets the workflow read the prior full catalog
directly from the fork before it has published any of its own release bundles.

## Branches

The branch split is intentional:

- `main`: code, docs, workflows, and the lightweight controller action
- `node`: the running node state, including `data/`, `ledger.json`, generated
  reports, release receipts, and the latest two retained full catalogs

By default, the daily workflow first updates `main` from upstream `OMPub/RSO`,
then merges the latest code into `node` while preserving node-generated state.
That gives normal operators daily code updates without overwriting their own
archive outputs. Standalone operators can disable this by setting the repository
variable `RSO_AUTO_UPDATE_CODE=false`.

## First-time path

### 1. Fork the repository

On GitHub, open this repo and press **Fork**. On the fork form, make sure the
fork includes all branches. In GitHub's UI, that means leaving "Copy the main
branch only" **UNchecked**.

That creates your operator copy at:

```text
https://github.com/YOUR_USERNAME/RSO
```

Including all branches copies the upstream `node` branch into your fork, so
your first run already has the latest bootstrap catalog state. If you
accidentally fork only `main`, the daily workflow can still create `node` and
import the upstream archive state on its first run, but copying all branches is
the simpler and more transparent setup.

After creating the fork, do **NOT** use GitHub's "**Sync fork**" button as a
normal maintenance habit. The daily workflow already updates your fork's `main`
from upstream code and then applies that code to your `node` branch without
overwriting node-generated archive state. Manual fork syncs can be useful for
rare workflow-controller updates, but do them deliberately and only on `main`;
never use a sync or reset operation that overwrites your `node` branch.

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

Choose read/write access if GitHub offers it. The daily workflow needs to
update `main`, create or update `node`, and commit archive metadata back into
your fork.

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

Optional:

```text
ARWEAVE_JWK
ARWEAVE_FORCE_CHUNK_UPLOAD
RSO_WORKFLOW_UPDATE_TOKEN
```

If you want your fork to publish to Arweave automatically during the normal
daily workflow, add `ARWEAVE_JWK` as a repository secret containing the full
Arweave wallet JSON. If that secret is present, the node uploads to Arweave
alongside the GitHub Release bundle. No separate workflow is needed. Small
bundles go in one inline Arweave transaction; larger bundles automatically use
Arweave chunk upload. If you want to force chunk upload for testing before the
bundle naturally exceeds the inline limit, set `ARWEAVE_FORCE_CHUNK_UPLOAD` to
`true`.

The default `GITHUB_TOKEN` can update normal code files, but GitHub may reject
self-updates to `.github/workflows/*`. If you want your fork to automatically
accept upstream workflow-file changes too, create a fine-grained token for this
repository with Contents write and Workflows write, then save it as
`RSO_WORKFLOW_UPDATE_TOKEN`. If you do not add that token, normal pipeline code
updates still work. Workflow controller changes may show a warning and require
clicking GitHub's **Sync fork** button on `main`.

### 4. Run the validator first

Before pulling live data, prove your fork can run the read-only checks. On
`main`, the validator runs tests and syntax checks. Once your `node` branch
exists, running the validator from `node` also checks archived manifests,
ledger entries, and retained `catalog.json.gz` files.

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
- your fork has a `node` branch
- a new or refreshed `data/YYYY/MM/DD/manifest.json` appears on `node`
- `ledger.json` updates on `node`
- `catalog.json.gz` remains committed on `node` for the two newest archived days
- a matching release asset appears in your fork's Releases

That proves your fork can:

- update code from upstream `main`
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

That document is on the `node` branch. It is the agreed `genesis_from_gp`
baseline for the live archive.

### 7. Compare with another operator

For the same date, compare:

- `ledger.json` hash
- `manifest.json` hash
- `object_count`

Matching hashes across forks are the real success condition.

## Where to look when you are lost

- `README.md`: full technical walkthrough and command reference
- `GLOSSARY.md`: orbital-data terms and field definitions
- `node` branch: your generated archive state
- `data/YYYY/MM/DD/manifest.json`: the daily hash and provenance summary
- `ledger.json`: rolling public hash chain
- `Releases`: where your fork publishes full daily bundles
- `reports/rehearsal/`: pre-baseline practice data, separate from the official lineage

## The outputs to remember

Every successful producer run writes to four places:

- Git metadata on `node`: `data/` and `ledger.json`
- Git bootstrap cache on `node`: `catalog.json.gz` for the two newest archived days
- Release bundle: `rso-archive-YYYY-MM-DD.tar.gz`
- Publish receipt: `data/YYYY/MM/DD/storage.json`

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
- `main` does not show the latest workflow controller change because automatic
  code sync changed `.github/workflows/*` and your fork does not have
  `RSO_WORKFLOW_UPDATE_TOKEN`

If the daily workflow can read Space-Track but fails on `git push`, check
workflow write permissions first. If it only warns while updating `main` from
upstream, either click **Sync fork** on GitHub or add
`RSO_WORKFLOW_UPDATE_TOKEN`; the run can still continue with the locally fetched
pipeline code.

Your fork can also be forked by others as long as it preserves these
conventions: `main` stays code/controller-focused, node-generated state stays
on `node`, the two newest catalogs remain available for bootstrap, and the
workflows keep committing the daily hash chain. Share it with friends, and star
the upstream repo so other operators can find the canonical project.
