#!/usr/bin/env python3
"""Check the public sweeper report for this node and manage a local alert issue."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from indexer.rso_profile import normalize_node_id as normalize_profile_node_id  # noqa: E402
from vendor.docchain.fetch import FetchError, read_limited, reject_private_host  # noqa: E402


ACTIONABLE_STATUSES = frozenset({"missing", "deferred", "failed", "error", "not_found"})
HEALTHY_STATUSES = frozenset({"submitted", "duplicate", "simulated"})
NON_ALERT_STATUSES = frozenset({"candidate", "not_backed", "not_sponsored_limit"})
DEFAULT_LABELS = ("rso-archive", "sweeper-alert")
MAX_REPORT_BYTES = int(os.environ.get("RSO_SWEEPER_REPORT_MAX_BYTES", str(1024 * 1024)))
MAX_GITHUB_BYTES = int(os.environ.get("RSO_SWEEPER_REPORT_GITHUB_MAX_BYTES", str(2 * 1024 * 1024)))


class ReportCheckError(ValueError):
    """Report check configuration or validation error."""


def main() -> int:
    try:
        args = parse_args()
        snapshot_date = normalize_snapshot_date(args.date)
        report_url = args.report_url or report_url_for(
            repo=args.report_repo,
            branch=args.report_branch,
            path_template=args.report_path_template,
            snapshot_date=snapshot_date,
        )
        report = fetch_report(report_url, timeout=args.timeout)
        node_id = normalize_node_id(args.node_id)
        status = classify_node_status(report, node_id=node_id)
        print(f"{snapshot_date} {node_id}: {status['status']}")
        if args.dry_run:
            print(json.dumps(status, indent=2, sort_keys=True))
            return 0
        issue_repo = args.issue_repo
        token = os.environ.get(args.github_token_env) or os.environ.get("GITHUB_TOKEN")
        if not issue_repo or not token:
            print("Issue alert skipped: issue repo or GitHub token is not configured.")
            return 0
        manage_alert_issue(
            issue_repo=issue_repo,
            token=token,
            node_id=node_id,
            snapshot_date=snapshot_date,
            report_url=report_url,
            status=status,
            labels=args.labels,
        )
        return 0
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            print(f"Sweeper report is not published yet: HTTP 404")
            return 0
        print(f"check_sweeper_report.py: HTTP {exc.code}: {exc.reason}", file=sys.stderr)
        return 2
    except (OSError, ReportCheckError, ValueError) as exc:
        print(f"check_sweeper_report.py: {exc}", file=sys.stderr)
        return 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check this node against a public RSO sweeper report.")
    parser.add_argument("--date", default=os.environ.get("RSO_SWEEPER_REPORT_DATE") or yesterday_utc())
    parser.add_argument("--node-id", default=os.environ.get("RSO_NODE_ID") or default_node_id())
    parser.add_argument("--report-url", default=os.environ.get("RSO_SWEEPER_REPORT_URL", ""))
    parser.add_argument("--report-repo", default=os.environ.get("RSO_SWEEPER_REPORT_REPO", "OMPub/RSO"))
    parser.add_argument("--report-branch", default=os.environ.get("RSO_SWEEPER_REPORT_BRANCH", "main"))
    parser.add_argument(
        "--report-path-template",
        default=os.environ.get("RSO_SWEEPER_REPORT_PATH_TEMPLATE", "reports/sweeper/{date}.json"),
    )
    parser.add_argument("--issue-repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--github-token-env", default="GH_TOKEN")
    parser.add_argument("--timeout", type=float, default=float(os.environ.get("RSO_SWEEPER_REPORT_TIMEOUT", "30")))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--labels", nargs="*", default=list(DEFAULT_LABELS))
    return parser.parse_args()


def yesterday_utc() -> str:
    return (datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat()


def normalize_snapshot_date(value: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ReportCheckError("report date must be YYYY-MM-DD")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ReportCheckError("report date must be a valid YYYY-MM-DD date") from exc
    return parsed.isoformat()


def default_node_id() -> str:
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if not repository:
        return ""
    return "github:" + repository


def report_url_for(*, repo: str, branch: str, path_template: str, snapshot_date: str) -> str:
    repo = normalize_github_repo(repo)
    path = path_template.format(date=snapshot_date).lstrip("/")
    if not path:
        raise ReportCheckError("report path template produced an empty path")
    return f"https://raw.githubusercontent.com/{repo}/{urllib.parse.quote(branch, safe='')}/{path}"


def fetch_report(url: str, *, timeout: float) -> dict[str, object]:
    validate_report_url(url)
    request = urllib.request.Request(url, headers={"user-agent": "rso-sweeper-report-check/1"})
    opener = urllib.request.build_opener(NoRedirectHandler)
    with opener.open(request, timeout=timeout) as response:
        raw = json.loads(read_limited(response, MAX_REPORT_BYTES, label="sweeper report").decode("utf-8"))
    if not isinstance(raw, dict):
        raise ReportCheckError("sweeper report must be a JSON object")
    return raw


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate_report_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise ReportCheckError("sweeper report URL must use HTTPS")
    host = (parsed.hostname or "").lower()
    if host != "raw.githubusercontent.com":
        raise ReportCheckError("sweeper report URL must use raw.githubusercontent.com")
    # the DNS/private-address check is the vendored SSRF guard
    try:
        reject_private_host(host, label="sweeper report URL")
    except FetchError as exc:
        raise ReportCheckError(str(exc)) from exc


def classify_node_status(report: Mapping[str, object], *, node_id: str) -> dict[str, object]:
    records = node_records(report, node_id=node_id)
    if not records:
        return {
            "status": "not_found",
            "actionable": True,
            "summary": "This node was not present in the public sweeper report.",
            "records": [],
        }
    statuses = [str(record.get("status", "")) for record in records if isinstance(record, Mapping)]
    if any(status in HEALTHY_STATUSES for status in statuses):
        return {
            "status": "healthy",
            "actionable": False,
            "summary": "The sweeper accepted or already had this node's attestation.",
            "records": records,
        }
    actionable = [status for status in statuses if status in ACTIONABLE_STATUSES]
    if actionable:
        return {
            "status": actionable[0],
            "actionable": True,
            "summary": "The sweeper reported a node condition that may need operator attention.",
            "records": records,
        }
    if all(status in NON_ALERT_STATUSES for status in statuses):
        return {
            "status": statuses[0] if statuses else "checked",
            "actionable": False,
            "summary": "The node was visible to the sweeper, with no operator action required.",
            "records": records,
        }
    return {
        "status": "checked",
        "actionable": False,
        "summary": "The node was present in the sweeper report.",
        "records": records,
    }


def node_records(report: Mapping[str, object], *, node_id: str) -> list[dict[str, object]]:
    normalized = normalize_node_id(node_id)
    records: list[dict[str, object]] = []
    if report.get("schema") == "rso-sweeper-date-report-v1":
        records.extend(records_for_date_report(report, normalized))
    else:
        dates = report.get("dates", [])
        if isinstance(dates, list):
            for date_report in dates:
                if isinstance(date_report, Mapping):
                    records.extend(records_for_date_report(date_report, normalized))
    return records


def records_for_date_report(report: Mapping[str, object], node_id: str) -> list[dict[str, object]]:
    operators = report.get("operators", [])
    if not isinstance(operators, list):
        return []
    records = []
    for record in operators:
        if not isinstance(record, Mapping):
            continue
        raw_node_id = record.get("nodeId")
        if not isinstance(raw_node_id, str) or not raw_node_id:
            continue
        if normalize_node_id(raw_node_id) == node_id:
            enriched = dict(record)
            enriched["date"] = report.get("date", "")
            records.append(enriched)
    return records


def manage_alert_issue(
    *,
    issue_repo: str,
    token: str,
    node_id: str,
    snapshot_date: str,
    report_url: str,
    status: Mapping[str, object],
    labels: list[str],
) -> None:
    issue_repo = normalize_github_repo(issue_repo)
    title = f"RSO sweeper alert for {node_id}"
    issue = find_open_issue(issue_repo=issue_repo, token=token, title=title)
    if status.get("actionable"):
        body = issue_body(
            node_id=node_id,
            snapshot_date=snapshot_date,
            report_url=report_url,
            status=status,
        )
        if issue is None:
            ensure_labels(issue_repo=issue_repo, token=token, labels=labels)
            create_issue(issue_repo=issue_repo, token=token, title=title, body=body, labels=labels)
            print(f"Opened sweeper alert issue: {title}")
        else:
            update_issue(issue_repo=issue_repo, token=token, number=int(issue["number"]), body=body)
            print(f"Updated sweeper alert issue: #{issue['number']}")
        return
    if issue is not None:
        comment = (
            f"Sweeper report for `{snapshot_date}` no longer requires node action.\n\n"
            f"Status: `{status.get('status')}`\n\n"
            f"Report: {report_url}"
        )
        add_issue_comment(issue_repo=issue_repo, token=token, number=int(issue["number"]), body=comment)
        close_issue(issue_repo=issue_repo, token=token, number=int(issue["number"]))
        print(f"Closed resolved sweeper alert issue: #{issue['number']}")


def issue_body(
    *,
    node_id: str,
    snapshot_date: str,
    report_url: str,
    status: Mapping[str, object],
) -> str:
    records = json.dumps(status.get("records", []), indent=2, sort_keys=True)
    fence = markdown_fence(records)
    return (
        f"The public RSO sweeper report flagged this node.\n\n"
        f"- Node: `{node_id}`\n"
        f"- Date: `{snapshot_date}`\n"
        f"- Status: `{status.get('status')}`\n"
        f"- Report: {report_url}\n\n"
        f"{status.get('summary', '')}\n\n"
        f"{fence}json\n{records}\n{fence}\n\n"
        f"This issue is maintained automatically by the node's sweeper report check workflow."
    )


def markdown_fence(content: str) -> str:
    longest = 0
    current = 0
    for char in content:
        if char == "`":
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return "`" * max(3, longest + 1)


def find_open_issue(
    *, issue_repo: str, token: str, title: str, max_pages: int = 20
) -> dict[str, object] | None:
    # Paginate the open issues. The canonical sweeper-alert issue is deduped by
    # title; without pagination it would fall off page 1 once the repo has >100
    # newer open issues, causing a duplicate alert to be opened and the real one
    # to never be updated/closed. Capped at max_pages (2000 issues) as a backstop.
    for page in range(1, max_pages + 1):
        issues = github_request(
            "GET",
            issue_repo,
            f"/issues?state=open&per_page=100&page={page}",
            token=token,
        )
        if not isinstance(issues, list):
            raise ReportCheckError("GitHub issues response must be an array")
        if not issues:
            return None
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            if "pull_request" in issue:
                continue
            if issue.get("title") == title:
                return issue
        if len(issues) < 100:
            return None
    return None


def ensure_labels(*, issue_repo: str, token: str, labels: list[str]) -> None:
    for label in labels:
        if not label:
            continue
        payload = {"name": label, "color": "0e8a16", "description": "RSO Archive sweeper alert"}
        try:
            github_request("POST", issue_repo, "/labels", token=token, payload=payload)
        except urllib.error.HTTPError as exc:
            if exc.code not in (422,):
                raise


def create_issue(*, issue_repo: str, token: str, title: str, body: str, labels: list[str]) -> None:
    payload = {"title": title, "body": body, "labels": [label for label in labels if label]}
    try:
        github_request("POST", issue_repo, "/issues", token=token, payload=payload)
    except urllib.error.HTTPError as exc:
        if labels and exc.code == 422:
            github_request("POST", issue_repo, "/issues", token=token, payload={"title": title, "body": body})
            return
        raise


def update_issue(*, issue_repo: str, token: str, number: int, body: str) -> None:
    github_request("PATCH", issue_repo, f"/issues/{number}", token=token, payload={"body": body})


def close_issue(*, issue_repo: str, token: str, number: int) -> None:
    github_request("PATCH", issue_repo, f"/issues/{number}", token=token, payload={"state": "closed"})


def add_issue_comment(*, issue_repo: str, token: str, number: int, body: str) -> None:
    github_request("POST", issue_repo, f"/issues/{number}/comments", token=token, payload={"body": body})


def github_request(
    method: str,
    repo: str,
    path: str,
    *,
    token: str,
    payload: Mapping[str, object] | None = None,
) -> object:
    url = f"https://api.github.com/repos/{repo}{path}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "rso-sweeper-report-check/1",
        "X-GitHub-Api-Version": "2022-11-28",
        "Authorization": "Bearer " + token,
    }
    data = None
    if payload is not None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    opener = urllib.request.build_opener(NoRedirectHandler)
    with opener.open(request, timeout=60) as response:
        body = read_limited(response, MAX_GITHUB_BYTES, label="GitHub API response")
    if not body:
        return None
    return json.loads(body.decode("utf-8"))


def normalize_github_repo(repo: str) -> str:
    text = repo.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", text):
        raise ReportCheckError("GitHub repository must be OWNER/REPO")
    owner, name = text.split("/", 1)
    if owner in (".", "..") or name in (".", ".."):
        raise ReportCheckError("GitHub repository must be OWNER/REPO")
    return text


def normalize_node_id(node_id: str) -> str:
    try:
        return normalize_profile_node_id(node_id)
    except ValueError as exc:
        raise ReportCheckError(str(exc)) from exc


if __name__ == "__main__":
    raise SystemExit(main())
