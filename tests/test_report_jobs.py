"""Durable report-job, redelivery, cache, and evidence-retention contract."""

import inspect as python_inspect
import unittest
import uuid
from datetime import UTC, datetime
from unittest.mock import Mock

from sqlalchemy import func, select

from tests.db_support import clean_database, require_test_database_url, upgrade_database


class PostgreSQLJobTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database_url = require_test_database_url()
        upgrade_database(cls.database_url)
        from roster_scout.database import create_session_factory, get_engine

        cls.engine = get_engine(cls.database_url)
        cls.Session = create_session_factory(cls.engine)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        clean_database(self.database_url)

    def fixture(self):
        from roster_scout.models import Organization, Report, Season, Team, TeamSeason

        with self.Session.begin() as session:
            organization = Organization(name="Axis Baseball")
            team = Team(source="ncaa", source_team_id=str(uuid.uuid4()), name="Tampa")
            season = Season(source="ncaa", source_season_id=str(uuid.uuid4()), year=2026)
            session.add_all([organization, team, season])
            session.flush()
            team_season = TeamSeason(
                team_id=team.id,
                season_id=season.id,
                source_season_team_id=str(uuid.uuid4()),
                division="2",
                batting_stat_category_id="bat",
                pitching_stat_category_id="pit",
            )
            session.add(team_season)
            session.flush()
            report = Report(
                organization_id=organization.id,
                team_season_id=team_season.id,
                name="Tampa 2026",
            )
            session.add(report)
            session.flush()
            return organization.id, report.id


class JobLifecycleTests(PostgreSQLJobTestCase):
    def test_job_model_supports_all_required_durable_states(self):
        from roster_scout.enums import JOB_STATUSES

        for state in ("queued", "running", "retrying", "ready", "partial", "failed"):
            self.assertIn(state, JOB_STATUSES)

    def test_duplicate_active_request_returns_same_job_and_dispatches_once(self):
        from roster_scout.jobs import accept_report_job

        organization_id, report_id = self.fixture()
        dispatch = Mock(return_value=Mock(id="message-1"))
        first = accept_report_job(
            self.Session, organization_id, report_id, "same-refresh", dispatch=dispatch
        )
        second = accept_report_job(
            self.Session, organization_id, report_id, "same-refresh", dispatch=dispatch
        )
        self.assertEqual(first.id, second.id)
        self.assertEqual(dispatch.call_count, 1)

    def test_broker_failure_leaves_one_queued_job_for_recoverable_dispatch(self):
        from roster_scout.jobs import accept_report_job
        from roster_scout.models import ReportJob

        organization_id, report_id = self.fixture()
        failing_dispatch = Mock(side_effect=ConnectionError("broker unavailable"))
        first = accept_report_job(
            self.Session, organization_id, report_id, "recoverable", dispatch=failing_dispatch
        )
        with self.Session() as session:
            stored = session.get(ReportJob, first.id)
            self.assertEqual(stored.status, "queued")
            self.assertIsNone(stored.enqueued_at)
        second = accept_report_job(
            self.Session,
            organization_id,
            report_id,
            "recoverable",
            dispatch=Mock(return_value=Mock(id="message-2")),
        )
        self.assertEqual(first.id, second.id)
        with self.Session() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(ReportJob)), 1)
            self.assertIsNotNone(session.get(ReportJob, first.id).enqueued_at)

    def test_retryable_and_deterministic_failures_have_distinct_terminal_behavior(self):
        from roster_scout.jobs import (
            DeterministicJobError,
            RetryableJobError,
            accept_report_job,
            run_report_job,
        )
        from roster_scout.models import ReportJob

        organization_id, report_id = self.fixture()
        transient = accept_report_job(
            self.Session, organization_id, report_id, "transient", dispatch=None
        )
        with self.assertRaises(RetryableJobError):
            run_report_job(
                self.Session,
                transient.id,
                collector=Mock(side_effect=ConnectionError("ncaa timeout secret-token")),
            )
        with self.Session() as session:
            self.assertEqual(session.get(ReportJob, transient.id).status, "retrying")

        terminal = accept_report_job(
            self.Session, organization_id, report_id, "terminal", dispatch=None
        )
        with self.assertRaises(DeterministicJobError):
            run_report_job(
                self.Session,
                terminal.id,
                collector=Mock(side_effect=ValueError("schema invalid secret-token")),
            )
        with self.Session() as session:
            failed = session.get(ReportJob, terminal.id)
            self.assertEqual(failed.status, "failed")
            self.assertNotIn("secret-token", failed.failure_message or "")

    def test_celery_delivery_is_json_late_acked_bounded_and_resultless(self):
        from roster_scout.celery_app import celery_app
        from roster_scout.tasks import build_report_job

        self.assertEqual(tuple(celery_app.conf.accept_content), ("json",))
        self.assertEqual(celery_app.conf.task_serializer, "json")
        self.assertTrue(celery_app.conf.task_acks_late)
        self.assertTrue(celery_app.conf.task_reject_on_worker_lost)
        self.assertEqual(celery_app.conf.worker_prefetch_multiplier, 1)
        self.assertTrue(celery_app.conf.task_ignore_result)
        self.assertTrue(build_report_job.ignore_result)
        self.assertEqual(build_report_job.max_retries, 2)
        self.assertTrue(build_report_job.retry_backoff)
        self.assertTrue(build_report_job.retry_jitter)


