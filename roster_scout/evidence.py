"""Restricted, time-bounded HTML evidence for failed report builds."""

import gzip
import hashlib
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .jobs import SAFE_FAILURE_MESSAGES
from .models import FailureEvidence, ReportJob


FAILURE_EVIDENCE_MAX_BYTES = 64 * 1024
FAILURE_EVIDENCE_BYTE_RETENTION = timedelta(hours=696)


def _safe_source_url(source_url):
    if not source_url:
        return None
    parts = urlsplit(source_url)
    if parts.scheme not in {"http", "https"}:
        return None
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


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
        expires_at=collected_at + FAILURE_EVIDENCE_BYTE_RETENTION,
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
):
    """Store bounded failure bytes in a caller-owned or standalone transaction."""
    collected_at = collected_at or datetime.now(UTC)
    if isinstance(session_or_factory, Session):
        return _store_failure_evidence(
            session_or_factory,
            job_id,
            section=section,
            source_url=source_url,
            html=html,
            failure_category=failure_category,
            collected_at=collected_at,
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
        )


def purge_expired_evidence(session_factory, *, now=None):
    """Remove expired bytes while preserving safe metadata and checksums."""
    now = now or datetime.now(UTC)
    with session_factory.begin() as session:
        result = session.execute(
            update(FailureEvidence)
            .where(
                FailureEvidence.expires_at <= now,
                FailureEvidence.compressed_html.is_not(None),
            )
            .values(compressed_html=None, purged_at=now)
        )
        return result.rowcount
