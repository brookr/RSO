import unittest
import io
from unittest.mock import patch

from sweeper.check_sweeper_report import (
    ReportCheckError,
    classify_node_status,
    issue_body,
    node_records,
    normalize_node_id,
    normalize_snapshot_date,
    read_limited,
    report_url_for,
    validate_report_url,
)


class SweeperReportCheckTest(unittest.TestCase):
    def test_report_url_uses_public_raw_github_path(self):
        self.assertEqual(
            report_url_for(
                repo="OMPub/RSO",
                branch="main",
                path_template="reports/sweeper/{date}.json",
                snapshot_date="2026-06-04",
            ),
            "https://raw.githubusercontent.com/OMPub/RSO/main/reports/sweeper/2026-06-04.json",
        )

    def test_report_url_validation_rejects_non_raw_github_hosts(self):
        with patch("sweeper.check_sweeper_report.socket.getaddrinfo", return_value=[(None, None, None, None, ("140.82.112.133", 443))]):
            validate_report_url("https://raw.githubusercontent.com/OMPub/RSO/main/report.json")
            with self.assertRaisesRegex(ReportCheckError, "raw.githubusercontent.com"):
                validate_report_url("https://example.com/report.json")
            with self.assertRaisesRegex(ReportCheckError, "HTTPS"):
                validate_report_url("http://raw.githubusercontent.com/OMPub/RSO/main/report.json")

    def test_node_id_normalization_defaults_github_scheme(self):
        self.assertEqual(normalize_node_id("Owner/RSO"), "github:owner/rso")
        self.assertEqual(normalize_node_id("GitHub:Owner/RSO"), "github:owner/rso")
        self.assertEqual(normalize_node_id("domain:node.example.com"), "domain:node.example.com")
        with self.assertRaisesRegex(ReportCheckError, "domain:hostname"):
            normalize_node_id("domain:node..example.com")
        with self.assertRaisesRegex(ReportCheckError, "GitHub node id"):
            normalize_node_id("github:../repo")

    def test_report_date_must_be_a_real_calendar_date(self):
        self.assertEqual(normalize_snapshot_date("2026-06-04"), "2026-06-04")
        for value in ("2026-06-04/other", "2026-02-30", "not-a-date"):
            with self.subTest(value=value):
                with self.assertRaises(ReportCheckError):
                    normalize_snapshot_date(value)

    def test_classify_submitted_node_as_healthy(self):
        status = classify_node_status(
            {
                "schema": "rso-sweeper-date-report-v1",
                "date": "2026-06-04",
                "operators": [
                    {
                        "nodeId": "github:owner/rso",
                        "status": "candidate",
                    },
                    {
                        "nodeId": "github:owner/rso",
                        "status": "submitted",
                    },
                ],
            },
            node_id="github:owner/rso",
        )

        self.assertEqual(status["status"], "healthy")
        self.assertFalse(status["actionable"])

    def test_classify_missing_node_as_actionable(self):
        status = classify_node_status(
            {
                "schema": "rso-sweeper-date-report-v1",
                "date": "2026-06-04",
                "operators": [
                    {
                        "nodeId": "github:owner/rso",
                        "status": "missing",
                        "error": "HTTP 404",
                    }
                ],
            },
            node_id="github:owner/rso",
        )

        self.assertEqual(status["status"], "missing")
        self.assertTrue(status["actionable"])

    def test_classify_not_backed_node_as_non_actionable(self):
        status = classify_node_status(
            {
                "schema": "rso-sweeper-date-report-v1",
                "date": "2026-06-04",
                "operators": [
                    {
                        "nodeId": "github:owner/rso",
                        "status": "not_backed",
                    }
                ],
            },
            node_id="github:owner/rso",
        )

        self.assertEqual(status["status"], "not_backed")
        self.assertFalse(status["actionable"])

    def test_classify_absent_node_as_actionable(self):
        status = classify_node_status(
            {
                "schema": "rso-sweeper-date-report-v1",
                "date": "2026-06-04",
                "operators": [],
            },
            node_id="github:owner/rso",
        )

        self.assertEqual(status["status"], "not_found")
        self.assertTrue(status["actionable"])

    def test_node_records_support_aggregate_report(self):
        records = node_records(
            {
                "schema": "rso-sweeper-report-v1",
                "dates": [
                    {
                        "date": "2026-06-04",
                        "operators": [
                            {"nodeId": "github:owner/rso", "status": "deferred"},
                            {"nodeId": "github:other/rso", "status": "submitted"},
                        ],
                    }
                ],
            },
            node_id="github:owner/rso",
        )

        self.assertEqual(records, [{"nodeId": "github:owner/rso", "status": "deferred", "date": "2026-06-04"}])

    def test_issue_body_includes_report_records(self):
        body = issue_body(
            node_id="github:owner/rso",
            snapshot_date="2026-06-04",
            report_url="https://example.test/report.json",
            status={
                "status": "missing",
                "summary": "Missing signed artifact.",
                "records": [{"nodeId": "github:owner/rso", "status": "missing"}],
            },
        )

        self.assertIn("github:owner/rso", body)
        self.assertIn("https://example.test/report.json", body)
        self.assertIn('"status": "missing"', body)

    def test_issue_body_uses_longer_fence_for_backticks_in_report(self):
        body = issue_body(
            node_id="github:owner/rso",
            snapshot_date="2026-06-04",
            report_url="https://raw.githubusercontent.com/OMPub/RSO/main/report.json",
            status={
                "status": "deferred",
                "summary": "Problem.",
                "records": [{"nodeId": "github:owner/rso", "error": "bad ``` fence"}],
            },
        )

        self.assertIn("````json", body)
        self.assertIn("bad ``` fence", body)

    def test_read_limited_rejects_non_positive_limit(self):
        with self.assertRaisesRegex(ReportCheckError, "positive"):
            read_limited(io.BytesIO(b"x"), 0, label="report")


if __name__ == "__main__":
    unittest.main()
