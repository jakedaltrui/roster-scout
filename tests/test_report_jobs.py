"""Durable report-job, redelivery, cache, and evidence-retention contract."""

import gzip
import hashlib
import inspect as python_inspect
import multiprocessing
import os
import threading
import unittest
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.db_support import clean_database, require_test_database_url, upgrade_database


def _crash_after_generation_cas(database_url, job_id):
    from roster_scout.database import create_session_factory, get_engine
    from roster_scout.jobs import run_report_job

    engine = get_engine(database_url)
    Session = create_session_factory(engine)

    def terminate(_job_id, *, cached_sections=frozenset()):
        os._exit(0)

    run_report_job(Session, job_id, 1, collector=terminate)


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
        self.assertEqual(
            list(python_inspect.signature(build_report_job.run).parameters),
            ["job_id", "generation"],
        )
        self.assertNotIn("self.retry", python_inspect.getsource(build_report_job.run))


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


class AttemptGenerationDeliveryTests(PostgreSQLJobTestCase):
    def test_sequential_duplicate_and_stale_generation_are_precollection_noops(self):
        from roster_scout.jobs import RetryableJobError, accept_report_job, run_report_job
        from roster_scout.models import ReportJob, ReportJobDispatch

        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session,
            organization_id,
            report_id,
            "generation-sequential",
            dispatch=None,
        )
        with self.assertRaises(RetryableJobError):
            run_report_job(
                self.Session,
                job.id,
                1,
                collector=Mock(side_effect=ConnectionError("temporary")),
            )

        stale_collector = Mock(side_effect=AssertionError("stale generation collected"))
        outcome = run_report_job(
            self.Session, job.id, 1, collector=stale_collector
        )
        self.assertEqual(outcome, "stale")
        stale_collector.assert_not_called()

        with self.Session() as session:
            stored = session.get(ReportJob, job.id)
            intent = session.scalar(
                select(ReportJobDispatch).where(
                    ReportJobDispatch.job_id == job.id,
                    ReportJobDispatch.generation == 2,
                )
            )
            before = (
                stored.attempt_count,
                stored.failure_category,
                stored.failure_message,
                intent.status,
                intent.next_attempt_at,
            )
            self.assertEqual(before[0], 1)
            self.assertEqual(before[1], "network")
            self.assertEqual(before[3], "pending")

        snapshot = run_report_job(
            self.Session,
            job.id,
            2,
            collector=Mock(return_value=PublicationTests.successful_sections()),
        )
        self.assertEqual(snapshot.status, "partial")
        with self.Session() as session:
            stored = session.get(ReportJob, job.id)
            self.assertEqual(stored.attempt_count, 2)
            self.assertEqual(stored.started_generation, 2)

    def test_stale_started_generation_exhaustion_is_terminal_without_successor(self):
        from roster_scout.jobs import accept_report_job, reconcile_stale_report_jobs
        from roster_scout.models import ReportJob, ReportJobDispatch

        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session,
            organization_id,
            report_id,
            "generation-stale-exhausted",
            dispatch=None,
        )
        stale_started = datetime.now(UTC) - timedelta(hours=1)
        with self.Session.begin() as session:
            stored = session.get(ReportJob, job.id)
            stored.status = "running"
            stored.max_attempts = 1
            stored.attempt_count = 1
            stored.started_generation = 1
            stored.started_at = stale_started
            intent = session.scalar(
                select(ReportJobDispatch).where(ReportJobDispatch.job_id == job.id)
            )
            intent.status = "closed"

        first = reconcile_stale_report_jobs(
            self.Session,
            now=datetime.now(UTC),
            running_timeout=timedelta(minutes=30),
        )
        second = reconcile_stale_report_jobs(
            self.Session,
            now=datetime.now(UTC) + timedelta(seconds=1),
            running_timeout=timedelta(minutes=30),
        )
        self.assertEqual(first.reopened, 0)
        self.assertEqual(second.reopened, 0)
        with self.Session() as session:
            stored = session.get(ReportJob, job.id)
            self.assertEqual(stored.status, "failed")
            self.assertEqual(stored.failure_category, "retry_exhausted")
            self.assertEqual(stored.dispatch_generation, 1)
            self.assertEqual(
                session.scalar(
                    select(func.count())
                    .select_from(ReportJobDispatch)
                    .where(
                        ReportJobDispatch.job_id == job.id,
                        ReportJobDispatch.generation == 2,
                    )
                ),
                0,
            )

    def test_overlapping_same_generation_returns_duplicate_without_retry(self):
        from roster_scout.database import create_session_factory, get_engine
        from roster_scout.jobs import accept_report_job, run_report_job
        from roster_scout.models import ReportJob

        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session,
            organization_id,
            report_id,
            "generation-overlap",
            dispatch=None,
        )
        first_engine = get_engine(self.database_url)
        second_engine = get_engine(self.database_url)
        first_session = create_session_factory(first_engine)
        second_session = create_session_factory(second_engine)
        started = threading.Event()
        release = threading.Event()
        calls = []
        outcomes = {}

        def collector(job_id, *, cached_sections=frozenset()):
            calls.append(job_id)
            started.set()
            self.assertTrue(release.wait(timeout=5))
            return PublicationTests.successful_sections()

        def deliver(label, Session):
            try:
                outcomes[label] = run_report_job(
                    Session, job.id, 1, collector=collector
                )
            except Exception as exc:
                outcomes[label] = exc

        winner = threading.Thread(target=deliver, args=("winner", first_session))
        loser = threading.Thread(target=deliver, args=("loser", second_session))
        try:
            winner.start()
            self.assertTrue(started.wait(timeout=5))
            loser.start()
            loser.join(timeout=2)
            self.assertFalse(loser.is_alive())
            self.assertEqual(outcomes.get("loser"), "duplicate")
        finally:
            release.set()
            winner.join(timeout=5)
            loser.join(timeout=5)
            first_engine.dispose()
            second_engine.dispose()

        self.assertEqual(len(calls), 1)
        with self.Session() as session:
            stored = session.get(ReportJob, job.id)
            self.assertEqual(stored.attempt_count, 1)
            self.assertEqual(stored.started_generation, 1)

    def test_task_acknowledges_retry_scheduling_without_celery_retry(self):
        from roster_scout.jobs import accept_report_job
        from roster_scout.models import ReportJob, ReportJobDispatch
        from roster_scout.tasks import build_report_job

        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session,
            organization_id,
            report_id,
            "generation-task-retry",
            dispatch=None,
        )
        with patch("roster_scout.tasks._session_factory", return_value=self.Session), patch(
            "roster_scout.tasks.collect_report_sections",
            side_effect=ConnectionError("temporary"),
        ):
            result = build_report_job.apply(args=(str(job.id), 1), throw=True)

        self.assertEqual(result.result, "retry_scheduled")
        with self.Session() as session:
            stored = session.get(ReportJob, job.id)
            intent = session.scalar(
                select(ReportJobDispatch).where(
                    ReportJobDispatch.job_id == job.id,
                    ReportJobDispatch.generation == 2,
                )
            )
            self.assertEqual(stored.attempt_count, 1)
            self.assertEqual(stored.dispatch_generation, 2)
            self.assertGreater(intent.next_attempt_at, stored.started_at)

    def test_retry_budget_is_started_generation_bounded(self):
        from roster_scout.jobs import DeterministicJobError, accept_report_job, run_report_job
        from roster_scout.models import ReportJob, ReportJobDispatch

        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session,
            organization_id,
            report_id,
            "generation-exhausted",
            dispatch=None,
        )
        with self.Session.begin() as session:
            session.get(ReportJob, job.id).max_attempts = 1

        with self.assertRaises(DeterministicJobError) as raised:
            run_report_job(
                self.Session,
                job.id,
                1,
                collector=Mock(side_effect=ConnectionError("temporary")),
            )
        self.assertEqual(raised.exception.category, "retry_exhausted")
        with self.Session() as session:
            stored = session.get(ReportJob, job.id)
            self.assertEqual(stored.status, "failed")
            self.assertEqual(stored.attempt_count, 1)
            self.assertEqual(stored.dispatch_generation, 1)
            self.assertEqual(
                session.scalar(
                    select(func.count())
                    .select_from(ReportJobDispatch)
                    .where(
                        ReportJobDispatch.job_id == job.id,
                        ReportJobDispatch.generation == 2,
                    )
                ),
                0,
            )

    def test_task_payload_requires_uuid_and_positive_integer_generation(self):
        from roster_scout.tasks import build_report_job, dispatch_report_job

        parameters = list(python_inspect.signature(build_report_job.run).parameters)
        self.assertEqual(parameters, ["job_id", "generation"])
        for invalid in (0, -1, True, "1"):
            with self.assertRaises(ValueError):
                dispatch_report_job(uuid.uuid4(), invalid)
        with self.assertRaises(ValueError):
            dispatch_report_job("not-a-uuid", 1)


class CrashAfterGenerationCasRecoveryTests(PostgreSQLJobTestCase):
    def test_process_loss_advances_exactly_once_and_next_generation_completes(self):
        from roster_scout.celery_app import celery_app
        from roster_scout.database import session_advisory_lock
        from roster_scout.jobs import (
            accept_report_job,
            drain_report_dispatch_outbox,
            reconcile_stale_report_jobs,
            run_report_job,
        )
        from roster_scout.models import ReportJob, ReportJobDispatch, ReportSnapshot
        from roster_scout.tasks import dispatch_report_job

        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session,
            organization_id,
            report_id,
            "generation-process-crash",
            dispatch=None,
        )

        process = multiprocessing.get_context("spawn").Process(
            target=_crash_after_generation_cas,
            args=(self.database_url, str(job.id)),
        )
        process.start()
        process.join(timeout=10)
        self.assertFalse(process.is_alive())
        self.assertEqual(process.exitcode, 0)

        with self.Session() as session:
            stored = session.get(ReportJob, job.id)
            self.assertEqual(stored.status, "running")
            self.assertEqual(stored.attempt_count, 1)
            self.assertEqual(stored.started_generation, 1)
            stale_started_at = stored.started_at
        with session_advisory_lock(self.Session, job.id) as replacement:
            self.assertIsNotNone(replacement)

        recovery_time = stale_started_at + timedelta(hours=1)
        first = reconcile_stale_report_jobs(
            self.Session,
            now=recovery_time,
            running_timeout=timedelta(minutes=30),
        )
        second = reconcile_stale_report_jobs(
            self.Session,
            now=recovery_time + timedelta(seconds=1),
            running_timeout=timedelta(minutes=30),
        )
        self.assertEqual(first.reopened, 1)
        self.assertEqual(second.reopened, 0)

        stale_collector = Mock(side_effect=AssertionError("generation 1 reran"))
        self.assertEqual(
            run_report_job(self.Session, job.id, 1, collector=stale_collector),
            "stale",
        )
        stale_collector.assert_not_called()

        previous_eager = celery_app.conf.task_always_eager
        celery_app.conf.task_always_eager = True
        try:
            with patch(
                "roster_scout.tasks._session_factory", return_value=self.Session
            ), patch(
                "roster_scout.tasks.collect_report_sections",
                return_value=PublicationTests.successful_sections(),
            ):
                drain_report_dispatch_outbox(
                    self.Session,
                    dispatch=dispatch_report_job,
                    now=recovery_time + timedelta(seconds=1),
                    limit=1,
                )
        finally:
            celery_app.conf.task_always_eager = previous_eager

        with self.Session() as session:
            stored = session.get(ReportJob, job.id)
            intents = list(
                session.scalars(
                    select(ReportJobDispatch).where(
                        ReportJobDispatch.job_id == job.id
                    )
                )
            )
            self.assertEqual(stored.status, "partial")
            self.assertEqual(stored.attempt_count, 2)
            self.assertEqual(stored.started_generation, 2)
            self.assertEqual(stored.dispatch_generation, 2)
            self.assertEqual(
                [intent.generation for intent in intents], [1, 2]
            )
            self.assertEqual(
                session.scalar(select(func.count()).select_from(ReportSnapshot)), 1
            )


