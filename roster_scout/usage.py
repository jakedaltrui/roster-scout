"""Privacy-conscious product usage events and aggregate operator metrics.

This module is connection-free. Callers own the SQLAlchemy session and its
transaction. Phase 1 records anonymous workbook downloads only; Phase 2 may
record ``authenticated_use`` after authorization and durable job acceptance.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from .models import ProductUsageEvent


AUTHENTICATED_USE = "authenticated_use"
WORKBOOK_DOWNLOAD_SERVED = "workbook_download_served"
ALLOWED_EVENT_TYPES = frozenset(
    (AUTHENTICATED_USE, WORKBOOK_DOWNLOAD_SERVED)
)
MAX_EVENT_KEY_LENGTH = 200


@dataclass(frozen=True)
class UsageMetrics:
    window_start: datetime
    window_end: datetime
    organization_id: uuid.UUID | None
    unique_authenticated_users: int
    successful_workbook_downloads: int


def _canonical_uuid(value, *, field, required=False):
    if value is None:
        if required:
            raise ValueError(f"{field} is required")
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{field} must be a UUID") from exc


def _opaque_event_key(value):
    if not isinstance(value, str):
        raise ValueError("event_key must be an opaque UUID string")
    candidate = value.strip()
    if not candidate or len(candidate) > MAX_EVENT_KEY_LENGTH:
        raise ValueError("event_key must be a bounded opaque UUID string")
    try:
        return str(uuid.UUID(candidate))
    except ValueError as exc:
        raise ValueError("event_key must be an opaque UUID string") from exc


def _utc_datetime(value, *, field):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware UTC")
    if value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field} must be UTC")
    return value.astimezone(UTC)


def record_usage_event(
    session,
    *,
    event_type,
    event_key,
    occurred_at=None,
    actor_account_id=None,
    organization_id=None,
):
    """Insert or reselect one allowlisted event without committing.

    The event key is an opaque UUID. It must be the accepted ReportJob UUID for
    ``authenticated_use`` and a per-submission UUID for legacy workbook
    downloads. No email, IP, cookie, user-agent, or fingerprint is accepted as
    an identity substitute.
    """
    if event_type not in ALLOWED_EVENT_TYPES:
        raise ValueError("unsupported usage event type")
    event_key = _opaque_event_key(event_key)
    occurred_at = _utc_datetime(
        occurred_at or datetime.now(UTC),
        field="occurred_at",
    )
    actor_account_id = _canonical_uuid(
        actor_account_id,
        field="actor_account_id",
    )
    organization_id = _canonical_uuid(
        organization_id,
        field="organization_id",
    )
    if (actor_account_id is None) != (organization_id is None):
        raise ValueError(
            "actor_account_id and organization_id must be provided together"
        )
    if event_type == AUTHENTICATED_USE and actor_account_id is None:
        raise ValueError(
            "authenticated_use requires canonical account and organization UUIDs"
        )

    event_id = uuid.uuid4()
    statement = (
        insert(ProductUsageEvent)
        .values(
            id=event_id,
            event_type=event_type,
            event_key=event_key,
            occurred_at=occurred_at,
            actor_account_id=actor_account_id,
            organization_id=organization_id,
        )
        .on_conflict_do_nothing(
            constraint="uq_product_usage_events_type_key"
        )
        .returning(ProductUsageEvent.id)
    )
    inserted_id = session.scalar(statement)
    event = session.scalar(
        select(ProductUsageEvent).where(
            ProductUsageEvent.event_type == event_type,
            ProductUsageEvent.event_key == event_key,
        )
    )
    if event is None:
        raise RuntimeError("usage event insert did not produce a durable row")
    if inserted_id is not None and event.id != inserted_id:
        raise RuntimeError("usage event identity changed during insertion")
    return event


def record_workbook_download_served(
    session,
    *,
    event_key,
    occurred_at=None,
    actor_account_id=None,
    organization_id=None,
):
    """Record one workbook response successfully served to the browser."""
    return record_usage_event(
        session,
        event_type=WORKBOOK_DOWNLOAD_SERVED,
        event_key=event_key,
        occurred_at=occurred_at,
        actor_account_id=actor_account_id,
        organization_id=organization_id,
    )


def record_authenticated_report_request_accepted(
    session,
    *,
    event_key,
    actor_account_id,
    organization_id,
    occurred_at=None,
):
    """Record one authorized report/job request after durable acceptance.

    Phase 2 calls this only after authentication, membership authorization,
    validation, and durable ReportJob acceptance. Page views, login, rejected
    requests, and validation failures never call this producer.
    """
    return record_usage_event(
        session,
        event_type=AUTHENTICATED_USE,
        event_key=event_key,
        occurred_at=occurred_at,
        actor_account_id=actor_account_id,
        organization_id=organization_id,
    )


def query_usage_metrics(
    session,
    *,
    start,
    end,
    organization_id=None,
):
    """Return exact aggregate metrics for the half-open window ``[start, end)``."""
    start = _utc_datetime(start, field="start")
    end = _utc_datetime(end, field="end")
    if start >= end:
        raise ValueError("start must be before end")
    organization_id = _canonical_uuid(
        organization_id,
        field="organization_id",
    )
    statement = select(
        func.count(ProductUsageEvent.id)
        .filter(ProductUsageEvent.event_type == WORKBOOK_DOWNLOAD_SERVED)
        .label("successful_workbook_downloads"),
        func.count(func.distinct(ProductUsageEvent.actor_account_id))
        .filter(
            ProductUsageEvent.event_type == AUTHENTICATED_USE,
            ProductUsageEvent.actor_account_id.is_not(None),
        )
        .label("unique_authenticated_users"),
    ).where(
        ProductUsageEvent.occurred_at >= start,
        ProductUsageEvent.occurred_at < end,
    )
    if organization_id is not None:
        statement = statement.where(
            ProductUsageEvent.organization_id == organization_id
        )
    row = session.execute(statement).one()
    return UsageMetrics(
        window_start=start,
        window_end=end,
        organization_id=organization_id,
        unique_authenticated_users=int(row.unique_authenticated_users or 0),
        successful_workbook_downloads=int(
            row.successful_workbook_downloads or 0
        ),
    )
