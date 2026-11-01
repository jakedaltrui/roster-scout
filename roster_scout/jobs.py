"""PostgreSQL-authoritative report-job acceptance and execution."""

import hashlib
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

from usage_limits import BrowserCapacityExceeded

from .models import ReportJob, ReportSection, ReportSnapshot, SourceRecord
from .snapshots import SnapshotPublicationError, publish_snapshot


TERMINAL_JOB_STATUSES = frozenset({"ready", "partial", "failed", "blocked"})
SCHEMA_VERSION = os.environ.get("REPORT_SCHEMA_VERSION", "schema-1")
PARSER_VERSION = os.environ.get("NCAA_PARSER_VERSION", "parser-1")
MAX_ATTEMPTS = max(1, min(10, int(os.environ.get("REPORT_JOB_MAX_ATTEMPTS", "3"))))
SAFE_FAILURE_MESSAGES = {
    "browser_capacity": "Browser capacity is temporarily unavailable.",
    "network": "The NCAA source could not be reached.",
    "source_blocked": "The NCAA source blocked collection.",
    "schema_validation": "Collected data did not match the required schema.",
    "identity_conflict": "Collected player identity evidence conflicted.",
    "core_section_failed": "A required report section could not be validated.",
    "unexpected": "The report build failed unexpectedly.",
    "retry_exhausted": "The report build exhausted its retry limit.",
}


class RetryableJobError(RuntimeError):
    """Safe signal that Celery may retry this job."""

    def __init__(self, category, message=None):
        self.category = category
        super().__init__(message or SAFE_FAILURE_MESSAGES[category])


class DeterministicJobError(ValueError):
    """Safe signal that retrying the same evidence cannot succeed."""

    def __init__(self, category, message=None):
        self.category = category
        super().__init__(message or SAFE_FAILURE_MESSAGES[category])


@dataclass(frozen=True)
class SectionCacheKey:
    job_id: uuid.UUID
    section: str
    source_url: str | None
    source_team_id: str | None
    source_season_id: str | None
    source_season_team_id: str | None
    source_stat_category_id: str | None
    schema_version: str
    parser_version: str


@dataclass(frozen=True)
class CollectedSection:
    name: str
    critical: bool
    rows: tuple[dict, ...] = field(default_factory=tuple)
    verified_empty: bool = False
    source_url: str | None = None
    source_team_id: str | None = None
    source_season_id: str | None = None
    source_season_team_id: str | None = None
    source_stat_category_id: str | None = None
    collected_at: datetime | None = None
    parser_version: str = PARSER_VERSION
    schema_version: str = SCHEMA_VERSION
    status: str = "complete"
    failure_category: str | None = None

    @classmethod
    def failed(cls, name, *, critical, category):
        return cls(
            name=name,
            critical=critical,
            status="failed",
            failure_category=category,
        )


def _utcnow():
    return datetime.now(UTC)


def create_or_get_job(session, organization_id, report_id, idempotency_key):
    """Accept one durable job per organization/idempotency key."""
    if not idempotency_key or len(idempotency_key) > 200:
        raise ValueError("idempotency_key must contain 1-200 characters")
    job_id = uuid.uuid4()
    statement = (
        postgresql_insert(ReportJob)
        .values(
            id=job_id,
            organization_id=organization_id,
            report_id=report_id,
            idempotency_key=idempotency_key,
            status="queued",
            max_attempts=MAX_ATTEMPTS,
        )
        .on_conflict_do_nothing(
            constraint="uq_report_jobs_organization_idempotency"
        )
        .returning(ReportJob.id)
    )
    inserted_id = session.scalar(statement)
    if inserted_id is not None:
        return session.get(ReportJob, inserted_id)
    job = session.scalar(
        select(ReportJob)
        .where(
            ReportJob.organization_id == organization_id,
            ReportJob.idempotency_key == idempotency_key,
        )
        .with_for_update()
    )
    if job is None:
        # A concurrent transaction may not have become visible under an unusual
        # transaction isolation override; let the caller retry the acceptance.
        raise RuntimeError("idempotent job conflict was not visible")
    if job.report_id != report_id:
        raise ValueError("idempotency_key is already bound to another report")
    return job


