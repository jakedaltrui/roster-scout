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
from threading import Lock
from typing import Protocol, runtime_checkable
from urllib.parse import urlencode

from sqlalchemy import and_, case, delete, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

from .models import AuthRequestLimit, AuthTransaction


AUTH_TRANSACTION_LIFETIME = timedelta(minutes=10)
AUTH_TRANSACTION_RETENTION = timedelta(hours=1)
AUTH_REQUEST_LIMIT_WINDOW = timedelta(minutes=10)
AUTH_REQUEST_LIMIT_RETENTION = timedelta(days=1)
AUTH_RETENTION_BATCH_SIZE = 100
PROVIDER_INVITATION_TIMEOUT_SECONDS = 20.0


class IdentityFailure(RuntimeError):
    """One fixed, customer-safe identity failure category."""

    def __init__(self, category: str, *, audit_worthy: bool = False):
        self.category = category
        self.audit_worthy = audit_worthy
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
    provider_organization_id: str | None = None


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


@dataclass(frozen=True)
class ProviderOrganization:
    provider_organization_id: str


@dataclass(frozen=True)
class ProviderInvitation:
    provider_invitation_id: str
    expires_at: datetime | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class ProviderInvitationReference:
    """Sanitized provider invitation metadata resolved from a bearer token."""

    provider_invitation_id: str
    provider_organization_id: str
    email: str = field(repr=False)
    state: str
    expires_at: datetime