class PublicationTests(PostgreSQLJobTestCase):
    @staticmethod
    def successful_sections(collected_at=None, *, supplemental_failed=False):
        from roster_scout.jobs import CollectedSection, PARSER_VERSION

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
                parser_version=PARSER_VERSION,
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
        with self.Session() as session:
            artifact = session.scalar(select(ReportArtifact))
            self.assertEqual(artifact.snapshot_id, snapshot.id)
            self.assertEqual(artifact.status, "pending")
            self.assertEqual(artifact.schema_version, "coach-facing-two-sheet-xlsx-v5")
            self.assertEqual(artifact.attempt_count, 0)

    def test_partial_schedule_rows_are_persisted_before_scope_publication(self):
        from roster_scout.jobs import (
            PARSER_VERSION,
            CollectedSection,
            accept_report_job,
            run_report_job,
        )
        from roster_scout.models import SourceRecord

        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session, organization_id, report_id, "partial-schedule", dispatch=None
        )
        collected_at = datetime.now(UTC)
        schedule = CollectedSection(
            name="schedule",
            critical=False,
            rows=({
                "source_game_id": "fixture-1",
                "game_date": "2026-03-01",
                "doubleheader_ordinal": 1,
                "status": "completed",
                "venue": "home",
                "result_sha256": "a" * 64,
                "conference_evidence_status": "source_absent",
                "schedule_complete": False,
            },),
            source_url="https://stats.ncaa.org/teams/fixture/schedule",
            source_team_id="team",
            source_season_id="season",
            source_season_team_id="team-season",
            source_stat_category_id="schedule",
            collected_at=collected_at,
            parser_version=PARSER_VERSION,
            schema_version="schema-1",
            status="partial",
            failure_category="schedule_evidence_incomplete",
        )

        snapshot = run_report_job(
            self.Session,
            job.id,
            collector=Mock(return_value=(*self.successful_sections(collected_at), schedule)),
        )

        self.assertEqual(snapshot.status, "partial")
        with self.Session() as session:
            records = list(session.scalars(select(SourceRecord).where(
                SourceRecord.snapshot_id == snapshot.id,
                SourceRecord.section == "schedule",
            )))
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].raw_values["source_game_id"], "fixture-1")

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
                result = build_report_job.apply(args=(str(job.id), 1), throw=True)
        finally:
            celery_app.conf.task_always_eager = previous_eager
        self.assertTrue(result.successful())


class TwoStagePublicationTests(PostgreSQLJobTestCase):
    def test_quick_publication_queues_one_detailed_successor(self):
        from roster_scout.jobs import accept_report_job, run_report_job
        from roster_scout.models import (
            ReportArtifact,
            ReportJob,
            ReportJobDispatch,
            ReportSnapshot,
        )

        organization_id, report_id = self.fixture()
        quick = accept_report_job(
            self.Session,
            organization_id,
            report_id,
            "quick-first",
            dispatch=None,
            build_profile="quick",
        )
        snapshot = run_report_job(
            self.Session,
            quick.id,
            collector=Mock(return_value=PublicationTests.successful_sections()),
        )
        replay = run_report_job(
            self.Session,
            quick.id,
            collector=Mock(side_effect=AssertionError("terminal replay collected")),
        )

        self.assertEqual(replay.id, snapshot.id)
        self.assertEqual(snapshot.status, "ready")
        with self.Session() as session:
            jobs = list(session.scalars(
                select(ReportJob).order_by(ReportJob.created_at, ReportJob.id)
            ))
            self.assertEqual(len(jobs), 2)
            self.assertEqual(jobs[0].id, quick.id)
            self.assertEqual(jobs[0].build_profile, "quick")
            self.assertEqual(jobs[0].status, "ready")
            self.assertEqual(jobs[1].build_profile, "detailed")
            self.assertEqual(jobs[1].status, "queued")
            self.assertEqual(jobs[1].idempotency_key, f"detailed:{quick.id}")
            stored_snapshot = session.get(ReportSnapshot, snapshot.id)
            self.assertEqual(
                stored_snapshot.collection_contract_version, "quick-season-v1"
            )
            artifacts = list(session.scalars(select(ReportArtifact)))
            self.assertEqual(len(artifacts), 1)
            self.assertEqual(artifacts[0].snapshot_id, snapshot.id)
            successor_dispatches = list(session.scalars(
                select(ReportJobDispatch).where(
                    ReportJobDispatch.job_id == jobs[1].id
                )
            ))
            self.assertEqual(len(successor_dispatches), 1)
            self.assertEqual(successor_dispatches[0].status, "pending")

    def test_detailed_publication_does_not_queue_another_job(self):
        from roster_scout.jobs import accept_report_job, run_report_job
        from roster_scout.models import ReportJob

        organization_id, report_id = self.fixture()
        detailed = accept_report_job(
            self.Session,
            organization_id,
            report_id,
            "detailed-only",
            dispatch=None,
        )
        run_report_job(
            self.Session,
            detailed.id,
            collector=Mock(return_value=PublicationTests.successful_sections()),
        )
        with self.Session() as session:
            self.assertEqual(
                session.scalar(select(func.count()).select_from(ReportJob)), 1
            )

    def test_quick_status_waits_for_the_downloadable_artifact(self):
        from roster_scout.jobs import accept_report_job, run_report_job
        from roster_scout.models import ReportArtifact
        from roster_scout.reports import load_report_status

        organization_id, report_id = self.fixture()
        quick = accept_report_job(
            self.Session,
            organization_id,
            report_id,
            "quick-artifact-boundary",
            dispatch=None,
            build_profile="quick",
        )
        run_report_job(
            self.Session,
            quick.id,
            collector=Mock(return_value=PublicationTests.successful_sections()),
        )
        with self.Session() as session:
            saving = load_report_status(
                session,
                organization_id,
                report_id,
                job_id=quick.id,
            )
            self.assertEqual(saving.state, "saving")
            self.assertFalse(saving.terminal)

        with self.Session.begin() as session:
            artifact = session.scalar(select(ReportArtifact))
            artifact.status = "ready"
            artifact.sha256 = "a" * 64
            artifact.object_key = "test/quick.xlsx"

        with self.Session() as session:
            ready = load_report_status(
                session,
                organization_id,
                report_id,
                job_id=quick.id,
            )
            self.assertEqual(ready.state, "ready")
            self.assertTrue(ready.terminal)