class PublicationTests(PostgreSQLJobTestCase):
    @staticmethod
    def successful_sections(collected_at=None, *, supplemental_failed=False):
        from roster_scout.jobs import CollectedSection

        collected_at = collected_at or datetime.now(UTC)
        sections = [
            CollectedSection(
                name=name,
                critical=True,
                rows=(),
                verified_empty=True,
                source_url=f"https://stats.ncaa.org/{name}",
                source_team_id="team",
                source_season_id="season",
                source_season_team_id="team-season",
                source_stat_category_id=name,
                collected_at=collected_at,
                parser_version="parser-1",
                schema_version="schema-1",
            )
            for name in ("batting", "pitching")
        ]
        if supplemental_failed:
            sections.append(CollectedSection.failed("fielding", critical=False, category="unavailable"))
        return sections

    def test_duplicate_delivery_and_terminal_replay_publish_exactly_one_snapshot(self):
        from roster_scout.jobs import accept_report_job, run_report_job
        from roster_scout.models import ReportSnapshot

        organization_id, report_id = self.fixture()
        job = accept_report_job(self.Session, organization_id, report_id, "once", dispatch=None)
        first = run_report_job(
            self.Session, job.id, collector=Mock(return_value=self.successful_sections())
        )
        second = run_report_job(
            self.Session,
            job.id,
            collector=Mock(side_effect=AssertionError("terminal replay collected again")),
        )
        self.assertEqual(first.id, second.id)
        with self.Session() as session:
            count = session.scalar(
                select(func.count()).select_from(ReportSnapshot).where(
                    ReportSnapshot.report_id == report_id
                )
            )
            self.assertEqual(count, 1)

    def test_supplemental_failure_publishes_partial_without_changing_artifact_state(self):
        from roster_scout.jobs import accept_report_job, run_report_job
        from roster_scout.models import ReportArtifact

        organization_id, report_id = self.fixture()
        job = accept_report_job(self.Session, organization_id, report_id, "partial", dispatch=None)
        snapshot = run_report_job(
            self.Session,
            job.id,
            collector=Mock(return_value=self.successful_sections(supplemental_failed=True)),
        )
        self.assertEqual(snapshot.status, "partial")
        with self.Session.begin() as session:
            artifact = ReportArtifact(
                organization_id=organization_id,
                snapshot_id=snapshot.id,
                artifact_type="xlsx",
                status="pending",
            )
            session.add(artifact)
        with self.Session() as session:
            self.assertEqual(session.scalar(select(ReportArtifact.status)), "pending")

    def test_same_job_cache_reuse_preserves_source_collection_time(self):
        from roster_scout.jobs import accept_report_job, run_report_job
        from roster_scout.models import ReportSection

        organization_id, report_id = self.fixture()
        collected_at = datetime(2026, 10, 31, 18, 0, tzinfo=UTC)
        job = accept_report_job(self.Session, organization_id, report_id, "cache", dispatch=None)
        run_report_job(
            self.Session,
            job.id,
            collector=Mock(return_value=self.successful_sections(collected_at)),
        )
        with self.Session() as session:
            sections = list(session.scalars(select(ReportSection)))
            self.assertEqual({section.collected_at for section in sections}, {collected_at})