@runtime_checkable
class IdentityProvider(Protocol):
    def authorization_url(
        self,
        *,
        state: str,
        invitation_token: str | None = None,
    ) -> str: ...

    def exchange_code(self, *, code: str) -> VerifiedIdentity: ...

    def resolve_invitation_token(
        self, *, invitation_token: str
    ) -> ProviderInvitationReference: ...

    def refresh(self, *, sealed_session: str) -> RefreshResult: ...

    def logout_url(self, *, sealed_session: str) -> str: ...

    def ensure_organization(
        self, *, name: str, external_id: str
    ) -> ProviderOrganization: ...

    def send_invitation(
        self,
        *,
        email: str,
        provider_organization_id: str,
        expires_in_days: int,
        idempotency_key: str,
    ) -> ProviderInvitation: ...

    def find_pending_invitation(
        self,
        *,
        email: str,
        provider_organization_id: str,
        created_after: datetime,
    ) -> ProviderInvitation | None: ...


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
        organization_failure: Exception | None = None,
        invitation_failure: Exception | None = None,
        invitation_references: (
            dict[str, ProviderInvitationReference] | None
        ) = None,
        invitation_resolution_failure: Exception | None = None,
    ):
        self._identities = dict(identities or {})
        self._authorization_base_url = authorization_base_url
        self._failure = failure
        self._refresh_result = refresh_result or RefreshResult.retryable_failure()
        self._refresh_failure = refresh_failure
        self._logout_url = logout_url
        self._logout_failure = logout_failure
        self._organization_failure = organization_failure
        self._invitation_failure = invitation_failure
        self._invitation_references = dict(invitation_references or {})
        self._invitation_resolution_failure = invitation_resolution_failure
        self.refresh_calls: list[str] = []
        self.logout_calls: list[str] = []
        self.organization_calls: list[tuple[str, str]] = []
        self.invitation_calls: list[tuple[str, str, int, str]] = []
        self.invitation_reconciliation_calls: list[tuple[str, str, datetime]] = []
        self.invitation_sends = 0
        self._invitation_send_lock = Lock()
        self._sent_invitations_by_key: dict[
            str, tuple[ProviderInvitation, str, str]
        ] = {}
        self.invitation_resolution_calls: list[str] = []

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

    def resolve_invitation_token(
        self, *, invitation_token: str
    ) -> ProviderInvitationReference:
        self.invitation_resolution_calls.append(invitation_token)
        if self._invitation_resolution_failure is not None:
            raise self._invitation_resolution_failure
        return self._invitation_references[invitation_token]

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

    def ensure_organization(
        self, *, name: str, external_id: str
    ) -> ProviderOrganization:
        self.organization_calls.append((name, external_id))
        if self._organization_failure is not None:
            raise self._organization_failure
        return ProviderOrganization(
            provider_organization_id=f"provider-org-{external_id}"
        )

    def send_invitation(
        self,
        *,
        email: str,
        provider_organization_id: str,
        expires_in_days: int,
        idempotency_key: str,
    ) -> ProviderInvitation:
        self.invitation_calls.append(
            (email, provider_organization_id, expires_in_days, idempotency_key)
        )
        if self._invitation_failure is not None:
            raise self._invitation_failure
        with self._invitation_send_lock:
            existing = self._sent_invitations_by_key.get(idempotency_key)
            if existing is not None:
                return existing[0]
            created_at = datetime.now(UTC)
            invitation = ProviderInvitation(
                provider_invitation_id=f"provider-invite-{uuid.uuid4()}",
                expires_at=created_at + timedelta(days=expires_in_days),
                created_at=created_at,
            )
            self._sent_invitations_by_key[idempotency_key] = (
                invitation,
                email.strip().casefold(),
                provider_organization_id,
            )
            self.invitation_sends += 1
            return invitation

    def find_pending_invitation(
        self,
        *,
        email: str,
        provider_organization_id: str,
        created_after: datetime,
    ) -> ProviderInvitation | None:
        self.invitation_reconciliation_calls.append(
            (email, provider_organization_id, created_after)
        )
        matches = [
            invitation
            for invitation, stored_email, stored_organization_id
            in self._sent_invitations_by_key.values()
            if stored_email == email.strip().casefold()
            and stored_organization_id == provider_organization_id
            and invitation.created_at is not None
            and invitation.created_at >= created_after
            and (
                invitation.expires_at is None
                or invitation.expires_at > datetime.now(UTC)
            )
        ]
        if len(matches) > 1:
            raise IdentityFailure("invalid_identity_response")
        return matches[0] if matches else None


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
                provider_organization_id=(
                    str(response.organization_id)
                    if getattr(response, "organization_id", None)
                    else None
                ),
            )
        except IdentityFailure:
            raise
        except Exception as exc:
            raise IdentityFailure(_provider_failure_category(exc)) from None

    def resolve_invitation_token(
        self, *, invitation_token: str
    ) -> ProviderInvitationReference:
        """Resolve one WorkOS bearer token without retaining the token."""
        try:
            invitation = self._client.user_management.find_invitation_by_token(
                invitation_token
            )
            state = getattr(invitation.state, "value", invitation.state)
            organization_id = invitation.organization_id
            expires_at = invitation.expires_at
            if (
                not isinstance(organization_id, str)
                or not organization_id
                or not isinstance(expires_at, datetime)
            ):
                raise ValueError("invalid invitation reference")
            return ProviderInvitationReference(
                provider_invitation_id=invitation.id,
                provider_organization_id=organization_id,
                email=invitation.email,
                state=str(state),
                expires_at=expires_at,
            )
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

    def ensure_organization(
        self, *, name: str, external_id: str
    ) -> ProviderOrganization:
        try:
            try:
                organization = (
                    self._client.organizations.get_organization_by_external_id(
                        external_id
                    )
                )
            except Exception as exc:
                if getattr(exc, "status_code", None) != 404:
                    raise
                organization = self._client.organizations.create_organization(
                    name=name,
                    external_id=external_id,
                    allow_profiles_outside_organization=False,
                )
            return ProviderOrganization(provider_organization_id=organization.id)
        except Exception as exc:
            raise IdentityFailure(_provider_failure_category(exc)) from None

    def send_invitation(
        self,
        *,
        email: str,
        provider_organization_id: str,
        expires_in_days: int,
        idempotency_key: str,
    ) -> ProviderInvitation:
        try:
            invitation = self._client.user_management.send_invitation(
                email=email,
                organization_id=provider_organization_id,
                expires_in_days=expires_in_days,
                request_options={
                    "idempotency_key": idempotency_key,
                    "timeout": PROVIDER_INVITATION_TIMEOUT_SECONDS,
                    "max_retries": 2,
                },
            )
            expires_at = getattr(invitation, "expires_at", None)
            created_at = getattr(invitation, "created_at", None)
            return ProviderInvitation(
                provider_invitation_id=invitation.id,
                expires_at=expires_at if isinstance(expires_at, datetime) else None,
                created_at=created_at if isinstance(created_at, datetime) else None,
            )
        except Exception as exc:
            raise IdentityFailure(_provider_failure_category(exc)) from None

    def find_pending_invitation(
        self,
        *,
        email: str,
        provider_organization_id: str,
        created_after: datetime,
    ) -> ProviderInvitation | None:
        """Reconcile one indeterminate send without exposing provider tokens."""
        try:
            page = self._client.user_management.list_invitations(
                limit=100,
                organization_id=provider_organization_id,
                email=email,
                request_options={
                    "timeout": PROVIDER_INVITATION_TIMEOUT_SECONDS,
                    "max_retries": 2,
                },
            )
            normalized_email = email.strip().casefold()
            matches = []
            for invitation in page.data:
                state = getattr(invitation.state, "value", invitation.state)
                if (
                    state == "pending"
                    and invitation.organization_id == provider_organization_id
                    and invitation.email.strip().casefold() == normalized_email
                    and isinstance(invitation.created_at, datetime)
                    and invitation.created_at >= created_after
                ):
                    matches.append(invitation)
            if len(matches) > 1:
                raise ValueError("ambiguous invitation reconciliation")
            if not matches:
                return None
            invitation = matches[0]
            return ProviderInvitation(
                provider_invitation_id=invitation.id,
                expires_at=(
                    invitation.expires_at
                    if isinstance(invitation.expires_at, datetime)
                    else None
                ),
                created_at=invitation.created_at,
            )
        except IdentityFailure:
            raise
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


