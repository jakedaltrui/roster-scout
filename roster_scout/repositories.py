"""Organization-scoped canonical snapshot and dispatch-outbox queries."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select

from .enums import PUBLISHED_SNAPSHOT_STATUSES
from .models import ReportJob, ReportJobDispatch, ReportSnapshot


ACTIVE_DISPATCH_STATUSES = ("pending", "leased", "acknowledged")


@dataclass(frozen=True)
class ReportDispatchLease:
    dispatch_id: uuid.UUID
    organization_id: uuid.UUID
    job_id: uuid.UUID
    generation: int
    message_id: str
    lease_token: uuid.UUID
    lease_expires_at: datetime


def list_published_snapshots(session, organization_id):
    statement = (
        select(ReportSnapshot)
        .where(
            ReportSnapshot.organization_id == organization_id,
            ReportSnapshot.status.in_(PUBLISHED_SNAPSHOT_STATUSES),
        )
        .order_by(ReportSnapshot.published_at, ReportSnapshot.id)
    )
    return list(session.scalars(statement))


def get_published_snapshot(session, organization_id, snapshot_id):
    return session.scalar(
        select(ReportSnapshot).where(
            ReportSnapshot.id == snapshot_id,
            ReportSnapshot.organization_id == organization_id,
            ReportSnapshot.status.in_(PUBLISHED_SNAPSHOT_STATUSES),
        )
    )


def get_report_job(session, organization_id, job_id, *, for_update=False):
    statement = select(ReportJob).where(
        ReportJob.id == job_id,
        ReportJob.organization_id == organization_id,
    )
    if for_update:
        statement = statement.with_for_update()
    return session.scalar(statement)


def ensure_report_dispatch(
    session, job, *, generation=None, next_attempt_at=None, now=None
):
    """Ensure one recoverable active dispatch intent while the job row is locked."""
    if job.status in {"ready", "partial", "failed", "blocked"}:
        return None
    generation = generation or job.dispatch_generation
    existing = session.scalar(
        select(ReportJobDispatch)
        .where(
            ReportJobDispatch.job_id == job.id,
            ReportJobDispatch.generation == generation,
            ReportJobDispatch.status.in_(ACTIVE_DISPATCH_STATUSES),
        )
        .with_for_update()
    )
    if existing is not None:
        return existing
    intent = ReportJobDispatch(
        organization_id=job.organization_id,
        job_id=job.id,
        generation=generation,
        message_id=str(uuid.uuid4()),
        next_attempt_at=next_attempt_at or now or datetime.now(UTC),
    )
    session.add(intent)
    session.flush()
    return intent


def claim_report_dispatches(
    session,
    *,
    now=None,
    limit=10,
    lease_duration=timedelta(seconds=60),
    job_id=None,
):
    """Claim due outbox rows without blocking concurrent publisher sessions."""
    if limit < 1:
        return []
    now = now or datetime.now(UTC)
    due = or_(
        (
            (ReportJobDispatch.status == "pending")
            & (ReportJobDispatch.next_attempt_at <= now)
        ),
        (
            (ReportJobDispatch.status == "leased")
            & (ReportJobDispatch.lease_expires_at <= now)
        ),
        (
            (ReportJobDispatch.status == "acknowledged")
            & (ReportJobDispatch.redispatch_after <= now)
        ),
    )
    statement = (
        select(ReportJobDispatch)
        .join(ReportJob, ReportJob.id == ReportJobDispatch.job_id)
        .where(
            due,
            ReportJob.status.not_in(("ready", "partial", "failed", "blocked")),
            ReportJobDispatch.generation == ReportJob.dispatch_generation,
        )
        .order_by(ReportJobDispatch.next_attempt_at, ReportJobDispatch.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True, of=ReportJobDispatch)
    )
    if job_id is not None:
        statement = statement.where(ReportJobDispatch.job_id == job_id)
    claimed = []
    for intent in session.scalars(statement):
        token = uuid.uuid4()
        expires_at = now + lease_duration
        intent.status = "leased"
        intent.lease_token = token
        intent.leased_at = now
        intent.lease_expires_at = expires_at
        intent.redispatch_after = None
        intent.publish_attempts += 1
        intent.updated_at = now
        claimed.append(
            ReportDispatchLease(
                dispatch_id=intent.id,
                organization_id=intent.organization_id,
                job_id=intent.job_id,
                generation=intent.generation,
                message_id=intent.message_id,
                lease_token=token,
                lease_expires_at=expires_at,
            )
        )
    return claimed


def acknowledge_report_dispatch(
    session,
    lease,
    *,
    broker_message_id,
    now=None,
    redispatch_after,
):
    """Acknowledge only the publisher that still owns the matching lease token."""
    now = now or datetime.now(UTC)
    intent = session.scalar(
        select(ReportJobDispatch)
        .where(
            ReportJobDispatch.id == lease.dispatch_id,
            ReportJobDispatch.status == "leased",
            ReportJobDispatch.lease_token == lease.lease_token,
            ReportJobDispatch.generation == lease.generation,
        )
        .with_for_update()
    )
    if intent is None:
        return False
    intent.status = "acknowledged"
    intent.lease_token = None
    intent.lease_expires_at = None
    intent.acknowledged_at = now
    intent.redispatch_after = redispatch_after
    intent.next_attempt_at = redispatch_after
    intent.updated_at = now
    job = session.get(ReportJob, intent.job_id)
    if job is not None:
        job.enqueued_at = now
        # Persist the outbox-owned opaque identifier, never broker topology or
        # implementation-specific acknowledgement text.
        job.broker_message_id = intent.message_id
    return True


def release_report_dispatch(session, lease, *, next_attempt_at, now=None):
    """Release a matching publisher lease without storing broker error details."""
    now = now or datetime.now(UTC)
    intent = session.scalar(
        select(ReportJobDispatch)
        .where(
            ReportJobDispatch.id == lease.dispatch_id,
            ReportJobDispatch.status == "leased",
            ReportJobDispatch.lease_token == lease.lease_token,
            ReportJobDispatch.generation == lease.generation,
        )
        .with_for_update()
    )
    if intent is None:
        return False
    intent.status = "pending"
    intent.lease_token = None
    intent.lease_expires_at = None
    intent.redispatch_after = None
    intent.next_attempt_at = next_attempt_at
    intent.updated_at = now
    return True


def reopen_report_dispatch(session, intent, *, generation=None, now=None):
    """Make an acknowledged, deadline-expired intent immediately claimable."""
    now = now or datetime.now(UTC)
    if intent.status != "acknowledged" or (
        generation is not None and intent.generation != generation
    ):
        return False
    intent.status = "pending"
    intent.lease_token = None
    intent.lease_expires_at = None
    intent.redispatch_after = None
    intent.next_attempt_at = now
    intent.updated_at = now
    return True


def close_report_dispatches(session, job_id, *, generation=None, now=None):
    """Close every active intent once worker progress or terminal state is durable."""
    now = now or datetime.now(UTC)
    statement = select(ReportJobDispatch).where(
        ReportJobDispatch.job_id == job_id,
        ReportJobDispatch.status.in_(ACTIVE_DISPATCH_STATUSES),
    )
    if generation is not None:
        statement = statement.where(ReportJobDispatch.generation == generation)
    intents = list(session.scalars(statement.with_for_update()))
    for intent in intents:
        intent.status = "closed"
        intent.lease_token = None
        intent.lease_expires_at = None
        intent.redispatch_after = None
        intent.updated_at = now
    return len(intents)
