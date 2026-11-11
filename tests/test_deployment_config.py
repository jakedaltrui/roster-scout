"""Deployment topology and local-service exposure regressions."""

import copy
import inspect
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import tomllib
import unittest
from urllib.parse import urlsplit
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from sqlalchemy import text

from tests.test_report_jobs import PostgreSQLJobTestCase


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def render_compose():
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--profile",
            "worker",
            "--profile",
            "scheduler",
            "config",
            "--format",
            "json",
        ],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def validate_local_service_bindings(config):
    for service_name, expected_port in (("postgres", 5432), ("redis", 6379)):
        service = config["services"][service_name]
        if service.get("network_mode") == "host":
            raise ValueError(f"{service_name} must not use host networking")
        ports = service.get("ports", [])
        if len(ports) != 1:
            raise ValueError(f"{service_name} must publish one loopback port")
        port = ports[0]
        if port.get("host_ip") != "127.0.0.1":
            raise ValueError(f"{service_name} must bind only to 127.0.0.1")
        if int(port.get("published", 0)) != expected_port:
            raise ValueError(f"{service_name} host port drifted")
        if int(port.get("target", 0)) != expected_port:
            raise ValueError(f"{service_name} container port drifted")


class LocalServiceBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = render_compose()

    def test_postgresql_and_redis_publish_only_on_loopback(self):
        validate_local_service_bindings(self.config)

    def test_app_and_worker_keep_internal_service_dns(self):
        services = self.config["services"]
        for service_name in ("roster-scout", "worker"):
            environment = services[service_name]["environment"]
            with self.subTest(service=service_name, dependency="postgres"):
                self.assertIn("@postgres:5432/", environment["DATABASE_URL"])
            with self.subTest(service=service_name, dependency="redis"):
                self.assertIn("redis:6379/", environment["CELERY_BROKER_URL"])

    def test_validator_rejects_wildcard_bare_and_host_network_exposure(self):
        unsafe_variants = []
        wildcard = copy.deepcopy(self.config)
        wildcard["services"]["postgres"]["ports"][0]["host_ip"] = "0.0.0.0"
        unsafe_variants.append(("wildcard", wildcard))

        bare = copy.deepcopy(self.config)
        bare["services"]["redis"]["ports"][0].pop("host_ip", None)
        unsafe_variants.append(("bare", bare))

        host_network = copy.deepcopy(self.config)
        host_network["services"]["postgres"]["network_mode"] = "host"
        unsafe_variants.append(("host-network", host_network))

        for name, config in unsafe_variants:
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_local_service_bindings(config)

    def test_example_environment_marks_local_credentials_as_nonproduction(self):
        contents = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("loopback development-only", contents)
        self.assertIn("authenticated, TLS-protected DATABASE_URL", contents)
        self.assertNotIn("postgresql+psycopg://roster_scout:roster_scout@", contents)
        self.assertNotIn("redis://redis:6379", contents)


class BrowserlessCapacityConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compose = render_compose()
        with (PROJECT_ROOT / "fly.toml").open("rb") as handle:
            cls.fly = tomllib.load(handle)

    def test_app_and_worker_share_backend_namespace_and_account_limit(self):
        expected = {
            "BROWSERLESS_CAPACITY_BACKEND": "postgresql",
            "BROWSERLESS_CAPACITY_NAMESPACE": "roster-scout-browserless-account",
            "BROWSERLESS_ACCOUNT_MAX_SESSIONS": "2",
        }
        compose_services = self.compose["services"]
        for service_name in ("roster-scout", "worker"):
            environment = compose_services[service_name]["environment"]
            with self.subTest(platform="compose", service=service_name):
                self.assertEqual(
                    {key: environment.get(key) for key in expected},
                    expected,
                )
        self.assertEqual(
            {key: self.fly["env"].get(key) for key in expected},
            expected,
        )

    def test_worker_concurrency_is_bounded_by_account_limit(self):
        from scripts.run_worker import worker_command

        fly_environment = self.fly["env"]
        command = worker_command(fly_environment)
        self.assertEqual(command[-2:], ["--concurrency", "1"])
        with self.assertRaisesRegex(ValueError, "cannot exceed"):
            worker_command({
                **fly_environment,
                "CELERY_WORKER_CONCURRENCY": "2",
                "BROWSERLESS_ACCOUNT_MAX_SESSIONS": "1",
            })

    def test_fly_http_service_and_data_mount_are_app_only(self):
        self.assertEqual(self.fly["http_service"]["processes"], ["app"])
        self.assertEqual(len(self.fly["mounts"]), 1)
        self.assertEqual(self.fly["mounts"][0]["processes"], ["app"])
        self.assertIn("worker", self.fly["processes"])
        self.assertNotIn("gunicorn", self.fly["processes"]["worker"])

    def test_fly_config_contains_no_embedded_service_credentials(self):
        sensitive_names = {
            "BROWSERLESS_TOKEN",
            "DATABASE_URL",
            "CELERY_BROKER_URL",
        }
        self.assertTrue(sensitive_names.isdisjoint(self.fly["env"]))
        source = (PROJECT_ROOT / "fly.toml").read_text(encoding="utf-8")
        self.assertNotIn("postgresql://", source)
        self.assertNotIn("postgresql+psycopg://", source)
        self.assertNotIn("redis://", source)


class DownloadAcknowledgementPublicOriginConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with (PROJECT_ROOT / "fly.toml").open("rb") as handle:
            cls.fly = tomllib.load(handle)

    def test_fly_declares_exact_public_https_origin_at_internal_http_edge(self):
        origin = self.fly["env"].get("ROSTER_SCOUT_PUBLIC_ORIGIN")
        self.assertEqual(origin, "https://roster-scout.fly.dev")
        parsed = urlsplit(origin)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(parsed.netloc, "roster-scout.fly.dev")
        self.assertEqual((parsed.path, parsed.query, parsed.fragment), ("", "", ""))
        self.assertEqual(self.fly["http_service"]["internal_port"], 5000)
        self.assertTrue(self.fly["http_service"]["force_https"])

    def test_manifest_keeps_acknowledgement_secret_out_of_nonsecret_env(self):
        self.assertNotIn(
            "ROSTER_SCOUT_DOWNLOAD_ACK_SECRET",
            self.fly["env"],
        )


class WorkerRetentionPreflightTests(unittest.TestCase):
    def test_invalid_retention_is_rejected_before_worker_command_is_built(self):
        from scripts.run_worker import worker_command

        for value in ("29", "", "not-an-integer", "0", "-1"):
            with self.subTest(value=value), self.assertRaisesRegex(
                RuntimeError,
                "invalid failure evidence retention configuration",
            ):
                worker_command({
                    "FAILURE_EVIDENCE_RETENTION_DAYS": value,
                    "CELERY_WORKER_CONCURRENCY": "1",
                    "BROWSERLESS_ACCOUNT_MAX_SESSIONS": "1",
                })

    def test_retention_boundaries_preserve_worker_command(self):
        from scripts.run_worker import worker_command

        for value in ("1", "28"):
            with self.subTest(value=value):
                command = worker_command({
                    "FAILURE_EVIDENCE_RETENTION_DAYS": value,
                    "CELERY_WORKER_CONCURRENCY": "1",
                    "BROWSERLESS_ACCOUNT_MAX_SESSIONS": "1",
                })
            self.assertEqual(command[-2:], ["--concurrency", "1"])

    def test_main_does_not_touch_cache_or_exec_for_invalid_retention(self):
        from scripts import run_worker

        with patch.dict(
            "os.environ",
            {"FAILURE_EVIDENCE_RETENTION_DAYS": "29"},
            clear=True,
        ), patch.object(run_worker.os, "makedirs") as makedirs, patch.object(
            run_worker.os,
            "execvp",
        ) as execvp:
            with self.assertRaisesRegex(
                RuntimeError,
                "invalid failure evidence retention configuration",
            ):
                run_worker.main()

        makedirs.assert_not_called()
        execvp.assert_not_called()


