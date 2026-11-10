#!/usr/bin/env python3
"""Run bounded PostgreSQL-to-broker dispatch reconciliation."""

import logging
import math
import os
import signal
import sys
import threading
from dataclasses import dataclass
from datetime import timedelta


LOGGER = logging.getLogger("roster_scout.scheduler")
DISPATCH_BATCH_LIMIT = 10


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


def run_scheduler(
    config,
    *,
    session_factory,
    dispatch,
    drain,
    reconcile,
    stop_event,
    logger=LOGGER,
    max_cycles=None,
):
    """Poll service boundaries; a shutdown request never interrupts a cycle."""
    cycles = 0
    while not stop_event.is_set() or cycles == 0:
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
    engine, session_factory, dispatch, drain, reconcile = _runtime_dependencies()
    try:
        run_scheduler(
            config,
            session_factory=session_factory,
            dispatch=dispatch,
            drain=drain,
            reconcile=reconcile,
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
