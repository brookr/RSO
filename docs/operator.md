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
V1. Card holders back operator signing addresses directly. That lets operators
remain pseudonymous, and lets card holders switch support as operator
performance changes.

Recommended V1 shape:

```text
card holder -> backs operator attester
```

The daily signed attestation sets:

```text
attester    disposable EOA
onBehalfOf  normally zero for RSO V1
```

After the daily 6529 TDH calculation, RSO computes a backing snapshot:

```text
date -> operator attester -> card-specific TDH backing
```

The sweeper uses that snapshot before spending treasury gas. The indexer uses
the same snapshot when reporting weighted agreement groups.

## Sweeper Role

The sweeper:

- reads the known operator registry
- reads the daily operator-backing snapshot
- ranks operators by card-specific TDH backing
- fetches signed artifacts from operator `node` branches
- validates signatures by simulating `attestDoc`
- validates the signed archive bundle fingerprint and the catalog fingerprint
- submits valid claims for selected backed operators with a funded sweeper
  wallet

The sweeper does not decide truth. If it refuses to submit a valid signature,
the public signature can still be submitted by anyone else.

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

- remove or pause that operator in the sweeper registry
- ask backers to move support to the new disposable EOA
- publish the key rotation in operator docs

If the sweeper fails:

- signed artifacts remain public
- another sweeper can submit them
- anyone can submit a valid signature manually

If operators disagree:

- the contract records all valid claims
- the indexer groups matching and conflicting fingerprints
- the UI reports support behind each group

Disagreement is not hidden. It is the evidence the archive is meant to surface.
