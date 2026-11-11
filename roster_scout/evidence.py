"""Restricted, time-bounded HTML evidence for failed report builds."""

import gzip
import hashlib
import ipaddress
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session

from .jobs import SAFE_FAILURE_MESSAGES
from .models import EvidencePurgeState, FailureEvidence, ReportJob


FAILURE_EVIDENCE_MAX_BYTES = 64 * 1024
FAILURE_EVIDENCE_RETENTION_ENV = "FAILURE_EVIDENCE_RETENTION_DAYS"
FAILURE_EVIDENCE_DEFAULT_RETENTION_DAYS = 28
FAILURE_EVIDENCE_MAX_RETENTION_DAYS = 28
EVIDENCE_PURGE_LOCK_TIMEOUT_MS = 5_000
EVIDENCE_PURGE_STATEMENT_TIMEOUT_MS = 30_000
EVIDENCE_PURGE_READINESS_FRESHNESS = timedelta(hours=6)
EVIDENCE_PURGE_BATCH_SIZE = 100


class FailureEvidenceConfigurationError(RuntimeError):
    """Raised without echoing an unsafe retention configuration value."""


@dataclass(frozen=True)
class EvidencePurgeReadiness:
    ready: bool
    status: str
    last_success_at: datetime | None
    last_purged_count: int
    expired_bytes_count: int


@dataclass(frozen=True, eq=False)
class EvidencePurgeResult:
    """Safe aggregate outcome for one complete or partial purge scan."""

    purged_count: int
    complete: bool
    outstanding_count: int
    batches: int

    def __eq__(self, other):
        # Preserve the original integer result contract for existing callers while
        # exposing completion explicitly to the standalone purge runner.
        if isinstance(other, int):
            return self.purged_count == other
        if isinstance(other, EvidencePurgeResult):
            return (
                self.purged_count,
                self.complete,
                self.outstanding_count,
                self.batches,
            ) == (
                other.purged_count,
                other.complete,
                other.outstanding_count,
                other.batches,
            )
        return NotImplemented


def get_failure_evidence_retention(environ=None):
    """Parse the single production retention setting into a bounded duration."""
    source = os.environ if environ is None else environ
    raw_value = source.get(FAILURE_EVIDENCE_RETENTION_ENV)
    if raw_value is None:
        days = FAILURE_EVIDENCE_DEFAULT_RETENTION_DAYS
    elif not isinstance(raw_value, str) or not re.fullmatch(r"[0-9]+", raw_value):
        raise FailureEvidenceConfigurationError(
            "invalid failure evidence retention configuration"
        )
    else:
        days = int(raw_value)
    if not 1 <= days <= FAILURE_EVIDENCE_MAX_RETENTION_DAYS:
        raise FailureEvidenceConfigurationError(
            "invalid failure evidence retention configuration"
        )
    return timedelta(days=days)


def _safe_source_url(source_url):
    if not isinstance(source_url, str) or not source_url:
        return None
    if any(character in source_url for character in "\r\n\t\\"):
        return None
    try:
        parts = urlsplit(source_url)
        scheme = parts.scheme.lower()
        hostname = parts.hostname
        port = parts.port
    except (TypeError, ValueError):
        return None
    if scheme not in {"http", "https"} or not parts.netloc or not hostname:
        return None

    if ":" in hostname:
        try:
            ipaddress.IPv6Address(hostname)
        except ValueError:
            return None
        authority = f"[{hostname}]"
    else:
        try:
            ipaddress.IPv4Address(hostname)
        except ValueError:
            if len(hostname) > 253:
                return None
            labels = hostname.rstrip(".").split(".")
            if not labels or any(
                not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                for label in labels
            ):
                return None
        authority = hostname
    if port is not None:
        authority += f":{port}"
    return urlunsplit((scheme, authority, parts.path, "", ""))


def _safe_section(section):
    value = str(section or "")[:50]
    return value if re.fullmatch(r"[A-Za-z0-9_-]+", value) else "unknown"


def _html_bytes(html):
    if isinstance(html, str):
        raw_html = html.encode("utf-8")
    elif isinstance(html, (bytes, bytearray, memoryview)):
        raw_html = bytes(html)
    else:
        raise TypeError("html must be str or bytes-like")
    return raw_html[:FAILURE_EVIDENCE_MAX_BYTES]


def _store_failure_evidence(
    session,
    job_id,
    *,
    section,
    source_url,
    html,
    failure_category,
    collected_at,
    retention,
):
    category = (
        failure_category
        if failure_category in SAFE_FAILURE_MESSAGES
        else "unexpected"
    )
    raw_html = _html_bytes(html)
    job = session.scalar(
        select(ReportJob).where(ReportJob.id == job_id).with_for_update()
    )
    if job is None:
        raise ValueError("report job does not exist")
    if job.status not in {"failed", "blocked"}:
        raise ValueError("HTML evidence is allowed only for failed or blocked jobs")
    evidence = FailureEvidence(
        organization_id=job.organization_id,
        job_id=job.id,
        compressed_html=gzip.compress(raw_html, mtime=0),
        sha256=hashlib.sha256(raw_html).hexdigest(),
        section=_safe_section(section),
        source_url=_safe_source_url(source_url),
        failure_category=category,
        collected_at=collected_at,
        expires_at=collected_at + retention,
    )
    session.add(evidence)
    session.flush()
    return evidence.id


