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

        self.assertEqual(raised.exception.category, "schema_validation")
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


class SafeSourceUrlTests(unittest.TestCase):
    def test_credentials_queries_fragments_and_invalid_authorities(self):
        from roster_scout.evidence import _safe_source_url

        cases = {
            "https://user:pass@example.com:8443/path?q=x#frag": (
                "https://example.com:8443/path"
            ),
            "https://user%40mail:pass%3Aword@example.com/path?token=secret#private": (
                "https://example.com/path"
            ),
            "http://192.0.2.10:8080/stats?q=1": "http://192.0.2.10:8080/stats",
            "https://[2001:db8::1]/stats#section": "https://[2001:db8::1]/stats",
            "https://[2001:db8::1]:8443/stats?q=1": (
                "https://[2001:db8::1]:8443/stats"
            ),
            "ftp://user:pass@example.com/file": None,
            "https:///missing-host": None,
            "https://example.com:not-a-port/path": None,
            "https://example.com:99999/path": None,
            "https://[broken/path": None,
            "not a URL": None,
        }
        for source_url, expected in cases.items():
            with self.subTest(source_url=source_url):
                self.assertEqual(_safe_source_url(source_url), expected)
                if expected is not None:
                    self.assertNotIn("@", expected)
                    self.assertNotIn("?", expected)
                    self.assertNotIn("#", expected)
                    self.assertNotIn("user", expected)
                    self.assertNotIn("pass", expected)


class SafeEvidenceMetadataTests(PostgreSQLJobTestCase):
    def test_persisted_failure_metadata_is_allowlisted(self):
        from roster_scout.evidence import store_failure_evidence
        from roster_scout.jobs import accept_report_job
        from roster_scout.models import FailureEvidence, ReportJob

        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session,
            organization_id,
            report_id,
            "safe-evidence-metadata",
            dispatch=None,
        )
        with self.Session.begin() as session:
            session.get(ReportJob, job.id).status = "blocked"
        collected_at = datetime(2026, 11, 9, 14, 30, tzinfo=UTC)
        evidence_id = store_failure_evidence(
            self.Session,
            job.id,
            section="batting",
            source_url=(
                "https://proxy-user:proxy-password@example.com:8443/stats"
                "?token=customer-secret#raw-fragment"
            ),
            html="<html>customer-secret raw failure body</html>",
            failure_category="source_blocked",
            collected_at=collected_at,
        )

        with self.Session() as session:
            evidence = session.get(FailureEvidence, evidence_id)
            metadata = {
                "id": evidence.id,
                "organization_id": evidence.organization_id,
                "job_id": evidence.job_id,
                "sha256": evidence.sha256,
                "section": evidence.section,
                "source_url": evidence.source_url,
                "failure_category": evidence.failure_category,
                "collected_at": evidence.collected_at,
                "expires_at": evidence.expires_at,
                "purged_at": evidence.purged_at,
                "created_at": evidence.created_at,
            }

        self.assertEqual(
            set(metadata),
            {
                "id",
                "organization_id",
                "job_id",
                "sha256",
                "section",
                "source_url",
                "failure_category",
                "collected_at",
                "expires_at",
                "purged_at",
                "created_at",
            },
        )
        self.assertEqual(metadata["source_url"], "https://example.com:8443/stats")
        serialized = " ".join(str(value) for value in metadata.values())
        for unsafe in (
            "proxy-user",
            "proxy-password",
            "customer-secret",
            "raw-fragment",
            "<html>",
            "?token=",
            "#",
        ):
            self.assertNotIn(unsafe, serialized)


class EvidencePurgeDeadlineTests(PostgreSQLJobTestCase):
    def _stored_evidence(self, *, suffix, collected_at):
        from roster_scout.evidence import store_failure_evidence
        from roster_scout.jobs import accept_report_job
        from roster_scout.models import ReportJob

        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session,
            organization_id,
            report_id,
            f"purge-deadline-{suffix}",
            dispatch=None,
        )
        with self.Session.begin() as session:
            session.get(ReportJob, job.id).status = "failed"
        evidence_id = store_failure_evidence(
            self.Session,
            job.id,
            section="pitching",
            source_url="https://stats.ncaa.org/team/123/stats",
            html="<html>restricted failure evidence</html>",
            failure_category="source_blocked",
            collected_at=collected_at,
        )
        return evidence_id

    def test_expiry_boundary_physically_deletes_bytes_and_preserves_metadata(self):
        from roster_scout.evidence import purge_expired_evidence
        from roster_scout.models import FailureEvidence

        collected_at = datetime(2026, 1, 1, tzinfo=UTC)
        evidence_id = self._stored_evidence(
            suffix="boundary",
            collected_at=collected_at,
        )
        expires_at = collected_at + timedelta(hours=696)

        self.assertEqual(
            purge_expired_evidence(
                self.Session,
                now=expires_at - timedelta(microseconds=1),
            ),
            0,
        )
        with self.Session() as session:
            before = session.get(FailureEvidence, evidence_id)
            self.assertIsNotNone(before.compressed_html)
            self.assertIsNone(before.purged_at)
            audit_before = {
                "id": before.id,
                "organization_id": before.organization_id,
                "job_id": before.job_id,
                "sha256": before.sha256,
                "section": before.section,
                "source_url": before.source_url,
                "failure_category": before.failure_category,
                "collected_at": before.collected_at,
                "expires_at": before.expires_at,
                "created_at": before.created_at,
            }

        self.assertEqual(
            purge_expired_evidence(self.Session, now=expires_at),
            1,
        )
        with self.Session() as session:
            after = session.get(FailureEvidence, evidence_id)
            self.assertIsNone(after.compressed_html)
            self.assertEqual(after.purged_at, expires_at)
            audit_after = {
                "id": after.id,
                "organization_id": after.organization_id,
                "job_id": after.job_id,
                "sha256": after.sha256,
                "section": after.section,
                "source_url": after.source_url,
                "failure_category": after.failure_category,
                "collected_at": after.collected_at,
                "expires_at": after.expires_at,
                "created_at": after.created_at,
            }
        self.assertEqual(audit_after, audit_before)

        self.assertEqual(
            purge_expired_evidence(
                self.Session,
                now=expires_at + timedelta(hours=1),
            ),
            0,
        )
        with self.Session() as session:
            repeated = session.get(FailureEvidence, evidence_id)
            self.assertEqual(repeated.purged_at, expires_at)

    def test_immediately_after_prior_purge_is_deleted_by_720_hours(self):
        from roster_scout.evidence import purge_expired_evidence
        from roster_scout.models import FailureEvidence

        previous_purge = datetime(2026, 1, 1, tzinfo=UTC)
        collected_at = previous_purge + timedelta(microseconds=1)
        evidence_id = self._stored_evidence(
            suffix="worst-case",
            collected_at=collected_at,
        )

        last_run_before_expiry = previous_purge + timedelta(hours=696)
        next_allowed_run = previous_purge + timedelta(hours=720)
        self.assertEqual(
            purge_expired_evidence(self.Session, now=last_run_before_expiry),
            0,
        )
        self.assertLessEqual(
            next_allowed_run - collected_at,
            timedelta(hours=720),
        )
        self.assertEqual(
            purge_expired_evidence(self.Session, now=next_allowed_run),
            1,
        )
        with self.Session() as session:
            evidence = session.get(FailureEvidence, evidence_id)
            self.assertIsNone(evidence.compressed_html)
            self.assertEqual(evidence.purged_at, next_allowed_run)


if __name__ == "__main__":
    unittest.main()
