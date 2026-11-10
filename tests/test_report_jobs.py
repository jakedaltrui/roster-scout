"""Durable report-job, redelivery, cache, and evidence-retention contract."""

import gzip
import inspect as python_inspect
import threading
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

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


class TransactionalOutboxTests(PostgreSQLJobTestCase):
    def test_acceptance_and_dispatch_intent_roll_back_together(self):
        from roster_scout.jobs import create_or_get_job
        from roster_scout.models import ReportJob, ReportJobDispatch

        organization_id, report_id = self.fixture()
        with self.assertRaisesRegex(RuntimeError, "forced rollback"):
            with self.Session.begin() as session:
                create_or_get_job(session, organization_id, report_id, "rollback")
                raise RuntimeError("forced rollback")

        with self.Session() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(ReportJob)), 0)
            self.assertEqual(
                session.scalar(select(func.count()).select_from(ReportJobDispatch)), 0
            )

    def test_barrier_released_acceptance_converges_on_one_job_and_intent(self):
        from roster_scout.database import create_session_factory, get_engine
        from roster_scout.jobs import accept_report_job
        from roster_scout.models import ReportJob, ReportJobDispatch

        organization_id, report_id = self.fixture()
        ready = threading.Barrier(2)
        outcomes = []
        errors = []

        def accept_once():
            engine = get_engine(self.database_url)
            Session = create_session_factory(engine)
            try:
                ready.wait(timeout=5)
                outcomes.append(
                    accept_report_job(
                        Session,
                        organization_id,
                        report_id,
                        "acceptance-race",
                        dispatch=None,
                    ).id
                )
            except Exception as exc:
                errors.append(exc)
            finally:
                engine.dispose()

        threads = [threading.Thread(target=accept_once) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len(set(outcomes)), 1)
        with self.Session() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(ReportJob)), 1)
            intents = list(session.scalars(select(ReportJobDispatch)))
            self.assertEqual(len(intents), 1)
            self.assertEqual(intents[0].job_id, outcomes[0])
            self.assertEqual(intents[0].status, "pending")

    def test_barrier_released_publishers_claim_once_and_expired_lease_reclaims(self):
        from roster_scout.database import create_session_factory, get_engine
        from roster_scout.jobs import accept_report_job
        from roster_scout.models import ReportJobDispatch
        from roster_scout.repositories import claim_report_dispatches

        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session, organization_id, report_id, "claim-race", dispatch=None
        )
        now = datetime.now(UTC) + timedelta(seconds=1)
        ready = threading.Barrier(2)
        outcomes = []
        errors = []

        def claim_once():
            engine = get_engine(self.database_url)
            Session = create_session_factory(engine)
            try:
                with Session.begin() as session:
                    ready.wait(timeout=5)
                    outcomes.append(
                        claim_report_dispatches(
                            session,
                            now=now,
                            limit=1,
                            lease_duration=timedelta(seconds=30),
                        )
                    )
            except Exception as exc:
                errors.append(exc)
            finally:
                engine.dispose()

        threads = [threading.Thread(target=claim_once) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        leases = [lease for claimed in outcomes for lease in claimed]
        self.assertEqual(len(leases), 1)
        self.assertEqual(leases[0].job_id, job.id)
        self.assertIsNotNone(leases[0].lease_token)

        with self.Session.begin() as session:
            reclaimed = claim_report_dispatches(
                session,
                now=now + timedelta(seconds=31),
                limit=1,
                lease_duration=timedelta(seconds=30),
            )
        self.assertEqual(len(reclaimed), 1)
        self.assertEqual(reclaimed[0].dispatch_id, leases[0].dispatch_id)
        self.assertNotEqual(reclaimed[0].lease_token, leases[0].lease_token)
        with self.Session() as session:
            intent = session.get(ReportJobDispatch, leases[0].dispatch_id)
            self.assertEqual(intent.publish_attempts, 2)

    def test_revision_0005_downgrades_and_reupgrades_cleanly(self):
        from alembic import command
        from sqlalchemy import inspect

        from tests.db_support import alembic_config

        command.downgrade(alembic_config(self.database_url), "0004")
        self.assertNotIn("report_job_dispatch_outbox", inspect(self.engine).get_table_names())
        command.upgrade(alembic_config(self.database_url), "0005")
        self.assertIn("report_job_dispatch_outbox", inspect(self.engine).get_table_names())
        command.downgrade(alembic_config(self.database_url), "0004")
        command.upgrade(alembic_config(self.database_url), "head")
        command.check(alembic_config(self.database_url))


class AttemptGenerationSchemaTests(PostgreSQLJobTestCase):
    def test_new_job_and_initial_intent_start_at_generation_one(self):
        from roster_scout.jobs import accept_report_job
        from roster_scout.models import ReportJob, ReportJobDispatch

        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session,
            organization_id,
            report_id,
            "generation-defaults",
            dispatch=None,
        )

        with self.Session() as session:
            stored = session.get(ReportJob, job.id)
            intent = session.scalar(
                select(ReportJobDispatch).where(ReportJobDispatch.job_id == job.id)
            )
            self.assertEqual(stored.dispatch_generation, 1)
            self.assertEqual(stored.started_generation, 0)
            self.assertEqual(intent.generation, 1)

    def test_bounded_retry_delay_has_exact_cap_and_injected_jitter(self):
        from roster_scout.jobs import bounded_retry_delay

        self.assertEqual(bounded_retry_delay(1, jitter=lambda _base: 0), 1)
        self.assertEqual(bounded_retry_delay(4, jitter=lambda _base: 3), 11)
        self.assertEqual(bounded_retry_delay(10, jitter=lambda _base: 0), 300)
        self.assertEqual(bounded_retry_delay(8, jitter=lambda _base: 500), 300)

    def test_retryable_failure_advances_once_and_persists_due_generation(self):
        from roster_scout.jobs import (
            RetryableJobError,
            _record_failure,
            accept_report_job,
        )
        from roster_scout.models import ReportJob, ReportJobDispatch

        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session,
            organization_id,
            report_id,
            "generation-retry",
            dispatch=None,
        )
        started_at = datetime(2026, 11, 9, 18, 0, tzinfo=UTC)
        with self.Session.begin() as session:
            stored = session.get(ReportJob, job.id)
            stored.status = "running"
            stored.attempt_count = 1
            stored.started_generation = 1

        first = _record_failure(
            self.Session,
            job.id,
            1,
            RetryableJobError("network"),
            now=started_at,
            jitter=lambda _base: 4,
        )
        second = _record_failure(
            self.Session,
            job.id,
            1,
            RetryableJobError("network"),
            now=started_at + timedelta(seconds=1),
            jitter=lambda _base: 99,
        )

        self.assertEqual(first.category, "network")
        self.assertEqual(second.category, "network")
        with self.Session() as session:
            stored = session.get(ReportJob, job.id)
            intents = list(
                session.scalars(
                    select(ReportJobDispatch)
                    .where(ReportJobDispatch.job_id == job.id)
                    .order_by(ReportJobDispatch.generation)
                )
            )
            self.assertEqual(stored.dispatch_generation, 2)
            self.assertEqual(stored.started_generation, 1)
            self.assertEqual(stored.attempt_count, 1)
            self.assertEqual([intent.generation for intent in intents], [1, 2])
            self.assertEqual([intent.status for intent in intents], ["closed", "pending"])
            self.assertEqual(
                intents[1].next_attempt_at,
                started_at + timedelta(seconds=5),
            )

    def test_lease_mutations_require_matching_generation_and_token(self):
        from roster_scout.jobs import accept_report_job
        from roster_scout.models import ReportJobDispatch
        from roster_scout.repositories import (
            ReportDispatchLease,
            acknowledge_report_dispatch,
            claim_report_dispatches,
            close_report_dispatches,
            release_report_dispatch,
        )

        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session,
            organization_id,
            report_id,
            "generation-lease",
            dispatch=None,
        )
        now = datetime.now(UTC) + timedelta(seconds=1)
        with self.Session.begin() as session:
            lease = claim_report_dispatches(session, now=now, limit=1)[0]

        wrong_generation = ReportDispatchLease(
            dispatch_id=lease.dispatch_id,
            organization_id=lease.organization_id,
            job_id=lease.job_id,
            generation=lease.generation + 1,
            message_id=lease.message_id,
            lease_token=lease.lease_token,
            lease_expires_at=lease.lease_expires_at,
        )
        with self.Session.begin() as session:
            self.assertFalse(
                acknowledge_report_dispatch(
                    session,
                    wrong_generation,
                    broker_message_id="ignored",
                    now=now,
                    redispatch_after=now + timedelta(minutes=5),
                )
            )
            self.assertFalse(
                release_report_dispatch(
                    session,
                    wrong_generation,
                    next_attempt_at=now + timedelta(seconds=30),
                    now=now,
                )
            )
            self.assertEqual(
                close_report_dispatches(
                    session,
                    job.id,
                    generation=lease.generation + 1,
                    now=now,
                ),
                0,
            )

        with self.Session() as session:
            intent = session.get(ReportJobDispatch, lease.dispatch_id)
            self.assertEqual(intent.status, "leased")
            self.assertEqual(intent.generation, 1)

    def test_revision_0007_downgrades_reupgrades_and_fresh_installs(self):
        from alembic import command
        from sqlalchemy import inspect

        from tests.db_support import alembic_config

        config = alembic_config(self.database_url)
        command.downgrade(config, "0006")
        inspector = inspect(self.engine)
        self.assertNotIn(
            "dispatch_generation",
            {column["name"] for column in inspector.get_columns("report_jobs")},
        )
        command.upgrade(config, "0007")
        self.assertIn(
            "dispatch_generation",
            {
                column["name"]
                for column in inspect(self.engine).get_columns("report_jobs")
            },
        )
        command.downgrade(config, "base")
        command.upgrade(config, "head")
        command.check(config)


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

    def test_celery_eager_delivery_executes_from_opaque_job_id(self):
        from roster_scout.celery_app import celery_app
        from roster_scout.jobs import accept_report_job
        from roster_scout.tasks import build_report_job

        organization_id, report_id = self.fixture()
        job = accept_report_job(self.Session, organization_id, report_id, "eager", dispatch=None)
        previous_eager = celery_app.conf.task_always_eager
        celery_app.conf.task_always_eager = True
        try:
            with patch("roster_scout.tasks._session_factory", return_value=self.Session), patch(
                "roster_scout.tasks.collect_report_sections",
                return_value=self.successful_sections(),
            ):
                result = build_report_job.apply(args=(str(job.id),), throw=True)
        finally:
            celery_app.conf.task_always_eager = previous_eager
        self.assertTrue(result.successful())


