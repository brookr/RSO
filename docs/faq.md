# RSO Archive: FAQ

RSO Archive preserves a daily, verifiable record of the public catalog of
Resident Space Objects: satellites, rocket bodies, and debris in Earth orbit.
The goal is simple: give an important public dataset independent public memory,
so future users can verify what was publicly available on a given day.

RSO Archive does not track satellites, improve orbit estimates, or replace the
institutions that publish orbital data. It preserves public evidence.

---

## The Basics

### What is RSO Archive?

RSO Archive is a daily preserved record of the public catalog of artificial
objects in Earth orbit.

The source catalog comes from Space-Track, operated by the U.S. Space Force.
CelesTrak, an independent nonprofit public service, also provides essential
access, formats, documentation, and supplemental orbital data. KeepTrack.space
is another independent public service that makes orbital data easier to explore
and understand. RSO Archive does not replace any of these services. It records
what public sources made available and publishes verifiable artifacts so the
record can be checked later.

### What gets preserved?

The current RSO Archive preserves the public Space-Track GP catalog as a daily
canonical snapshot, along with metadata needed to verify that snapshot:

- source query boundaries
- daily change data
- the canonical catalog bytes
- a cryptographic fingerprint of those bytes
- manifests, audit outputs, and ledger entries
- release bundles and storage receipts

Future archive profiles may include related public datasets, such as CelesTrak
Supplemental GP data or space-weather inputs, but those should be treated as
separate document chains with their own source rules and permission review.

### Why does this matter?

The public orbital catalog is part of the shared information environment for
space. Operators, researchers, journalists, educators, policy analysts, and
citizens all rely on public orbital data to understand what is happening above
Earth.

Today that public record is accessed through a small number of trusted services.
Those services are valuable, and RSO Archive depends on them. The gap RSO
Archive addresses is narrower: public access does not automatically create
durable public memory.

RSO Archive turns service responses into stable artifacts: daily files,
fingerprints, manifests, and verification rules that can be cited, mirrored,
checked, and preserved independently.

---

## Common Questions

### Isn't this data already public?

Yes. That is why it can be archived.

RSO Archive is not claiming to reveal hidden information or produce a better
catalog. The value is record fixation: preserving a daily public artifact with a
published fingerprint and enough metadata for others to verify the archive.

If someone in 2035 asks what the public catalog said on 2026-05-22, the answer
should not depend only on whether an old API query still works the same way, an
account is still available, or a service still exposes the same historical view.
RSO Archive provides a frozen artifact for that day.

### Why preserve data that might never be needed?

Most archived records are rarely used. That is normal.

Libraries, public archives, scientific repositories, and the Internet Archive
preserve material because the future value of a record is hard to know in
advance. Some records become important only after accidents, disputes, outages,
policy changes, format transitions, or historical research questions.

RSO Archive should be understood in that tradition. It is not daily utility
infrastructure for most users. It is preservation infrastructure for the rare
moments when the historical public record matters.

### What is this not?

- Not a replacement for Space-Track, CelesTrak, KeepTrack.space, or any live data service
- Not an authoritative source of orbital truth
- Not a real-time tracking or collision-avoidance tool
- Not a sensor network
- Not a guarantee that upstream public access will continue
- Not a claim that archived public data is complete or physically correct
- Not a merged operational catalog

RSO Archive mirrors and preserves public sources. It does not become the source
of authority.

### What happens if Space-Track stops publishing?

RSO Archive cannot continue producing new Space-Track-derived snapshots without
Space-Track data.

What it can do is preserve the public record up to the cutoff: the last
available daily artifacts, the fingerprint chain, and the evidence of when the
source stopped being available to the archive. That is memory, not operational
continuity.

### What about CelesTrak Supplemental GP data?

CelesTrak Supplemental GP, or SupGP, is a strong candidate for a related witness
archive because it includes orbital elements derived from public or
operator-supplied sources and can provide timelier or more accurate public
information for some spacecraft.

It should not be silently folded into the main RSO chain. SupGP has different
source semantics and may include data provided to CelesTrak with permissions
that do not automatically authorize downstream permanent redistribution.

