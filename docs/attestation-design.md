# RSO DocChain Attestation Design

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
3. The node signs it with `DISPOSABLE_NO_FUNDS_ETH_PRIVATE_KEY`.
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

1. Loads the daily operator-backing snapshot created after the TDH calculation.
2. Ranks known operators by card-specific TDH backing.
3. Sponsors the top backed operators, defaulting to the top 5.
4. Fetches each selected operator's signed attestation artifact.
5. Checks the signed payload targets the expected DocChain contract and RSO
   `docChainId`.
6. Checks the signed attester matches the registered operator.
7. Fetches the archive bundle locations and verifies both the signed bundle
   fingerprint, when present, and the signed `contentHash`.
8. Simulates `attestDoc`.
9. Submits the same signed claim if validation passes.

If the sweeper censors or fails, the signature remains public and anyone can
submit it. The sweeper decides whether to spend treasury gas; it does not decide
which history is true.

## Operator Backing

RSO V1 backing is holder-to-operator. Operators can stay pseudonymous. A holder
backs the operator signing address they trust, and that backing is captured in a
daily snapshot after the 6529 TDH calculation.

The daily signed attestation includes:

```text
attester    disposable no-funds EOA
onBehalfOf  optional generic DocChain metadata, normally zero for RSO V1
uri         empty, a direct bundle URI, or an RSO publication-locator data URI
```

For normal node attestations, `uri` is a versioned `data:` URI whose JSON payload
contains the exact release bundle SHA-256 and one or more storage locations:

```json
{
  "bundleSha256": "...",
  "locations": ["ar://...", "https://..."]
}
```

The data URI media type is
`application/vnd.ompub.rso.publication-locator.v1+json`, so the nested JSON does
not carry its own schema field. The signed `contentHash` remains the canonical
catalog fingerprint; `bundleSha256` fixes the exact `.tar.gz` artifact being
published.

The daily backing snapshot is keyed by `attester`:

```text
date -> operator attester -> card-specific TDH backing
```

`onBehalfOf` remains available in the generic DocChain schema for future
profiles that need delegated identities. RSO V1 does not use it for sponsorship
or TDH weighting.

## Indexer Role

The indexer groups `DocAttested` events by:

```text
docChainId
docRef
blockHash / contentHash
```

It then reports:

- which operators attested to each daily chain link
- which operators agree or disagree
- which branch follows the expected parent links
- which operator signing keys and card-specific backing support each group

The indexer should not count raw GitHub repos as votes. Support comes from
recognized operators and card-specific backing, not sybilable repository count.

## What V1 Does Not Need

V1 does not need:

- a wallet-enabled holder dApp
- HTTP relayers
- funded operator signing keys
- per-attestation treasury decisions in the contract
- TDH logic in the contract

Those can be added around the same DocChain event stream later. The core V1
pattern is simpler: operators sign, sweepers submit, indexers show agreement.