class FinalAttemptEvidenceRecoveryTests(PostgreSQLJobTestCase):
    def evidence_sections(self, job_id, *, parser_version="parser-1"):
        from roster_scout.jobs import CollectedSection
        from roster_scout.models import ReportJob, Report, Season, Team, TeamSeason

        with self.Session() as session:
            job = session.get(ReportJob, job_id)
            report = session.get(Report, job.report_id)
            team_season = session.get(TeamSeason, report.team_season_id)
            team = session.get(Team, team_season.team_id)
            season = session.get(Season, team_season.season_id)
            common = {
                "critical": True,
                "rows": (),
                "verified_empty": True,
                "source_team_id": team.source_team_id,
                "source_season_id": season.source_season_id,
                "source_season_team_id": team_season.source_season_team_id,
                "collected_at": datetime(2026, 11, 9, 12, 0, tzinfo=UTC),
                "parser_version": parser_version,
                "schema_version": "schema-1",
            }
            return tuple(
                CollectedSection(
                    name=name,
                    source_url=f"https://stats.ncaa.org/{name}",
                    source_stat_category_id=category,
                    **common,
                )
                for name, category in (
                    ("batting", team_season.batting_stat_category_id),
                    ("pitching", team_season.pitching_stat_category_id),
                )
            )

    def crash_after_source_commit(self, *, max_attempts, attempt_count):
        from roster_scout.jobs import accept_report_job, run_report_job
        from roster_scout.models import ReportJob

        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session, organization_id, report_id, uuid.uuid4().hex, dispatch=None
        )
        with self.Session.begin() as session:
            stored = session.get(ReportJob, job.id)
            stored.max_attempts = max_attempts
            stored.attempt_count = attempt_count
        sections = self.evidence_sections(job.id)
        with patch("roster_scout.jobs._persist_canonical", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                run_report_job(self.Session, job.id, collector=Mock(return_value=sections))
        return job

    def assert_evidence_only_replay(self, job, expected_attempts):
        from roster_scout.jobs import run_report_job
        from roster_scout.models import ReportJob

        collector = Mock(side_effect=AssertionError("finalization collected twice"))
        snapshot = run_report_job(self.Session, job.id, collector=collector)
        self.assertEqual(snapshot.status, "ready")
        collector.assert_not_called()
        with self.Session() as session:
            stored = session.get(ReportJob, job.id)
            self.assertEqual(stored.attempt_count, expected_attempts)
            self.assertEqual(stored.status, "ready")

    def test_max_attempts_one_replays_complete_evidence_without_collection(self):
        job = self.crash_after_source_commit(max_attempts=1, attempt_count=0)
        self.assert_evidence_only_replay(job, 1)

    def test_final_configured_attempt_replays_complete_evidence_without_collection(self):
        job = self.crash_after_source_commit(max_attempts=2, attempt_count=1)
        self.assert_evidence_only_replay(job, 2)

    def test_incomplete_or_version_mismatched_evidence_still_exhausts(self):
        from roster_scout.jobs import DeterministicJobError, run_report_job
        from roster_scout.models import ReportSection

        for mutation in ("missing", "version"):
            with self.subTest(mutation=mutation):
                clean_database(self.database_url)
                job = self.crash_after_source_commit(max_attempts=1, attempt_count=0)
                with self.Session.begin() as session:
                    pitching = session.scalar(
                        select(ReportSection).where(ReportSection.section == "pitching")
                    )
                    if mutation == "missing":
                        session.delete(pitching)
                    else:
                        pitching.parser_version = "parser-stale"
                collector = Mock(side_effect=AssertionError("exhausted job collected"))
                with self.assertRaises(DeterministicJobError) as raised:
                    run_report_job(self.Session, job.id, collector=collector)
                self.assertEqual(raised.exception.category, "retry_exhausted")
                collector.assert_not_called()


class PerSectionRecoveryTests(PostgreSQLJobTestCase):
    def seed_partial_batting(self):
        from roster_scout.jobs import (
            CollectedSection,
            _new_or_existing_snapshot,
            _persist_sections,
            accept_report_job,
        )
        from roster_scout.models import ReportJob, Report, Season, Team, TeamSeason

        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session, organization_id, report_id, uuid.uuid4().hex, dispatch=None
        )
        with self.Session.begin() as session:
            stored_job = session.get(ReportJob, job.id)
            report = session.get(Report, stored_job.report_id)
            team_season = session.get(TeamSeason, report.team_season_id)
            team = session.get(Team, team_season.team_id)
            season = session.get(Season, team_season.season_id)
            source = {
                "team_id": team.source_team_id,
                "season_id": season.source_season_id,
                "season_team_id": team_season.source_season_team_id,
                "division": team_season.division,
                "batting_id": team_season.batting_stat_category_id,
                "pitching_id": team_season.pitching_stat_category_id,
                "year": season.year,
            }
            batting = CollectedSection(
                name="batting",
                critical=True,
                rows=({
                    "Player": "Recovery Player",
                    "AB": "10",
                    "H": "4",
                    "BB": "1",
                    "K": "2",
                    "AVG": ".400",
                    "_player_id": "recovery-player",
                    "_player_url": "https://stats.ncaa.org/players/recovery-player",
                    "_payload_sha256": "a" * 64,
                },),
                source_url="https://stats.ncaa.org/batting-original",
                source_team_id=source["team_id"],
                source_season_id=source["season_id"],
                source_season_team_id=source["season_team_id"],
                source_stat_category_id=source["batting_id"],
                collected_at=datetime(2026, 11, 8, 10, 0, tzinfo=UTC),
                parser_version="parser-1",
                schema_version="schema-1",
            )
            snapshot = _new_or_existing_snapshot(session, stored_job)
            _persist_sections(session, stored_job, snapshot, (batting,))
            snapshot_id = snapshot.id
        return job, snapshot_id, batting, source

    @staticmethod
    def pitching_collection(source):
        from roster_scraper import (
            NcaaCollection,
            NcaaCollectionMetadata,
            NcaaSection,
            NcaaSeason,
            NcaaSourceRow,
            NcaaTeam,
        )

        collected_at = datetime(2026, 11, 9, 11, 0, tzinfo=UTC)
        row = NcaaSourceRow(
            section="pitching",
            row_ordinal=0,
            source_url="https://stats.ncaa.org/pitching-new",
            source_player_id="recovery-player",
            player_url="https://stats.ncaa.org/players/recovery-player",
            raw_values={
                "Player": "Recovery Player",
                "IP": "3.0",
                "H": "2",
                "ER": "1",
                "BB": "1",
                "SO": "4",
                "ERA": "3.00",
                "player_url": "https://stats.ncaa.org/players/recovery-player",
            },
            payload_sha256="b" * 64,
        )
        return NcaaCollection(
            team=NcaaTeam(
                team_id=source["team_id"],
                name="Tampa",
                season_team_id=source["season_team_id"],
                division=f"d{source['division']}",
            ),
            season=NcaaSeason(
                season_id=source["season_id"],
                year=source["year"],
                batting_category_id=source["batting_id"],
                pitching_category_id=source["pitching_id"],
            ),
            metadata=NcaaCollectionMetadata(collected_at=collected_at),
            sections=(NcaaSection(
                name="pitching",
                source_url="https://stats.ncaa.org/pitching-new",
                stat_category_id=source["pitching_id"],
                html_sha256="b" * 64,
                rows=(row,),
            ),),
            players=(),
            team_label="Tampa",
        )

    def test_only_missing_section_is_fetched_and_cached_section_stays_exact(self):
        import roster_scraper as rs
        from roster_scout.jobs import collect_ncaa_sections, run_report_job
        from roster_scout.models import ReportSection, SourceRecord

        job, snapshot_id, _batting, source = self.seed_partial_batting()
        with self.Session() as session:
            before_section = session.scalar(
                select(ReportSection).where(ReportSection.section == "batting")
            )
            before_section_values = (
                before_section.row_count,
                before_section.source_url,
                before_section.collected_at,
                before_section.parser_version,
                before_section.schema_version,
            )
            before_rows = [
                (
                    row.id,
                    row.row_ordinal,
                    row.raw_values,
                    row.payload_sha256,
                    row.collected_at,
                    row.parser_version,
                    row.schema_version,
                )
                for row in session.scalars(
                    select(SourceRecord).where(SourceRecord.section == "batting")
                )
            ]

        with patch.object(
            rs, "collect_ncaa_by_id", return_value=self.pitching_collection(source)
        ) as collect:
            snapshot = run_report_job(
                self.Session,
                job.id,
                collector=lambda job_id, cached_sections=frozenset(): collect_ncaa_sections(
                    self.Session, job_id, cached_sections=cached_sections
                ),
            )

        self.assertEqual(snapshot.status, "ready")
        collect.assert_called_once_with(
            source["season_team_id"],
            division=source["division"],
            year=source["year"],
            force_refresh=True,
            section_names=frozenset({"pitching"}),
        )
        with self.Session() as session:
            batting = session.scalar(
                select(ReportSection).where(ReportSection.section == "batting")
            )
            self.assertEqual(
                (
                    batting.row_count,
                    batting.source_url,
                    batting.collected_at,
                    batting.parser_version,
                    batting.schema_version,
                ),
                before_section_values,
            )
            after_rows = [
                (
                    row.id,
                    row.row_ordinal,
                    row.raw_values,
                    row.payload_sha256,
                    row.collected_at,
                    row.parser_version,
                    row.schema_version,
                )
                for row in session.scalars(
                    select(SourceRecord)
                    .where(SourceRecord.section == "batting")
                    .order_by(SourceRecord.row_ordinal)
                )
            ]
            self.assertEqual(after_rows, before_rows)
            self.assertEqual(
                session.scalar(
                    select(func.count()).select_from(SourceRecord).where(
                        SourceRecord.snapshot_id == snapshot_id
                    )
                ),
                2,
            )

    def test_conflicting_content_cannot_extend_a_complete_section(self):
        from dataclasses import replace

        from roster_scout.jobs import DeterministicJobError, _persist_sections
        from roster_scout.models import ReportJob, ReportSnapshot, SourceRecord

        job, snapshot_id, batting, _source = self.seed_partial_batting()
        conflicting = replace(
            batting,
            rows=batting.rows + ({
                "Player": "Injected Player",
                "AB": "1",
                "_player_id": "injected",
                "_payload_sha256": "c" * 64,
            },),
        )
        with self.assertRaises(DeterministicJobError) as raised:
            with self.Session.begin() as session:
                _persist_sections(
                    session,
                    session.get(ReportJob, job.id),
                    session.get(ReportSnapshot, snapshot_id),
                    (conflicting,),
                )
        self.assertEqual(raised.exception.category, "schema_validation")
        with self.Session() as session:
            self.assertEqual(
                session.scalar(select(func.count()).select_from(SourceRecord)), 1
            )


