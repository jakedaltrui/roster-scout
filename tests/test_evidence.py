"""Failure-only HTML evidence capture and metadata sanitization contracts."""

import gzip
import hashlib
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from sqlalchemy import func, select

from tests.test_report_jobs import PostgreSQLJobTestCase


class FailedCollectionEvidenceTests(PostgreSQLJobTestCase):
    def test_failed_collector_stores_bounded_sanitized_evidence(self):
        import roster_scraper as scraper
        from roster_scout.jobs import (
            DeterministicJobError,
            accept_report_job,
            collect_ncaa_sections,
            run_report_job,
        )
        from roster_scout.models import FailureEvidence, ReportJob

        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session,
            organization_id,
            report_id,
            "failed-collector-evidence",
            dispatch=None,
        )
        with self.Session.begin() as session:
            session.get(ReportJob, job.id).max_attempts = 1

        source_url = (
            "https://stats.ncaa.org/teams/123/season_to_date_stats"
            "?year_stat_category_id=bat-secret#private"
        )
        blocked_marker = "<html><table id='stat_grid'><thead><tr><th>Name</th></tr></thead>"
        oversized_html = blocked_marker + ("SENSITIVE-HTML" * 10000) + "</table></html>"
        team = {
            "team_id": "team",
            "team_name": "Tampa",
            "season_team_id": "123",
            "season_id": "season",
            "division": "2",
        }
        season = {
            "id": "season",
            "batting_id": "bat-secret",
            "pitching_id": "pit",
        }

        def failed_collector(job_id, *, cached_sections=frozenset()):
            return collect_ncaa_sections(
                self.Session, job_id, cached_sections=cached_sections
            )

        with patch.object(scraper, "_find_ncaa_team_by_season_id", return_value=team), patch.object(
            scraper, "_find_ncaa_season", return_value=season
        ), patch.object(
            scraper,
            "_fetch_ncaa_pages",
            return_value={"batting": oversized_html, "pitching": oversized_html},
        ):
            with self.assertRaises(DeterministicJobError) as raised:
                run_report_job(self.Session, job.id, collector=failed_collector)

        self.assertEqual(raised.exception.category, "retry_exhausted")
        with self.Session() as session:
            stored_job = session.get(ReportJob, job.id)
            evidence_rows = list(session.scalars(select(FailureEvidence)))

        self.assertEqual(stored_job.status, "failed")
        self.assertEqual(len(evidence_rows), 1)
        evidence = evidence_rows[0]
        raw_html = gzip.decompress(evidence.compressed_html)
        self.assertEqual(len(raw_html), scraper.FAILURE_EVIDENCE_MAX_BYTES)
        self.assertEqual(
            raw_html,
            oversized_html.encode("utf-8")[: scraper.FAILURE_EVIDENCE_MAX_BYTES],
        )
        self.assertEqual(evidence.sha256, hashlib.sha256(raw_html).hexdigest())
        self.assertEqual(evidence.section, "batting")
        self.assertEqual(evidence.failure_category, "schema_validation")
        self.assertEqual(
            evidence.source_url,
            "https://stats.ncaa.org/teams/123/season_to_date_stats",
        )
        self.assertEqual(evidence.expires_at - evidence.collected_at, timedelta(hours=696))
        for public_text in (
            str(raised.exception),
            stored_job.failure_message,
            stored_job.failure_category,
        ):
            self.assertNotIn("SENSITIVE-HTML", public_text or "")
            self.assertNotIn("bat-secret", public_text or "")
            self.assertNotIn("?", public_text or "")
            self.assertNotIn("#", public_text or "")

    def test_string_and_bytes_inputs_round_trip_to_identical_utf8(self):
        from roster_scout.evidence import store_failure_evidence
        from roster_scout.jobs import accept_report_job
        from roster_scout.models import FailureEvidence, ReportJob

        organization_id, report_id = self.fixture()
        html_text = "<html>Jos\N{LATIN SMALL LETTER E WITH ACUTE}</html>"
        collected_at = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
        evidence_ids = []
        for suffix, html in (("string", html_text), ("bytes", html_text.encode("utf-8"))):
            job = accept_report_job(
                self.Session,
                organization_id,
                report_id,
                f"evidence-{suffix}",
                dispatch=None,
            )
            with self.Session.begin() as session:
                session.get(ReportJob, job.id).status = "failed"
            evidence_ids.append(
                store_failure_evidence(
                    self.Session,
                    job.id,
                    section="batting",
                    source_url="https://stats.ncaa.org/batting",
                    html=html,
                    failure_category="source_blocked",
                    collected_at=collected_at,
                )
            )

        with self.Session() as session:
            evidence = [session.get(FailureEvidence, item) for item in evidence_ids]
            raw_values = [gzip.decompress(item.compressed_html) for item in evidence]
        self.assertEqual(raw_values, [html_text.encode("utf-8")] * 2)
        self.assertEqual(evidence[0].sha256, evidence[1].sha256)
        self.assertEqual(evidence[0].compressed_html, evidence[1].compressed_html)


if __name__ == "__main__":
    unittest.main()
