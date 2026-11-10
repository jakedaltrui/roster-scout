"""Opaque job-ID-plus-generation Celery entry point."""

import uuid
from functools import lru_cache

from .celery_app import celery_app
from .database import create_session_factory, get_engine
from .jobs import (
    DeterministicJobError,
    RetryableJobError,
    _validated_generation,
    collect_ncaa_sections,
    run_report_job,
)


@lru_cache(maxsize=1)
def _session_factory():
    return create_session_factory(get_engine())


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
    return str(snapshot.id) if hasattr(snapshot, "id") else snapshot


def dispatch_report_job(job_id, generation):
    """Publish one registered task carrying an opaque UUID and generation."""
    normalized_job_id = str(uuid.UUID(str(job_id)))
    normalized_generation = _validated_generation(generation)
    return build_report_job.apply_async(
        args=(normalized_job_id, normalized_generation)
    )