class ConcurrentExecutionClaimTests(PostgreSQLJobTestCase):
    def test_overlapping_deliveries_collect_and_count_one_attempt(self):
        from roster_scout.database import create_session_factory, get_engine
        from roster_scout.jobs import (
            RetryableJobError,
            SAFE_FAILURE_MESSAGES,
            accept_report_job,
            run_report_job,
        )
        from roster_scout.models import (
            BattingFact,
            PitchingFact,
            ReportJob,
            ReportSnapshot,
            SourceRecord,
        )

        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session, organization_id, report_id, "overlapping", dispatch=None
        )
        first_engine = get_engine(self.database_url)
        second_engine = get_engine(self.database_url)
        first_session = create_session_factory(first_engine)
        second_session = create_session_factory(second_engine)
        collector_started = threading.Event()
        release_collector = threading.Event()
        collector_calls = []
        outcomes = {}

        def collector(job_id, *, cached_sections=frozenset()):
            collector_calls.append((job_id, cached_sections))
            collector_started.set()
            self.assertTrue(release_collector.wait(timeout=5))
            return PublicationTests.successful_sections()

        def deliver(label, session_factory):
            try:
                outcomes[label] = run_report_job(
                    session_factory, job.id, collector=collector
                )
            except Exception as exc:  # assertions below inspect the safe boundary
                outcomes[label] = exc

        winner = threading.Thread(target=deliver, args=("winner", first_session))
        loser = threading.Thread(target=deliver, args=("loser", second_session))
        try:
            winner.start()
            self.assertTrue(collector_started.wait(timeout=5))
            loser.start()
            loser.join(timeout=2)
            self.assertFalse(loser.is_alive(), "duplicate delivery blocked instead of deferring")
            self.assertIsInstance(outcomes.get("loser"), RetryableJobError)
            self.assertEqual(outcomes["loser"].category, "job_busy")
            self.assertEqual(str(outcomes["loser"]), SAFE_FAILURE_MESSAGES["job_busy"])
        finally:
            release_collector.set()
            winner.join(timeout=5)
            loser.join(timeout=5)
            first_engine.dispose()
            second_engine.dispose()

        self.assertFalse(winner.is_alive())
        self.assertFalse(loser.is_alive())
        self.assertEqual(len(collector_calls), 1)
        self.assertNotIsInstance(outcomes.get("winner"), Exception)
        with self.Session() as session:
            stored = session.get(ReportJob, job.id)
            self.assertEqual(stored.attempt_count, 1)
            self.assertEqual(stored.status, "ready")
            self.assertIsNone(stored.failure_category)
            self.assertIsNone(stored.failure_message)
            self.assertEqual(
                session.scalar(select(func.count()).select_from(ReportSnapshot)), 1
            )
            self.assertEqual(
                session.scalar(select(func.count()).select_from(SourceRecord)), 0
            )
            self.assertEqual(
                session.scalar(select(func.count()).select_from(BattingFact)), 0
            )
            self.assertEqual(
                session.scalar(select(func.count()).select_from(PitchingFact)), 0
            )

    def test_closing_claim_connection_releases_job_for_redelivery(self):
        from roster_scout.database import session_advisory_lock

        job_id = uuid.uuid4()
        with session_advisory_lock(self.Session, job_id) as first_claim:
            self.assertIsNotNone(first_claim)
            first_claim.connection.close()
            with session_advisory_lock(self.Session, job_id) as replacement_claim:
                self.assertIsNotNone(replacement_claim)


