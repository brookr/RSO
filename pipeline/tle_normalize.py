#!/usr/bin/env python3
"""Canonical numeric core projection — schema ``rso-core-omm-v1`` (Phase B §4).

Normalizes an orbital mean-element set from ANY of its serializations (modern
69-col TLE, the legacy spaced-designator / 5-wide / trailing-backslash export,
McDowell 1957 lines, or live OMM JSON) to ONE canonical record of 13 all-string
tokens, and hashes a day's catalog deterministically.

The contract (frozen at genesis, see .backpatch/PHASE_B_SPEC.md §4):
  * NO IEEE-754 float anywhere — every value is produced by integer / string ops
    so a verifier in any language reproduces byte-identical ``canonical_bytes``.
  * EPOCH is pinned to the TLE 864-microsecond day-fraction grid; an off-grid
    epoch is an ingest error, not silently rounded.
  * Assumed-exponent (BSTAR / nddot) and Alpha-5 decoders fail closed.

This supersedes the live exclusion-based ``rso-core-v1`` (which hashed raw
Space-Track OMM strings and so could not reproduce from a TLE). Already-attested
``rso-core-v1`` days are untouched — each manifest names its own schema.
"""
from __future__ import annotations

import hashlib
import json

SCHEMA_ID = "rso-core-omm-v1"

# Alpha-5: first char of the 5-char satnum extends the range to 339,999.
# Index in this string IS the high-digit value; I and O are skipped.
ALPHA5 = "0123456789ABCDEFGHJKLMNPQRSTUVWXYZ"

# The 11 mandatory core keys = the pure orbit (CCSDS-OMM aligned). The three
# bookkeeping fields are deliberately NOT hashed: REV_AT_EPOCH, EPHEMERIS_TYPE,
# and ELEMENT_SET_NO are provider-assigned counters, not the orbit. Excluding
# them makes the hash a pure, source-independent statement about the orbit
# (maximizes third-party reproducibility). They live in the observation plane;
# ELEMENT_SET_NO is still read for the §5 carry-forward tie-break (selection
# only, never hashed) via element_set_no_from_tle().
CORE_KEYS = (
    "ARG_OF_PERICENTER", "BSTAR", "ECCENTRICITY", "EPOCH", "INCLINATION",
    "MEAN_ANOMALY", "MEAN_MOTION", "MEAN_MOTION_DDOT", "MEAN_MOTION_DOT",
    "NORAD_CAT_ID", "RA_OF_ASC_NODE",
)

USEC_PER_DAY = 86_400_000_000
EPOCH_GRID_USEC = 864  # 1e-8 day exactly; every TLE epoch is a multiple of this


# --------------------------------------------------------------------------
# §4.2  canon_decimal — the shared numeric tokenizer (integer/string only)
# --------------------------------------------------------------------------

def _apply_exponent(mantissa: str, exp: int) -> str:
    """Shift the decimal point of an unsigned decimal string by ``exp`` (no float)."""
    if "." in mantissa:
        int_part, frac_part = mantissa.split(".", 1)
    else:
        int_part, frac_part = mantissa, ""
    digits = int_part + frac_part
    point = len(int_part) + exp  # number of digits left of the point
    if point <= 0:
        return "0." + "0" * (-point) + digits
    if point >= len(digits):
        return digits + "0" * (point - len(digits))
    return digits[:point] + "." + digits[point:]


def _is_all_zero(s: str) -> bool:
    return all(c in "0." for c in s)


def canon_decimal(s: str) -> str:
    """Canonical shortest plain-decimal token for a terminating decimal string."""
    s = s.strip().rstrip("\\").strip()
    if not s:
        raise ValueError("empty decimal field")
    neg = False
    if s[0] in "+-":
        neg = s[0] == "-"
        s = s[1:]
    if "e" in s or "E" in s:
        mant, _, exp = s.lower().partition("e")
        if not exp or not mant:
            raise ValueError(f"bad exponent form: {s!r}")
        s = _apply_exponent(mant, int(exp))
    if "." not in s:
        s += "."
    int_part, frac_part = s.split(".", 1)
    if not (int_part + frac_part).isdigit():
        raise ValueError(f"non-numeric decimal field: {s!r}")
    frac_part = frac_part.rstrip("0")
    int_part = int_part.lstrip("0") or "0"
    out = int_part if not frac_part else int_part + "." + frac_part
    if _is_all_zero(out):
        return "0"
    return ("-" + out) if neg else out


