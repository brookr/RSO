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


def _ascii_digits(s: str) -> bool:
    """True iff s is one or more ASCII 0-9 (NOT Unicode str.isdigit, which
    accepts Arabic-Indic/fullwidth/superscript digits a strict verifier rejects)."""
    return s != "" and all("0" <= c <= "9" for c in s)


# Whitespace handling is ASCII-only by spec (rso-verify SPEC §2.2): exactly
# \t\n\v\f\r and space. Python's str.strip() strips a *different* (Unicode) set
# — NBSP, NEL, the C0 separators, ideographic space — which is exactly the
# cross-language divergence the spec forbids. Exotic whitespace is left in
# place so the ASCII-digit guards reject it fail-closed.
_ASCII_WS = " \t\n\v\f\r"


def _strip_ascii(s: str) -> str:
    return s.strip(_ASCII_WS)


def _rstrip_ascii(s: str) -> str:
    return s.rstrip(_ASCII_WS)


def canon_decimal(s: str) -> str:
    """Canonical shortest plain-decimal token for a terminating decimal string."""
    s = _strip_ascii(s)
    s = s.rstrip("\\")
    s = _strip_ascii(s)
    if not s:
        raise ValueError("empty decimal field")
    neg = False
    if s[0] in "+-":
        neg = s[0] == "-"
        s = s[1:]
    if "e" in s or "E" in s:
        mant, _, exp = s.lower().partition("e")
        esign = ""
        # NOTE: `exp[:1] in "+-"` would be True for the EMPTY string ('' is a
        # substring of "+-") and then exp[0] raises IndexError on inputs like
        # "1e" — a tuple membership test has no such trap.
        if exp[:1] in ("+", "-"):
            esign, exp = exp[0], exp[1:]
        # the mantissa must contain at least one digit — ".e5" must not
        # materialize zeros through the exponent shift
        if not _ascii_digits(exp) or not any("0" <= c <= "9" for c in mant):
            raise ValueError(f"bad exponent form: {s!r}")
        ev = int(esign + exp)
        if not -999 <= ev <= 999:  # SPEC §2.2 step 3: |exponent| ≤ 999
            raise ValueError(f"exponent out of bounds: {s!r}")
        s = _apply_exponent(mant, ev)
    if "." not in s:
        s += "."
    int_part, frac_part = s.split(".", 1)
    if not _ascii_digits(int_part + frac_part):
        raise ValueError(f"non-numeric decimal field: {s!r}")
    frac_part = frac_part.rstrip("0")
    int_part = int_part.lstrip("0") or "0"
    out = int_part if not frac_part else int_part + "." + frac_part
    if _is_all_zero(out):
        return "0"
    return ("-" + out) if neg else out


def _valid_assumed_exp_str(s: str) -> bool:
    """The assumed-exponent field's exponent: an optional sign then EXACTLY
    1 or 2 ASCII digits (rso-verify SPEC §2.2 shape bounds)."""
    if not s:
        return False
    if s[0] in "+-":
        s = s[1:]
    return len(s) in (1, 2) and _ascii_digits(s)


