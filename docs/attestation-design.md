# RSO DocChain Attestation Design

> **Note:** attestations sign `manifest.content_sha256` (docChainId
> `keccak256("https://om.pub/rso/doc-chain")`; the DocChain contract
> supports single and batch submission). See [chain.md](chain.md) and
> [late-join.md](late-join.md).

RSO uses the generic DocChain contract as an append-only witness log. Operator
nodes sign daily archive claims with disposable no-funds EOAs. A separate
treasury-funded sweeper submits eligible signed claims onchain.

The important split is:

```text
operator signing key = says "I witnessed this document"
sweeper key          = pays gas to publish already-signed claims
```

The signing key never needs ETH, NFTs, or tokens.

## Contract Fit

The existing DocChain contract already supports this model:

```solidity
attestDoc(DocAttestation attestation, bytes signature)
```

`attestation.attester` is the signer. `msg.sender` is only the submitter/gas
payer and is emitted as `submitter`. The contract verifies the EIP-712
signature, prevents exact duplicates, and emits `DocAttested`.

No RSO-specific truth rule lives in the contract. The contract records claims.
Indexers and viewers interpret those claims under the RSO profile.

## Daily Operator Flow

1. The node captures and publishes the daily archive bundle.
2. The node prepares a DocChain `DocAttestation` for that day.
3. The node signs it with a disposable EOA, usually through
   `DISPOSABLE_NO_FUNDS_ETH_PRIVATE_KEY` or an encrypted keystore secret.
4. The node writes the signed artifact to:

```text
data/attestations/signed/YYYY-MM-DD.json
```

5. The node commits that signed artifact to its `node` branch.

The signed artifact is public. It can be submitted by the project sweeper, the
operator, or any third party.

## Sweeper Flow

The sweeper is a funded courier, not an oracle.

For each known operator and archive day, it:

1. Loads the daily TDH support snapshot created after the TDH calculation.
2. Ranks known operators by net-positive usable card-specific TDH backing.
3. Sponsors the top backed operators, defaulting to the top 5.
4. Fetches each selected operator's signed attestation artifact.
5. Checks the signed payload targets the expected DocChain contract and RSO
   `docChainId`.
6. Checks the artifact URL belongs to the selected GitHub repository or domain.
7. Checks the selected node id, artifact declaration, signed publication
   `nodeId`, declared attester, and signed attester all agree.
8. Fetches every archive bundle location and verifies both the signed bundle
   fingerprint, when present, and the signed `contentHash`.
9. Simulates `attestDoc`.
10. Submits the same signed claim if validation passes.
11. Publishes a report recording the verified node/signing-address association,
    exact claim fingerprint, publication checks, and submission result.

The report's `claimFingerprint` is SHA-256 over canonical compact JSON containing
the normalized `attester`, `onBehalfOf`, `docChainId`, integer `docRef`,
`parentHash`, `contentHash`, and exact signed `uri`. The indexer computes the
same fingerprint from each onchain event, so sweeper evidence can authorize only
that exact claim.

If the sweeper censors or fails, the signature remains public and anyone can
submit it. The sweeper decides whether to spend treasury gas; it does not decide
which history is true.

## Operator Backing

RSO V1 backing is holder-to-operator. Operators can stay pseudonymous. A holder
backs the node they trust, and that backing is captured in a daily snapshot
after the 6529 TDH calculation. The node can rotate its disposable signing key
without changing the node id that holders backed.

### 6529 REP category

Card holders express node backing by assigning REP to the canonical RSO 6529
identity. The current 6529 category allowlist is any Unicode letter or number,
plus `?`, `!`, `,`, `.`, `'`, `(`, `)`, and space. RSO categories use a compact
command form made only from lowercase ASCII letters, numbers, periods,
exclamation marks, and single spaces:

```text
!node !github <owner>.<repo>
!node !domain <hostname>
!node !id <digest>
```

Examples:

```text
!node !github brookr.rso
!node !domain rso.om.pub
```

The GitHub argument splits at its first period: the left side is the owner and
the remainder is the repository. It maps to canonical node id
`github:<owner>/<repo>`. The domain argument maps to `domain:<hostname>`.

The direct forms are valid only when the canonical owner, repository, or
hostname is representable using characters accepted by 6529 categories. Writers
MUST NOT replace unsupported punctuation with a period because that creates
ambiguous aliases. Until 6529 accepts a dash, a GitHub owner, repository, or
hostname containing one uses the digest form. Names containing any other
unsupported character also use:

```text
!node !id <digest>
```

`digest` is the first 32 lowercase hexadecimal characters of SHA-256 over the
UTF-8 canonical node id. The published node roster carries both the category and
canonical node id; readers recompute the digest before accepting the mapping.
This fallback is deterministic, uses a lowercase hexadecimal argument accepted
by 6529, and does not collapse distinct repository or domain names.

Writers emit categories in lowercase with no surrounding whitespace and exactly
one ASCII space between tokens. Readers trim surrounding whitespace, tokenize
on whitespace, match the `!node` and type commands case-insensitively, resolve
the argument to a canonical node id, and combine categories that resolve to the
same node before allocation. Invalid or unresolvable RSO node commands are
ignored.