class DispatchSchedulerEntrypointTests(unittest.TestCase):
    def test_valid_configuration_invokes_bounded_service_boundary(self):
        from scripts.run_scheduler import SchedulerConfig, run_scheduler

        config = SchedulerConfig.from_environ({
            "REPORT_DISPATCH_POLL_SECONDS": "7",
            "REPORT_DISPATCH_LEASE_SECONDS": "45",
            "REPORT_QUEUED_REDISPATCH_SECONDS": "180",
            "REPORT_RUNNING_STALE_SECONDS": "900",
        })
        session_factory = object()
        drain = Mock(return_value=_DrainResult(claimed=2, published=1, released=1))
        reconcile = Mock(
            return_value=_ReconcileResult(reopened=3, closed=4, active=1)
        )
        dispatch = Mock()

        run_scheduler(
            config,
            session_factory=session_factory,
            dispatch=dispatch,
            drain=drain,
            reconcile=reconcile,
            stop_event=threading.Event(),
            max_cycles=1,
        )

        reconcile.assert_called_once_with(
            session_factory,
            queued_timeout=config.queued_redispatch_deadline,
            running_timeout=config.running_stale_threshold,
        )
        drain.assert_called_once_with(
            session_factory,
            dispatch=dispatch,
            limit=10,
            lease_duration=config.dispatch_lease_duration,
            redispatch_delay=config.queued_redispatch_deadline,
            failure_backoff=config.poll_interval,
        )

    def test_invalid_timing_values_fail_before_startup(self):
        from scripts.run_scheduler import SchedulerConfig

        variables = (
            "REPORT_DISPATCH_POLL_SECONDS",
            "REPORT_DISPATCH_LEASE_SECONDS",
            "REPORT_QUEUED_REDISPATCH_SECONDS",
            "REPORT_RUNNING_STALE_SECONDS",
        )
        for variable in variables:
            with self.subTest(variable=variable):
                with self.assertRaisesRegex(ValueError, variable):
                    SchedulerConfig.from_environ({variable: "0"})
                with self.assertRaisesRegex(ValueError, variable):
                    SchedulerConfig.from_environ({variable: "not-a-number"})

        with self.assertRaisesRegex(ValueError, "must not exceed"):
            SchedulerConfig.from_environ({
                "REPORT_DISPATCH_POLL_SECONDS": "120",
                "REPORT_DISPATCH_LEASE_SECONDS": "60",
            })
        with self.assertRaisesRegex(ValueError, "must be less than"):
            SchedulerConfig.from_environ({
                "REPORT_QUEUED_REDISPATCH_SECONDS": "900",
                "REPORT_RUNNING_STALE_SECONDS": "600",
            })

    def test_shutdown_finishes_in_flight_cycle_before_stopping(self):
        from scripts.run_scheduler import SchedulerConfig, run_scheduler

        stop_event = threading.Event()
        calls = []

        def reconcile(*args, **kwargs):
            calls.append("reconcile-start")
            stop_event.set()
            calls.append("reconcile-commit")
            return _ReconcileResult()

        def drain(*args, **kwargs):
            calls.append("drain")
            return _DrainResult()

        run_scheduler(
            SchedulerConfig.from_environ({}),
            session_factory=object(),
            dispatch=Mock(),
            drain=drain,
            reconcile=reconcile,
            stop_event=stop_event,
        )

        self.assertEqual(calls, ["reconcile-start", "reconcile-commit", "drain"])

    def test_logs_only_safe_counts_and_health(self):
        from scripts.run_scheduler import SchedulerConfig, run_scheduler

        logger = Mock(spec=logging.Logger)
        secret = "redis://user:password@example.invalid/customer-payload"
        run_scheduler(
            SchedulerConfig.from_environ({}),
            session_factory=object(),
            dispatch=Mock(),
            drain=Mock(side_effect=RuntimeError(secret)),
            reconcile=Mock(return_value=_ReconcileResult()),
            stop_event=threading.Event(),
            logger=logger,
            max_cycles=1,
        )

        rendered = " ".join(str(call) for call in logger.method_calls)
        self.assertNotIn(secret, rendered)
        self.assertNotIn("customer-payload", rendered)
        self.assertIn("degraded", rendered)


class EvidencePurgeEntrypointTests(unittest.TestCase):
    def test_configuration_defaults_to_one_hour_and_rejects_unsafe_values(self):
        from scripts.run_purge import PurgeConfig

        self.assertEqual(
            PurgeConfig.from_environ({}).interval,
            timedelta(hours=1),
        )
        for value in (
            "0",
            "-1",
            "59.999",
            "3600.001",
            "not-a-number",
            "inf",
            "nan",
        ):
            with self.subTest(value=value), self.assertRaisesRegex(
                ValueError,
                "FAILURE_EVIDENCE_PURGE_SECONDS",
            ):
                PurgeConfig.from_environ({
                    "FAILURE_EVIDENCE_PURGE_SECONDS": value,
                })

    def test_invalid_configuration_fails_before_runtime_dependencies(self):
        from scripts import run_purge

        with patch.object(run_purge, "_runtime_dependencies") as dependencies:
            with self.assertRaises(ValueError):
                run_purge.main(
                    [],
                    environ={"FAILURE_EVIDENCE_PURGE_SECONDS": "secret-invalid"},
                )
        dependencies.assert_not_called()

    def test_first_purge_is_immediate_then_waits_on_monotonic_deadline(self):
        from scripts.run_purge import PurgeConfig, run_purge

        stop_event = _RecordingStopEvent()
        purge = Mock(return_value=2)
        readiness = Mock(return_value=_PurgeReadiness(
            ready=True,
            status="fresh",
            last_purged_count=2,
            expired_bytes_count=0,
        ))
        monotonic_values = iter((10.0, 10.0, 11.0, 71.0, 71.0))

        run_purge(
            PurgeConfig.from_environ({
                "FAILURE_EVIDENCE_PURGE_SECONDS": "60",
            }),
            session_factory=object(),
            purge=purge,
            readiness=readiness,
            stop_event=stop_event,
            monotonic=lambda: next(monotonic_values),
            max_cycles=2,
        )

        self.assertEqual(purge.call_count, 2)
        self.assertEqual(stop_event.waits, [59.0])

    def test_failure_retries_are_bounded_and_log_no_exception_payload(self):
        from scripts.run_purge import PurgeConfig, run_purge

        stop_event = _RecordingStopEvent()
        secret = "postgresql://user:password@example.invalid/restricted-html"
        purge = Mock(side_effect=RuntimeError(secret))
        readiness = Mock()
        logger = Mock(spec=logging.Logger)
        monotonic_values = iter((10.0, 10.0, 11.0, 71.0, 71.0))

        run_purge(
            PurgeConfig.from_environ({}),
            session_factory=object(),
            purge=purge,
            readiness=readiness,
            stop_event=stop_event,
            logger=logger,
            monotonic=lambda: next(monotonic_values),
            max_cycles=2,
        )

        self.assertEqual(purge.call_count, 2)
        self.assertEqual(stop_event.waits, [59.0])
        readiness.assert_not_called()
        rendered = " ".join(str(call) for call in logger.method_calls)
        self.assertIn("evidence_purge health=degraded", rendered)
        self.assertNotIn(secret, rendered)
        self.assertNotIn("restricted-html", rendered)

    def test_shutdown_finishes_active_purge_and_persisted_readiness_query(self):
        from scripts.run_purge import PurgeConfig, run_purge

        stop_event = threading.Event()
        calls = []

        def purge(*_args, **_kwargs):
            calls.append("purge-start")
            stop_event.set()
            calls.append("purge-commit")
            return 1

        def readiness(*_args, **_kwargs):
            calls.append("readiness")
            return _PurgeReadiness(
                ready=True,
                status="fresh",
                last_purged_count=1,
                expired_bytes_count=0,
            )

        run_purge(
            PurgeConfig.from_environ({}),
            session_factory=object(),
            purge=purge,
            readiness=readiness,
            stop_event=stop_event,
            monotonic=lambda: 10.0,
        )

        self.assertEqual(calls, ["purge-start", "purge-commit", "readiness"])

    def test_check_mode_uses_persisted_readiness_and_safe_output(self):
        from scripts import run_purge

        cases = (
            ("fresh", True, 0),
            ("never_succeeded", False, 1),
            ("stale", False, 1),
            ("expired_bytes_present", False, 1),
            ("database_unavailable", False, 1),
        )
        for status, ready, expected_exit in cases:
            with self.subTest(status=status):
                engine = Mock()
                readiness = Mock(return_value=_PurgeReadiness(
                    ready=ready,
                    status=status,
                    last_purged_count=3,
                    expired_bytes_count=0 if ready else 1,
                ))
                secret = "postgresql://user:password@example.invalid/private"
                logger = Mock(spec=logging.Logger)
                with patch.object(
                    run_purge,
                    "_runtime_dependencies",
                    return_value=(engine, object(), Mock(), readiness),
                ):
                    result = run_purge.main(
                        ["--check"],
                        environ={"DATABASE_URL": secret},
                        logger=logger,
                    )

                self.assertEqual(result, expected_exit)
                readiness.assert_called_once()
                engine.dispose.assert_called_once_with()
                rendered = " ".join(str(call) for call in logger.method_calls)
                self.assertIn(status, rendered)
                self.assertNotIn(secret, rendered)
                self.assertNotIn("password", rendered)