class FullSeasonMetricPublicationTests(PostgreSQLJobTestCase):
    @staticmethod
    def detailed_sections():
        from roster_scout.jobs import CollectedSection, PARSER_VERSION, SCHEMA_VERSION

        collected_at = datetime(2026, 4, 1, 18, 30, tzinfo=UTC)
        batting_rows = (
            {
                "Player": "Complete Two Way",
                "G": "12",
                "GS": "11",
                "AB": "10",
                "R": "3",
                "H": "4",
                "2B": "1",
                "3B": "1",
                "HR": "1",
                "RBI": "4",
                "BB": "2",
                "HBP": "1",
                "K": "2",
                "SF": "1",
                "SH": "1",
                "SB": "2",
                "CS": "0",
                "TB": "10",
                "_player_id": "100",
                "_player_url": "https://stats.ncaa.org/players/100",
            },
            {
                "Player": "Available Zero",
                "AB": "4",
                "H": "0",
                "2B": "0",
                "3B": "0",
                "HR": "0",
                "BB": "0",
                "HBP": "0",
                "K": "1",
                "SF": "0",
                "SH": "0",
                "_player_id": "101",
            },
            {
                "Player": "Undefined Average",
                "AB": "0",
                "H": "0",
                "2B": "0",
                "3B": "0",
                "HR": "0",
                "BB": "0",
                "HBP": "0",
                "K": "0",
                "SF": "0",
                "SH": "0",
                "_player_id": "102",
            },
            {
                "Player": "Missing Average",
                "AB": "-",
                "H": "-",
                "2B": "-",
                "3B": "-",
                "HR": "-",
                "BB": "-",
                "HBP": "-",
                "K": "-",
                "SF": "-",
                "SH": "-",
                "_player_id": "103",
            },
        )
        pitching_rows = (
            {
                "Player": "Complete Two Way",
                "APP": "5",
                "GS": "2",
                "IP": "7.0",
                "H": "6",
                "R": "3",
                "ER": "2",
                "BB": "3",
                "SO": "9",
                "HR": "1",
                "HBP": "1",
                "BF": "30",
                "AB": "26",
                "SF": "1",
                "_player_id": "100",
                "_player_url": "https://stats.ncaa.org/players/100",
            },
        )
        return tuple(
            CollectedSection(
                name=name,
                critical=True,
                rows=rows,
                source_url=f"https://stats.ncaa.org/{name}",
                source_team_id="team",
                source_season_id="season",
                source_season_team_id="team-season",
                source_stat_category_id=name,
                collected_at=collected_at,
                parser_version=PARSER_VERSION,
                schema_version=SCHEMA_VERSION,
            )
            for name, rows in (
                ("batting", batting_rows),
                ("pitching", pitching_rows),
            )
        )

    def _build_published_snapshot(self, key="detailed-published"):
        from roster_scout.jobs import accept_report_job, run_report_job

        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session, organization_id, report_id, key, dispatch=None
        )
        snapshot = run_report_job(
            self.Session,
            job.id,
            collector=Mock(return_value=self.detailed_sections()),
        )
        return organization_id, report_id, snapshot.id

    def _build_draft_snapshot(self, key):
        from roster_scout.jobs import (
            _new_or_existing_snapshot,
            _persist_canonical,
            _persist_sections,
            accept_report_job,
        )
        from roster_scout.models import ReportJob

        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session, organization_id, report_id, key, dispatch=None
        )
        sections = self.detailed_sections()
        with self.Session.begin() as session:
            stored = session.get(ReportJob, job.id)
            snapshot = _new_or_existing_snapshot(session, stored)
            _persist_sections(session, stored, snapshot, sections)
            session.flush()
            snapshot_id = snapshot.id
        with self.Session.begin() as session:
            stored = session.get(ReportJob, job.id)
            snapshot = session.get(type(snapshot), snapshot_id)
            _persist_canonical(session, stored, snapshot, sections)
            session.flush()
        return snapshot_id

    def test_current_contract_missing_ledgers_and_phase3_families_stays_partial(self):
        from roster_scout.models import ReportSnapshot, RequestLedger

        _organization_id, _report_id, snapshot_id = self._build_published_snapshot(
            "phase3-contract-missing-evidence"
        )
        with self.Session() as session:
            snapshot = session.get(ReportSnapshot, snapshot_id)
            self.assertEqual(
                snapshot.collection_contract_version, "phase3-detailed-v1"
            )
            self.assertEqual(snapshot.status, "partial")
            self.assertEqual(
                session.scalar(
                    select(func.count()).select_from(RequestLedger).where(
                        RequestLedger.snapshot_id == snapshot_id
                    )
                ),
                0,
            )

    def test_normal_report_build_persists_detailed_facts_and_metric_lineage(self):
        from roster_scout.metrics import DEFINITION_VERSION
        from roster_scout.models import (
            BattingFact,
            MetricConstantSet,
            MetricDefinition,
            MetricValue,
            PitchingFact,
            PlayerTeamSeason,
            ReportSnapshot,
        )

        _organization_id, _report_id, snapshot_id = self._build_published_snapshot()
        with self.Session() as session:
            snapshot = session.get(ReportSnapshot, snapshot_id)
            self.assertEqual(snapshot.status, "partial")
            batting = session.scalar(
                select(BattingFact)
                .join(PlayerTeamSeason)
                .where(PlayerTeamSeason.display_name == "Complete Two Way")
            )
            pitching = session.scalar(
                select(PitchingFact)
                .join(PlayerTeamSeason)
                .where(PlayerTeamSeason.display_name == "Complete Two Way")
            )
            self.assertEqual(batting.plate_appearances, 15)
            self.assertEqual(batting.doubles, 1)
            self.assertEqual(batting.total_bases, 10)
            self.assertEqual(pitching.innings_outs, 21)
            self.assertEqual(pitching.batters_faced, 30)
            self.assertEqual(pitching.opponent_at_bats, 26)

            rows = session.execute(
                select(MetricValue, MetricDefinition, PlayerTeamSeason)
                .join(MetricDefinition)
                .join(PlayerTeamSeason)
                .where(MetricValue.snapshot_id == snapshot_id)
            ).all()
            self.assertEqual(len(rows), 64)
            self.assertEqual(
                session.scalar(select(func.count()).select_from(MetricConstantSet)),
                2,
            )
            metrics = {
                (identity.display_name, definition.code): (value, definition)
                for value, definition, identity in rows
            }
            legacy_avg, legacy_avg_definition = metrics[
                ("Complete Two Way", "AVG")
            ]
            self.assertEqual(legacy_avg.value, Decimal(".4"))
            self.assertEqual(legacy_avg.status, "available")
            self.assertEqual(
                legacy_avg_definition.version,
                "ncaa-1.0:calculated",
            )
            legacy_era, legacy_era_definition = metrics[
                ("Complete Two Way", "ERA")
            ]
            self.assertIsNone(legacy_era.value)
            self.assertEqual(legacy_era.status, "missing_input")
            self.assertEqual(legacy_era_definition.formula, "unavailable")
            available_avg, avg_definition = metrics[("Complete Two Way", "avg")]
            self.assertEqual(available_avg.status, "available")
            self.assertEqual(available_avg.value, Decimal(".4"))
            self.assertEqual(available_avg.inputs_json["hits"], 4)
            self.assertEqual(available_avg.sample_json["ab"], 10)
            self.assertEqual(avg_definition.version, DEFINITION_VERSION)
            self.assertEqual(avg_definition.formula, "H / AB")

            zero_avg, _ = metrics[("Available Zero", "avg")]
            undefined_avg, _ = metrics[("Undefined Average", "avg")]
            missing_avg, _ = metrics[("Missing Average", "avg")]
            self.assertEqual((zero_avg.status, zero_avg.value), ("available", 0))
            self.assertEqual(
                (undefined_avg.status, undefined_avg.value),
                ("undefined_denominator", None),
            )
            self.assertEqual(
                (missing_avg.status, missing_avg.value),
                ("missing_input", None),
            )
            woba, _ = metrics[("Complete Two Way", "woba")]
            fip, _ = metrics[("Complete Two Way", "fip")]
            self.assertEqual(woba.status, "available")
            self.assertEqual(woba.value, Decimal("0.59618829"))
            self.assertIsNotNone(woba.constant_set_id)
            constant = session.get(MetricConstantSet, woba.constant_set_id)
            self.assertEqual(constant.version, "ncaa-bbstats-1.4.2-eb7e4af63cb6")
            self.assertEqual(
                constant.payload_sha256,
                "eb7e4af63cb60455904b6377b361e50f57628b779d52e53ff86fdab26e86e6bb",
            )
            self.assertEqual(fip.status, "available")
            self.assertEqual(fip.value, Decimal("5.72170000"))
            self.assertIsNotNone(fip.constant_set_id)
            fip_constant = session.get(MetricConstantSet, fip.constant_set_id)
            self.assertEqual(
                fip_constant.version,
                "ncaa-bbstats-1.4.2-ef3dfcbcd9e7",
            )

    def test_publication_rejects_missing_inputs_sample_or_formula_lineage(self):
        from roster_scout.models import MetricDefinition, MetricValue
        from roster_scout.snapshots import SnapshotPublicationError, publish_snapshot
        from tests.db_support import clean_database

        mutations = {
            "inputs": lambda value, definition: setattr(value, "inputs_json", {}),
            "sample": lambda value, definition: setattr(value, "sample_json", {}),
            "formula": lambda value, definition: setattr(definition, "formula", ""),
        }
        for label, mutate in mutations.items():
            clean_database(self.database_url)
            snapshot_id = self._build_draft_snapshot(f"invalid-{label}")
            with self.subTest(label=label), self.Session.begin() as session:
                value, definition = session.execute(
                    select(MetricValue, MetricDefinition)
                    .join(MetricDefinition)
                    .where(
                        MetricValue.snapshot_id == snapshot_id,
                        MetricDefinition.code == "avg",
                    )
                    .limit(1)
                ).one()
                mutate(value, definition)
                session.flush()
                with self.assertRaises(SnapshotPublicationError):
                    publish_snapshot(session, snapshot_id)

    def test_verified_exact_scope_constant_is_persisted_and_becomes_immutable(self):
        from roster_scout.jobs import accept_report_job, run_report_job
        from roster_scout.metrics import DEFINITION_VERSION, METRIC_DEFINITIONS
        from roster_scout.models import (
            MetricConstantSet,
            MetricDefinition,
            MetricValue,
            Report,
            TeamSeason,
        )

        organization_id, report_id = self.fixture()
        with self.Session.begin() as session:
            report = session.get(Report, report_id)
            team_season = session.get(TeamSeason, report.team_season_id)
            definition = MetricDefinition(
                code="fip",
                version=DEFINITION_VERSION,
                formula=METRIC_DEFINITIONS["fip"].formula,
            )
            session.add(definition)
            session.flush()
            constant = MetricConstantSet(
                metric_definition_id=definition.id,
                metric_code="fip",
                version="ncaa-2026-d2-v1",
                season_id=team_season.season_id,
                division=team_season.division,
                constant_type="additive_constant",
                values_json={"constant": "3.2"},
                methodology="Verified NCAA season/division methodology",
                source_url="https://stats.ncaa.org/constants",
                source_identifier="fip:2026:d2:v1",
                collected_at=datetime.now(UTC),
                payload_sha256="b" * 64,
                verification_status="verified",
                verified_at=datetime.now(UTC),
            )
            session.add(constant)
            session.flush()
            constant_id = constant.id

        job = accept_report_job(
            self.Session,
            organization_id,
            report_id,
            "verified-fip-constant",
            dispatch=None,
        )
        snapshot = run_report_job(
            self.Session,
            job.id,
            collector=Mock(return_value=self.detailed_sections()),
        )
        with self.Session() as session:
            metric = session.scalar(
                select(MetricValue)
                .join(MetricDefinition)
                .where(
                    MetricValue.snapshot_id == snapshot.id,
                    MetricDefinition.code == "fip",
                )
            )
            self.assertEqual(metric.status, "available")
            self.assertEqual(metric.value, Decimal("4.2"))
            self.assertEqual(metric.constant_set_id, constant_id)
        with self.Session.begin() as session:
            with self.assertRaises(DBAPIError):
                session.execute(
                    text(
                        "UPDATE metric_constant_sets "
                        "SET values_json = '{\"constant\": \"4.0\"}'::jsonb "
                        "WHERE id = :constant_id"
                    ),
                    {"constant_id": constant_id},
                )

    def test_publication_rejects_unverified_or_wrong_scope_constant_sets(self):
        from roster_scout.models import (
            MetricConstantSet,
            MetricDefinition,
            MetricValue,
            Report,
            ReportSnapshot,
            Season,
            TeamSeason,
        )
        from roster_scout.snapshots import SnapshotPublicationError, publish_snapshot
        from tests.db_support import clean_database

        for label in ("unverified", "wrong_division", "wrong_season"):
            clean_database(self.database_url)
            snapshot_id = self._build_draft_snapshot(f"constant-{label}")
            with self.subTest(label=label), self.Session.begin() as session:
                snapshot = session.get(ReportSnapshot, snapshot_id)
                report = session.get(Report, snapshot.report_id)
                team_season = session.get(TeamSeason, report.team_season_id)
                value, definition = session.execute(
                    select(MetricValue, MetricDefinition)
                    .join(MetricDefinition)
                    .where(
                        MetricValue.snapshot_id == snapshot_id,
                        MetricDefinition.code == "fip",
                    )
                ).one()
                season_id = team_season.season_id
                division = team_season.division
                status = "pending"
                verified_at = None
                if label != "unverified":
                    status = "verified"
                    verified_at = datetime.now(UTC)
                if label == "wrong_division":
                    division = "1"
                if label == "wrong_season":
                    other_season = Season(
                        source="ncaa",
                        source_season_id=str(uuid.uuid4()),
                        year=2025,
                    )
                    session.add(other_season)
                    session.flush()
                    season_id = other_season.id
                constant = MetricConstantSet(
                    metric_definition_id=definition.id,
                    metric_code="fip",
                    version=f"{label}-v1",
                    season_id=season_id,
                    division=division,
                    constant_type="additive_constant",
                    values_json={"constant": "3.2"},
                    methodology="Verified NCAA season/division methodology",
                    source_url="https://stats.ncaa.org/constants",
                    source_identifier=f"fip:{label}",
                    collected_at=datetime.now(UTC),
                    payload_sha256="c" * 64,
                    verification_status=status,
                    verified_at=verified_at,
                )
                session.add(constant)
                session.flush()
                value.status = "available"
                value.value = Decimal("3.2")
                value.reason_code = None
                value.constant_set_id = constant.id
                session.flush()
                with self.assertRaises(SnapshotPublicationError):
                    publish_snapshot(session, snapshot_id)

    def test_constant_definition_fk_rejects_cross_definition_substitution(self):
        from roster_scout.models import (
            MetricConstantSet,
            MetricDefinition,
            MetricValue,
            TeamSeason,
        )

        snapshot_id = self._build_draft_snapshot("constant-definition-mismatch")
        with self.Session.begin() as session:
            fip_value, _fip_definition = session.execute(
                select(MetricValue, MetricDefinition)
                .join(MetricDefinition)
                .where(
                    MetricValue.snapshot_id == snapshot_id,
                    MetricDefinition.code == "fip",
                )
            ).one()
            avg_definition = session.scalar(
                select(MetricDefinition).where(MetricDefinition.code == "avg")
            )
            constant = MetricConstantSet(
                metric_definition_id=avg_definition.id,
                metric_code="fip",
                version="mismatched-v1",
                season_id=session.scalar(select(TeamSeason.season_id).limit(1)),
                division="2",
                constant_type="additive_constant",
                values_json={"constant": "3.2"},
                methodology="Mismatch counterexample",
                source_url="https://stats.ncaa.org/constants",
                source_identifier="fip:mismatch",
                collected_at=datetime.now(UTC),
                payload_sha256="d" * 64,
            )
            session.add(constant)
            session.flush()
            fip_value.constant_set_id = constant.id
            fip_value.status = "available"
            fip_value.value = Decimal("3.2")
            fip_value.reason_code = None
            with self.assertRaises(IntegrityError):
                session.flush()


