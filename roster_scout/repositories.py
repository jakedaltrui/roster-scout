"""Organization-scoped canonical snapshot, report, and outbox queries."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import distinct_on

from .enums import PUBLISHED_SNAPSHOT_STATUSES
from .models import (
    Report,
    ReportJob,
    ReportJobDispatch,
    ReportSection,
    ReportSnapshot,
    Season,
    Team,
    TeamSeason,
)


ACTIVE_DISPATCH_STATUSES = ("pending", "leased", "acknowledged")
LIBRARY_BUILD_STATUSES = ("queued", "running", "retrying", "failed", "blocked")
REPORT_LIBRARY_PAGE_SIZE = 25


@dataclass(frozen=True)
class ReportLibraryRecord:
    report_id: uuid.UUID
    report_name: str
    team_name: str
    source_team_id: str
    division: str
    season_year: int
    created_at: datetime
    latest_snapshot: ReportSnapshot | None
    latest_build: ReportJob | None
    snapshot_count: int
    unavailable_section_count: int


@dataclass(frozen=True)
class ReportDetailRecord:
    """One tenant-scoped report projection for detail and status views."""

    report_id: uuid.UUID
    report_name: str
    team_name: str
    division: str
    season_year: int
    snapshots: tuple[ReportSnapshot, ...]
    latest_job: ReportJob | None
    snapshot_sections: tuple[ReportSection, ...]
    job_sections: tuple[ReportSection, ...]


def get_report_for_organization(session, organization_id, report_id):
    """Return one report only when it belongs to the explicit organization."""
    return session.scalar(
        select(Report).where(
            Report.id == report_id,
            Report.organization_id == organization_id,
        )
    )


def count_reports_for_organization(session, organization_id):
    return int(
        session.scalar(
            select(func.count(Report.id)).where(
                Report.organization_id == organization_id
            )
        )
        or 0
    )


def count_reports_updated_since(session, organization_id, since):
    return int(
        session.scalar(
            select(func.count(func.distinct(ReportSnapshot.report_id))).where(
                ReportSnapshot.organization_id == organization_id,
                ReportSnapshot.status.in_(PUBLISHED_SNAPSHOT_STATUSES),
                ReportSnapshot.published_at >= since,
            )
        )
        or 0
    )


def get_report_detail_record(
    session,
    organization_id,
    report_id,
    *,
    history_limit=REPORT_LIBRARY_PAGE_SIZE,
):
    """Load bounded detail data without allowing an unscoped child read."""
    if history_limit < 1 or history_limit > REPORT_LIBRARY_PAGE_SIZE:
        raise ValueError("report history bounds are invalid")

    report_row = session.execute(
        select(
            Report.id,
            Report.name,
            Team.name,
            TeamSeason.division,
            Season.year,
        )
        .join(TeamSeason, TeamSeason.id == Report.team_season_id)
        .join(Team, Team.id == TeamSeason.team_id)
        .join(Season, Season.id == TeamSeason.season_id)
        .where(
            Report.id == report_id,
            Report.organization_id == organization_id,
        )
    ).one_or_none()
    if report_row is None:
        return None

    snapshots = tuple(session.scalars(
        select(ReportSnapshot)
        .where(
            ReportSnapshot.organization_id == organization_id,
            ReportSnapshot.report_id == report_id,
            ReportSnapshot.status.in_(PUBLISHED_SNAPSHOT_STATUSES),
        )
        .order_by(
            ReportSnapshot.published_at.desc(),
            ReportSnapshot.id.desc(),
        )
        .limit(history_limit)
    ))
    latest_job = session.scalar(
        select(ReportJob)
        .where(
            ReportJob.organization_id == organization_id,
            ReportJob.report_id == report_id,
        )
        .order_by(ReportJob.created_at.desc(), ReportJob.id.desc())
        .limit(1)
    )
    snapshot_sections = ()
    if snapshots:
        snapshot_sections = tuple(session.scalars(
            select(ReportSection)
            .where(
                ReportSection.organization_id == organization_id,
                ReportSection.snapshot_id == snapshots[0].id,
            )
            .order_by(ReportSection.section, ReportSection.id)
        ))
    job_sections = ()
    if latest_job is not None:
        job_sections = tuple(session.scalars(
            select(ReportSection)
            .where(
                ReportSection.organization_id == organization_id,
                ReportSection.job_id == latest_job.id,
            )
            .order_by(ReportSection.section, ReportSection.id)
        ))
    return ReportDetailRecord(
        report_id=report_row[0],
        report_name=report_row[1],
        team_name=report_row[2],
        division=report_row[3],
        season_year=report_row[4],
        snapshots=snapshots,
        latest_job=latest_job,
        snapshot_sections=snapshot_sections,
        job_sections=job_sections,
    )


def list_report_library_records(
    session,
    organization_id,
    *,
    offset=0,
    limit=REPORT_LIBRARY_PAGE_SIZE,
):
    """Return one bounded page with every child query scoped to the tenant."""
    if offset < 0 or limit < 1 or limit > REPORT_LIBRARY_PAGE_SIZE:
        raise ValueError("report library bounds are invalid")

    latest_snapshot_activity = (
        select(func.max(ReportSnapshot.published_at))
        .where(
            ReportSnapshot.organization_id == organization_id,
            ReportSnapshot.report_id == Report.id,
            ReportSnapshot.status.in_(PUBLISHED_SNAPSHOT_STATUSES),
        )
        .correlate(Report)
        .scalar_subquery()
    )
    latest_build_activity = (
        select(func.max(ReportJob.created_at))
        .where(
            ReportJob.organization_id == organization_id,
            ReportJob.report_id == Report.id,
            ReportJob.status.in_(LIBRARY_BUILD_STATUSES),
        )
        .correlate(Report)
        .scalar_subquery()
    )
    activity = func.greatest(
        Report.created_at,
        func.coalesce(latest_snapshot_activity, Report.created_at),
        func.coalesce(latest_build_activity, Report.created_at),
    )
    base_rows = list(
        session.execute(
            select(
                Report.id,
                Report.name,
                Team.name,
                Team.source_team_id,
                TeamSeason.division,
                Season.year,
                Report.created_at,
            )
            .join(TeamSeason, TeamSeason.id == Report.team_season_id)
            .join(Team, Team.id == TeamSeason.team_id)
            .join(Season, Season.id == TeamSeason.season_id)
            .where(Report.organization_id == organization_id)
            .order_by(activity.desc(), Report.id.desc())
            .offset(offset)
            .limit(limit)
        )
    )
    report_ids = [row[0] for row in base_rows]
    if not report_ids:
        return []

    latest_snapshots = {
        snapshot.report_id: snapshot
        for snapshot in session.scalars(
            select(ReportSnapshot)
            .where(
                ReportSnapshot.organization_id == organization_id,
                ReportSnapshot.report_id.in_(report_ids),
                ReportSnapshot.status.in_(PUBLISHED_SNAPSHOT_STATUSES),
            )
            .ext(distinct_on(ReportSnapshot.report_id))
            .order_by(
                ReportSnapshot.report_id,
                ReportSnapshot.published_at.desc(),
                ReportSnapshot.id.desc(),
            )
        )
    }
    latest_builds = {
        job.report_id: job
        for job in session.scalars(
            select(ReportJob)
            .where(
                ReportJob.organization_id == organization_id,
                ReportJob.report_id.in_(report_ids),
                ReportJob.status.in_(LIBRARY_BUILD_STATUSES),
            )
            .ext(distinct_on(ReportJob.report_id))
            .order_by(
                ReportJob.report_id,
                ReportJob.created_at.desc(),
                ReportJob.id.desc(),
            )
        )
    }
    snapshot_counts = {
        report_id: int(count)
        for report_id, count in session.execute(
            select(ReportSnapshot.report_id, func.count(ReportSnapshot.id))
            .where(
                ReportSnapshot.organization_id == organization_id,
                ReportSnapshot.report_id.in_(report_ids),
                ReportSnapshot.status.in_(PUBLISHED_SNAPSHOT_STATUSES),
            )
            .group_by(ReportSnapshot.report_id)
        )
    }
    latest_snapshot_ids = [snapshot.id for snapshot in latest_snapshots.values()]
    unavailable_counts = {}
    if latest_snapshot_ids:
        unavailable_counts = {
            snapshot_id: int(count)
            for snapshot_id, count in session.execute(
                select(ReportSection.snapshot_id, func.count(ReportSection.id))
                .where(
                    ReportSection.organization_id == organization_id,
                    ReportSection.snapshot_id.in_(latest_snapshot_ids),
                    ReportSection.status.in_(("unavailable", "failed")),
                )
                .group_by(ReportSection.snapshot_id)
            )
        }

    records = []
    for row in base_rows:
        snapshot = latest_snapshots.get(row[0])
        records.append(ReportLibraryRecord(
            report_id=row[0],
            report_name=row[1],
            team_name=row[2],
            source_team_id=row[3],
            division=row[4],
            season_year=row[5],
            created_at=row[6],
            latest_snapshot=snapshot,
            latest_build=latest_builds.get(row[0]),
            snapshot_count=snapshot_counts.get(row[0], 0),
            unavailable_section_count=(
                unavailable_counts.get(snapshot.id, 0) if snapshot else 0
            ),
        ))
    return records


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
