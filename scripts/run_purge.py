#!/usr/bin/env python3
"""Run failure-evidence deletion independently of dispatch reconciliation."""

import argparse
import logging
import math
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

LOGGER = logging.getLogger("roster_scout.evidence_purge")
PURGE_INTERVAL_ENV = "FAILURE_EVIDENCE_PURGE_SECONDS"
PURGE_INTERVAL_DEFAULT_SECONDS = 3600
PURGE_INTERVAL_MIN_SECONDS = 60
PURGE_INTERVAL_MAX_SECONDS = 3600
PURGE_FAILURE_RETRY_SECONDS = 60


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
class PurgeConfig:
    interval: timedelta

    @classmethod
    def from_environ(cls, environ=None):
        environ = os.environ if environ is None else environ
        return cls(
            interval=_bounded_seconds(
                environ,
                PURGE_INTERVAL_ENV,
                PURGE_INTERVAL_DEFAULT_SECONDS,
                minimum=PURGE_INTERVAL_MIN_SECONDS,
                maximum=PURGE_INTERVAL_MAX_SECONDS,
            )
        )


def run_purge(
    config,
    *,
    session_factory,
    purge,
    readiness,
    stop_event,
    logger=LOGGER,
    monotonic=time.monotonic,
    max_cycles=None,
):
    """Purge immediately, then retry or repeat on bounded monotonic waits."""
    cycles = 0
    next_run_at = monotonic()
    while not stop_event.is_set() or cycles == 0:
        try:
            result = purge(session_factory)
        except Exception:
            # Exception text can contain database URLs or retained evidence.
            logger.error("evidence_purge health=degraded")
            delay = min(
                config.interval.total_seconds(),
                PURGE_FAILURE_RETRY_SECONDS,
            )
        else:
            # Integer results are accepted for compatibility with older injected
            # purge callables; production returns EvidencePurgeResult.
            if not getattr(result, "complete", True):
                logger.info(
                    "evidence_purge health=incomplete purged=%d outstanding=%d",
                    int(getattr(result, "purged_count", 0)),
                    int(getattr(result, "outstanding_count", 0)),
                )
                delay = min(
                    config.interval.total_seconds(),
                    PURGE_FAILURE_RETRY_SECONDS,
                )
            else:
                persisted = readiness(session_factory)
                logger.info(
                    "evidence_purge health=%s purged=%d expired=%d",
                    persisted.status,
                    persisted.last_purged_count,
                    persisted.expired_bytes_count,
                )
                delay = config.interval.total_seconds()

        cycles += 1
        if stop_event.is_set() or (
            max_cycles is not None and cycles >= max_cycles
        ):
            break

        next_run_at = monotonic() + delay
        stop_event.wait(max(0.0, next_run_at - monotonic()))


def _runtime_dependencies():
    """Construct PostgreSQL dependencies only after timing validation."""
    from roster_scout.database import create_session_factory, get_engine
    from roster_scout.evidence import (
        get_evidence_purge_readiness,
        purge_expired_evidence,
    )

    engine = get_engine()
    return (
        engine,
        create_session_factory(engine),
        purge_expired_evidence,
        get_evidence_purge_readiness,
    )


def main(argv=None, *, environ=None, logger=LOGGER):
    parser = argparse.ArgumentParser(
        description="Run or check the independent failure-evidence purge",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="check persisted purge readiness and exit",
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    environ = os.environ if environ is None else environ
    config = PurgeConfig.from_environ(environ)
    logging.basicConfig(
        level=environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    engine, session_factory, purge, readiness = _runtime_dependencies()
    try:
        if args.check:
            persisted = readiness(session_factory)
            logger.info(
                "evidence_purge readiness=%s purged=%d expired=%d",
                persisted.status,
                persisted.last_purged_count,
                persisted.expired_bytes_count,
            )
            return 0 if persisted.ready else 1

        stop_event = threading.Event()

        def request_shutdown(_signum, _frame):
            stop_event.set()

        signal.signal(signal.SIGTERM, request_shutdown)
        signal.signal(signal.SIGINT, request_shutdown)
        run_purge(
            config,
            session_factory=session_factory,
            purge=purge,
            readiness=readiness,
            stop_event=stop_event,
            logger=logger,
        )
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from None
    except Exception:
        print("evidence purge startup failed", file=sys.stderr)
        raise SystemExit(1) from None