class ParserContractRecoveryTests(PostgreSQLJobTestCase):
    def _sections(self, job_id, *, parser_version, collected_at):
        from roster_scout.jobs import CollectedSection
        from roster_scout.models import Report, ReportJob, Season, Team, TeamSeason

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
                "collected_at": collected_at,
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

    def test_parser_one_sections_are_recollected_then_current_evidence_is_reused(self):
        import roster_scraper as scraper
        from roster_scout.jobs import (
            PARSER_VERSION,
            _new_or_existing_snapshot,
            accept_report_job,
            reconcile_stale_report_jobs,
            run_report_job,
        )
        from roster_scout.models import ReportJob, ReportSection

        self.assertIn("parser-2", PARSER_VERSION)
        self.assertIn("ncaa-core-v2", PARSER_VERSION)
        self.assertEqual(PARSER_VERSION, scraper.NCAA_PARSER_VERSION)
        self.assertEqual(
            {item["version"] for item in scraper.NCAA_CORE_HEADER_SIGNATURES.values()},
            {"ncaa-core-v2"},
        )
        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session,
            organization_id,
            report_id,
            "parser-contract-recovery",
            dispatch=None,
        )
        stale_time = datetime(2026, 11, 8, 10, 0, tzinfo=UTC)
        current_time = datetime(2026, 11, 9, 11, 0, tzinfo=UTC)
        stale_sections = self._sections(
            job.id, parser_version="parser-1", collected_at=stale_time
        )
        with self.Session.begin() as session:
            stored_job = session.get(ReportJob, job.id)
            snapshot = _new_or_existing_snapshot(session, stored_job)
            for collected in stale_sections:
                session.add(ReportSection(
                    organization_id=stored_job.organization_id,
                    snapshot_id=snapshot.id,
                    job_id=stored_job.id,
                    section=collected.name,
                    critical=True,
                    status="complete",
                    row_count=0,
                    verified_empty=True,
                    collected_at=stale_time,
                    source_url=collected.source_url,
                    source_team_id=collected.source_team_id,
                    source_season_id=collected.source_season_id,
                    source_season_team_id=collected.source_season_team_id,
                    source_stat_category_id=collected.source_stat_category_id,
                    schema_version="schema-1",
                    parser_version="parser-1",
                ))

        current_sections = self._sections(
            job.id, parser_version=PARSER_VERSION, collected_at=current_time
        )
        collector = Mock(return_value=current_sections)
        with patch(
            "roster_scout.jobs._persist_canonical", side_effect=KeyboardInterrupt
        ):
            with self.assertRaises(KeyboardInterrupt):
                run_report_job(self.Session, job.id, collector=collector)
        collector.assert_called_once()
        self.assertEqual(collector.call_args.kwargs["cached_sections"], frozenset())

        with self.Session() as session:
            stored_job = session.get(ReportJob, job.id)
            started_at = stored_job.started_at
            sections = list(session.scalars(select(ReportSection)))
            self.assertEqual({item.parser_version for item in sections}, {PARSER_VERSION})
            self.assertEqual({item.collected_at for item in sections}, {current_time})

        reconciled = reconcile_stale_report_jobs(
            self.Session,
            now=started_at + timedelta(hours=1),
            running_timeout=timedelta(minutes=30),
        )
        self.assertEqual(reconciled.reopened, 1)
        duplicate_collector = Mock(
            side_effect=AssertionError("current evidence should be reused")
        )
        snapshot = run_report_job(
            self.Session, job.id, collector=duplicate_collector
        )
        self.assertEqual(snapshot.status, "partial")
        duplicate_collector.assert_not_called()
        with self.Session() as session:
            sections = list(session.scalars(select(ReportSection)))
            self.assertEqual({item.parser_version for item in sections}, {PARSER_VERSION})
            self.assertEqual({item.collected_at for item in sections}, {current_time})


