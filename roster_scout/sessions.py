"""Opaque, revocable, server-side account session services."""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from .identity import IdentityProvider
from .models import Account, AccountSession


SESSION_COOKIE_NAME = "__Host-rs_session"
SESSION_IDLE_LIFETIME = timedelta(hours=12)
SESSION_ABSOLUTE_LIFETIME = timedelta(days=7)


@dataclass(frozen=True)
class SessionCredential:
    cookie_value: str = field(repr=False)
    csrf_secret: str = field(repr=False)
    idle_expires_at: datetime
    absolute_expires_at: datetime


@dataclass(frozen=True)
class LoadedAccountSession:
    session_id: uuid.UUID
    account_id: uuid.UUID
    active_organization_id: uuid.UUID | None
    csrf_secret: str = field(repr=False)
    sealed_provider_state: str = field(repr=False)
    idle_expires_at: datetime
    absolute_expires_at: datetime


@dataclass(frozen=True)
class SessionRefreshOutcome:
    status: str
    session: LoadedAccountSession | None = None


@dataclass(frozen=True)
class LogoutPreparation:
    sealed_provider_state: str = field(repr=False)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _verifier_digest(verifier: str) -> str:
    return hashlib.sha256(verifier.encode("ascii")).hexdigest()


def _parse_cookie(cookie_value: str) -> tuple[uuid.UUID, str] | None:
    if not isinstance(cookie_value, str) or len(cookie_value) > 200:
        return None
    try:
        lookup_text, verifier = cookie_value.split(".", 1)
        lookup_id = uuid.UUID(lookup_text)
        if str(lookup_id) != lookup_text or len(verifier) < 32:
            return None
        verifier.encode("ascii")
    except (AttributeError, UnicodeEncodeError, ValueError):
        return None
    return lookup_id, verifier


def session_cookie_options() -> dict[str, object]:
    return {
        "secure": True,
        "httponly": True,
        "samesite": "Lax",
        "path": "/",
    }


def _session_row_for_cookie(session, cookie_value: str, *, lock: bool):
    parsed = _parse_cookie(cookie_value)
    if parsed is None:
        return None
    lookup_id, verifier = parsed
    statement = select(AccountSession).where(
        AccountSession.lookup_id == lookup_id
    )
    if lock:
        statement = statement.with_for_update()
    row = session.scalar(statement)
    if row is None or not hmac.compare_digest(
        row.verifier_digest,
        _verifier_digest(verifier),
    ):
        return None
    return row


def _row_is_current(session, row, current: datetime) -> bool:
    if (
        row is None
        or row.revoked_at is not None
        or current >= row.idle_expires_at
        or current >= row.absolute_expires_at
    ):
        return False
    account = session.get(Account, row.account_id)
    return account is not None and account.status == "active"


def _touch_row(row, current: datetime) -> None:
    row.last_seen_at = current
    row.idle_expires_at = min(
        current + SESSION_IDLE_LIFETIME,
        row.absolute_expires_at,
    )


def _loaded_from_row(row) -> LoadedAccountSession:
    return LoadedAccountSession(
        session_id=row.id,
        account_id=row.account_id,
        active_organization_id=row.active_organization_id,
        csrf_secret=row.csrf_secret,
        sealed_provider_state=row.sealed_provider_state,
        idle_expires_at=row.idle_expires_at,
        absolute_expires_at=row.absolute_expires_at,
    )


def revoke_account_session(
    session,
    *,
    cookie_value: str,
    now: datetime | None = None,
    rotated: bool = False,
) -> bool:
    current = now or _utc_now()
    row = _session_row_for_cookie(session, cookie_value, lock=True)
    if row is None:
        return False
    if row.revoked_at is None:
        row.revoked_at = current
        if rotated:
            row.rotated_at = current
        session.flush()
    return True


