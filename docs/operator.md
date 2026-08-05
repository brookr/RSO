# RSO Operator and Sweeper Model

An RSO operator runs an archive node. The node downloads public Space-Track
data, produces the daily archive artifact, and optionally signs a DocChain
attestation with a disposable no-funds EOA.

A sweeper is different. It is a funded courier that gathers public signed
attestations from known operators and submits eligible ones to the DocChain
contract.

## Operator Role

An operator node should be boring:

- run the daily snapshot workflow
- publish the release bundle
- optionally publish a signed DocChain artifact
- keep the disposable signing key unfunded

The operator signing key is not a personal wallet. It should hold no ETH, NFTs,
or tokens. If it leaks, there are no funds to steal. The damage is reputational:
an attacker could sign bad claims until the operator rotates the key and backing
relationship.

## Card-Backed Operator Support

Operators do not need to connect their disposable key to a 6529 identity in RSO
V1. Card holders back nodes, not signing addresses. That lets operators rotate
disposable signing keys without asking backers to move, and lets card holders
switch support as node performance changes. Card holders may also attest
directly from an account in their own 6529 identity; direct witness support is
reported separately from node backing.

Recommended V1 shape:

```text
card holder -> backs node
```

The daily signed attestation sets:

```text
attester    disposable EOA
onBehalfOf  normally zero for RSO V1
uri         signed locator containing nodeId, bundle fingerprint, and locations
```

After the daily 6529 TDH calculation, RSO computes a TDH support snapshot:

```text
date -> identity accounts -> direct witness card-specific TDH
date -> node id -> positive, negative, net, and usable TDH backing
```

The sweeper uses that snapshot before spending treasury gas. The indexer uses
the same snapshot when reporting weighted agreement groups. For GitHub-hosted
nodes, the node id is `github:owner/repo`.

## Sweeper Role

The sweeper:

- reads a known operator registry or discovers candidate repositories from the
  upstream GitHub fork graph
- reads the daily TDH support snapshot
- augments discovery with the highest-backed GitHub node ids in that snapshot,
  so an independent implementation does not need to be an upstream fork
- extracts each signed artifact's attester and ranks only net-positive nodes by
  usable card-specific TDH backing
- fetches signed artifacts from operator `node` branches
- requires the signed-artifact URL to belong to the selected GitHub repository
  or domain
- requires the selected node, artifact declaration, signed locator `nodeId`,
  declared attester, and signed attester to agree
- validates signatures by simulating `attestDoc`
- validates every listed location against the signed archive bundle fingerprint
  and catalog fingerprint
- submits valid claims for selected backed operators with a funded sweeper
  wallet
- publishes the observed signing address, backing/rank context, verification
  evidence, and result

The sweeper does not decide truth. If it refuses to submit a valid signature,
the public signature can still be submitted by anyone else.

Fork discovery is not a trust signal. It only tells the sweeper where to look.
Sponsorship still requires a signed artifact from a node present in the daily
TDH support snapshot.

The current default sweeper discovers and fetches GitHub fork nodes. The
`domain:hostname` node-id form is reserved for self-hosted nodes, but production
support still needs a hardened domain discovery and artifact-fetch path before
the default treasury sweeper will sponsor those nodes.

## Treasury Role

The NFT treasury funds shared infrastructure:

- Arweave publishing
- sweeper gas
- public indexes and monitoring

The treasury should not be the only attester. Truth comes from reproducible
daily artifacts plus independent operator signatures. The treasury only pays to
publish eligible signatures.

## Failure Modes

If an operator key leaks:

- rotate the disposable EOA configured by the node
- publish new signed artifacts from the same node id
- review the public sweeper history for unauthorized observations

If the sweeper fails:

- signed artifacts remain public
- another sweeper can submit them
- anyone can submit a valid signature manually
- the next scheduled sweep can retry missed signed artifacts that remain
  eligible and discoverable

Node signatures default to a seven-day submission deadline so ordinary deferred
or missed sweeps have several retry windows without creating indefinitely valid
signatures. The default scheduled sweeper checks a bounded two-day window, so a
missed claim is retried on the next day's run.

The sweeper publishes public per-date reports under:

```text
reports/sweeper/YYYY-MM-DD.json
```

Retry observations are merged into the existing per-date report. Completed
verified records are preserved even if a later retry encounters a temporary
failure, while non-final history is bounded to keep reports manageable.

Node repos include a default **Check RSO Sweeper Report** workflow. It reads the
public report for its own `nodeId` and opens, updates, or closes one local issue
when the report shows a condition that may need operator attention. Operators do
not need to monitor Actions artifacts manually.

If a signed attestation lists multiple publication locations, the sweeper checks
every listed location before submitting it onchain. Temporary fetch failures are
retried immediately, then deferred to a later sweep. Locators with too many
locations are rejected to keep one operator from consuming unbounded treasury
resources.

If operators disagree:

- the contract records all valid claims
- the indexer groups matching and conflicting fingerprints
- the UI reports support behind each group

Disagreement is not hidden. It is the evidence the archive is meant to surface.
One node and one 6529 identity each count at most once per agreement group. If
either supports conflicting groups for the same day, that support is shown as
equivocating and counts for neither group.
