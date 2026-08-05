#!/usr/bin/env python3
"""Full deep-history rebuild engine (Phase B §9 step 3) — bounded memory.

Streams the whole TLE corpus, canonicalizes every elset to the §4 11-key core,
external-sorts by EPOCH, then sweeps days in order producing one
``contentHash`` + ``recordCount`` per UTC day via carry-forward (§2/§5). Memory
is O(objects) (~70k current elsets), not O(elsets) — the 232M-elset corpus
never lives in RAM. Disk: one temp file (~corpus-expanded) + the sort's scratch.

Output: a manifest ``YYYY-MM-DD <contentHash> <recordCount>`` per day, plus a
JSON summary. The Merkle month-roots + on-chain blockHash linkage (§6) run
afterward on this manifest (they need the chain context).

Usage:
  python3 build_history.py --corpus DIR --out-dir DIR [--final-day YYYY-MM-DD]
                           [--temp DIR] [--year-max YYYY] [--keep-temp]
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import multiprocessing
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from tle_normalize import (  # noqa: E402
    core_record_from_tle, element_set_no_from_tle, record_json_bytes,
)


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _parse_to_line(l1, l2, year_max):
    """One elset -> TSV line (epoch\tnorad9\telset5\tcorejson), or None if filtered.
    Raises on a malformed elset (caller logs + skips — fail-closed)."""
    if year_max is not None:
        yy = int(l1[18:20])
        yr = 1900 + yy if yy >= 57 else 2000 + yy
        if yr > year_max:
            return None
    core = core_record_from_tle(l1, l2)
    elset = int(element_set_no_from_tle(l1))
    norad = int(core["NORAD_CAT_ID"])
    return (f"{core['EPOCH']}\t{norad:09d}\t{elset:05d}\t"
            f"{record_json_bytes(core).decode('ascii')}\n")


def _extract_one(task):
    """Worker: one zip -> its own part file (+ skip file). Returns counts + paths."""
    zf, part_path, skip_path, year_max = task
    kept = skipped = 0
    with open(part_path, "w", encoding="ascii", buffering=1 << 22) as out, \
         open(skip_path, "w", encoding="ascii", buffering=1 << 20) as skiplog:
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
                    line = _parse_to_line(l1, l2, year_max)
                    if line:
                        out.write(line)
                        kept += 1
                except Exception as e:
                    skipped += 1
                    skiplog.write(f"{os.path.basename(zf)}\t{e}\t{l1}\t{l2}\n")
        p.wait()
    return os.path.basename(zf), kept, skipped, part_path, skip_path


def pass1_extract(corpus_dir, temp_path, temp_dir, year_max, skip_log_path, jobs):
    """Extract every elset to TSV. Per-file parallelism (files are independent);
    parts are merged by the external sort. Nothing is silently dropped — every
    fail-closed reject is logged to ``skip_log_path``."""
    zips = sorted(glob.glob(os.path.join(corpus_dir, "*.zip")))
    if not zips:
        sys.exit(f"no .zip files in {corpus_dir}")
    jobs = max(1, min(jobs, len(zips)))
    log(f"pass 1: extracting {len(zips)} files with {jobs} workers")
    tasks = [(zf, os.path.join(temp_dir, f"part-{i:02d}.tsv"),
              os.path.join(temp_dir, f"skip-{i:02d}.tsv"), year_max)
             for i, zf in enumerate(zips)]

    if jobs == 1:
        results = [_extract_one(t) for t in tasks]
    else:
        with multiprocessing.Pool(jobs) as pool:
            results = pool.map(_extract_one, tasks)

    kept = sum(r[1] for r in results)
    skipped = sum(r[2] for r in results)
    parts = [r[3] for r in results]
    for name, k, s, _p, _sk in results:
        log(f"  {name}: kept={k:,} skipped={s:,}")

    # concatenate skip files into the single audit log, then remove parts' skips
    with open(skip_log_path, "w", encoding="ascii") as agg:
        for _n, _k, _s, _p, sk in results:
            with open(sk, encoding="ascii") as f:
                agg.write(f.read())
            os.remove(sk)
    log(f"pass 1 done: {kept:,} elsets, {skipped:,} skipped (log: {skip_log_path})")
    return kept, skipped, parts


def external_sort(parts, temp_path, temp_dir, jobs):
    """Whole-line LC_ALL=C sort of all part files -> temp_path. Fixed-width
    epoch/norad/elset make lexical order == (chrono, numeric, numeric). Merge
    scratch (-T) is kept on the same (large) volume, not the system disk."""
    log(f"sort: external merge of {len(parts)} part(s) (LC_ALL=C)")
    env = dict(os.environ, LC_ALL="C")
    cmd = ["sort", f"--parallel={max(1, jobs)}", "-T", temp_dir,
           "-S", "4G", "-o", temp_path] + parts
    subprocess.run(cmd, check=True, env=env)
    for p in parts:
        os.remove(p)
    log("sort done")


def _hash_catalog(current):
    """contentHash of the current carry-forward catalog (sorted by int NORAD)."""
    body = b",".join(current[n] for n in sorted(current))
    return hashlib.sha256(b"[" + body + b"]").hexdigest()


def pass2_sweep(temp_path, manifest_path, final_day):
    """Sweep sorted elsets; emit one (day, contentHash, recordCount) per UTC day."""
    log("pass 2: day sweep")
    current = {}            # norad9(str) -> core json bytes (latest <= cursor)
    last_day = None
    days_emitted = 0
    genesis = None
    first_hash = last_hash = None

    def emit_range(d_start, d_end, mf):
        nonlocal days_emitted, first_hash, last_hash
        if d_start > d_end:
            return
        h = _hash_catalog(current)
        rc = len(current)
        for d in _iter_days(d_start, d_end):
            mf.write(f"{d} {h} {rc}\n")
            days_emitted += 1
            if first_hash is None:
                first_hash = h
            last_hash = h

    with open(temp_path, encoding="ascii") as f, \
         open(manifest_path, "w", encoding="ascii", buffering=1 << 20) as mf:
        for line in f:
            epoch, norad9, _elset, corejson = line.rstrip("\n").split("\t", 3)
            day = epoch[:10]
            if genesis is None:
                genesis = day
            if last_day is not None and day > last_day:
                emit_range(last_day, _prev_day(day), mf)
            current[norad9] = corejson.encode("ascii")
            last_day = day
        # finalize from the last change-day through the archive end
        if last_day is not None:
            end = final_day if (final_day and final_day >= last_day) else last_day
            emit_range(last_day, end, mf)

    log(f"pass 2 done: {days_emitted:,} days, genesis {genesis}, "
        f"final objects {len(current):,}")
    return {"days": days_emitted, "genesis": genesis, "final_day": final_day,
            "final_object_count": len(current), "first_hash": first_hash,
            "last_hash": last_hash}


# --- calendar helpers (integer, leap-correct) ---
_ML = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def _leap(y):
    return y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)


def _dim(y, m):
    return 29 if (m == 2 and _leap(y)) else _ML[m - 1]


def _prev_day(day):
    y, m, d = int(day[:4]), int(day[5:7]), int(day[8:10])
    d -= 1
    if d == 0:
        m -= 1
        if m == 0:
            y -= 1
            m = 12
        d = _dim(y, m)
    return f"{y:04d}-{m:02d}-{d:02d}"


def _iter_days(d_start, d_end):
    y, m, d = int(d_start[:4]), int(d_start[5:7]), int(d_start[8:10])
    while True:
        cur = f"{y:04d}-{m:02d}-{d:02d}"
        yield cur
        if cur == d_end:
            return
        d += 1
        if d > _dim(y, m):
            d = 1
            m += 1
            if m > 12:
                m = 1
                y += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--final-day", default=None, help="archive end, default = last elset day")
    ap.add_argument("--temp", default=None, help="temp dir (default: out-dir)")
    ap.add_argument("--year-max", type=int, default=None, help="slice: keep epoch year <= this")
    ap.add_argument("--jobs", type=int, default=1, help="parallel pass-1 workers (per-file)")
    ap.add_argument("--keep-temp", action="store_true")
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    temp_dir = args.temp or args.out_dir
    os.makedirs(temp_dir, exist_ok=True)
    temp_path = os.path.join(temp_dir, "elsets.tsv")
    manifest_path = os.path.join(args.out_dir, "daily_manifest.txt")
    summary_path = os.path.join(args.out_dir, "summary.json")
    skip_log_path = os.path.join(args.out_dir, "skipped_elsets.tsv")

    t0 = time.time()
    kept, skipped, parts = pass1_extract(
        args.corpus, temp_path, temp_dir, args.year_max, skip_log_path, args.jobs)
    external_sort(parts, temp_path, temp_dir, args.jobs)
    summary = pass2_sweep(temp_path, manifest_path, args.final_day)
    summary.update({"elsets_kept": kept, "elsets_skipped": skipped,
                    "schema": "rso-core-omm-v1", "seconds": round(time.time() - t0, 1)})
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    if not args.keep_temp:
        os.remove(temp_path)
    log(f"DONE in {summary['seconds']}s -> {manifest_path}")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
