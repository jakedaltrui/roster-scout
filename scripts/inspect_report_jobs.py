#!/usr/bin/env python3
"""Print a read-only, metadata-only diagnostic projection for report jobs."""

from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.report_usage import SafeArgumentParser  # noqa: E402


INVALID_ARGUMENTS = "report job diagnostics invalid arguments"
UNAVAILABLE = "report job diagnostics unavailable"
RUNNING_TIMEOUT = timedelta(minutes=30)


def build_parser():
    parser = SafeArgumentParser(description=__doc__)
    parser.add_argument("--job-id", help="optional exact report-job UUID")
    parser.add_argument(
        "--older-than-seconds",
        help="optional job-age lower bound from 1 through 604800 seconds",
    )
    return parser


def _parse_arguments(argv):
    try:
        args = build_parser().parse_args(argv)
        job_id = uuid.UUID(args.job_id) if args.job_id is not None else None
        older_than_seconds = None
        if args.older_than_seconds is not None:
            older_than_seconds = int(args.older_than_seconds)
            if not 1 <= older_than_seconds <= 604800:
                raise ValueError(INVALID_ARGUMENTS)
        if job_id is None and older_than_seconds is None:
            raise ValueError(INVALID_ARGUMENTS)
        return job_id, older_than_seconds
    except (TypeError, ValueError):
        raise ValueError(INVALID_ARGUMENTS) from None


def _runtime_dependencies(database_url=None):
    from roster_scout.database import create_session_factory, get_engine

    engine = get_engine(database_url)
    return engine, create_session_factory(engine)


def _iso(value):
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _age_seconds(now, value):
    if value is None:
        return None
    return max(0, int((now - value).total_seconds()))


def _state_category(job, dispatch, ledgers, now, navigation_timeout):
    if any(
        item.outcome == "planned"
        and now - item.started_at > timedelta(seconds=navigation_timeout)
        for item in ledgers
    ):
        return "request_stalled"
    if job.status == "running" and job.started_at is not None:
        if now - job.started_at > RUNNING_TIMEOUT:
            return "running_stuck"
    if job.status == "retrying" and dispatch is not None:
        if dispatch.next_attempt_at <= now:
            return "retry_overdue"
    if job.status == "queued":
        heartbeat = dispatch.updated_at if dispatch is not None else job.enqueued_at
        if heartbeat is None or now - heartbeat > RUNNING_TIMEOUT:
            return "queued_stuck"
    return job.status


def _job_projection(session, job, *, now, navigation_timeout):
    from sqlalchemy import select
    from roster_scout.models import (
        ExternalRefreshDiagnostic,
        ReportJobDispatch,
        ReportSection,
        RequestLedger,
        StatisticScope,
    )

    dispatch = session.scalar(
        select(ReportJobDispatch)
        .where(ReportJobDispatch.job_id == job.id)
        .order_by(ReportJobDispatch.generation.desc())
        .limit(1)
    )
    ledgers = list(session.scalars(
        select(RequestLedger)
        .where(RequestLedger.job_id == job.id)
        .order_by(RequestLedger.family, RequestLedger.source_identity)
    ))
    sections = list(session.scalars(
        select(ReportSection)
        .where(ReportSection.job_id == job.id)
        .order_by(ReportSection.section)
    ))
    scopes = []
    refreshes = []
    if job.result_snapshot_id is not None:
        scopes = list(session.scalars(
            select(StatisticScope)
            .where(StatisticScope.snapshot_id == job.result_snapshot_id)
            .order_by(StatisticScope.scope_code, StatisticScope.stat_type)
        ))
        refreshes = list(session.scalars(
            select(ExternalRefreshDiagnostic)
            .where(
                ExternalRefreshDiagnostic.snapshot_id == job.result_snapshot_id,
                ExternalRefreshDiagnostic.organization_id == job.organization_id,
            )
            .order_by(
                ExternalRefreshDiagnostic.provider,
                ExternalRefreshDiagnostic.attempt_number,
            )
        ))

    live_count = sum(item.request_kind == "live" for item in ledgers)
    cache_count = sum(item.request_kind == "cache" for item in ledgers)
    remaining = min(
        (item.budget_remaining for item in ledgers),
        default=None,
    )
    capacity_category = next(
        (
            item.safe_category
            for item in reversed(ledgers)
            if item.outcome == "capacity_exhausted"
        ),
        None,
    )
    heartbeat = dispatch.updated_at if dispatch is not None else job.started_at
    return {
        "attempt_count": job.attempt_count,
        "capacity_category": capacity_category,
        "dispatch_generation": job.dispatch_generation,
        "external_refreshes": [
            {
                "attempt_number": item.attempt_number,
                "completed_at": _iso(item.completed_at),
                "correlation_id": item.correlation_id,
                "provider": item.provider,
                "requested_at": _iso(item.requested_at),
                "safe_category": item.safe_category,
                "safe_reason_code": item.safe_reason_code,
                "started_at": _iso(item.started_at),
                "status": item.status,
                "updated_at": _iso(item.updated_at),
            }
            for item in refreshes
        ],
        "heartbeat_age_seconds": _age_seconds(now, heartbeat),
        "job_age_seconds": _age_seconds(now, job.created_at),
        "job_id": str(job.id),
        "next_retry_at": _iso(
            dispatch.next_attempt_at if dispatch is not None else None
        ),
        "request_budget": {
            "cache_count": cache_count,
            "live_count": live_count,
            "remaining": remaining,
            "used": live_count,
        },
        "result_snapshot_id": (
            str(job.result_snapshot_id) if job.result_snapshot_id is not None else None
        ),
        "scopes": [
            {
                "safe_reason": item.availability_reason,
                "scope_code": item.scope_code,
                "stat_type": item.stat_type,
                "status": item.status,
            }
            for item in scopes
        ],
        "sections": [
            {
                "row_count": item.row_count,
                "safe_reason": item.failure_category,
                "section": item.section,
                "status": item.status,
            }
            for item in sections
        ],
        "started_generation": job.started_generation,
        "state_category": _state_category(
            job, dispatch, ledgers, now, navigation_timeout
        ),
        "status": job.status,
    }


def main(argv=None, *, environ=None, stdout=None, stderr=None):
    environ = os.environ if environ is None else environ
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    try:
        job_id, older_than_seconds = _parse_arguments(
            sys.argv[1:] if argv is None else argv
        )
        from roster_scout.jobs import load_phase3_collection_limits

        limits = load_phase3_collection_limits(environ)
    except (TypeError, ValueError):
        print(INVALID_ARGUMENTS, file=stderr)
        return 2

    engine = None
    try:
        from sqlalchemy import select
        from roster_scout.models import ReportJob

        engine, session_factory = _runtime_dependencies(environ.get("DATABASE_URL"))
        now = datetime.now(UTC)
        with session_factory() as session:
            statement = select(ReportJob)
            if job_id is not None:
                statement = statement.where(ReportJob.id == job_id)
            if older_than_seconds is not None:
                statement = statement.where(
                    ReportJob.created_at
                    <= now - timedelta(seconds=older_than_seconds)
                )
            jobs = list(session.scalars(statement.order_by(ReportJob.id)))
            payload = {
                "generated_at": _iso(now),
                "jobs": [
                    _job_projection(
                        session,
                        job,
                        now=now,
                        navigation_timeout=limits.navigation_timeout_seconds,
                    )
                    for job in jobs
                ],
            }
        print(json.dumps(payload, sort_keys=True), file=stdout)
        return 0
    except Exception:
        print(UNAVAILABLE, file=stderr)
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
