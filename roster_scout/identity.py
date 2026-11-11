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


@dataclass(frozen=True)
class RefreshResult:
    """Provider refresh outcome with no provider payload in its representation."""

    authenticated: bool
    retryable: bool
    sealed_session: str | None = field(default=None, repr=False)
    category: str = "refresh_unavailable"

    @classmethod
    def refreshed(cls, sealed_session: str) -> "RefreshResult":
        if not isinstance(sealed_session, str) or not sealed_session:
            raise ValueError("sealed session is required")
        return cls(
            authenticated=True,
            retryable=False,
            sealed_session=sealed_session,
            category="refreshed",
        )

    @classmethod
    def retryable_failure(cls) -> "RefreshResult":
        return cls(
            authenticated=False,
            retryable=True,
            category="refresh_unavailable",
        )

    @classmethod
    def terminal_failure(cls) -> "RefreshResult":
        return cls(
            authenticated=False,
            retryable=False,
            category="refresh_revoked",
        )


@runtime_checkable
class IdentityProvider(Protocol):
    def authorization_url(
        self,
        *,
        state: str,
        invitation_token: str | None = None,
    ) -> str: ...

    def exchange_code(self, *, code: str) -> VerifiedIdentity: ...

    def refresh(self, *, sealed_session: str) -> RefreshResult: ...

    def logout_url(self, *, sealed_session: str) -> str: ...


class FakeIdentityProvider:
    """Deterministic in-memory provider for local and test composition."""

    def __init__(
        self,
        *,
        identities: dict[str, VerifiedIdentity] | None = None,
        authorization_base_url: str = "https://identity.example.test/authorize",
        failure: Exception | None = None,
        refresh_result: RefreshResult | None = None,
        refresh_failure: Exception | None = None,
        logout_url: str = "https://identity.example.test/logout",
        logout_failure: Exception | None = None,
    ):
        self._identities = dict(identities or {})
        self._authorization_base_url = authorization_base_url
        self._failure = failure
        self._refresh_result = refresh_result or RefreshResult.retryable_failure()
        self._refresh_failure = refresh_failure
        self._logout_url = logout_url
        self._logout_failure = logout_failure
        self.refresh_calls: list[str] = []
        self.logout_calls: list[str] = []

    def authorization_url(
        self,
        *,
        state: str,
        invitation_token: str | None = None,
    ) -> str:
        query = {"state": state}
        if invitation_token is not None:
            query["invitation_token"] = invitation_token
        return f"{self._authorization_base_url}?{urlencode(query)}"

    def exchange_code(self, *, code: str) -> VerifiedIdentity:
        if self._failure is not None:
            raise self._failure
        return self._identities[code]

    def refresh(self, *, sealed_session: str) -> RefreshResult:
        self.refresh_calls.append(sealed_session)
        if self._refresh_failure is not None:
            raise self._refresh_failure
        return self._refresh_result

    def logout_url(self, *, sealed_session: str) -> str:
        self.logout_calls.append(sealed_session)
        if self._logout_failure is not None:
            raise self._logout_failure
        return self._logout_url


class WorkOSIdentityProvider:
    """WorkOS AuthKit adapter that exposes only Roster Scout identity values."""

    def __init__(
        self,
        *,
        api_key: str,
        client_id: str,
        redirect_uri: str,
        cookie_password: str,
        issuer: str = "https://api.workos.com/user_management",
        client=None,
    ):
        if not all(
            isinstance(value, str) and value
            for value in (api_key, client_id, redirect_uri, cookie_password, issuer)
        ):
            raise ValueError("identity provider configuration is incomplete")
        if client is None:
            from workos import WorkOSClient

            client = WorkOSClient(api_key=api_key, client_id=client_id)
        self._client = client
        self._redirect_uri = redirect_uri
        self._cookie_password = cookie_password
        self._issuer = issuer

    def authorization_url(
        self,
        *,
        state: str,
        invitation_token: str | None = None,
    ) -> str:
        try:
            return self._client.user_management.get_authorization_url(
                provider="authkit",
                redirect_uri=self._redirect_uri,
                state=state,
                invitation_token=invitation_token,
            )
        except Exception as exc:
            raise IdentityFailure(_provider_failure_category(exc)) from None

    def exchange_code(self, *, code: str) -> VerifiedIdentity:
        try:
            response = self._client.user_management.authenticate_with_code(code=code)
            user = response.user
            from workos.session import seal_session_from_auth_response

            sealed_session = seal_session_from_auth_response(
                access_token=response.access_token,
                refresh_token=response.refresh_token,
                user=user.to_dict(),
                impersonator=(
                    response.impersonator.to_dict()
                    if getattr(response.impersonator, "to_dict", None)
                    else response.impersonator
                ),
                cookie_password=self._cookie_password,
            )
            return VerifiedIdentity(
                issuer=self._issuer,
                subject=user.id,
                provider_user_id=user.id,
                email=user.email,
                email_verified=bool(user.email_verified),
                linking_approved=False,
                sealed_session=sealed_session,
            )
        except IdentityFailure:
            raise
        except Exception as exc:
            raise IdentityFailure(_provider_failure_category(exc)) from None

    def refresh(self, *, sealed_session: str) -> RefreshResult:
        try:
            provider_session = self._client.user_management.load_sealed_session(
                session_data=sealed_session,
                cookie_password=self._cookie_password,
            )
            response = provider_session.refresh()
        except Exception as exc:
            category = _provider_failure_category(exc)
            if category in {"identity_unavailable", "throttled"}:
                return RefreshResult.retryable_failure()
            return RefreshResult.terminal_failure()

        if response.authenticated:
            return RefreshResult.refreshed(response.sealed_session)
        reason = getattr(response.reason, "value", response.reason)
        if reason in {"refresh_denied", "invalid_session_cookie", "invalid_jwt"}:
            return RefreshResult.terminal_failure()
        return RefreshResult.retryable_failure()

    def logout_url(self, *, sealed_session: str) -> str:
        try:
            provider_session = self._client.user_management.load_sealed_session(
                session_data=sealed_session,
                cookie_password=self._cookie_password,
            )
            return provider_session.get_logout_url()
        except Exception as exc:
            raise IdentityFailure(_provider_failure_category(exc)) from None


def _provider_failure_category(exc: Exception) -> str:
    """Collapse SDK failures without retaining exception or response material."""
    name = type(exc).__name__
    status_code = getattr(exc, "status_code", None)
    if name == "RateLimitExceededError" or status_code == 429:
        return "throttled"
    if name in {"ServerError", "WorkOSConnectionError", "WorkOSTimeoutError"}:
        return "identity_unavailable"
    if isinstance(status_code, int) and status_code >= 500:
        return "identity_unavailable"
    return "invalid_identity_response"


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
    except IdentityFailure:
        raise
    except Exception:
        (logger or logging.getLogger(__name__)).warning(
            "identity_provider_exchange_failed"
        )
        raise IdentityFailure("identity_unavailable") from None