def decode_assumed_exp(field: str) -> str:
    """Decode a TLE assumed-exponent field ``MMMMM±E`` (BSTAR, nddot). Fail-closed."""
    field = field.strip()
    neg = False
    if field and field[0] in "+-":
        neg = field[0] == "-"
        field = field[1:]
    if len(field) < 2 or field[-2] not in "+-":
        raise ValueError(f"malformed assumed-exponent field: {field!r}")
    exp = field[-2:]
    mant = field[:-2]
    if not mant.isdigit() or not exp[1:].isdigit():
        raise ValueError(f"malformed assumed-exponent field: {field!r}")
    if _is_all_zero(mant):
        return "0"
    token = ("-" if neg else "") + "0." + mant + "e" + exp
    return canon_decimal(token)


# --------------------------------------------------------------------------
# §4.4  NORAD_CAT_ID
# --------------------------------------------------------------------------

def decode_satnum(field: str) -> int:
    """Alpha-5 / plain decode of a satnum field to its integer value."""
    field = field.strip()
    if not field:
        raise ValueError("empty satnum")
    c0 = field[0].upper()
    if c0.isdigit():
        if not field.isdigit():
            raise ValueError(f"bad numeric satnum: {field!r}")
        return int(field)
    if c0 not in ALPHA5:
        raise ValueError(f"bad Alpha-5 leading char: {field!r}")
    rest = field[1:]
    if not rest.isdigit():
        raise ValueError(f"bad Alpha-5 satnum: {field!r}")
    return ALPHA5.index(c0) * 10000 + int(rest)


def canon_norad(value) -> str:
    """Canonical base-10 integer-string NORAD token from a TLE field or OMM id."""
    if isinstance(value, int):
        n = value
    else:
        s = str(value).strip()
        # An OMM id may be plain digits (incl. future 9-digit) or Alpha-5.
        n = int(s) if s.isdigit() else decode_satnum(s)
    if n < 0:
        raise ValueError(f"negative NORAD id: {value!r}")
    token = str(n)
    if not _ascii_int_token(token):
        raise ValueError(f"non-canonical NORAD token: {token!r}")
    return token


def _ascii_int_token(s: str) -> bool:
    """ASCII-only ^(0|[1-9][0-9]*)$ (NOT Unicode isdigit)."""
    if not s or any(c not in "0123456789" for c in s):
        return False
    return s == "0" or s[0] != "0"


# --------------------------------------------------------------------------
# §4.3  EPOCH — fixed-width civil UTC on the 864-microsecond grid
# --------------------------------------------------------------------------

def _is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _days_in_year(year: int) -> int:
    return 366 if _is_leap(year) else 365


_MONTH_LENGTHS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def _doy_to_md(year: int, doy: int) -> tuple[int, int]:
    leap = _is_leap(year)
    for month, base in enumerate(_MONTH_LENGTHS, 1):
        length = base + (1 if (month == 2 and leap) else 0)
        if doy <= length:
            return month, doy
        doy -= length
    raise ValueError(f"day-of-year {doy} out of range for {year}")


def _md_to_doy(year: int, month: int, day: int) -> int:
    leap = _is_leap(year)
    doy = day
    for m, base in enumerate(_MONTH_LENGTHS[: month - 1], 1):
        doy += base + (1 if (m == 2 and leap) else 0)
    return doy


def _render_epoch(year: int, doy: int, usec_of_day: int) -> str:
    # carry whole days produced by rounding/overflow
    while usec_of_day >= USEC_PER_DAY:
        usec_of_day -= USEC_PER_DAY
        doy += 1
    while doy > _days_in_year(year):
        doy -= _days_in_year(year)
        year += 1
    if usec_of_day % EPOCH_GRID_USEC != 0:
        raise ValueError(f"epoch off the {EPOCH_GRID_USEC}us grid: {usec_of_day}")
    month, day = _doy_to_md(year, doy)
    h, rem = divmod(usec_of_day, 3_600_000_000)
    mi, rem = divmod(rem, 60_000_000)
    s, us = divmod(rem, 1_000_000)
    return f"{year:04d}-{month:02d}-{day:02d}T{h:02d}:{mi:02d}:{s:02d}.{us:06d}"


