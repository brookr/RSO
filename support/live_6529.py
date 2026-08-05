#!/usr/bin/env python3
"""Build an RSO v1 TDH support snapshot from the live 6529 APIs."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from support.tdh import (  # noqa: E402
    TDH_SUPPORT_SCHEMA,
    build_operator_support,
    node_id_digest,
    parse_node_category,
)


DEFAULT_API_BASE = "https://api.6529.io/api"
MEMES_CONTRACT = "0x33FD426905F149f8376e227d0C9D3340AaD17aF1"
ETH_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")


class LiveSupportError(ValueError):
    """The live 6529 data was unavailable, malformed, or internally unstable."""


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_url(base: str, path: str, params: Mapping[str, object] | None = None) -> str:
    url = base.rstrip("/") + "/" + path.lstrip("/")
    if params:
        url += "?" + urllib.parse.urlencode(
            {key: str(value).lower() if isinstance(value, bool) else str(value) for key, value in params.items()}
        )
    return url


def fetch_json(url: str, *, timeout: float = 30.0, attempts: int = 3) -> object:
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "RSO-Archive-TDH-Snapshot/1"},
    )
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (TimeoutError, urllib.error.URLError, json.JSONDecodeError) as exc:
            if attempt == attempts:
                raise LiveSupportError(f"failed to fetch {url}: {exc}") from exc
            time.sleep(2 ** (attempt - 1))
    raise AssertionError("unreachable")


def fetch_mapping(fetcher: Callable[[str], object], url: str, label: str) -> Mapping[str, object]:
    raw = fetcher(url)
    if not isinstance(raw, Mapping):
        raise LiveSupportError(f"{label} must be a JSON object")
    return raw


def load_digest_nodes(path: str | None) -> dict[str, str]:
    if not path:
        return {}
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    nodes = raw.get("nodes") if isinstance(raw, Mapping) else None
    if not isinstance(nodes, list):
        raise LiveSupportError("digest roster requires a nodes array")
    result = {}
    for record in nodes:
        if not isinstance(record, Mapping):
            raise LiveSupportError("digest roster node records must be JSON objects")
        node_id = str(record.get("id", "")).lower()
        if not node_id:
            raise LiveSupportError("digest roster nodes require id")
        digest = node_id_digest(node_id)
        if digest in result and result[digest] != node_id:
            raise LiveSupportError("digest roster contains a truncated digest collision")
        result[digest] = node_id
    return result


def fetch_profile(
    identity: str,
    *,
    api_base: str,
    fetcher: Callable[[str], object],
) -> dict[str, object]:
    url = build_url(api_base, f"profiles/{urllib.parse.quote(identity, safe='')}")
    raw = fetch_mapping(fetcher, url, "6529 profile")
    profile = raw.get("profile")
    consolidation = raw.get("consolidation")
    if not isinstance(profile, Mapping) or not isinstance(consolidation, Mapping):
        raise LiveSupportError("6529 profile requires profile and consolidation objects")
    profile_id = str(profile.get("external_id", ""))
    handle = str(profile.get("handle", ""))
    wallets_raw = consolidation.get("wallets", [])
    if not profile_id or not handle or not isinstance(wallets_raw, list):
        raise LiveSupportError("6529 profile is missing id, handle, or wallets")
    wallets = []
    for item in wallets_raw:
        wallet = item.get("wallet") if isinstance(item, Mapping) else None
        address = wallet.get("address") if isinstance(wallet, Mapping) else None
        if not isinstance(address, str) or not ETH_ADDRESS.fullmatch(address):
            raise LiveSupportError("6529 profile contains an invalid wallet address")
        wallets.append(address.lower())
    return {
        "profileId": profile_id,
        "handle": handle,
        "wallets": sorted(set(wallets)),
        "consolidationKey": str(consolidation.get("consolidation_key", "")),
    }


def fetch_rep_state(
    identity: str,
    *,
    api_base: str,
    digest_nodes: Mapping[str, str],
    fetcher: Callable[[str], object],
) -> dict[str, dict[str, object]]:
    url = build_url(
        api_base,
        f"profiles/{urllib.parse.quote(identity, safe='')}/rep/ratings/received",
    )
    raw = fetch_mapping(fetcher, url, "6529 REP state")
    stats = raw.get("rating_stats")
    if not isinstance(stats, list):
        raise LiveSupportError("6529 REP state requires rating_stats")
    result = {}
    for record in stats:
        if not isinstance(record, Mapping):
            raise LiveSupportError("6529 REP category stats must be JSON objects")
        category = str(record.get("category", ""))
        node_id = parse_node_category(category, digest_nodes=digest_nodes)
        if not node_id:
            continue
        if category in result:
            raise LiveSupportError("6529 REP state contains a duplicate category")
        result[category] = {
            "nodeId": node_id,
            "rating": int(record.get("rating", 0)),
            "contributorCount": int(record.get("contributor_count", 0)),
        }
    return result


def fetch_category_stat(
    identity: str,
    category: str,
    *,
    api_base: str,
    fetcher: Callable[[str], object],
) -> dict[str, int]:
    url = build_url(
        api_base,
        f"profiles/{urllib.parse.quote(identity, safe='')}/rep/ratings/received",
    )
    raw = fetch_mapping(fetcher, url, "6529 REP state")
    stats = raw.get("rating_stats")
    if not isinstance(stats, list):
        raise LiveSupportError("6529 REP state requires rating_stats")
    matches = [
        record
        for record in stats
        if isinstance(record, Mapping) and str(record.get("category", "")) == category
    ]
    if len(matches) != 1:
        raise LiveSupportError(f"REP category {category!r} was not found exactly once")
    return {
        "rating": int(matches[0].get("rating", 0)),
        "contributorCount": int(matches[0].get("contributor_count", 0)),
    }


def fetch_category_raters(
    identity: str,
    category: str,
    expected: Mapping[str, object],
    *,
    api_base: str,
    fetcher: Callable[[str], object],
    page_size: int = 2000,
) -> list[dict[str, object]]:
    records = []
    page = 1
    expected_count = None
    while True:
        url = build_url(
            api_base,
            f"profiles/{urllib.parse.quote(identity, safe='')}/rep/ratings/by-rater",
            {
                "given": False,
                "page": page,
                "page_size": page_size,
                "order": "desc",
                "order_by": "rating",
                "category": category,
            },
        )
        raw = fetch_mapping(fetcher, url, f"6529 REP raters for {category}")
        data = raw.get("data")
        if not isinstance(data, list):
            raise LiveSupportError("6529 REP rater page requires data")
        if expected_count is None and raw.get("count") is not None:
            expected_count = int(raw["count"])
        for record in data:
            if not isinstance(record, Mapping):
                raise LiveSupportError("6529 REP rater records must be JSON objects")
            profile_id = str(record.get("profile_id", ""))
            wallets = record.get("wallets")
            if not profile_id or not isinstance(wallets, list):
                raise LiveSupportError("6529 REP rater requires profile_id and wallets")
            normalized_wallets = []
            for wallet in wallets:
                if not isinstance(wallet, str) or not ETH_ADDRESS.fullmatch(wallet):
                    raise LiveSupportError("6529 REP rater contains an invalid wallet")
                normalized_wallets.append(wallet.lower())
            records.append(
                {
                    "profileId": profile_id,
                    "handle": str(record.get("handle", "")),
                    "wallets": sorted(set(normalized_wallets)),
                    "consolidationKey": str(record.get("consolidation_key", "")),
                    "profileTdh": int(record.get("tdh", 0)),
                    "rating": int(record.get("rating", 0)),
                    "lastModified": str(record.get("last_modified", "")),
                }
            )
        if not raw.get("next"):
            break
        page += 1

    if expected_count is not None and len(records) != expected_count:
        raise LiveSupportError(f"REP rater count mismatch for {category}")
    if len(records) != int(expected["contributorCount"]):
        raise LiveSupportError(f"REP contributor count mismatch for {category}")
    if sum(int(record["rating"]) for record in records) != int(expected["rating"]):
        raise LiveSupportError(f"REP rating total mismatch for {category}")
    if len({str(record["profileId"]) for record in records}) != len(records):
        raise LiveSupportError(f"REP category contains duplicate rater profiles: {category}")
    return records


def fetch_tdh_state(
    *,
    api_base: str,
    fetcher: Callable[[str], object],
) -> dict[str, object]:
    url = build_url(api_base, "tdh_global_history", {"page_size": 1, "page": 1})
    raw = fetch_mapping(fetcher, url, "6529 TDH history")
    data = raw.get("data")
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], Mapping):
        raise LiveSupportError("6529 TDH history requires one latest record")
    block = int(data[0].get("block", 0))
    snapshot_date = str(data[0].get("date", ""))
    if block <= 0 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", snapshot_date):
        raise LiveSupportError("6529 TDH history record is malformed")
    return {"block": block, "date": snapshot_date}


def fetch_card_tdh(
    wallets: list[str],
    consolidation_key: str,
    card_token_id: int,
    *,
    api_base: str,
    fetcher: Callable[[str], object],
) -> dict[str, object]:
    if not wallets:
        raise LiveSupportError("6529 card TDH lookup requires a consolidated wallet")
    url = build_url(
        api_base,
        f"tdh/nft/{MEMES_CONTRACT}/{int(card_token_id)}",
        {
            "page_size": 25,
            "page": 1,
            "sort": "boosted_tdh",
            "sort_direction": "DESC",
            "search": ",".join(wallets),
        },
    )
    raw = fetch_mapping(fetcher, url, "6529 card TDH")
    data = raw.get("data")
    if not isinstance(data, list):
        raise LiveSupportError("6529 card TDH requires data")
    if int(raw.get("count", len(data))) != len(data) or len(data) > 1 or raw.get("next"):
        raise LiveSupportError("6529 card TDH search did not resolve to at most one collector")
    if not data:
        return {
            "tdh": 0,
            "balance": 0,
            "apiBoostedTdh": 0,
            "apiTdh": 0,
            "apiRawTdh": 0,
        }

    record = data[0]
    if not isinstance(record, Mapping):
        raise LiveSupportError("6529 card TDH record must be a JSON object")
    contract = str(record.get("contract", ""))
    token_id = int(record.get("token_id", -1))
    returned_key = str(record.get("consolidation_key", ""))
    balance = int(record.get("balance", -1))
    boosted_tdh = Decimal(str(record.get("boosted_tdh", "-1")))
    api_tdh = Decimal(str(record.get("tdh", "-1")))
    api_raw_tdh = Decimal(str(record.get("tdh__raw", "-1")))
    if (
        contract.lower() != MEMES_CONTRACT.lower()
        or token_id != int(card_token_id)
        or returned_key.lower() != consolidation_key.lower()
        or balance < 0
        or boosted_tdh < 0
        or api_tdh < 0
        or api_raw_tdh < 0
    ):
        raise LiveSupportError("6529 card TDH record does not match the requested collector and card")

    tdh = int(api_tdh.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return {
        "tdh": tdh,
        "balance": balance,
        "apiBoostedTdh": float(boosted_tdh),
        "apiTdh": float(api_tdh),
        "apiRawTdh": float(api_raw_tdh),
    }


def probe_live_category(
    *,
    identity: str,
    category: str,
    card_token_id: int,
    api_base: str = DEFAULT_API_BASE,
    fetcher: Callable[[str], object] = fetch_json,
    workers: int = 4,
) -> dict[str, object]:
    """Exercise the live REP-to-card-TDH path without producing RSO support."""
    if workers < 1:
        raise LiveSupportError("workers must be positive")
    initial_tdh_state = fetch_tdh_state(api_base=api_base, fetcher=fetcher)
    target = fetch_profile(identity, api_base=api_base, fetcher=fetcher)
    expected = fetch_category_stat(
        identity,
        category,
        api_base=api_base,
        fetcher=fetcher,
    )
    raters = fetch_category_raters(
        identity,
        category,
        expected,
        api_base=api_base,
        fetcher=fetcher,
    )
    results = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                fetch_card_tdh,
                rater["wallets"],
                str(rater["consolidationKey"]),
                card_token_id,
                api_base=api_base,
                fetcher=fetcher,
            ): rater
            for rater in raters
        }
        for future in as_completed(futures):
            rater = futures[future]
            results.append({**rater, **future.result()})
    final_expected = fetch_category_stat(
        identity,
        category,
        api_base=api_base,
        fetcher=fetcher,
    )
    if final_expected != expected:
        raise LiveSupportError("probed REP category changed during collection")
    final_tdh_state = fetch_tdh_state(api_base=api_base, fetcher=fetcher)
    if final_tdh_state != initial_tdh_state:
        raise LiveSupportError("6529 TDH state advanced during collection; retry the probe")
    return {
        "identity": target,
        "category": category,
        "categoryRating": expected["rating"],
        "raterCount": len(results),
        "cardTokenId": int(card_token_id),
        "cardHolderCount": sum(int(record["balance"]) > 0 for record in results),
        "totalCardSpecificTdh": sum(int(record["tdh"]) for record in results),
        "tdhDate": initial_tdh_state["date"],
        "tdhBlock": initial_tdh_state["block"],
        "completedAt": utc_now(),
    }


def build_live_snapshot(
    *,
    identity: str,
    card_token_id: int,
    snapshot_date: str,
    api_base: str = DEFAULT_API_BASE,
    digest_nodes: Mapping[str, str] | None = None,
    fetcher: Callable[[str], object] = fetch_json,
    workers: int = 4,
    allow_empty: bool = False,
) -> dict[str, object]:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", snapshot_date):
        raise LiveSupportError("snapshot date must be YYYY-MM-DD")
    if int(card_token_id) < 0:
        raise LiveSupportError("card token id must not be negative")
    if workers < 1:
        raise LiveSupportError("workers must be positive")
    digest_nodes = digest_nodes or {}
    started_at = utc_now()
    initial_tdh_state = fetch_tdh_state(api_base=api_base, fetcher=fetcher)
    if snapshot_date != initial_tdh_state["date"]:
        raise LiveSupportError(
            f"snapshot date {snapshot_date} does not match latest 6529 TDH date "
            f"{initial_tdh_state['date']}"
        )
    target = fetch_profile(identity, api_base=api_base, fetcher=fetcher)
    initial_rep = fetch_rep_state(
        identity,
        api_base=api_base,
        digest_nodes=digest_nodes,
        fetcher=fetcher,
    )
    if not initial_rep and not allow_empty:
        raise LiveSupportError(f"6529 identity {identity!r} has no valid !node REP categories")

    raters_by_profile: dict[str, dict[str, object]] = {}
    for category, expected in sorted(initial_rep.items()):
        for rater in fetch_category_raters(
            identity,
            category,
            expected,
            api_base=api_base,
            fetcher=fetcher,
        ):
            profile_id = str(rater["profileId"])
            existing = raters_by_profile.get(profile_id)
            if existing is None:
                existing = {
                    key: rater[key]
                    for key in (
                        "profileId",
                        "handle",
                        "wallets",
                        "consolidationKey",
                        "profileTdh",
                    )
                }
                existing["nodeRep"] = {}
                existing["ratingsLastModified"] = {}
                raters_by_profile[profile_id] = existing
            elif existing["wallets"] != rater["wallets"]:
                raise LiveSupportError("one REP rater has inconsistent consolidated wallets")
            existing["nodeRep"][category] = int(rater["rating"])
            existing["ratingsLastModified"][category] = rater["lastModified"]

    tdh_by_profile = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        for profile_id, rater in raters_by_profile.items():
            future = pool.submit(
                fetch_card_tdh,
                rater["wallets"],
                str(rater["consolidationKey"]),
                card_token_id,
                api_base=api_base,
                fetcher=fetcher,
            )
            futures[future] = profile_id
        for future in as_completed(futures):
            tdh_by_profile[futures[future]] = future.result()

    final_rep = fetch_rep_state(
        identity,
        api_base=api_base,
        digest_nodes=digest_nodes,
        fetcher=fetcher,
    )
    if final_rep != initial_rep:
        raise LiveSupportError("RSO node REP changed during collection; retry the snapshot")
    final_tdh_state = fetch_tdh_state(api_base=api_base, fetcher=fetcher)
    if final_tdh_state != initial_tdh_state:
        raise LiveSupportError("6529 TDH state advanced during collection; retry the snapshot")

    identities = {}
    for profile_id, rater in sorted(raters_by_profile.items()):
        card_tdh = int(tdh_by_profile[profile_id]["tdh"])
        identities[profile_id] = {
            "handle": rater["handle"],
            "cardSpecificTdh": card_tdh,
            "cardBalance": int(tdh_by_profile[profile_id]["balance"]),
            "cardApiBoostedTdh": tdh_by_profile[profile_id]["apiBoostedTdh"],
            "cardApiTdh": tdh_by_profile[profile_id]["apiTdh"],
            "cardApiRawTdh": tdh_by_profile[profile_id]["apiRawTdh"],
            "accounts": rater["wallets"],
            "consolidationKey": rater["consolidationKey"],
            "profileTdh": rater["profileTdh"],
            "nodeRep": dict(sorted(rater["nodeRep"].items())),
            "ratingsLastModified": dict(sorted(rater["ratingsLastModified"].items())),
        }

    completed_at = utc_now()
    return {
        "schema": TDH_SUPPORT_SCHEMA,
        "date": snapshot_date,
        "cardTokenId": int(card_token_id),
        "targetIdentity": target,
        "repSnapshot": {
            "source": api_base,
            "startedAt": started_at,
            "completedAt": completed_at,
            "categories": initial_rep,
            "stabilityCheck": "category totals and contributor counts matched before and after collection",
        },
        "tdhSnapshot": {
            "source": api_base,
            "date": initial_tdh_state["date"],
            "block": initial_tdh_state["block"],
            "field": "tdh",
            "weighting": "unboosted card-specific TDH",
            "completedAt": completed_at,
            "stabilityCheck": "latest TDH date and block matched before and after collection",
        },
        "identities": identities,
        "operators": build_operator_support(identities, digest_nodes=digest_nodes),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identity", required=True, help="RSO 6529 handle or wallet address.")
    parser.add_argument("--card-token-id", required=True, type=int, help="Existing Meme card used for TDH weighting.")
    parser.add_argument(
        "--date",
        default=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        help="Support date, default current UTC date.",
    )
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--digest-roster", help="Node roster JSON used to resolve !node !id categories.")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--allow-empty", action="store_true")
    parser.add_argument(
        "--probe-category",
        help="Exercise all live REP and card-TDH reads for one exact category without writing a support snapshot.",
    )
    parser.add_argument("--out", help="Output path. Prints to stdout when omitted.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        digest_nodes = load_digest_nodes(args.digest_roster)
        live_fetcher = lambda url: fetch_json(url, timeout=args.timeout)
        if args.probe_category:
            output = probe_live_category(
                identity=args.identity,
                category=args.probe_category,
                card_token_id=args.card_token_id,
                api_base=args.api_base,
                fetcher=live_fetcher,
                workers=args.workers,
            )
        else:
            output = build_live_snapshot(
                identity=args.identity,
                card_token_id=args.card_token_id,
                snapshot_date=args.date,
                api_base=args.api_base,
                digest_nodes=digest_nodes,
                fetcher=live_fetcher,
                workers=args.workers,
                allow_empty=args.allow_empty,
            )
        encoded = json.dumps(output, indent=2, sort_keys=True) + "\n"
        if args.out:
            path = Path(args.out)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(encoded, encoding="utf-8")
            print(path)
        else:
            print(encoded, end="")
        return 0
    except (OSError, TypeError, ValueError) as exc:
        print(f"live_6529.py: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