For each 6529 identity and daily cutoff, positive and negative current REP
assigned to valid RSO node categories participate. Resolve and combine all
categories first. If identity `h` has card-specific TDH `T_h` and gives signed
REP `R_h,n` to node `n`, define its REP budget denominator as:

```text
A_h = sum(abs(R_h,*))
allocation(h, n) = T_h * R_h,n / A_h
```

An identity with no nonzero node REP allocates nothing. Otherwise, integer
allocation uses the largest-remainder method over absolute allocation amounts,
then restores each node's REP sign; ties break by canonical node id ascending.
This preserves exactly `T_h` units of absolute allocation and makes every
producer emit the same totals.

For each node, the support snapshot records positive allocation, negative
allocation, and their signed balance:

```text
positiveBackingTdh(n) = sum(max(allocation(h,n), 0))
negativeBackingTdh(n) = sum(max(-allocation(h,n), 0))
netBackingTdh(n)      = positiveBackingTdh(n) - negativeBackingTdh(n)
usableBackingTdh(n)   = max(netBackingTdh(n), 0)
```

Only `usableBackingTdh` contributes to node ranking or agreement-group support.
A negative balance can disqualify or rank down a node, but never subtracts from
an agreement group merely because that node attested to it. The support snapshot
records the 6529 data cutoff; only REP and consolidation state effective at that
cutoff participate. Consolidated wallets are one 6529 identity for this
calculation; they do not create additional TDH budgets.

The daily signed attestation includes:

```text
attester    disposable no-funds EOA
onBehalfOf  optional generic DocChain metadata, normally zero for RSO V1
uri         empty, a direct bundle URI, or an RSO publication-locator data URI
```

For normal node attestations, `uri` is a versioned `data:` URI whose JSON payload
contains the node id, exact release bundle SHA-256, and one or more storage
locations:

```json
{
  "nodeId": "github:owner/repo",
  "bundleSha256": "...",
  "locations": ["ar://...", "https://..."]
}
```

The data URI media type is
`application/vnd.ompub.rso.publication-locator.v1+json`, so the nested JSON does
not carry its own schema field. The signed `contentHash` remains the canonical
catalog fingerprint; `bundleSha256` fixes the exact `.tar.gz` artifact being
published.

Publication locations answer only "where can this exact bundle be fetched?"
They never establish operator identity. The node id is explicitly signed, and
the sweeper only recognizes it when it matches the node from which the artifact
was fetched.

The daily TDH support snapshot records two independent support channels:

```text
date -> 6529 identity and accounts -> direct witness card-specific TDH
date -> node id -> positive, negative, net, and usable card-specific TDH backing
```

For GitHub-hosted nodes, the node id is `github:owner/repo`. A manual operator
registry can declare another `nodeId` for non-GitHub nodes.

`onBehalfOf` remains available in the generic DocChain schema for future
profiles that need delegated identities. RSO V1 does not use it for sponsorship
or TDH weighting.

A card holder may use both support channels: attest directly from an account in
their 6529 identity and back a node. Indexers report these separately as
`directWitnessTdh` and the four `nodePositiveBackingTdh`,
`nodeNegativeBackingTdh`, `nodeNetBackingTdh`, and `nodeUsableBackingTdh`
fields. `combinedSupportTdh` is direct witness TDH plus usable node backing.
One identity is counted at most once per agreement group, and one node is
counted at most once. If an identity or node supports conflicting groups for the
same day, that support channel is shown as equivocating and counts for neither
group.

The TDH support snapshot generator must enforce the backing-channel budget:
when an identity has nonzero node REP, the sum of the absolute values of its
signed node allocations must equal that identity's card-specific TDH. Direct
witnessing is a separate use of the
same card-specific TDH, so an identity may contribute once through direct
witnessing and once through allocated node backing, but cannot multiply either
channel by using more accounts or more nodes.

## Indexer Role

The indexer groups `DocAttested` events by:

```text
docChainId
docRef
blockHash / contentHash
```

It then reports:

- which attesters signed each daily chain link
- which attesters agree or disagree
- which branch follows the expected parent links
- which verified nodes and signing keys support each group
- direct-witness TDH, signed node-backing components, and combined usable support
- identities or nodes that equivocated across conflicting groups

The indexer should not count raw GitHub repos as votes. Support comes from
verified sweeper evidence and daily card-specific TDH snapshots, not sybilable
repository count, attestation count, or publication URL strings. If conflicting
groups have equal combined support, the index reports no leading agreement
group.

### Current Offchain Trust Boundary

Node backing is derived from the selected public sweeper reports and daily TDH
support snapshots. Those inputs are transparent and independently inspectable,
but their history is currently published through the controller repository
rather than fixed onchain. A consumer can always verify the underlying onchain
attestations and bundles independently; reproducing the displayed historical TDH
weight also requires the same report and support-snapshot inputs.

Production should publish and fingerprint those daily inputs in durable storage
so their exact historical bytes remain reproducible outside the controller Git
branch.

## What V1 Does Not Need

V1 does not need:

- a wallet-enabled holder dApp
- HTTP relayers
- funded operator signing keys
- per-attestation treasury decisions in the contract
- TDH logic in the contract

Those can be added around the same DocChain event stream later. The core V1
pattern is simpler: operators sign, sweepers submit, indexers show agreement.
