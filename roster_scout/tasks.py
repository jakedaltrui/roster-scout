"""Opaque job-ID-only Celery entry point."""

from functools import lru_cache

from .celery_app import celery_app
from .database import create_session_factory, get_engine
from .jobs import MAX_ATTEMPTS, DeterministicJobError, RetryableJobError, run_report_job


@lru_cache(maxsize=1)
def _session_factory():
    return create_session_factory(get_engine())


def collect_report_sections(job_id, *, cached_sections=frozenset()):
    """Collector seam implemented by the canonical NCAA adapter in Plan 01-03."""
    del job_id, cached_sections
    raise DeterministicJobError(
        "schema_validation", "No canonical report collector is configured."
    )


@celery_app.task(
    bind=True,
    name="roster_scout.build_report_job",
    max_retries=MAX_ATTEMPTS - 1,
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    ignore_result=True,
)
def build_report_job(self, job_id):
    try:
        snapshot = run_report_job(
            _session_factory(), job_id, collector=collect_report_sections
        )
    except RetryableJobError as exc:
        raise self.retry(exc=exc) from None
    except DeterministicJobError:
        raise
    return str(snapshot.id)


def dispatch_report_job(job_id):
    return build_report_job.apply_async(args=(job_id,))