class CeleryConcurrentDeliveryTests(PostgreSQLJobTestCase):
    def test_overlapping_task_entrypoints_collect_once_and_replay_terminal_result(self):
        from celery.exceptions import Retry

        from roster_scout.celery_app import celery_app
        from roster_scout.jobs import SAFE_FAILURE_MESSAGES, accept_report_job
        from roster_scout.models import ReportJob, ReportSnapshot, SourceRecord
        from roster_scout.tasks import build_report_job

        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session, organization_id, report_id, "celery-overlap", dispatch=None
        )
        collector_started = threading.Event()
        release_collector = threading.Event()
        collector_calls = []
        outcomes = {}

        def collector(job_id, *, cached_sections=frozenset()):
            collector_calls.append((job_id, cached_sections))
            collector_started.set()
            self.assertTrue(release_collector.wait(timeout=5))
            return PublicationTests.successful_sections()

        def deliver(label):
            try:
                result = build_report_job.apply(args=(str(job.id),), throw=True)
                outcomes[label] = result.result
            except Exception as exc:  # task retry is an observable delivery outcome
                outcomes[label] = exc

        with patch("roster_scout.tasks._session_factory", return_value=self.Session), patch(
            "roster_scout.tasks.collect_report_sections", side_effect=collector
        ):
            winner = threading.Thread(target=deliver, args=("winner",))
            loser = threading.Thread(target=deliver, args=("loser",))
            try:
                winner.start()
                self.assertTrue(collector_started.wait(timeout=5))
                loser.start()
                loser.join(timeout=2)
                self.assertFalse(loser.is_alive())
                self.assertIsInstance(outcomes.get("loser"), Retry)
                self.assertIn(SAFE_FAILURE_MESSAGES["job_busy"], str(outcomes["loser"]))
            finally:
                release_collector.set()
                winner.join(timeout=5)
                loser.join(timeout=5)

        self.assertFalse(winner.is_alive())
        self.assertFalse(loser.is_alive())
        self.assertEqual(len(collector_calls), 1)
        snapshot_id = outcomes.get("winner")
        self.assertIsInstance(snapshot_id, str)
        with self.Session() as session:
            stored = session.get(ReportJob, job.id)
            self.assertEqual(stored.attempt_count, 1)
            self.assertEqual(stored.status, "ready")
            self.assertIsNone(stored.failure_category)
            self.assertIsNone(stored.failure_message)
            self.assertEqual(
                session.scalar(select(func.count()).select_from(ReportSnapshot)), 1
            )
            self.assertEqual(
                session.scalar(select(func.count()).select_from(SourceRecord)), 0
            )

        terminal_collector = Mock(
            side_effect=AssertionError("terminal replay contacted Browserless")
        )
        with patch("roster_scout.tasks._session_factory", return_value=self.Session), patch(
            "roster_scout.tasks.collect_report_sections", terminal_collector
        ):
            replayed_snapshot_id = build_report_job.apply(
                args=(str(job.id),), throw=True
            ).result
        self.assertEqual(replayed_snapshot_id, snapshot_id)
        terminal_collector.assert_not_called()

        self.assertTrue(celery_app.conf.task_acks_late)
        self.assertTrue(celery_app.conf.task_reject_on_worker_lost)
        self.assertEqual(celery_app.conf.worker_prefetch_multiplier, 1)
        self.assertIsNone(celery_app.conf.result_backend)

    def test_task_failure_persists_only_allowlisted_safe_text(self):
        from roster_scout.jobs import (
            DeterministicJobError,
            SAFE_FAILURE_MESSAGES,
            accept_report_job,
        )
        from roster_scout.models import ReportJob
        from roster_scout.tasks import build_report_job

        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session, organization_id, report_id, "celery-safe-failure", dispatch=None
        )
        with patch("roster_scout.tasks._session_factory", return_value=self.Session), patch(
            "roster_scout.tasks.collect_report_sections",
            side_effect=ValueError("schema response contained secret-token"),
        ):
            with self.assertRaises(DeterministicJobError):
                build_report_job.apply(args=(str(job.id),), throw=True)

        with self.Session() as session:
            stored = session.get(ReportJob, job.id)
            self.assertEqual(stored.status, "failed")
            self.assertEqual(stored.failure_category, "schema_validation")
            self.assertEqual(
                stored.failure_message, SAFE_FAILURE_MESSAGES["schema_validation"]
            )
            self.assertIn(stored.failure_message, SAFE_FAILURE_MESSAGES.values())
            self.assertNotIn("secret-token", stored.failure_message)


