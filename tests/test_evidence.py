"""Failure-only HTML evidence capture and metadata sanitization contracts."""

import gzip
import hashlib
import os
import unittest
from dataclasses import FrozenInstanceError, asdict
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from sqlalchemy import func, inspect, select
from sqlalchemy.exc import DBAPIError

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
        self.assertEqual(evidence.expires_at - evidence.collected_at, timedelta(hours=672))
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
        expires_at = collected_at + timedelta(hours=672)

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

    def test_first_hourly_run_after_maximum_expiry_leaves_47_hour_margin(self):
        from roster_scout.evidence import purge_expired_evidence
        from roster_scout.models import FailureEvidence

        previous_purge = datetime(2026, 1, 1, tzinfo=UTC)
        collected_at = previous_purge + timedelta(microseconds=1)
        evidence_id = self._stored_evidence(
            suffix="worst-case",
            collected_at=collected_at,
        )

        last_run_before_expiry = previous_purge + timedelta(hours=672)
        next_allowed_run = previous_purge + timedelta(hours=673)
        self.assertEqual(
            purge_expired_evidence(self.Session, now=last_run_before_expiry),
            0,
        )
        self.assertLessEqual(
            next_allowed_run - collected_at,
            timedelta(hours=673),
        )
        self.assertEqual(
            purge_expired_evidence(self.Session, now=next_allowed_run),
            1,
        )
        with self.Session() as session:
            evidence = session.get(FailureEvidence, evidence_id)
            self.assertIsNone(evidence.compressed_html)
            self.assertEqual(evidence.purged_at, next_allowed_run)


