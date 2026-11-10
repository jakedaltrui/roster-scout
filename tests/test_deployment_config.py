"""Deployment topology and local-service exposure regressions."""

import copy
import inspect
import json
import logging
import subprocess
import threading
import tomllib
import unittest
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
            {"DATABASE_URL", "FAILURE_EVIDENCE_PURGE_SECONDS"},
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