class BrokerLossRecoveryTests(PostgreSQLJobTestCase):
    def test_acknowledged_dropped_message_is_reconciled_without_new_acceptance(self):
        from roster_scout.jobs import (
            accept_report_job,
            drain_report_dispatch_outbox,
            reconcile_stale_report_jobs,
            run_report_job,
        )
        from roster_scout.models import ReportJob, ReportJobDispatch, ReportSnapshot

        organization_id, report_id = self.fixture()
        dropped_payloads = []

        def acknowledge_then_drop(job_id):
            dropped_payloads.append(job_id)
            return Mock(id="broker-accepted-but-dropped")

        job = accept_report_job(
            self.Session,
            organization_id,
            report_id,
            "broker-loss",
            dispatch=acknowledge_then_drop,
        )
        self.assertEqual(dropped_payloads, [str(job.id)])
        with self.Session() as session:
            intent = session.scalar(
                select(ReportJobDispatch).where(ReportJobDispatch.job_id == job.id)
            )
            deadline = intent.redispatch_after

        result = reconcile_stale_report_jobs(
            self.Session,
            now=deadline + timedelta(seconds=1),
            queued_timeout=timedelta(minutes=5),
            running_timeout=timedelta(minutes=30),
        )
        self.assertEqual(result.reopened, 1)
        delivered_payloads = []

        def redeliver(job_id):
            delivered_payloads.append(job_id)
            return Mock(id="broker-redelivery")

        drained = drain_report_dispatch_outbox(
            self.Session,
            dispatch=redeliver,
            now=deadline + timedelta(seconds=1),
            limit=10,
            lease_duration=timedelta(seconds=30),
            redispatch_delay=timedelta(minutes=5),
        )
        self.assertEqual(drained.published, 1)
        self.assertEqual(delivered_payloads, [str(job.id)])
        self.assertEqual(uuid.UUID(delivered_payloads[0]), job.id)

        snapshot = run_report_job(
            self.Session,
            delivered_payloads[0],
            collector=Mock(return_value=PublicationTests.successful_sections()),
        )
        self.assertIn(snapshot.status, {"ready", "partial"})
        with self.Session() as session:
            stored = session.get(ReportJob, job.id)
            self.assertIn(stored.status, {"ready", "partial"})
            self.assertEqual(
                session.scalar(select(func.count()).select_from(ReportJob)), 1
            )
            self.assertEqual(
                session.scalar(select(func.count()).select_from(ReportSnapshot)), 1
            )

    def test_active_execution_and_terminal_jobs_are_never_redispatched(self):
        from roster_scout.jobs import (
            accept_report_job,
            drain_report_dispatch_outbox,
            reconcile_stale_report_jobs,
            run_report_job,
        )

        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session, organization_id, report_id, "active-reconcile", dispatch=None
        )
        collector_started = threading.Event()
        release_collector = threading.Event()
        outcome = {}

        def collector(_job_id, *, cached_sections=frozenset()):
            collector_started.set()
            self.assertTrue(release_collector.wait(timeout=5))
            return PublicationTests.successful_sections()

        def execute():
            try:
                outcome["snapshot"] = run_report_job(
                    self.Session, job.id, collector=collector
                )
            except Exception as exc:
                outcome["error"] = exc

        worker = threading.Thread(target=execute)
        worker.start()
        self.assertTrue(collector_started.wait(timeout=5))
        future = datetime.now(UTC) + timedelta(hours=2)
        try:
            reconciled = reconcile_stale_report_jobs(
                self.Session,
                now=future,
                queued_timeout=timedelta(seconds=1),
                running_timeout=timedelta(seconds=1),
            )
            self.assertEqual(reconciled.reopened, 0)
            dispatch = Mock(return_value=Mock(id="must-not-publish"))
            drained = drain_report_dispatch_outbox(
                self.Session, dispatch=dispatch, now=future, limit=10
            )
            self.assertEqual(drained.published, 0)
            dispatch.assert_not_called()
        finally:
            release_collector.set()
            worker.join(timeout=5)

        self.assertNotIn("error", outcome)
        terminal = reconcile_stale_report_jobs(
            self.Session,
            now=future + timedelta(hours=1),
            queued_timeout=timedelta(seconds=1),
            running_timeout=timedelta(seconds=1),
        )
        self.assertEqual(terminal.reopened, 0)
        dispatch = Mock(return_value=Mock(id="terminal-must-not-publish"))
        drained = drain_report_dispatch_outbox(
            self.Session,
            dispatch=dispatch,
            now=future + timedelta(hours=1),
            limit=10,
        )
        self.assertEqual(drained.published, 0)
        dispatch.assert_not_called()

    def test_stale_running_without_execution_claim_reopens_and_crashed_lease_reclaims(self):
        from roster_scout.jobs import (
            accept_report_job,
            drain_report_dispatch_outbox,
            reconcile_stale_report_jobs,
        )
        from roster_scout.models import ReportJob, ReportJobDispatch
        from roster_scout.repositories import claim_report_dispatches

        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session, organization_id, report_id, "stale-running", dispatch=None
        )
        stale_started = datetime.now(UTC) - timedelta(hours=1)
        with self.Session.begin() as session:
            stored = session.get(ReportJob, job.id)
            stored.status = "running"
            stored.started_at = stale_started
            intent = session.scalar(
                select(ReportJobDispatch).where(ReportJobDispatch.job_id == job.id)
            )
            intent.status = "closed"

        now = datetime.now(UTC)
        reconciled = reconcile_stale_report_jobs(
            self.Session,
            now=now,
            queued_timeout=timedelta(minutes=5),
            running_timeout=timedelta(minutes=30),
        )
        self.assertEqual(reconciled.reopened, 1)
        with self.Session.begin() as session:
            leases = claim_report_dispatches(
                session,
                now=now,
                limit=1,
                lease_duration=timedelta(seconds=10),
            )
        self.assertEqual(len(leases), 1)

        dispatch = Mock(return_value=Mock(id="reclaimed-message"))
        drained = drain_report_dispatch_outbox(
            self.Session,
            dispatch=dispatch,
            now=now + timedelta(seconds=11),
            limit=1,
            lease_duration=timedelta(seconds=10),
            redispatch_delay=timedelta(minutes=5),
        )
        self.assertEqual(drained.published, 1)
        dispatch.assert_called_once_with(str(job.id))
        with self.Session() as session:
            intent = session.scalar(
                select(ReportJobDispatch).where(
                    ReportJobDispatch.job_id == job.id,
                    ReportJobDispatch.status == "acknowledged",
                )
            )
            self.assertEqual(intent.publish_attempts, 2)
            self.assertNotIn("postgresql", intent.message_id)
            self.assertNotIn("redis", intent.message_id)


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