def store_failure_evidence(
    session_or_factory,
    job_id,
    *,
    section,
    source_url,
    html,
    failure_category,
    collected_at=None,
    retention=None,
):
    """Store bounded failure bytes in a caller-owned or standalone transaction."""
    collected_at = collected_at or datetime.now(UTC)
    if retention is None:
        retention = get_failure_evidence_retention()
    if isinstance(session_or_factory, Session):
        return _store_failure_evidence(
            session_or_factory,
            job_id,
            section=section,
            source_url=source_url,
            html=html,
            failure_category=failure_category,
            collected_at=collected_at,
            retention=retention,
        )
    with session_or_factory.begin() as session:
        return _store_failure_evidence(
            session,
            job_id,
            section=section,
            source_url=source_url,
            html=html,
            failure_category=failure_category,
            collected_at=collected_at,
            retention=retention,
        )


def _set_evidence_purge_timeouts(session):
    session.execute(
        text(f"SET LOCAL lock_timeout = '{int(EVIDENCE_PURGE_LOCK_TIMEOUT_MS)}ms'")
    )
    session.execute(
        text(
            "SET LOCAL statement_timeout = "
            f"'{int(EVIDENCE_PURGE_STATEMENT_TIMEOUT_MS)}ms'"
        )
    )


def _upsert_evidence_purge_state(session, *, now, purged_count):
    statement = postgresql_insert(EvidencePurgeState).values(
        singleton_key=1,
        last_success_at=now,
        last_purged_count=purged_count,
        updated_at=now,
    )
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[EvidencePurgeState.singleton_key],
            set_={
                "last_success_at": now,
                "last_purged_count": purged_count,
                "updated_at": now,
            },
        )
    )


def purge_expired_evidence(session_factory, *, now=None):
    """Commit bounded expired-byte batches and record only complete scans."""
    now = now or datetime.now(UTC)
    purged_count = 0
    batches = 0

    while True:
        with session_factory.begin() as session:
            _set_evidence_purge_timeouts(session)
            claimed_ids = list(
                session.scalars(
                    select(FailureEvidence.id)
                    .where(
                        FailureEvidence.expires_at <= now,
                        FailureEvidence.compressed_html.is_not(None),
                    )
                    .order_by(FailureEvidence.expires_at, FailureEvidence.id)
                    .limit(EVIDENCE_PURGE_BATCH_SIZE)
                    .with_for_update(skip_locked=True)
                )
            )
            if claimed_ids:
                result = session.execute(
                    update(FailureEvidence)
                    .where(FailureEvidence.id.in_(claimed_ids))
                    .values(compressed_html=None, purged_at=now)
                )
                purged_count += int(result.rowcount or 0)
                batches += 1
        if not claimed_ids:
            break

    # This ordinary MVCC count includes rows skipped because another transaction
    # still holds their row lock. Only a zero result may advance readiness.
    with session_factory.begin() as session:
        _set_evidence_purge_timeouts(session)
        outstanding_count = int(
            session.scalar(
                select(func.count())
                .select_from(FailureEvidence)
                .where(
                    FailureEvidence.expires_at <= now,
                    FailureEvidence.compressed_html.is_not(None),
                )
            )
            or 0
        )
        complete = outstanding_count == 0
        if complete:
            _upsert_evidence_purge_state(
                session,
                now=now,
                purged_count=purged_count,
            )
    return EvidencePurgeResult(
        purged_count=purged_count,
        complete=complete,
        outstanding_count=outstanding_count,
        batches=batches,
    )


def get_evidence_purge_readiness(session_factory, *, now=None):
    """Return fail-closed purge health without exposing evidence or exceptions."""
    now = now or datetime.now(UTC)
    try:
        with session_factory() as session:
            state = session.get(EvidencePurgeState, 1)
            expired_bytes_count, _oldest_expiry = session.execute(
                select(
                    func.count(),
                    func.min(FailureEvidence.expires_at),
                ).where(
                    FailureEvidence.compressed_html.is_not(None),
                    FailureEvidence.expires_at <= now,
                )
            ).one()
    except Exception:
        return EvidencePurgeReadiness(
            ready=False,
            status="database_unavailable",
            last_success_at=None,
            last_purged_count=0,
            expired_bytes_count=0,
        )

    expired_bytes_count = int(expired_bytes_count or 0)
    if state is None:
        return EvidencePurgeReadiness(
            ready=False,
            status="never_succeeded",
            last_success_at=None,
            last_purged_count=0,
            expired_bytes_count=expired_bytes_count,
        )

    last_purged_count = int(state.last_purged_count)
    if now - state.last_success_at > EVIDENCE_PURGE_READINESS_FRESHNESS:
        status = "stale"
        ready = False
    elif expired_bytes_count:
        status = "expired_bytes_present"
        ready = False
    else:
        status = "fresh"
        ready = True
    return EvidencePurgeReadiness(
        ready=ready,
        status=status,
        last_success_at=state.last_success_at,
        last_purged_count=last_purged_count,
        expired_bytes_count=expired_bytes_count,
    )
