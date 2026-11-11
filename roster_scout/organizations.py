"""Locally authoritative organization and invitation services.

Core mutations use a caller-owned SQLAlchemy session and transaction. Provider
delivery coordinators accept a session factory so no database connection is held
while calling the identity provider.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from .accounts import AccountLinkFailure, normalize_verified_email
from .audit import append_security_audit_event
from .models import (
    Account,
    Organization,
    OrganizationIdentityConnection,
    OrganizationInvitation,
    OrganizationMembership,
)


INVITATION_LIFETIME = timedelta(days=7)
ORGANIZATION_ROLES = frozenset({"admin", "member"})
_LOGGER = logging.getLogger(__name__)


class OrganizationFailure(RuntimeError):
    """One fixed organization failure category without customer data."""

    def __init__(self, category: str):
        self.category = category
        super().__init__(category)


def _canonical_uuid(value, *, category="validation_failed"):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise OrganizationFailure(category) from None


def _utc_datetime(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise OrganizationFailure("validation_failed")
    if value.utcoffset() != UTC.utcoffset(value):
        raise OrganizationFailure("validation_failed")
    return value.astimezone(UTC)


def _normalized_email(value):
    try:
        return normalize_verified_email(value)
    except AccountLinkFailure:
        raise OrganizationFailure("validation_failed") from None


def _role(value):
    if value not in ORGANIZATION_ROLES:
        raise OrganizationFailure("validation_failed")
    return value


def _organization_name(value):
    if not isinstance(value, str):
        raise OrganizationFailure("validation_failed")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > 200:
        raise OrganizationFailure("validation_failed")
    return normalized


def _active_admin(session, *, organization_id, account_id):
    membership = session.scalar(
        select(OrganizationMembership)
        .where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.account_id == account_id,
        )
        .with_for_update()
    )
    if (
        membership is None
        or membership.status != "active"
        or membership.role != "admin"
    ):
        raise OrganizationFailure("forbidden")
    return membership


def _create_invitation(
    session,
    *,
    organization_id,
    actor_account_id,
    normalized_email,
    role,
    now,
):
    current = session.scalar(
        select(OrganizationInvitation)
        .where(
            OrganizationInvitation.organization_id == organization_id,
            OrganizationInvitation.normalized_email == normalized_email,
            OrganizationInvitation.status == "pending",
        )
        .with_for_update()
    )
    if current is not None and current.expires_at > now:
        raise OrganizationFailure("invitation_exists")
    if current is not None:
        current.status = "expired"
        session.flush()

    invitation = OrganizationInvitation(
        organization_id=organization_id,
        normalized_email=normalized_email,
        role=role,
        status="pending",
        invited_by_account_id=actor_account_id,
        expires_at=now + INVITATION_LIFETIME,
    )
    session.add(invitation)
    session.flush()
    append_security_audit_event(
        session,
        actor_account_id=actor_account_id,
        organization_id=organization_id,
        target_type="invitation",
        target_id=invitation.id,
        action="invitation.create",
        outcome="succeeded",
        reason="invitation_created",
        occurred_at=now,
    )
    return invitation


def create_invitation(
    session,
    *,
    organization_id,
    actor_account_id,
    email,
    role,
    now=None,
):
    """Create one exact-address local invitation as an active local admin."""
    organization_id = _canonical_uuid(organization_id)
    actor_account_id = _canonical_uuid(actor_account_id)
    normalized_email = _normalized_email(email)
    role = _role(role)
    now = _utc_datetime(now or datetime.now(UTC))

    organization = session.get(
        Organization, organization_id, with_for_update=True
    )
    if organization is None:
        raise OrganizationFailure("not_found")
    _active_admin(
        session,
        organization_id=organization_id,
        account_id=actor_account_id,
    )
    return _create_invitation(
        session,
        organization_id=organization_id,
        actor_account_id=actor_account_id,
        normalized_email=normalized_email,
        role=role,
        now=now,
    )


def bootstrap_organization(
    session,
    *,
    name,
    admin_email,
    now=None,
):
    """Idempotently create/reselect an organization and its first admin invite.

    This is a domain primitive for the restricted operator command in Plan 02-14;
    it is intentionally not exposed by a public route.
    """
    name = _organization_name(name)
    admin_email = _normalized_email(admin_email)
    now = _utc_datetime(now or datetime.now(UTC))

    organizations = list(
        session.scalars(
            select(Organization)
            .where(Organization.name == name)
            .with_for_update()
        )
    )
    if len(organizations) > 1:
        raise OrganizationFailure("organization_ambiguous")
    created = not organizations
    if created:
        organization = Organization(name=name)
        session.add(organization)
        session.flush()
        append_security_audit_event(
            session,
            organization_id=organization.id,
            target_type="organization",
            target_id=organization.id,
            action="organization.bootstrap",
            outcome="succeeded",
            reason="operator_provisioned",
            occurred_at=now,
        )
    else:
        organization = organizations[0]

    invitation = session.scalar(
        select(OrganizationInvitation)
        .where(
            OrganizationInvitation.organization_id == organization.id,
            OrganizationInvitation.normalized_email == admin_email,
            OrganizationInvitation.status == "pending",
        )
        .with_for_update()
    )
    if invitation is not None and invitation.expires_at > now:
        if invitation.role != "admin":
            raise OrganizationFailure("invitation_exists")
        return organization, invitation
    invitation = _create_invitation(
        session,
        organization_id=organization.id,
        actor_account_id=None,
        normalized_email=admin_email,
        role="admin",
        now=now,
    )
    return organization, invitation


def accept_invitation(
    session,
    *,
    invitation_id,
    account_id,
    now=None,
):
    """Accept one local invite by exact verified account email, once."""
    invitation_id = _canonical_uuid(invitation_id)
    account_id = _canonical_uuid(account_id)
    now = _utc_datetime(now or datetime.now(UTC))

    invitation = session.scalar(
        select(OrganizationInvitation)
        .where(OrganizationInvitation.id == invitation_id)
        .with_for_update()
    )
    if invitation is None:
        raise OrganizationFailure("invitation_unavailable")
    if invitation.status == "accepted":
        if invitation.accepted_account_id != account_id:
            raise OrganizationFailure("invitation_unavailable")
        membership = session.scalar(
            select(OrganizationMembership).where(
                OrganizationMembership.organization_id
                == invitation.organization_id,
                OrganizationMembership.account_id == account_id,
            )
        )
        if membership is None:
            raise OrganizationFailure("invitation_unavailable")
        return membership
    if invitation.status != "pending" or invitation.expires_at <= now:
        raise OrganizationFailure("invitation_unavailable")

    account = session.get(Account, account_id, with_for_update=True)
    if account is None or account.status != "active":
        raise OrganizationFailure("account_unavailable")
    if account.normalized_email != invitation.normalized_email:
        raise OrganizationFailure("invitation_email_mismatch")

    membership = session.scalar(
        select(OrganizationMembership)
        .where(
            OrganizationMembership.organization_id == invitation.organization_id,
            OrganizationMembership.account_id == account_id,
        )
        .with_for_update()
    )
    membership_created = membership is None
    if membership_created:
        membership = OrganizationMembership(
            organization_id=invitation.organization_id,
            account_id=account_id,
            role=invitation.role,
            status="active",
        )
        session.add(membership)
    else:
        membership.role = invitation.role
        membership.status = "active"
        membership.deactivated_at = None
    session.flush()

    invitation.status = "accepted"
    invitation.accepted_account_id = account_id
    invitation.accepted_at = now
    session.flush()
    if membership_created:
        append_security_audit_event(
            session,
            actor_account_id=account_id,
            organization_id=invitation.organization_id,
            target_type="membership",
            target_id=membership.id,
            action="membership.create",
            outcome="succeeded",
            reason="membership_created",
            occurred_at=now,
        )
    append_security_audit_event(
        session,
        actor_account_id=account_id,
        organization_id=invitation.organization_id,
        target_type="invitation",
        target_id=invitation.id,
        action="invitation.accept",
        outcome="succeeded",
        reason="invitation_accepted",
        occurred_at=now,
    )
    return membership


def revoke_invitation(
    session,
    *,
    invitation_id,
    actor_account_id,
    now=None,
):
    """Revoke a pending invitation as an active local administrator."""
    invitation_id = _canonical_uuid(invitation_id)
    actor_account_id = _canonical_uuid(actor_account_id)
    now = _utc_datetime(now or datetime.now(UTC))
    invitation = session.scalar(
        select(OrganizationInvitation)
        .where(OrganizationInvitation.id == invitation_id)
        .with_for_update()
    )
    if invitation is None:
        raise OrganizationFailure("not_found")
    _active_admin(
        session,
        organization_id=invitation.organization_id,
        account_id=actor_account_id,
    )
    if invitation.status == "revoked":
        return invitation
    if invitation.status != "pending":
        raise OrganizationFailure("invitation_unavailable")
    invitation.status = "revoked"
    invitation.revoked_at = now
    session.flush()
    append_security_audit_event(
        session,
        actor_account_id=actor_account_id,
        organization_id=invitation.organization_id,
        target_type="invitation",
        target_id=invitation.id,
        action="invitation.revoke",
        outcome="succeeded",
        reason="invitation_revoked",
        occurred_at=now,
    )
    return invitation


def _provider_failure(logger, operation):
    logger.warning(
        "organization identity operation failed operation=%s category=identity_unavailable",
        operation,
    )
    return OrganizationFailure("identity_unavailable")


def mirror_organization(
    session_factory,
    *,
    provider,
    organization_id,
    now=None,
    logger=None,
):
    """Ensure provider routing metadata without treating it as authorization."""
    organization_id = _canonical_uuid(organization_id)
    now = _utc_datetime(now or datetime.now(UTC))
    logger = logger or _LOGGER
    with session_factory.begin() as session:
        connection = session.scalar(
            select(OrganizationIdentityConnection).where(
                OrganizationIdentityConnection.organization_id == organization_id
            )
        )
        if connection is not None:
            return connection
        organization = session.get(Organization, organization_id)
        if organization is None:
            raise OrganizationFailure("not_found")
        organization_name = organization.name

    try:
        mirrored = provider.ensure_organization(
            name=organization_name,
            external_id=str(organization_id),
        )
        provider_organization_id = mirrored.provider_organization_id
        if (
            not isinstance(provider_organization_id, str)
            or not provider_organization_id
            or len(provider_organization_id) > 255
        ):
            raise ValueError("invalid provider organization")
    except Exception:
        raise _provider_failure(logger, "organization_mirror") from None

    with session_factory.begin() as session:
        organization = session.get(
            Organization, organization_id, with_for_update=True
        )
        if organization is None:
            raise OrganizationFailure("not_found")
        connection = session.scalar(
            select(OrganizationIdentityConnection).where(
                OrganizationIdentityConnection.organization_id == organization_id
            )
        )
        if connection is None:
            connection = OrganizationIdentityConnection(
                organization_id=organization_id,
                provider_organization_id=provider_organization_id,
                mirrored_at=now,
            )
            session.add(connection)
            session.flush()
        return connection


def deliver_invitation(
    session_factory,
    *,
    provider,
    invitation_id,
    now=None,
    logger=None,
):
    """Deliver a durable local invite and persist provider routing metadata."""
    invitation_id = _canonical_uuid(invitation_id)
    now = _utc_datetime(now or datetime.now(UTC))
    logger = logger or _LOGGER
    with session_factory.begin() as session:
        invitation = session.get(OrganizationInvitation, invitation_id)
        if (
            invitation is None
            or invitation.status != "pending"
            or invitation.expires_at <= now
        ):
            raise OrganizationFailure("invitation_unavailable")
        if invitation.provider_invitation_id is not None:
            return invitation
        organization_id = invitation.organization_id
        normalized_email = invitation.normalized_email
        local_expires_at = invitation.expires_at

    connection = mirror_organization(
        session_factory,
        provider=provider,
        organization_id=organization_id,
        now=now,
        logger=logger,
    )
    remaining = local_expires_at - now
    expires_in_days = max(1, min(7, (remaining.days + (remaining.seconds > 0))))
    try:
        delivered = provider.send_invitation(
            email=normalized_email,
            provider_organization_id=connection.provider_organization_id,
            expires_in_days=expires_in_days,
        )
        provider_invitation_id = delivered.provider_invitation_id
        if (
            not isinstance(provider_invitation_id, str)
            or not provider_invitation_id
            or len(provider_invitation_id) > 255
        ):
            raise ValueError("invalid provider invitation")
        provider_expires_at = delivered.expires_at or local_expires_at
        provider_expires_at = _utc_datetime(provider_expires_at)
        provider_expires_at = min(provider_expires_at, local_expires_at)
    except OrganizationFailure:
        raise
    except Exception:
        raise _provider_failure(logger, "invitation_delivery") from None

    with session_factory.begin() as session:
        invitation = session.scalar(
            select(OrganizationInvitation)
            .where(OrganizationInvitation.id == invitation_id)
            .with_for_update()
        )
        if (
            invitation is None
            or invitation.status != "pending"
            or invitation.expires_at <= now
        ):
            raise OrganizationFailure("invitation_unavailable")
        if invitation.provider_invitation_id is None:
            invitation.provider_invitation_id = provider_invitation_id
            invitation.provider_expires_at = provider_expires_at
            invitation.delivered_at = now
            session.flush()
            append_security_audit_event(
                session,
                actor_account_id=invitation.invited_by_account_id,
                organization_id=invitation.organization_id,
                target_type="invitation",
                target_id=invitation.id,
                action="invitation.deliver",
                outcome="succeeded",
                reason="invitation_delivered",
                occurred_at=now,
            )
        return invitation