class FinalAttemptEvidenceRecoveryTests(PostgreSQLJobTestCase):
    def evidence_sections(self, job_id, *, parser_version=None):
        from roster_scout.jobs import CollectedSection, PARSER_VERSION
        from roster_scout.models import ReportJob, Report, Season, Team, TeamSeason

        parser_version = parser_version or PARSER_VERSION

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
        from roster_scout.jobs import (
            accept_report_job,
            reconcile_stale_report_jobs,
            run_report_job,
        )
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
        with self.Session() as session:
            started_at = session.get(ReportJob, job.id).started_at
        reconciled = reconcile_stale_report_jobs(
            self.Session,
            now=started_at + timedelta(hours=1),
            running_timeout=timedelta(minutes=30),
        )
        self.assertEqual(reconciled.reopened, 1)
        return job

    def assert_evidence_only_replay(self, job, expected_attempts):
        from roster_scout.jobs import run_report_job
        from roster_scout.models import ReportJob

        collector = Mock(side_effect=AssertionError("finalization collected twice"))
        snapshot = run_report_job(self.Session, job.id, collector=collector)
        self.assertEqual(snapshot.status, "partial")
        collector.assert_not_called()
        with self.Session() as session:
            stored = session.get(ReportJob, job.id)
            self.assertEqual(stored.attempt_count, expected_attempts)
            self.assertEqual(stored.status, "partial")

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
            PARSER_VERSION,
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
                parser_version=PARSER_VERSION,
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

    @staticmethod
    def fielding_collection(source):
        from roster_scraper import (
            NcaaCollection,
            NcaaCollectionMetadata,
            NcaaSection,
            NcaaSeason,
            NcaaSourceRow,
            NcaaTeam,
        )

        collected_at = datetime(2026, 11, 9, 11, 1, tzinfo=UTC)
        row = NcaaSourceRow(
            section="fielding",
            row_ordinal=0,
            source_url="https://stats.ncaa.org/fielding-new",
            source_player_id="recovery-player",
            player_url="https://stats.ncaa.org/players/recovery-player",
            raw_values={
                "Player": "Recovery Player",
                "G": "10",
                "GS": "9",
                "PO": "12",
                "A": "8",
                "E": "1",
                "TC": "21",
                "DP": "2",
                "FPCT": ".952",
                "player_url": "https://stats.ncaa.org/players/recovery-player",
            },
            payload_sha256="c" * 64,
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
                fielding_category_id="fielding",
            ),
            metadata=NcaaCollectionMetadata(collected_at=collected_at),
            sections=(NcaaSection(
                name="fielding",
                source_url="https://stats.ncaa.org/fielding-new",
                stat_category_id="fielding",
                html_sha256="c" * 64,
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

        def collect_section(*_args, section_names, **_kwargs):
            if section_names == frozenset({"pitching"}):
                return self.pitching_collection(source)
            if section_names == frozenset({"fielding"}):
                return self.fielding_collection(source)
            raise AssertionError(f"unexpected sections: {section_names}")

        with patch.object(
            rs, "collect_ncaa_by_id", side_effect=collect_section
        ) as collect:
            snapshot = run_report_job(
                self.Session,
                job.id,
                collector=lambda job_id, cached_sections=frozenset(): collect_ncaa_sections(
                    self.Session, job_id, cached_sections=cached_sections
                ),
            )

        self.assertEqual(snapshot.status, "partial")
        self.assertEqual(collect.call_count, 2)
        self.assertEqual(
            [call.kwargs["section_names"] for call in collect.call_args_list],
            [frozenset({"pitching"}), frozenset({"fielding"})],
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
                3,
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
            self.assertEqual(outcomes.get("loser"), "duplicate")
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
            self.assertEqual(stored.status, "partial")
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
        from roster_scout.celery_app import celery_app
        from roster_scout.jobs import accept_report_job
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
                result = build_report_job.apply(args=(str(job.id), 1), throw=True)
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
                self.assertEqual(outcomes.get("loser"), "duplicate")
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
            self.assertEqual(stored.status, "partial")
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
                args=(str(job.id), 1), throw=True
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
                build_report_job.apply(args=(str(job.id), 1), throw=True)

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

        def acknowledge_then_drop(job_id, generation):
            dropped_payloads.append((job_id, generation))
            return Mock(id="broker-accepted-but-dropped")

        job = accept_report_job(
            self.Session,
            organization_id,
            report_id,
            "broker-loss",
            dispatch=acknowledge_then_drop,
        )
        self.assertEqual(dropped_payloads, [(str(job.id), 1)])
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

        def redeliver(job_id, generation):
            delivered_payloads.append((job_id, generation))
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
        self.assertEqual(delivered_payloads, [(str(job.id), 1)])
        self.assertEqual(uuid.UUID(delivered_payloads[0][0]), job.id)

        snapshot = run_report_job(
            self.Session,
            delivered_payloads[0][0],
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
        dispatch.assert_called_once_with(str(job.id), 1)
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

    def test_celery_task_has_only_job_id_and_generation_application_arguments(self):
        from roster_scout.tasks import build_report_job

        parameters = list(python_inspect.signature(build_report_job.run).parameters)
        self.assertEqual(parameters, ["job_id", "generation"])

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
        self.assertEqual(snapshot.status, "partial")
        with self.Session() as session:
            self.assertEqual(
                session.scalar(select(func.count()).select_from(FailureEvidence)), 0
            )


class InvalidRetentionTerminalStateTests(PostgreSQLJobTestCase):
    INVALID_VALUES = ("29", "", "not-an-integer", "0", "-1")

    @staticmethod
    def _failure_error():
        from roster_scout.jobs import DeterministicJobError

        return DeterministicJobError(
            "schema_validation",
            evidence=Mock(
                section="batting",
                source_url="https://stats.ncaa.test/player?token=secret",
                html=b"<html>secret evidence</html>",
                failure_category="schema_validation",
                collected_at=datetime(2026, 11, 10, 20, 0, tzinfo=UTC),
            ),
        )

    def _running_job(self, idempotency_key):
        from roster_scout.jobs import accept_report_job
        from roster_scout.models import ReportJob

        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session,
            organization_id,
            report_id,
            idempotency_key,
            dispatch=None,
        )
        with self.Session.begin() as session:
            stored = session.get(ReportJob, job.id)
            stored.status = "running"
            stored.attempt_count = 1
            stored.started_generation = 1
        return job

    def test_invalid_retention_commits_sanitized_terminal_state_without_evidence(self):
        from roster_scout.jobs import SAFE_FAILURE_MESSAGES, _record_failure
        from roster_scout.models import FailureEvidence, ReportJob, ReportJobDispatch

        for index, value in enumerate(self.INVALID_VALUES):
            clean_database(self.database_url)
            job = self._running_job(f"invalid-retention-{index}")

            with self.subTest(value=value), patch.dict(
                os.environ,
                {"FAILURE_EVIDENCE_RETENTION_DAYS": value},
            ):
                _record_failure(
                    self.Session,
                    job.id,
                    1,
                    self._failure_error(),
                    now=datetime(2026, 11, 10, 20, 5, tzinfo=UTC),
                )

            with self.Session() as session:
                stored = session.get(ReportJob, job.id)
                dispatches = list(session.scalars(
                    select(ReportJobDispatch).where(
                        ReportJobDispatch.job_id == job.id
                    )
                ))
                self.assertEqual(stored.status, "failed")
                self.assertIsNotNone(stored.finished_at)
                self.assertEqual(stored.failure_category, "unexpected")
                self.assertEqual(
                    stored.failure_message,
                    SAFE_FAILURE_MESSAGES["unexpected"],
                )
                self.assertTrue(dispatches)
                self.assertTrue(all(item.status == "closed" for item in dispatches))
                self.assertEqual(
                    session.scalar(select(func.count()).select_from(FailureEvidence)),
                    0,
                )
                persisted_text = (
                    f"{stored.failure_category} {stored.failure_message}"
                )
                for forbidden in (
                    value,
                    "secret evidence",
                    "stats.ncaa.test",
                    "token=secret",
                    "FailureEvidenceConfigurationError",
                ):
                    if forbidden:
                        self.assertNotIn(forbidden, persisted_text)

    def test_valid_retention_commits_terminal_state_and_one_evidence_row(self):
        from roster_scout.jobs import SAFE_FAILURE_MESSAGES, _record_failure
        from roster_scout.models import FailureEvidence, ReportJob

        job = self._running_job("valid-retention-control")
        with patch.dict(
            os.environ,
            {"FAILURE_EVIDENCE_RETENTION_DAYS": "28"},
        ):
            _record_failure(
                self.Session,
                job.id,
                1,
                self._failure_error(),
                now=datetime(2026, 11, 10, 20, 5, tzinfo=UTC),
            )

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
            self.assertEqual(evidence[0].job_id, job.id)


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
    FIELDING_ID = "15869"

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
            "fielding_id": self.FIELDING_ID,
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

    def _accepted_job(
        self, division, idempotency_key, *, build_profile="detailed"
    ):
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
            build_profile=build_profile,
        )

    def _run_real_collector(self, job, generation=None):
        from roster_scout.jobs import collect_ncaa_sections, run_report_job

        return run_report_job(
            self.Session,
            job.id,
            generation,
            collector=lambda job_id, cached_sections=frozenset(): collect_ncaa_sections(
                self.Session,
                job_id,
                cached_sections=cached_sections,
            ),
        )

    def test_quick_collector_stops_after_core_sections(self):
        import roster_scraper as scraper
        from roster_scraper import (
            NcaaCollection,
            NcaaCollectionMetadata,
            NcaaSeason,
            NcaaSection,
            NcaaTeam,
        )
        from roster_scout.models import ReportJob, ReportSection, ReportSnapshot

        job = self._accepted_job(
            1, "quick-core-only", build_profile="quick"
        )
        source = self.TEAMS[1]

        def collect_core(*_args, request_reserver=None, **_kwargs):
            request_reserver("batting")
            request_reserver("pitching")
            return NcaaCollection(
                team=NcaaTeam(
                    team_id=source["team_id"],
                    name=source["team_name"],
                    season_team_id=source["season_team_id"],
                    division="d1",
                ),
                season=NcaaSeason(
                    season_id=self.SEASON_ID,
                    year=2026,
                    batting_category_id=self.BATTING_ID,
                    pitching_category_id=self.PITCHING_ID,
                    fielding_category_id=self.FIELDING_ID,
                ),
                metadata=NcaaCollectionMetadata(collected_at=datetime.now(UTC)),
                sections=tuple(
                    NcaaSection(
                        name=name,
                        source_url=f"https://stats.ncaa.org/{name}",
                        stat_category_id=category_id,
                        html_sha256="a" * 64,
                        rows=(),
                        verified_empty=True,
                    )
                    for name, category_id in (
                        ("batting", self.BATTING_ID),
                        ("pitching", self.PITCHING_ID),
                    )
                ),
                players=(),
                team_label=source["team_name"],
            )

        with patch.object(
            scraper, "collect_ncaa_by_id", side_effect=collect_core
        ) as core, patch.object(
            scraper,
            "collect_ncaa_schedule_evidence",
            side_effect=AssertionError("quick build fetched schedule"),
        ), patch.object(
            scraper,
            "collect_ncaa_player_detail_evidence",
            side_effect=AssertionError("quick build fetched player history"),
        ), patch.object(
            scraper,
            "collect_ncaa_split_results",
            side_effect=AssertionError("quick build fetched splits"),
        ):
            snapshot = self._run_real_collector(job)

        self.assertEqual(core.call_count, 1)
        self.assertEqual(snapshot.status, "ready")
        with self.Session() as session:
            stored_snapshot = session.get(ReportSnapshot, snapshot.id)
            self.assertEqual(
                stored_snapshot.collection_contract_version,
                "quick-season-v1",
            )
            self.assertEqual(
                set(session.scalars(
                    select(ReportSection.section).where(
                        ReportSection.snapshot_id == snapshot.id
                    )
                )),
                {"batting", "pitching"},
            )
            successor = session.scalar(
                select(ReportJob).where(ReportJob.id != job.id)
            )
            self.assertIsNotNone(successor)
            self.assertEqual(successor.build_profile, "detailed")

    @staticmethod
    def _fixture_page_transport(pages, *, error=None):
        """Exercise the production collector above a deterministic network adapter."""
        def fetch(urls, **kwargs):
            reserve = kwargs.get("request_reserver")
            for key in urls:
                if reserve is not None:
                    reserve(key)
            if error is not None:
                raise error
            return {key: pages[key] for key in urls}

        return fetch

    @staticmethod
    def _reserving_collector_result(result, families):
        def collect(*_args, request_reserver=None, **_kwargs):
            for family in families:
                request_reserver(family)
            return result

        return collect

    def _complete_phase3_evidence(self):
        from dataclasses import replace
        from roster_scout.jobs import CollectedSection
        from roster_scout.ncaa_detail import (
            parse_player_detail_fixture, parse_schedule_fixture,
        )
        from tests.test_ncaa_detail import _SplitContractMixin

        base = FullSeasonMetricPublicationTests.detailed_sections()
        core = tuple(
            CollectedSection(
                **{
                    **section.__dict__,
                    "rows": ({
                        **section.rows[0],
                        "_player_id": "9698671",
                        "_player_url": "https://stats.ncaa.org/players/9698671",
                    },),
                    "source_team_id": "234",
                    "source_season_id": "17040",
                    "source_season_team_id": "614606",
                    "source_stat_category_id": (
                        self.BATTING_ID
                        if section.name == "batting"
                        else self.PITCHING_ID
                    ),
                }
            )
            for section in base
        )
        schedule = parse_schedule_fixture(
            self._fixture("ncaa_detail/d1/schedule.html"),
            expected_season_id="17040",
            expected_team_id="234",
            expected_season_team_id="614606",
            expected_division="1",
        )
        detail = parse_player_detail_fixture(
            self._fixture("ncaa_detail/d1/player_detail.html"),
            expected_player_id="9698671",
            expected_season_id="17040",
            expected_season_team_id="614606",
            expected_division="1",
        )
        detail = replace(
            detail,
            career_seasons=tuple(
                row for row in detail.career_seasons
                if row.source_season_id == "17040"
                and row.source_season_team_id == "614606"
            ),
        )
        split_fixture = _SplitContractMixin()
        split_results = []
        for stat_type in ("batting", "pitching"):
            parsed = split_fixture.parse_result(stat_type)
            row = next(
                item for item in parsed.rows
                if item.source_player_id == "9698671"
            )
            metric = next(
                item for item in parsed.metric_results
                if item.source_player_id == "9698671"
            )
            for scope in split_fixture.discovery(stat_type):
                split_results.append(replace(
                    parsed,
                    scope_code=scope.scope_code,
                    display_label=scope.display_label,
                    source_identifier=scope.source_identifier,
                    source_url=scope.source_url,
                    source_payload_sha256=hashlib.sha256(
                        f"{stat_type}:{scope.scope_code}:complete".encode()
                    ).hexdigest(),
                    rows=(row,),
                    metric_results=(metric,),
                ))
        return (
            core,
            schedule,
            detail,
            {
                "season": "2026",
                "id": "17040",
                "batting_id": self.BATTING_ID,
                "pitching_id": self.PITCHING_ID,
                "fielding_id": "",
            },
            tuple(split_results),
        )

    def _ready_transport_fixtures(self):
        """Build raw source pages for one complete public-collector run."""
        from bs4 import BeautifulSoup
        from html import escape

        player_id = "9698671"
        core_pages = {
            "batting": self._table(
                "".join(f"<th>{item}</th>" for item in (
                    "Player", "AB", "H", "BB", "K", "HBP", "SF", "SH",
                    "2B", "3B", "HR", "TB",
                )),
                "<tr><td><a href='/players/9698671'>Fixture Player</a></td>"
                "<td>10</td><td>4</td><td>1</td><td>2</td><td>0</td>"
                "<td>0</td><td>0</td><td>1</td><td>0</td><td>0</td><td>5</td></tr>",
            ),
            "pitching": self._table(
                "".join(f"<th>{item}</th>" for item in (
                    "Player", "IP", "H", "R", "ER", "BB", "SO", "HR",
                    "HBP", "BF", "AB", "SF", "ERA",
                )),
                "<tr><td><a href='/players/9698671'>Fixture Player</a></td>"
                "<td>3.0</td><td>2</td><td>1</td><td>1</td><td>1</td>"
                "<td>4</td><td>0</td><td>0</td><td>12</td><td>10</td>"
                "<td>0</td><td>3.00</td></tr>",
            ),
            "fielding": self._table(
                "".join(f"<th>{item}</th>" for item in (
                    "Player", "G", "GS", "PO", "A", "E", "TC", "DP", "FPCT",
                )),
                "<tr><td><a href='/players/9698671'>Fixture Player</a></td>"
                "<td>2</td><td>2</td><td>3</td><td>1</td><td>0</td>"
                "<td>4</td><td>1</td><td>1.000</td></tr>",
            ),
        }
        schedule_html = (
            "<html><body><table data-total-games='2'><thead><tr>"
            "<th>Date</th><th>Opponent</th><th>Result</th>"
            "</tr></thead><tbody>"
            "<tr data-venue='away'><td>05/08/2026</td>"
            "<td><a href='/teams/614584'>Notre Dame</a></td>"
            "<td><a href='/contests/6530192/box_score'>W 1-0</a></td></tr>"
            "<tr data-venue='away'><td>05/09/2026</td>"
            "<td><a href='/teams/614584'>Notre Dame</a></td>"
            "<td><a href='/contests/6530195/box_score'>W 2-1</a></td></tr>"
            "</tbody></table></body></html>"
        )
        catalog = (
            {
                "team_id": "234", "season_team_id": "614606",
                "conference_id": "821", "division": "1",
                "season_id": "17040", "year": "2026",
            },
            {
                "team_id": "147", "season_team_id": "614584",
                "conference_id": "821", "division": "1",
                "season_id": "17040", "year": "2026",
            },
        )

        fixture = BeautifulSoup(
            self._fixture("ncaa_detail/d1/player_detail.html"), "html.parser"
        )
        player_pages = {}
        game_ids = {"6530192", "6530195"}
        for stat_type in ("batting", "pitching", "fielding"):
            section = fixture.find("stat-section", {"data-stat-type": stat_type})
            career_row = section.find(
                "career-totals"
            ).find("row", {"data-source-season-id": "17040"})
            game_rows = [
                row for row in section.find("game-log").find_all("row", recursive=False)
                if row.get("data-source-game-id") in game_ids
            ]

            def render_table(kind, rows):
                identity = (
                    ("Year", "Team") if kind == "career_totals"
                    else ("Date", "Opponent", "Result")
                )
                values = rows[0].find_all("value", recursive=False)
                headers = (*identity, *(item["data-source-header"] for item in values))
                output = [
                    f"<table id='{kind}_{player_id}_player'><thead><tr>",
                    *(f"<th>{escape(header)}</th>" for header in headers),
                    "</tr></thead><tbody>",
                ]
                for row in rows:
                    if kind == "career_totals":
                        cells = (
                            escape(row["data-season-label"]),
                            "<a href='/teams/614606'>Florida St.</a>",
                        )
                    else:
                        game_id = row["data-source-game-id"]
                        cells = (
                            escape(row["data-date-label"]),
                            "<a href='/teams/614584'>Notre Dame</a>",
                            f"<a href='/contests/{game_id}/box_score'>W</a>",
                        )
                    output.append("<tr>")
                    output.extend(f"<td>{cell}</td>" for cell in cells)
                    output.extend(
                        f"<td>{escape(item.get_text(' ', strip=True))}</td>"
                        for item in row.find_all("value", recursive=False)
                    )
                    output.append("</tr>")
                output.append("</tbody></table>")
                return "".join(output)

            player_pages[stat_type] = (
                "<html><body>"
                + render_table("career_totals", [career_row])
                + render_table("game_log", game_rows)
                + "</body></html>"
            )

        split_labels = {
            "batting": (
                "vs. Left-Handed Pitchers", "vs. Right-Handed Pitchers",
                "Runners On", "Bases Empty", "Bases Loaded",
                "Runners in Scoring Position", "Two Outs",
            ),
            "pitching": (
                "vs. Left-Handed Batters", "vs. Right-Handed Batters",
                "Runners On", "Bases Empty", "Bases Loaded",
                "Runners in Scoring Position", "Two Outs",
            ),
        }
        split_controls = {}
        for stat_type, labels in split_labels.items():
            headers = (
                "<th>Player</th><th>AB</th><th>H</th>"
                if stat_type == "batting"
                else "<th>Player</th><th>IP</th><th>ERA</th>"
            )
            controls = "".join(
                f"<button class='sport_stat_menu_option_{index}' "
                f"data-sport-stat-id='{index}' data-split-id='{index}'>"
                f"{label}</button>"
                for index, label in enumerate(labels, start=101)
            )
            split_controls[stat_type] = self._table(headers) + controls
        return core_pages, schedule_html, catalog, player_pages, split_controls

    @staticmethod
    def _split_result_page(stat_type, labels):
        """Return a minimal Playwright-shaped page backed by split fixtures."""
        class Control:
            def __init__(self, page, identifier, label):
                self.page = page
                self.identifier = identifier
                self.label = label

            def count(self):
                return 1

            def get_attribute(self, name):
                return self.identifier if name == "data-sport-stat-id" else None

            def inner_text(self):
                return self.label

            def evaluate(self, _script):
                self.page.active = self.identifier

        class Page:
            active = "101"

            def set_default_timeout(self, _milliseconds):
                return None

            def wait_for_timeout(self, _milliseconds):
                return None

            def locator(self, selector):
                identifier = selector.rsplit("_", 1)[-1]
                return Control(self, identifier, labels[int(identifier) - 101])

            def content(self):
                if stat_type == "batting":
                    headers, values = ("Player", "AB", "H"), ("Fixture Player", "10", "4")
                else:
                    headers, values = ("Player", "P-OAB", "H"), ("Fixture Player", "10", "2")
                return (
                    "<table id='stat_grid'><thead><tr>"
                    + "".join(f"<th>{item}</th>" for item in headers)
                    + "</tr></thead><tbody>"
                    "<tr id='player_9698671'><td><a href='/players/9698671'>"
                    f"{values[0]}</a></td><td></td><td></td></tr>"
                    f"<tr class='sport_stat_{self.active}'><td></td>"
                    f"<td>{values[1]}</td><td>{values[2]}</td></tr>"
                    "</tbody></table>"
                )

        return Page()

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
                "fielding": self._fixture("ncaa_detail/d2/fielding.html"),
                section: self._table(headers),
            }
            with self.subTest(section=section), patch.object(
                scraper, "_fetch_ncaa_pages", return_value=pages
            ):
                snapshot = self._run_real_collector(job)
            self.assertEqual(snapshot.status, "partial")
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

    def test_valid_d1_d2_d3_pages_publish_partial_when_split_transport_is_unavailable(self):
        import roster_scraper as scraper
        from roster_scout.models import (
            BattingFact, PitchingFact, ReportSection, RequestLedger,
        )
        from tests.db_support import clean_database

        for division in (1, 2, 3):
            clean_database(self.database_url)
            job = self._accepted_job(division, f"valid-d{division}")
            pages = {
                "batting": self._fixture(f"d{division}_batting.html"),
                "pitching": self._fixture(f"d{division}_pitching.html"),
                "fielding": self._fixture(
                    f"ncaa_detail/d{division}/fielding.html"
                ),
            }
            with self.subTest(division=division), patch.dict(
                os.environ, {}, clear=False
            ), patch.object(
                scraper,
                "_fetch_ncaa_pages",
                side_effect=self._fixture_page_transport(pages),
            ):
                os.environ.pop("NCAA_BROWSER_PROVIDER", None)
                snapshot = self._run_real_collector(job)
            self.assertEqual(snapshot.status, "partial")
            with self.Session() as session:
                self.assertGreater(
                    session.scalar(select(func.count()).select_from(BattingFact)), 0
                )
                self.assertGreater(
                    session.scalar(select(func.count()).select_from(PitchingFact)), 0
                )
                split_sections = list(session.scalars(select(ReportSection).where(
                    ReportSection.snapshot_id == snapshot.id,
                    ReportSection.section.in_(("splits:batting", "splits:pitching")),
                )))
                self.assertEqual({item.section for item in split_sections}, {
                    "splits:batting", "splits:pitching",
                })
                self.assertTrue(all(item.status == "partial" for item in split_sections))
                split_ledgers = list(session.scalars(select(RequestLedger).where(
                    RequestLedger.snapshot_id == snapshot.id,
                    RequestLedger.family.in_(("splits:batting", "splits:pitching")),
                )))
                self.assertEqual(len(split_ledgers), 2)
                self.assertTrue(all(
                    item.safe_category == "split_transport_unavailable"
                    for item in split_ledgers
                ))
                live_ledgers = list(session.scalars(select(RequestLedger).where(
                    RequestLedger.snapshot_id == snapshot.id,
                    RequestLedger.family.in_(("batting", "pitching")),
                )))
                self.assertEqual(
                    {(item.family, item.pages_consumed) for item in live_ledgers},
                    {("batting", 1), ("pitching", 1)},
                )

    def test_real_collector_complete_phase3_evidence_publishes_ready(self):
        import roster_scraper as scraper
        from roster_scout.models import (
            BattingGameLogFact, CareerSeasonFact, FieldingFact, Game,
            RequestLedger, ScopedBattingFact, ScopedPitchingFact, StatisticScope,
        )
        from scripts import capture_phase3_ncaa_fixtures as capture

        job = self._accepted_job(1, "phase3-real-collector-complete")
        (
            core_pages, schedule_html, catalog, player_pages, split_controls,
        ) = self._ready_transport_fixtures()
        split_labels = {
            "batting": (
                "vs. Left-Handed Pitchers", "vs. Right-Handed Pitchers",
                "Runners On", "Bases Empty", "Bases Loaded",
                "Runners in Scoring Position", "Two Outs",
            ),
            "pitching": (
                "vs. Left-Handed Batters", "vs. Right-Handed Batters",
                "Runners On", "Bases Empty", "Bases Loaded",
                "Runners in Scoring Position", "Two Outs",
            ),
        }
        transport_stages = []

        def fetch_pages(urls, **kwargs):
            reserve = kwargs.get("request_reserver")
            consumer = kwargs.get("page_consumer")
            for key in urls:
                if reserve is not None:
                    reserve(key)
            if consumer is None and (
                "core" not in transport_stages or "fielding" in urls
            ):
                transport_stages.append(
                    "fielding" if set(urls) == {"fielding"} else "core"
                )
                return {key: core_pages[key] for key in urls}
            if consumer is None:
                transport_stages.append("split_controls")
                return {key: split_controls[key] for key in urls}
            transport_stages.append("split_results")
            output = {}
            for key, source_url in urls.items():
                page = self._split_result_page(key, split_labels[key])
                output[key] = consumer(
                    key=key,
                    page=page,
                    source_url=source_url,
                    html=split_controls[key],
                )
            return output

        def fetch_schedule(_url, _limits, *, request_reserver=None, **_kwargs):
            transport_stages.append("schedule")
            request_reserver("schedule")
            return schedule_html

        def fetch_player(_urls, _player_id, _limits, *, request_reserver=None, **_kwargs):
            transport_stages.append("player_detail")
            for stat_type in ("batting", "pitching", "fielding"):
                request_reserver(f"history:{stat_type}")
            return player_pages

        season_contract = {
            "season": "2026", "id": "17040",
            "batting_id": self.BATTING_ID,
            "pitching_id": self.PITCHING_ID,
            "fielding_id": self.FIELDING_ID,
        }
        with patch.dict(
            os.environ,
            {
                "NCAA_BROWSER_PROVIDER": "browserless",
                "PHASE3_LIVE_PAGE_BUDGET": "32",
            },
            clear=False,
        ), patch.object(
            scraper, "_find_ncaa_season", return_value=season_contract
        ), patch.object(
            scraper, "_fetch_ncaa_pages", side_effect=fetch_pages
        ), patch.object(
            capture, "_catalog_rows_for_season", return_value=catalog
        ), patch.object(
            capture, "_bounded_schedule_fetch", side_effect=fetch_schedule
        ), patch.object(
            capture, "_bounded_player_fetch", side_effect=fetch_player
        ):
            snapshot = self._run_real_collector(job)

        self.assertEqual(
            transport_stages,
            [
                "core", "fielding", "schedule", "player_detail",
                "split_controls", "split_results", "split_results",
            ],
        )
        self.assertEqual(snapshot.status, "ready")
        with self.Session() as session:
            ledgers = list(session.scalars(select(RequestLedger).where(
                RequestLedger.snapshot_id == snapshot.id
            )))
            self.assertTrue(ledgers)
            self.assertNotIn("planned", {item.outcome for item in ledgers})
            self.assertTrue({
                "batting", "pitching", "schedule",
                "history:batting", "history:pitching", "history:fielding",
                "splits:batting", "splits:pitching",
            } <= {item.family for item in ledgers})
            self.assertEqual(
                session.scalar(select(func.count()).select_from(Game)), 2
            )
            self.assertEqual(
                session.scalar(select(func.count()).select_from(CareerSeasonFact)), 3
            )
            self.assertGreater(
                session.scalar(select(func.count()).select_from(BattingGameLogFact)), 0
            )
            self.assertEqual(
                session.scalar(select(func.count()).select_from(FieldingFact)), 1
            )
            self.assertEqual(
                session.scalar(
                    select(func.count()).select_from(ScopedBattingFact).join(
                        StatisticScope,
                        StatisticScope.id == ScopedBattingFact.statistic_scope_id,
                    ).where(StatisticScope.scope_kind == "source_split")
                ),
                7,
            )
            self.assertEqual(
                session.scalar(
                    select(func.count()).select_from(ScopedPitchingFact).join(
                        StatisticScope,
                        StatisticScope.id == ScopedPitchingFact.statistic_scope_id,
                    ).where(StatisticScope.scope_kind == "source_split")
                ),
                7,
            )

    def test_live_page_reservation_is_atomic_under_concurrency(self):
        from roster_scout.jobs import _reserve_job_live_pages
        from roster_scout.models import ReportJob

        job = self._accepted_job(1, "phase3-concurrent-budget")
        ready = threading.Barrier(2)
        results = []
        errors = []

        def reserve():
            try:
                ready.wait()
                results.append(_reserve_job_live_pages(
                    self.Session,
                    job_id=job.id,
                    dispatch_generation=1,
                    page_count=1,
                    page_budget=1,
                ))
            except Exception as exc:  # pragma: no cover - assertion reports details
                errors.append(exc)

        threads = [threading.Thread(target=reserve) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)

        self.assertFalse(errors)
        self.assertEqual(sorted(results, key=lambda item: item is None), [0, None])
        with self.Session() as session:
            stored = session.get(ReportJob, job.id)
            self.assertEqual(stored.phase3_live_pages_consumed, 1)

    def test_retry_generation_reuses_durable_budget_without_ledger_collision(self):
        import roster_scraper as scraper
        from roster_scout.jobs import RetryableJobError
        from roster_scout.models import ReportJob, RequestLedger

        job = self._accepted_job(1, "phase3-retry-budget")
        pages = {
            "batting": self._fixture("d1_batting.html"),
            "pitching": self._fixture("d1_pitching.html"),
            "fielding": self._fixture("ncaa_detail/d1/fielding.html"),
        }
        blocked = scraper.NcaaAccessError("fixture transport blocked")
        with patch.dict(os.environ, {}, clear=False), patch.object(
            scraper,
            "_fetch_ncaa_pages",
            side_effect=self._fixture_page_transport(pages, error=blocked),
        ):
            os.environ.pop("NCAA_BROWSER_PROVIDER", None)
            with self.assertRaises(RetryableJobError):
                self._run_real_collector(job, generation=1)

        with self.Session() as session:
            retried = session.get(ReportJob, job.id)
            self.assertEqual(retried.dispatch_generation, 2)
            self.assertEqual(retried.phase3_live_pages_consumed, 2)

        with patch.dict(os.environ, {}, clear=False), patch.object(
            scraper,
            "_fetch_ncaa_pages",
            side_effect=self._fixture_page_transport(pages),
        ):
            os.environ.pop("NCAA_BROWSER_PROVIDER", None)
            snapshot = self._run_real_collector(job, generation=2)

        self.assertEqual(snapshot.status, "partial")
        with self.Session() as session:
            stored = session.get(ReportJob, job.id)
            ledgers = list(session.scalars(
                select(RequestLedger)
                .where(RequestLedger.job_id == job.id)
                .order_by(
                    RequestLedger.dispatch_generation,
                    RequestLedger.family,
                )
            ))
            self.assertEqual(stored.phase3_live_pages_consumed, 5)
            self.assertEqual(
                sum(item.pages_consumed for item in ledgers),
                stored.phase3_live_pages_consumed,
            )
            self.assertEqual(
                {
                    (item.dispatch_generation, item.family, item.pages_consumed)
                    for item in ledgers
                    if item.family in {"batting", "pitching"}
                },
                {
                    (1, "batting", 1),
                    (1, "pitching", 1),
                    (2, "batting", 1),
                    (2, "pitching", 1),
                },
            )
            self.assertNotIn("planned", {item.outcome for item in ledgers})

    def test_complete_retry_generation_ignores_prior_failed_ledgers(self):
        import roster_scraper as scraper
        from roster_scout.jobs import RetryableJobError
        from roster_scout.models import RequestLedger

        job = self._accepted_job(1, "phase3-complete-retry-generation")
        pages = {
            "batting": self._fixture("d1_batting.html"),
            "pitching": self._fixture("d1_pitching.html"),
        }
        with patch.dict(os.environ, {}, clear=False), patch.object(
            scraper,
            "_fetch_ncaa_pages",
            side_effect=self._fixture_page_transport(
                pages, error=scraper.NcaaAccessError("fixture blocked")
            ),
        ):
            os.environ.pop("NCAA_BROWSER_PROVIDER", None)
            with self.assertRaises(RetryableJobError):
                self._run_real_collector(job, generation=1)

        core, schedule, detail, season_contract, split_results = (
            self._complete_phase3_evidence()
        )
        with patch.dict(
            os.environ, {"NCAA_BROWSER_PROVIDER": "browserless"}, clear=False
        ), patch.object(
            scraper, "_find_ncaa_season", return_value=season_contract
        ), patch.object(
            scraper,
            "collect_ncaa_by_id",
            side_effect=self._reserving_collector_result(
                Mock(), ("batting", "pitching")
            ),
        ), patch(
            "roster_scout.canonical.to_collected_sections", return_value=core
        ), patch.object(
            scraper,
            "collect_ncaa_schedule_evidence",
            side_effect=self._reserving_collector_result(schedule, ("schedule",)),
        ), patch.object(
            scraper,
            "collect_ncaa_player_detail_evidence",
            side_effect=self._reserving_collector_result(
                detail, ("history:batting", "history:pitching", "history:fielding")
            ),
        ), patch.object(
            scraper,
            "collect_ncaa_split_results",
            side_effect=self._reserving_collector_result(
                split_results, ("splits:batting", "splits:pitching")
            ),
        ):
            snapshot = self._run_real_collector(job, generation=2)

        self.assertEqual(snapshot.status, "ready")
        with self.Session() as session:
            ledgers = list(session.scalars(select(RequestLedger).where(
                RequestLedger.job_id == job.id
            )))
            self.assertIn(
                "live_failed",
                {
                    item.outcome for item in ledgers
                    if item.dispatch_generation == 1
                },
            )
            self.assertNotIn(
                "planned",
                {
                    item.outcome for item in ledgers
                    if item.dispatch_generation == 2
                },
            )

    def test_reconciliation_terminalizes_planned_generation_before_redelivery(self):
        import roster_scraper as scraper
        from roster_scout.jobs import (
            _new_or_existing_snapshot,
            reconcile_stale_report_jobs,
        )
        from roster_scout.models import ReportJob, RequestLedger

        job = self._accepted_job(1, "phase3-interrupted-generation")
        stale_at = datetime.now(UTC) - timedelta(hours=2)
        with self.Session.begin() as session:
            stored = session.get(ReportJob, job.id)
            stored.status = "running"
            stored.started_generation = 1
            stored.attempt_count = 1
            stored.started_at = stale_at
            stored.phase3_live_pages_consumed = 1
            snapshot = _new_or_existing_snapshot(session, stored)
            session.add(RequestLedger(
                organization_id=stored.organization_id,
                job_id=stored.id,
                snapshot_id=snapshot.id,
                dispatch_generation=1,
                family="batting",
                source_identity="ncaa:batting:interrupted",
                request_kind="planned",
                outcome="planned",
                started_at=stale_at,
                budget_remaining=24,
                pages_consumed=1,
            ))

        result = reconcile_stale_report_jobs(
            self.Session,
            now=datetime.now(UTC),
            running_timeout=timedelta(minutes=30),
        )
        self.assertEqual(result.reopened, 1)
        with self.Session() as session:
            stored = session.get(ReportJob, job.id)
            interrupted = session.scalar(select(RequestLedger).where(
                RequestLedger.job_id == job.id,
                RequestLedger.dispatch_generation == 1,
            ))
            self.assertEqual(stored.dispatch_generation, 2)
            self.assertEqual(stored.phase3_live_pages_consumed, 1)
            self.assertEqual(
                (interrupted.outcome, interrupted.safe_category),
                ("interrupted", "worker_interrupted"),
            )
            self.assertIsNotNone(interrupted.completed_at)

        core, schedule, detail, season_contract, split_results = (
            self._complete_phase3_evidence()
        )
        with patch.dict(
            os.environ, {"NCAA_BROWSER_PROVIDER": "browserless"}, clear=False
        ), patch.object(
            scraper, "_find_ncaa_season", return_value=season_contract
        ), patch.object(
            scraper,
            "collect_ncaa_by_id",
            side_effect=self._reserving_collector_result(
                Mock(), ("batting", "pitching")
            ),
        ), patch(
            "roster_scout.canonical.to_collected_sections", return_value=core
        ), patch.object(
            scraper,
            "collect_ncaa_schedule_evidence",
            side_effect=self._reserving_collector_result(schedule, ("schedule",)),
        ), patch.object(
            scraper,
            "collect_ncaa_player_detail_evidence",
            side_effect=self._reserving_collector_result(
                detail, ("history:batting", "history:pitching", "history:fielding")
            ),
        ), patch.object(
            scraper,
            "collect_ncaa_split_results",
            side_effect=self._reserving_collector_result(
                split_results, ("splits:batting", "splits:pitching")
            ),
        ):
            snapshot = self._run_real_collector(job, generation=2)
        self.assertEqual(snapshot.status, "ready")

    def test_supplemental_deadline_persists_sanitized_unavailable_sections(self):
        import roster_scraper as scraper
        from roster_scout.collection_control import CollectionDeadlineExceeded
        from roster_scout.models import ReportSection, RequestLedger

        job = self._accepted_job(1, "phase3-deadline-state")
        pages = {
            "batting": self._fixture("d1_batting.html"),
            "pitching": self._fixture("d1_pitching.html"),
            "fielding": self._fixture("ncaa_detail/d1/fielding.html"),
        }

        def deadline(family):
            def collect(*_args, request_reserver=None, **_kwargs):
                request_reserver(family)
                raise CollectionDeadlineExceeded("fixture deadline")
            return collect

        with patch.dict(
            os.environ, {"NCAA_BROWSER_PROVIDER": "browserless"}, clear=False
        ), patch.object(
            scraper,
            "_fetch_ncaa_pages",
            side_effect=self._fixture_page_transport(pages),
        ), patch.object(
            scraper,
            "collect_ncaa_schedule_evidence",
            side_effect=deadline("schedule"),
        ), patch.object(
            scraper,
            "collect_ncaa_split_results",
            side_effect=deadline("splits:batting"),
        ):
            snapshot = self._run_real_collector(job)

        self.assertEqual(snapshot.status, "partial")
        with self.Session() as session:
            sections = list(session.scalars(select(ReportSection).where(
                ReportSection.snapshot_id == snapshot.id,
                ReportSection.section.in_(("schedule", "splits:batting")),
            )))
            self.assertEqual({item.section for item in sections}, {
                "schedule", "splits:batting",
            })
            self.assertEqual(
                {
                    (item.section, item.status, item.failure_category)
                    for item in sections
                },
                {
                    ("schedule", "unavailable", "collection_timeout"),
                    ("splits:batting", "partial", "collection_timeout"),
                },
            )
            ledgers = list(session.scalars(select(RequestLedger).where(
                RequestLedger.snapshot_id == snapshot.id,
                RequestLedger.family.in_(("schedule", "splits:batting")),
            )))
            self.assertTrue(all(
                item.outcome == "live_failed"
                and item.safe_category == "collection_timeout"
                and item.pages_consumed == 1
                for item in ledgers
            ))

    def test_public_collector_transport_cap_persists_budget_exhaustion(self):
        import roster_scraper as scraper
        from roster_scout.models import ReportSection, RequestLedger

        job = self._accepted_job(1, "phase3-public-transport-budget")
        batting_url = (
            "https://stats.ncaa.org/teams/614606/season_to_date_stats"
            f"?year_stat_category_id={self.BATTING_ID}"
        )
        pitching_url = (
            "https://stats.ncaa.org/teams/614606/season_to_date_stats"
            f"?year_stat_category_id={self.PITCHING_ID}"
        )
        pages = {
            batting_url: self._fixture("d1_batting.html"),
            pitching_url: self._fixture("d1_pitching.html"),
        }
        page = Mock()
        page.url = batting_url
        page.goto.side_effect = lambda url, **_kwargs: setattr(page, "url", url)
        page.content.side_effect = lambda: pages[page.url]
        browser = Mock()
        browser.contexts = [Mock(pages=[page])]
        playwright = Mock()
        playwright.chromium.connect_over_cdp.return_value = browser
        starter = Mock()
        starter.start.return_value = playwright
        response = Mock(status_code=200)
        response.json.return_value = {
            "browserWSEndpoint": "wss://example.test/browser"
        }

        with patch.dict(os.environ, {
            "NCAA_BROWSER_PROVIDER": "browserless",
            "BROWSERLESS_TOKEN": "test-only-token",
            "PHASE3_LIVE_PAGE_BUDGET": "2",
        }, clear=False), patch.object(
            scraper.requests, "post", return_value=response
        ), patch(
            "scripts.capture_phase3_ncaa_fixtures._catalog_rows_for_season",
            return_value=({
                "team_id": "234",
                "season_team_id": "614606",
                "conference_id": "821",
                "division": "1",
                "year": "2026",
            },),
        ), patch(
            "patchright.sync_api.sync_playwright", return_value=starter
        ):
            snapshot = self._run_real_collector(job)

        self.assertEqual(snapshot.status, "partial")
        with self.Session() as session:
            ledgers = list(session.scalars(select(RequestLedger).where(
                RequestLedger.snapshot_id == snapshot.id
            )))
            self.assertEqual(
                {
                    (item.family, item.outcome, item.safe_category)
                    for item in ledgers
                    if item.family in {"fielding", "schedule"}
                },
                {
                    (
                        "fielding", "budget_exhausted",
                        "request_budget_exhausted",
                    ),
                    (
                        "schedule", "budget_exhausted",
                        "request_budget_exhausted",
                    ),
                },
            )
            sections = list(session.scalars(select(ReportSection).where(
                ReportSection.snapshot_id == snapshot.id,
                ReportSection.section.in_(("fielding", "schedule")),
            )))
            self.assertTrue(all(
                item.failure_category == "request_budget_exhausted"
                for item in sections
            ))

    def test_real_collector_budget_boundary_leaves_no_planned_ledgers(self):
        import roster_scraper as scraper
        from roster_scout.models import ReportSection, RequestLedger

        job = self._accepted_job(1, "phase3-real-collector-budget-boundary")
        core, schedule, detail, season_contract, split_results = (
            self._complete_phase3_evidence()
        )
        split_collector = Mock(side_effect=self._reserving_collector_result(
            split_results, ("splits:batting", "splits:pitching")
        ))
        with patch.dict(os.environ, {
            "NCAA_BROWSER_PROVIDER": "browserless",
            "PHASE3_LIVE_PAGE_BUDGET": "5",
        }, clear=False), patch.object(
            scraper, "_find_ncaa_season", return_value=season_contract
        ), patch.object(
            scraper,
            "collect_ncaa_by_id",
            side_effect=self._reserving_collector_result(
                Mock(), ("batting", "pitching")
            ),
        ), patch(
            "roster_scout.canonical.to_collected_sections", return_value=core
        ), patch.object(
            scraper,
            "collect_ncaa_schedule_evidence",
            side_effect=self._reserving_collector_result(schedule, ("schedule",)),
        ), patch.object(
            scraper,
            "collect_ncaa_player_detail_evidence",
            side_effect=self._reserving_collector_result(
                detail, ("history:batting", "history:pitching", "history:fielding")
            ),
        ), patch.object(
            scraper,
            "collect_ncaa_split_results",
            split_collector,
        ):
            snapshot = self._run_real_collector(job)

        self.assertEqual(snapshot.status, "partial")
        split_collector.assert_called_once()
        with self.Session() as session:
            ledgers = list(session.scalars(select(RequestLedger).where(
                RequestLedger.snapshot_id == snapshot.id
            )))
            self.assertNotIn("planned", {item.outcome for item in ledgers})
            split_ledgers = [
                item for item in ledgers if item.family.startswith("splits:")
            ]
            self.assertEqual(len(split_ledgers), 2)
            self.assertTrue(all(
                item.outcome == "budget_exhausted"
                and item.safe_category == "request_budget_exhausted"
                for item in split_ledgers
            ))
            split_sections = list(session.scalars(select(ReportSection).where(
                ReportSection.snapshot_id == snapshot.id,
                ReportSection.section.in_(("splits:batting", "splits:pitching")),
            )))
            self.assertEqual(len(split_sections), 2)
            self.assertTrue(all(
                item.failure_category == "request_budget_exhausted"
                for item in split_sections
            ))

    def test_real_collector_persists_schedule_history_and_typed_game_logs(self):
        import roster_scraper as scraper
        from roster_scout.jobs import CollectedSection
        from roster_scout.models import (
            BattingGameLogFact, CareerAggregate, CareerSeasonFact, Game,
            Player, PlayerTeamSeason, Report, ReportSection, RequestLedger,
            Season, SourceRecord, TeamSeason,
        )
        from roster_scout.ncaa_detail import (
            parse_player_detail_fixture, parse_schedule_fixture,
        )

        job = self._accepted_job(1, "phase3-real-collector")
        # Legacy reports used the year as the season identity and did not
        # persist NCAA category IDs. Live collection must recover those exact
        # identities from the authoritative season contract.
        with self.Session.begin() as session:
            report = session.get(Report, job.report_id)
            team_season = session.get(TeamSeason, report.team_season_id)
            season = session.get(Season, team_season.season_id)
            season.source_season_id = "2026"
            team_season.batting_stat_category_id = None
            team_season.pitching_stat_category_id = None
        base = FullSeasonMetricPublicationTests.detailed_sections()
        core = tuple(
            CollectedSection(
                **{
                    **section.__dict__,
                    "rows": ({
                        **section.rows[0],
                        "_player_id": "9698671",
                        "_player_url": "https://stats.ncaa.org/players/9698671",
                    },),
                    "source_team_id": "234",
                    "source_season_id": "17040",
                    "source_season_team_id": "614606",
                    "source_stat_category_id": (
                        self.BATTING_ID if section.name == "batting" else self.PITCHING_ID
                    ),
                }
            )
            for section in base
        )
        schedule = parse_schedule_fixture(
            self._fixture("ncaa_detail/d1/schedule.html"),
            expected_season_id="17040",
            expected_team_id="234",
            expected_season_team_id="614606",
            expected_division="1",
        )
        detail = parse_player_detail_fixture(
            self._fixture("ncaa_detail/d1/player_detail.html"),
            expected_player_id="9698671",
            expected_season_id="17040",
            expected_season_team_id="614606",
            expected_division="1",
        )
        season_contract = {
            "season": "2026", "id": "17040",
            "batting_id": self.BATTING_ID,
            "pitching_id": self.PITCHING_ID,
            "fielding_id": "",
        }
        with patch.dict(os.environ, {"NCAA_BROWSER_PROVIDER": "browserless"}), patch.object(
            scraper, "_find_ncaa_season", return_value=season_contract
        ), patch.object(
            scraper, "collect_ncaa_by_id", return_value=Mock()
        ), patch(
            "roster_scout.canonical.to_collected_sections", return_value=core
        ), patch.object(
            scraper, "collect_ncaa_schedule_evidence", return_value=schedule
        ), patch.object(
            scraper, "collect_ncaa_player_detail_evidence", return_value=detail
        ), patch.object(
            scraper, "collect_ncaa_split_results",
            side_effect=scraper.NcaaAccessError("split unavailable"),
        ):
            snapshot = self._run_real_collector(job)

        self.assertEqual(snapshot.status, "partial")
        with self.Session() as session:
            section_names = set(session.scalars(select(ReportSection.section).where(
                ReportSection.snapshot_id == snapshot.id
            )))
            self.assertTrue({
                "schedule", "game_logs:batting", "game_logs:pitching",
                "game_logs:fielding", "history:batting", "history:pitching",
                "history:fielding", "splits:batting", "splits:pitching",
            } <= section_names)
            self.assertGreater(session.scalar(select(func.count()).select_from(Game)), 0)
            self.assertGreater(
                session.scalar(select(func.count()).select_from(CareerSeasonFact)), 0
            )
            self.assertEqual(
                session.scalar(select(func.count()).select_from(CareerAggregate)), 3
            )
            self.assertGreater(
                session.scalar(select(func.count()).select_from(BattingGameLogFact)), 0
            )
            detail_sources = list(session.scalars(select(SourceRecord).where(
                SourceRecord.snapshot_id == snapshot.id,
                SourceRecord.section.in_((
                    "history:batting", "history:pitching", "history:fielding",
                    "game_logs:batting", "game_logs:pitching", "game_logs:fielding",
                )),
            )))
            self.assertTrue(detail_sources)
            self.assertTrue(all(
                item.source_url == "https://stats.ncaa.org/players/9698671"
                and item.source_stat_category_id in {
                    self.BATTING_ID, self.PITCHING_ID, "15869",
                }
                and item.source_stat_category_id
                == item.raw_values["source_stat_category_id"]
                for item in detail_sources
            ))
            self.assertEqual(
                {item.payload_sha256 for item in detail_sources},
                {
                    row.source_payload_sha256
                    for row in (*detail.career_seasons, *detail.game_logs)
                },
            )
            families = set(session.scalars(select(RequestLedger.family).where(
                RequestLedger.snapshot_id == snapshot.id
            )))
            self.assertTrue({
                "schedule", "history:batting", "history:pitching",
                "history:fielding", "splits:batting", "splits:pitching",
            } <= families)
            identity, player = session.execute(
                select(PlayerTeamSeason, Player)
                .join(Player, Player.id == PlayerTeamSeason.player_id)
                .where(
                    PlayerTeamSeason.snapshot_id == snapshot.id,
                    Player.source_player_id == "9698671",
                )
            ).one()
            from roster_scout.player_detail import load_snapshot_player_detail

            drill = load_snapshot_player_detail(
                session,
                job.organization_id,
                job.report_id,
                snapshot.id,
                player.id,
            )
            self.assertTrue(drill["data"]["career"]["seasons"])
            self.assertTrue(drill["data"]["career"]["aggregate"])
            self.assertTrue(drill["data"]["games"])
            self.assertEqual(
                {
                    item["source_stat_category_id"]
                    for item in drill["data"]["career"]["seasons"]
                },
                {self.BATTING_ID, self.PITCHING_ID, "15869"},
            )

class ComputedScopePublicationTests(PostgreSQLJobTestCase):
    def _sections(self, game_count):
        from roster_scout.jobs import (
            CollectedGameFact,
            to_collected_schedule_sections,
        )
        from roster_scout.models import Report, Season, Team, TeamSeason
        from roster_scout.ncaa_detail import ScheduleEvidence, ScheduleGameEvidence

        with self.Session() as session:
            report = session.scalar(select(Report))
            team_season = session.get(TeamSeason, report.team_season_id)
            team = session.get(Team, team_season.team_id)
            season = session.get(Season, team_season.season_id)
            source = (
                team.source_team_id,
                season.source_season_id,
                team_season.source_season_team_id,
            )
        games = tuple(
            ScheduleGameEvidence(
                source_game_id=f"game-{index:02d}",
                game_date=datetime(2026, 3, index, tzinfo=UTC).date(),
                status="completed",
                opponent_team_id=f"opponent-{index}",
                opponent_season_team_id=f"opponent-season-{index}",
                opponent_conference_id="821",
                conference_evidence_status="present",
                opponent_identity_status="present",
                doubleheader_ordinal=1,
                result_sha256=f"{index:064x}",
                venue="home",
            )
            for index in range(1, game_count + 1)
        )
        schedule = ScheduleEvidence(
            source_path=f"/team/{source[0]}/{source[1]}",
            source_url=f"https://stats.ncaa.org/team/{source[0]}/{source[1]}",
            source_payload_sha256="a" * 64,
            source_season_id=source[1],
            source_team_id=source[0],
            source_season_team_id=source[2],
            team_conference_id="821",
            division="d2",
            schedule_complete=True,
            collected_at=datetime(2026, 4, 1, tzinfo=UTC),
            games=games,
        )
        facts = []
        for game in games:
            facts.extend((
                CollectedGameFact(
                    f"bat-{game.source_game_id}", game.source_game_id, "100",
                    "batting", {"at_bats": 2, "hits": 1},
                ),
                CollectedGameFact(
                    f"pit-{game.source_game_id}", game.source_game_id, "100",
                    "pitching", {
                        "earned_runs": 1, "innings_outs": 3,
                        "opponent_at_bats": 2, "hits_allowed": 1,
                    },
                ),
                CollectedGameFact(
                    f"fld-{game.source_game_id}", game.source_game_id, "100",
                    "fielding", {
                        "putouts": 2, "assists": 1, "errors": 0,
                        "total_chances": 3,
                    },
                ),
            ))
        return (
            *FullSeasonMetricPublicationTests.detailed_sections(),
            *to_collected_schedule_sections(
                schedule,
                game_facts=tuple(facts),
                complete_stat_types=frozenset({"batting", "pitching", "fielding"}),
            ),
        )

    def test_refresh_replaces_last_ten_only_in_successor_snapshot(self):
        from roster_scout.jobs import accept_report_job, run_report_job
        from roster_scout.models import (
            Game, ScopedBattingFact, StatisticScope, StatisticScopeGame,
        )

        organization_id, report_id = self.fixture()
        first_job = accept_report_job(
            self.Session, organization_id, report_id, "computed-first", dispatch=None
        )
        first = run_report_job(
            self.Session, first_job.id,
            collector=Mock(return_value=self._sections(11)),
        )
        second_job = accept_report_job(
            self.Session, organization_id, report_id, "computed-refresh", dispatch=None
        )
        second = run_report_job(
            self.Session, second_job.id,
            collector=Mock(return_value=self._sections(12)),
        )
        self.assertEqual((first.status, second.status), ("partial", "partial"))
        with self.Session() as session:
            scopes = list(session.scalars(select(StatisticScope).where(
                StatisticScope.scope_kind == "computed_game"
            )))
            self.assertEqual(len(scopes), 12)
            first_scope = next(
                scope for scope in scopes
                if scope.snapshot_id == first.id
                and scope.stat_type == "batting"
                and scope.scope_code == "last_10"
            )
            second_scope = next(
                scope for scope in scopes
                if scope.snapshot_id == second.id
                and scope.stat_type == "batting"
                and scope.scope_code == "last_10"
            )
            def members(scope):
                return tuple(session.scalars(
                    select(Game.source_game_id)
                    .join(StatisticScopeGame, StatisticScopeGame.game_id == Game.id)
                    .where(StatisticScopeGame.statistic_scope_id == scope.id)
                    .order_by(StatisticScopeGame.membership_ordinal)
                ))
            self.assertEqual(members(first_scope), tuple(
                f"game-{index:02d}" for index in range(2, 12)
            ))
            self.assertEqual(members(second_scope), tuple(
                f"game-{index:02d}" for index in range(3, 13)
            ))
            self.assertEqual(
                session.scalar(select(func.count()).select_from(ScopedBattingFact)),
                4,
            )

    def test_publication_rejects_disconnected_computed_fact_lineage(self):
        from roster_scout.jobs import (
            _new_or_existing_snapshot,
            _persist_canonical,
            _persist_sections,
            accept_report_job,
        )
        from roster_scout.models import ReportJob, ReportSnapshot, ScopedBattingFact
        from roster_scout.snapshots import SnapshotPublicationError, publish_snapshot

        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session, organization_id, report_id, "computed-invalid", dispatch=None
        )
        sections = self._sections(11)
        with self.Session.begin() as session:
            stored = session.get(ReportJob, job.id)
            snapshot = _new_or_existing_snapshot(session, stored)
            _persist_sections(session, stored, snapshot, sections)
            snapshot_id = snapshot.id
        with self.Session.begin() as session:
            stored = session.get(ReportJob, job.id)
            snapshot = session.get(ReportSnapshot, snapshot_id)
            _persist_canonical(session, stored, snapshot, sections)
            session.flush()
            fact = session.scalar(select(ScopedBattingFact))
            fact.contributing_fact_ids = ["disconnected-fact"]
            session.flush()
            with self.assertRaisesRegex(
                SnapshotPublicationError, "computed fact source parity failed"
            ):
                publish_snapshot(session, snapshot_id)


if __name__ == "__main__":
    unittest.main()