def decode_assumed_exp(field: str) -> str:
    """Decode a TLE assumed-decimal-exponent field (BSTAR, MEAN_MOTION_DDOT).

    One unified rule covers the standard form AND every Space-Track historical
    overflow form for large drag terms on near-reentry objects. All values below
    are verified byte-for-byte against the authoritative Space-Track ``gp_history``
    JSON (the OMM emits the same number, preserving cross-source equivalence):
      `` 17028-3`` -> 0.00017028 (standard)   ``+2083500`` -> 0.20835
      ``-2979601`` -> -2.9796                 ``+1582202`` -> 15.822 (nddot)
      ``49000-10`` -> 4.9e-11                 ``973196+1`` -> 97.3196   ``+00000-0`` -> 0

    Decode (integer/string only, no float):
      1. A leading ``+``/``-``/space is the mantissa sign (``-`` negative, else
         positive); a leading digit means no sign (mantissa starts at col 1).
      2. If there is an interior ``+``/``-``, the exponent is the substring from it
         (e.g. ``-3``, ``+1``, ``-10``) and the mantissa is the digits before it,
         with an implied decimal point **5 places from the right** (5 digits ->
         ``0.MMMMM``, 6 -> ``M.MMMMM`` — the leading digit is the integer part).
      3. If there is NO interior sign, the mantissa is the first 5 digits
         (``0.MMMMM``) and the exponent is whatever follows — 1 or 2 digits, a
         positive exponent: ``+028410`` -> ``0.02841`` (exp ``0``), ``+2083500`` ->
         ``0.20835`` (exp ``00``). This split is the only one consistent with
         Space-Track's JSON across the 5-digit, 6-digit, and trailing-space forms.
    Fail-closed on anything that does not fit. (Column-shifted records — blank
    international designator — are realigned upstream in ``core_record_from_tle``.)
    """
    if not field or len(field) < 7:
        raise ValueError(f"assumed-exponent field too short: {field!r}")
    c0 = field[0]
    if c0 in "+-":
        msign, rest = ("-" if c0 == "-" else ""), _rstrip_ascii(field[1:])
    elif c0 == " ":
        msign, rest = "", _rstrip_ascii(field[1:])
    elif _ascii_digits(c0):
        msign, rest = "", _rstrip_ascii(field)
    else:
        raise ValueError(f"malformed assumed-exponent field: {field!r}")
    sp = max(rest.rfind("+"), rest.rfind("-"))
    if sp > 0:                      # interior exponent sign: '-3', '+1', '-10'
        mant_digits, exp_str = rest[:sp], rest[sp:]
    else:                           # no sign: 5-digit mantissa, the rest is a +exponent
        mant_digits, exp_str = rest[:5], rest[5:]
    # SPEC §2.2 shape bounds: mantissa EXACTLY 5 or 6 digits, exponent 1-2 digits.
    if not 5 <= len(mant_digits) <= 6 or not _ascii_digits(mant_digits) or not _valid_assumed_exp_str(exp_str):
        raise ValueError(f"malformed assumed-exponent field: {field!r}")
    if _is_all_zero(mant_digits):
        return "0"
    int_part = mant_digits[:-5].lstrip("0") or "0"   # implied decimal 5 from the right
    return canon_decimal(msign + int_part + "." + mant_digits[-5:] + "e" + str(int(exp_str)))


# --------------------------------------------------------------------------
# §4.4  NORAD_CAT_ID
# --------------------------------------------------------------------------

def decode_satnum(field: str) -> int:
    """Alpha-5 / plain decode of a satnum field to its integer value.

    Bounded + ASCII-strict (rso-verify SPEC §2.4): a plain numeric id has at
    most 9 significant digits; an Alpha-5 id is EXACTLY 5 chars (ASCII letter +
    4 ASCII digits, ≤ 339,999). The leading char is ASCII-uppercased ONLY —
    str.upper() Unicode-folds (long-s ſ → S) which a strict verifier rejects.
    """
    field = _strip_ascii(field)
    if not field:
        raise ValueError("empty satnum")
    c0 = field[0]
    if "0" <= c0 <= "9":
        if not _ascii_digits(field):
            raise ValueError(f"bad numeric satnum: {field!r}")
        if len(field.lstrip("0") or "0") > 9:  # ≤ 9 significant digits
            raise ValueError(f"numeric satnum out of range: {field!r}")
        return int(field)
    if len(field) != 5:  # Alpha-5 is exactly 5 chars
        raise ValueError(f"bad Alpha-5 satnum length: {field!r}")
    if "a" <= c0 <= "z":
        c0 = chr(ord(c0) - 32)  # ASCII uppercase only, never Unicode case-folding
    if c0 not in ALPHA5 or c0 < "A":  # must be a letter, not a digit
        raise ValueError(f"bad Alpha-5 leading char: {field!r}")
    rest = field[1:]
    if not _ascii_digits(rest):
        raise ValueError(f"bad Alpha-5 satnum: {field!r}")
    return ALPHA5.index(c0) * 10000 + int(rest)