def accept_report_job(
    session_factory,
    organization_id,
    report_id,
    idempotency_key,
    *,
    dispatch,
):
    """Commit acceptance before best-effort broker publication.

    A failed broker handoff deliberately leaves ``enqueued_at`` null so a
    repeated accepted request can dispatch the same durable row.
    """
    with session_factory.begin() as session:
        job = create_or_get_job(
            session, organization_id, report_id, idempotency_key
        )
        job_id = job.id
    if dispatch is None:
        with session_factory() as session:
            return session.get(ReportJob, job_id)

    with session_factory() as session:
        stored = session.get(ReportJob, job_id)
        should_dispatch = (
            stored.status not in TERMINAL_JOB_STATUSES and stored.enqueued_at is None
        )
    if should_dispatch:
        try:
            message = dispatch(str(job_id))
        except Exception:
            # Broker details may include credentials or topology and are never
            # persisted as customer-visible failure state.
            pass
        else:
            with session_factory.begin() as session:
                stored = session.scalar(
                    select(ReportJob)
                    .where(ReportJob.id == job_id)
                    .with_for_update()
                )
                if stored.enqueued_at is None:
                    stored.enqueued_at = _utcnow()
                    stored.broker_message_id = str(message.id)
    with session_factory() as session:
        return session.get(ReportJob, job_id)


def _new_or_existing_snapshot(session, job):
    if job.result_snapshot_id is not None:
        return session.get(ReportSnapshot, job.result_snapshot_id)
    previous = session.scalar(
        select(ReportSnapshot)
        .where(
            ReportSnapshot.report_id == job.report_id,
            ReportSnapshot.status.in_(("ready", "partial")),
        )
        .order_by(ReportSnapshot.published_at.desc())
        .limit(1)
    )
    snapshot = ReportSnapshot(
        organization_id=job.organization_id,
        report_id=job.report_id,
        predecessor_snapshot_id=previous.id if previous else None,
        schema_version=SCHEMA_VERSION,
    )
    session.add(snapshot)
    session.flush()
    job.result_snapshot_id = snapshot.id
    return snapshot


def _load_cached_sections(session, job, snapshot):
    sections = list(
        session.scalars(
            select(ReportSection).where(
                ReportSection.snapshot_id == snapshot.id,
                ReportSection.job_id == job.id,
                ReportSection.status == "complete",
                ReportSection.schema_version == SCHEMA_VERSION,
                ReportSection.parser_version == PARSER_VERSION,
            )
        )
    )
    return {
        SectionCacheKey(
            job_id=job.id,
            section=section.section,
            source_url=section.source_url,
            source_team_id=section.source_team_id,
            source_season_id=section.source_season_id,
            source_season_team_id=section.source_season_team_id,
            source_stat_category_id=section.source_stat_category_id,
            schema_version=section.schema_version,
            parser_version=section.parser_version,
        ): section
        for section in sections
    }


def _persist_sections(session, job, snapshot, sections):
    existing = {
        section.section: section
        for section in session.scalars(
            select(ReportSection).where(ReportSection.snapshot_id == snapshot.id)
        )
    }
    for collected in sections:
        section = existing.get(collected.name)
        values = {
            "organization_id": job.organization_id,
            "snapshot_id": snapshot.id,
            "job_id": job.id,
            "section": collected.name,
            "critical": collected.critical,
            "status": collected.status,
            "row_count": len(collected.rows),
            "verified_empty": collected.verified_empty,
            "collected_at": collected.collected_at,
            "failure_category": collected.failure_category,
            "source_url": collected.source_url,
            "source_team_id": collected.source_team_id,
            "source_season_id": collected.source_season_id,
            "source_season_team_id": collected.source_season_team_id,
            "source_stat_category_id": collected.source_stat_category_id,
            "schema_version": collected.schema_version,
            "parser_version": collected.parser_version,
        }
        if section is None:
            section = ReportSection(**values)
            session.add(section)
            existing[collected.name] = section
        elif section.status != "complete":
            for key, value in values.items():
                setattr(section, key, value)
        elif (
            section.schema_version != collected.schema_version
            or section.parser_version != collected.parser_version
        ):
            raise DeterministicJobError("schema_validation")

        if collected.status != "complete" or not collected.rows:
            continue
        existing_ordinals = set(
            session.scalars(
                select(SourceRecord.row_ordinal).where(
                    SourceRecord.snapshot_id == snapshot.id,
                    SourceRecord.section == collected.name,
                )
            )
        )
        for ordinal, row in enumerate(collected.rows):
            if ordinal in existing_ordinals:
                continue
            encoded = json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
            session.add(
                SourceRecord(
                    organization_id=job.organization_id,
                    snapshot_id=snapshot.id,
                    section=collected.name,
                    row_ordinal=ordinal,
                    source_url=collected.source_url or "unknown",
                    source_team_id=collected.source_team_id or "unknown",
                    source_season_id=collected.source_season_id or "unknown",
                    source_season_team_id=collected.source_season_team_id or "unknown",
                    source_stat_category_id=collected.source_stat_category_id or "unknown",
                    source_player_id=row.get("_player_id"),
                    collected_at=collected.collected_at or _utcnow(),
                    parser_version=collected.parser_version,
                    schema_version=collected.schema_version,
                    payload_sha256=hashlib.sha256(encoded).hexdigest(),
                    raw_values=row,
                )
            )