class RetentionAndCacheTests(PostgreSQLJobTestCase):
    def test_evidence_is_failure_only_compressed_and_purges_bytes_not_metadata(self):
        from roster_scout.evidence import purge_expired_evidence, store_failure_evidence
        from roster_scout.jobs import accept_report_job
        from roster_scout.models import FailureEvidence, ReportJob

        organization_id, report_id = self.fixture()
        job = accept_report_job(self.Session, organization_id, report_id, "failed-html", dispatch=None)
        with self.Session.begin() as session:
            stored_job = session.get(ReportJob, job.id)
            stored_job.status = "failed"
        collected_at = datetime(2026, 1, 1, tzinfo=UTC)
        evidence_id = store_failure_evidence(
            self.Session,
            job.id,
            section="batting",
            source_url="https://stats.ncaa.org/blocked",
            html=b"<html>blocked</html>",
            failure_category="source_blocked",
            collected_at=collected_at,
        )
        with self.Session() as session:
            evidence = session.get(FailureEvidence, evidence_id)
            self.assertNotEqual(evidence.compressed_html, b"<html>blocked</html>")
            checksum = evidence.sha256
        self.assertEqual(purge_expired_evidence(self.Session, now=datetime(2026, 2, 1, tzinfo=UTC)), 1)
        with self.Session() as session:
            evidence = session.get(FailureEvidence, evidence_id)
            self.assertIsNone(evidence.compressed_html)
            self.assertEqual(evidence.sha256, checksum)
            self.assertIsNotNone(evidence.purged_at)

    def test_celery_task_has_only_job_id_application_argument(self):
        from roster_scout.tasks import build_report_job

        parameters = list(python_inspect.signature(build_report_job.run).parameters)
        self.assertEqual(parameters, ["job_id"])

    def test_successful_job_cannot_store_html_evidence(self):
        from roster_scout.evidence import store_failure_evidence
        from roster_scout.jobs import accept_report_job
        from roster_scout.models import ReportJob

        organization_id, report_id = self.fixture()
        job = accept_report_job(self.Session, organization_id, report_id, "success-html", dispatch=None)
        with self.Session.begin() as session:
            session.get(ReportJob, job.id).status = "ready"
        with self.assertRaisesRegex(ValueError, "failed or blocked"):
            store_failure_evidence(
                self.Session,
                job.id,
                section="batting",
                source_url="https://stats.ncaa.org/success?token=secret",
                html=b"<html>success</html>",
                failure_category="unexpected",
            )

    def test_worker_concurrency_is_strictly_bounded_and_cache_is_ephemeral(self):
        from scripts.run_worker import WORKER_CACHE_DIR, parse_concurrency, worker_command

        self.assertEqual(parse_concurrency("1"), 1)
        self.assertEqual(parse_concurrency("2"), 2)
        for value in ("0", "3", "many"):
            with self.assertRaises(ValueError):
                parse_concurrency(value)
        command = worker_command({"BROWSERLESS_MAX_SESSIONS": "2"})
        self.assertEqual(command[-2:], ["--concurrency", "2"])
        self.assertEqual(WORKER_CACHE_DIR, "/tmp/roster-scout-worker")

    def test_cache_key_includes_job_source_schema_and_parser_identity(self):
        from roster_scout.jobs import SectionCacheKey

        fields = set(SectionCacheKey.__dataclass_fields__)
        self.assertEqual(
            fields,
            {
                "job_id",
                "section",
                "source_url",
                "source_team_id",
                "source_season_id",
                "source_season_team_id",
                "source_stat_category_id",
                "schema_version",
                "parser_version",
            },
        )


if __name__ == "__main__":
    unittest.main()
