"""Opaque, revocable, server-side account session services."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

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
    if (
        row is None
        or row.revoked_at is not None
        or current >= row.idle_expires_at
        or current >= row.absolute_expires_at
    ):
        return None
    account = session.get(Account, row.account_id)
    if account is None or account.status != "active":
        return None
    if touch:
        row.last_seen_at = current
        row.idle_expires_at = min(
            current + SESSION_IDLE_LIFETIME,
            row.absolute_expires_at,
        )
        session.flush()
    return LoadedAccountSession(
        session_id=row.id,
        account_id=row.account_id,
        active_organization_id=row.active_organization_id,
        csrf_secret=row.csrf_secret,
        sealed_provider_state=row.sealed_provider_state,
        idle_expires_at=row.idle_expires_at,
        absolute_expires_at=row.absolute_expires_at,
    )