def _classified_error(exc):
    if isinstance(exc, RetryableJobError | DeterministicJobError):
        return exc
    if isinstance(exc, BrowserCapacityExceeded):
        return RetryableJobError("browser_capacity")
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return RetryableJobError("network")
    if isinstance(exc, SnapshotPublicationError):
        return DeterministicJobError("core_section_failed")
    if isinstance(exc, ValueError):
        return DeterministicJobError("schema_validation")
    return DeterministicJobError("unexpected")


def _record_failure(session_factory, job_id, error):
    now = _utcnow()
    with session_factory.begin() as session:
        job = session.scalar(
            select(ReportJob).where(ReportJob.id == job_id).with_for_update()
        )
        if job is None or job.status in TERMINAL_JOB_STATUSES:
            return error
        retryable = isinstance(error, RetryableJobError)
        if retryable and job.attempt_count < job.max_attempts:
            job.status = "retrying"
        else:
            if retryable:
                error = DeterministicJobError("retry_exhausted")
            job.status = "failed"
            job.finished_at = now
        job.failure_category = error.category
        job.failure_message = str(error)
    return error


def run_report_job(session_factory, job_id, *, collector):
    """Run or safely replay one accepted job using caller-injected collection."""
    job_id = uuid.UUID(str(job_id))
    exhausted = False
    with session_factory.begin() as session:
        job = session.scalar(
            select(ReportJob).where(ReportJob.id == job_id).with_for_update()
        )
        if job is None:
            raise DeterministicJobError("schema_validation", "Report job does not exist.")
        if job.status in {"ready", "partial"}:
            return session.get(ReportSnapshot, job.result_snapshot_id)
        if job.status in {"failed", "blocked"}:
            raise DeterministicJobError(job.failure_category or "unexpected")
        if job.attempt_count >= job.max_attempts:
            job.status = "failed"
            job.failure_category = "retry_exhausted"
            job.failure_message = SAFE_FAILURE_MESSAGES["retry_exhausted"]
            job.finished_at = _utcnow()
            exhausted = True
            cached = {}
        else:
            snapshot = _new_or_existing_snapshot(session, job)
            cached = _load_cached_sections(session, job, snapshot)
            job.status = "running"
            job.attempt_count += 1
            job.started_at = job.started_at or _utcnow()
            job.failure_category = None
            job.failure_message = None
    if exhausted:
        raise DeterministicJobError("retry_exhausted")

    try:
        sections = collector(str(job_id), cached_sections=frozenset(cached))
        with session_factory.begin() as session:
            job = session.scalar(
                select(ReportJob).where(ReportJob.id == job_id).with_for_update()
            )
            if job.status in {"ready", "partial"}:
                return session.get(ReportSnapshot, job.result_snapshot_id)
            snapshot = session.get(ReportSnapshot, job.result_snapshot_id)
            _persist_sections(session, job, snapshot, sections)
            collected_times = [
                item.collected_at for item in sections if item.collected_at is not None
            ]
            if collected_times:
                snapshot.source_collected_at = min(collected_times)
            published = publish_snapshot(session, snapshot.id)
            job.status = published.status
            job.finished_at = _utcnow()
            return published
    except Exception as exc:
        safe_error = _record_failure(session_factory, job_id, _classified_error(exc))
        raise safe_error from None
