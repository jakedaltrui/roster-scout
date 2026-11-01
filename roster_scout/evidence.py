"""Restricted, time-bounded HTML evidence for failed report builds."""

import gzip
import hashlib
import os
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select, update

from .jobs import SAFE_FAILURE_MESSAGES
from .models import FailureEvidence, ReportJob


RETENTION_DAYS = max(
    1, min(30, int(os.environ.get("FAILURE_EVIDENCE_RETENTION_DAYS", "30")))
)


def _safe_source_url(source_url):
    if not source_url:
        return None
    parts = urlsplit(source_url)
    if parts.scheme not in {"http", "https"}:
        return None
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def store_failure_evidence(
    session_factory,
    job_id,
    *,
    section,
    source_url,
    html,
    failure_category,
    collected_at=None,
):
    """Compress HTML for a failed/blocked job; successful jobs are rejected."""
    collected_at = collected_at or datetime.now(UTC)
    category = (
        failure_category
        if failure_category in SAFE_FAILURE_MESSAGES
        else "unexpected"
    )
    raw_html = bytes(html)
    with session_factory.begin() as session:
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
            section=section[:50] or "unknown",
            source_url=_safe_source_url(source_url),
            failure_category=category,
            collected_at=collected_at,
            expires_at=collected_at + timedelta(days=RETENTION_DAYS),
        )
        session.add(evidence)
        session.flush()
        evidence_id = evidence.id
    return evidence_id


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