def _bounded_delete(session, model, *, predicate, order_by, limit):
    identifiers = (
        select(model.id)
        .where(predicate)
        .order_by(order_by)
        .limit(limit)
    )
    return session.execute(
        delete(model).where(model.id.in_(identifiers))
    ).rowcount


def prune_auth_records(session, *, now=None, limit=AUTH_RETENTION_BATCH_SIZE):
    """Delete a bounded batch of stale callback and limiter state."""
    current = now or _utc_now()
    if not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError("invalid auth retention batch size")
    transaction_cutoff = current - AUTH_TRANSACTION_RETENTION
    removed_transactions = _bounded_delete(
        session,
        AuthTransaction,
        predicate=or_(
            AuthTransaction.expires_at <= transaction_cutoff,
            and_(
                AuthTransaction.consumed_at.is_not(None),
                AuthTransaction.consumed_at <= transaction_cutoff,
            ),
        ),
        order_by=AuthTransaction.expires_at,
        limit=limit,
    )
    limiter_cutoff = current - AUTH_REQUEST_LIMIT_RETENTION
    limiter_keys = (
        select(AuthRequestLimit.scope, AuthRequestLimit.client_digest)
        .where(AuthRequestLimit.updated_at <= limiter_cutoff)
        .order_by(AuthRequestLimit.updated_at)
        .limit(limit)
    )
    removed_limits = session.execute(
        delete(AuthRequestLimit).where(
            tuple_(
                AuthRequestLimit.scope,
                AuthRequestLimit.client_digest,
            ).in_(
                limiter_keys
            )
        )
    ).rowcount
    return removed_transactions, removed_limits


def check_auth_request_limit(
    session,
    *,
    scope: str,
    client_digest: str,
    limit: int,
    now=None,
):
    """Advance one shared auth window and return retry seconds, or zero."""
    if scope not in {"start", "callback"}:
        raise ValueError("invalid auth limit scope")
    if (
        not isinstance(client_digest, str)
        or len(client_digest) != 64
        or any(character not in "0123456789abcdef" for character in client_digest)
    ):
        raise ValueError("invalid auth client digest")
    if not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError("invalid auth request limit")
    current = now or _utc_now()
    prune_auth_records(session, now=current)
    cutoff = current - AUTH_REQUEST_LIMIT_WINDOW
    insert = postgresql_insert(AuthRequestLimit).values(
        scope=scope,
        client_digest=client_digest,
        window_started_at=current,
        attempt_count=1,
        updated_at=current,
    )
    window_expired = AuthRequestLimit.window_started_at <= cutoff
    statement = insert.on_conflict_do_update(
        index_elements=[
            AuthRequestLimit.scope,
            AuthRequestLimit.client_digest,
        ],
        set_={
            "window_started_at": case(
                (window_expired, current),
                else_=AuthRequestLimit.window_started_at,
            ),
            "attempt_count": case(
                (window_expired, 1),
                else_=AuthRequestLimit.attempt_count + 1,
            ),
            "updated_at": current,
        },
    ).returning(
        AuthRequestLimit.window_started_at,
        AuthRequestLimit.attempt_count,
    )
    row = session.execute(statement).one()
    if row.attempt_count <= limit:
        return 0
    remaining = (
        row.window_started_at + AUTH_REQUEST_LIMIT_WINDOW - current
    ).total_seconds()
    return max(1, int(remaining + 0.999))


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
    if row is None:
        raise IdentityFailure("invalid_auth_state")
    if row.consumed_at is not None or current >= row.expires_at:
        raise IdentityFailure("invalid_auth_state", audit_worthy=True)
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


def resolve_provider_invitation_token(
    provider: IdentityProvider,
    *,
    invitation_token: str,
    logger: logging.Logger | None = None,
) -> ProviderInvitationReference:
    """Resolve a provider bearer token while keeping failures and token private."""
    try:
        return provider.resolve_invitation_token(
            invitation_token=invitation_token
        )
    except Exception:
        if logger is not None:
            logger.warning("identity_provider_invitation_resolution_failed")
        raise IdentityFailure("identity_unavailable") from None
