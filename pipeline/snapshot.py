#!/usr/bin/env python3
"""
RSO Archive - Space-Track GP Catalog Snapshot Pipeline.

The canonical archive is a rolling state machine. A daily snapshot is built from
the previous archived snapshot plus Space-Track gp_history rows published during
the closed UTC window before the snapshot cutoff.
"""

import argparse
import base64
import collections
import io
import gzip
import hashlib
import http.cookiejar
import ipaddress
import json
import os
import random
import re
import socket
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path


CUTOFF_TIME = "00:00:00"
OPERATOR_RUN_TIME = "00:15:00"
PIPELINE_VERSION = "1.0.0"

# --- consensus/observation field partition -----------------------------------
# Space-Track mutates object-directory fields on already-published gp_history
# rows (measured 2026-06-09 across 50 archived windows: DECAY_DATE backfills up
# to 7,224 days late; OBJECT_NAME/OBJECT_TYPE/TLE_LINE0 flip when TBA objects
# get named; the rest of the satcat family backfills by the same mechanism).
# The consensus contentHash therefore covers only elset-intrinsic fields, which
# the same study showed are immutable once published (50/50 archived day hashes
# reproduce from a fresh query with these fields excluded). The excluded fields
# remain in the raw catalog exactly as returned and are tracked per day, with
# the time we learned them, in annotations.json (the observation plane).
CONTENT_SCHEMA = "rso-core-v1"
CONTENT_EXCLUDED_FIELDS = (
    "COUNTRY_CODE",
    "DECAY_DATE",
    "LAUNCH_DATE",
    "OBJECT_ID",
    "OBJECT_NAME",
    "OBJECT_TYPE",
    "RCS_SIZE",
    "SITE",
    "TLE_LINE0",
)
ANNOTATIONS_SCHEMA = "rso-annotations-v1"

SPACETRACK_BASE = "https://www.space-track.org"
SPACETRACK_LOGIN = f"{SPACETRACK_BASE}/ajaxauth/login"
SPACETRACK_QUERY = f"{SPACETRACK_BASE}/basicspacedata/query"

# Space-Track guideline: fewer than 30 req/min and 300 req/hr; exceeding either
# can suspend the account. Space-Track does NOT return HTTP 429 / Retry-After --
# a violation comes back as HTTP 500 with "violated your query rate limit" in the
# body -- so the client paces PROACTIVELY to the windows below (never tripping the
# limit) and treats that 500 as a retryable backstop. The per-minute/per-hour
# pacing keeps every operator fork compliant during long roll-forward/replay runs
# with no per-fork tuning. RSO_REQUEST_DELAY is now just an optional minimum gap.
REQUEST_DELAY = float(os.environ.get("RSO_REQUEST_DELAY", "2.5"))
MAX_REQUESTS_PER_MINUTE = int(os.environ.get("RSO_MAX_REQUESTS_PER_MINUTE", "25"))
MAX_REQUESTS_PER_HOUR = int(os.environ.get("RSO_MAX_REQUESTS_PER_HOUR", "275"))
MAX_REQUEST_RETRIES = int(os.environ.get("RSO_MAX_REQUEST_RETRIES", "5"))
CATALOG_RANGE_SIZE = int(os.environ.get("RSO_CATALOG_RANGE_SIZE", "10000"))
MAX_NORAD_CAT_ID = int(os.environ.get("RSO_MAX_NORAD_CAT_ID", "339999"))
MIN_OBJECT_COUNT = int(os.environ.get("RSO_MIN_OBJECT_COUNT", "40000"))
DEFAULT_RETAINED_CATALOG_COUNT = int(os.environ.get("RSO_RETAINED_CATALOG_COUNT", "2"))

DATA_DIR = Path(__file__).parent.parent / "data"
LEDGER_PATH = Path(__file__).parent.parent / "ledger.json"
REPORTS_DIR = Path(__file__).parent.parent / "reports"
RELEASE_OUTPUT_DIR = Path(__file__).parent.parent / ".release"

STORAGE_BACKENDS = frozenset({"none", "github_release", "arweave", "ipfs_pinata"})
UPLOAD_POLICIES = frozenset({"never", "if_missing", "always_mirror"})
ARWEAVE_GATEWAY_DEFAULT = "https://arweave.net"
MAX_RELEASE_BUNDLE_BYTES = int(os.environ.get("RSO_MAX_RELEASE_BUNDLE_BYTES", str(100 * 1024 * 1024)))
MAX_CATALOG_BYTES = int(os.environ.get("RSO_MAX_CATALOG_BYTES", str(200 * 1024 * 1024)))
MAX_JSON_BYTES = int(os.environ.get("RSO_MAX_JSON_BYTES", str(2 * 1024 * 1024)))
ARWEAVE_MAX_CHUNK_SIZE = 256 * 1024
ARWEAVE_MIN_CHUNK_SIZE = 32 * 1024
ARWEAVE_CHUNK_UPLOAD_RETRIES = int(os.environ.get("ARWEAVE_CHUNK_UPLOAD_RETRIES", "5"))
ARWEAVE_CHUNK_UPLOAD_RETRY_DELAY = float(
    os.environ.get("ARWEAVE_CHUNK_UPLOAD_RETRY_DELAY", "40")
)
ARWEAVE_TRANSACTION_RETRIES = int(os.environ.get("ARWEAVE_TRANSACTION_RETRIES", "3"))
ARWEAVE_TRANSACTION_RETRY_DELAY = float(
    os.environ.get("ARWEAVE_TRANSACTION_RETRY_DELAY", "20")
)
ARWEAVE_TRANSIENT_CHUNK_ERRORS = frozenset(
    {
        "data_root_not_found",
        "exceeds_disk_pool_size_limit",
        "not_joined",
        "timeout",
    }
)
ARWEAVE_TRANSIENT_HTTP_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
RELEASE_ARTIFACT_FILENAMES = (
    "catalog.json.gz",
    "manifest.json",
    "annotations.json",
    "delta.json",
    "audit.json",
    "visibility_state.json",
)

REQUIRED_OMM_FIELDS = frozenset(
    {
        "NORAD_CAT_ID",
        "CREATION_DATE",
        "EPOCH",
        "MEAN_MOTION",
        "ECCENTRICITY",
        "INCLINATION",
        "RA_OF_ASC_NODE",
        "ARG_OF_PERICENTER",
        "MEAN_ANOMALY",
    }
)


class SnapshotError(RuntimeError):
    """Raised when a snapshot would be incomplete or invalid."""


class SpaceTrackRateLimiter:
    """Proactively keep Space-Track requests under the published limits.

    Space-Track allows fewer than 30 requests/minute and 300 requests/hour and
    may suspend accounts that exceed either. It does NOT emit HTTP 429 or a
    Retry-After header, so reactive backoff alone cannot keep a long run
    compliant -- the only safe control is to pace before each request and never
    trip the limit. acquire() sleeps until issuing a request now would breach
    neither the rolling per-minute window, the rolling per-hour window, nor an
    optional minimum spacing. This keeps every operator fork well behaved during
    multi-day roll-forward/replay without per-fork tuning.
    """

    def __init__(
        self,
        *,
        per_minute=MAX_REQUESTS_PER_MINUTE,
        per_hour=MAX_REQUESTS_PER_HOUR,
        min_spacing=0.0,
        sleep=time.sleep,
        clock=time.monotonic,
    ):
        if per_minute < 1 or per_hour < 1:
            raise SnapshotError("Space-Track rate limits must be positive")
        self.per_minute = per_minute
        self.per_hour = per_hour
        self.min_spacing = max(0.0, min_spacing)
        self._sleep = sleep
        self._clock = clock
        self._calls = collections.deque()
        self._last = None

    def acquire(self):
        for _ in range(10000):  # bounded; resolves in 1-2 iterations in practice
            now = self._clock()
            while self._calls and now - self._calls[0] >= 3600:
                self._calls.popleft()
            wait = 0.0
            if self.min_spacing and self._last is not None:
                wait = max(wait, self.min_spacing - (now - self._last))
            minute = [t for t in self._calls if now - t < 60]
            if len(minute) >= self.per_minute:
                wait = max(wait, 60 - (now - minute[0]))
            if len(self._calls) >= self.per_hour:
                wait = max(wait, 3600 - (now - self._calls[0]))
            if wait <= 0:
                break
            self._sleep(wait)
        now = self._clock()
        self._calls.append(now)
        self._last = now


def request_backoff_seconds(attempt, *, rate_limited, jitter=random.uniform):
    """Exponential backoff for transient Space-Track errors / rate-limit 500s."""
    base = min(2.0 * (2 ** attempt), 30.0)
    if rate_limited:
        # A rate-limit 500 means the minute window is full; wait it out.
        base = max(60.0, base)
    return base + jitter(0.0, base * 0.25)


def is_rate_limit_response(detail):
    return "violated your query rate limit" in detail.lower()


class SpaceTrackClient:
    def __init__(self, *, rate_limiter=None, sleep=time.sleep, jitter=None):
        cookie_jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(cookie_jar)
        )
        self.authenticated = False
        self.rate_limiter = rate_limiter or SpaceTrackRateLimiter(min_spacing=REQUEST_DELAY)
        self._sleep = sleep
        self._jitter = jitter if jitter is not None else random.uniform

    def _request(self, url, data=None):
        headers = {"User-Agent": f"rso-archive/{PIPELINE_VERSION}"}
        body = urllib.parse.urlencode(data).encode("utf-8") if data is not None else None
        last_error = None
        for attempt in range(MAX_REQUEST_RETRIES + 1):
            # Pace proactively before every attempt so we stay under the limits
            # rather than relying on tripping them.
            self.rate_limiter.acquire()
            req = urllib.request.Request(url, data=body, headers=headers)
            try:
                resp = self.opener.open(req, timeout=180)
                return resp.read()
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    detail = exc.read().decode("utf-8", errors="replace")
                except Exception:
                    detail = ""
                # Space-Track signals a rate-limit violation as HTTP 500 with a
                # marker in the body (not 429/Retry-After). 5xx gateway errors
                # are transient. Anything else (e.g. a bad query) is not retried.
                rate_limited = exc.code == 500 and is_rate_limit_response(detail)
                transient = exc.code in (502, 503, 504)
                last_error = SnapshotError(
                    f"Space-Track request failed (HTTP {exc.code}): {detail[:200].strip()}"
                )
                if not (rate_limited or transient) or attempt >= MAX_REQUEST_RETRIES:
                    raise last_error from exc
                self._sleep(
                    request_backoff_seconds(attempt, rate_limited=rate_limited, jitter=self._jitter)
                )
            except (TimeoutError, urllib.error.URLError) as exc:
                last_error = SnapshotError(f"Space-Track request did not settle: {exc}")
                if attempt >= MAX_REQUEST_RETRIES:
                    raise last_error from exc
                self._sleep(
                    request_backoff_seconds(attempt, rate_limited=False, jitter=self._jitter)
                )
        raise last_error or SnapshotError("Space-Track request failed")

    def login(self):
        user = os.environ.get("SPACETRACK_USER")
        passwd = os.environ.get("SPACETRACK_PASS")
        if not user or not passwd:
            raise SnapshotError("Set SPACETRACK_USER and SPACETRACK_PASS env vars")

        raw = self._request(
            SPACETRACK_LOGIN,
            {
                "identity": user,
                "password": passwd,
            },
        )

        try:
            result = json.loads(raw)
        except ValueError:
            result = None

        if isinstance(result, dict) and result.get("Login") == "Failed":
            raise SnapshotError("Space-Track login failed. Check credentials.")

        self.authenticated = True
        print("Authenticated with Space-Track.org")

    def query(self, query_path):
        if not self.authenticated:
            self.login()

        url = f"{SPACETRACK_QUERY}{query_path}"
        validate_query_url(url)
        print(f"  Querying: ...{query_path[:140]}")
        raw = self._request(url)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            snippet = raw[:300].decode("utf-8", errors="replace")
            raise SnapshotError(f"Space-Track returned non-JSON response: {snippet}") from exc

        if isinstance(payload, dict) and "error" in payload:
            raise SnapshotError(f"Space-Track error response: {payload['error']}")
        return payload

    def close(self):
        try:
            self._request(f"{SPACETRACK_BASE}/ajaxauth/logout")
        except Exception:
            pass


def parse_date(date_str):
    return datetime.strptime(date_str, "%Y-%m-%d")


def date_str(date_obj):
    return date_obj.strftime("%Y-%m-%d")


def previous_date_str(current_date_str):
    return date_str(parse_date(current_date_str) - timedelta(days=1))


def get_cutoff_for_date(current_date_str):
    """Return the canonical midnight UTC cutoff for a given snapshot date."""
    parse_date(current_date_str)
    return f"{current_date_str}T{CUTOFF_TIME}"


def normalize_utc_for_filter(value):
    value = str(value)
    return value[:-1] if value.endswith("Z") else value


def now_utc():
    return datetime.now(timezone.utc)


def utc_stamp(dt=None):
    if dt is None:
        dt = now_utc()
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def encode_query_value(value):
    """Encode a Space-Track path segment value without leaving raw spaces."""
    return urllib.parse.quote(str(value), safe=",.-_:")


def validate_query_url(url):
    if any(ch.isspace() for ch in url):
        raise SnapshotError(f"Query URL contains raw whitespace: {url}")
    urllib.request.Request(url)


def build_query_path(api_class, clauses):
    """
    Build a Space-Track REST path with encoded path-segment values.

    clauses is an ordered iterable of (field, value) pairs, for example:
    [("CREATION_DATE", "2026-04-12T00:00:00--2026-04-13T00:00:00")].
    """
    segments = ["class", api_class]
    for field, value in clauses:
        segments.append(str(field))
        segments.append(encode_query_value(value))
    segments.extend(["format", "json"])
    path = "/" + "/".join(segments)
    validate_query_url(f"{SPACETRACK_QUERY}{path}")
    return path


def catalog_id_sort_key(record):
    try:
        return int(record.get("NORAD_CAT_ID", 0))
    except (TypeError, ValueError):
        return 0


def iter_catalog_ranges(max_catalog_id=MAX_NORAD_CAT_ID, range_size=CATALOG_RANGE_SIZE):
    if max_catalog_id < 1:
        raise SnapshotError("--max-catalog-id must be positive")
    if range_size < 1:
        raise SnapshotError("--range-size must be positive")

    start = 0
    while start <= max_catalog_id:
        end = min(start + range_size - 1, max_catalog_id)
        yield start, end
        start = end + 1


def validate_gp_records(records, min_count=MIN_OBJECT_COUNT, context="Space-Track response"):
    if not isinstance(records, list):
        raise SnapshotError(
            f"{context} was {type(records).__name__}, expected list. "
            "Space-Track may have returned an error payload."
        )

    if len(records) < min_count:
        raise SnapshotError(
            f"{context} has {len(records):,} records, below minimum {min_count:,}; "
            "refusing to archive a likely incomplete snapshot."
        )

    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise SnapshotError(f"{context} record {index} is not an object")
        validate_gp_record_values(record, index=index, context=context)
        missing = REQUIRED_OMM_FIELDS.difference(record)
        if missing:
            missing_list = ", ".join(sorted(missing))
            cat_id = record.get("NORAD_CAT_ID", f"index {index}")
            raise SnapshotError(f"{context} record {cat_id} missing fields: {missing_list}")


# A NORAD catalog number is a canonical non-negative decimal integer: ASCII
# digits only with no leading-zero alias ("0" itself is allowed; "05" is not).
# This keeps the string form a bijection with the int used for sort/dedup, so
# string-keyed uniqueness and int-keyed ordering can never disagree, and the
# hashed catalog bytes stay reproducible across language implementations.
# str.isdigit() is insufficient: it also accepts Unicode digits (Arabic-Indic,
# fullwidth) and non-decimal digit characters (superscripts) that int() then
# rejects or aliases. NORAD_CAT_ID is an integer, not a hash or 0x address;
# identifiers that legitimately carry significant leading zeros are compared as
# canonical strings elsewhere and are never int()-coerced.
_CANONICAL_NORAD_CAT_ID = re.compile(r"0|[1-9][0-9]*")


def is_canonical_norad_cat_id(value):
    return isinstance(value, str) and _CANONICAL_NORAD_CAT_ID.fullmatch(value) is not None


def validate_gp_record_values(record, *, index, context):
    cat_id = record.get("NORAD_CAT_ID")
    if not is_canonical_norad_cat_id(cat_id):
        raise SnapshotError(f"{context} record {index} has invalid NORAD_CAT_ID")
    for field, value in record.items():
        if not isinstance(field, str):
            raise SnapshotError(f"{context} record {cat_id} has a non-string field name")
        # Space-Track returns JSON null for fields like DECAY_DATE, RCS_SIZE,
        # SITE, LAUNCH_DATE, and COUNTRY_CODE on many objects. null is allowed:
        # it serializes deterministically as `null` across implementations (and
        # is already part of committed snapshot hashes). Any other non-string
        # type (notably float) is rejected to fail closed on the real
        # cross-language determinism risk.
        if value is not None and not isinstance(value, str):
            raise SnapshotError(
                f"{context} record {cat_id} field {field} is {type(value).__name__}, expected string or null"
            )


