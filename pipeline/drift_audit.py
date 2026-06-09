#!/usr/bin/env python3
"""Drift audit: re-query archived gp_history windows and diff against the record.

The v2 consensus rests on a measured invariant: once published, the
elset-intrinsic (non-excluded) fields of a gp_history row never change, and a
window's selection never changes. This audit re-checks that invariant on a
sliding sample of archived days and classifies every difference:

  expected_observation_drift  - excluded-field mutation (DECAY_DATE, naming,
                                ...). Normal Space-Track back-patching; logged,
                                never alerting.
  CORE_FIELD_MUTATION         - a non-excluded field changed on the same GP_ID.
                                Consensus risk: the field partition needs
                                widening. ALERT.
  SELECTION_DRIFT             - the window's deduped selection changed (new,
                                superseded, or retracted elsets). Consensus
                                risk in the capture rule itself. ALERT.

Run weekly from CI; exits 1 when any alert class is non-empty so the workflow
can open an issue with the report.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "pipeline")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import snapshot  # noqa: E402

# Wide NORAD ranges covering the observed catalog (dense low ids, the 8xxxx
# band, and the 27xxxx analyst band). Five requests per audited window keeps a
# 20-day sample around 100 requests -- well under Space-Track's hourly limit
# at the client's enforced pacing.
AUDIT_RANGES = ((0, 19999), (20000, 39999), (40000, 59999), (60000, 99999), (270000, 279999))


def audit_window(client, day, recorded_records):
    """Re-query day's window and diff against what the archive recorded."""
    manifest = snapshot.load_manifest(day)
    window_start = snapshot.normalize_utc_for_filter(manifest["delta_window_start_utc"])
    window_end = snapshot.normalize_utc_for_filter(manifest["delta_window_end_utc"])

    raw = []
    query_paths = []
    for low, high in AUDIT_RANGES:
        path = snapshot.build_query_path(
            "gp_history",
            [
                ("NORAD_CAT_ID", f"{low}--{high}"),
                ("CREATION_DATE", f"{window_start}--{window_end}"),
                ("orderby", "NORAD_CAT_ID asc,CREATION_DATE desc"),
            ],
        )
        batch = client.query(path)
        snapshot.validate_gp_records(batch, min_count=0, context=f"drift audit {low}--{high}")
        raw.extend(batch)
        query_paths.append(path)

    fresh_window = snapshot.filter_creation_window(
        raw, lower_inclusive=window_start, upper_exclusive=window_end
    )
    fresh_selected = {
        record["NORAD_CAT_ID"]: record
        for record in snapshot.dedupe_latest_per_object(fresh_window)
    }

    previous_day = snapshot.previous_date_str(day)
    previous_records = json.loads(snapshot.read_catalog_bytes(previous_day))
    previous_by_id = snapshot.records_by_cat_id(previous_records)
    recorded_by_id = snapshot.records_by_cat_id(recorded_records)
    recorded_updates = {
        cat_id: record
        for cat_id, record in recorded_by_id.items()
        if record != previous_by_id.get(cat_id)
    }

    observation_drift = []
    core_mutations = []
    selection_drift = []

    for cat_id, recorded in recorded_updates.items():
        fresh = fresh_selected.get(cat_id)
        if fresh is None:
            selection_drift.append(
                {"norad_cat_id": cat_id, "kind": "recorded_elset_absent",
                 "gp_id": recorded.get("GP_ID")}
            )
            continue
        if fresh.get("GP_ID") != recorded.get("GP_ID"):
            selection_drift.append(
                {"norad_cat_id": cat_id, "kind": "selection_superseded",
                 "recorded_gp_id": recorded.get("GP_ID"), "fresh_gp_id": fresh.get("GP_ID")}
            )
            continue
        for field in set(recorded) | set(fresh):
            if recorded.get(field) == fresh.get(field):
                continue
            change = {
                "norad_cat_id": cat_id,
                "gp_id": recorded.get("GP_ID"),
                "field": field,
                "recorded": recorded.get(field),
                "fresh": fresh.get(field),
            }
            if field in snapshot.CONTENT_EXCLUDED_FIELDS:
                observation_drift.append(change)
            else:
                core_mutations.append(change)

    for cat_id in fresh_selected:
        if cat_id not in recorded_updates:
            selection_drift.append(
                {"norad_cat_id": cat_id, "kind": "selection_appeared",
                 "fresh_gp_id": fresh_selected[cat_id].get("GP_ID")}
            )

    return {
        "date": day,
        "window_start_utc": window_start + "Z",
        "window_end_utc": window_end + "Z",
        "raw_rows": len(raw),
        "fresh_selected": len(fresh_selected),
        "recorded_updates": len(recorded_updates),
        "observation_drift_count": len(observation_drift),
        "core_field_mutations": core_mutations,
        "selection_drift": selection_drift,
        "observation_drift_sample": observation_drift[:25],
        "api_query_paths": query_paths,
    }


