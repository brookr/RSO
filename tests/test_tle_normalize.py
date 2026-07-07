"""Tests for the canonical numeric core projection (Phase B §4, rso-core-omm-v1).

Validates the float-free canonicalization against the §6 reference vectors, the
proven cross-source equivalences (TLE implied-decimal ≡ OMM trailing-zero), the
864-µs epoch grid (and its fail-closed rejection), Alpha-5 decoding, the legacy
spaced-designator / 5-wide / trailing-backslash export format, and TLE ≡ OMM
record identity for one elset.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "pipeline")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import tle_normalize as tn  # noqa: E402


def with_checksum(line68: str) -> str:
    """Append the TLE modulo-10 checksum digit to a 68-char line body."""
    assert len(line68) == 68, f"need 68 chars, got {len(line68)}"
    total = sum(int(c) if c.isdigit() else (1 if c == "-" else 0) for c in line68)
    return line68 + str(total % 10)


# A char-exact modern ISS elset matching the §4.5 byte-exact example.
ISS_L1_BODY = "1 25544U 98067A   26172.76913116  .00016717  00000-0  17028-3 0  999"
ISS_L2_BODY = "2 25544  51.6326 277.4139 0004499  89.5790 270.6630 15.49357580    0"
ISS_L1 = with_checksum(ISS_L1_BODY)
ISS_L2 = with_checksum(ISS_L2_BODY)

ISS_CORE = {
    "ARG_OF_PERICENTER": "89.579", "BSTAR": "0.00017028", "ECCENTRICITY": "0.0004499",
    "EPOCH": "2026-06-21T18:27:32.932224",
    "INCLINATION": "51.6326", "MEAN_ANOMALY": "270.663", "MEAN_MOTION": "15.4935758",
    "MEAN_MOTION_DDOT": "0", "MEAN_MOTION_DOT": "0.00016717", "NORAD_CAT_ID": "25544",
    "RA_OF_ASC_NODE": "277.4139",
}

# The §6(c) legacy 1959 Vanguard export line (spaced designator, +signs, 5-wide
# element-set number, trailing backslash). No checksum digit (backslash slot).
VANGUARD_1959_L1 = "1 00005U 58002  B 59143.01387787  .00002171 +00000-0 +00000-0 0 00041\\"


class CanonDecimalTests(unittest.TestCase):
    def test_eccentricity_tle_omm_collapse(self):
        # TLE implied "0." ≡ OMM trailing zero.
        self.assertEqual(tn.canon_decimal("0." + "0004499"), "0.0004499")
        self.assertEqual(tn.canon_decimal("0.00044990"), "0.0004499")

    def test_mean_motion_strips_trailing_zero(self):
        self.assertEqual(tn.canon_decimal("15.49357580"), "15.4935758")

    def test_signed_and_zero_forms(self):
        self.assertEqual(tn.canon_decimal("-.00002171"), "-0.00002171")
        self.assertEqual(tn.canon_decimal("+0.0000"), "0")
        self.assertEqual(tn.canon_decimal("-0.000"), "0")  # never -0
        self.assertEqual(tn.canon_decimal(" .00016717"), "0.00016717")

    def test_leading_and_trailing_noise(self):
        self.assertEqual(tn.canon_decimal(" 51.6326 "), "51.6326")
        self.assertEqual(tn.canon_decimal("089.5790\\"), "89.579")


class AssumedExponentTests(unittest.TestCase):
    def test_bstar_tle_omm_collapse(self):
        self.assertEqual(tn.decode_assumed_exp("17028-3"), "0.00017028")
        self.assertEqual(tn.canon_decimal("0.00017028000000"), "0.00017028")

    def test_negative_mantissa(self):
        self.assertEqual(tn.decode_assumed_exp("-11606-4"), "-0.000011606")

    def test_zero_spellings(self):
        for z in ("+00000-0", "00000-0", "00000+0", "-00000-0", " 00000-0"):
            self.assertEqual(tn.decode_assumed_exp(z), "0")

    def test_positive_exponent(self):
        self.assertEqual(tn.decode_assumed_exp("30050-3"), "0.0003005")

    def test_fail_closed_on_missing_exponent_sign(self):
        with self.assertRaises(ValueError):
            tn.decode_assumed_exp("170283")   # 6 chars, truncated

    def test_spacetrack_2digit_exponent_overflow(self):
        # Large drag terms on near-reentry objects — every value verified against
        # the authoritative Space-Track gp_history JSON.
        self.assertEqual(tn.decode_assumed_exp("+2083500"), "0.20835")    # BSTAR 0.20835x10^0
        self.assertEqual(tn.decode_assumed_exp("+0208301"), "0.2083")     # 0.02083x10^1
        self.assertEqual(tn.decode_assumed_exp("-2979601"), "-2.9796")    # -0.29796x10^1
        self.assertEqual(tn.decode_assumed_exp("+3387300"), "0.33873")    # nddot 0.33873
        self.assertEqual(tn.decode_assumed_exp("+1582202"), "15.822")     # nddot 0.15822x10^2
        self.assertEqual(tn.decode_assumed_exp("973196+1"), "97.3196")    # 6-digit mantissa 9.73196x10^1
        self.assertEqual(tn.decode_assumed_exp("100000+2"), "100")        # 1.00000x10^2
        self.assertEqual(tn.decode_assumed_exp("+028410 "), "0.02841")    # nddot, no sign, 1-digit exp (trailing space)
        self.assertEqual(tn.decode_assumed_exp("+1056600"), "0.10566")    # no sign, exp 00
        self.assertEqual(tn.decode_assumed_exp(" 17028-3"), "0.00017028")  # standard, leading space

    def test_spacetrack_negative_2digit_exponent(self):
        # Tiny BSTAR needing a -10 exponent (no leading sign; mantissa in cols 1-5).
        self.assertEqual(tn.decode_assumed_exp("49000-10"), "0.000000000049")  # 4.9e-11
        self.assertEqual(tn.decode_assumed_exp("87000-10"), "0.000000000087")  # 8.7e-11


class ColumnShiftTests(unittest.TestCase):
    def test_blank_designator_plus_one_shift_recovers(self):
        # Blank international designator + extra space shifts line-1 fields +1.
        shifted = "1 22273U           04141.15499409  .00442848 -92388-5  22200-3 0  4447\\"
        self.assertEqual(tn._line1_offset(shifted), 1)
        l1 = shifted[tn._line1_offset(shifted):]
        self.assertEqual(tn.decode_assumed_exp(l1[53:61]), "0.000222")     # recovered BSTAR (API)
        self.assertEqual(tn.epoch_from_tle(l1)[:10], "2004-05-20")

    def test_standard_line_has_no_shift(self):
        self.assertEqual(tn._line1_offset(ISS_L1), 0)


class NoradTests(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(tn.canon_norad("00005"), "5")
        self.assertEqual(tn.canon_norad("25544"), "25544")

    def test_alpha5_boundaries(self):
        self.assertEqual(tn.decode_satnum("A0000"), 100000)
        self.assertEqual(tn.decode_satnum("S9999"), 269999)
        self.assertEqual(tn.decode_satnum("T0000"), 270000)
        self.assertEqual(tn.decode_satnum("Z9999"), 339999)

    def test_alpha5_skips_i_and_o(self):
        # 'H'->17, next valid is 'J'->18 (I skipped); 'N'->22, then 'P'->23 (O skipped).
        self.assertEqual(tn.decode_satnum("J0000"), 180000)
        self.assertEqual(tn.decode_satnum("P0000"), 230000)
        with self.assertRaises(ValueError):
            tn.decode_satnum("I0000")

    def test_nine_digit_omm_forward_compat(self):
        self.assertEqual(tn.canon_norad("270000"), "270000")
        self.assertEqual(tn.canon_norad(123456789), "123456789")

    def test_alpha5_omm_string_decoded_not_opaque(self):
        # An OMM carrying "T0000" must decode to the same token as the TLE.
        self.assertEqual(tn.canon_norad("T0000"), "270000")


class EpochTests(unittest.TestCase):
    def test_iss_tle(self):
        self.assertEqual(tn.epoch_from_tle(ISS_L1), "2026-06-21T18:27:32.932224")

    def test_iss_omm_roundtrip(self):
        self.assertEqual(tn.epoch_from_omm("2026-06-21T18:27:32.932224"),
                         "2026-06-21T18:27:32.932224")
        self.assertEqual(tn.epoch_from_omm("2026-06-21T18:27:32.932224Z"),
                         "2026-06-21T18:27:32.932224")

    def test_sputnik_genesis(self):
        l1 = "1 00001U 57001A   57277.80437500  .00000000  00000-0  00000-0 0  9990"
        self.assertEqual(tn.epoch_from_tle(l1), "1957-10-04T19:18:18.000000")

    def test_vanguard_1959_epoch(self):
        # Computed from first principles: 0.01387787 day = 1199.047968 s.
        self.assertEqual(tn.epoch_from_tle(VANGUARD_1959_L1),
                         "1959-05-23T00:19:59.047968")

    def test_year_pivot(self):
        self.assertTrue(tn.epoch_from_tle(
            "1 00001U 57001A   99001.00000000  .0  0  0 0 0").startswith("1999-"))
        self.assertTrue(tn.epoch_from_tle(
            "1 00001U 57001A   00001.00000000  .0  0  0 0 0").startswith("2000-"))

    def test_leap_year_doy_366(self):
        # 2024 is a leap year; DOY 366 = Dec 31.
        self.assertEqual(tn.epoch_from_tle(
            "1 00001U 57001A   24366.00000000  .0  0  0 0 0"),
            "2024-12-31T00:00:00.000000")

    def test_off_grid_omm_rejected(self):
        # One microsecond past the proven .932224 grid point -> not a 864 multiple.
        with self.assertRaises(ValueError):
            tn.epoch_from_omm("2026-06-21T18:27:32.932225")

    def test_on_grid_spacetrack_sample(self):
        # A real Space-Track OMM EPOCH (must be on the 864 grid).
        self.assertEqual(tn.epoch_from_omm("2026-06-07T12:15:59.925600"),
                         "2026-06-07T12:15:59.925600")


class CoreRecordTests(unittest.TestCase):
    def test_iss_core_record_matches_reference(self):
        self.assertEqual(tn.core_record_from_tle(ISS_L1, ISS_L2), ISS_CORE)

    def test_iss_byte_exact_serialization(self):
        expected = (
            '{"ARG_OF_PERICENTER":"89.579","BSTAR":"0.00017028",'
            '"ECCENTRICITY":"0.0004499",'
            '"EPOCH":"2026-06-21T18:27:32.932224","INCLINATION":"51.6326",'
            '"MEAN_ANOMALY":"270.663","MEAN_MOTION":"15.4935758","MEAN_MOTION_DDOT":"0",'
            '"MEAN_MOTION_DOT":"0.00016717","NORAD_CAT_ID":"25544",'
            '"RA_OF_ASC_NODE":"277.4139"}'
        )
        self.assertEqual(tn.canonicalize(ISS_CORE).decode(), expected)

    def test_fast_record_json_matches_canonicalize(self):
        self.assertEqual(tn.record_json_bytes(ISS_CORE), tn.canonicalize(ISS_CORE))

    def test_element_set_no_for_tiebreak_not_in_core(self):
        # ELEMENT_SET_NO is read for the §5 tie-break but never enters the core.
        self.assertNotIn("ELEMENT_SET_NO", tn.core_record_from_tle(ISS_L1, ISS_L2))
        self.assertNotIn("EPHEMERIS_TYPE", tn.core_record_from_tle(ISS_L1, ISS_L2))
        # Standard: "...0  9993" -> set 999 (checksum stripped).
        self.assertEqual(tn.element_set_no_from_tle(ISS_L1), "999")
        # Legacy 5-wide + backslash: "...0 00041\" -> set 41.
        self.assertEqual(tn.element_set_no_from_tle(VANGUARD_1959_L1), "41")

    def test_vanguard_1959_line1_fields(self):
        # The §6(c) reference tokens that derive from line 1 alone.
        self.assertEqual(tn.decode_satnum(VANGUARD_1959_L1[2:7]), 5)
        self.assertEqual(tn.canon_decimal(VANGUARD_1959_L1[33:43]), "0.00002171")
        self.assertEqual(tn.decode_assumed_exp(VANGUARD_1959_L1[44:52]), "0")
        self.assertEqual(tn.decode_assumed_exp(VANGUARD_1959_L1[53:61]), "0")


class TleOmmEquivalenceTests(unittest.TestCase):
    def test_same_elset_tle_and_omm_hash_identically(self):
        omm = {
            "NORAD_CAT_ID": "25544", "EPOCH": "2026-06-21T18:27:32.932224",
            "INCLINATION": "51.6326", "RA_OF_ASC_NODE": "277.4139",
            "ECCENTRICITY": "0.00044990", "ARG_OF_PERICENTER": "89.5790",
            "MEAN_ANOMALY": "270.6630", "MEAN_MOTION": "15.49357580",
            "MEAN_MOTION_DOT": "0.00016717", "MEAN_MOTION_DDOT": "0",
            "BSTAR": "0.00017028000000", "EPHEMERIS_TYPE": "0", "ELEMENT_SET_NO": "999",
        }
        self.assertEqual(tn.core_record_from_omm(omm),
                         tn.core_record_from_tle(ISS_L1, ISS_L2))
        self.assertEqual(tn.content_sha256([tn.core_record_from_omm(omm)]),
                         tn.content_sha256([tn.core_record_from_tle(ISS_L1, ISS_L2)]))

    def test_omm_absent_drag_fields_default_to_zero(self):
        omm = {
            "NORAD_CAT_ID": "5", "EPOCH": "1959-05-23T00:19:59.047968",
            "INCLINATION": "34.2", "RA_OF_ASC_NODE": "100.0",
            "ECCENTRICITY": "0.14", "ARG_OF_PERICENTER": "200.0",
            "MEAN_ANOMALY": "150.0", "MEAN_MOTION": "10.5",
        }
        rec = tn.core_record_from_omm(omm)
        self.assertEqual(rec["BSTAR"], "0")
        self.assertEqual(rec["MEAN_MOTION_DOT"], "0")
        self.assertEqual(set(rec), set(tn.CORE_KEYS))  # exactly 11 keys


class CatalogTests(unittest.TestCase):
    def _rec(self, norad):
        r = dict(ISS_CORE)
        r["NORAD_CAT_ID"] = str(norad)
        return r

    def test_numeric_sort_not_lexicographic(self):
        recs = [self._rec(270000), self._rec(99999), self._rec(100000), self._rec(5)]
        ordered = tn.canonicalize(sorted(recs, key=lambda r: int(r["NORAD_CAT_ID"])))
        # 5 < 99999 < 100000 < 270000 (numeric), not "100000" < "270000" < "5" < "99999"
        self.assertLess(ordered.index(b'"5"'), ordered.index(b'"99999"'))
        self.assertLess(ordered.index(b'"99999"'), ordered.index(b'"100000"'))
        self.assertLess(ordered.index(b'"100000"'), ordered.index(b'"270000"'))

    def test_duplicate_norad_is_hard_error(self):
        with self.assertRaises(ValueError):
            tn.canonical_bytes([self._rec(5), self._rec(5)])

    def test_empty_catalog_hashes_empty_array(self):
        import hashlib
        self.assertEqual(tn.content_sha256([]),
                         hashlib.sha256(b"[]").hexdigest())

    def test_content_hash_is_deterministic(self):
        recs = [self._rec(5), self._rec(270000), self._rec(100)]
        self.assertEqual(tn.content_sha256(recs), tn.content_sha256(list(reversed(recs))))


class FailClosedGuardTests(unittest.TestCase):
    """Regression tests for the integrity-review fixes (B5 DOY, B6 ASCII)."""

    def _l1(self, epoch14):  # line-1 with a full-width 14-char YYDDD.FFFFFFFF epoch field
        assert len(epoch14) == 14, epoch14
        return f"1 00001U 57001A   {epoch14}  .00000000  00000-0  00000-0 0  999"

    def test_b5_doy_out_of_range_rejected(self):
        for bad in ("26000.50000000", "26400.50000000", "25366.50000000"):  # DOY 0, 400, 366(2025)
            with self.assertRaises(ValueError):
                tn.epoch_from_tle(self._l1(bad))

    def test_b5_valid_and_leap_366_accepted(self):
        self.assertTrue(tn.epoch_from_tle(self._l1("26001.00000000")).startswith("2026-01-01"))
        self.assertEqual(tn.epoch_from_tle(self._l1("24366.00000000")),
                         "2024-12-31T00:00:00.000000")

    def test_b6_unicode_digits_rejected(self):
        self.assertEqual(tn.canon_decimal("1.5"), "1.5")  # ascii still fine
        with self.assertRaises(ValueError):
            tn.canon_decimal("1.٥")            # Arabic-Indic 5
        with self.assertRaises(ValueError):
            tn.decode_assumed_exp("٥0000-3")
        with self.assertRaises(ValueError):
            tn.epoch_from_omm("2026-06-21T18:27:32.93222٤")
        with self.assertRaises(ValueError):
            tn.decode_satnum("００００５")  # fullwidth digits

    def test_b6_underscore_exponent_rejected(self):
        with self.assertRaises(ValueError):
            tn.canon_decimal("1e1_0")

    def test_b6_omm_epoch_still_clean(self):
        self.assertEqual(tn.epoch_from_omm("2026-06-07T12:15:59.925600"),
                         "2026-06-07T12:15:59.925600")


if __name__ == "__main__":
    unittest.main()
