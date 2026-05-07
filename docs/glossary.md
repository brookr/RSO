# Orbital Witness Glossary

This glossary defines the terms and snapshot fields used by the RSO archive. It
is written for operators, reviewers, artists, and future maintainers who need to
understand what the data means before trusting a hash.

The field profile below is based on the rehearsal baseline release bundle:

- Snapshot: `rso-archive-2026-04-14.tar.gz` / `catalog.json.gz`
- Manifest date: `2026-04-14`
- Rows: `67,052`
- Fields per row: `40`
- Distinct key sets: `1`
- Rows with `DECAY_DATE`: `33,471`
- Rows without `DECAY_DATE`: `33,581`
- `NORAD_CAT_ID` range: `4` to `270446`
- Missing IDs between min and max: `203,391`

Source references:

- [Space-Track.org](https://www.space-track.org): source API for the archive.
  The Space-Track `gp` model definition was checked through the authenticated
  `/basicspacedata/modeldef/class/gp/format/json` endpoint.
- [CelesTrak GP data format documentation](https://celestrak.org/NORAD/documentation/gp-data-formats.php):
  explains the shift from TLE-only delivery to GP data in OMM-compatible JSON,
  CSV, XML, and KVN formats.
- [CelesTrak TLE format documentation](https://celestrak.org/NORAD/documentation/tle-fmt.php):
  documents the legacy two-line element fields and units.
- [CelesTrak SATCAT format documentation](https://celestrak.org/satcat/satcat-format.php):
  documents catalog identifiers, object types, launch/decay fields, and the
  important fact that catalog entries only exist for cataloged objects.
- [CCSDS Orbit Data Messages](https://public.ccsds.org/Pubs/502x0b3e1.pdf):
  standard behind the Orbit Mean-Elements Message (OMM) vocabulary used for GP
  data.

## Core Terms

### RSO

Resident Space Object. In this project, RSO means an artificial object tracked
in Earth orbit or formerly in Earth orbit: payloads, rocket bodies, debris,
unknown objects, and temporary analyst objects. Do not call these NEOs. NEOs
are natural near-Earth objects such as asteroids and comets.

### GP / GPE

General Perturbations / General Perturbations Element set. This is the public
orbital element product used with the SGP4 propagator. CelesTrak describes GP
data as fitted observations from the U.S. Space Surveillance Network producing
Brouwer mean elements for SGP4 propagation.

### Space-Track

The source API used by this archive. The current project reads Space-Track
`gp` for genesis snapshots and `gp_history` for bounded daily deltas.

### CelesTrak

An independent public mirror, documentation source, and access layer for GP,
SATCAT, and related space-object datasets. CelesTrak is not the source endpoint
for this archive, but its documentation is the clearest public explanation of
the TLE-to-OMM transition and many catalog fields.

### TLE

Two-Line Element set. A compact fixed-width orbital element format originally
designed for severe bandwidth and storage limits. A three-line TLE includes:

- Line 0: object name.
- Line 1: catalog number, classification, international designator, epoch,
  mean-motion derivatives, BSTAR, ephemeris type, element number, checksum.
- Line 2: inclination, right ascension of the ascending node, eccentricity,
  argument of perigee, mean anomaly, mean motion, revolution number, checksum.

TLEs are still widely used, but they have fixed-field limits, including the
5-digit catalog-number problem. CelesTrak notes that newly cataloged objects
will move beyond what TLE can represent cleanly.

### OMM

Orbit Mean-Elements Message. OMM is part of the CCSDS Orbit Data Messages
standard. It carries the same core orbital content needed for SGP4-style GP
data, but in structured formats that can support ISO timestamps, larger catalog
numbers, Unicode names, and less brittle parsing.

The archive stores Space-Track OMM/JSON-style rows. The fields are strings in
JSON, but the Space-Track model definition identifies their database types.

### OMM-Era Cataloging

The practical transition away from assuming every public object can be expressed
as a legacy TLE catalog number. CelesTrak warns that the public catalog will run
out of 5-digit catalog numbers at `69999`, with the transition
estimated around `2026-07-20` when that note was published. CelesTrak has also
highlighted public Space Fence analyst objects in the `270000+` range.

In the current archive, the highest `NORAD_CAT_ID` is `270446`, while the main
legacy sequence only reaches the high `80000` range. The jump is a real feature
of the public data, not an archive bug.

### SGP4

Simplified General Perturbations 4. The standard propagation model used with GP
element sets. This archive stores the published elements and hashes them; it
does not propagate or predict object positions.

### Conjunction

A close approach between two space objects. A conjunction can be predicted or
observed, and it does not necessarily mean the objects touch. Conjunctions are
risk events: they tell operators that two trajectories may pass near enough to
evaluate collision probability or consider a maneuver.

### Collision

Actual physical contact between two space objects. A collision is the realized
impact event; it can damage or destroy objects and create debris. Every orbital
collision is preceded by a conjunction, but most conjunctions do not become
collisions.

### `gp`

Space-Track class containing the current latest public GP element set for each
reported object. The archive uses `gp` only to create a genesis/base snapshot.

### `gp_history`

Space-Track class containing historical GP element rows. The archive uses
bounded `CREATION_DATE` windows from `gp_history` to roll a prior snapshot
forward one day at a time.

### Catalog Row

One GP element-set row for one `NORAD_CAT_ID` at one publication state. It is
not the same thing as a physical object forever; the same object can receive
many rows over time as new public elements are published.

### Canonical Snapshot

The sorted, normalized JSON array that the archive hashes. For rolling days:

```text
snapshot[D] = snapshot[D-1] + gp_history rows published during day D
```

Objects that do not receive a new row in a daily `gp_history` window are carried
forward from the prior snapshot.

### `CREATION_DATE`

Publication timestamp for a GP row. It does not mean the physical object was
created, launched, cataloged, or first observed at that time. It is the right
field for daily deltas because it answers "when did this public element row
enter the feed?"

### `EPOCH`

Time at which the orbital elements are defined. This can be before or after
`CREATION_DATE`. It is about the state represented by the element set, not the
publication time of the row.

### `DECAY_DATE`

Catalog field for the object's decay date when populated. In the current
baseline, every populated `DECAY_DATE` is in the past; the latest is
as recent as the retrieval date. The field is not a removal marker: about half the rows in current
`gp` already have `DECAY_DATE` populated and are still present.

### Skipped NORAD IDs

`NORAD_CAT_ID` values are identifiers, not a contiguous row count. The catalog
has gaps. CelesTrak's SATCAT documentation explicitly says entries only exist
for cataloged objects; there are no blank entries for missing catalog numbers.

In the current baseline:

- Present IDs: `67,052`
- Min ID: `4`
- Max ID: `270446`
- Numeric span: `270,443`
- Missing IDs in that span: `203,391`
- Present density in that span: about `24.8%`
- Largest gap: `89497-269999`

Day-over-day changes in skipped-ID count, max ID, and gap ranges are worth
tracking because they can reveal catalog-number allocation shifts, newly public
ranges, analyst-object ranges, or source-side data policy changes.

## Snapshot Field Reference

All rows in the current baseline have the same 40 fields. Space-Track returns
JSON values as strings or nulls; the `Type` column below is from Space-Track's
`gp` model definition.

`Snapshot profile` summarizes what we observed in that bundled
`catalog.json.gz`.

| Field | Type | Meaning | Snapshot profile |
|---|---:|---|---|
| `APOAPSIS` | `double(12,3)` | Apogee altitude above the reference body, in kilometers. This is the high point of the orbit relative to Earth for normal Earth-centered rows. | No blanks. Range `-141.837` to `4082823.682`. |
| `ARG_OF_PERICENTER` | `decimal(7,4)` | Argument of pericenter/perigee, in degrees. This locates the orbit's low point within the orbital plane. | No blanks. Range `0.0` to `360.0`. |
| `BSTAR` | `decimal(19,14)` | SGP4 drag-like coefficient from the TLE/GP model. It is useful mainly for low-orbit drag behavior and should not be read as a simple physical area or mass. | No blanks. Many rows are `0.00000000000000`; range `-5.3739` to `7460.0`. |
| `CCSDS_OMM_VERS` | `varchar(3)` | OMM version identifier used by the row. | Constant `3.0`. |
| `CENTER_NAME` | `varchar(5)` | Central body for the orbit. | Constant `EARTH`. |
| `CLASSIFICATION_TYPE` | `char(1)` | Classification code. In TLE line 1, `U` means unclassified. | Constant `U`. |
| `COMMENT` | `varchar(33)` | Source comment added by Space-Track. | Constant `GENERATED VIA SPACE-TRACK.ORG API`. |
| `COUNTRY_CODE` | `char(6)` | Owner/source country or organization code associated with the object. This aligns with SATCAT-style ownership codes rather than orbital physics. | `11,178` blanks. Common values include `US`, `CIS`, `PRC`, `FR`, `JPN`. |
| `CREATION_DATE` | `datetime` | Publication time for this GP row in the Space-Track feed. This is the field used for bounded daily `gp_history` deltas. | No blanks. Range `2004-05-19T21:11:55` to `2026-04-14T06:56:42`. |
| `DECAY_DATE` | `date` | Decay date if Space-Track has one for the object. It is not a current-`gp` removal marker. | `33,581` blanks. Populated range `1959-09-28` to `2026-04-13`. |
| `ECCENTRICITY` | `decimal(13,8)` | Orbital eccentricity. `0` is circular; values closer to `1` are more elongated. | No blanks. Range `0.0` to `0.996795`. |
| `ELEMENT_SET_NO` | `smallint(5) unsigned` | Element-set number, inherited from TLE/GP conventions. | Constant `999` in this snapshot. |
| `EPHEMERIS_TYPE` | `tinyint(4)` | TLE/SGP4 ephemeris type code. CelesTrak's TLE docs identify this as a line-1 field. | No blanks. `0` for `67,050` rows; `1` for `2` rows. |
| `EPOCH` | `datetime(6)` | Time at which the orbital elements are defined. Used by propagators as the reference time for the element set. | No blanks. Range `1959-09-26T07:47:40.948799` to `2026-04-16T04:59:05.116704`. |
| `FILE` | `bigint(20) unsigned` | Space-Track file identifier associated with the element set or ingestion batch. This is source metadata, not an orbital element. | No blanks. Range `982` to `5131138`. |
| `GP_ID` | `int(10) unsigned` | Space-Track unique identifier for this GP row. The archive uses it as a deterministic tie-breaker after `CREATION_DATE`. | No blanks. All `67,052` values are unique. |
| `INCLINATION` | `decimal(7,4)` | Orbital inclination in degrees relative to the reference plane. | No blanks. Range `0.0` to `150.9437`. |
| `LAUNCH_DATE` | `date` | Launch date when known. | `11,180` blanks. Populated range `1958-03-17` to `2026-04-11`. |
| `MEAN_ANOMALY` | `decimal(7,4)` | Mean anomaly at epoch, in degrees. This locates the object around its mean orbit at `EPOCH`. | No blanks. Range `0.0` to `359.9999`. |
| `MEAN_ELEMENT_THEORY` | `varchar(4)` | Theory/model family for the mean elements. | Constant `SGP4`. |
| `MEAN_MOTION` | `decimal(13,8)` | Mean motion in revolutions per day, matching the TLE line-2 field. | No blanks. Range `0.00296241` to `17.63767394`. |
| `MEAN_MOTION_DDOT` | `decimal(22,13)` | Second time derivative of mean motion, from TLE line 1 conventions. | No blanks. Most rows are `0.0000000000000`; range `-0.03105` to `475.61`. |
| `MEAN_MOTION_DOT` | `decimal(9,8)` | First time derivative of mean motion, from TLE line 1 conventions. | No blanks. Range `-0.99999999` to `0.99999999`. |
| `NORAD_CAT_ID` | `int(10) unsigned` | NORAD catalog number. This is the main object identifier used for sorting and daily merge state. | No blanks. Unique. Range `4` to `270446`. |
| `OBJECT_ID` | `varchar(12)` | International Designator, usually `YYYY-NNNAAA`: launch year, launch number of the year, and launch piece. Unknown or analyst objects may use `UNKNOWN`. | No blanks. `UNKNOWN` appears `981` times. |
| `OBJECT_NAME` | `varchar(25)` | Public object name. Names may be reused across debris objects, so this is not unique. | No blanks. Common names include `FENGYUN 1C DEB`, `COSMOS 1408 DEB`, `DELTA 1 DEB`. |
| `OBJECT_TYPE` | `varchar(12)` | Object class. CelesTrak SATCAT docs define payload, rocket body, debris, and unknown categories. | No blanks. `DEBRIS`: `34,370`; `PAYLOAD`: `24,056`; `ROCKET BODY`: `6,522`; `UNKNOWN`: `1,902`; `TBA`: `202`. |
| `ORIGINATOR` | `varchar(7)` | Organization that originated the GP row. | Constant `18 SPCS`. |
| `PERIAPSIS` | `double(12,3)` | Perigee altitude above the reference body, in kilometers. This is the low point of the orbit relative to Earth for normal Earth-centered rows. | No blanks. Range `-5020.981` to `705392.176`. |
| `PERIOD` | `double(12,3)` | Orbital period in minutes. CelesTrak SATCAT uses the same unit for orbital period. | No blanks. Range `81.643` to `486090.717`. |
| `RA_OF_ASC_NODE` | `decimal(7,4)` | Right ascension of the ascending node, in degrees. TLE docs list this as the line-2 RAAN field. | No blanks. Range `0.0` to `359.9999`. |
| `RCS_SIZE` | `char(6)` | Radar cross-section size bucket, not the exact radar cross section. | `15,540` blanks. Values: `SMALL`, `MEDIUM`, `LARGE`. |
| `REF_FRAME` | `varchar(4)` | Reference frame for the orbital elements. | Constant `TEME`. |
| `REV_AT_EPOCH` | `mediumint(8) unsigned` | Revolution number at epoch, matching the TLE line-2 convention. | No blanks. Range `0` to `99998`. |
| `SEMIMAJOR_AXIS` | `double(12,3)` | Semimajor axis in kilometers. This is measured from Earth's center for Earth-centered rows, not altitude above the surface. | No blanks. Range `0.0` to `2047882.641`. |
| `SITE` | `char(5)` | Launch site code when known. | `11,183` blanks. Common values include `AFETR`, `AFWTR`, `PKMTR`, `TTMTR`, `TSC`. |
| `TIME_SYSTEM` | `varchar(3)` | Time system used for date/time fields in the OMM row. | Constant `UTC`. |
| `TLE_LINE0` | `varchar(27)` | TLE name line generated for compatibility. | No blanks. Same naming distribution as `OBJECT_NAME`, prefixed with `0 `. |
| `TLE_LINE1` | `varchar(71)` | TLE line 1 generated for compatibility. Contains catalog number, classification, designator, epoch, mean-motion derivatives, BSTAR, ephemeris type, element number, and checksum. | No blanks. All `67,052` values are unique. |
| `TLE_LINE2` | `varchar(71)` | TLE line 2 generated for compatibility. Contains inclination, RAAN, eccentricity, argument of perigee, mean anomaly, mean motion, revolution number, and checksum. | No blanks. All `67,052` values are unique. |

## Field Groups

### Archive / Source Metadata

- `CCSDS_OMM_VERS`
- `COMMENT`
- `CREATION_DATE`
- `ORIGINATOR`
- `FILE`
- `GP_ID`

These fields describe the message, source, or source-side row identity. They
are critical for provenance and deterministic ordering, but they are not
physical orbit parameters.

### Object Identity

- `NORAD_CAT_ID`
- `OBJECT_NAME`
- `OBJECT_ID`
- `OBJECT_TYPE`
- `COUNTRY_CODE`
- `LAUNCH_DATE`
- `SITE`
- `DECAY_DATE`
- `RCS_SIZE`

These fields describe the cataloged object, its origin, and broad category.
They can change as catalog knowledge improves.

### Reference Frame / Time Model

- `CENTER_NAME`
- `REF_FRAME`
- `TIME_SYSTEM`
- `MEAN_ELEMENT_THEORY`
- `EPOCH`

These fields explain how to interpret the orbital elements.

### Orbital Elements

- `MEAN_MOTION`
- `ECCENTRICITY`
- `INCLINATION`
- `RA_OF_ASC_NODE`
- `ARG_OF_PERICENTER`
- `MEAN_ANOMALY`
- `EPHEMERIS_TYPE`
- `ELEMENT_SET_NO`
- `REV_AT_EPOCH`
- `BSTAR`
- `MEAN_MOTION_DOT`
- `MEAN_MOTION_DDOT`

These are the SGP4/TLE-style element fields used by propagation software.

### Derived Orbit Summary

- `SEMIMAJOR_AXIS`
- `PERIOD`
- `APOAPSIS`
- `PERIAPSIS`

These are convenient derived values included by Space-Track. They are useful
for reporting and visualization, but the core published element set is the
SGP4/OMM element block.

### TLE Compatibility

- `TLE_LINE0`
- `TLE_LINE1`
- `TLE_LINE2`

These preserve the familiar TLE representation for objects that can be encoded
that way. As catalog numbers grow beyond legacy fixed-field limits, OMM/JSON is
the safer canonical format for this archive.
