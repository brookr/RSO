#!/usr/bin/env python3
"""Independent verifier for the deep-history rebuild (Phase B §9 verify).

Recomputes selected days' contentHashes by a DIFFERENT aggregation than
build_history: a per-object "latest elset with EPOCH <= D" reduction (no external
sort, no day-sweep). Agreement with daily_manifest.txt cross-validates the
sort+sweep path. Shares only the (unit-tested) §4 canonicalizer.

Usage: python3 verify_days.py --corpus DIR --manifest FILE --jobs N D1 D2 ...
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import multiprocessing
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from tle_normalize import (  # noqa: E402
    core_record_from_tle, element_set_no_from_tle, record_json_bytes,
)

TARGETS: list[str] = []  # set in worker init


def _worker(zf):
    """Per file: for each target day, the latest elset <= D per object.
    Returns {day_index: {norad9: (epoch, elset, core_bytes)}}."""
    best = {j: {} for j in range(len(TARGETS))}
    p = subprocess.Popen(["unzip", "-p", zf], stdout=subprocess.PIPE, bufsize=1 << 22)
    prev = None
    for raw in p.stdout:
        if raw[:2] == b"1 ":
            prev = raw
        elif raw[:2] == b"2 " and prev is not None:
            l1 = prev.decode("ascii", "replace").rstrip("\n")
            l2 = raw.decode("ascii", "replace").rstrip("\n")
            prev = None
            try:
                core = core_record_from_tle(l1, l2)
                epoch = core["EPOCH"]
                day = epoch[:10]
                norad = f"{int(core['NORAD_CAT_ID']):09d}"
                elset = int(element_set_no_from_tle(l1))
                cbytes = record_json_bytes(core)
                key = (epoch, elset, cbytes)
                for j, D in enumerate(TARGETS):
                    if day <= D:
                        cur = best[j].get(norad)
                        if cur is None or key > cur:
                            best[j][norad] = key
            except Exception:
                pass
    p.wait()
    return best


def _init(targets):
    global TARGETS
    TARGETS = targets


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("days", nargs="+")
    args = ap.parse_args()

    want = {}
    with open(args.manifest) as f:
        days_set = set(args.days)
        for line in f:
            d, h, c = line.split()
            if d in days_set:
                want[d] = (h, int(c))

    zips = sorted(glob.glob(os.path.join(args.corpus, "*.zip")))
    print(f"verifying {len(args.days)} days over {len(zips)} files, {args.jobs} workers")
    with multiprocessing.Pool(args.jobs, initializer=_init, initargs=(args.days,)) as pool:
        partials = pool.map(_worker, zips)

    ok = True
    for j, D in enumerate(args.days):
        merged = {}
        for part in partials:
            for norad, key in part[j].items():
                cur = merged.get(norad)
                if cur is None or key > cur:
                    merged[norad] = key
        body = b",".join(merged[n][2] for n in sorted(merged))
        h = hashlib.sha256(b"[" + body + b"]").hexdigest()
        rc = len(merged)
        exp_h, exp_c = want.get(D, ("<absent>", -1))
        match = (h == exp_h and rc == exp_c)
        ok = ok and match
        print(f"  {D}  objects={rc} (manifest {exp_c})  "
              f"{'MATCH' if match else 'MISMATCH'}  {h[:16]} vs {exp_h[:16]}")
    print("ALL MATCH" if ok else "VERIFICATION FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