def create_account_session(
    session,
    *,
    account_id: uuid.UUID | str,
    sealed_provider_state: str,
    active_organization_id: uuid.UUID | str | None = None,
    rotate_cookie_value: str | None = None,
    now: datetime | None = None,
) -> SessionCredential:
    """Create a new bearer verifier, persisting only its one-way digest."""
    current = now or _utc_now()
    canonical_account_id = uuid.UUID(str(account_id))
    account = session.get(Account, canonical_account_id)
    if account is None or account.status != "active":
        raise ValueError("account is unavailable")
    if not isinstance(sealed_provider_state, str) or not sealed_provider_state:
        raise ValueError("sealed provider state is required")
    if rotate_cookie_value:
        revoke_account_session(
            session,
            cookie_value=rotate_cookie_value,
            now=current,
            rotated=True,
        )

    lookup_id = uuid.uuid4()
    verifier = secrets.token_urlsafe(32)
    csrf_secret = secrets.token_urlsafe(32)
    idle_expires_at = current + SESSION_IDLE_LIFETIME
    absolute_expires_at = current + SESSION_ABSOLUTE_LIFETIME
    row = AccountSession(
        account_id=canonical_account_id,
        lookup_id=lookup_id,
        verifier_digest=_verifier_digest(verifier),
        sealed_provider_state=sealed_provider_state,
        csrf_secret=csrf_secret,
        rotation_family=uuid.uuid4(),
        active_organization_id=(
            uuid.UUID(str(active_organization_id))
            if active_organization_id is not None
            else None
        ),
        created_at=current,
        last_seen_at=current,
        idle_expires_at=idle_expires_at,
        absolute_expires_at=absolute_expires_at,
    )
    session.add(row)
    session.flush()
    return SessionCredential(
        cookie_value=f"{lookup_id}.{verifier}",
        csrf_secret=csrf_secret,
        idle_expires_at=idle_expires_at,
        absolute_expires_at=absolute_expires_at,
    )


def load_account_session(
    session,
    *,
    cookie_value: str,
    now: datetime | None = None,
    touch: bool = True,
) -> LoadedAccountSession | None:
    """Resolve and optionally slide one valid local session's idle expiry."""
    current = now or _utc_now()
    row = _session_row_for_cookie(session, cookie_value, lock=touch)
    if not _row_is_current(session, row, current):
        return None
    if touch:
        _touch_row(row, current)
        session.flush()
    return _loaded_from_row(row)


def refresh_account_session(
    session,
    *,
    provider: IdentityProvider,
    cookie_value: str,
    now: datetime | None = None,
    logger: logging.Logger | None = None,
) -> SessionRefreshOutcome:
    """Refresh provider state under a local row lock without reviving revocation."""
    current = now or _utc_now()
    row = _session_row_for_cookie(session, cookie_value, lock=True)
    if not _row_is_current(session, row, current):
        return SessionRefreshOutcome(status="revoked")
    try:
        result = provider.refresh(sealed_session=row.sealed_provider_state)
    except Exception:
        (logger or logging.getLogger(__name__)).warning(
            "identity_provider_refresh_failed"
        )
        _touch_row(row, current)
        session.flush()
        return SessionRefreshOutcome(
            status="preserved",
            session=_loaded_from_row(row),
        )

    if result.authenticated:
        if not result.sealed_session:
            row.revoked_at = current
            session.flush()
            return SessionRefreshOutcome(status="revoked")
        row.sealed_provider_state = result.sealed_session
        _touch_row(row, current)
        session.flush()
        return SessionRefreshOutcome(
            status="refreshed",
            session=_loaded_from_row(row),
        )
    if result.retryable:
        _touch_row(row, current)
        session.flush()
        return SessionRefreshOutcome(
            status="preserved",
            session=_loaded_from_row(row),
        )
    row.revoked_at = current
    session.flush()
    return SessionRefreshOutcome(status="revoked")


def prepare_account_session_logout(
    session,
    *,
    cookie_value: str,
    now: datetime | None = None,
) -> LogoutPreparation | None:
    """Revoke locally and return opaque provider state for use after commit."""
    current = now or _utc_now()
    row = _session_row_for_cookie(session, cookie_value, lock=True)
    if row is None:
        return None
    sealed_provider_state = row.sealed_provider_state
    if row.revoked_at is None:
        row.revoked_at = current
        session.flush()
    return LogoutPreparation(sealed_provider_state=sealed_provider_state)


def complete_provider_logout(
    provider: IdentityProvider,
    prepared: LogoutPreparation | None,
    *,
    logger: logging.Logger | None = None,
) -> str | None:
    """Best-effort provider logout; call only after local revoke commits."""
    if prepared is None:
        return None
    try:
        value = provider.logout_url(
            sealed_session=prepared.sealed_provider_state
        )
        return value if isinstance(value, str) and value else None
    except Exception:
        (logger or logging.getLogger(__name__)).warning(
            "identity_provider_logout_failed"
        )
        return None
