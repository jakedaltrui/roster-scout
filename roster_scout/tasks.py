"""Opaque job-ID-plus-generation Celery entry point."""

import os
import uuid
from functools import lru_cache

from .artifacts import (
    artifact_store_from_environ,
    build_claimed_snapshot_artifact,
    claim_artifact_build,
    ensure_snapshot_workbook_artifact,
)
from .celery_app import celery_app
from .database import create_session_factory, get_engine
from .jobs import (
    DeterministicJobError,
    RetryableJobError,
    _validated_generation,
    collect_ncaa_sections,
    run_report_job,
)
from .models import ReportSnapshot


@lru_cache(maxsize=1)
def _session_factory():
    return create_session_factory(get_engine())


@lru_cache(maxsize=1)
def _artifact_store():
    return artifact_store_from_environ(required=bool(os.environ.get("FLY_APP_NAME")))


def _ensure_artifact_id(snapshot_id):
    snapshot_id = uuid.UUID(str(snapshot_id))
    with _session_factory().begin() as session:
        snapshot = session.get(ReportSnapshot, snapshot_id)
        if snapshot is None:
            return None
        artifact = ensure_snapshot_workbook_artifact(session, snapshot)
        return artifact.id


def collect_report_sections(job_id, *, cached_sections=frozenset()):
    """Collect live NCAA evidence for one durable report job."""
    return collect_ncaa_sections(
        _session_factory(), job_id, cached_sections=cached_sections
    )


@celery_app.task(
    name="roster_scout.build_report_job",
    ignore_result=True,
)
def build_report_job(job_id, generation):
    normalized_job_id = str(uuid.UUID(str(job_id)))
    generation = _validated_generation(generation)
    try:
        snapshot = run_report_job(
            _session_factory(),
            normalized_job_id,
            generation,
            collector=collect_report_sections,
        )
    except RetryableJobError:
        return "retry_scheduled"
    except DeterministicJobError:
        raise
    if hasattr(snapshot, "id"):
        artifact_id = _ensure_artifact_id(snapshot.id)
        store = _artifact_store()
        if artifact_id is not None and store is not None:
            attempt = claim_artifact_build(_session_factory(), artifact_id)
            if attempt is not None:
                build_claimed_snapshot_artifact(
                    _session_factory(), artifact_id, attempt, store=store
                )
    return str(snapshot.id) if hasattr(snapshot, "id") else snapshot


@celery_app.task(
    name="roster_scout.build_snapshot_artifact",
    ignore_result=True,
)
def build_snapshot_artifact(artifact_id, attempt):
    normalized_artifact_id = str(uuid.UUID(str(artifact_id)))
    normalized_attempt = int(attempt)
    if normalized_attempt < 1 or normalized_attempt > 10:
        raise ValueError("artifact attempt must be from 1 to 10")
    store = _artifact_store()
    if store is None:
        return "unconfigured"
    return build_claimed_snapshot_artifact(
        _session_factory(),
        normalized_artifact_id,
        normalized_attempt,
        store=store,
    )


def dispatch_report_job(job_id, generation):
    """Publish one registered task carrying an opaque UUID and generation."""
    normalized_job_id = str(uuid.UUID(str(job_id)))
    normalized_generation = _validated_generation(generation)
    return build_report_job.apply_async(
        args=(normalized_job_id, normalized_generation)
    )


def dispatch_snapshot_artifact(artifact_id, attempt):
    normalized_artifact_id = str(uuid.UUID(str(artifact_id)))
    normalized_attempt = int(attempt)
    if normalized_attempt < 1 or normalized_attempt > 10:
        raise ValueError("artifact attempt must be from 1 to 10")
    return build_snapshot_artifact.apply_async(
        args=(normalized_artifact_id, normalized_attempt)
    )