class FailureEvidenceIntegrationTests(PostgreSQLJobTestCase):
    def test_successful_job_stores_no_html(self):
        from roster_scout.jobs import accept_report_job, run_report_job
        from roster_scout.models import FailureEvidence

        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session,
            organization_id,
            report_id,
            "successful-job-no-html",
            dispatch=None,
        )
        snapshot = run_report_job(
            self.Session,
            job.id,
            collector=lambda *_args, **_kwargs: PublicationTests.successful_sections(),
        )
        self.assertEqual(snapshot.status, "ready")
        with self.Session() as session:
            self.assertEqual(
                session.scalar(select(func.count()).select_from(FailureEvidence)), 0
            )


class SectionHeaderPublicationTests(PostgreSQLJobTestCase):
    TEAMS = {
        1: {
            "team_id": "234",
            "team_name": "Florida St.",
            "season_team_id": "614606",
        },
        2: {
            "team_id": "689",
            "team_name": "Tampa",
            "season_team_id": "615087",
        },
        3: {
            "team_id": "633",
            "team_name": "Scranton",
            "season_team_id": "615349",
        },
    }
    SEASON_ID = "17040"
    BATTING_ID = "15867"
    PITCHING_ID = "15868"

    def setUp(self):
        super().setUp()
        import roster_scraper as scraper

        self._previous_team_rows = scraper._NCAA_TEAM_ROWS
        self._previous_season_rows = scraper._NCAA_SEASON_ROWS
        scraper._NCAA_TEAM_ROWS = [
            {
                **team,
                "division": str(division),
                "year": "2026",
                "season_id": self.SEASON_ID,
            }
            for division, team in self.TEAMS.items()
        ]
        scraper._NCAA_SEASON_ROWS = [{
            "season": "2026",
            "id": self.SEASON_ID,
            "batting_id": self.BATTING_ID,
            "pitching_id": self.PITCHING_ID,
        }]

    def tearDown(self):
        import roster_scraper as scraper

        scraper._NCAA_TEAM_ROWS = self._previous_team_rows
        scraper._NCAA_SEASON_ROWS = self._previous_season_rows
        super().tearDown()

    @staticmethod
    def _fixture(name):
        return (
            Path(__file__).parent / "fixtures" / name
        ).read_text(encoding="utf-8")

    @staticmethod
    def _table(headers, rows=""):
        return (
            "<html><body><table id='stat_grid'>"
            f"<thead><tr>{headers}</tr></thead><tbody>{rows}</tbody>"
            "</table></body></html>"
        )

    def _accepted_job(self, division, idempotency_key):
        from roster_scout.jobs import accept_report_job
        from roster_scout.models import Report, Season, Team, TeamSeason

        organization_id, report_id = self.fixture()
        source = self.TEAMS[division]
        with self.Session.begin() as session:
            report = session.get(Report, report_id)
            team_season = session.get(TeamSeason, report.team_season_id)
            team = session.get(Team, team_season.team_id)
            season = session.get(Season, team_season.season_id)
            team.source_team_id = source["team_id"]
            team.name = source["team_name"]
            season.source_season_id = self.SEASON_ID
            season.year = 2026
            team_season.source_season_team_id = source["season_team_id"]
            team_season.division = str(division)
            team_season.batting_stat_category_id = self.BATTING_ID
            team_season.pitching_stat_category_id = self.PITCHING_ID
        return accept_report_job(
            self.Session,
            organization_id,
            report_id,
            idempotency_key,
            dispatch=None,
        )

    def _run_real_collector(self, job):
        from roster_scout.jobs import collect_ncaa_sections, run_report_job

        return run_report_job(
            self.Session,
            job.id,
            collector=lambda job_id, cached_sections=frozenset(): collect_ncaa_sections(
                self.Session,
                job_id,
                cached_sections=cached_sections,
            ),
        )

    def test_malformed_core_headers_fail_closed_with_bounded_evidence(self):
        import roster_scraper as scraper
        from roster_scout.jobs import DeterministicJobError, SAFE_FAILURE_MESSAGES
        from roster_scout.models import (
            BattingFact,
            FailureEvidence,
            PitchingFact,
            ReportJob,
            ReportSnapshot,
        )
        from tests.db_support import clean_database

        corruptions = {
            "player-only": "<th>Player</th>",
            "unrelated": "<th>Player</th><th>Unrelated</th>",
            "renamed": None,
        }
        renamed = {
            "batting": "<th>Player</th><th>At Bats</th><th>Hits</th>",
            "pitching": (
                "<th>Player</th><th>Innings</th>"
                "<th>Earned Run Average</th>"
            ),
        }
        for section in ("batting", "pitching"):
            for case, headers in corruptions.items():
                clean_database(self.database_url)
                job = self._accepted_job(2, f"{section}-{case}")
                pages = {
                    "batting": self._fixture("d2_batting.html"),
                    "pitching": self._fixture("d2_pitching.html"),
                    section: self._table(headers or renamed[section]),
                }
                with self.subTest(section=section, case=case), patch.object(
                    scraper, "_fetch_ncaa_pages", return_value=pages
                ) as fetch:
                    with self.assertRaises(DeterministicJobError) as caught:
                        self._run_real_collector(job)
                self.assertEqual(caught.exception.category, "schema_validation")
                fetch.assert_called_once()

                with self.Session() as session:
                    stored = session.get(ReportJob, job.id)
                    evidence = list(session.scalars(select(FailureEvidence)))
                    self.assertEqual(stored.status, "failed")
                    self.assertEqual(stored.failure_category, "schema_validation")
                    self.assertEqual(
                        stored.failure_message,
                        SAFE_FAILURE_MESSAGES["schema_validation"],
                    )
                    self.assertEqual(len(evidence), 1)
                    self.assertEqual(evidence[0].section, section)
                    self.assertEqual(evidence[0].failure_category, "schema_validation")
                    self.assertLessEqual(
                        len(gzip.decompress(evidence[0].compressed_html)),
                        scraper.FAILURE_EVIDENCE_MAX_BYTES,
                    )
                    self.assertNotIn("?", evidence[0].source_url or "")
                    self.assertNotIn("#", evidence[0].source_url or "")
                    self.assertNotIn("@", evidence[0].source_url or "")
                    self.assertNotIn("<html", stored.failure_message.casefold())
                    self.assertEqual(
                        session.scalar(
                            select(func.count())
                            .select_from(ReportSnapshot)
                            .where(ReportSnapshot.status.in_(("ready", "partial")))
                        ),
                        0,
                    )
                    self.assertEqual(
                        session.scalar(select(func.count()).select_from(BattingFact)),
                        0,
                    )
                    self.assertEqual(
                        session.scalar(select(func.count()).select_from(PitchingFact)),
                        0,
                    )

    def test_matching_core_headers_allow_verified_empty_sections(self):
        import roster_scraper as scraper
        from roster_scout.models import FailureEvidence, ReportSection
        from tests.db_support import clean_database

        empty_headers = {
            "batting": "<th>Player</th><th>AB</th><th>H</th>",
            "pitching": "<th>Player</th><th>IP</th><th>ERA</th>",
        }
        for section, headers in empty_headers.items():
            clean_database(self.database_url)
            job = self._accepted_job(2, f"valid-empty-{section}")
            pages = {
                "batting": self._fixture("d2_batting.html"),
                "pitching": self._fixture("d2_pitching.html"),
                section: self._table(headers),
            }
            with self.subTest(section=section), patch.object(
                scraper, "_fetch_ncaa_pages", return_value=pages
            ):
                snapshot = self._run_real_collector(job)
            self.assertEqual(snapshot.status, "ready")
            with self.Session() as session:
                stored_section = session.scalar(select(ReportSection).where(
                    ReportSection.snapshot_id == snapshot.id,
                    ReportSection.section == section,
                ))
                self.assertEqual(stored_section.row_count, 0)
                self.assertTrue(stored_section.verified_empty)
                self.assertEqual(
                    session.scalar(select(func.count()).select_from(FailureEvidence)),
                    0,
                )

    def test_valid_d1_d2_d3_pages_still_publish(self):
        import roster_scraper as scraper
        from roster_scout.models import BattingFact, PitchingFact
        from tests.db_support import clean_database

        for division in (1, 2, 3):
            clean_database(self.database_url)
            job = self._accepted_job(division, f"valid-d{division}")
            pages = {
                "batting": self._fixture(f"d{division}_batting.html"),
                "pitching": self._fixture(f"d{division}_pitching.html"),
            }
            with self.subTest(division=division), patch.object(
                scraper, "_fetch_ncaa_pages", return_value=pages
            ):
                snapshot = self._run_real_collector(job)
            self.assertEqual(snapshot.status, "ready")
            with self.Session() as session:
                self.assertGreater(
                    session.scalar(select(func.count()).select_from(BattingFact)), 0
                )
                self.assertGreater(
                    session.scalar(select(func.count()).select_from(PitchingFact)), 0
                )


if __name__ == "__main__":
    unittest.main()
