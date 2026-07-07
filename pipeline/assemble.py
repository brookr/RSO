#!/usr/bin/env python3
"""EPOCH-keyed daily catalog assembly with carry-forward (Phase B §2 / §5).

The deep-history archive is one UTC day at a time. For a given day D the catalog
is the carry-forward set: for every object, the single elset with the latest
EPOCH at or before D's end-of-day cutoff. Selection keys on **EPOCH, never
CREATION_DATE** (pre-2004 gp_history rows all carry the bulk-load CREATION_DATE
2004-08-15 — see the 2004-migration finding), and every tie-break is over
core-only fields so two builders reproduce the same set from core data alone.

The day's contentHash is then ``tle_normalize.content_sha256(catalog)``.

This module is source-agnostic: feed it canonical core records (from
``tle_normalize.core_record_from_tle`` / ``…_from_omm``) regardless of whether
they came from a zip TLE, the live API, or McDowell.
"""
from __future__ import annotations

from tle_normalize import canonical_bytes, content_sha256

# End-of-UTC-day cutoff suffix; epoch tokens are fixed-width ISO so a plain
# string compare is chronological.
_DAY_END = "T23:59:59.999999"


def _selection_key(epoch: str, element_set_no: str, record_bytes: bytes):
    """Tie-break: latest EPOCH, then highest ELEMENT_SET_NO, then largest bytes."""
    return (epoch, int(element_set_no), record_bytes)


class ObjectHistory:
    """Accumulates per-object elset history, then serves carry-forward catalogs."""

    def __init__(self):
        # norad(int) -> list of (epoch, sort_key, record) kept sorted by epoch
        self._hist: dict[int, list] = {}
        self._sorted = False

    def add(self, record: dict, element_set_no: str = "0") -> None:
        """Add a canonical 11-key core record. ``element_set_no`` is selection
        metadata for the tie-break only (it is NOT part of the hashed core)."""
        norad = int(record["NORAD_CAT_ID"])
        epoch = record["EPOCH"]
        key = _selection_key(epoch, element_set_no, canonical_bytes([record]))
        self._hist.setdefault(norad, []).append((epoch, key, record))
        self._sorted = False

    def add_tle(self, line1: str, line2: str) -> None:
        from tle_normalize import core_record_from_tle, element_set_no_from_tle
        self.add(core_record_from_tle(line1, line2), element_set_no_from_tle(line1))

    def _ensure_sorted(self) -> None:
        if not self._sorted:
            for entries in self._hist.values():
                entries.sort(key=lambda e: e[1])  # by (epoch, elsetno, bytes)
            self._sorted = True

    def catalog_for_day(self, day: str) -> list[dict]:
        """The carry-forward catalog (list of core records) for UTC day ``YYYY-MM-DD``."""
        self._ensure_sorted()
        cutoff = day + _DAY_END
        catalog = []
        for entries in self._hist.values():
            # entries sorted by (epoch, …); find the last one with epoch <= cutoff.
            lo, hi = 0, len(entries)
            while lo < hi:
                mid = (lo + hi) // 2
                if entries[mid][0] <= cutoff:
                    lo = mid + 1
                else:
                    hi = mid
            if lo > 0:
                # among entries with epoch <= cutoff, the tie-break key already
                # ordered them, so the last is the winner.
                catalog.append(entries[lo - 1][2])
        return catalog

    def content_sha256_for_day(self, day: str) -> str:
        return content_sha256(self.catalog_for_day(day))

    def object_count_for_day(self, day: str) -> int:
        return len(self.catalog_for_day(day))

    def first_epoch_day(self) -> str | None:
        """The earliest epoch date across all objects (the genesis candidate)."""
        self._ensure_sorted()
        firsts = [entries[0][0] for entries in self._hist.values() if entries]
        return min(firsts)[:10] if firsts else None
