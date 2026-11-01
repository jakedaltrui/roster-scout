"""Organization-scoped canonical snapshot queries."""

from sqlalchemy import select

from .enums import PUBLISHED_SNAPSHOT_STATUSES
from .models import ReportJob, ReportSnapshot


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