class BatchedPurgeRunnerTests(PostgreSQLJobTestCase):
    def test_standalone_check_imports_project_without_pythonpath(self):
        from roster_scout.database import create_session_factory, get_engine
        from roster_scout.evidence import purge_expired_evidence

        engine = get_engine(self.database_url)
        self.addCleanup(engine.dispose)
        result = purge_expired_evidence(create_session_factory(engine))
        self.assertTrue(result.complete)

        environ = os.environ.copy()
        environ["DATABASE_URL"] = self.database_url
        environ.pop("PYTHONPATH", None)
        completed = subprocess.run(
            [sys.executable, "scripts/run_purge.py", "--check"],
            cwd=PROJECT_ROOT,
            env=environ,
            capture_output=True,
            text=True,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("readiness=fresh", completed.stderr)

    def test_incomplete_scan_retries_within_sixty_seconds_without_success_log(self):
        from roster_scout.evidence import EvidencePurgeResult
        from scripts.run_purge import PurgeConfig, run_purge

        stop_event = _RecordingStopEvent()
        purge = Mock(
            side_effect=(
                EvidencePurgeResult(
                    purged_count=2,
                    complete=False,
                    outstanding_count=1,
                    batches=1,
                ),
                EvidencePurgeResult(
                    purged_count=1,
                    complete=True,
                    outstanding_count=0,
                    batches=1,
                ),
            )
        )
        readiness = Mock(
            return_value=_PurgeReadiness(
                ready=True,
                status="fresh",
                last_purged_count=1,
                expired_bytes_count=0,
            )
        )
        logger = Mock(spec=logging.Logger)
        monotonic_values = iter((10.0, 10.0, 10.0))

        run_purge(
            PurgeConfig.from_environ({}),
            session_factory=object(),
            purge=purge,
            readiness=readiness,
            stop_event=stop_event,
            logger=logger,
            monotonic=lambda: next(monotonic_values),
            max_cycles=2,
        )

        self.assertEqual(stop_event.waits, [60.0])
        readiness.assert_called_once()
        rendered = " ".join(str(call) for call in logger.method_calls)
        self.assertIn("health=incomplete", rendered)
        self.assertIn("purged=%d outstanding=%d", rendered)
        self.assertIn("health=%s purged=%d expired=%d", rendered)
        for unsafe in ("row", "url", "checksum", "html", "exception"):
            self.assertNotIn(unsafe, rendered.lower())

    def test_lock_release_retry_completes_and_advances_heartbeat_once(self):
        from sqlalchemy import select

        from roster_scout.database import create_session_factory, get_engine
        from roster_scout.evidence import (
            get_evidence_purge_readiness,
            purge_expired_evidence,
            store_failure_evidence,
        )
        from roster_scout.jobs import accept_report_job
        from roster_scout.models import EvidencePurgeState, FailureEvidence, ReportJob
        from scripts.run_purge import PurgeConfig, run_purge

        seed_at = datetime.now(UTC) - timedelta(minutes=2)
        purge_expired_evidence(self.Session, now=seed_at)
        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session,
            organization_id,
            report_id,
            "batched-runner-lock-release",
            dispatch=None,
        )
        with self.Session.begin() as session:
            session.get(ReportJob, job.id).status = "failed"
        collected_at = datetime.now(UTC) - timedelta(days=1, minutes=1)
        with patch.dict(
            "os.environ",
            {"FAILURE_EVIDENCE_RETENTION_DAYS": "1"},
            clear=False,
        ):
            evidence_ids = [
                store_failure_evidence(
                    self.Session,
                    job.id,
                    section="batting",
                    source_url="https://stats.ncaa.org/batched-runner",
                    html=f"<html>restricted-{index}</html>",
                    failure_category="source_blocked",
                    collected_at=collected_at,
                )
                for index in range(2)
            ]

        lock_engine = get_engine(self.database_url)
        purge_engine = get_engine(self.database_url)
        self.addCleanup(lock_engine.dispose)
        self.addCleanup(purge_engine.dispose)
        lock_sessions = create_session_factory(lock_engine)
        purge_sessions = create_session_factory(purge_engine)
        blocker = lock_sessions()
        blocker.begin()
        blocker.scalar(
            select(FailureEvidence)
            .where(FailureEvidence.id == evidence_ids[0])
            .with_for_update()
        )

        class ReleaseLockEvent:
            def __init__(self):
                self.waits = []

            def is_set(self):
                return False

            def wait(self, seconds):
                self.waits.append(seconds)
                blocker.rollback()
                blocker.close()
                return False

        stop_event = ReleaseLockEvent()
        logger = Mock(spec=logging.Logger)
        try:
            run_purge(
                PurgeConfig.from_environ({}),
                session_factory=purge_sessions,
                purge=purge_expired_evidence,
                readiness=get_evidence_purge_readiness,
                stop_event=stop_event,
                logger=logger,
                max_cycles=2,
            )
        finally:
            if blocker.is_active:
                blocker.rollback()
                blocker.close()

        self.assertEqual(len(stop_event.waits), 1)
        self.assertLessEqual(stop_event.waits[0], 60)
        with self.Session() as session:
            rows = list(
                session.scalars(
                    select(FailureEvidence).where(
                        FailureEvidence.id.in_(evidence_ids)
                    )
                )
            )
            self.assertTrue(all(row.compressed_html is None for row in rows))
            state = session.get(EvidencePurgeState, 1)
            self.assertGreater(state.last_success_at, seed_at)
            self.assertEqual(state.last_purged_count, 1)
        rendered = " ".join(str(call) for call in logger.method_calls)
        self.assertIn("health=incomplete", rendered)
        self.assertIn("health=%s purged=%d expired=%d", rendered)


