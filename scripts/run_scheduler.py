#!/usr/bin/env python3
"""Run bounded PostgreSQL-to-broker dispatch reconciliation."""

import logging
import math
import os
import signal
import sys
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


LOGGER = logging.getLogger("roster_scout.scheduler")
DISPATCH_BATCH_LIMIT = 10
EVIDENCE_PURGE_MAX_AGE = timedelta(hours=24)


def _bounded_seconds(environ, name, default, *, minimum, maximum):
    raw_value = environ.get(name, str(default))
    try:
        value = float(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{name} must be a number from {minimum} to {maximum}"
        ) from exc
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be from {minimum} to {maximum} seconds")
    return timedelta(seconds=value)


@dataclass(frozen=True)
class SchedulerConfig:
    poll_interval: timedelta
    dispatch_lease_duration: timedelta
    queued_redispatch_deadline: timedelta
    running_stale_threshold: timedelta

    @classmethod
    def from_environ(cls, environ=None):
        environ = os.environ if environ is None else environ
        config = cls(
            poll_interval=_bounded_seconds(
                environ,
                "REPORT_DISPATCH_POLL_SECONDS",
                5,
                minimum=1,
                maximum=300,
            ),
            dispatch_lease_duration=_bounded_seconds(
                environ,
                "REPORT_DISPATCH_LEASE_SECONDS",
                60,
                minimum=5,
                maximum=600,
            ),
            queued_redispatch_deadline=_bounded_seconds(
                environ,
                "REPORT_QUEUED_REDISPATCH_SECONDS",
                300,
                minimum=30,
                maximum=86400,
            ),
            running_stale_threshold=_bounded_seconds(
                environ,
                "REPORT_RUNNING_STALE_SECONDS",
                1800,
                minimum=60,
                maximum=604800,
            ),
        )
        if config.poll_interval > config.dispatch_lease_duration:
            raise ValueError(
                "REPORT_DISPATCH_POLL_SECONDS must not exceed "
                "REPORT_DISPATCH_LEASE_SECONDS"
            )
        if config.queued_redispatch_deadline >= config.running_stale_threshold:
            raise ValueError(
                "REPORT_QUEUED_REDISPATCH_SECONDS must be less than "
                "REPORT_RUNNING_STALE_SECONDS"
            )
        return config


@dataclass(frozen=True)
class PurgeReadiness:
    ready: bool
    status: str
    alert: str | None


@dataclass
class EvidencePurgeHealth:
    """Process-local safe health for the singleton evidence purge owner."""

    last_success_at: datetime | None = None
    last_purged_count: int | None = None

    def record_success(self, *, completed_at, purged_count):
        self.last_success_at = completed_at
        self.last_purged_count = int(purged_count)

    def readiness(self, *, now=None):
        now = now or datetime.now(UTC)
        if self.last_success_at is None:
            return PurgeReadiness(
                ready=False,
                status="never_succeeded",
                alert="evidence_purge health=never_succeeded readiness=blocked",
            )
        if now - self.last_success_at > EVIDENCE_PURGE_MAX_AGE:
            return PurgeReadiness(
                ready=False,
                status="stale",
                alert="evidence_purge health=stale readiness=blocked",
            )
        return PurgeReadiness(ready=True, status="fresh", alert=None)


def run_scheduler(
    config,
    *,
    session_factory,
    dispatch,
    drain,
    reconcile,
    stop_event,
    purge=None,
    purge_health=None,
    clock=None,
    logger=LOGGER,
    max_cycles=None,
):
    """Poll service boundaries; a shutdown request never interrupts a cycle."""
    clock = clock or (lambda: datetime.now(UTC))
    purge_health = purge_health or EvidencePurgeHealth()
    cycles = 0
    while not stop_event.is_set() or cycles == 0:
        cycle_now = clock()
        if purge is not None:
            try:
                purged_count = purge(session_factory, now=cycle_now)
            except Exception:
                readiness = purge_health.readiness(now=cycle_now)
                logger.error("%s", readiness.alert)
            else:
                # purge_expired_evidence returns only after its transaction commits.
                purge_health.record_success(
                    completed_at=cycle_now,
                    purged_count=purged_count,
                )
                logger.info(
                    "evidence_purge health=fresh purged=%d",
                    purged_count,
                )
        try:
            reconciliation = reconcile(
                session_factory,
                queued_timeout=config.queued_redispatch_deadline,
                running_timeout=config.running_stale_threshold,
            )
            dispatches = drain(
                session_factory,
                dispatch=dispatch,
                limit=DISPATCH_BATCH_LIMIT,
                lease_duration=config.dispatch_lease_duration,
                redispatch_delay=config.queued_redispatch_deadline,
                failure_backoff=config.poll_interval,
            )
        except Exception:
            # Exception text can contain URLs, credentials, or source payloads.
            logger.warning("scheduler_cycle health=degraded")
        else:
            logger.info(
                "scheduler_cycle health=healthy claimed=%d published=%d "
                "released=%d reopened=%d closed=%d active=%d",
                dispatches.claimed,
                dispatches.published,
                dispatches.released,
                reconciliation.reopened,
                reconciliation.closed,
                reconciliation.active,
            )
        cycles += 1
        if stop_event.is_set() or (
            max_cycles is not None and cycles >= max_cycles
        ):
            break
        stop_event.wait(config.poll_interval.total_seconds())


def _runtime_dependencies():
    """Construct network-capable dependencies only after config validation."""
    from roster_scout.database import create_session_factory, get_engine
    from roster_scout.evidence import purge_expired_evidence
    from roster_scout.jobs import (
        drain_report_dispatch_outbox,
        reconcile_stale_report_jobs,
    )
    from roster_scout.tasks import dispatch_report_job

    engine = get_engine()
    return (
        engine,
        create_session_factory(engine),
        dispatch_report_job,
        drain_report_dispatch_outbox,
        reconcile_stale_report_jobs,
        purge_expired_evidence,
    )


def main(environ=None):
    environ = os.environ if environ is None else environ
    config = SchedulerConfig.from_environ(environ)
    logging.basicConfig(
        level=environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    stop_event = threading.Event()

    def request_shutdown(_signum, _frame):
        stop_event.set()

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)
    engine, session_factory, dispatch, drain, reconcile, purge = _runtime_dependencies()
    try:
        run_scheduler(
            config,
            session_factory=session_factory,
            dispatch=dispatch,
            drain=drain,
            reconcile=reconcile,
            purge=purge,
            purge_health=EvidencePurgeHealth(),
            stop_event=stop_event,
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        main()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from None
    except Exception:
        print("scheduler startup failed", file=sys.stderr)
        raise SystemExit(1) from None