def creation_time(record):
    value = str(record.get("CREATION_DATE", ""))
    return value[:-1] if value.endswith("Z") else value


def epoch_time(record):
    value = str(record.get("EPOCH", ""))
    return value[:-1] if value.endswith("Z") else value


def numeric_record_field(record, field):
    try:
        return int(record.get(field, 0))
    except (TypeError, ValueError):
        return 0


def element_selection_key(record):
    """
    Pick the latest published row for the canonical archive.

    CREATION_DATE defines when a row was published and therefore whether it is
    inside a deterministic daily window. Once a row is inside the window, the
    archive records the latest public Space-Track publication for the object.
    EPOCH remains a final stable tie-breaker for rare equal publication times.
    """
    return (
        creation_time(record),
        numeric_record_field(record, "GP_ID"),
        epoch_time(record),
    )


def filter_creation_window(records, lower_inclusive=None, upper_exclusive=None):
    filtered = []
    for record in records:
        created = creation_time(record)
        if lower_inclusive is not None and created < lower_inclusive:
            continue
        if upper_exclusive is not None and created >= upper_exclusive:
            continue
        filtered.append(record)
    return filtered


def dedupe_latest_per_object(records):
    """Keep the latest published element row per NORAD_CAT_ID."""
    selected = {}
    for record in records:
        cat_id = record.get("NORAD_CAT_ID")
        if cat_id is None:
            continue
        existing = selected.get(cat_id)
        if existing is None or element_selection_key(record) > element_selection_key(existing):
            selected[cat_id] = record
    return sorted(selected.values(), key=catalog_id_sort_key)