class PurgeIsolationTests(PostgreSQLJobTestCase):
    def _expired_evidence(self):
        from roster_scout.evidence import store_failure_evidence
        from roster_scout.jobs import accept_report_job
        from roster_scout.models import ReportJob

        now = datetime.now(UTC)
        organization_id, report_id = self.fixture()
        job = accept_report_job(
            self.Session,
            organization_id,
            report_id,
            "blocked-dispatch-isolation",
            dispatch=None,
        )
        with self.Session.begin() as session:
            session.get(ReportJob, job.id).status = "failed"
        with patch.dict(
            "os.environ",
            {"FAILURE_EVIDENCE_RETENTION_DAYS": "1"},
            clear=False,
        ):
            evidence_id = store_failure_evidence(
                self.Session,
                job.id,
                section="batting",
                source_url="https://stats.ncaa.org/team/123/stats",
                html="<html>independent purge bytes</html>",
                failure_category="source_blocked",
                collected_at=now - timedelta(days=1, minutes=1),
            )
        return evidence_id

    def test_blocked_dispatch_reconciliation_cannot_delay_real_purge_commit(self):
        from roster_scout.database import create_session_factory, get_engine
        from roster_scout.evidence import (
            get_evidence_purge_readiness,
            purge_expired_evidence,
        )
        from roster_scout.models import EvidencePurgeState, FailureEvidence
        from scripts.run_purge import PurgeConfig, run_purge
        from scripts.run_scheduler import SchedulerConfig, run_scheduler

        evidence_id = self._expired_evidence()
        scheduler_engine = get_engine(self.database_url)
        purge_engine = get_engine(self.database_url)
        self.addCleanup(scheduler_engine.dispose)
        self.addCleanup(purge_engine.dispose)
        scheduler_sessions = create_session_factory(scheduler_engine)
        purge_sessions = create_session_factory(purge_engine)
        self.assertIsNot(scheduler_sessions, purge_sessions)
        self.assertIsNot(scheduler_engine, purge_engine)

        reconcile_started = threading.Event()
        release_reconcile = threading.Event()
        scheduler_stop = threading.Event()
        purge_stop = threading.Event()
        scheduler_errors = []
        scheduler_pid = []

        def blocked_reconcile(*_args, **_kwargs):
            with scheduler_sessions() as session:
                scheduler_pid.append(session.scalar(text("SELECT pg_backend_pid()")))
                reconcile_started.set()
                release_reconcile.wait(10)
            return _ReconcileResult()

        def scheduler_target():
            try:
                run_scheduler(
                    SchedulerConfig.from_environ({}),
                    session_factory=scheduler_sessions,
                    dispatch=Mock(),
                    drain=Mock(return_value=_DrainResult()),
                    reconcile=blocked_reconcile,
                    stop_event=scheduler_stop,
                    max_cycles=1,
                )
            except Exception as exc:  # pragma: no cover - asserted below
                scheduler_errors.append(exc)

        scheduler_thread = threading.Thread(target=scheduler_target, daemon=True)
        scheduler_thread.start()
        self.assertTrue(reconcile_started.wait(5))
        self.assertTrue(scheduler_thread.is_alive())

        with purge_sessions() as session:
            purge_pid = session.scalar(text("SELECT pg_backend_pid()"))
        self.assertNotEqual(scheduler_pid, [purge_pid])
        self.assertIsNot(scheduler_stop, purge_stop)

        run_purge(
            PurgeConfig.from_environ({}),
            session_factory=purge_sessions,
            purge=purge_expired_evidence,
            readiness=get_evidence_purge_readiness,
            stop_event=purge_stop,
            logger=Mock(spec=logging.Logger),
            max_cycles=1,
        )

        with self.Session() as session:
            evidence = session.get(FailureEvidence, evidence_id)
            state = session.get(EvidencePurgeState, 1)
            self.assertIsNone(evidence.compressed_html)
            self.assertIsNotNone(evidence.purged_at)
            self.assertIsNotNone(state.last_success_at)
            self.assertEqual(state.last_purged_count, 1)
        self.assertTrue(scheduler_thread.is_alive())

        release_reconcile.set()
        scheduler_thread.join(5)
        self.assertFalse(scheduler_thread.is_alive())
        self.assertEqual(scheduler_errors, [])


class EvidencePurgeDeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compose = render_compose()
        with (PROJECT_ROOT / "fly.toml").open("rb") as handle:
            cls.fly = tomllib.load(handle)

    def test_purge_has_one_independently_supervised_process_per_topology(self):
        scheduler = self.compose["services"]["scheduler"]
        purge = self.compose["services"]["purge"]
        self.assertEqual(purge["profiles"], ["scheduler"])
        self.assertEqual(purge["command"], ["python", "scripts/run_purge.py"])
        self.assertEqual(
            set(purge["environment"]),
            {"DATABASE_URL", "FAILURE_EVIDENCE_PURGE_SECONDS", "LOG_LEVEL"},
        )
        self.assertEqual(
            self.fly["env"]["FAILURE_EVIDENCE_PURGE_SECONDS"],
            "3600",
        )
        command = self.fly["processes"]["purge"]
        self.assertIn(
            'FAILURE_EVIDENCE_PURGE_SECONDS="$FAILURE_EVIDENCE_PURGE_SECONDS"',
            command,
        )
        self.assertIn("run_purge.py", command)
        self.assertNotIn("CELERY_BROKER_URL", command)
        self.assertNotIn("BROWSERLESS", command)
        self.assertNotIn("FAILURE_EVIDENCE_PURGE_SECONDS", scheduler["environment"])

        dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("COPY scripts/run_purge.py ./scripts/run_purge.py", dockerfile)

    def test_purge_readiness_check_is_the_container_health_contract(self):
        purge = self.compose["services"]["purge"]
        self.assertEqual(
            purge["healthcheck"]["test"],
            ["CMD", "python", "scripts/run_purge.py", "--check"],
        )

    def test_dispatch_scheduler_source_has_no_purge_ownership(self):
        import scripts.run_scheduler as scheduler

        source = inspect.getsource(scheduler)
        for forbidden in ("purge", "EvidencePurgeHealth", "FAILURE_EVIDENCE"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


class ProcessHealthCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compose = render_compose()
        with (PROJECT_ROOT / "fly.toml").open("rb") as handle:
            cls.fly = tomllib.load(handle)

    def test_shared_image_has_no_global_http_healthcheck(self):
        dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertNotIn("HEALTHCHECK", dockerfile)

    def test_compose_assigns_one_role_specific_check_per_process(self):
        services = self.compose["services"]
        app_check = " ".join(services["roster-scout"]["healthcheck"]["test"])
        worker_check = " ".join(services["worker"]["healthcheck"]["test"])
        scheduler_check = " ".join(services["scheduler"]["healthcheck"]["test"])
        purge_check = services["purge"]["healthcheck"]["test"]

        self.assertIn("http://127.0.0.1:5000/healthz", app_check)
        self.assertNotIn("/readyz", app_check)
        self.assertIn("celery", worker_check)
        self.assertIn("inspect ping", worker_check)
        self.assertNotIn("http", worker_check)
        self.assertIn("os.kill(1, 0)", scheduler_check)
        self.assertNotIn("http", scheduler_check)
        self.assertEqual(
            purge_check,
            ["CMD", "python", "scripts/run_purge.py", "--check"],
        )

    def test_fly_readiness_is_app_only_and_uses_persisted_route(self):
        service = self.fly["http_service"]
        self.assertEqual(service["processes"], ["app"])
        self.assertEqual(service["checks"][0]["path"], "/readyz")
        self.assertEqual(self.fly["mounts"][0]["processes"], ["app"])


class IndependentPurgeConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compose = render_compose()
        with (PROJECT_ROOT / "fly.toml").open("rb") as handle:
            cls.fly = tomllib.load(handle)

    def test_compose_has_exactly_one_postgresql_only_purge_service(self):
        services = self.compose["services"]
        purge_services = [
            service
            for service in services.values()
            if "run_purge.py" in " ".join(service.get("command") or [])
        ]
        self.assertEqual(len(purge_services), 1)
        purge = purge_services[0]
        self.assertEqual(
            set(purge["environment"]),
            {"DATABASE_URL", "FAILURE_EVIDENCE_PURGE_SECONDS", "LOG_LEVEL"},
        )
        self.assertEqual(purge["environment"]["FAILURE_EVIDENCE_PURGE_SECONDS"], "3600")
        self.assertEqual(purge["environment"]["LOG_LEVEL"], "INFO")
        self.assertNotIn("ports", purge)
        self.assertNotIn("volumes", purge)
        for forbidden in ("CELERY_BROKER_URL", "BROWSERLESS", "PORT", "ROSTER_SCOUT_CACHE_DIR"):
            with self.subTest(forbidden=forbidden):
                self.assertFalse(
                    any(forbidden in name for name in purge["environment"])
                )

    def test_fly_has_exactly_one_allowlisted_purge_process(self):
        purge_commands = [
            command
            for command in self.fly["processes"].values()
            if "run_purge.py" in command
        ]
        self.assertEqual(len(purge_commands), 1)
        command = purge_commands[0]
        self.assertIn("env -i", command)
        self.assertIn('DATABASE_URL="$DATABASE_URL"', command)
        self.assertIn(
            'FAILURE_EVIDENCE_PURGE_SECONDS="$FAILURE_EVIDENCE_PURGE_SECONDS"',
            command,
        )
        self.assertIn("LOG_LEVEL=INFO", command)
        for forbidden in (
            "CELERY_BROKER_URL",
            "BROWSERLESS",
            "FAILURE_EVIDENCE_RETENTION_DAYS",
            "ROSTER_SCOUT_CACHE_DIR",
            "PORT=",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, command)

    def test_dispatch_and_purge_are_each_defined_once(self):
        services = self.compose["services"].values()
        self.assertEqual(
            sum("run_scheduler.py" in " ".join(service.get("command") or []) for service in services),
            1,
        )
        self.assertEqual(
            sum("run_scheduler.py" in command for command in self.fly["processes"].values()),
            1,
        )


class DispatchSchedulerConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compose = render_compose()
        with (PROJECT_ROOT / "fly.toml").open("rb") as handle:
            cls.fly = tomllib.load(handle)

    def test_exactly_one_scheduler_uses_a_separate_image_command(self):
        compose_services = self.compose["services"]
        scheduler = compose_services["scheduler"]
        self.assertEqual(scheduler["profiles"], ["scheduler"])
        self.assertEqual(scheduler["command"], ["python", "scripts/run_scheduler.py"])
        self.assertEqual(
            sum(
                "run_scheduler.py" in " ".join(service.get("command") or [])
                for service in compose_services.values()
            ),
            1,
        )

        processes = self.fly["processes"]
        self.assertEqual(
            sum("run_scheduler.py" in command for command in processes.values()),
            1,
        )
        self.assertIn("scheduler", processes)
        self.assertEqual(len(set(processes.values())), len(processes))
        self.assertEqual(self.fly["deploy"]["release_command"], "alembic upgrade head")

        dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn(
            "COPY scripts/run_scheduler.py ./scripts/run_scheduler.py",
            dockerfile,
        )

    def test_scheduler_has_only_database_broker_and_timing_configuration(self):
        timing = {
            "REPORT_DISPATCH_POLL_SECONDS": "5",
            "REPORT_DISPATCH_LEASE_SECONDS": "60",
            "REPORT_QUEUED_REDISPATCH_SECONDS": "300",
            "REPORT_RUNNING_STALE_SECONDS": "1800",
        }
        scheduler = self.compose["services"]["scheduler"]
        self.assertEqual(
            set(scheduler["environment"]),
            {"DATABASE_URL", "CELERY_BROKER_URL", *timing},
        )
        self.assertEqual(
            {key: scheduler["environment"].get(key) for key in timing},
            timing,
        )
        self.assertNotIn("ports", scheduler)
        self.assertNotIn("volumes", scheduler)
        self.assertFalse(
            any(name.startswith("BROWSERLESS_") for name in scheduler["environment"])
        )

        self.assertEqual(
            {key: self.fly["env"].get(key) for key in timing},
            timing,
        )
        command = self.fly["processes"]["scheduler"]
        self.assertIn("env -i", command)
        self.assertIn('DATABASE_URL="$DATABASE_URL"', command)
        self.assertIn('CELERY_BROKER_URL="$CELERY_BROKER_URL"', command)
        self.assertNotIn("FAILURE_EVIDENCE", command)
        self.assertNotIn("BROWSERLESS", command)

    def test_scheduler_owns_no_http_service_or_app_volume(self):
        self.assertEqual(self.fly["http_service"]["processes"], ["app"])
        self.assertEqual(self.fly["mounts"][0]["processes"], ["app"])
        self.assertNotIn("scheduler", self.fly["http_service"]["processes"])
        self.assertNotIn("scheduler", self.fly["mounts"][0]["processes"])

    def test_example_defaults_and_production_gate_are_explicit(self):
        example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
        for name, value in {
            "REPORT_DISPATCH_POLL_SECONDS": "5",
            "REPORT_DISPATCH_LEASE_SECONDS": "60",
            "REPORT_QUEUED_REDISPATCH_SECONDS": "300",
            "REPORT_RUNNING_STALE_SECONDS": "1800",
        }.items():
            self.assertIn(f"{name}={value}", example)

        guide = (PROJECT_ROOT / "DEPLOYMENT.md").read_text(encoding="utf-8")
        for prerequisite in (
            "managed PostgreSQL",
            "managed Redis",
            "managed object/evidence storage",
            "uninterrupted Fly worker billing/capacity",
        ):
            self.assertIn(prerequisite, guide)
        self.assertIn("Local Compose is development-only", guide)


class FailureEvidenceRetentionConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compose = render_compose()
        with (PROJECT_ROOT / "fly.toml").open("rb") as handle:
            cls.fly = tomllib.load(handle)

    def test_default_is_exposed_only_to_evidence_creator_processes(self):
        self.assertIn("FAILURE_EVIDENCE_RETENTION_DAYS=28", (
            PROJECT_ROOT / ".env.example"
        ).read_text(encoding="utf-8"))
        services = self.compose["services"]
        for service_name in ("roster-scout", "worker"):
            with self.subTest(platform="compose", service=service_name):
                self.assertEqual(
                    services[service_name]["environment"].get(
                        "FAILURE_EVIDENCE_RETENTION_DAYS"
                    ),
                    "28",
                )
        for service_name in ("scheduler", "purge"):
            with self.subTest(platform="compose", service=service_name):
                self.assertNotIn(
                    "FAILURE_EVIDENCE_RETENTION_DAYS",
                    services[service_name]["environment"],
                )

        self.assertEqual(self.fly["env"]["FAILURE_EVIDENCE_RETENTION_DAYS"], "28")
        for process_name in ("scheduler", "purge"):
            with self.subTest(platform="fly", process=process_name):
                self.assertNotIn(
                    "FAILURE_EVIDENCE_RETENTION_DAYS",
                    self.fly["processes"][process_name],
                )

    def test_runbook_states_bounded_operational_contract(self):
        guide = (PROJECT_ROOT / "DEPLOYMENT.md").read_text(encoding="utf-8")
        normalized_guide = " ".join(guide.split())
        for statement in (
            "integer days from 1 through 28",
            "28 days (672 hours)",
            "default and non-extendable hard cap",
            "moves each new evidence row's `expires_at` earlier",
            "fail closed before evidence insertion",
            "at least hourly",
            "exactly six hours",
            "independently supervised purge process",
            "physically absent before 720 hours",
        ):
            with self.subTest(statement=statement):
                self.assertIn(statement, normalized_guide)


class Phase1ReleaseDiffHygieneTests(unittest.TestCase):
    def setUp(self):
        self._temporary_directory = tempfile.TemporaryDirectory()
        self.repo = Path(self._temporary_directory.name)
        self._git("init")
        self._git("config", "user.email", "release-gate@example.com")
        self._git("config", "user.name", "Release Gate")
        self.older_base = self._commit("history.txt", "older\n", "older base")
        self.audited_base = self._commit(
            "history.txt", "older\naudited\n", "audited baseline"
        )
        self.round_five_commits = (
            self._commit("round-five.txt", "one\n", "round five one"),
            self._commit("round-five.txt", "one\ntwo\n", "round five two"),
        )

    def tearDown(self):
        self._temporary_directory.cleanup()

    def _git(self, *arguments, check=True):
        return subprocess.run(
            ["git", *arguments],
            cwd=self.repo,
            check=check,
            capture_output=True,
            text=True,
        )

    def _commit(self, relative_path, contents, message):
        (self.repo / relative_path).write_text(contents, encoding="utf-8")
        self._git("add", relative_path)
        self._git("commit", "-m", message)
        return self._git("rev-parse", "HEAD").stdout.strip()

    def test_default_equal_and_older_bases_preserve_round_five_range(self):
        from scripts.validate_phase1_release import resolve_release_diff_base

        for override, expected in (
            (None, self.audited_base),
            (self.audited_base, self.audited_base),
            (self.older_base, self.older_base),
        ):
            env = {} if override is None else {"PHASE1_RELEASE_DIFF_BASE": override}
            with self.subTest(override=override):
                selected = resolve_release_diff_base(
                    env, project_root=self.repo, audited_ref=self.audited_base
                )
                self.assertEqual(selected, expected)
                included = set(
                    self._git("rev-list", f"{selected}..HEAD")
                    .stdout.strip()
                    .splitlines()
                )
                self.assertTrue(set(self.round_five_commits).issubset(included))

    def test_invalid_truncated_and_post_baseline_bases_fail_closed(self):
        from scripts.validate_phase1_release import resolve_release_diff_base

        rejected = (
            "missing-ref",
            "HEAD",
            "HEAD^",
            self.round_five_commits[0],
            f"{self.audited_base}^{{tree}}",
        )
        for override in rejected:
            with self.subTest(override=override), self.assertRaises(SystemExit):
                resolve_release_diff_base(
                    {"PHASE1_RELEASE_DIFF_BASE": override},
                    project_root=self.repo,
                    audited_ref=self.audited_base,
                )

        with self.assertRaises(SystemExit):
            resolve_release_diff_base(
                {}, project_root=self.repo, audited_ref="missing-audited-base"
            )

    def test_committed_whitespace_defect_fails_with_clean_worktree(self):
        from scripts.validate_phase1_release import committed_diff_command

        self._commit("defect.txt", "trailing whitespace  \n", "committed defect")
        self.assertEqual(self._git("status", "--short").stdout, "")
        command = committed_diff_command(self.audited_base)
        with self.assertRaises(subprocess.CalledProcessError):
            subprocess.run(command, cwd=self.repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "diff", "--check"],
            cwd=self.repo,
            check=True,
            capture_output=True,
        )

    def test_manifest_ends_with_committed_then_working_tree_hygiene(self):
        from scripts import validate_phase1_release as validator

        manifest = validator.release_command_manifest(
            "python", diff_base=self.audited_base
        )
        self.assertEqual(
            manifest[-2:],
            (
                (
                    validator.COMMITTED_DIFF_LABEL,
                    ["git", "diff", "--check", f"{self.audited_base}..HEAD", "--"],
                ),
                (validator.WORKING_TREE_DIFF_LABEL, ["git", "diff", "--check"]),
            ),
        )
        labels = [label for label, _command in manifest]
        self.assertLess(labels.index(validator.COMPILATION_LABEL), len(labels) - 2)
        self.assertLess(labels.index(validator.TEMPLATE_LABEL), len(labels) - 2)