def pick_sample_days(archived_days, *, recent, older, seed):
    recent_days = archived_days[-recent:] if recent else []
    pool = archived_days[1:-recent] if recent else archived_days[1:]
    rng = random.Random(seed)
    older_days = sorted(rng.sample(pool, min(older, len(pool)))) if older and pool else []
    return sorted(set(older_days + recent_days))


def archived_day_list():
    days = []
    for manifest_path in sorted(snapshot.DATA_DIR.glob("*/*/*/manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if "delta_window_start_utc" in manifest:
            days.append(manifest["date"])
    return sorted(days)


def main() -> int:
    args = parse_args()
    archived = archived_day_list()
    if not archived:
        print("drift-audit: no archived window days found", file=sys.stderr)
        return 2
    sample = (
        snapshot.date_range(args.start, args.end)
        if args.start and args.end
        else pick_sample_days(archived, recent=args.recent, older=args.older, seed=args.seed)
    )
    sample = [day for day in sample if day in archived]
    print(f"drift-audit: checking {len(sample)} windows: {', '.join(sample)}")

    client = snapshot.SpaceTrackClient()
    results = []
    try:
        for day in sample:
            recorded = json.loads(snapshot.read_catalog_bytes(day))
            result = audit_window(client, day, recorded)
            results.append(result)
            print(
                f"  {day}: raw={result['raw_rows']:,} "
                f"observation_drift={result['observation_drift_count']} "
                f"CORE_MUTATIONS={len(result['core_field_mutations'])} "
                f"SELECTION_DRIFT={len(result['selection_drift'])}"
            )
    finally:
        client.close()

    alerts = [
        r for r in results if r["core_field_mutations"] or r["selection_drift"]
    ]
    report = {
        "schema": "rso-drift-audit-v1",
        "generated_at_utc": snapshot.utc_stamp(),
        "content_excluded_fields": list(snapshot.CONTENT_EXCLUDED_FIELDS),
        "windows_checked": len(results),
        "alert_window_count": len(alerts),
        "results": results,
    }
    report_path = Path(args.report) if args.report else (
        ROOT / "reports" / "drift" / f"drift-{snapshot.utc_stamp().replace(':', '')}.json"
    )
    snapshot.write_json(report_path, report)
    print(f"\nReport: {report_path}")

    if alerts:
        print(
            f"DRIFT ALERT: {len(alerts)} window(s) show core-field mutations or "
            "selection drift -- the consensus field partition may need widening.",
            file=sys.stderr,
        )
        return 1
    print("No consensus-affecting drift. Observation-plane drift is expected and logged.")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit archived windows for consensus drift.")
    parser.add_argument("--recent", type=int, default=7, help="Most recent windows to check.")
    parser.add_argument("--older", type=int, default=5, help="Random older windows to check.")
    parser.add_argument("--seed", default=None, help="Random seed for the older-day sample.")
    parser.add_argument("--start", help="Explicit range start (overrides sampling).")
    parser.add_argument("--end", help="Explicit range end (overrides sampling).")
    parser.add_argument("--report", help="Report output path.")
    return parser.parse_args()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except snapshot.SnapshotError as exc:
        print(f"drift_audit.py: {exc}", file=sys.stderr)
        raise SystemExit(2)