def canon_norad(value) -> str:
    """Canonical base-10 integer-string NORAD token from a TLE field or OMM id."""
    if isinstance(value, int):
        n = value
    else:
        s = _strip_ascii(str(value))
        # An OMM id may be plain digits (incl. future 9-digit) or Alpha-5.
        # ASCII-digit test, never str.isdigit() (Unicode digits parse in int()).
        n = int(s) if _ascii_digits(s) else decode_satnum(s)
    if not 0 <= n <= 999_999_999:  # SPEC §2.4 bound; keeps int(token) exact everywhere
        raise ValueError(f"NORAD id out of range: {value!r}")
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
    """Canonical EPOCH token from a TLE line-1 ``YYDDD.FFFFFFFF`` field.

    The SPEC §1.1 line check runs HERE too (not only in core_record_from_tle):
    the epoch window is located by offset, so a non-ASCII char anywhere before
    it would silently shift the slice differently in byte- vs UTF-16- vs
    code-point-indexed implementations."""
    _require_ascii_tle_line(line1)
    raw = _strip_ascii(line1[18:32])
    if not _ascii_digits(raw[:2]):
        raise ValueError(f"non-ASCII/invalid epoch year: {raw!r}")
    yy = int(raw[:2])
    year = 1900 + yy if yy >= 57 else 2000 + yy
    doy_str, _, frac_str = raw[2:].partition(".")
    if not _ascii_digits(doy_str) or (frac_str and not _ascii_digits(frac_str)):
        raise ValueError(f"non-ASCII/invalid epoch field: {raw!r}")
    doy = int(doy_str)
    if not (1 <= doy <= _days_in_year(year)):
        raise ValueError(f"day-of-year {doy} out of range for {year}")  # B5 fail-closed
    if len(frac_str) > 8:  # SPEC §2.3: L ≤ 8 keeps F·USEC_PER_DAY < 2^63 in every language
        raise ValueError(f"epoch fraction longer than 8 digits: {raw!r}")
    if frac_str:
        scale = 10 ** len(frac_str)
        usec_of_day = (int(frac_str) * USEC_PER_DAY + scale // 2) // scale
    else:
        usec_of_day = 0
    return _render_epoch(year, doy, usec_of_day)


def epoch_from_omm(iso: str) -> str:
    """Canonical EPOCH token from an OMM ISO timestamp (assert on 864-grid)."""
    s = _strip_ascii(iso)
    if s.endswith("Z"):
        s = s[:-1]
    date_part, _, time_part = s.partition("T")
    dparts = date_part.split("-")
    hh_s, mi_s, sec_part = time_part.split(":")
    sec_s, _, frac = sec_part.partition(".")
    for tok in dparts + [hh_s, mi_s, sec_s] + ([frac] if frac else []):
        if not _ascii_digits(tok):  # B6: reject Unicode digits / underscores int() would accept
            raise ValueError(f"non-ASCII/invalid OMM epoch token: {tok!r}")
    y, mo, d = (int(x) for x in dparts)
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
    # ASCII-digit tests only — str.isdigit() accepts Unicode digits that int()
    # happily parses (guarded upstream on pipeline paths, but this function is
    # also part of the direct API surface).
    if len(line) < 69 or not _ascii_digits(line[68]):
        return False
    total = 0
    for ch in line[:68]:
        if "0" <= ch <= "9":
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
    return str(int(last_tok)) if _ascii_digits(last_tok) else "0"


# rso-verify SPEC §1.1 input model: a TLE line contains ONLY the ASCII
# whitespace set (0x09-0x0D) and printable ASCII (0x20-0x7E). Compiled regex so
# the guard is C-speed on the 232M-elset rebuild path.
_NON_ASCII_TLE = __import__("re").compile(r"[^\x09-\x0d\x20-\x7e]")


def _require_ascii_tle_line(line: str) -> None:
    """Reject any byte outside the SPEC §1.1 TLE alphabet — this is what makes
    byte / UTF-16 / code-point slicing coincide across the producer and every
    clean-room client."""
    m = _NON_ASCII_TLE.search(line)
    if m is not None:
        raise ValueError(
            f"non-ASCII/control char U+{ord(m.group()):04X} at offset {m.start()} in TLE line"
        )


def _line1_offset(line1: str) -> int:
    """Detect a +1 column shift in line 1 from the epoch decimal point.

    A standard line puts the epoch ``YYDDD.FFFFFFFF`` at cols 19-32, so the ``.``
    is at index 23. A subset of Space-Track's legacy export records have a blank
    international designator plus one extra space, shifting every line-1 field +1
    (``.`` at index 24). Keyed off the epoch dot (not the designator) so it never
    false-positives on a standard line. Other alignments fall through to 0 and
    fail closed downstream.
    """
    if len(line1) > 23 and line1[23] == ".":
        return 0
    if len(line1) > 24 and line1[24] == ".":
        return 1
    return 0


def core_record_from_tle(line1: str, line2: str) -> dict:
    """Build the canonical 11-key (pure-orbit) core record from a TLE line pair.

    Line 1 fields are read at a detected offset so blank-designator column-shifted
    records parse correctly; line 2 carries no designator and is never shifted.
    """
    _require_ascii_tle_line(line1)
    _require_ascii_tle_line(line2)
    off = _line1_offset(line1)
    l1 = line1[off:] if off else line1
    record = {
        "NORAD_CAT_ID": canon_norad(decode_satnum(line2[2:7])),
        "EPOCH": epoch_from_tle(l1),
        "INCLINATION": canon_decimal(line2[8:16]),
        "RA_OF_ASC_NODE": canon_decimal(line2[17:25]),
        "ECCENTRICITY": canon_decimal("0." + _strip_ascii(line2[26:33])),
        "ARG_OF_PERICENTER": canon_decimal(line2[34:42]),
        "MEAN_ANOMALY": canon_decimal(line2[43:51]),
        "MEAN_MOTION": canon_decimal(line2[52:63]),
        "MEAN_MOTION_DOT": canon_decimal(l1[33:43]),
        "MEAN_MOTION_DDOT": decode_assumed_exp(l1[44:52]),
        "BSTAR": decode_assumed_exp(l1[53:61]),
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


_VALUE_CHARSET = frozenset("0123456789.-T:")


def canonical_bytes(records) -> bytes:
    """Sort by int(NORAD), reject duplicates + non-canonical tokens, serialize
    the array (sole hash input). Token guards per rso-verify SPEC §2.6: NORAD is
    ^(0|[1-9][0-9]*)$ with ≤ 9 digits (int-sort exact in every language), every
    value non-empty and drawn from [0-9.\\-T:] (no JSON escaping can ever fire)."""
    seen = set()
    for r in records:
        if set(r) != set(CORE_KEYS):
            raise ValueError(f"core record key set mismatch: {sorted(r)}")
        n = r["NORAD_CAT_ID"]
        if not _ascii_int_token(n) or len(n) > 9:
            raise ValueError(f"non-canonical NORAD_CAT_ID token: {n!r}")
        for key in CORE_KEYS:
            v = r[key]
            if not v or any(c not in _VALUE_CHARSET for c in v):
                raise ValueError(f"non-canonical value token {v!r} in record {n}")
        if n in seen:
            raise ValueError(f"duplicate NORAD_CAT_ID in catalog: {n}")
        seen.add(n)
    ordered = sorted(records, key=lambda r: int(r["NORAD_CAT_ID"]))
    return canonicalize(ordered)


def content_sha256(records) -> str:
    """Consensus contentHash = SHA-256 of the canonical core projection."""
    return hashlib.sha256(canonical_bytes(records)).hexdigest()