class Phase1ReleaseValidatorContractTests(unittest.TestCase):
    def test_manifest_names_every_plan_14_through_21_regression_class(self):
        from scripts.validate_phase1_release import GAP_REGRESSION_TESTS

        expected = {
            "tests.test_report_jobs.SectionHeaderPublicationTests",
            "tests.test_snapshot_persistence.SnapshotMutationSerializationTests",
            "tests.test_snapshot_persistence.SnapshotMutationRaceTests",
            "tests.test_report_jobs.AttemptGenerationSchemaTests",
            "tests.test_report_jobs.AttemptGenerationDeliveryTests",
            "tests.test_report_jobs.CrashAfterGenerationCasRecoveryTests",
            "tests.test_snapshot_persistence.SourceIdentityConstraintTests",
            "tests.test_canonical_publish.SourceIdentityBindingTests",
            "tests.test_canonical_data.CanonicalIdentityResolutionTests",
            "tests.test_canonical_publish.UnresolvedIdentityPersistenceTests",
            "tests.test_evidence.EvidencePurgePersistenceTests",
            "tests.test_evidence.PersistedPurgeReadinessTests",
            "tests.test_deployment_config.EvidencePurgeEntrypointTests",
            "tests.test_deployment_config.PurgeIsolationTests",
            "tests.test_deployment_config.EvidencePurgeDeploymentTests",
            "tests.test_app_routes.AppReadinessTests",
            "tests.test_deployment_config.ProcessHealthCheckTests",
            "tests.test_deployment_config.IndependentPurgeConfigTests",
            "tests.test_phase1_release.Phase1ReleaseTests",
            (
                "tests.test_app_routes.AppRouteTests."
                "test_d2_selector_id_downloads_workbook_without_fuzzy_matching"
            ),
        }
        self.assertTrue(expected.issubset(GAP_REGRESSION_TESTS))

    def test_manifest_names_every_plan_22_through_29_regression_class(self):
        from scripts.validate_phase1_release import GAP_REGRESSION_TESTS

        expected = {
            "tests.test_ncaa_workflow.NcaaParserCorruptionTests",
            "tests.test_report_jobs.ParserContractRecoveryTests",
            "tests.test_canonical_data.CanonicalIdentityResolutionTests",
            "tests.test_canonical_publish.IdlessCorroborationPublicationTests",
            (
                "tests.test_snapshot_persistence."
                "AuthoritativeSourcePlayerConstraintTests"
            ),
            (
                "tests.test_snapshot_persistence."
                "AuthoritativeSourcePlayerPublicationTests"
            ),
            "tests.test_evidence.FailureEvidenceDatabaseRetentionTests",
            (
                "tests.test_snapshot_persistence."
                "FailureEvidenceRetentionMigrationTests"
            ),
            "tests.test_app_routes.AppReadinessTests",
            "tests.test_deployment_config.WorkerRetentionPreflightTests",
            "tests.test_report_jobs.InvalidRetentionTerminalStateTests",
            "tests.test_evidence.BatchedEvidencePurgeTests",
            "tests.test_deployment_config.BatchedPurgeRunnerTests",
            "tests.test_usage_metrics.UsageEventSchemaTests",
            "tests.test_usage_metrics.UsageServiceTests",
            "tests.test_usage_metrics.UsageReportCliTests",
            "tests.test_app_routes.DownloadEventContractTests",
            "tests.test_app_routes.WorkbookDownloadUsageTests",
        }
        self.assertTrue(expected.issubset(GAP_REGRESSION_TESTS))

    def test_manifest_names_every_round_four_counterexample_class(self):
        from scripts.validate_phase1_release import (
            ROUND_FOUR_COUNTEREXAMPLE_TESTS,
        )

        expected = {
            (
                "tests.test_snapshot_persistence."
                "AuthoritativeIdentitySerializationTests"
            ),
            (
                "tests.test_canonical_publish."
                "AuthoritativeIdentityPublicationSerializationTests"
            ),
            "tests.test_ncaa_workflow.NcaaResultCacheContractTests",
            (
                "tests.test_browserless_lifecycle."
                "NcaaSameTeamCollectionLockTests"
            ),
            "tests.test_usage_metrics.UsageServiceTests",
            "tests.test_app_routes.NativeWorkbookDatabaseOptionalTests",
            "tests.test_app_routes.WorkbookPreparationReceiptTests",
            (
                "tests.test_app_routes."
                "DownloadAcknowledgementSecretConfigurationTests"
            ),
            "tests.test_app_routes.WorkbookDownloadAcknowledgementTests",
            (
                "tests.test_evidence."
                "EvidencePurgeFinalizationSerializationTests"
            ),
        }
        self.assertEqual(set(ROUND_FOUR_COUNTEREXAMPLE_TESTS), expected)

    def test_manifest_names_every_round_five_counterexample_class(self):
        from scripts.validate_phase1_release import ROUND_FIVE_COUNTEREXAMPLE_TESTS

        expected = {
            (
                "tests.test_snapshot_persistence."
                "AuthoritativeGlobalPlayerSerializationTests"
            ),
            (
                "tests.test_snapshot_persistence."
                "AuthoritativeGlobalPlayerSerializationMigrationTests"
            ),
            (
                "tests.test_canonical_publish."
                "AuthoritativeIdentityPublicationSerializationTests"
            ),
            (
                "tests.test_deployment_config."
                "DownloadAcknowledgementPublicOriginConfigTests"
            ),
            "tests.test_app_routes.DownloadAcknowledgementPublicOriginTests",
            "tests.test_app_routes.DownloadEventContractTests",
        }
        self.assertEqual(set(ROUND_FIVE_COUNTEREXAMPLE_TESTS), expected)

    def test_round_five_groups_run_before_every_full_validation_group(self):
        import inspect

        from scripts import validate_phase1_release as validator

        manifest = validator.release_command_manifest("python")
        labels = [label for label, _command in manifest]
        sentinels = (
            validator.MINIMAL_HEAD_SETUP_LABEL,
            validator.MINIMAL_HEAD_ASSERTION_LABEL,
            validator.ROUND_FIVE_PYTHON_LABEL,
            validator.ROUND_FIVE_NODE_LABEL,
            validator.MIGRATION_SERIALIZATION_LABEL,
            validator.FULL_MIGRATION_START_LABEL,
            validator.FULL_DISCOVERY_LABEL,
            validator.CATALOG_LABEL,
            validator.DEPLOYMENT_LABEL,
            validator.WORKBOOK_LABEL,
            validator.COMPILATION_LABEL,
            validator.TEMPLATE_LABEL,
            validator.COMMITTED_DIFF_LABEL,
            validator.WORKING_TREE_DIFF_LABEL,
        )
        for sentinel in sentinels:
            with self.subTest(sentinel=sentinel):
                self.assertEqual(labels.count(sentinel), 1)
        indices = [labels.index(sentinel) for sentinel in sentinels]
        self.assertEqual(indices, sorted(indices))

        source = inspect.getsource(validator.release_command_manifest)
        source_sentinels = (
            "MINIMAL_HEAD_SETUP_LABEL",
            "MINIMAL_HEAD_ASSERTION_LABEL",
            "ROUND_FIVE_PYTHON_LABEL",
            "ROUND_FIVE_NODE_LABEL",
            "MIGRATION_SERIALIZATION_LABEL",
            "FULL_MIGRATION_START_LABEL",
            "FULL_DISCOVERY_LABEL",
            "CATALOG_LABEL",
            "DEPLOYMENT_LABEL",
            "WORKBOOK_LABEL",
            "COMPILATION_LABEL",
            "TEMPLATE_LABEL",
            "COMMITTED_DIFF_LABEL",
            "WORKING_TREE_DIFF_LABEL",
        )
        source_indices = [source.index(sentinel) for sentinel in source_sentinels]
        self.assertEqual(source_indices, sorted(source_indices))

        through_focused_node = "\n".join(
            " ".join(command)
            for _label, command in manifest[: labels.index(
                validator.ROUND_FIVE_NODE_LABEL
            ) + 1]
        )
        for counterexample in (
            *validator.ROUND_FOUR_COUNTEREXAMPLE_TESTS,
            *validator.ROUND_FIVE_COUNTEREXAMPLE_TESTS,
        ):
            with self.subTest(counterexample=counterexample):
                self.assertIn(counterexample, through_focused_node)
        for forbidden in (
            "alembic downgrade base",
            "unittest discover -s tests",
            "scripts/validate_ncaa_catalog.py",
            "docker compose config --quiet",
            "AppRouteTests.test_d2_selector_id_downloads_workbook",
            "-m py_compile",
            "git diff --check",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, through_focused_node)

    def test_manifest_contains_migrations_catalog_compose_full_suite_and_diff(self):
        from scripts.validate_phase1_release import (
            EXPECTED_MIGRATION_HEAD,
            ROUND_FOUR_COUNTEREXAMPLE_TESTS,
            ROUND_FIVE_COUNTEREXAMPLE_TESTS,
            release_command_manifest,
        )

        rendered = "\n".join(
            " ".join(command)
            for _label, command in release_command_manifest("python")
        )
        for command in (
            "python -m alembic upgrade head",
            "python -m alembic downgrade base",
            "python -m alembic current --check-heads",
            "python -m alembic check",
            "python -m unittest discover -s tests -v",
            (
                "python -m unittest tests.test_snapshot_persistence."
                "AuthoritativeGlobalPlayerSerializationMigrationTests -v"
            ),
            "node --test tests/js/download_tracking.test.mjs",
            "scripts/validate_ncaa_catalog.py",
            "docker compose config --quiet",
            "python -m unittest tests.test_deployment_config -v",
            (
                "python -m unittest tests.test_app_routes."
                "DownloadEventContractTests -v"
            ),
            "git diff --check",
            (
                "tests.test_app_routes.AppRouteTests."
                "test_d2_selector_id_downloads_workbook_without_fuzzy_matching"
            ),
        ):
            with self.subTest(command=command):
                self.assertIn(command, rendered)

        for counterexample in ROUND_FOUR_COUNTEREXAMPLE_TESTS:
            with self.subTest(counterexample=counterexample):
                self.assertIn(counterexample, rendered)

        for counterexample in ROUND_FIVE_COUNTEREXAMPLE_TESTS:
            with self.subTest(counterexample=counterexample):
                self.assertIn(counterexample, rendered)

        self.assertEqual(EXPECTED_MIGRATION_HEAD, "0014")
        head_revision = (
            PROJECT_ROOT
            / "migrations"
            / "versions"
            / "0014_global_authoritative_player_serialization.py"
        ).read_text(encoding="utf-8")
        self.assertIn('revision = "0014"', head_revision)
        self.assertIn('down_revision = "0013"', head_revision)

    def test_runbook_documents_final_release_contract(self):
        guide = (PROJECT_ROOT / "DEPLOYMENT.md").read_text(encoding="utf-8")
        for statement in (
            "audited baseline `95776ed`",
            "`PHASE1_RELEASE_DIFF_BASE`",
            "equal to or an ancestor of `95776ed`",
            "`git diff --check <resolved-base>..HEAD --`",
            "`git diff --check`",
            "revision `0014`",
            "`ROSTER_SCOUT_PUBLIC_ORIGIN`",
            "HTTP 400 and 403 acknowledgement responses are definitive",
            "HTTP 5xx and network failures are retryable",
        ):
            with self.subTest(statement=statement):
                self.assertIn(statement, guide)

    def test_missing_database_url_fails_closed_before_release_commands(self):
        from scripts.validate_phase1_release import _validate_database_url

        with self.assertRaisesRegex(SystemExit, "DATABASE_URL is required"):
            _validate_database_url({})


class UsageMetricsRunbookContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.guide = (PROJECT_ROOT / "DEPLOYMENT.md").read_text(encoding="utf-8")
        cls.normalized = " ".join(cls.guide.split())

    def test_runbook_contains_global_and_organization_window_commands(self):
        global_command = (
            "python scripts/report_usage.py --from 2026-11-01T00:00:00Z "
            "--to 2026-12-01T00:00:00Z"
        )
        organization_command = (
            f"{global_command} --organization-id "
            "00000000-0000-0000-0000-000000000000"
        )
        self.assertIn(global_command, self.normalized)
        self.assertIn(organization_command, self.normalized)
        self.assertIn("`--from` is inclusive and `--to` is exclusive", self.normalized)
        self.assertIn("half-open UTC window", self.normalized)

    def test_metric_definitions_and_phase_two_boundary_are_explicit(self):
        for statement in (
            "committed, deduplicated `workbook_download_served` event",
            "distinct, nonnull canonical `actor_account_id`",
            "Phase 1 does not authenticate users",
            "`unique_authenticated_users` remains zero",
            "exactly one `authenticated_use`",
            "accepts and durably creates an authorized report/job request",
            "accepted `ReportJob` UUID",
            "canonical account UUID plus the authorized organization UUID",
            "Page views, login, rejected requests, and validation failures",
        ):
            with self.subTest(statement=statement):
                self.assertIn(statement, self.normalized)

    def test_runbook_forbids_identity_substitutes_and_provisioning_claims(self):
        for forbidden_substitute in (
            "IP address",
            "cookie",
            "email address",
            "user-agent",
            "fingerprint",
            "pseudonymous identifier",
        ):
            with self.subTest(forbidden_substitute=forbidden_substitute):
                self.assertIn(forbidden_substitute, self.normalized)
        self.assertIn("Output is aggregate-only", self.normalized)
        self.assertIn(
            "does not provision or validate managed PostgreSQL",
            self.normalized,
        )


class RoundFourOperationsRunbookContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.guide = (PROJECT_ROOT / "DEPLOYMENT.md").read_text(encoding="utf-8")
        cls.normalized = " ".join(cls.guide.split())

    def test_native_analytics_and_production_postgresql_boundaries_are_explicit(self):
        for statement in (
            "`scripts/run_local.sh` and the legacy native `POST /scrape` workbook remain usable when `DATABASE_URL` is unset",
            "returns the two-sheet workbook without opening PostgreSQL",
            "`usage_event_dropped route=download_ack reason=database_unavailable`",
            "Production durable report jobs, migrations, `/readyz`, usage reports, and authoritative analytics require PostgreSQL",
        ):
            with self.subTest(statement=statement):
                self.assertIn(statement, self.normalized)

    def test_shared_secret_predicate_and_rotation_match_runtime(self):
        for statement in (
            "Fly production is detected by a non-empty `FLY_APP_NAME`",
            "multi-worker mode is exactly `WEB_CONCURRENCY>1`",
            "valid URL-safe-base64 material that decodes to at least 32 random bytes",
            "missing, malformed, or short value fails startup before serving",
            "non-Fly native/local mode with `WEB_CONCURRENCY==1`",
            "Every worker in one deployment must share the exact same configured value",
            "ROSTER_SCOUT_DOWNLOAD_ACK_SECRET=your-url-safe-base64-32-byte-secret",
            "wait the exact 15-minute proof TTL",
            "Mixed-secret rolling workers are unsupported",
            "Neither the shared secret nor an acknowledgement proof may be logged",
        ):
            with self.subTest(statement=statement):
                self.assertIn(statement, self.normalized)

    def test_public_origin_validation_and_cutover_match_runtime(self):
        for statement in (
            "A non-empty `FLY_APP_NAME` or `WEB_CONCURRENCY>1` requires `ROSTER_SCOUT_PUBLIC_ORIGIN`",
            "Fly requires the configured origin to use HTTPS",
            "only non-Fly `WEB_CONCURRENCY==1` may fall back to the direct request origin",
            "contains no credentials, path, query, or fragment",
            "`Forwarded`, `X-Forwarded-Proto`, `X-Forwarded-Host`, and `Host` are not trusted as origin authority",
            "ROSTER_SCOUT_PUBLIC_ORIGIN=https://roster-scout.fly.dev",
            "internationalized domains are canonicalized with UTS46 and STD3 rules to the exact ASCII A-label form browsers send",
            "restart every app worker with the same new value before directing browser traffic to the custom domain",
        ):
            with self.subTest(statement=statement):
                self.assertIn(statement, self.normalized)

    def test_idna_runtime_dependency_is_direct_and_pinned(self):
        requirements = (PROJECT_ROOT / "requirements.txt").read_text(
            encoding="utf-8"
        ).splitlines()
        self.assertIn("idna==3.20", requirements)

    def test_download_success_is_bounded_stateless_and_does_not_overclaim(self):
        for statement in (
            "browser read the response blob, initiated the anchor download, and idempotently acknowledged the same opaque UUID",
            "exact 15-minute signed proof",
            "it is not proof of a filesystem save",
            "there is no receipt registry, table, cache, collection, list, or set",
            "acknowledgement must be same-origin",
            "JSON body is limited to 1 KiB",
            "token is limited to 512 characters",
            "Ambiguous scrape, blob, or acknowledgement failures reuse the same UUID",
            "PostgreSQL's unique usage-event key makes proof replay idempotent",
        ):
            with self.subTest(statement=statement):
                self.assertIn(statement, self.normalized)

    def test_metric_privacy_cache_identity_and_purge_contracts_are_exact(self):
        for statement in (
            "`actor_account_id` from `authenticated_use` events only",
            "actor-bearing download events and anonymous downloads never increase that metric",
            "Do not attach or derive an IP address, cookie, email address, user-agent, device fingerprint, pseudonymous identifier",
            "legacy successful-result cache uses contract version 3",
            "metric-definition (when applicable) -> snapshot -> tenant-scoped Player -> PlayerTeamSeason",
            "Eligible failure-evidence writers and final complete-scan certification share one stable transaction advisory lock",
            "PostgreSQL `GREATEST` keeps the purge heartbeat monotonic",
        ):
            with self.subTest(statement=statement):
                self.assertIn(statement, self.normalized)

    def test_exact_release_commands_and_fail_fast_order_are_documented(self):
        for command in (
            "DATABASE_URL=postgresql+psycopg://roster_scout:roster_scout@localhost:5432/roster_scout_test .venv/bin/python scripts/validate_phase1_release.py",
            "node --test tests/js/download_tracking.test.mjs",
            ".venv/bin/python scripts/live_ncaa_smoke.py",
        ):
            with self.subTest(command=command):
                self.assertIn(command, self.normalized)
        focused_python = self.normalized.index(
            "every round-four and round-five Python counterexample"
        )
        focused_node = self.normalized.index(
            "`node --test tests/js/download_tracking.test.mjs`"
        )
        lifecycle = self.normalized.index(
            "revision `0014` global Player serialization class"
        )
        self.assertLess(focused_python, focused_node)
        self.assertLess(focused_node, lifecycle)


@dataclass(frozen=True)
class _DrainResult:
    claimed: int = 0
    published: int = 0
    released: int = 0


@dataclass(frozen=True)
class _ReconcileResult:
    reopened: int = 0
    closed: int = 0
    active: int = 0


@dataclass(frozen=True)
class _PurgeReadiness:
    ready: bool
    status: str
    last_purged_count: int
    expired_bytes_count: int


class _NoWaitStopEvent:
    def is_set(self):
        return False

    def wait(self, _seconds):
        return False


class _RecordingStopEvent(_NoWaitStopEvent):
    def __init__(self):
        self.waits = []

    def wait(self, seconds):
        self.waits.append(seconds)
        return False


if __name__ == "__main__":
    unittest.main()
