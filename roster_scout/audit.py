"""Connection-free, append-only security audit event producer.

Callers own the SQLAlchemy session and transaction. Only canonical UUIDs and
fixed allowlisted tokens cross this boundary; request and provider payloads do
not.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from .models import SecurityAuditEvent


AUDIT_ACTIONS = frozenset(
    {
        "identity.sign_in",
        "organization.bootstrap",
        "organization.switch",
        "invitation.create",
        "invitation.deliver",
        "invitation.accept",
        "invitation.revoke",
        "membership.create",
        "membership.remove",
        "membership.role_change",
        "report.create",
        "report.refresh",
        "report.retry",
        "snapshot.access",
        "artifact.download",
        "authorization.deny",
    }
)
AUDIT_OUTCOMES = frozenset({"allowed", "denied", "succeeded", "failed", "pending"})
AUDIT_TARGET_TYPES = frozenset(
    {
        "account",
        "organization",
        "invitation",
        "membership",
        "report",
        "job",
        "snapshot",
        "artifact",
        "session",
    }
)
AUDIT_REASONS = frozenset(
    {
        "operator_provisioned",
        "invitation_created",
        "invitation_delivered",
        "invitation_accepted",
        "invitation_revoked",
        "membership_created",
        "membership_removed",
        "role_changed",
        "authorized",
        "not_found",
        "forbidden",
        "validation_failed",
        "provider_unavailable",
        "job_accepted",
        "refresh_accepted",
        "retry_accepted",
        "snapshot_opened",
        "download_prepared",
        "download_acknowledged",
        "invalid_auth_state",
        "identity_not_verified",
        "identity_conflict",
        "identity_unavailable",
    }
)


def _optional_uuid(value, *, field):
    if value is None:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise ValueError(f"{field} must be a UUID") from None


def _utc_datetime(value, *, field):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware UTC")
    if value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field} must be UTC")
    return value.astimezone(UTC)


def append_security_audit_event(
    session,
    *,
    target_type,
    action,
    outcome,
    actor_account_id=None,
    organization_id=None,
    target_id=None,
    reason=None,
    occurred_at=None,
):
    """Append one sanitized event without committing or accepting free text."""
    if target_type not in AUDIT_TARGET_TYPES:
        raise ValueError("unsupported audit target type")
    if action not in AUDIT_ACTIONS:
        raise ValueError("unsupported audit action")
    if outcome not in AUDIT_OUTCOMES:
        raise ValueError("unsupported audit outcome")
    if reason is not None and reason not in AUDIT_REASONS:
        raise ValueError("unsupported audit reason")

    event = SecurityAuditEvent(
        actor_account_id=_optional_uuid(
            actor_account_id, field="actor_account_id"
        ),
        organization_id=_optional_uuid(
            organization_id, field="organization_id"
        ),
        target_type=target_type,
        target_id=_optional_uuid(target_id, field="target_id"),
        action=action,
        outcome=outcome,
        reason=reason,
        occurred_at=_utc_datetime(
            occurred_at or datetime.now(UTC), field="occurred_at"
        ),
    )
    session.add(event)
    session.flush()
    return event


def append_authorization_denial(
    session,
    *,
    actor_account_id,
    organization_id,
    target_type,
    target_id=None,
):
    """Append the one fixed, non-enumerating scoped-resource denial event."""
    return append_security_audit_event(
        session,
        actor_account_id=actor_account_id,
        organization_id=organization_id,
        target_type=target_type,
        target_id=target_id,
        action="authorization.deny",
        outcome="denied",
        reason="not_found",
    )
