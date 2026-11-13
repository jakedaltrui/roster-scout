"""Canonical account and verified external-identity linking services."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import or_, select

from .identity import VerifiedIdentity
from .models import Account, ExternalIdentity


class AccountLinkFailure(RuntimeError):
    """One fixed account-linking failure category without identity material."""

    def __init__(self, category: str):
        self.category = category
        super().__init__(category)


def normalize_verified_email(value: str) -> str:
    if not isinstance(value, str):
        raise AccountLinkFailure("identity_not_verified")
    normalized = value.strip().casefold()
    if (
        not normalized
        or len(normalized) > 320
        or normalized.count("@") != 1
        or any(character.isspace() for character in normalized)
    ):
        raise AccountLinkFailure("identity_not_verified")
    return normalized


def _required_identity_field(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 500:
        raise AccountLinkFailure("identity_not_verified")
    return value.strip()


def link_verified_identity(
    session,
    identity: VerifiedIdentity,
    *,
    now: datetime | None = None,
) -> Account:
    """Resolve a provider subject first; use email only with verified linking."""
    if not identity.email_verified:
        raise AccountLinkFailure("identity_not_verified")
    issuer = _required_identity_field(identity.issuer)
    subject = _required_identity_field(identity.subject)
    provider_user_id = _required_identity_field(identity.provider_user_id)
    if len(provider_user_id) > 255:
        raise AccountLinkFailure("identity_not_verified")
    normalized_email = normalize_verified_email(identity.email)
    verified_at = now or datetime.now(UTC)

    existing = session.scalar(
        select(ExternalIdentity)
        .where(
            ExternalIdentity.issuer == issuer,
            ExternalIdentity.subject == subject,
        )
        .with_for_update()
    )
    if existing is not None:
        if existing.provider_user_id != provider_user_id:
            raise AccountLinkFailure("identity_conflict")
        account = session.scalar(
            select(Account)
            .where(Account.id == existing.account_id)
            .with_for_update()
        )
        if account is None or account.status != "active":
            raise AccountLinkFailure("account_unavailable")
        if account.normalized_email != normalized_email:
            conflicting_account = session.scalar(
                select(Account)
                .where(Account.normalized_email == normalized_email)
                .with_for_update()
            )
            if (
                conflicting_account is not None
                and conflicting_account.id != account.id
            ):
                raise AccountLinkFailure("identity_link_required")
            account.normalized_email = normalized_email
            account.email_verified_at = verified_at
        existing.last_verified_email = normalized_email
        existing.verified_at = verified_at
        session.flush()
        return account

    collision = session.scalar(
        select(ExternalIdentity)
        .where(
            or_(
                ExternalIdentity.provider_user_id == provider_user_id,
                (
                    (ExternalIdentity.issuer == issuer)
                    & (ExternalIdentity.subject == subject)
                ),
            )
        )
        .with_for_update()
    )
    if collision is not None:
        raise AccountLinkFailure("identity_conflict")

    account = session.scalar(
        select(Account)
        .where(Account.normalized_email == normalized_email)
        .with_for_update()
    )
    if account is not None:
        if account.status != "active":
            raise AccountLinkFailure("account_unavailable")
        if not identity.linking_approved:
            raise AccountLinkFailure("identity_link_required")
    else:
        account = Account(
            normalized_email=normalized_email,
            email_verified_at=verified_at,
            status="active",
        )
        session.add(account)
        session.flush()

    session.add(ExternalIdentity(
        account_id=account.id,
        issuer=issuer,
        subject=subject,
        provider_user_id=provider_user_id,
        last_verified_email=normalized_email,
        verified_at=verified_at,
    ))
    session.flush()
    return account