def epoch_from_tle(line1: str) -> str:
    """Canonical EPOCH token from a TLE line-1 ``YYDDD.FFFFFFFF`` field."""
    raw = line1[18:32].strip()
    yy = int(raw[:2])
    year = 1900 + yy if yy >= 57 else 2000 + yy
    doy_str, _, frac_str = raw[2:].partition(".")
    doy = int(doy_str)
    if frac_str:
        scale = 10 ** len(frac_str)
        usec_of_day = (int(frac_str) * USEC_PER_DAY + scale // 2) // scale
    else:
        usec_of_day = 0
    return _render_epoch(year, doy, usec_of_day)


def epoch_from_omm(iso: str) -> str:
    """Canonical EPOCH token from an OMM ISO timestamp (assert on 864-grid)."""
    s = iso.strip()
    if s.endswith("Z"):
        s = s[:-1]
    date_part, _, time_part = s.partition("T")
    y, mo, d = (int(x) for x in date_part.split("-"))
    hh_s, mi_s, sec_part = time_part.split(":")
    sec_s, _, frac = sec_part.partition(".")
    hh, mi, sec = int(hh_s), int(mi_s), int(sec_s)
    # round-half-up the fractional seconds to exactly 6 digits
    frac6 = frac[:6].ljust(6, "0")
    us = int(frac6) if frac6 else 0
    if len(frac) > 6 and frac[6] >= "5":
        us += 1
    usec_of_day = ((hh * 3600 + mi * 60 + sec) * 1_000_000) + us
    doy = _md_to_doy(y, mo, d)
    return _render_epoch(y, doy, usec_of_day)


def tle_checksum_ok(line: str) -> bool:
    if len(line) < 69 or not line[68].isdigit():
        return False
    total = 0
    for ch in line[:68]:
        if ch.isdigit():
            total += int(ch)
        elif ch == "-":
            total += 1
    return total % 10 == int(line[68])


# --------------------------------------------------------------------------
# §4.1  core_record builders
# --------------------------------------------------------------------------

def element_set_no_from_tle(line1: str) -> str:
    """ELEMENT_SET_NO from a (possibly legacy) line-1 tail, for the §5 tie-break
    only (selection metadata — NOT part of the hashed core). Whitespace-tokenize,
    never fixed-slice: the legacy 5-wide element-set number + trailing backslash
    shift the columns."""
    raw = line1.rstrip("\r\n")
    had_backslash = raw.rstrip().endswith("\\")
    tokens = raw.replace("\\", " ").split()
    if not tokens:
        return "0"
    last_tok = tokens[-1]
    # A standard 69-col line ends with a checksum digit fused to the element-set
    # number (e.g. "9993" = set 999 + checksum 3); legacy lines do not.
    if (not had_backslash) and tle_checksum_ok(raw) and len(last_tok) > 1:
        last_tok = last_tok[:-1]
    return str(int(last_tok)) if last_tok.isdigit() else "0"


def core_record_from_tle(line1: str, line2: str) -> dict:
    """Build the canonical 11-key (pure-orbit) core record from a TLE line pair."""
    record = {
        "NORAD_CAT_ID": canon_norad(decode_satnum(line2[2:7])),
        "EPOCH": epoch_from_tle(line1),
        "INCLINATION": canon_decimal(line2[8:16]),
        "RA_OF_ASC_NODE": canon_decimal(line2[17:25]),
        "ECCENTRICITY": canon_decimal("0." + line2[26:33].strip()),
        "ARG_OF_PERICENTER": canon_decimal(line2[34:42]),
        "MEAN_ANOMALY": canon_decimal(line2[43:51]),
        "MEAN_MOTION": canon_decimal(line2[52:63]),
        "MEAN_MOTION_DOT": canon_decimal(line1[33:43]),
        "MEAN_MOTION_DDOT": decode_assumed_exp(line1[44:52]),
        "BSTAR": decode_assumed_exp(line1[53:61]),
    }
    assert set(record) == set(CORE_KEYS), "core record key mismatch"
    return record


_OMM_DEFAULTS = {
    "BSTAR": "0", "MEAN_MOTION_DOT": "0", "MEAN_MOTION_DDOT": "0",
}


def core_record_from_omm(omm: dict) -> dict:
    """Build the canonical 11-key (pure-orbit) core record from an OMM/GP record."""
    def field(key):
        if key in omm and omm[key] not in (None, ""):
            return str(omm[key])
        if key in _OMM_DEFAULTS:
            return _OMM_DEFAULTS[key]
        raise ValueError(f"OMM record missing mandatory core field {key}")

    record = {
        "NORAD_CAT_ID": canon_norad(omm["NORAD_CAT_ID"]),
        "EPOCH": epoch_from_omm(field("EPOCH")),
        "INCLINATION": canon_decimal(field("INCLINATION")),
        "RA_OF_ASC_NODE": canon_decimal(field("RA_OF_ASC_NODE")),
        "ECCENTRICITY": canon_decimal(field("ECCENTRICITY")),
        "ARG_OF_PERICENTER": canon_decimal(field("ARG_OF_PERICENTER")),
        "MEAN_ANOMALY": canon_decimal(field("MEAN_ANOMALY")),
        "MEAN_MOTION": canon_decimal(field("MEAN_MOTION")),
        "MEAN_MOTION_DOT": canon_decimal(field("MEAN_MOTION_DOT")),
        "MEAN_MOTION_DDOT": canon_decimal(field("MEAN_MOTION_DDOT")),
        "BSTAR": canon_decimal(field("BSTAR")),
    }
    assert set(record) == set(CORE_KEYS), "core record key mismatch"
    return record


def element_set_no_from_omm(omm: dict) -> str:
    """ELEMENT_SET_NO for the §5 tie-break only (selection metadata, not hashed)."""
    v = omm.get("ELEMENT_SET_NO")
    return str(int(v)) if v not in (None, "") else "0"


# --------------------------------------------------------------------------
# §4.5  catalog serialization → contentHash
# --------------------------------------------------------------------------

def canonicalize(data) -> bytes:
    """Canonical JSON bytes (identical rule to pipeline/snapshot.canonicalize)."""
    return json.dumps(
        data, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False,
    ).encode("utf-8")


def record_json_bytes(r: dict) -> bytes:
    """Fast canonical bytes for ONE 11-key core record (byte-identical to
    ``canonicalize(r)``). Keys are fixed + alphabetical and values are clean
    ASCII, so a template beats json.dumps — material for a 232M-elset rebuild."""
    return (
        f'{{"ARG_OF_PERICENTER":"{r["ARG_OF_PERICENTER"]}",'
        f'"BSTAR":"{r["BSTAR"]}",'
        f'"ECCENTRICITY":"{r["ECCENTRICITY"]}",'
        f'"EPOCH":"{r["EPOCH"]}",'
        f'"INCLINATION":"{r["INCLINATION"]}",'
        f'"MEAN_ANOMALY":"{r["MEAN_ANOMALY"]}",'
        f'"MEAN_MOTION":"{r["MEAN_MOTION"]}",'
        f'"MEAN_MOTION_DDOT":"{r["MEAN_MOTION_DDOT"]}",'
        f'"MEAN_MOTION_DOT":"{r["MEAN_MOTION_DOT"]}",'
        f'"NORAD_CAT_ID":"{r["NORAD_CAT_ID"]}",'
        f'"RA_OF_ASC_NODE":"{r["RA_OF_ASC_NODE"]}"}}'
    ).encode("ascii")


def canonical_bytes(records) -> bytes:
    """Sort by int(NORAD), reject duplicates, serialize the array (sole hash input)."""
    seen = set()
    for r in records:
        n = r["NORAD_CAT_ID"]
        if n in seen:
            raise ValueError(f"duplicate NORAD_CAT_ID in catalog: {n}")
        seen.add(n)
    ordered = sorted(records, key=lambda r: int(r["NORAD_CAT_ID"]))
    return canonicalize(ordered)


def content_sha256(records) -> str:
    """Consensus contentHash = SHA-256 of the canonical core projection."""
    return hashlib.sha256(canonical_bytes(records)).hexdigest()
