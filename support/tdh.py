"""Signed 6529 REP allocation rules for RSO node support snapshots."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping


TDH_SUPPORT_SCHEMA = "rso-tdh-support-snapshot-v1"
SUPPORT_FIELDS = (
    "positiveBackingTdh",
    "negativeBackingTdh",
    "netBackingTdh",
    "usableBackingTdh",
)

_GITHUB_PART = re.compile(r"[a-z0-9_.]+")
_DOMAIN = re.compile(
    r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9]*[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9]*[a-z0-9])?"
)
_DIGEST = re.compile(r"[0-9a-f]{32}")


def normalize_support_record(record: object) -> dict[str, int]:
    """Validate and normalize one v1 signed node-support record."""
    if not isinstance(record, Mapping):
        raise ValueError("node support must be a JSON object")
    missing = [field for field in SUPPORT_FIELDS if field not in record]
    if missing:
        raise ValueError("node support requires " + ", ".join(missing))
    values = {field: int(record[field]) for field in SUPPORT_FIELDS}
    if values["positiveBackingTdh"] < 0:
        raise ValueError("positiveBackingTdh must not be negative")
    if values["negativeBackingTdh"] < 0:
        raise ValueError("negativeBackingTdh must not be negative")
    expected_net = values["positiveBackingTdh"] - values["negativeBackingTdh"]
    if values["netBackingTdh"] != expected_net:
        raise ValueError("netBackingTdh must equal positiveBackingTdh - negativeBackingTdh")
    expected_usable = max(expected_net, 0)
    if values["usableBackingTdh"] != expected_usable:
        raise ValueError("usableBackingTdh must equal max(netBackingTdh, 0)")
    return values


def node_id_digest(node_id: str) -> str:
    """Return the REP-safe 128-bit display prefix for a canonical node id."""
    return hashlib.sha256(node_id.encode("utf-8")).hexdigest()[:32]


def parse_node_category(
    category: str,
    *,
    digest_nodes: Mapping[str, str] | None = None,
) -> str:
    """Resolve one REP category command to a canonical node id, or ``""``."""
    tokens = category.strip().split()
    if len(tokens) != 3 or tokens[0].lower() != "!node":
        return ""
    command = tokens[1].lower()
    argument = tokens[2].lower()
    if command == "!github":
        if "." not in argument:
            return ""
        owner, repo = argument.split(".", 1)
        if not owner or not repo or not _GITHUB_PART.fullmatch(owner) or not _GITHUB_PART.fullmatch(repo):
            return ""
        return f"github:{owner}/{repo}"
    if command == "!domain":
        if not _DOMAIN.fullmatch(argument):
            return ""
        return f"domain:{argument}"
    if command != "!id" or not _DIGEST.fullmatch(argument):
        return ""
    node_id = str((digest_nodes or {}).get(argument, "")).lower()
    if not node_id or node_id_digest(node_id) != argument:
        return ""
    return node_id


def allocate_identity_tdh(
    card_specific_tdh: int,
    rep_by_node: Mapping[str, int],
) -> dict[str, int]:
    """Allocate one identity's TDH by signed REP using largest remainders."""
    tdh = int(card_specific_tdh)
    if tdh < 0:
        raise ValueError("cardSpecificTdh must not be negative")
    combined = {
        str(node_id): int(rep)
        for node_id, rep in rep_by_node.items()
        if int(rep) != 0
    }
    denominator = sum(abs(rep) for rep in combined.values())
    if tdh == 0 or denominator == 0:
        return {}

    allocations: dict[str, int] = {}
    remainders = []
    allocated = 0
    for node_id in sorted(combined):
        rep = combined[node_id]
        numerator = tdh * abs(rep)
        amount, remainder = divmod(numerator, denominator)
        allocations[node_id] = amount
        allocated += amount
        remainders.append((-remainder, node_id))
    for _, node_id in sorted(remainders)[: tdh - allocated]:
        allocations[node_id] += 1
    return {
        node_id: amount if combined[node_id] > 0 else -amount
        for node_id, amount in allocations.items()
        if amount
    }


def build_operator_support(
    identities: Mapping[str, object],
    *,
    digest_nodes: Mapping[str, str] | None = None,
) -> dict[str, dict[str, int]]:
    """Build deterministic node totals from identity TDH and signed REP categories.

    Each identity record contains ``cardSpecificTdh`` and a ``nodeRep`` mapping
    from REP category text to its current signed REP balance.
    """
    totals: dict[str, dict[str, object]] = {}
    for identity, raw_record in sorted(identities.items()):
        if not isinstance(raw_record, Mapping):
            raise ValueError("identity support records must be JSON objects")
        tdh = int(raw_record.get("cardSpecificTdh", 0))
        node_rep = raw_record.get("nodeRep", {})
        if not isinstance(node_rep, Mapping):
            raise ValueError("identity nodeRep must be a JSON object")
        rep_by_node: dict[str, int] = {}
        for category, raw_rep in node_rep.items():
            node_id = parse_node_category(str(category), digest_nodes=digest_nodes)
            if not node_id:
                continue
            rep_by_node[node_id] = rep_by_node.get(node_id, 0) + int(raw_rep)
        for node_id, allocation in allocate_identity_tdh(tdh, rep_by_node).items():
            total = totals.setdefault(
                node_id,
                {"positive": 0, "negative": 0, "backers": set(), "positiveBackers": set(), "negativeBackers": set()},
            )
            total["backers"].add(identity)
            if allocation > 0:
                total["positive"] += allocation
                total["positiveBackers"].add(identity)
            else:
                total["negative"] += -allocation
                total["negativeBackers"].add(identity)

    records: dict[str, dict[str, int]] = {}
    for node_id, total in totals.items():
        positive = int(total["positive"])
        negative = int(total["negative"])
        net = positive - negative
        records[node_id] = {
            "positiveBackingTdh": positive,
            "negativeBackingTdh": negative,
            "netBackingTdh": net,
            "usableBackingTdh": max(net, 0),
            "backerCount": len(total["backers"]),
            "positiveBackerCount": len(total["positiveBackers"]),
            "negativeBackerCount": len(total["negativeBackers"]),
            "rank": 0,
        }
    ranked = sorted(
        records,
        key=lambda node_id: (
            -records[node_id]["usableBackingTdh"],
            -records[node_id]["netBackingTdh"],
            node_id,
        ),
    )
    for rank, node_id in enumerate(ranked, start=1):
        records[node_id]["rank"] = rank
    return dict(sorted(records.items()))
