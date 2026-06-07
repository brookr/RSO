# RSO Operator Setup

The mechanical first-time path for archive nodes and the optional sweeper.
Conceptual context and policy decisions — *why* you'd run one, funding models,
operational expectations — live in [`operator.md`](operator.md).

This guide is written as a detailed, beginner-friendly walkthrough.
Questions and requests for clarification are welcome.

## What you need

Minimum, for an archive node:

- A GitHub account
- A free Space-Track account
- Permission to enable GitHub Actions on your fork

A fork is all you need. No local clone is necessary for normal operation,
because GitHub Actions can run the node entirely inside your fork.

The intended setup path is deliberately short: fork the repo, enable
Actions, add the two Space-Track secrets, and let the scheduled workflow
run. Optional attestation secrets let the same workflow sign and submit
DocChain attestations for a sweeper to submit later. The default `main` branch
is the code/controller branch. The running archive state lives on a `node`
branch created automatically by the daily workflow if your fork does not
already have one.

## Setting up an archive node

### 1. Fork the repository

On GitHub, open this repo and press **Fork**. On the fork form, make sure
the fork includes all branches. In GitHub's UI, that means leaving "Copy
the main branch only" **UNchecked**.

That creates your operator copy at:

```text
https://github.com/YOUR_USERNAME/RSO
```

Including all branches copies the upstream `node` branch into your fork,
so your first run already has the latest bootstrap catalog state. If you
accidentally fork only `main`, the daily workflow can still create `node`
and import the upstream archive state on its first run, but copying all
branches is the simpler and more transparent setup.

After creating the fork, do **NOT** use GitHub's "**Sync fork**" button as
a normal maintenance habit. The daily workflow already updates your fork's
`main` from upstream code and then applies that code to your `node`
branch without overwriting node-generated archive state. Manual fork
syncs can be useful for rare workflow-controller updates, but do them
deliberately and only on `main`; never use a sync or reset operation that
overwrites your `node` branch.

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
update `main`, create or update `node`, and commit archive metadata back
into your fork.

### 3. Add your Space-Track credentials

