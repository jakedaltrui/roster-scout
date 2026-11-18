"""Independent Power BI semantic-refresh lifecycle for immutable snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select

from .models import ExternalRefreshDiagnostic, ReportSnapshot
from .power_bi import PowerBIClient, PowerBIFailure


@dataclass(frozen=True)
class DashboardRefreshState:
    status: str
    correlation_id: str | None = None
    attempt_number: int | None = None


def _utcnow():
    return datetime.now(UTC)


def _latest(session, organization_id, report_id, snapshot_id):
    return session.scalar(
        select(ExternalRefreshDiagnostic)
        .where(
            ExternalRefreshDiagnostic.organization_id == organization_id,
            ExternalRefreshDiagnostic.report_id == report_id,
            ExternalRefreshDiagnostic.snapshot_id == snapshot_id,
            ExternalRefreshDiagnostic.provider == "power_bi",
        )
        .order_by(ExternalRefreshDiagnostic.attempt_number.desc())
        .limit(1)
    )


def ensure_dashboard_refresh(
    session_factory,
    provider: PowerBIClient,
    *,
    organization_id,
    report_id,
    snapshot_id,
    now=None,
) -> DashboardRefreshState:
    """Start one refresh for a published snapshot, idempotently."""
    timestamp = (now or _utcnow)()
    with session_factory.begin() as session:
        snapshot = session.scalar(select(ReportSnapshot).where(
            ReportSnapshot.id == snapshot_id,
            ReportSnapshot.report_id == report_id,
            ReportSnapshot.organization_id == organization_id,
            ReportSnapshot.status.in_(("ready", "partial")),
            ReportSnapshot.published_at.is_not(None),
        ))
        if snapshot is None:
            raise ValueError("published snapshot not found")
        existing = _latest(session, organization_id, report_id, snapshot_id)
        if existing is not None:
            return DashboardRefreshState(
                existing.status, existing.correlation_id, existing.attempt_number
            )

    started = provider.start_refresh()
    with session_factory.begin() as session:
        existing = _latest(session, organization_id, report_id, snapshot_id)
        if existing is not None:
            return DashboardRefreshState(
                existing.status, existing.correlation_id, existing.attempt_number
            )
        attempt = session.scalar(select(func.coalesce(
            func.max(ExternalRefreshDiagnostic.attempt_number), 0
        )).where(
            ExternalRefreshDiagnostic.organization_id == organization_id,
            ExternalRefreshDiagnostic.snapshot_id == snapshot_id,
            ExternalRefreshDiagnostic.provider == "power_bi",
        )) + 1
        completed = started.status == "completed"
        diagnostic = ExternalRefreshDiagnostic(
            organization_id=organization_id,
            report_id=report_id,
            snapshot_id=snapshot_id,
            provider="power_bi",
            status=started.status,
            attempt_number=attempt,
            correlation_id=started.correlation_id,
            requested_at=timestamp,
            started_at=timestamp,
            completed_at=timestamp if completed else None,
            updated_at=timestamp,
        )
        session.add(diagnostic)
        session.flush()
        return DashboardRefreshState(
            diagnostic.status, diagnostic.correlation_id, diagnostic.attempt_number
        )


def reconcile_dashboard_refresh(
    session_factory,
    provider: PowerBIClient,
    *,
    organization_id,
    report_id,
    snapshot_id,
    now=None,
) -> DashboardRefreshState:
    """Poll one pending refresh and persist only its safe terminal state."""
    current = ensure_dashboard_refresh(
        session_factory,
        provider,
        organization_id=organization_id,
        report_id=report_id,
        snapshot_id=snapshot_id,
        now=now,
    )
    if current.status != "pending":
        return current
    timestamp = (now or _utcnow)()
    try:
        polled = provider.refresh_status(current.correlation_id)
    except PowerBIFailure as failure:
        with session_factory.begin() as session:
            diagnostic = _latest(session, organization_id, report_id, snapshot_id)
            if diagnostic is not None and diagnostic.status == "pending":
                diagnostic.status = "failed"
                diagnostic.safe_category = failure.category
                diagnostic.safe_reason_code = "semantic_refresh_unavailable"
                diagnostic.completed_at = timestamp
                diagnostic.updated_at = timestamp
        return DashboardRefreshState("failed", current.correlation_id, current.attempt_number)
    if polled.status == "pending":
        return current
    with session_factory.begin() as session:
        diagnostic = _latest(session, organization_id, report_id, snapshot_id)
        if diagnostic is not None and diagnostic.status == "pending":
            diagnostic.status = polled.status
            diagnostic.completed_at = timestamp
            diagnostic.updated_at = timestamp
            if polled.status == "failed":
                diagnostic.safe_category = "provider_response"
                diagnostic.safe_reason_code = "semantic_refresh_failed"
    return DashboardRefreshState(
        polled.status, current.correlation_id, current.attempt_number
    )
