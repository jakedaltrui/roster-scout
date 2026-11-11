"""Provider-neutral managed identity and one-time callback state services.

This module is connection-free. Callers own the SQLAlchemy session and its
transaction. Provider secrets and exceptions never cross this boundary.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable
from urllib.parse import urlencode

from sqlalchemy import select

from .models import AuthTransaction


AUTH_TRANSACTION_LIFETIME = timedelta(minutes=10)


class IdentityFailure(RuntimeError):
    """One fixed, customer-safe identity failure category."""

    def __init__(self, category: str):
        self.category = category
        super().__init__(category)


@dataclass(frozen=True)
class VerifiedIdentity:
    issuer: str
    subject: str
    provider_user_id: str
    email: str = field(repr=False)
    email_verified: bool
    linking_approved: bool
    sealed_session: str = field(repr=False)


@runtime_checkable
class IdentityProvider(Protocol):
    def authorization_url(self, *, state: str) -> str: ...

    def exchange_code(self, *, code: str) -> VerifiedIdentity: ...


class FakeIdentityProvider:
    """Deterministic in-memory provider for local and test composition."""

    def __init__(
        self,
        *,
        identities: dict[str, VerifiedIdentity] | None = None,
        authorization_base_url: str = "https://identity.example.test/authorize",
        failure: Exception | None = None,
    ):
        self._identities = dict(identities or {})
        self._authorization_base_url = authorization_base_url
        self._failure = failure

    def authorization_url(self, *, state: str) -> str:
        return f"{self._authorization_base_url}?{urlencode({'state': state})}"

    def exchange_code(self, *, code: str) -> VerifiedIdentity:
        if self._failure is not None:
            raise self._failure
        return self._identities[code]


@dataclass(frozen=True)
class AuthTransactionStart:
    state: str = field(repr=False)
    expires_at: datetime
    return_path: str


@dataclass(frozen=True)
class ConsumedAuthTransaction:
    transaction_id: uuid.UUID
    invitation_id: uuid.UUID | None
    return_path: str


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _state_digest(state: str) -> str:
    return hashlib.sha256(state.encode("ascii")).hexdigest()


def _safe_return_path(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith("/")
        or value.startswith("//")
        or len(value) > 500
        or "\x00" in value
    ):
        raise IdentityFailure("invalid_return_path")
    return value


def begin_auth_transaction(
    session,
    *,
    invitation_id: uuid.UUID | str | None = None,
    return_path: str = "/",
    now: datetime | None = None,
) -> AuthTransactionStart:
    """Persist one hashed callback state and return its bearer value once."""
    current = now or _utc_now()
    state = secrets.token_urlsafe(32)
    expires_at = current + AUTH_TRANSACTION_LIFETIME
    row = AuthTransaction(
        state_digest=_state_digest(state),
        invitation_id=(uuid.UUID(str(invitation_id)) if invitation_id else None),
        return_path=_safe_return_path(return_path),
        created_at=current,
        expires_at=expires_at,
    )
    session.add(row)
    session.flush()
    return AuthTransactionStart(
        state=state,
        expires_at=expires_at,
        return_path=row.return_path,
    )


def consume_auth_transaction(
    session,
    *,
    state: str,
    now: datetime | None = None,
) -> ConsumedAuthTransaction:
    """Consume callback state exactly once, returning only safe local context."""
    current = now or _utc_now()
    try:
        digest = _state_digest(state)
    except (AttributeError, UnicodeEncodeError):
        raise IdentityFailure("invalid_auth_state") from None
    row = session.scalar(
        select(AuthTransaction)
        .where(AuthTransaction.state_digest == digest)
        .with_for_update()
    )
    if row is None or row.consumed_at is not None or current >= row.expires_at:
        raise IdentityFailure("invalid_auth_state")
    row.consumed_at = current
    session.flush()
    return ConsumedAuthTransaction(
        transaction_id=row.id,
        invitation_id=row.invitation_id,
        return_path=row.return_path,
    )


def exchange_authorization_code(
    provider: IdentityProvider,
    *,
    code: str,
    logger: logging.Logger | None = None,
) -> VerifiedIdentity:
    """Exchange a provider code while keeping all exception detail private."""
    try:
        return provider.exchange_code(code=code)
    except Exception:
        (logger or logging.getLogger(__name__)).warning(
            "identity_provider_exchange_failed"
        )
        raise IdentityFailure("identity_unavailable") from None