def canonicalize(data):
    """Produce canonical JSON bytes for hashing."""
    return json.dumps(
        data,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def core_record(record):
    """Project a raw record onto the consensus (elset-intrinsic) field set."""
    return {key: value for key, value in record.items() if key not in CONTENT_EXCLUDED_FIELDS}


def core_records(records):
    return [core_record(record) for record in records]


def core_content_sha256(records):
    """Consensus contentHash: SHA-256 of the canonical core projection."""
    return compute_hash(canonicalize(core_records(records)))


def compute_hash(canonical_bytes):
    return hashlib.sha256(canonical_bytes).hexdigest()


def record_hash(record):
    return compute_hash(canonicalize(record))


def records_by_cat_id(records):
    selected = {}
    for index, record in enumerate(records):
        cat_id = record.get("NORAD_CAT_ID")
        if not is_canonical_norad_cat_id(cat_id):
            raise SnapshotError(f"catalog record {index} has invalid NORAD_CAT_ID")
        if cat_id in selected:
            raise SnapshotError(f"duplicate NORAD_CAT_ID value: {cat_id}")
        selected[cat_id] = record
    return selected


def sorted_records_from_state(state_by_cat_id):
    return sorted(state_by_cat_id.values(), key=catalog_id_sort_key)


def snapshot_dir(current_date_str):
    date_obj = parse_date(current_date_str)
    return DATA_DIR / f"{date_obj.year}" / f"{date_obj.month:02d}" / f"{date_obj.day:02d}"


def catalog_gz_path(current_date_str):
    return snapshot_dir(current_date_str) / "catalog.json.gz"


def report_dir():
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    return REPORTS_DIR


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")


def read_json_if_exists(path, default=None):
    if not path.exists():
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def env_flag(name):
    value = os.environ.get(name, "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def query_gp_history_ranges(
    client,
    creation_date_filter,
    max_catalog_id=MAX_NORAD_CAT_ID,
    range_size=CATALOG_RANGE_SIZE,
):
    records = []
    query_paths = []
    ranges = list(iter_catalog_ranges(max_catalog_id, range_size))

    for index, (start, end) in enumerate(ranges):
        path = build_query_path(
            "gp_history",
            [
                ("NORAD_CAT_ID", f"{start}--{end}"),
                ("CREATION_DATE", creation_date_filter),
                ("orderby", "NORAD_CAT_ID asc,CREATION_DATE desc"),
            ],
        )
        batch = client.query(path)
        validate_gp_records(batch, min_count=0, context=f"gp_history {start}--{end}")
        records.extend(batch)
        query_paths.append(path)
        # Pacing (per-minute, per-hour, and the optional RSO_REQUEST_DELAY minimum
        # gap) is enforced centrally by the client rate limiter before each request.

    return records, query_paths


def validate_annotation_rows(rows, *, context):
    if not isinstance(rows, list):
        raise SnapshotError(
            f"{context} was {type(rows).__name__}, expected list. "
            "Space-Track may have returned an error payload."
        )
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise SnapshotError(f"{context} row {index} is not an object")
        for field, value in row.items():
            if not isinstance(field, str):
                raise SnapshotError(f"{context} row {index} has a non-string field name")
            if value is not None and not isinstance(value, str):
                raise SnapshotError(
                    f"{context} row {index} field {field} is "
                    f"{type(value).__name__}, expected string or null"
                )
    return rows


def query_annotation_observations(client, previous_cutoff, current_cutoff):
    """Query Space-Track's own change feeds for the daily window.

    satcat_change (windowed on CHANGE_MADE) records catalog directory edits as
    previous->current transitions; decay (windowed on MSG_EPOCH) records decay
    messages. Both catch knowledge that never reaches the gp window capture:
    a decayed object publishes no further elsets, so a DECAY_DATE stamped onto
    an already-captured row would otherwise stay invisible (measured: the gp
    capture alone saw 2 of 118 decay stamps over 50 days). These rows are
    observation-plane only and never feed the consensus contentHash.
    """
    window = f"{previous_cutoff}--{current_cutoff}"
    satcat_path = build_query_path(
        "satcat_change",
        [("CHANGE_MADE", window), ("orderby", "CHANGE_MADE asc")],
    )
    satcat_rows = validate_annotation_rows(
        client.query(satcat_path), context="satcat_change response"
    )
    decay_path = build_query_path(
        "decay",
        [("MSG_EPOCH", window), ("orderby", "MSG_EPOCH asc")],
    )
    decay_rows = validate_annotation_rows(client.query(decay_path), context="decay response")
    return satcat_rows, decay_rows, [satcat_path, decay_path]


def build_annotations(
    current_date_str,
    records,
    previous_records,
    *,
    observed_at_utc,
    window_start_utc=None,
    window_end_utc=None,
    satcat_changes=None,
    decay_messages=None,
    query_paths=None,
    baseline=False,
):
    """Build the day's observation-plane artifact.

    Records, with the time of recording, everything the node learned about the
    mutable object-directory fields: the per-object changes visible between the
    prior and current raw catalogs, plus Space-Track's own satcat_change/decay
    feeds for the window. The consensus core never includes these fields, so
    two nodes may legitimately hold different annotations for the same day.
    """
    changes = []
    if not baseline and previous_records is not None:
        previous_by_id = records_by_cat_id(previous_records or [])
        for record in records:
            cat_id = record.get("NORAD_CAT_ID")
            prior = previous_by_id.get(cat_id)
            if prior == record:
                continue
            for field in CONTENT_EXCLUDED_FIELDS:
                current_value = record.get(field)
                previous_value = prior.get(field) if prior is not None else None
                if prior is None and current_value is None:
                    continue
                if prior is not None and current_value == previous_value:
                    continue
                changes.append(
                    {
                        "norad_cat_id": cat_id,
                        "field": field,
                        "previous": previous_value,
                        "current": current_value,
                        "first_observation": prior is None,
                    }
                )
        changes.sort(key=lambda item: (int_string_sort_key(item["norad_cat_id"]), item["field"]))

    annotations = {
        "schema": ANNOTATIONS_SCHEMA,
        "date": current_date_str,
        "observed_at_utc": observed_at_utc,
        "source": "space-track.org",
        "fields": list(CONTENT_EXCLUDED_FIELDS),
        "baseline": bool(baseline),
        "catalog_changes": changes,
        "satcat_changes": satcat_changes if satcat_changes is not None else [],
        "decay_messages": decay_messages if decay_messages is not None else [],
    }
    if window_start_utc is not None:
        annotations["window_start_utc"] = window_start_utc
    if window_end_utc is not None:
        annotations["window_end_utc"] = window_end_utc
    if query_paths:
        annotations["api_query_paths"] = list(query_paths)
    return annotations


def query_current_gp(client, min_count=MIN_OBJECT_COUNT):
    path = build_query_path(
        "gp",
        [
            ("orderby", "NORAD_CAT_ID asc"),
        ],
    )
    records = client.query(path)
    validate_gp_records(records, min_count=min_count, context="current gp")
    return sorted(records, key=catalog_id_sort_key), [path]


def pull_updates_between_cutoffs(
    client,
    previous_cutoff,
    current_cutoff,
    max_catalog_id=MAX_NORAD_CAT_ID,
    range_size=CATALOG_RANGE_SIZE,
):
    records, query_paths = query_gp_history_ranges(
        client,
        f"{previous_cutoff}--{current_cutoff}",
        max_catalog_id=max_catalog_id,
        range_size=range_size,
    )
    records = filter_creation_window(
        records,
        lower_inclusive=previous_cutoff,
        upper_exclusive=current_cutoff,
    )
    return dedupe_latest_per_object(records), records, query_paths


def apply_updates(base_records, updates):
    state = records_by_cat_id(base_records)
    new_ids = []
    changed_ids = []
    unchanged_update_ids = []
    ignored_older_update_ids = []

    for record in updates:
        cat_id = record["NORAD_CAT_ID"]
        existing = state.get(cat_id)
        if existing is None:
            new_ids.append(cat_id)
            state[cat_id] = record
        elif element_selection_key(record) > element_selection_key(existing):
            changed_ids.append(cat_id)
            state[cat_id] = record
        elif record_hash(existing) == record_hash(record):
            unchanged_update_ids.append(cat_id)
        else:
            ignored_older_update_ids.append(cat_id)

    return sorted_records_from_state(state), {
        "new_norad_cat_ids": sorted(new_ids, key=int_string_sort_key),
        "updated_norad_cat_ids": sorted(changed_ids, key=int_string_sort_key),
        "unchanged_update_norad_cat_ids": sorted(unchanged_update_ids, key=int_string_sort_key),
        "ignored_older_update_norad_cat_ids": sorted(
            ignored_older_update_ids,
            key=int_string_sort_key,
        ),
        "carried_forward_count": (
            len(base_records)
            - len(changed_ids)
            - len(unchanged_update_ids)
            - len(ignored_older_update_ids)
        ),
    }


def apply_updates_to_state(state_by_cat_id, updates):
    """Apply updates in place using the same selection rule as daily snapshots."""
    applied = 0
    ignored = 0
    for record in updates:
        cat_id = record["NORAD_CAT_ID"]
        existing = state_by_cat_id.get(cat_id)
        if existing is None or element_selection_key(record) > element_selection_key(existing):
            state_by_cat_id[cat_id] = record
            applied += 1
        elif record_hash(existing) != record_hash(record):
            ignored += 1
    return {
        "applied_update_count": applied,
        "ignored_older_update_count": ignored,
    }


def int_string_sort_key(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def build_delta(
    snapshot_date,
    previous_cutoff,
    current_cutoff,
    raw_records,
    deduped_updates,
    merge_summary,
    query_paths,
):
    return {
        "date": snapshot_date,
        "window_start_utc": f"{previous_cutoff}Z",
        "window_end_utc": f"{current_cutoff}Z",
        "source": "space-track.org",
        "api_class": "gp_history",
        "raw_row_count": len(raw_records),
        "deduped_update_count": len(deduped_updates),
        "new_object_count": len(merge_summary["new_norad_cat_ids"]),
        "updated_object_count": len(merge_summary["updated_norad_cat_ids"]),
        "unchanged_update_count": len(merge_summary["unchanged_update_norad_cat_ids"]),
        "ignored_older_update_count": len(
            merge_summary["ignored_older_update_norad_cat_ids"]
        ),
        "carried_forward_count": merge_summary["carried_forward_count"],
        "new_norad_cat_ids": merge_summary["new_norad_cat_ids"],
        "updated_norad_cat_ids": merge_summary["updated_norad_cat_ids"],
        "unchanged_update_norad_cat_ids": merge_summary["unchanged_update_norad_cat_ids"],
        "ignored_older_update_norad_cat_ids": merge_summary[
            "ignored_older_update_norad_cat_ids"
        ],
        "api_query_base": SPACETRACK_QUERY,
        "api_query_paths": query_paths,
    }


def load_snapshot(current_date_str):
    raw_bytes = read_catalog_bytes(current_date_str)
    return json.loads(raw_bytes)


def load_manifest(current_date_str):
    manifest_path = snapshot_dir(current_date_str) / "manifest.json"
    with open(manifest_path, encoding="utf-8") as f:
        return json.load(f)


def load_visibility_state(current_date_str):
    path = snapshot_dir(current_date_str) / "visibility_state.json"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    if isinstance(payload, dict) and isinstance(payload.get("missing_objects"), dict):
        return payload["missing_objects"]
    if isinstance(payload, dict) and isinstance(payload.get("objects"), dict):
        return {
            cat_id: entry
            for cat_id, entry in payload["objects"].items()
            if entry.get("currently_missing_from_current_gp")
        }
    return {}


def save_snapshot(
    current_date_str,
    canonical_bytes,
    data,
    provenance,
    query_strategy,
    query_paths,
    base_snapshot_date=None,
    base_snapshot_sha256=None,
    delta_window_start_utc=None,
    delta_window_end_utc=None,
    observed_at_utc=None,
    state_as_of_utc=None,
    annotations=None,
):
    day_dir = snapshot_dir(current_date_str)
    day_dir.mkdir(parents=True, exist_ok=True)

    sha256 = compute_hash(canonical_bytes)
    gz_path = day_dir / "catalog.json.gz"
    with open(gz_path, "wb") as raw_file:
        with gzip.GzipFile(
            filename="",
            mode="wb",
            fileobj=raw_file,
            compresslevel=9,
            mtime=0,
        ) as gz_file:
            gz_file.write(canonical_bytes)

    cutoff_utc = state_as_of_utc or f"{current_date_str}T{CUTOFF_TIME}Z"
    manifest = {
        "date": current_date_str,
        "cutoff_utc": cutoff_utc,
        "state_as_of_utc": cutoff_utc,
        "sha256": sha256,
        "content_schema": CONTENT_SCHEMA,
        "content_excluded_fields": list(CONTENT_EXCLUDED_FIELDS),
        "content_sha256": core_content_sha256(data),
        "object_count": len(data),
        "raw_bytes": len(canonical_bytes),
        "compressed_bytes": gz_path.stat().st_size,
        "provenance": provenance,
        "format": "OMM/JSON",
        "source": "space-track.org",
        "pipeline_version": PIPELINE_VERSION,
        "query_strategy": query_strategy,
        "api_query_base": SPACETRACK_QUERY,
        "api_query_paths": query_paths,
        "archived_at": now_utc().isoformat(),
    }

    optional = {
        "base_snapshot_date": base_snapshot_date,
        "base_snapshot_sha256": base_snapshot_sha256,
        "delta_window_start_utc": delta_window_start_utc,
        "delta_window_end_utc": delta_window_end_utc,
        "observed_at_utc": observed_at_utc,
        "state_as_of_utc": state_as_of_utc,
    }
    for key, value in optional.items():
        if value is not None:
            manifest[key] = value

    if annotations is not None:
        annotations_path = day_dir / "annotations.json"
        write_json(annotations_path, annotations)
        manifest["annotations_sha256"] = sha256_path(annotations_path)

    write_json(day_dir / "manifest.json", manifest)
    return manifest


def save_artifacts(current_date_str, delta=None, audit=None, visibility_state=None):
    day_dir = snapshot_dir(current_date_str)
    if delta is not None:
        write_json(day_dir / "delta.json", delta)
    if audit is not None:
        write_json(day_dir / "audit.json", audit)
    if visibility_state is not None:
        write_json(day_dir / "visibility_state.json", visibility_state)


def annotations_path(current_date_str):
    return snapshot_dir(current_date_str) / "annotations.json"


def load_annotations(current_date_str):
    return read_json_if_exists(annotations_path(current_date_str))


def cleanup_stale_artifacts(
    current_date_str, delta=None, audit=None, visibility_state=None, annotations=None
):
    """Remove optional day artifacts that are not produced by the current run."""
    day_dir = snapshot_dir(current_date_str)
    optional_artifacts = {
        "delta.json": delta,
        "audit.json": audit,
        "visibility_state.json": visibility_state,
        "annotations.json": annotations,
    }
    for filename, payload in optional_artifacts.items():
        path = day_dir / filename
        if payload is None and path.exists():
            path.unlink()


def ledger_entry_from_manifest(manifest):
    entry = {
        "date": manifest["date"],
        "sha256": manifest["sha256"],
        "object_count": manifest["object_count"],
        "compressed_bytes": manifest["compressed_bytes"],
        "provenance": manifest["provenance"],
        "query_strategy": manifest["query_strategy"],
        "archived_at": manifest["archived_at"],
    }
    for key in (
        "content_schema",
        "content_sha256",
        "annotations_sha256",
        "state_as_of_utc",
        "base_snapshot_date",
        "base_snapshot_sha256",
        "delta_window_start_utc",
        "delta_window_end_utc",
    ):
        if key in manifest:
            entry[key] = manifest[key]
    return entry


def update_ledger(manifest):
    ledger = []
    if LEDGER_PATH.exists():
        with open(LEDGER_PATH, encoding="utf-8") as f:
            try:
                loaded = json.load(f)
                if isinstance(loaded, list):
                    ledger = loaded
            except json.JSONDecodeError:
                ledger = []

    new_entry = ledger_entry_from_manifest(manifest)
    replaced = False
    for index, entry in enumerate(ledger):
        if entry.get("date") == manifest["date"]:
            if entry.get("sha256") != new_entry["sha256"]:
                new_entry["previous_sha256"] = entry.get("sha256")
                new_entry["regenerated_at"] = now_utc().isoformat()
            ledger[index] = new_entry
            replaced = True
            break

    if not replaced:
        ledger.append(new_entry)

    ledger.sort(key=lambda entry: entry.get("date", ""))
    write_json(LEDGER_PATH, ledger)


def archive_snapshot(
    current_date_str,
    data,
    provenance,
    query_strategy,
    query_paths,
    force=False,
    min_count=MIN_OBJECT_COUNT,
    base_snapshot_date=None,
    base_snapshot_sha256=None,
    delta_window_start_utc=None,
    delta_window_end_utc=None,
    observed_at_utc=None,
    state_as_of_utc=None,
    delta=None,
    audit=None,
    visibility_state=None,
    annotations=None,
):
    print(f"\n{'=' * 60}")
    print(f"  Date: {current_date_str}")
    print(f"  Cutoff: {CUTOFF_TIME} UTC")
    print(f"  Provenance: {provenance}")
    print(f"{'=' * 60}")

    manifest_path = snapshot_dir(current_date_str) / "manifest.json"
    if manifest_path.exists() and not force:
        print(f"  SKIP: Already archived ({manifest_path})")
        return None

    validate_gp_records(data, min_count=min_count, context=f"snapshot {current_date_str}")
    canonical = canonicalize(data)
    sha256 = compute_hash(canonical)

    print(f"  Objects: {len(data):,}")
    print(f"  Raw size: {len(canonical):,} bytes")
    print(f"  SHA-256: {sha256}")

    manifest = save_snapshot(
        current_date_str,
        canonical,
        data,
        provenance,
        query_strategy,
        query_paths,
        base_snapshot_date=base_snapshot_date,
        base_snapshot_sha256=base_snapshot_sha256,
        delta_window_start_utc=delta_window_start_utc,
        delta_window_end_utc=delta_window_end_utc,
        observed_at_utc=observed_at_utc,
        state_as_of_utc=state_as_of_utc,
        annotations=annotations,
    )
    print(f"  Content SHA-256 ({CONTENT_SCHEMA}): {manifest['content_sha256']}")
    cleanup_stale_artifacts(
        current_date_str,
        delta=delta,
        audit=audit,
        visibility_state=visibility_state,
        annotations=annotations,
    )
    save_artifacts(
        current_date_str,
        delta=delta,
        audit=audit,
        visibility_state=visibility_state,
    )
    update_ledger(manifest)

    print(f"  Compressed: {manifest['compressed_bytes']:,} bytes")
    print(f"  Saved to: {snapshot_dir(current_date_str)}")
    return manifest


def build_visibility_audit(
    snapshot_date,
    archived_records,
    current_gp_records,
    observed_at_utc,
    query_paths,
    previous_visibility=None,
):
    previous_visibility = previous_visibility or {}
    archived = records_by_cat_id(archived_records)
    current = records_by_cat_id(current_gp_records)
    archived_ids = set(archived)
    current_ids = set(current)

    missing_ids = sorted(archived_ids - current_ids, key=int_string_sort_key)
    reappeared_ids = []
    missing_state = {}

    for cat_id in sorted(archived_ids, key=int_string_sort_key):
        record = archived[cat_id]
        previous = previous_visibility.get(cat_id, {})
        was_missing = bool(previous)
        present = cat_id in current_ids

        if present:
            if was_missing:
                reappeared_ids.append(cat_id)
        else:
            if was_missing:
                first_missing = previous.get("first_missing_in_current_gp_audit", observed_at_utc)
                consecutive = int(previous.get("consecutive_missing_audits", 0)) + 1
                last_seen = previous.get("last_seen_in_current_gp_audit")
            else:
                first_missing = observed_at_utc
                consecutive = 1
                last_seen = None
            missing_state[cat_id] = {
                "norad_cat_id": cat_id,
                "object_name": record.get("OBJECT_NAME"),
                "last_gp_creation_date": creation_time(record),
                "last_seen_in_current_gp_audit": last_seen,
                "first_missing_in_current_gp_audit": first_missing,
                "consecutive_missing_audits": consecutive,
            }

    missing_records = [
        {
            "norad_cat_id": cat_id,
            "object_name": archived[cat_id].get("OBJECT_NAME"),
            "last_gp_creation_date": creation_time(archived[cat_id]),
            "first_missing_in_current_gp_audit": missing_state[cat_id][
                "first_missing_in_current_gp_audit"
            ],
            "consecutive_missing_audits": missing_state[cat_id][
                "consecutive_missing_audits"
            ],
        }
        for cat_id in missing_ids
    ]
    reappeared_records = [
        {
            "norad_cat_id": cat_id,
            "object_name": archived[cat_id].get("OBJECT_NAME"),
            "reappeared_at_utc": observed_at_utc,
            "previously_missing_since": previous_visibility.get(cat_id, {}).get(
                "first_missing_in_current_gp_audit"
            ),
        }
        for cat_id in reappeared_ids
    ]

    current_id_bytes = "\n".join(sorted(current_ids, key=int_string_sort_key)).encode("utf-8")
    audit = {
        "date": snapshot_date,
        "observed_at_utc": observed_at_utc,
        "source": "space-track.org",
        "api_class": "gp",
        "api_query_base": SPACETRACK_QUERY,
        "api_query_paths": query_paths,
        "archive_object_count": len(archived_records),
        "current_gp_object_count": len(current_gp_records),
        "present_ids_sha256": compute_hash(current_id_bytes),
        "missing_from_current_gp_count": len(missing_records),
        "present_in_current_gp_not_in_archive_count": len(current_ids - archived_ids),
        "reappeared_in_current_gp_count": len(reappeared_records),
        "missing_from_current_gp": missing_records,
        "present_in_current_gp_not_in_archive": sorted(
            current_ids - archived_ids,
            key=int_string_sort_key,
        ),
        "reappeared_in_current_gp": reappeared_records,
    }
    visibility_state = {
        "date": snapshot_date,
        "observed_at_utc": observed_at_utc,
        "missing_objects": missing_state,
    }
    return audit, visibility_state


def build_snapshot_from_base(
    client,
    current_date_str,
    base_records,
    base_state_as_of_utc,
    max_catalog_id=MAX_NORAD_CAT_ID,
    range_size=CATALOG_RANGE_SIZE,
):
    previous_cutoff = normalize_utc_for_filter(base_state_as_of_utc)
    current_cutoff = get_cutoff_for_date(current_date_str)
    if previous_cutoff >= current_cutoff:
        raise SnapshotError(
            f"Base state {base_state_as_of_utc} is not before snapshot cutoff {current_cutoff}Z"
        )
    updates, raw_records, query_paths = pull_updates_between_cutoffs(
        client,
        previous_cutoff,
        current_cutoff,
        max_catalog_id=max_catalog_id,
        range_size=range_size,
    )
    data, merge_summary = apply_updates(base_records, updates)
    delta = build_delta(
        current_date_str,
        previous_cutoff,
        current_cutoff,
        raw_records,
        updates,
        merge_summary,
        query_paths,
    )
    return data, delta, query_paths


def process_genesis(args, client):
    current_date_str = args.date or now_utc().strftime("%Y-%m-%d")
    observed_at_utc = utc_stamp()
    data, query_paths = query_current_gp(client, min_count=args.min_objects)
    audit, visibility_state = build_visibility_audit(
        current_date_str,
        data,
        data,
        observed_at_utc,
        query_paths,
    )
    # The genesis catalog itself is the observation baseline: every mutable
    # field value it carries is a first observation, recorded in the raw bytes.
    annotations = build_annotations(
        current_date_str,
        data,
        None,
        observed_at_utc=observed_at_utc,
        baseline=True,
    )
    manifest = archive_snapshot(
        current_date_str,
        data,
        "genesis_from_gp",
        "current_gp_genesis",
        query_paths,
        force=args.force,
        min_count=args.min_objects,
        observed_at_utc=observed_at_utc,
        state_as_of_utc=observed_at_utc,
        audit=audit,
        visibility_state=visibility_state,
        annotations=annotations,
    )
    if manifest:
        print(f"\n  DONE. Hash: {manifest['sha256']}")


def process_daily(args, client):
    current_date_str = args.date or now_utc().strftime("%Y-%m-%d")
    manifest_path = snapshot_dir(current_date_str) / "manifest.json"
    if manifest_path.exists() and not args.force:
        print(f"  SKIP: Already archived ({manifest_path})")
        return

    now = now_utc()
    operator_hour, operator_minute, operator_second = [
        int(part) for part in OPERATOR_RUN_TIME.split(":")
    ]
    run_time = parse_date(current_date_str).replace(
        hour=operator_hour,
        minute=operator_minute,
        second=operator_second,
        tzinfo=timezone.utc,
    )
    if now < run_time and args.date is None:
        print(
            f"WARNING: Current time {now.strftime('%H:%M:%S')} UTC is before "
            f"operator run time {OPERATOR_RUN_TIME} UTC."
        )
        print("The UTC day is closed, but Space-Track may still be settling.")

    previous = previous_date_str(current_date_str)
    previous_manifest_path = snapshot_dir(previous) / "manifest.json"
    if not previous_manifest_path.exists():
        raise SnapshotError(
            f"Missing prior snapshot {previous}. Run genesis first or roll forward from an existing base."
        )

    base_records = load_snapshot(previous)
    base_manifest = load_manifest(previous)
    validate_gp_records(base_records, min_count=args.min_objects, context=f"snapshot {previous}")

    base_state_as_of_utc = base_manifest.get("state_as_of_utc", base_manifest["cutoff_utc"])
    data, delta, query_paths = build_snapshot_from_base(
        client,
        current_date_str,
        base_records,
        base_state_as_of_utc,
        max_catalog_id=args.max_catalog_id,
        range_size=args.range_size,
    )
    observed_at_utc = utc_stamp()
    satcat_rows, decay_rows, annotation_paths = query_annotation_observations(
        client,
        normalize_utc_for_filter(delta["window_start_utc"]),
        normalize_utc_for_filter(delta["window_end_utc"]),
    )
    annotations = build_annotations(
        current_date_str,
        data,
        base_records,
        observed_at_utc=observed_at_utc,
        window_start_utc=delta["window_start_utc"],
        window_end_utc=delta["window_end_utc"],
        satcat_changes=satcat_rows,
        decay_messages=decay_rows,
        query_paths=annotation_paths,
    )
    audit = None
    visibility_state = None
    if not args.no_audit:
        current_gp, gp_query_paths = query_current_gp(client, min_count=args.min_objects)
        previous_visibility = load_visibility_state(previous)
        audit, visibility_state = build_visibility_audit(
            current_date_str,
            data,
            current_gp,
            observed_at_utc,
            gp_query_paths,
            previous_visibility=previous_visibility,
        )

    manifest = archive_snapshot(
        current_date_str,
        data,
        "rolling_gp_history_delta",
        "prior_snapshot_plus_bounded_gp_history_delta",
        query_paths,
        force=args.force,
        min_count=args.min_objects,
        base_snapshot_date=previous,
        base_snapshot_sha256=base_manifest["sha256"],
        delta_window_start_utc=delta["window_start_utc"],
        delta_window_end_utc=delta["window_end_utc"],
        observed_at_utc=observed_at_utc if audit else None,
        delta=delta,
        audit=audit,
        visibility_state=visibility_state,
        annotations=annotations,
    )
    if manifest:
        print(f"\n  DONE. Hash: {manifest['sha256']}")


def process_roll_forward(args, client):
    start = parse_date(args.start)
    end = parse_date(args.end)
    if end < start:
        raise SnapshotError("--end must be on or after --start")

    total_days = (end - start).days + 1
    print(f"\nRolling forward {total_days} days: {args.start} to {args.end}")
    print("Mode: prior snapshot plus bounded gp_history deltas")

    previous = previous_date_str(args.start)
    previous_manifest_path = snapshot_dir(previous) / "manifest.json"
    if not previous_manifest_path.exists():
        raise SnapshotError(
            f"Missing base snapshot {previous}. Create a genesis snapshot or start at the day after one."
        )

    state_records = load_snapshot(previous)
    base_manifest = load_manifest(previous)
    validate_gp_records(state_records, min_count=args.min_objects, context=f"snapshot {previous}")

    archived = 0
    skipped = 0
    current = start
    last_manifest = base_manifest

    while current <= end:
        current_date_str = date_str(current)
        manifest_path = snapshot_dir(current_date_str) / "manifest.json"

        if manifest_path.exists() and not args.force:
            state_records = load_snapshot(current_date_str)
            validate_gp_records(
                state_records,
                min_count=args.min_objects,
                context=f"snapshot {current_date_str}",
            )
            last_manifest = load_manifest(current_date_str)
            print(f"\n  SKIP: Already archived ({manifest_path})")
            skipped += 1
            current += timedelta(days=1)
            continue

        base_state_as_of_utc = last_manifest.get("state_as_of_utc", last_manifest["cutoff_utc"])
        previous_records = state_records
        data, delta, query_paths = build_snapshot_from_base(
            client,
            current_date_str,
            state_records,
            base_state_as_of_utc,
            max_catalog_id=args.max_catalog_id,
            range_size=args.range_size,
        )
        observed_at_utc = utc_stamp()
        satcat_rows, decay_rows, annotation_paths = query_annotation_observations(
            client,
            normalize_utc_for_filter(delta["window_start_utc"]),
            normalize_utc_for_filter(delta["window_end_utc"]),
        )
        annotations = build_annotations(
            current_date_str,
            data,
            previous_records,
            observed_at_utc=observed_at_utc,
            window_start_utc=delta["window_start_utc"],
            window_end_utc=delta["window_end_utc"],
            satcat_changes=satcat_rows,
            decay_messages=decay_rows,
            query_paths=annotation_paths,
        )
        manifest = archive_snapshot(
            current_date_str,
            data,
            "rolling_gp_history_delta",
            "prior_snapshot_plus_bounded_gp_history_delta",
            query_paths,
            force=args.force,
            min_count=args.min_objects,
            base_snapshot_date=previous_date_str(current_date_str),
            base_snapshot_sha256=last_manifest["sha256"],
            delta_window_start_utc=delta["window_start_utc"],
            delta_window_end_utc=delta["window_end_utc"],
            delta=delta,
            annotations=annotations,
        )
        if manifest:
            archived += 1
            last_manifest = manifest
        state_records = data
        current += timedelta(days=1)

    print(f"\nRoll-forward complete: {archived} days archived, {skipped} skipped")


def process_rebuild_content(args):
    """Add consensus/observation artifacts to already-archived days.

    Offline (no Space-Track access): for each day the recorded raw catalog is
    loaded (local gz or release bundle), its sha256 is verified against the
    committed manifest -- the integrity gate that proves the rebuild describes
    exactly the bytes we archived -- and then the manifest gains the v2 content
    fields while annotations.json is derived from consecutive recorded
    catalogs with observed_at set to each day's original archived_at. Nothing
    in the raw record of "what we knew, when" is altered.
    """
    days = []
    current = parse_date(args.start)
    end = parse_date(args.end)
    if end < current:
        raise SnapshotError("--end must be on or after --start")
    while current <= end:
        days.append(date_str(current))
        current += timedelta(days=1)

    print(f"\nRebuilding content fields for {len(days)} days: {args.start} to {args.end}")
    previous_records = None
    previous_date_value = previous_date_str(args.start)
    previous_manifest = read_json_if_exists(snapshot_dir(previous_date_value) / "manifest.json")
    if previous_manifest is not None:
        previous_records = json.loads(read_catalog_bytes(previous_date_value))

    rebuilt = 0
    skipped = 0
    for day in days:
        manifest_path = snapshot_dir(day) / "manifest.json"
        manifest = read_json_if_exists(manifest_path)
        if manifest is None:
            raise SnapshotError(f"{day}: no committed manifest; cannot rebuild an unarchived day")

        if (
            manifest.get("content_schema") == CONTENT_SCHEMA
            and "annotations_sha256" in manifest
            and (snapshot_dir(day) / "annotations.json").exists()
            and not getattr(args, "force", False)
        ):
            # Idempotent: hydrated or previously rebuilt days keep their exact
            # artifacts (re-deriving annotations would change rebuilt_at and
            # break byte-identity with the published bundle).
            print(f"  {day}: already carries {CONTENT_SCHEMA} fields; skipping")
            skipped += 1
            previous_records = json.loads(read_catalog_bytes(day))
            continue

        records = json.loads(read_catalog_bytes(day))
        canonical = canonicalize(records)
        computed = compute_hash(canonical)
        if computed != manifest["sha256"]:
            raise SnapshotError(
                f"{day}: catalog bytes hash {computed} != recorded manifest sha256 "
                f"{manifest['sha256']}; refusing to rebuild from unverified bytes"
            )

        observed_at = str(manifest.get("observed_at_utc") or manifest.get("archived_at"))
        baseline = previous_records is None
        annotations = build_annotations(
            day,
            records,
            previous_records,
            observed_at_utc=observed_at,
            window_start_utc=manifest.get("delta_window_start_utc"),
            window_end_utc=manifest.get("delta_window_end_utc"),
            baseline=baseline,
        )
        annotations["rebuilt"] = True
        annotations["rebuilt_at"] = utc_stamp()

        day_dir = snapshot_dir(day)
        annotations_file = day_dir / "annotations.json"
        write_json(annotations_file, annotations)

        manifest["content_schema"] = CONTENT_SCHEMA
        manifest["content_excluded_fields"] = list(CONTENT_EXCLUDED_FIELDS)
        manifest["content_sha256"] = core_content_sha256(records)
        manifest["annotations_sha256"] = sha256_path(annotations_file)
        write_json(manifest_path, manifest)
        update_ledger(manifest)

        rebuilt += 1
        print(
            f"  {day}: raw={manifest['sha256'][:12]} core={manifest['content_sha256'][:12]} "
            f"changes={len(annotations['catalog_changes'])}"
            f"{' (baseline)' if baseline else ''}"
        )
        previous_records = records

    print(
        f"\nRebuild complete: {rebuilt} days now carry {CONTENT_SCHEMA} content fields"
        + (f"; {skipped} already current" if skipped else "")
    )


def compare_record_sets(replay_records, current_gp_records, sample_size=25):
    replay = records_by_cat_id(replay_records)
    current = records_by_cat_id(current_gp_records)
    replay_ids = set(replay)
    current_ids = set(current)
    shared_ids = replay_ids & current_ids

    missing_from_replay = sorted(current_ids - replay_ids, key=int_string_sort_key)
    missing_from_current = sorted(replay_ids - current_ids, key=int_string_sort_key)
    mismatched = []
    matched = 0
    for cat_id in sorted(shared_ids, key=int_string_sort_key):
        if record_hash(replay[cat_id]) == record_hash(current[cat_id]):
            matched += 1
        else:
            mismatched.append(cat_id)

    return {
        "replay_object_count": len(replay_records),
        "current_gp_object_count": len(current_gp_records),
        "shared_object_count": len(shared_ids),
        "matched_record_count": matched,
        "mismatched_record_count": len(mismatched),
        "missing_from_replay_count": len(missing_from_replay),
        "missing_from_current_gp_count": len(missing_from_current),
        "missing_from_replay_sample": missing_from_replay[:sample_size],
        "missing_from_current_gp_sample": missing_from_current[:sample_size],
        "mismatched_record_sample": mismatched[:sample_size],
    }


def compact_record_summary(record):
    return {
        "norad_cat_id": record.get("NORAD_CAT_ID"),
        "object_name": record.get("OBJECT_NAME"),
        "epoch": record.get("EPOCH"),
        "creation_date": record.get("CREATION_DATE"),
        "gp_id": record.get("GP_ID"),
    }


def mismatch_sample_details(replay_records, current_gp_records, cat_ids):
    replay = records_by_cat_id(replay_records)
    current = records_by_cat_id(current_gp_records)
    details = []
    for cat_id in cat_ids:
        replay_record = replay[cat_id]
        current_record = current[cat_id]
        differing_fields = sorted(
            field
            for field in set(replay_record) | set(current_record)
            if replay_record.get(field) != current_record.get(field)
        )
        details.append(
            {
                "norad_cat_id": cat_id,
                "replay": compact_record_summary(replay_record),
                "current_gp": compact_record_summary(current_record),
                "differing_fields": differing_fields[:25],
                "differing_field_count": len(differing_fields),
            }
        )
    return details


def process_replay(args, client):
    start_cutoff = f"{args.start}T{CUTOFF_TIME}"

    print("\nCapturing current gp reference")
    current_gp, current_gp_query_paths = query_current_gp(client, min_count=args.min_objects)
    observed_at_utc = utc_stamp()
    observed_at_filter = observed_at_utc[:-1]
    current_gp_by_id = records_by_cat_id(current_gp)

    print(f"\nReplaying bounded gp_history from {start_cutoff} to {observed_at_filter}")
    state = {}
    windows = []
    current_start = parse_date(args.start).replace(tzinfo=timezone.utc)
    final_end = datetime.strptime(observed_at_filter, "%Y-%m-%dT%H:%M:%S").replace(
        tzinfo=timezone.utc
    )

    while current_start < final_end:
        current_end = min(current_start + timedelta(days=1), final_end)
        window_start = current_start.strftime("%Y-%m-%dT%H:%M:%S")
        window_end = current_end.strftime("%Y-%m-%dT%H:%M:%S")
        updates, raw_records, query_paths = pull_updates_between_cutoffs(
            client,
            window_start,
            window_end,
            max_catalog_id=args.max_catalog_id,
            range_size=args.range_size,
        )
        before_count = len(state)
        merge_summary = apply_updates_to_state(state, updates)
        windows.append(
            {
                "window_start_utc": f"{window_start}Z",
                "window_end_utc": f"{window_end}Z",
                "raw_row_count": len(raw_records),
                "deduped_update_count": len(updates),
                "applied_update_count": merge_summary["applied_update_count"],
                "ignored_older_update_count": merge_summary["ignored_older_update_count"],
                "object_count_before": before_count,
                "object_count_after": len(state),
                "api_query_paths": query_paths,
            }
        )
        print(
            "  Window "
            f"{window_start}Z to {window_end}Z: "
            f"{len(raw_records):,} rows, {len(updates):,} objects, "
            f"state {before_count:,}->{len(state):,}"
        )
        current_start = current_end

    replay_records = sorted_records_from_state(state)
    comparison = compare_record_sets(replay_records, current_gp)
    first_seen_missing = []
    for cat_id in comparison["missing_from_replay_sample"]:
        first_seen_missing.append(
            {
                "norad_cat_id": cat_id,
                "current_gp_creation_date": creation_time(current_gp_by_id[cat_id]),
                "object_name": current_gp_by_id[cat_id].get("OBJECT_NAME"),
            }
        )

    report = {
        "generated_at_utc": utc_stamp(),
        "mode": "delta_replay_from_empty_state_compared_to_current_gp",
        "start_cutoff_utc": f"{start_cutoff}Z",
        "end_observed_at_utc": observed_at_utc,
        "source": "space-track.org",
        "current_gp_query_paths": current_gp_query_paths,
        "comparison": comparison,
        "missing_from_replay_sample_details": first_seen_missing,
        "mismatched_record_sample_details": mismatch_sample_details(
            replay_records,
            current_gp,
            comparison["mismatched_record_sample"],
        ),
        "window_count": len(windows),
        "windows": windows,
    }

    report_path = args.report_path
    if report_path is None:
        safe_stamp = observed_at_utc.replace(":", "").replace("-", "")
        report_path = report_dir() / f"replay_{args.start}_{safe_stamp}.json"
    else:
        report_path = Path(report_path)
    write_json(report_path, report)

    print("\nReplay comparison")
    for key, value in comparison.items():
        if not key.endswith("_sample"):
            print(f"  {key}: {value:,}" if isinstance(value, int) else f"  {key}: {value}")
    print(f"  Report: {report_path}")


def verify_date(current_date_str):
    day_dir = snapshot_dir(current_date_str)
    manifest_path = day_dir / "manifest.json"

    if not manifest_path.exists():
        print(f"No manifest found for {current_date_str}")
        sys.exit(1)

    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)

    raw_bytes = read_catalog_bytes(current_date_str)

    computed_hash = hashlib.sha256(raw_bytes).hexdigest()
    stored_hash = manifest["sha256"]

    print(f"  Date:     {current_date_str}")
    print(f"  Stored:   {stored_hash}")
    print(f"  Computed: {computed_hash}")

    if computed_hash == stored_hash:
        print("  Status:   VERIFIED")
    else:
        print("  Status:   MISMATCH")
        sys.exit(1)


def sha256_path(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_limited(stream, limit, *, label="payload"):
    if limit < 1:
        raise SnapshotError(f"{label} size limit must be positive")
    chunks = []
    total = 0
    while True:
        chunk = stream.read(min(1024 * 1024, limit - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise SnapshotError(f"{label} exceeds size limit")
        chunks.append(chunk)
    return b"".join(chunks)


def gzip_decompress_limited(payload, limit, *, label="gzip payload"):
    with gzip.GzipFile(fileobj=io.BytesIO(payload), mode="rb") as stream:
        return read_limited(stream, limit, label=label)


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def strip_authorization_headers(headers):
    return {key: value for key, value in headers.items() if str(key).lower() != "authorization"}


def fetch_url_bytes_with_redirects(url, *, timeout, max_bytes, headers, label, validate_url):
    opener = urllib.request.build_opener(NoRedirectHandler)
    current = url
    origin_host = (urllib.parse.urlparse(url).hostname or "").lower()
    for _redirect in range(4):
        validate_url(current)
        current_host = (urllib.parse.urlparse(current).hostname or "").lower()
        request_headers = dict(headers)
        if current_host != origin_host:
            request_headers = strip_authorization_headers(request_headers)
        request = urllib.request.Request(current, headers=request_headers)
        try:
            with opener.open(request, timeout=timeout) as response:
                return read_limited(response, max_bytes, label=label)
        except urllib.error.HTTPError as exc:
            if exc.code not in (301, 302, 303, 307, 308):
                raise
            try:
                location = exc.headers.get("location")
                if not location:
                    raise SnapshotError(f"{label} redirect response is missing Location") from exc
                current = urllib.parse.urljoin(current, location)
            finally:
                exc.close()
    raise SnapshotError(f"{label} redirects too many times")


def release_tag(current_date_str):
    parse_date(current_date_str)
    return f"rso-archive-{current_date_str}"


def release_asset_name(current_date_str):
    parse_date(current_date_str)
    return f"rso-archive-{current_date_str}.tar.gz"


def release_title(current_date_str):
    parse_date(current_date_str)
    return f"RSO Archive {current_date_str}"


def github_token():
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")


def parse_github_remote_url(url):
    url = url.strip()
    if url.endswith(".git"):
        url = url[:-4]
    if url.startswith("git@github.com:"):
        return url.split(":", 1)[1]
    marker = "github.com/"
    if marker in url:
        return url.split(marker, 1)[1]
    return None


def github_repo_from_git_config():
    config_path = Path(__file__).parent.parent / ".git" / "config"
    if not config_path.exists():
        return None

    current_section = None
    with open(config_path, encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if line.startswith("[") and line.endswith("]"):
                current_section = line[1:-1]
                continue
            if current_section != 'remote "origin"' or not line.startswith("url ="):
                continue
            return parse_github_remote_url(line.split("=", 1)[1])
    return None


def resolve_github_repo(repo=None):
    resolved = repo or os.environ.get("GITHUB_REPOSITORY") or github_repo_from_git_config()
    if not resolved:
        raise SnapshotError(
            "GitHub repository is required. Pass --repo OWNER/REPO or set GITHUB_REPOSITORY."
        )
    if "/" not in resolved:
        raise SnapshotError(f"Invalid GitHub repository: {resolved}")
    return resolved


def github_api_url(repo, path):
    return f"https://api.github.com/repos/{repo}{path}"


def github_request(
    method,
    url,
    payload=None,
    headers=None,
    token_required=False,
    allow_not_found=False,
):
    token = github_token()
    if token_required and not token:
        raise SnapshotError("Set GH_TOKEN or GITHUB_TOKEN to publish GitHub releases")

    request_headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"rso-archive/{PIPELINE_VERSION}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        request_headers["Authorization"] = f"Bearer {token}"
    if headers:
        request_headers.update(headers)

    data = None
    if payload is not None:
        if isinstance(payload, (bytes, bytearray)):
            data = bytes(payload)
        else:
            data = json.dumps(payload).encode("utf-8")
            request_headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            body = read_limited(resp, MAX_JSON_BYTES, label="GitHub API response")
            content_type = resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as exc:
        if allow_not_found and exc.code == 404:
            return None
        detail = read_limited(exc, MAX_JSON_BYTES, label="GitHub API error response").decode(
            "utf-8", errors="replace"
        )
        raise SnapshotError(f"GitHub API {method} {url} failed ({exc.code}): {detail}") from exc

    if not body:
        return None
    if "application/json" in content_type or body[:1] in (b"{", b"["):
        return json.loads(body)
    return body


def github_release_payload(tag, repo=None, allow_missing=False):
    resolved_repo = resolve_github_repo(repo)
    return github_request(
        "GET",
        github_api_url(resolved_repo, f"/releases/tags/{tag}"),
        allow_not_found=allow_missing,
    )


def find_release_asset(release, asset_name):
    if not release:
        return None
    for asset in release.get("assets", []):
        if asset.get("name") == asset_name:
            return asset
    return None


def github_download_bytes(url):
    token = github_token()
    headers = {"User-Agent": f"rso-archive/{PIPELINE_VERSION}"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return fetch_url_bytes_with_redirects(
        url,
        timeout=180,
        max_bytes=MAX_RELEASE_BUNDLE_BYTES,
        headers=headers,
        label="GitHub release asset",
        validate_url=validate_github_download_url,
    )


def validate_github_download_url(url):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise SnapshotError("GitHub release asset URL must use HTTPS")
    host = (parsed.hostname or "").lower()
    if not github_download_host_allowed(host):
        raise SnapshotError(f"GitHub release asset host is not allowed: {host}")
    reject_private_host(host, context="GitHub release asset")


def github_download_host_allowed(host):
    allowed = ("github.com", "api.github.com", "uploads.github.com", "objects.githubusercontent.com")
    if host in allowed:
        return True
    return host.endswith(".githubusercontent.com")


def release_bundle_from_github(current_date_str, repo=None):
    release = github_release_payload(release_tag(current_date_str), repo=repo)
    asset = find_release_asset(release, release_asset_name(current_date_str))
    if asset is None:
        raise SnapshotError(
            f"{release_tag(current_date_str)} has no {release_asset_name(current_date_str)} asset"
        )
    return github_download_bytes(asset["browser_download_url"])


def catalog_gz_bytes_from_release_bundle(current_date_str, repo=None):
    bundle_bytes = release_bundle_from_github(current_date_str, repo=repo)
    with tarfile.open(fileobj=io.BytesIO(bundle_bytes), mode="r:gz") as tar:
        member = tar.extractfile("catalog.json.gz")
        if member is None:
            raise SnapshotError(f"{release_asset_name(current_date_str)} missing catalog.json.gz")
        return read_limited(member, MAX_CATALOG_BYTES, label="catalog.json.gz")


def download_release_bundle_to_file(current_date_str, output_dir=None, repo=None):
    output_dir = Path(output_dir or RELEASE_OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = output_dir / release_asset_name(current_date_str)
    bundle_path.write_bytes(release_bundle_from_github(current_date_str, repo=repo))
    return release_bundle_from_existing(current_date_str, output_dir=output_dir)


def catalog_bytes_from_release_bundle(current_date_str, repo=None):
    return gzip_decompress_limited(
        catalog_gz_bytes_from_release_bundle(current_date_str, repo=repo),
        MAX_CATALOG_BYTES,
        label="catalog.json",
    )


def read_catalog_bytes(current_date_str, repo=None):
    gz_path = catalog_gz_path(current_date_str)
    if gz_path.exists():
        with gzip.open(gz_path, "rb") as f:
            return read_limited(f, MAX_CATALOG_BYTES, label=str(gz_path))
    return catalog_bytes_from_release_bundle(current_date_str, repo=repo)


def date_range(start_date_str, end_date_str):
    start = parse_date(start_date_str)
    end = parse_date(end_date_str)
    if end < start:
        raise SnapshotError("--end must be on or after --start")

    current = start
    while current <= end:
        yield date_str(current)
        current += timedelta(days=1)


def existing_release_artifact_paths(current_date_str):
    day_dir = snapshot_dir(current_date_str)
    paths = []
    for filename in RELEASE_ARTIFACT_FILENAMES:
        path = day_dir / filename
        if path.exists():
            paths.append(path)
    return paths


def release_manifest_payload(current_date_str, manifest, artifact_paths):
    files = []
    for path in artifact_paths:
        files.append(
            {
                "path": path.name,
                "bytes": path.stat().st_size,
                "sha256": sha256_path(path),
            }
        )

    return {
        "date": current_date_str,
        "bundle_format": "tar.gz",
        "bundle_schema": 1,
        "catalog_sha256": manifest["sha256"],
        "manifest_sha256": sha256_path(snapshot_dir(current_date_str) / "manifest.json"),
        "object_count": manifest["object_count"],
        "pipeline_version": PIPELINE_VERSION,
        "state_as_of_utc": manifest.get("state_as_of_utc"),
        "files": files,
    }


def add_tar_bytes(tar, arcname, data):
    info = tarfile.TarInfo(arcname)
    info.size = len(data)
    info.mtime = 0
    info.mode = 0o644
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    tar.addfile(info, io.BytesIO(data))


def build_release_bundle(
    current_date_str, output_dir=None, min_count=MIN_OBJECT_COUNT, asset_name=None
):
    parse_date(current_date_str)
    errors, manifest = validate_snapshot_artifacts(
        current_date_str,
        min_count=min_count,
        require_audit=False,
        require_catalog=True,
    )
    if errors:
        raise SnapshotError("\n".join(errors))
    if manifest is None:
        raise SnapshotError(f"{current_date_str}: missing manifest.json")

    artifact_paths = existing_release_artifact_paths(current_date_str)
    required = {"catalog.json.gz", "manifest.json"}
    existing = {path.name for path in artifact_paths}
    missing = sorted(required - existing)
    if missing:
        raise SnapshotError(f"{current_date_str}: missing release artifacts: {', '.join(missing)}")

    output_dir = Path(output_dir or RELEASE_OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = output_dir / (asset_name or release_asset_name(current_date_str))

    bundle_manifest = release_manifest_payload(current_date_str, manifest, artifact_paths)
    bundle_manifest_bytes = canonicalize(bundle_manifest) + b"\n"

    with open(bundle_path, "wb") as raw_file:
        with gzip.GzipFile(
            filename="",
            mode="wb",
            fileobj=raw_file,
            compresslevel=9,
            mtime=0,
        ) as gz_file:
            with tarfile.open(fileobj=gz_file, mode="w") as tar:
                for path in sorted(artifact_paths, key=lambda item: item.name):
                    add_tar_bytes(tar, path.name, path.read_bytes())
                add_tar_bytes(tar, "release-manifest.json", bundle_manifest_bytes)

    bundle_sha256 = sha256_path(bundle_path)
    return {
        "date": current_date_str,
        "path": str(bundle_path),
        "asset_name": bundle_path.name,
        "tag": release_tag(current_date_str),
        "title": release_title(current_date_str),
        "bundle_sha256": bundle_sha256,
        "bytes": bundle_path.stat().st_size,
        "catalog_sha256": manifest["sha256"],
        "manifest_sha256": bundle_manifest["manifest_sha256"],
        "object_count": manifest["object_count"],
        "state_as_of_utc": manifest.get("state_as_of_utc"),
        "files": bundle_manifest["files"],
    }


def release_bundle_from_existing(current_date_str, output_dir=None):
    parse_date(current_date_str)
    output_dir = Path(output_dir or RELEASE_OUTPUT_DIR)
    bundle_path = output_dir / release_asset_name(current_date_str)
    if not bundle_path.exists():
        raise SnapshotError(f"{current_date_str}: missing existing bundle {bundle_path}")
    if bundle_path.stat().st_size > MAX_RELEASE_BUNDLE_BYTES:
        raise SnapshotError(f"{current_date_str}: existing bundle exceeds size limit")

    manifest = load_manifest(current_date_str)
    with tarfile.open(bundle_path, mode="r:gz") as tar:
        member = tar.extractfile("release-manifest.json")
        if member is None:
            raise SnapshotError(f"{bundle_path}: missing release-manifest.json")
        bundle_manifest = json.loads(
            read_limited(member, MAX_JSON_BYTES, label="release-manifest.json").decode("utf-8")
        )
        if not isinstance(bundle_manifest, dict):
            raise SnapshotError(f"{bundle_path}: release-manifest.json must be a JSON object")
        catalog_member = tar.extractfile("catalog.json.gz")
        if catalog_member is None:
            raise SnapshotError(f"{bundle_path}: missing catalog.json.gz")
        catalog_gz = read_limited(catalog_member, MAX_CATALOG_BYTES, label="catalog.json.gz")

    if bundle_manifest.get("catalog_sha256") != manifest.get("sha256"):
        raise SnapshotError(
            f"{current_date_str}: existing bundle catalog hash does not match manifest"
        )
    if bundle_manifest.get("object_count") != manifest.get("object_count"):
        raise SnapshotError(
            f"{current_date_str}: existing bundle object count does not match manifest"
        )
    catalog_bytes = gzip_decompress_limited(catalog_gz, MAX_CATALOG_BYTES, label="catalog.json")
    if compute_hash(catalog_bytes) != manifest.get("sha256"):
        raise SnapshotError(
            f"{current_date_str}: existing bundle catalog bytes do not match manifest"
        )

    return {
        "date": current_date_str,
        "path": str(bundle_path),
        "asset_name": bundle_path.name,
        "tag": release_tag(current_date_str),
        "title": release_title(current_date_str),
        "bundle_sha256": sha256_path(bundle_path),
        "bytes": bundle_path.stat().st_size,
        "catalog_sha256": manifest["sha256"],
        "manifest_sha256": bundle_manifest["manifest_sha256"],
        "object_count": manifest["object_count"],
        "state_as_of_utc": manifest.get("state_as_of_utc"),
        "files": bundle_manifest["files"],
    }


def build_rebuilt_bundle(current_date_str, output_dir=None, min_count=MIN_OBJECT_COUNT):
    """Rebuild a day's bundle locally for re-publication.

    Requires the day to carry content fields and annotations, refuses days
    adopted from an upstream node (they are already published there; attest
    the shared locations instead), and rematerializes a pruned catalog from
    the published bundle, verified against the committed manifest.
    """
    manifest = load_manifest(current_date_str)
    if manifest.get("content_schema") != CONTENT_SCHEMA:
        raise SnapshotError(
            f"{current_date_str}: manifest lacks {CONTENT_SCHEMA} fields; run rebuild-content first"
        )
    if "annotations_sha256" not in manifest:
        raise SnapshotError(
            f"{current_date_str}: manifest has no annotations_sha256; run rebuild-content first"
        )

    receipt_path = storage_receipt_path(current_date_str)
    if receipt_path.exists():
        receipt = load_storage_receipt(current_date_str)
        if receipt.get("verified_from_upstream"):
            raise SnapshotError(
                f"{current_date_str}: day was adopted from "
                f"{receipt['verified_from_upstream']}; it is already published there "
                "(attest the shared locations instead of re-publishing)"
            )

    ensure_local_catalog(current_date_str, manifest)

    return build_release_bundle(
        current_date_str,
        output_dir=output_dir,
        min_count=min_count,
    )


def ensure_local_catalog(current_date_str, manifest):
    """Materialize a pruned day's catalog.json.gz from its published bundle.

    Bundle building needs the catalog on disk; retention pruning removes old
    copies. The re-fetched bytes are verified against the committed manifest
    before being written back.
    """
    gz_path = catalog_gz_path(current_date_str)
    if gz_path.exists():
        return
    raw = read_catalog_bytes(current_date_str)
    computed = compute_hash(raw)
    if computed != manifest["sha256"]:
        raise SnapshotError(
            f"{current_date_str}: refetched catalog hash {computed} does not match "
            f"manifest sha256 {manifest['sha256']}"
        )
    with open(gz_path, "wb") as raw_file:
        with gzip.GzipFile(
            filename="", mode="wb", fileobj=raw_file, compresslevel=9, mtime=0
        ) as gz_file:
            gz_file.write(raw)
    print(f"  Rematerialized {gz_path.name} from the published bundle")


def build_or_fetch_release_bundle(current_date_str, output_dir=None, min_count=MIN_OBJECT_COUNT, repo=None):
    try:
        return build_release_bundle(
            current_date_str,
            output_dir=output_dir,
            min_count=min_count,
        )
    except SnapshotError as exc:
        if "missing catalog.json.gz" not in str(exc):
            raise
        print(f"  Local catalog missing; fetching existing release bundle for {current_date_str}")
        return download_release_bundle_to_file(current_date_str, output_dir=output_dir, repo=repo)


def storage_receipt_path(current_date_str):
    return snapshot_dir(current_date_str) / "storage.json"


def load_storage_receipt(current_date_str):
    receipt = read_json_if_exists(storage_receipt_path(current_date_str), default={})
    if isinstance(receipt, dict):
        return receipt
    return {}


def record_storage_destination(bundle, destination, payload):
    receipt = load_storage_receipt(bundle["date"])
    receipt.update(
        {
            "date": bundle["date"],
            "asset_name": bundle["asset_name"],
            "bundle_bytes": bundle["bytes"],
            "bundle_sha256": bundle["bundle_sha256"],
            "catalog_sha256": bundle["catalog_sha256"],
            "manifest_sha256": bundle["manifest_sha256"],
            "updated_at": utc_stamp(),
        }
    )
    destinations = receipt.get("destinations")
    if not isinstance(destinations, dict):
        destinations = {}
    destinations[destination] = payload
    receipt["destinations"] = destinations
    write_json(storage_receipt_path(bundle["date"]), receipt)


def b64url_encode(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(value):
    padding = "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def sha256_bytes(data):
    return hashlib.sha256(data).digest()


def sha384_bytes(data):
    return hashlib.sha384(data).digest()


def arweave_gateway():
    gateway = os.environ.get("ARWEAVE_GATEWAY", ARWEAVE_GATEWAY_DEFAULT).rstrip("/")
    validate_arweave_gateway(gateway)
    return gateway


def validate_arweave_gateway(gateway):
    parsed = urllib.parse.urlparse(gateway)
    if parsed.scheme != "https":
        raise SnapshotError("ARWEAVE_GATEWAY must use HTTPS")
    host = parsed.hostname
    if not host:
        raise SnapshotError("ARWEAVE_GATEWAY is missing a host")
    host = host.lower()
    if host != "arweave.net":
        raise SnapshotError("ARWEAVE_GATEWAY must be https://arweave.net")
    reject_private_host(host, context="ARWEAVE_GATEWAY")


def reject_private_host(host, *, context):
    try:
        addresses = [host]
        ipaddress.ip_address(host)
    except ValueError:
        try:
            addresses = [item[4][0] for item in socket.getaddrinfo(host, None)]
        except OSError as exc:
            raise SnapshotError(f"{context} host cannot be resolved: {host}") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
            or not ip.is_global
        ):
            raise SnapshotError(f"{context} resolves to a non-public address")


def arweave_request(
    method,
    path,
    payload=None,
    headers=None,
    allow_http_errors=False,
    allow_not_found=False,
):
    url = path if path.startswith("http://") or path.startswith("https://") else f"{arweave_gateway()}{path}"
    validate_arweave_gateway(url)
    request_headers = {
        "Accept": "application/json",
        "User-Agent": f"rso-archive/{PIPELINE_VERSION}",
    }
    if headers:
        request_headers.update(headers)

    data = None
    if payload is not None:
        if isinstance(payload, (bytes, bytearray)):
            data = bytes(payload)
        else:
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            request_headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            body = read_limited(resp, MAX_JSON_BYTES, label="Arweave API response")
            content_type = resp.headers.get("Content-Type", "")
            status = resp.status
    except urllib.error.HTTPError as exc:
        if allow_not_found and exc.code == 404:
            return 404, None
        detail = read_limited(exc, MAX_JSON_BYTES, label="Arweave API error response").decode(
            "utf-8", errors="replace"
        )
        if allow_http_errors:
            try:
                return exc.code, json.loads(detail)
            except json.JSONDecodeError:
                return exc.code, detail
        raise SnapshotError(f"Arweave API {method} {url} failed ({exc.code}): {detail}") from exc

    if not body:
        return status, None
    if "application/json" in content_type or body[:1] in (b"{", b"["):
        return status, json.loads(body)
    return status, body.decode("utf-8", errors="replace")


def arweave_wallet_jwk():
    raw = os.environ.get("ARWEAVE_JWK") or os.environ.get("ARWEAVE_WALLET_JSON")
    if not raw:
        return None
    try:
        jwk = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SnapshotError("ARWEAVE_JWK is not valid JSON") from exc
    required = {"kty", "n", "e", "d", "p", "q", "dp", "dq", "qi"}
    if not isinstance(jwk, dict) or not required.issubset(jwk):
        raise SnapshotError("ARWEAVE_JWK is missing required RSA JWK fields")
    if jwk.get("kty") != "RSA":
        raise SnapshotError("ARWEAVE_JWK must be an RSA key")
    return jwk


def arweave_wallet_address(jwk):
    return b64url_encode(sha256_bytes(b64url_decode(jwk["n"])))


def arweave_int_to_buffer(value):
    buffer = bytearray(32)
    for index in range(len(buffer) - 1, -1, -1):
        byte = value % 256
        buffer[index] = byte
        value = (value - byte) // 256
    return bytes(buffer)


def arweave_chunk_data(data):
    chunks = []
    rest = data
    cursor = 0

    while len(rest) >= ARWEAVE_MAX_CHUNK_SIZE:
        chunk_size = ARWEAVE_MAX_CHUNK_SIZE
        next_chunk_size = len(rest) - ARWEAVE_MAX_CHUNK_SIZE
        if 0 < next_chunk_size < ARWEAVE_MIN_CHUNK_SIZE:
            chunk_size = (len(rest) + 1) // 2
        chunk = rest[:chunk_size]
        cursor += len(chunk)
        chunks.append(
            {
                "data_hash": sha256_bytes(chunk),
                "min_byte_range": cursor - len(chunk),
                "max_byte_range": cursor,
            }
        )
        rest = rest[chunk_size:]

    chunks.append(
        {
            "data_hash": sha256_bytes(rest),
            "min_byte_range": cursor,
            "max_byte_range": cursor + len(rest),
        }
    )
    return chunks


def arweave_generate_leaves(chunks):
    leaves = []
    for chunk in chunks:
        leaves.append(
            {
                "type": "leaf",
                "id": sha256_bytes(
                    sha256_bytes(chunk["data_hash"])
                    + sha256_bytes(arweave_int_to_buffer(chunk["max_byte_range"]))
                ),
                "data_hash": chunk["data_hash"],
                "min_byte_range": chunk["min_byte_range"],
                "max_byte_range": chunk["max_byte_range"],
            }
        )
    return leaves


def arweave_hash_branch(left, right):
    if right is None:
        return left
    return {
        "type": "branch",
        "id": sha256_bytes(
            sha256_bytes(left["id"])
            + sha256_bytes(right["id"])
            + sha256_bytes(arweave_int_to_buffer(left["max_byte_range"]))
        ),
        "byte_range": left["max_byte_range"],
        "max_byte_range": right["max_byte_range"],
        "left_child": left,
        "right_child": right,
    }


def arweave_build_layers(nodes):
    while len(nodes) > 1:
        next_layer = []
        for index in range(0, len(nodes), 2):
            left = nodes[index]
            right = nodes[index + 1] if index + 1 < len(nodes) else None
            next_layer.append(arweave_hash_branch(left, right))
        nodes = next_layer
    return nodes[0]


def arweave_array_flatten(values):
    flattened = []
    for value in values:
        if isinstance(value, list):
            flattened.extend(arweave_array_flatten(value))
        else:
            flattened.append(value)
    return flattened


def arweave_resolve_branch_proofs(node, proof=b""):
    if node["type"] == "leaf":
        return {
            "offset": node["max_byte_range"] - 1,
            "proof": proof + node["data_hash"] + arweave_int_to_buffer(node["max_byte_range"]),
        }

    partial_proof = (
        proof
        + node["left_child"]["id"]
        + node["right_child"]["id"]
        + arweave_int_to_buffer(node["byte_range"])
    )
    return [
        arweave_resolve_branch_proofs(node["left_child"], partial_proof),
        arweave_resolve_branch_proofs(node["right_child"], partial_proof),
    ]


def arweave_generate_proofs(root):
    proofs = arweave_resolve_branch_proofs(root)
    if isinstance(proofs, list):
        return arweave_array_flatten(proofs)
    return [proofs]


def arweave_generate_transaction_chunks(data):
    chunks = arweave_chunk_data(data)
    leaves = arweave_generate_leaves(chunks)
    root = arweave_build_layers(leaves)
    proofs = arweave_generate_proofs(root)

    last_chunk = chunks[-1]
    if last_chunk["max_byte_range"] - last_chunk["min_byte_range"] == 0:
        chunks = chunks[:-1]
        proofs = proofs[:-1]

    return {
        "data_root": root["id"],
        "chunks": chunks,
        "proofs": proofs,
    }


def arweave_deep_hash(item):
    if isinstance(item, (list, tuple)):
        acc = sha384_bytes(b"list" + str(len(item)).encode("utf-8"))
        for value in item:
            acc = sha384_bytes(acc + arweave_deep_hash(value))
        return acc
    data = bytes(item)
    return sha384_bytes(
        sha384_bytes(b"blob" + str(len(data)).encode("utf-8")) + sha384_bytes(data)
    )


def mgf1_sha256(seed, length):
    output = bytearray()
    counter = 0
    while len(output) < length:
        output.extend(hashlib.sha256(seed + counter.to_bytes(4, "big")).digest())
        counter += 1
    return bytes(output[:length])


def rsa_pss_sign_sha256(jwk, message, salt_length=32):
    modulus = int.from_bytes(b64url_decode(jwk["n"]), "big")
    private_exponent = int.from_bytes(b64url_decode(jwk["d"]), "big")
    mod_bits = modulus.bit_length()
    em_bits = mod_bits - 1
    em_len = (em_bits + 7) // 8
    hash_length = hashlib.sha256().digest_size
    if em_len < hash_length + salt_length + 2:
        raise SnapshotError("Arweave RSA key is too small for PSS signing")

    message_hash = hashlib.sha256(message).digest()
    salt = os.urandom(salt_length)
    m_prime = (b"\x00" * 8) + message_hash + salt
    digest = hashlib.sha256(m_prime).digest()
    padding = b"\x00" * (em_len - salt_length - hash_length - 2)
    data_block = padding + b"\x01" + salt
    db_mask = mgf1_sha256(digest, em_len - hash_length - 1)
    masked_db = bytearray(a ^ b for a, b in zip(data_block, db_mask))
    masked_db[0] &= 0xFF >> ((8 * em_len) - em_bits)
    encoded_message = bytes(masked_db) + digest + b"\xbc"

    signature_int = pow(int.from_bytes(encoded_message, "big"), private_exponent, modulus)
    key_length = (mod_bits + 7) // 8
    return signature_int.to_bytes(key_length, "big")


def arweave_tags(bundle):
    return [
        ("App-Name", "RSO-Archive"),
        ("App-Version", PIPELINE_VERSION),
        ("Content-Type", "application/gzip"),
        ("Bundle-Name", bundle["asset_name"]),
        ("Bundle-Format", "tar.gz"),
        ("Archive-Date", bundle["date"]),
        ("Catalog-SHA256", bundle["catalog_sha256"]),
        ("Bundle-SHA256", bundle["bundle_sha256"]),
    ]


def arweave_tag_objects(bundle):
    return [
        {"name": b64url_encode(name.encode("utf-8")), "value": b64url_encode(value.encode("utf-8"))}
        for name, value in arweave_tags(bundle)
    ]


def arweave_signature_payload(transaction):
    tag_list = []
    for tag in transaction["tags"]:
        tag_list.append([b64url_decode(tag["name"]), b64url_decode(tag["value"])])
    return arweave_deep_hash(
        [
            str(transaction["format"]).encode("utf-8"),
            b64url_decode(transaction["owner"]),
            b64url_decode(transaction["target"]),
            transaction["quantity"].encode("utf-8"),
            transaction["reward"].encode("utf-8"),
            b64url_decode(transaction["last_tx"]),
            tag_list,
            transaction["data_size"].encode("utf-8"),
            b64url_decode(transaction["data_root"]),
        ]
    )


def arweave_wallet_balance(address):
    _, balance = arweave_request("GET", f"/wallet/{address}/balance")
    if not isinstance(balance, str) or not balance.isdigit():
        raise SnapshotError(f"Arweave balance endpoint returned invalid payload: {balance!r}")
    return int(balance)


def arweave_build_transaction(bundle, jwk):
    bundle_bytes = Path(bundle["path"]).read_bytes()
    chunk_plan = arweave_generate_transaction_chunks(bundle_bytes)
    _, price = arweave_request("GET", f"/price/{len(bundle_bytes)}")
    _, anchor = arweave_request("GET", "/tx_anchor")
    if not isinstance(price, str) or not price.isdigit():
        raise SnapshotError(f"Arweave price endpoint returned invalid payload: {price!r}")
    if not isinstance(anchor, str) or not anchor:
        raise SnapshotError(f"Arweave tx_anchor endpoint returned invalid payload: {anchor!r}")
    address = arweave_wallet_address(jwk)
    balance = arweave_wallet_balance(address)
    reward = int(price)
    if balance < reward:
        raise SnapshotError(
            f"Arweave wallet {address} has {balance} winston, below required reward {reward}"
        )

    transaction = {
        "format": 2,
        "id": "",
        "last_tx": anchor,
        "owner": jwk["n"],
        "tags": arweave_tag_objects(bundle),
        "target": "",
        "quantity": "0",
        "data": "",
        "data_size": str(len(bundle_bytes)),
        "data_root": b64url_encode(chunk_plan["data_root"]),
        "reward": price,
        "signature": "",
    }
    signature = rsa_pss_sign_sha256(jwk, arweave_signature_payload(transaction), salt_length=32)
    transaction["signature"] = b64url_encode(signature)
    transaction["id"] = b64url_encode(sha256_bytes(signature))
    return {
        "transaction": transaction,
        "bundle_bytes": bundle_bytes,
        "chunk_plan": chunk_plan,
        "inline_data": False,
        "wallet_address": address,
    }


def arweave_chunk_payload(transaction, chunk_plan, bundle_bytes, chunk_index):
    proof = chunk_plan["proofs"][chunk_index]
    chunk = chunk_plan["chunks"][chunk_index]
    return {
        "data_root": transaction["data_root"],
        "data_size": transaction["data_size"],
        "data_path": b64url_encode(proof["proof"]),
        "offset": str(proof["offset"]),
        "chunk": b64url_encode(bundle_bytes[chunk["min_byte_range"] : chunk["max_byte_range"]]),
    }


def arweave_request_retryable(status, response):
    if status in ARWEAVE_TRANSIENT_HTTP_STATUSES:
        return True
    if isinstance(response, dict):
        error = str(response.get("error", "")).lower()
    else:
        error = str(response).lower()
    return any(
        marker in error
        for marker in (
            "timeout",
            "temporarily",
            "try again",
            "rate limit",
            "too many requests",
            "service unavailable",
            "gateway",
        )
    )


def arweave_request_with_retries(
    method,
    path,
    *,
    payload=None,
    headers=None,
    allow_not_found=False,
    attempts=None,
    delay=None,
):
    max_attempts = ARWEAVE_TRANSACTION_RETRIES + 1 if attempts is None else attempts
    retry_delay = ARWEAVE_TRANSACTION_RETRY_DELAY if delay is None else delay
    last_error = None
    for attempt in range(max_attempts):
        try:
            status, response = arweave_request(
                method,
                path,
                payload=payload,
                headers=headers,
                allow_http_errors=True,
                allow_not_found=allow_not_found,
            )
        except (TimeoutError, urllib.error.URLError) as exc:
            last_error = exc
            if attempt == max_attempts - 1:
                raise SnapshotError(f"Arweave API {method} {path} did not settle: {exc}") from exc
        else:
            if status < 400 or (allow_not_found and status == 404):
                return status, response
            if not arweave_request_retryable(status, response) or attempt == max_attempts - 1:
                return status, response
            last_error = SnapshotError(f"Arweave API {method} {path} returned {status}: {response}")
        time.sleep(retry_delay)
    raise SnapshotError(f"Arweave API {method} {path} did not settle: {last_error}")


def arweave_submit_transaction(upload):
    transaction = upload["transaction"]
    status, response = arweave_request_with_retries("POST", "/tx", payload=transaction)
    if status not in (200, 208):
        raise SnapshotError(
            f"Arweave transaction submission failed for {transaction['id']}: {response}"
        )


def arweave_submit_chunks(upload):
    total_chunks = len(upload["chunk_plan"]["chunks"])
    for chunk_index in range(total_chunks):
        payload = arweave_chunk_payload(
            upload["transaction"],
            upload["chunk_plan"],
            upload["bundle_bytes"],
            chunk_index,
        )
        for attempt in range(ARWEAVE_CHUNK_UPLOAD_RETRIES + 1):
            status, response = arweave_request(
                "POST",
                "/chunk",
                payload=payload,
                allow_http_errors=True,
            )
            if status == 200:
                break
            if not arweave_chunk_upload_retryable(response):
                raise SnapshotError(
                    f"Arweave chunk upload failed for {upload['transaction']['id']} "
                    f"chunk {chunk_index + 1}/{total_chunks}: {response}"
                )
            if attempt == ARWEAVE_CHUNK_UPLOAD_RETRIES:
                raise SnapshotError(
                    f"Arweave chunk upload did not settle for {upload['transaction']['id']} "
                    f"chunk {chunk_index + 1}/{total_chunks}: {response}"
                )
            time.sleep(ARWEAVE_CHUNK_UPLOAD_RETRY_DELAY)


def arweave_chunk_upload_retryable(response):
    if isinstance(response, dict):
        error = str(response.get("error", ""))
    else:
        error = str(response)
    return error in ARWEAVE_TRANSIENT_CHUNK_ERRORS


def github_release_assets(tag, repo=None):
    release = github_release_payload(tag, repo=repo, allow_missing=True)
    if release is None:
        return None
    return sorted(asset.get("name") for asset in release.get("assets", []) if asset.get("name"))


def release_notes(bundle):
    lines = [
        f"RSO archive snapshot for {bundle['date']}.",
        "",
        f"- Catalog SHA-256: `{bundle['catalog_sha256']}`",
        f"- Bundle SHA-256: `{bundle['bundle_sha256']}`",
        f"- Manifest SHA-256: `{bundle['manifest_sha256']}`",
        f"- Object count: `{bundle['object_count']}`",
        f"- State as of UTC: `{bundle.get('state_as_of_utc')}`",
        f"- Bundle bytes: `{bundle['bytes']}`",
        "",
        "The bundle is deterministic. Rebuilding the same archived day with the same code and artifacts should produce the same bundle hash.",
    ]
    return "\n".join(lines) + "\n"


def github_create_release(bundle, repo, notes, target_commitish=None):
    payload = {
        "tag_name": bundle["tag"],
        "name": bundle["title"],
        "body": notes,
        "prerelease": bool(bundle.get("prerelease")),
    }
    if target_commitish:
        payload["target_commitish"] = target_commitish
    return github_request(
        "POST",
        github_api_url(repo, "/releases"),
        payload=payload,
        token_required=True,
    )


def github_update_release(release, bundle, repo, notes):
    return github_request(
        "PATCH",
        github_api_url(repo, f"/releases/{release['id']}"),
        payload={
            "name": bundle["title"],
            "body": notes,
            "prerelease": bool(bundle.get("prerelease", release.get("prerelease", False))),
        },
        token_required=True,
    )


def github_delete_release_asset(asset, repo):
    github_request(
        "DELETE",
        github_api_url(repo, f"/releases/assets/{asset['id']}"),
        token_required=True,
    )


def github_upload_release_asset(release, bundle):
    upload_url = release["upload_url"].split("{", 1)[0]
    validate_github_download_url(upload_url)
    upload_url = f"{upload_url}?name={urllib.parse.quote(bundle['asset_name'])}"
    with open(bundle["path"], "rb") as f:
        payload = read_limited(f, MAX_RELEASE_BUNDLE_BYTES, label=str(bundle["path"]))
    return github_request(
        "POST",
        upload_url,
        payload=payload,
        headers={"Content-Type": "application/gzip"},
        token_required=True,
    )


def publish_github_release(
    bundle,
    repo=None,
    upload_policy="if_missing",
    force=False,
    target_commitish=None,
):
    resolved_repo = resolve_github_repo(repo)
    release = github_release_payload(bundle["tag"], repo=resolved_repo, allow_missing=True)
    asset = find_release_asset(release, bundle["asset_name"])
    asset_exists = asset is not None
    release_url = f"https://github.com/{resolved_repo}/releases/tag/{bundle['tag']}"

    if asset_exists and not force and not bundle.get("prerelease"):
        print(f"  SKIP: {bundle['tag']} already has {bundle['asset_name']}")
        result = {
            "status": "skipped",
            "reason": "asset_exists",
            "repo": resolved_repo,
            "release_url": release.get("html_url", release_url) if release else release_url,
            "asset_url": asset.get("browser_download_url") if asset else None,
            **bundle,
        }
        record_storage_destination(
            bundle,
            "github_release",
            {
                "status": result["status"],
                "repo": resolved_repo,
                "tag": bundle["tag"],
                "release_url": result["release_url"],
                "asset_name": bundle["asset_name"],
                "asset_url": result["asset_url"],
            },
        )
        return result

    notes = release_notes(bundle)

    if release is None:
        release = github_create_release(
            bundle,
            resolved_repo,
            notes,
            target_commitish=target_commitish,
        )
        uploaded_asset = github_upload_release_asset(release, bundle)
        print(f"  CREATED: {bundle['tag']} with {bundle['asset_name']}")
        result = {
            "status": "created",
            "repo": resolved_repo,
            "release_url": release.get("html_url", release_url),
            "asset_url": uploaded_asset.get("browser_download_url") if isinstance(uploaded_asset, dict) else None,
            **bundle,
        }
        record_storage_destination(
            bundle,
            "github_release",
            {
                "status": result["status"],
                "repo": resolved_repo,
                "tag": bundle["tag"],
                "release_url": result["release_url"],
                "asset_name": bundle["asset_name"],
                "asset_url": result["asset_url"],
            },
        )
        return result

    if asset_exists and not force and bundle.get("prerelease"):
        github_update_release(release, bundle, resolved_repo, notes)
        print(f"  UPDATED: {bundle['tag']} release metadata")
        result = {
            "status": "metadata_updated",
            "repo": resolved_repo,
            "release_url": release.get("html_url", release_url),
            "asset_url": asset.get("browser_download_url") if asset else None,
            **bundle,
        }
        record_storage_destination(
            bundle,
            "github_release",
            {
                "status": result["status"],
                "repo": resolved_repo,
                "tag": bundle["tag"],
                "release_url": result["release_url"],
                "asset_name": bundle["asset_name"],
                "asset_url": result["asset_url"],
            },
        )
        return result

    if asset_exists:
        github_delete_release_asset(asset, resolved_repo)
    uploaded_asset = github_upload_release_asset(release, bundle)
    github_update_release(release, bundle, resolved_repo, notes)
    print(f"  UPLOADED: {bundle['asset_name']} to {bundle['tag']}")
    result = {
        "status": "uploaded",
        "repo": resolved_repo,
        "release_url": release.get("html_url", release_url),
        "asset_url": uploaded_asset.get("browser_download_url") if isinstance(uploaded_asset, dict) else None,
        **bundle,
    }
    record_storage_destination(
        bundle,
        "github_release",
        {
            "status": result["status"],
            "repo": resolved_repo,
            "tag": bundle["tag"],
            "release_url": result["release_url"],
            "asset_name": bundle["asset_name"],
            "asset_url": result["asset_url"],
        },
    )
    return result


def publish_arweave_bundle(bundle, upload_policy="if_missing", force=False):
    jwk = arweave_wallet_jwk()
    if jwk is None:
        print("  SKIP: ARWEAVE_JWK not set; Arweave upload disabled")
        return {"status": "skipped", "reason": "missing_wallet", **bundle}

    existing = load_storage_receipt(bundle["date"]).get("destinations", {}).get("arweave")
    if (
        isinstance(existing, dict)
        and existing.get("bundle_sha256") == bundle["bundle_sha256"]
        and existing.get("status") == "submitted"
        and existing.get("transaction_id")
        and not force
        and upload_policy != "always_mirror"
    ):
        print(f"  SKIP: storage.json already records Arweave TX {existing.get('transaction_id')}")
        return {
            "status": "skipped",
            "reason": "receipt_exists",
            "transaction_id": existing.get("transaction_id"),
            **bundle,
        }

    upload = arweave_build_transaction(bundle, jwk)
    transaction = upload["transaction"]
    upload_mode = "inline" if upload["inline_data"] else "chunked"
    total_chunks = len(upload["chunk_plan"]["chunks"])
    print(f"  Arweave mode: {upload_mode} ({total_chunks} chunks)")
    arweave_submit_transaction(upload)
    if not upload["inline_data"]:
        arweave_submit_chunks(upload)

    status_code, tx_status = arweave_request_with_retries(
        "GET",
        f"/tx/{transaction['id']}/status",
        allow_not_found=True,
    )
    tx_url = f"{arweave_gateway()}/{transaction['id']}"
    destination = {
        "status": "submitted",
        "gateway": arweave_gateway(),
        "bundle_sha256": bundle["bundle_sha256"],
        "transaction_id": transaction["id"],
        "transaction_url": tx_url,
        "wallet_address": upload["wallet_address"],
        "reward_winston": transaction["reward"],
        "anchor": transaction["last_tx"],
        "upload_mode": upload_mode,
        "chunk_count": total_chunks,
        "submitted_at": utc_stamp(),
        "status_code": status_code,
        "status_response": tx_status,
    }
    record_storage_destination(bundle, "arweave", destination)
    print(
        f"  SUBMITTED: {bundle['asset_name']} to Arweave as {transaction['id']}"
    )
    return {"status": "submitted", "transaction_id": transaction["id"], "transaction_url": tx_url, **bundle}


def publish_arweave_bundle_nonfatal(bundle, upload_policy="if_missing", force=False):
    try:
        return publish_arweave_bundle(
            bundle,
            upload_policy=upload_policy,
            force=force,
        )
    except SnapshotError as exc:
        error = str(exc)
        print(f"  WARNING: Arweave upload failed; continuing with GitHub Release: {error}")
        record_storage_destination(
            bundle,
            "arweave",
            {
                "status": "failed",
                "bundle_sha256": bundle["bundle_sha256"],
                "failed_at": utc_stamp(),
                "error": error,
            },
        )
        return {"status": "failed", "reason": "arweave_upload_failed", "error": error, **bundle}


def resolve_publish_dates(args):
    if args.date:
        if args.start or args.end:
            raise SnapshotError("Use either --date or --start/--end, not both")
        return [args.date]
    if not args.start or not args.end:
        raise SnapshotError("publish requires --date or both --start and --end")
    return list(date_range(args.start, args.end))


def process_publish(args):
    storage_backend = args.storage_backend or os.environ.get("STORAGE_BACKEND", "github_release")
    upload_policy = args.upload_policy or os.environ.get("UPLOAD_POLICY", "if_missing")
    target_commitish = args.target_commitish or os.environ.get("RSO_RELEASE_TARGET_COMMITISH")

    if storage_backend not in STORAGE_BACKENDS:
        raise SnapshotError(
            f"Unsupported STORAGE_BACKEND={storage_backend}. "
            f"Expected one of: {', '.join(sorted(STORAGE_BACKENDS))}"
        )
    if upload_policy not in UPLOAD_POLICIES:
        raise SnapshotError(
            f"Unsupported UPLOAD_POLICY={upload_policy}. "
            f"Expected one of: {', '.join(sorted(UPLOAD_POLICIES))}"
        )

    dates = resolve_publish_dates(args)
    print(f"\nPublishing archive bundles: {dates[0]} to {dates[-1]}")
    print(f"  STORAGE_BACKEND={storage_backend}")
    print(f"  UPLOAD_POLICY={upload_policy}")

    results = []
    for current_date_str in dates:
        print(f"\n  Date: {current_date_str}")
        if getattr(args, "rebuild", False):
            bundle = build_rebuilt_bundle(
                current_date_str,
                output_dir=args.output_dir,
                min_count=args.min_objects,
            )
        elif args.use_existing_bundle:
            bundle = release_bundle_from_existing(
                current_date_str,
                output_dir=args.output_dir,
            )
        else:
            bundle = build_or_fetch_release_bundle(
                current_date_str,
                output_dir=args.output_dir,
                min_count=args.min_objects,
                repo=args.repo,
            )
        if args.prerelease:
            bundle["prerelease"] = True
        print(f"  Bundle: {bundle['path']}")
        print(f"  Bundle SHA-256: {bundle['bundle_sha256']}")

        if storage_backend == "none" or upload_policy == "never":
            print("  SKIP: upload disabled; bundle built for hash-only attestation")
            results.append({"status": "skipped", "reason": "upload_disabled", **bundle})
        elif storage_backend == "github_release":
            github_result = publish_github_release(
                bundle,
                repo=args.repo,
                upload_policy=upload_policy,
                force=args.force,
                target_commitish=target_commitish,
            )
            results.append({"destination": "github_release", **github_result})
            arweave_result = publish_arweave_bundle_nonfatal(
                bundle,
                upload_policy=upload_policy,
                force=args.force,
            )
            if arweave_result.get("reason") != "missing_wallet":
                results.append({"destination": "arweave", **arweave_result})
        elif storage_backend == "arweave":
            results.append(
                {
                    "destination": "arweave",
                    **publish_arweave_bundle(
                        bundle,
                        upload_policy=upload_policy,
                        force=args.force,
                    ),
                }
            )
        else:
            raise SnapshotError(
                f"STORAGE_BACKEND={storage_backend} is planned but not implemented yet"
            )

    summary = {}
    for result in results:
        summary[result["status"]] = summary.get(result["status"], 0) + 1
    print("\nPublish complete")
    for key in sorted(summary):
        print(f"  {key}: {summary[key]}")


def latest_dates(dates, count):
    if count <= 0:
        return []
    return sorted(dates)[-count:]


def next_unarchived_date(end_date_str=None):
    end = parse_date(end_date_str) if end_date_str else parse_date(now_utc().strftime("%Y-%m-%d"))
    dates = discover_snapshot_dates()
    if not dates:
        return date_str(end)

    candidate = parse_date(dates[-1]) + timedelta(days=1)
    if candidate <= end:
        return date_str(candidate)
    return date_str(end)


def resolve_prune_dates(args):
    if args.all:
        if args.date or args.start or args.end:
            raise SnapshotError("Use --all by itself, or use --date/--start/--end")
        return discover_snapshot_dates()
    return resolve_publish_dates(args)


def ensure_release_bundle_before_prune(current_date_str, output_dir=None):
    try:
        return release_bundle_from_existing(current_date_str, output_dir=output_dir)
    except SnapshotError:
        return build_release_bundle(current_date_str, output_dir=output_dir)


def process_prune_catalogs(args):
    dates = resolve_prune_dates(args)
    if not dates:
        raise SnapshotError("No snapshot dates selected for pruning")

    keep_latest = int(args.keep_latest or 0)
    retained_dates = set(latest_dates(discover_snapshot_dates(), keep_latest))
    pruned = 0
    retained = 0
    skipped = 0
    for current_date_str in dates:
        if current_date_str in retained_dates:
            retained += 1
            print(f"  RETAINED: {catalog_gz_path(current_date_str)}")
            continue
        path = catalog_gz_path(current_date_str)
        if not path.exists():
            skipped += 1
            continue
        if args.require_bundle:
            ensure_release_bundle_before_prune(current_date_str, output_dir=args.output_dir)
        path.unlink()
        pruned += 1
        print(f"  PRUNED: {path}")

    print("\nCatalog prune complete")
    print(f"  pruned:  {pruned}")
    print(f"  retained:{retained}")
    print(f"  skipped: {skipped}")


def validate_catalog_payload(current_date_str, catalog_gz_bytes, manifest):
    try:
        raw_bytes = gzip_decompress_limited(catalog_gz_bytes, MAX_CATALOG_BYTES, label="catalog.json")
        records = json.loads(raw_bytes)
    except (json.JSONDecodeError, OSError, gzip.BadGzipFile) as exc:
        raise SnapshotError(f"{current_date_str}: cannot read catalog payload: {exc}") from exc

    computed_hash = compute_hash(raw_bytes)
    if manifest.get("sha256") != computed_hash:
        raise SnapshotError(
            f"{current_date_str}: catalog hash mismatch "
            f"manifest={manifest.get('sha256')} computed={computed_hash}"
        )
    if manifest.get("raw_bytes") != len(raw_bytes):
        raise SnapshotError(
            f"{current_date_str}: raw_bytes={manifest.get('raw_bytes')} "
            f"actual={len(raw_bytes)}"
        )
    if manifest.get("compressed_bytes") != len(catalog_gz_bytes):
        raise SnapshotError(
            f"{current_date_str}: compressed_bytes={manifest.get('compressed_bytes')} "
            f"actual={len(catalog_gz_bytes)}"
        )
    if manifest.get("object_count") != len(records):
        raise SnapshotError(
            f"{current_date_str}: object_count={manifest.get('object_count')} "
            f"actual={len(records)}"
        )
    validate_gp_records(records, min_count=0, context=f"snapshot {current_date_str}")
    if raw_bytes != canonicalize(records):
        raise SnapshotError(f"{current_date_str}: catalog payload is not canonical JSON")


def hydrate_catalog(current_date_str, repo=None, force=False):
    manifest = load_manifest(current_date_str)
    path = catalog_gz_path(current_date_str)
    if path.exists() and not force:
        print(f"  SKIP: {path} already exists")
        return {"status": "skipped", "date": current_date_str, "path": str(path)}

    catalog_gz_bytes = catalog_gz_bytes_from_release_bundle(current_date_str, repo=repo)
    validate_catalog_payload(current_date_str, catalog_gz_bytes, manifest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(catalog_gz_bytes)
    print(f"  HYDRATED: {path}")
    return {"status": "hydrated", "date": current_date_str, "path": str(path)}


def resolve_hydrate_dates(args):
    if args.latest is not None:
        if args.date or args.start or args.end:
            raise SnapshotError("Use --latest by itself, or use --date/--start/--end")
        if args.latest < 0:
            raise SnapshotError("--latest must be zero or greater")
        return latest_dates(discover_snapshot_dates(), args.latest)
    return resolve_publish_dates(args)


def process_hydrate_catalogs(args):
    dates = resolve_hydrate_dates(args)
    if not dates:
        raise SnapshotError("No snapshot dates selected for hydration")

    print(f"\nHydrating local catalogs: {dates[0]} to {dates[-1]}")
    results = []
    for current_date_str in dates:
        results.append(hydrate_catalog(current_date_str, repo=args.repo, force=args.force))

    summary = {}
    for result in results:
        summary[result["status"]] = summary.get(result["status"], 0) + 1
    print("\nCatalog hydration complete")
    for key in sorted(summary):
        print(f"  {key}: {summary[key]}")


def process_mark_prerelease(args):
    dates = resolve_publish_dates(args)
    resolved_repo = resolve_github_repo(args.repo)
    prerelease = not args.undo
    for current_date_str in dates:
        tag = release_tag(current_date_str)
        release = github_release_payload(tag, repo=resolved_repo)
        github_request(
            "PATCH",
            github_api_url(resolved_repo, f"/releases/{release['id']}"),
            payload={"prerelease": prerelease},
            token_required=True,
        )
        state = "prerelease" if prerelease else "normal release"
        print(f"  UPDATED: {tag} -> {state}")


def discover_snapshot_dates():
    if not DATA_DIR.exists():
        return []

    dates = []
    for manifest_path in DATA_DIR.glob("*/*/*/manifest.json"):
        try:
            year = manifest_path.parents[2].name
            month = manifest_path.parents[1].name
            day = manifest_path.parents[0].name
            current_date_str = f"{year}-{month}-{day}"
            parse_date(current_date_str)
        except ValueError:
            continue
        dates.append(current_date_str)
    return sorted(set(dates))


def read_json_file(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def validate_counted_list(payload, count_key, list_key, errors, context):
    values = payload.get(list_key)
    if not isinstance(values, list):
        errors.append(f"{context}: {list_key} is missing or is not a list")
        return
    if payload.get(count_key) != len(values):
        errors.append(
            f"{context}: {count_key}={payload.get(count_key)} but "
            f"{list_key} has {len(values)} entries"
        )


def validate_snapshot_artifacts(
    current_date_str,
    min_count=MIN_OBJECT_COUNT,
    require_audit=False,
    require_catalog=False,
):
    errors = []
    day_dir = snapshot_dir(current_date_str)
    manifest_path = day_dir / "manifest.json"
    gz_path = catalog_gz_path(current_date_str)

    if not manifest_path.exists():
        return [f"{current_date_str}: missing manifest.json"], None

    try:
        manifest = read_json_file(manifest_path)
    except (json.JSONDecodeError, OSError) as exc:
        return [f"{current_date_str}: cannot read manifest.json: {exc}"], None

    context = f"snapshot {current_date_str}"
    if manifest.get("date") != current_date_str:
        errors.append(f"{context}: manifest date is {manifest.get('date')}")

    records = None
    if gz_path.exists():
        try:
            with gzip.open(gz_path, "rb") as f:
                raw_bytes = read_limited(f, MAX_CATALOG_BYTES, label=str(gz_path))
            records = json.loads(raw_bytes)
        except (json.JSONDecodeError, OSError, gzip.BadGzipFile) as exc:
            return [f"{current_date_str}: cannot read catalog.json.gz: {exc}"], manifest

        computed_hash = hashlib.sha256(raw_bytes).hexdigest()
        if manifest.get("sha256") != computed_hash:
            errors.append(
                f"{context}: sha256 mismatch manifest={manifest.get('sha256')} "
                f"computed={computed_hash}"
            )

        canonical_bytes = canonicalize(records)
        if raw_bytes != canonical_bytes:
            errors.append(f"{context}: catalog.json.gz is not canonical JSON")

        if manifest.get("raw_bytes") != len(raw_bytes):
            errors.append(
                f"{context}: raw_bytes={manifest.get('raw_bytes')} actual={len(raw_bytes)}"
            )
        compressed_size = gz_path.stat().st_size
        if manifest.get("compressed_bytes") != compressed_size:
            errors.append(
                f"{context}: compressed_bytes={manifest.get('compressed_bytes')} "
                f"actual={compressed_size}"
            )
        if manifest.get("object_count") != len(records):
            errors.append(
                f"{context}: object_count={manifest.get('object_count')} actual={len(records)}"
            )

        try:
            validate_gp_records(records, min_count=min_count, context=context)
        except SnapshotError as exc:
            errors.append(str(exc))

        cat_ids = [record.get("NORAD_CAT_ID") for record in records if isinstance(record, dict)]
        if len(cat_ids) != len(set(cat_ids)):
            errors.append(f"{context}: duplicate NORAD_CAT_ID values")
        if cat_ids != sorted(cat_ids, key=int_string_sort_key):
            errors.append(f"{context}: records are not sorted by NORAD_CAT_ID")
    elif require_catalog:
        errors.append(f"{context}: missing catalog.json.gz")
    elif int(manifest.get("object_count", 0)) < min_count:
        errors.append(
            f"{context}: object_count={manifest.get('object_count')} below minimum {min_count}"
        )

    provenance = manifest.get("provenance")
    if provenance == "rolling_gp_history_delta":
        delta_path = day_dir / "delta.json"
        if not delta_path.exists():
            errors.append(f"{context}: rolling snapshot missing delta.json")
        else:
            try:
                delta = read_json_file(delta_path)
            except (json.JSONDecodeError, OSError) as exc:
                errors.append(f"{context}: cannot read delta.json: {exc}")
            else:
                if delta.get("date") != current_date_str:
                    errors.append(f"{context}: delta date is {delta.get('date')}")
                if delta.get("window_start_utc") != manifest.get("delta_window_start_utc"):
                    errors.append(f"{context}: delta window_start_utc does not match manifest")
                if delta.get("window_end_utc") != manifest.get("delta_window_end_utc"):
                    errors.append(f"{context}: delta window_end_utc does not match manifest")
                for count_key, list_key in (
                    ("new_object_count", "new_norad_cat_ids"),
                    ("updated_object_count", "updated_norad_cat_ids"),
                    ("unchanged_update_count", "unchanged_update_norad_cat_ids"),
                    ("ignored_older_update_count", "ignored_older_update_norad_cat_ids"),
                ):
                    validate_counted_list(delta, count_key, list_key, errors, context)
                update_total = sum(
                    int(delta.get(key, 0))
                    for key in (
                        "new_object_count",
                        "updated_object_count",
                        "unchanged_update_count",
                        "ignored_older_update_count",
                    )
                )
                if delta.get("deduped_update_count") != update_total:
                    errors.append(
                        f"{context}: deduped_update_count={delta.get('deduped_update_count')} "
                        f"but categorized updates total {update_total}"
                    )
    elif provenance == "genesis_from_gp":
        if (day_dir / "delta.json").exists():
            errors.append(f"{context}: genesis snapshot must not include delta.json")
    else:
        errors.append(f"{context}: unexpected provenance {provenance}")

    audit_path = day_dir / "audit.json"
    visibility_path = day_dir / "visibility_state.json"
    if require_audit and not audit_path.exists():
        errors.append(f"{context}: missing audit.json")
    if require_audit and not visibility_path.exists():
        errors.append(f"{context}: missing visibility_state.json")

    if audit_path.exists():
        try:
            audit = read_json_file(audit_path)
        except (json.JSONDecodeError, OSError) as exc:
            errors.append(f"{context}: cannot read audit.json: {exc}")
        else:
            if audit.get("date") != current_date_str:
                errors.append(f"{context}: audit date is {audit.get('date')}")
            if audit.get("archive_object_count") != manifest.get("object_count"):
                errors.append(f"{context}: audit archive_object_count does not match manifest")
            for count_key, list_key in (
                ("missing_from_current_gp_count", "missing_from_current_gp"),
                ("present_in_current_gp_not_in_archive_count", "present_in_current_gp_not_in_archive"),
                ("reappeared_in_current_gp_count", "reappeared_in_current_gp"),
            ):
                validate_counted_list(audit, count_key, list_key, errors, context)

            if visibility_path.exists():
                try:
                    visibility = read_json_file(visibility_path)
                except (json.JSONDecodeError, OSError) as exc:
                    errors.append(f"{context}: cannot read visibility_state.json: {exc}")
                else:
                    if visibility.get("date") != current_date_str:
                        errors.append(
                            f"{context}: visibility_state date is {visibility.get('date')}"
                        )
                    if visibility.get("observed_at_utc") != audit.get("observed_at_utc"):
                        errors.append(
                            f"{context}: visibility_state observed_at_utc does not match audit"
                        )
                    missing_objects = visibility.get("missing_objects")
                    if not isinstance(missing_objects, dict):
                        errors.append(f"{context}: visibility_state missing_objects is not a dict")
                    elif len(missing_objects) != audit.get("missing_from_current_gp_count"):
                        errors.append(
                            f"{context}: visibility_state missing_objects count does not "
                            "match audit"
                        )

    return errors, manifest


def validate_ledger(manifests_by_date):
    errors = []
    if not LEDGER_PATH.exists():
        return ["ledger.json is missing"]

    try:
        ledger = read_json_file(LEDGER_PATH)
    except (json.JSONDecodeError, OSError) as exc:
        return [f"cannot read ledger.json: {exc}"]

    if not isinstance(ledger, list):
        return ["ledger.json is not a list"]

    ledger_dates = [entry.get("date") for entry in ledger if isinstance(entry, dict)]
    if len(ledger_dates) != len(set(ledger_dates)):
        errors.append("ledger.json has duplicate dates")
    if ledger_dates != sorted(ledger_dates):
        errors.append("ledger.json is not sorted by date")

    manifest_dates = sorted(manifests_by_date)
    if sorted(ledger_dates) != manifest_dates:
        errors.append(
            "ledger.json dates do not match archived manifests: "
            f"ledger={sorted(ledger_dates)} manifests={manifest_dates}"
        )

    for index, entry in enumerate(ledger):
        if not isinstance(entry, dict):
            errors.append(f"ledger entry {index} is not an object")
            continue
        current_date_str = entry.get("date")
        manifest = manifests_by_date.get(current_date_str)
        if manifest is None:
            continue
        expected = ledger_entry_from_manifest(manifest)
        for key, value in expected.items():
            if entry.get(key) != value:
                errors.append(
                    f"ledger {current_date_str}: {key}={entry.get(key)} "
                    f"but manifest has {value}"
                )

    for current_date_str, manifest in manifests_by_date.items():
        if manifest.get("provenance") != "rolling_gp_history_delta":
            continue
        base_date = manifest.get("base_snapshot_date")
        base_sha = manifest.get("base_snapshot_sha256")
        base_manifest = manifests_by_date.get(base_date)
        if base_manifest is None:
            errors.append(f"{current_date_str}: base snapshot {base_date} is not archived")
        elif base_manifest.get("sha256") != base_sha:
            errors.append(f"{current_date_str}: base_snapshot_sha256 does not match base manifest")

    return errors


def validate_archive(
    min_count=MIN_OBJECT_COUNT,
    require_audit=False,
    require_catalog=False,
    require_latest_catalogs=0,
):
    dates = discover_snapshot_dates()
    if not dates:
        raise SnapshotError("No archived snapshots found under data/")

    required_catalog_dates = set(latest_dates(dates, int(require_latest_catalogs or 0)))
    manifests_by_date = {}
    errors = []
    for current_date_str in dates:
        date_errors, manifest = validate_snapshot_artifacts(
            current_date_str,
            min_count=min_count,
            require_audit=require_audit,
            require_catalog=require_catalog or current_date_str in required_catalog_dates,
        )
        errors.extend(date_errors)
        if manifest is not None and manifest.get("date") == current_date_str:
            manifests_by_date[current_date_str] = manifest

    errors.extend(validate_ledger(manifests_by_date))
    if errors:
        formatted = "\n".join(f"  - {error}" for error in errors)
        raise SnapshotError(f"Archive validation failed:\n{formatted}")

    print("Archive validation")
    print(f"  Snapshots: {len(dates)}")
    print(f"  First:     {dates[0]}")
    print(f"  Latest:    {dates[-1]}")
    if required_catalog_dates:
        print(f"  Local catalogs required: {len(required_catalog_dates)}")
    print("  Status:    VALID")


def add_common_snapshot_args(parser):
    parser.add_argument(
        "--range-size",
        type=int,
        default=CATALOG_RANGE_SIZE,
        help=f"NORAD_CAT_ID range size per gp_history request (default: {CATALOG_RANGE_SIZE})",
    )
    parser.add_argument(
        "--max-catalog-id",
        type=int,
        default=MAX_NORAD_CAT_ID,
        help=f"Highest NORAD_CAT_ID to query (default: {MAX_NORAD_CAT_ID})",
    )
    parser.add_argument(
        "--min-objects",
        type=int,
        default=MIN_OBJECT_COUNT,
        help=f"Minimum snapshot size required (default: {MIN_OBJECT_COUNT})",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing snapshot and upsert the ledger entry",
    )


def main():
    parser = argparse.ArgumentParser(
        description="RSO Archive - Space-Track GP Catalog Snapshot Pipeline"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    genesis_parser = subparsers.add_parser(
        "genesis", help="Capture current gp as the first agreed rolling snapshot"
    )
    genesis_parser.add_argument(
        "--date",
        help="Snapshot date (YYYY-MM-DD), defaults to current UTC date",
        default=None,
    )
    add_common_snapshot_args(genesis_parser)

    daily_parser = subparsers.add_parser("daily", help="Build one rolling daily snapshot")
    daily_parser.add_argument(
        "--date",
        help="Snapshot date (YYYY-MM-DD), defaults to current UTC date",
        default=None,
    )
    daily_parser.add_argument(
        "--no-audit",
        action="store_true",
        help="Skip current-gp visibility audit for this daily snapshot",
    )
    add_common_snapshot_args(daily_parser)

    roll_forward_parser = subparsers.add_parser(
        "roll-forward", help="Roll forward snapshots from an existing prior snapshot"
    )
    roll_forward_parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    roll_forward_parser.add_argument("--end", required=True, help="End date inclusive (YYYY-MM-DD)")
    add_common_snapshot_args(roll_forward_parser)

    replay_parser = subparsers.add_parser(
        "replay",
        help="Replay bounded gp_history from an empty state and compare to current gp",
    )
    replay_parser.add_argument("--start", required=True, help="Replay start date (YYYY-MM-DD)")
    replay_parser.add_argument(
        "--report-path",
        default=None,
        help="Path for replay report JSON; defaults to reports/replay_*.json",
    )
    add_common_snapshot_args(replay_parser)
    replay_parser.set_defaults(force=False)

    verify_parser = subparsers.add_parser(
        "verify", help="Verify a stored snapshot's hash"
    )
    verify_parser.add_argument("--date", required=True, help="Date (YYYY-MM-DD)")

    rebuild_content_parser = subparsers.add_parser(
        "rebuild-content",
        help="Add content fields and annotations to already-archived days (offline)",
    )
    rebuild_content_parser.add_argument("--start", required=True, help="Start date (YYYY-MM-DD)")
    rebuild_content_parser.add_argument("--end", required=True, help="End date inclusive (YYYY-MM-DD)")
    rebuild_content_parser.add_argument(
        "--force",
        action="store_true",
        help="Re-derive content fields even for days that already carry them",
    )

    validate_parser = subparsers.add_parser(
        "validate", help="Validate every committed archive artifact without network access"
    )
    validate_parser.add_argument(
        "--min-objects",
        type=int,
        default=MIN_OBJECT_COUNT,
        help=f"Minimum snapshot size required (default: {MIN_OBJECT_COUNT})",
    )
    validate_parser.add_argument(
        "--require-audit",
        action="store_true",
        help="Require audit.json and visibility_state.json for every archived snapshot",
    )
    validate_parser.add_argument(
        "--require-catalog",
        action="store_true",
        help="Require local catalog.json.gz files and verify their hashes",
    )
    validate_parser.add_argument(
        "--require-latest-catalogs",
        type=int,
        default=DEFAULT_RETAINED_CATALOG_COUNT,
        help=(
            "Require local catalog.json.gz files for the N most recent snapshots "
            f"(default: {DEFAULT_RETAINED_CATALOG_COUNT}; use 0 to disable)"
        ),
    )

    next_date_parser = subparsers.add_parser(
        "next-date",
        help="Print the next unarchived snapshot date, capped at the current UTC date",
    )
    next_date_parser.add_argument(
        "--end",
        default=None,
        help="Maximum date to return (YYYY-MM-DD), defaults to current UTC date",
    )

    previous_date_parser = subparsers.add_parser(
        "previous-date",
        help="Print the UTC date before the given snapshot date",
    )
    previous_date_parser.add_argument("--date", required=True, help="Date (YYYY-MM-DD)")

    publish_parser = subparsers.add_parser(
        "publish",
        help="Build deterministic release bundles and optionally publish them",
    )
    publish_parser.add_argument("--date", help="Single archive date (YYYY-MM-DD)")
    publish_parser.add_argument("--start", help="Start date for release range (YYYY-MM-DD)")
    publish_parser.add_argument("--end", help="End date for release range (YYYY-MM-DD)")
    publish_parser.add_argument(
        "--storage-backend",
        choices=sorted(STORAGE_BACKENDS),
        default=None,
        help="Storage backend; defaults to STORAGE_BACKEND env or github_release",
    )
    publish_parser.add_argument(
        "--upload-policy",
        choices=sorted(UPLOAD_POLICIES),
        default=None,
        help="Upload policy; defaults to UPLOAD_POLICY env or if_missing",
    )
    publish_parser.add_argument(
        "--repo",
        default=os.environ.get("GITHUB_REPOSITORY"),
        help="GitHub repo for github_release backend, OWNER/REPO. Defaults to GITHUB_REPOSITORY.",
    )
    publish_parser.add_argument(
        "--output-dir",
        default=RELEASE_OUTPUT_DIR,
        help=f"Directory for generated release bundles (default: {RELEASE_OUTPUT_DIR})",
    )
    publish_parser.add_argument(
        "--min-objects",
        type=int,
        default=MIN_OBJECT_COUNT,
        help=f"Minimum snapshot size required (default: {MIN_OBJECT_COUNT})",
    )
    publish_parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing GitHub release asset with the same name",
    )
    publish_parser.add_argument(
        "--use-existing-bundle",
        action="store_true",
        help="Upload a bundle already present in --output-dir instead of rebuilding it",
    )
    publish_parser.add_argument(
        "--rebuild",
        action="store_true",
        help=(
            "Rebuild bundles locally from the day directories instead of fetching "
            "existing release assets; requires rebuild-content to have run first"
        ),
    )
    publish_parser.add_argument(
        "--prerelease",
        action="store_true",
        help="Create or update GitHub releases as prereleases",
    )
    publish_parser.add_argument(
        "--target-commitish",
        default=None,
        help=(
            "Commit SHA or branch for new GitHub release tags; defaults to "
            "RSO_RELEASE_TARGET_COMMITISH when set."
        ),
    )

    prune_parser = subparsers.add_parser(
        "prune-catalogs",
        help="Prune local catalog.json.gz files while retaining a recent bootstrap cache",
    )
    prune_parser.add_argument("--date", help="Single archive date (YYYY-MM-DD)")
    prune_parser.add_argument("--start", help="Start date for pruning (YYYY-MM-DD)")
    prune_parser.add_argument("--end", help="End date for pruning (YYYY-MM-DD)")
    prune_parser.add_argument(
        "--all",
        action="store_true",
        help="Prune catalogs for all dates discovered under data/",
    )
    prune_parser.add_argument(
        "--require-bundle",
        action="store_true",
        help="Require a matching release bundle in --output-dir before pruning",
    )
    prune_parser.add_argument(
        "--output-dir",
        default=RELEASE_OUTPUT_DIR,
        help=f"Directory containing release bundles (default: {RELEASE_OUTPUT_DIR})",
    )
    prune_parser.add_argument(
        "--keep-latest",
        type=int,
        default=0,
        help="Keep local catalog.json.gz files for the N most recent archived snapshots",
    )

    hydrate_parser = subparsers.add_parser(
        "hydrate-catalogs",
        help="Restore local catalog.json.gz files from GitHub release bundles",
    )
    hydrate_parser.add_argument("--date", help="Single archive date (YYYY-MM-DD)")
    hydrate_parser.add_argument("--start", help="Start date for hydration (YYYY-MM-DD)")
    hydrate_parser.add_argument("--end", help="End date for hydration (YYYY-MM-DD)")
    hydrate_parser.add_argument(
        "--latest",
        type=int,
        default=None,
        help="Hydrate the N most recent archived snapshots",
    )
    hydrate_parser.add_argument(
        "--repo",
        default=os.environ.get("GITHUB_REPOSITORY"),
        help="GitHub repo, OWNER/REPO. Defaults to GITHUB_REPOSITORY or origin.",
    )
    hydrate_parser.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing local catalog.json.gz",
    )

    mark_prerelease_parser = subparsers.add_parser(
        "mark-prerelease",
        help="Mark existing GitHub archive releases as prereleases",
    )
    mark_prerelease_parser.add_argument("--date", help="Single archive date (YYYY-MM-DD)")
    mark_prerelease_parser.add_argument("--start", help="Start date (YYYY-MM-DD)")
    mark_prerelease_parser.add_argument("--end", help="End date inclusive (YYYY-MM-DD)")
    mark_prerelease_parser.add_argument(
        "--repo",
        default=os.environ.get("GITHUB_REPOSITORY"),
        help="GitHub repo, OWNER/REPO. Defaults to GITHUB_REPOSITORY or origin.",
    )
    mark_prerelease_parser.add_argument(
        "--undo",
        action="store_true",
        help="Clear the prerelease flag instead of setting it",
    )

    args = parser.parse_args()

    if args.command == "verify":
        verify_date(args.date)
        return
    if args.command == "validate":
        validate_archive(
            min_count=args.min_objects,
            require_audit=args.require_audit,
            require_catalog=args.require_catalog,
            require_latest_catalogs=args.require_latest_catalogs,
        )
        return
    if args.command == "next-date":
        print(next_unarchived_date(args.end))
        return
    if args.command == "previous-date":
        print(previous_date_str(args.date))
        return
    if args.command == "publish":
        process_publish(args)
        return
    if args.command == "prune-catalogs":
        process_prune_catalogs(args)
        return
    if args.command == "hydrate-catalogs":
        process_hydrate_catalogs(args)
        return
    if args.command == "mark-prerelease":
        process_mark_prerelease(args)
        return
    if args.command == "rebuild-content":
        process_rebuild_content(args)
        return

    client = SpaceTrackClient()
    try:
        if args.command == "genesis":
            process_genesis(args, client)
        elif args.command == "daily":
            process_daily(args, client)
        elif args.command == "roll-forward":
            process_roll_forward(args, client)
        elif args.command == "replay":
            process_replay(args, client)
    finally:
        client.close()


if __name__ == "__main__":
    try:
        main()
    except (SnapshotError, urllib.error.URLError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)