Create a free [Space-Track.org](https://www.space-track.org/auth/createAccount)
account. Space-Track will email you a link to confirm your account and set
your password.

In your fork:

```text
Settings -> Secrets and variables -> Actions -> Repository secrets
```

Create:

```text
SPACETRACK_USER
SPACETRACK_PASS
```

Use the email address you signed up with as `SPACETRACK_USER`. Use the
password you created during Space-Track signup as `SPACETRACK_PASS`. These
are the only required secrets for the current GitHub-release operator
path.

Optional:

```text
ARWEAVE_JWK
RSO_WORKFLOW_UPDATE_TOKEN
DISPOSABLE_NO_FUNDS_ETH_PRIVATE_KEY
DISPOSABLE_NO_FUNDS_ETH_KEYSTORE_JSON
DISPOSABLE_NO_FUNDS_ETH_KEYSTORE_PASSWORD
```

If you want your fork to publish to Arweave automatically during the
normal daily workflow, add `ARWEAVE_JWK` as a repository secret containing
the full Arweave wallet JSON. If that secret is present, the node uploads
to Arweave alongside the GitHub Release bundle. No separate workflow is
needed. Arweave uploads always use chunked transaction data, so operators
do not need to configure bundle-size or gateway-body-limit knobs.

The default `GITHUB_TOKEN` can update normal code files, but GitHub may
reject self-updates to `.github/workflows/*`. If you want your fork to
automatically accept upstream workflow-file changes too, create a
fine-grained token for this repository with Contents write and Workflows
write, then save it as `RSO_WORKFLOW_UPDATE_TOKEN`. If you do not add that
token, normal pipeline code updates still work. Workflow controller
changes may show a warning and require clicking GitHub's **Sync fork**
button on `main`.

### 3a. Optional: add automatic attestations

Automatic attestations use a disposable Ethereum signing key. This key is
not a personal wallet and should never hold ETH, NFTs, or tokens. It signs
RSO DocChain claims; a separate sweeper pays gas for backed operators.

Create a fresh EOA address for the node. Store the private key as the
repository secret:

```text
DISPOSABLE_NO_FUNDS_ETH_PRIVATE_KEY
```

That raw private-key secret is the simplest disposable-node setup. For a more
hardened setup, store an encrypted Foundry-compatible keystore JSON as
`DISPOSABLE_NO_FUNDS_ETH_KEYSTORE_JSON` and its password as
`DISPOSABLE_NO_FUNDS_ETH_KEYSTORE_PASSWORD`. The workflow writes those secrets
to temporary files and passes only file paths to `cast`, so the private key does
not appear in the process command line.

Store the matching public address as a repository variable or secret:

```text
RSO_ATTESTER_ADDRESS
```

For RSO V1, leave `RSO_ON_BEHALF_OF_ADDRESS` unset unless you are testing a
future DocChain profile that needs delegated identity metadata. Card holders
back your stable `RSO_NODE_ID`, not the disposable signing address. The workflow
defaults `RSO_NODE_ID` to `github:OWNER/REPO`; set it explicitly only when the
node uses another supported identity such as `domain:node.example.org`. If you
rotate the disposable key, update the raw key or keystore secret and
`RSO_ATTESTER_ADDRESS`. Backers do not need to move support, and no contract
change is needed.

Finally, configure the DocChain deployment:

```text
RSO_DOCCHAIN_ADDRESS
RSO_DOCCHAIN_CHAIN_ID
RSO_DOCCHAIN_DEPLOYMENT_BLOCK
```

`RSO_DOCCHAIN_ADDRESS`, `RSO_DOCCHAIN_CHAIN_ID`,
and `RSO_DOCCHAIN_DEPLOYMENT_BLOCK` can be repository variables. The daily
workflow skips attestation signing cleanly if any required setting is absent, so
archive operation does not depend on this optional path.

### 4. Run the validator first

Before pulling live data, prove your fork can run the read-only checks. On
`main`, the validator runs tests and syntax checks. Once your `node`
branch exists, running the validator from `node` also checks archived
manifests, ledger entries, and retained `catalog.json.gz` files.

On GitHub:

```text
top navigation bar -> Actions -> Validate RSO Archive -> Run workflow
```

After clicking **Run workflow**, leave **Use workflow from** set to branch
`main`.

Expected result: green, usually complete in less than a minute.

### 5. Enable and run the daily snapshot

GitHub disables scheduled workflows by default in forks. This is easy to
miss. In your fork, go to:

```text
top navigation bar -> Actions -> Daily RSO Snapshot
```

If GitHub shows:

```text
This scheduled workflow is disabled because scheduled workflows are disabled by default in forks.
```

Click **Enable workflow**.

Then run it manually once. This is not optional; it proves the producer
workflow is enabled and can write archive data into your fork.

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

After that first successful run, leave **overwrite** unchecked for normal
daily operation. The workflow may complete in 30 seconds, or it may take
5 minutes or more depending on Space-Track response time and whether your
fork needs to catch up.

Expected result:

- workflow succeeds
- your fork has a `node` branch
- a new or refreshed `data/YYYY/MM/DD/manifest.json` appears on `node`
- `ledger.json` updates on `node`
- `catalog.json.gz` remains committed on `node` for the two newest
  archived days
- a matching release asset appears in your fork's Releases
- if automatic attestations are configured, `data/attestations/rso-docchain-state.json`
  and `data/attestations/signed/YYYY-MM-DD.json` update

That proves your fork can:

- update code from upstream `main`
- read the prior snapshot
- apply a bounded `gp_history` delta
- write the new manifest/audit files
- publish the release bundle
- optionally sign a DocChain attestation for sweepers

### 6. Understand the official genesis

The official chain already starts at `2026-04-20`. New operators normally
do not create a fresh genesis document. They validate the existing lineage
and then continue it.

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

## Setting up a sweeper (optional)

A sweeper is a funded courier. Most node operators do not need to run one. Run a
sweeper only if you understand that the sweeper key pays gas and must be
treated as a limited treasury allowance.

1. Clone this repo on the machine or workflow that will submit transactions.
2. Choose an operator source. By default, the sweeper can discover candidate
   repositories from the upstream GitHub fork graph with
   `github-forks:OMPub/RSO`. You can still copy
   `sweeper/operators.example.json` to `sweeper/operators.json` for a manual
   registry.
3. Configure mainnet RPC, DocChain, and the daily TDH support snapshot.
4. Generate and fund a hot wallet within the ceiling described in
   [`operator.md`](operator.md).
5. Run:

```text
python3 sweeper/rso_sweeper.py \
  --start YYYY-MM-DD \
  --end YYYY-MM-DD \
  --backing "data/backing/{date}.json"
```

The repository also includes a daily **Sweep RSO Attestations** GitHub Actions
workflow. It is scheduled for one hour after the expected TDH support snapshot
boundary. By default, it uses GitHub fork discovery as the operator source and
runs when the required sweeper secrets/variables are configured. Its scheduled
run checks yesterday plus the prior date once, giving deferred claims a bounded
next-day retry. If a TDH support snapshot is not available yet, that date is
skipped.

The sweeper publishes public date reports to:

```text
reports/sweeper/YYYY-MM-DD.json
```

Each accepted record includes the selected node id, the node-declared and signed
attester, the signed claim fingerprint, every verified publication location,
and the submission result. These reports provide the public history of which
signing addresses each node used.

Every node also has a default **Check RSO Sweeper Report** workflow. It runs
after the sweeper window, reads the public report for its own `nodeId`, and
opens or updates one local issue if the sweeper reports that the node was
missing, deferred, or not found. If a later report shows the node is healthy, the
workflow closes the issue.

Minimum sweeper environment:

```text
RSO_SWEEPER_RPC_URL
RSO_DOCCHAIN_ADDRESS
RSO_SWEEPER_KEYSTORE_JSON
RSO_SWEEPER_KEYSTORE_PASSWORD
RSO_OPERATOR_BACKING_SNAPSHOT
```

The sweeper key is funded, so the workflow intentionally does not use a raw
private-key argv path. Configure `RSO_SWEEPER_KEYSTORE_JSON` and
`RSO_SWEEPER_KEYSTORE_PASSWORD`, or configure an existing runner keystore with
`RSO_SWEEPER_KEYSTORE` or `RSO_SWEEPER_ACCOUNT`.

Important optional settings:

```text
RSO_SWEEPER_DRY_RUN
RSO_SWEEPER_OPERATORS
RSO_SWEEPER_MAX_FORKS
RSO_SWEEPER_MAX_BACKED_NODE_DISCOVERY
RSO_SWEEPER_TOP_OPERATORS
RSO_SWEEPER_MIN_CARD_SPECIFIC_TDH
RSO_SWEEPER_MAX_PUBLICATION_LOCATIONS
RSO_SWEEPER_FETCH_RETRIES
RSO_SWEEPER_REPORT_REPO
RSO_SWEEPER_REPORT_BRANCH
RSO_NODE_ID
```

The default TDH support snapshot location is:

```text
data/backing/{date}.json
```

The default sponsorship policy funds the top 5 backed operators for each day.
The sweeper also uses the daily support snapshot to discover backed GitHub nodes
that are not present in the upstream fork graph. This discovery is TDH-ranked
and capped by `RSO_SWEEPER_MAX_BACKED_NODE_DISCOVERY`, defaulting to 100.
The static indexer can consume a directory of these daily snapshots with
`--tdh-support data/backing` and the public reports with
`--sweeper-reports reports/sweeper`. It applies support only to the matching
archive date.

For background on the operator/sweeper split, see [`operator.md`](operator.md).
For RSO profile details — DocBlock validation, sponsorship policy, deadline
handling, etc. — see [`attestation-design.md`](attestation-design.md).

A node operator can run a sweeper alongside their archive node, but nothing is
shared between them automatically. They are separate services with separate
keys and separate concerns.

## If something fails

Most first-run node failures are one of these:

- Actions not enabled
- Workflow permissions still read-only
- `SPACETRACK_USER` or `SPACETRACK_PASS` missing
- A date already exists and needs **overwrite** checked during a deliberate
  rebuild
- `main` does not show the latest workflow controller change because
  automatic code sync changed `.github/workflows/*` and your fork does not
  have `RSO_WORKFLOW_UPDATE_TOKEN`

If the daily workflow can read Space-Track but fails on `git push`, check
workflow write permissions first. If it only warns while updating `main`
from upstream, either click **Sync fork** on GitHub or add
`RSO_WORKFLOW_UPDATE_TOKEN`; the run can still continue with the locally
fetched pipeline code.

Your fork can also be forked by others as long as it preserves these
conventions: `main` stays code/controller-focused, node-generated state
stays on `node`, the two newest catalogs remain available for bootstrap,
and the workflows keep committing the daily hash chain. Share it with
friends, and star the upstream repo so other operators can find the
canonical project.

## Where to look when you are lost

- [`operator.md`](operator.md): why you're doing this, and what's expected
- [`../README.md`](../README.md): full technical walkthrough and command
  reference
- [`glossary.md`](glossary.md): orbital-data terms and field definitions
- `node` branch on your fork: your generated archive state
- `data/YYYY/MM/DD/manifest.json`: the daily hash and provenance summary
- `ledger.json`: rolling public hash chain
- `Releases`: where your fork publishes full daily bundles
- `reports/rehearsal/`: pre-baseline practice data, separate from the
  official lineage