If archived, SupGP should be a separate document chain with explicit permission
review, clear attribution, and its own validation profile.

---

## What's this like? Give me an analogy!

### Certificate Transparency

Certificate Transparency keeps the web's certificate system auditable. It does
not issue certificates and does not decide which certificates are good. It logs
issuance events so anyone can monitor them.

RSO Archive applies a similar public-log idea to orbital catalog artifacts. It
does not create the catalog. It makes the public record easier to audit.

### The Internet Archive

The Internet Archive preserves public web pages even when original sites change,
move, or disappear. Researchers, journalists, courts, and ordinary users rely on
that memory because the live web is not a stable archive.

RSO Archive is narrower and more structured: one public technical dataset,
captured daily, with deterministic files and fingerprints.

### Libraries and Scientific Repositories

Libraries keep many records that are rarely touched. Scientific repositories
preserve datasets so future work can cite and reproduce earlier results.

RSO Archive follows that model. The value is not that every daily snapshot will
be used. The value is that the record exists when it is needed.

---

## How It Works

### How does someone verify the archive?

Verification has two layers.

First, anyone can verify an archived artifact by downloading the released files,
recomputing the cryptographic fingerprint of the canonical catalog bytes, and
comparing that fingerprint with the published manifest or ledger entry.

Second, independent operators can run the same source-to-snapshot procedure on
the same day. If their canonical fingerprints match, that is evidence that the
archive process is reproducible and not merely hosted by one party.

Historical source re-querying is useful, but it is not the main verification
path. Old API behavior, account access, source retention, and query semantics can
change. The archive exists so verification can rely on preserved artifacts, not
only on future access to the original services.

### Who runs it?

The project starts with a small operating team and is designed for independent
operators to run their own archive nodes.

The archive gets stronger when more independent parties reproduce the same daily
fingerprints and publish their own evidence. For the archived public record,
that means there is no single point of failure, and no single copy of the record
can be quietly changed and treated as the only source of truth.

### What's the connection to the art card?

The art card funds the archive and acts as a public interface to it.

The card can show the current archive state: which days have been recorded,
which fingerprints are attested, whether operators agree, and whether recent
archive activity looks normal. Opening the card is not a substitute for full
technical verification, but it can make the archive visible and monitorable by a
broader community.

Wallet-enabled attestations should happen through a separate signing interface,
because NFT display environments are usually sandboxed and should remain
read-only.

### What happens if the project stops running?

Everything preserved up to that point remains independently verifiable, assuming
the artifacts and attestations have been published to durable storage.

What stops is the addition of new days. Because the method is public, another
operator can resume from the last preserved state if source access remains
available.

---

## Looking Ahead

### Could this apply to other datasets?

Yes. RSO Archive is one instance of a broader pattern: community witness
infrastructure for public-interest datasets.

The pattern is useful when a dataset is public, historically important, and
published through trusted services that should not be the only memory of their
own records. Possible future domains include environmental monitoring, public
health dashboards, election administration artifacts, government spending,
regulatory notices, scientific reference datasets, and other civic or scientific
records.

### Does each dataset need its own chain?

Yes, at the document-chain level.

Different datasets have different source rules, schemas, update cadences,
licenses, and verification procedures. They can share common software and a
generic attestation contract, but each archive profile should have its own
document chain identifier and validation rules.

That separation keeps the meaning of each attestation clear.

### Could other people do this independently?

Yes, and they should.

The goal is not to make RSO Archive the only archive. The goal is to make
independent preservation normal: many operators, many mirrors, shared
verification rules, and public artifacts that back up and attest to important
public data.

---

## In Plain Language

RSO Archive is preservation infrastructure for public orbital data.

It does not make the data more accurate. It does not replace the institutions
that produce or publish it. It gives the public record a durable, verifiable
memory outside any single service.

The larger idea is simple: public-interest datasets should not depend on one
service to be their only memory. RSO Archive is a first concrete instance, and
the pattern is designed to generalize.
