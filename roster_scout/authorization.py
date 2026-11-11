"""Connection-free local account and organization authorization services."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from .models import AccountSession, OrganizationMembership
from .sessions import load_account_session


class AuthorizationFailure(RuntimeError):
    """One fixed authorization failure with no tenant or identity detail."""

    def __init__(self, category: str):
        self.category = category
        super().__init__(category)


@dataclass(frozen=True)
class AuthorizationContext:
    session_id: uuid.UUID
    account_id: uuid.UUID
    organization_id: uuid.UUID
    membership_id: uuid.UUID
    role: str
    csrf_secret: str


def list_active_memberships(session, *, account_id: uuid.UUID | str):
    """Return current local memberships; provider organization claims are absent."""
    try:
        canonical_account_id = uuid.UUID(str(account_id))
    except (TypeError, ValueError, AttributeError):
        raise AuthorizationFailure("authentication_required") from None
    return list(
        session.scalars(
            select(OrganizationMembership)
            .where(
                OrganizationMembership.account_id == canonical_account_id,
                OrganizationMembership.status == "active",
            )
            .order_by(
                OrganizationMembership.created_at,
                OrganizationMembership.id,
            )
        )
    )


def require_context(
    session,
    *,
    cookie_value: str,
    now: datetime | None = None,
) -> AuthorizationContext:
    """Reload a current local session and its active local membership."""
    loaded = load_account_session(
        session,
        cookie_value=cookie_value,
        now=now,
        touch=True,
    )
    if loaded is None:
        raise AuthorizationFailure("authentication_required")
    if loaded.active_organization_id is None:
        raise AuthorizationFailure("membership_required")

    membership = session.scalar(
        select(OrganizationMembership).where(
            OrganizationMembership.account_id == loaded.account_id,
            OrganizationMembership.organization_id
            == loaded.active_organization_id,
            OrganizationMembership.status == "active",
        )
    )
    if membership is None:
        local_session = session.scalar(
            select(AccountSession)
            .where(AccountSession.id == loaded.session_id)
            .with_for_update()
        )
        if local_session is not None:
            local_session.active_organization_id = None
            session.flush()
        raise AuthorizationFailure("membership_required")

    return AuthorizationContext(
        session_id=loaded.session_id,
        account_id=loaded.account_id,
        organization_id=membership.organization_id,
        membership_id=membership.id,
        role=membership.role,
        csrf_secret=loaded.csrf_secret,
    )


def require_admin(context: AuthorizationContext) -> AuthorizationContext:
    if context.role != "admin":
        raise AuthorizationFailure("forbidden")
    return context