class EvidencePurgePersistenceTests(PostgreSQLJobTestCase):
    def _failed_job(self, suffix):
        from roster_scout.jobs import accept_report_job
        from roster_scout.models import ReportJob

        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session,
            organization_id,
            report_id,
            f"purge-persistence-{suffix}",
            dispatch=None,
        )
        with self.Session.begin() as session:
            session.get(ReportJob, job.id).status = "failed"
        return job.id

    def _store(self, suffix, collected_at):
        from roster_scout.evidence import store_failure_evidence

        return store_failure_evidence(
            self.Session,
            self._failed_job(suffix),
            section="batting",
            source_url="https://stats.ncaa.org/team/123/stats?secret=hidden",
            html="<html>restricted failure evidence</html>",
            failure_category="source_blocked",
            collected_at=collected_at,
        )

    def test_default_and_permitted_shorter_retention_share_production_boundary(self):
        from roster_scout.models import FailureEvidence

        collected_at = datetime(2026, 1, 1, tzinfo=UTC)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FAILURE_EVIDENCE_RETENTION_DAYS", None)
            default_id = self._store("default-retention", collected_at)
        with patch.dict(
            os.environ,
            {"FAILURE_EVIDENCE_RETENTION_DAYS": "7"},
            clear=False,
        ):
            shorter_id = self._store("short-retention", collected_at)

        with self.Session() as session:
            default = session.get(FailureEvidence, default_id)
            shorter = session.get(FailureEvidence, shorter_id)
            self.assertEqual(
                default.expires_at - default.collected_at,
                timedelta(hours=672),
            )
            self.assertEqual(
                shorter.expires_at - shorter.collected_at,
                timedelta(hours=168),
            )

    def test_invalid_retention_fails_closed_without_storing_evidence(self):
        from roster_scout.evidence import (
            FailureEvidenceConfigurationError,
            store_failure_evidence,
        )
        from roster_scout.models import FailureEvidence

        job_id = self._failed_job("invalid-retention")
        for value in ("", "abc", "1.5", "0", "-1", "29"):
            with self.subTest(value=value), patch.dict(
                os.environ,
                {"FAILURE_EVIDENCE_RETENTION_DAYS": value},
                clear=False,
            ):
                with self.assertRaisesRegex(
                    FailureEvidenceConfigurationError,
                    "invalid failure evidence retention configuration",
                ):
                    store_failure_evidence(
                        self.Session,
                        job_id,
                        section="batting",
                        source_url="https://stats.ncaa.org/team/123/stats",
                        html="<html>must not persist</html>",
                        failure_category="source_blocked",
                    )

        with self.Session() as session:
            self.assertEqual(
                session.scalar(select(func.count()).select_from(FailureEvidence)),
                0,
            )

    def test_successful_purge_commits_bytes_and_singleton_health_together(self):
        from roster_scout.evidence import purge_expired_evidence
        from roster_scout.models import EvidencePurgeState, FailureEvidence

        expires_at = datetime(2026, 2, 1, tzinfo=UTC)
        evidence_id = self._store(
            "successful-transaction",
            expires_at - timedelta(hours=672),
        )
        self.assertEqual(purge_expired_evidence(self.Session, now=expires_at), 1)

        with self.Session() as session:
            evidence = session.get(FailureEvidence, evidence_id)
            health = session.get(EvidencePurgeState, 1)
            self.assertIsNone(evidence.compressed_html)
            self.assertEqual(evidence.purged_at, expires_at)
            self.assertEqual(health.last_success_at, expires_at)
            self.assertEqual(health.last_purged_count, 1)
            self.assertEqual(
                set(EvidencePurgeState.__table__.columns.keys()),
                {
                    "singleton_key",
                    "last_success_at",
                    "last_purged_count",
                    "updated_at",
                },
            )

    def test_failure_after_delete_rolls_back_bytes_and_prior_health(self):
        import roster_scout.evidence as evidence_service
        from roster_scout.models import EvidencePurgeState, FailureEvidence

        first_success = datetime(2026, 2, 1, tzinfo=UTC)
        self.assertEqual(
            evidence_service.purge_expired_evidence(
                self.Session,
                now=first_success,
            ),
            0,
        )
        evidence_id = self._store(
            "rolled-back-transaction",
            first_success - timedelta(hours=672),
        )

        with patch.object(
            evidence_service,
            "_upsert_evidence_purge_state",
            side_effect=RuntimeError("injected post-delete failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "injected post-delete failure"):
                evidence_service.purge_expired_evidence(
                    self.Session,
                    now=first_success + timedelta(hours=1),
                )

        with self.Session() as session:
            evidence = session.get(FailureEvidence, evidence_id)
            health = session.get(EvidencePurgeState, 1)
            self.assertIsNotNone(evidence.compressed_html)
            self.assertIsNone(evidence.purged_at)
            self.assertEqual(health.last_success_at, first_success)
            self.assertEqual(health.last_purged_count, 0)

    def test_lock_timeout_rolls_back_without_recording_success(self):
        import roster_scout.evidence as evidence_service
        from roster_scout.models import EvidencePurgeState, FailureEvidence

        expires_at = datetime(2026, 2, 1, tzinfo=UTC)
        evidence_id = self._store(
            "lock-timeout",
            expires_at - timedelta(hours=672),
        )
        blocker = self.Session()
        blocker.begin()
        blocker.scalar(
            select(FailureEvidence)
            .where(FailureEvidence.id == evidence_id)
            .with_for_update()
        )
        try:
            with patch.object(
                evidence_service,
                "EVIDENCE_PURGE_LOCK_TIMEOUT_MS",
                50,
            ):
                with self.assertRaises(DBAPIError):
                    evidence_service.purge_expired_evidence(
                        self.Session,
                        now=expires_at,
                    )
        finally:
            blocker.rollback()
            blocker.close()

        with self.Session() as session:
            evidence = session.get(FailureEvidence, evidence_id)
            self.assertIsNotNone(evidence.compressed_html)
            self.assertIsNone(evidence.purged_at)
            self.assertIsNone(session.get(EvidencePurgeState, 1))

    def test_revision_0009_fresh_install_downgrade_and_reupgrade(self):
        from alembic import command
        from tests.db_support import alembic_config

        config = alembic_config(self.database_url)
        command.downgrade(config, "0008")
        self.addCleanup(command.upgrade, config, "head")
        self.assertNotIn("evidence_purge_state", inspect(self.engine).get_table_names())

        command.upgrade(config, "0009")
        self.assertIn("evidence_purge_state", inspect(self.engine).get_table_names())
        command.downgrade(config, "0008")
        self.assertNotIn("evidence_purge_state", inspect(self.engine).get_table_names())
        command.upgrade(config, "head")

        command.downgrade(config, "base")
        command.upgrade(config, "head")
        self.assertIn("evidence_purge_state", inspect(self.engine).get_table_names())


class PersistedPurgeReadinessTests(PostgreSQLJobTestCase):
    def _store_expired_evidence(self, *, suffix, now):
        from roster_scout.evidence import store_failure_evidence
        from roster_scout.jobs import accept_report_job
        from roster_scout.models import ReportJob

        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session,
            organization_id,
            report_id,
            f"readiness-{suffix}",
            dispatch=None,
        )
        with self.Session.begin() as session:
            session.get(ReportJob, job.id).status = "failed"
        return store_failure_evidence(
            self.Session,
            job.id,
            section="pitching",
            source_url=(
                "https://operator:password@stats.ncaa.org/team/123/stats"
                "?token=secret#private"
            ),
            html="<html>customer-secret evidence bytes</html>",
            failure_category="source_blocked",
            collected_at=now - timedelta(hours=672),
        )

    def test_never_succeeded_is_blocked_and_result_is_frozen(self):
        from roster_scout.evidence import get_evidence_purge_readiness

        now = datetime(2026, 3, 1, tzinfo=UTC)
        result = get_evidence_purge_readiness(self.Session, now=now)

        self.assertFalse(result.ready)
        self.assertEqual(result.status, "never_succeeded")
        self.assertIsNone(result.last_success_at)
        self.assertEqual(result.last_purged_count, 0)
        self.assertEqual(result.expired_bytes_count, 0)
        with self.assertRaises(FrozenInstanceError):
            result.ready = True

    def test_exact_six_hour_boundary_is_fresh_and_next_microsecond_is_stale(self):
        from roster_scout.evidence import (
            get_evidence_purge_readiness,
            purge_expired_evidence,
        )

        success_at = datetime(2026, 3, 1, tzinfo=UTC)
        self.assertEqual(
            purge_expired_evidence(self.Session, now=success_at),
            0,
        )

        exact = get_evidence_purge_readiness(
            self.Session,
            now=success_at + timedelta(hours=6),
        )
        stale = get_evidence_purge_readiness(
            self.Session,
            now=success_at + timedelta(hours=6, microseconds=1),
        )
        self.assertTrue(exact.ready)
        self.assertEqual(exact.status, "fresh")
        self.assertFalse(stale.ready)
        self.assertEqual(stale.status, "stale")

    def test_expired_bytes_block_fresh_heartbeat_until_purge_clears_them(self):
        from roster_scout.evidence import (
            get_evidence_purge_readiness,
            purge_expired_evidence,
        )

        now = datetime(2026, 3, 1, tzinfo=UTC)
        purge_expired_evidence(self.Session, now=now - timedelta(hours=1))
        self._store_expired_evidence(suffix="outstanding", now=now)

        blocked = get_evidence_purge_readiness(self.Session, now=now)
        self.assertFalse(blocked.ready)
        self.assertEqual(blocked.status, "expired_bytes_present")
        self.assertEqual(blocked.expired_bytes_count, 1)

        self.assertEqual(purge_expired_evidence(self.Session, now=now), 1)
        cleared = get_evidence_purge_readiness(self.Session, now=now)
        self.assertTrue(cleared.ready)
        self.assertEqual(cleared.status, "fresh")
        self.assertEqual(cleared.last_purged_count, 1)
        self.assertEqual(cleared.expired_bytes_count, 0)

    def test_recreated_session_factory_observes_persisted_success(self):
        from roster_scout.database import create_session_factory
        from roster_scout.evidence import (
            get_evidence_purge_readiness,
            purge_expired_evidence,
        )

        success_at = datetime(2026, 3, 1, tzinfo=UTC)
        purge_expired_evidence(self.Session, now=success_at)
        restarted_session_factory = create_session_factory(self.engine)

        result = get_evidence_purge_readiness(
            restarted_session_factory,
            now=success_at + timedelta(hours=1),
        )
        self.assertTrue(result.ready)
        self.assertEqual(result.status, "fresh")
        self.assertEqual(result.last_success_at, success_at)

    def test_database_failure_is_sanitized_and_fails_closed(self):
        from roster_scout.evidence import get_evidence_purge_readiness

        class FailingSessionFactory:
            def __call__(self):
                raise RuntimeError(
                    "postgresql://user:password@private/customer-secret"
                )

        result = get_evidence_purge_readiness(
            FailingSessionFactory(),
            now=datetime(2026, 3, 1, tzinfo=UTC),
        )
        self.assertFalse(result.ready)
        self.assertEqual(result.status, "database_unavailable")
        serialized = " ".join(str(value) for value in asdict(result).values())
        for unsafe in (
            "password",
            "private",
            "customer-secret",
            "postgresql://",
            "<html>",
        ):
            self.assertNotIn(unsafe, serialized)

    def test_result_exposes_only_safe_health_fields(self):
        from roster_scout.evidence import get_evidence_purge_readiness

        result = get_evidence_purge_readiness(
            self.Session,
            now=datetime(2026, 3, 1, tzinfo=UTC),
        )
        self.assertEqual(
            set(asdict(result)),
            {
                "ready",
                "status",
                "last_success_at",
                "last_purged_count",
                "expired_bytes_count",
            },
        )


if __name__ == "__main__":
    unittest.main()
